"""
建筑提取与网格清理模块
--------------------
从场景网格中提取建筑部分：
  1. OSM 建筑分类
  2. 计算地面基准高度
  3. 按面裁剪
  4. 底面统一高度
  （裁剪、水密化和内部面剔除在分离建筑后单独执行）
"""

import numpy as np
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple

from scipy.spatial import cKDTree

from .config import Config
from .osm_loader import OSMLoader
from .utils import world_to_latlon_batch, build_adjacency


# ============================================================
# 地面平面裁剪
# ============================================================

def clip_faces_to_ground(vertices: np.ndarray, faces: np.ndarray,
                          ground_height: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    用地面平面 y=ground_height 裁剪所有三角形。
    
    每个三角形的处理：
    - 3顶点全在地上 → 保留原面
    - 3顶点全在地下 → 丢弃
    - 穿地面 (1上2下 或 2上1下) → 计算交点，只保留地面以上部分
    
    Returns
    -------
    clipped_vertices : (N', 6)
    clipped_faces : (M', 3)
    """
    if len(faces) == 0:
        return vertices, faces

    n_verts = len(vertices)
    new_vertices = list(vertices)
    new_faces = []
    edge_cache = {}  # (min_idx, max_idx) -> new_vertex_index

    def intersect_edge(i0, i1, gh):
        key = (min(i0, i1), max(i0, i1))
        if key in edge_cache:
            return edge_cache[key]
        v0, v1 = vertices[i0], vertices[i1]
        t = (gh - v0[1]) / (v1[1] - v0[1] + 1e-10)
        intersect = v0 + t * (v1 - v0)
        intersect[1] = gh  # 精确对齐
        new_idx = len(new_vertices)
        new_vertices.append(intersect)
        edge_cache[key] = new_idx
        return new_idx

    for face in faces:
        v0, v1, v2 = int(face[0]), int(face[1]), int(face[2])
        ys = [vertices[v0, 1], vertices[v1, 1], vertices[v2, 1]]
        idxs = [v0, v1, v2]
        
        above = [idxs[i] for i in range(3) if ys[i] >= ground_height]
        below = [idxs[i] for i in range(3) if ys[i] < ground_height]

        if len(above) == 3:
            # 全部在地上 → 保留原面
            new_faces.append([v0, v1, v2])
        elif len(above) == 0:
            # 全部在地下 → 丢弃
            continue
        elif len(above) == 2:
            # 2顶点在上, 1在下 → 生成2个三角形
            i0 = intersect_edge(above[0], below[0], ground_height)
            i1 = intersect_edge(above[1], below[0], ground_height)
            new_faces.append([above[0], above[1], i0])
            new_faces.append([above[1], i1, i0])
        elif len(above) == 1:
            # 1顶点在上, 2在下 → 生成1个三角形
            i0 = intersect_edge(above[0], below[0], ground_height)
            i1 = intersect_edge(above[0], below[1], ground_height)
            new_faces.append([above[0], i0, i1])

    if len(new_faces) == 0:
        return np.empty((0, 6), dtype=np.float64), np.empty((0, 3), dtype=np.int32)

    return np.array(new_vertices, dtype=np.float64), np.array(new_faces, dtype=np.int32)


# ============================================================
# 内部面剔除
# ============================================================

def _remove_internal_faces(vertices: np.ndarray, faces: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    n_faces = len(faces)
    if n_faces == 0:
        return vertices, faces
    v0, v1, v2 = vertices[faces[:, 0], :3], vertices[faces[:, 1], :3], vertices[faces[:, 2], :3]
    face_normals = np.cross(v1 - v0, v2 - v0)
    fnorms = np.linalg.norm(face_normals, axis=1, keepdims=True)
    face_normals = face_normals / (fnorms + 1e-10)
    vertex_normals = np.zeros((len(vertices), 3))
    vertex_count = np.zeros(len(vertices), dtype=np.int32)
    for i, face in enumerate(faces):
        for v in face:
            vertex_normals[v] += face_normals[i]
            vertex_count[v] += 1
    vertex_normals = vertex_normals / (vertex_count[:, None] + 1e-10)
    keep_mask = np.ones(n_faces, dtype=bool)
    for i, face in enumerate(faces):
        fn = fnorms[i, 0]
        if fn < 1e-10:
            keep_mask[i] = False
            continue
        dots = np.dot(face_normals[i], vertex_normals[face].T)
        if np.all(dots < -0.3):
            keep_mask[i] = False
    areas = fnorms.flatten() / 2.0
    area_threshold = np.percentile(areas, 1) * 0.5
    keep_mask &= (areas > area_threshold)
    kept_faces = faces[keep_mask]
    removed = n_faces - len(kept_faces)
    if removed > 0:
        print(f"    剔除内部面: {removed} 个面")
    used_verts = np.unique(kept_faces.flatten())
    old_to_new = {old: new for new, old in enumerate(used_verts)}
    new_faces = np.array([[old_to_new[v] for v in face] for face in kept_faces], dtype=np.int32)
    new_vertices = vertices[used_verts].copy()
    return new_vertices, new_faces


# ============================================================
# 底部开口闭合 (Ear Clipping)
# ============================================================

def _close_mesh_holes(vertices, faces, max_hole_edges=50, ground_height=None):
    edge_to_faces = {}
    for i, face in enumerate(faces):
        for j in range(3):
            v0, v1 = int(face[j]), int(face[(j + 1) % 3])
            key = (min(v0, v1), max(v0, v1))
            edge_to_faces.setdefault(key, []).append(i)
    boundary_edges = [(v0, v1) for (v0, v1), fl in edge_to_faces.items() if len(fl) == 1]
    if len(boundary_edges) < 3:
        return faces
    edge_graph = {}
    for v0, v1 in boundary_edges:
        edge_graph.setdefault(v0, []).append(v1)
        edge_graph.setdefault(v1, []).append(v0)
    all_bv = set(edge_graph.keys())
    visited_v = set()
    holes = []
    for start in all_bv:
        if start in visited_v:
            continue
        hole, current, prev = [], start, -1
        while True:
            hole.append(current)
            visited_v.add(current)
            neighbors = edge_graph.get(current, [])
            if len(neighbors) == 0:
                break
            if len(neighbors) == 1:
                nxt = neighbors[0]
            else:
                if prev >= 0:
                    dir_prev = vertices[current, :3] - vertices[prev, :3]
                    dlen = np.linalg.norm(dir_prev)
                    dir_prev = dir_prev / (dlen + 1e-10)
                    best_angle = -2.0
                    nxt = neighbors[0]
                    for nb in neighbors:
                        if nb == prev:
                            continue
                        dir_nb = vertices[nb, :3] - vertices[current, :3]
                        nlen = np.linalg.norm(dir_nb)
                        dir_nb = dir_nb / (nlen + 1e-10)
                        angle = np.dot(dir_prev, dir_nb)
                        if angle > best_angle:
                            best_angle = angle
                            nxt = nb
                else:
                    nxt = neighbors[0]
            if nxt == start:
                break
            if nxt in visited_v:
                if nxt in hole:
                    hole = hole[hole.index(nxt):]
                break
            prev, current = current, nxt
        if len(hole) >= 3:
            holes.append(hole)
    if not holes:
        return faces
    if len(faces) > 0:
        sample = min(1000, len(faces))
        areas_list = [np.linalg.norm(np.cross(
            vertices[f[0], :3] - vertices[f[1], :3],
            vertices[f[2], :3] - vertices[f[1], :3]
        )) / 2.0 for f in faces[:sample]]
        avg_face_area = np.mean(areas_list) if areas_list else 1.0
    else:
        avg_face_area = 1.0
    y_min_all = np.min(vertices[:, 1])
    y_range_all = np.max(vertices[:, 1]) - y_min_all
    filtered = []
    for hole in holes:
        if len(hole) > max_hole_edges:
            continue
        pts = vertices[hole, :3]
        xz = pts[:, [0, 2]]
        area = abs(sum(xz[i, 0] * xz[(i + 1) % len(xz), 1]
                       - xz[(i + 1) % len(xz), 0] * xz[i, 1]
                       for i in range(len(xz)))) / 2.0
        y_center = np.mean(pts[:, 1])
        is_bottom = (y_center - y_min_all) < y_range_all * 0.2
        if is_bottom:
            if area > avg_face_area * 5:
                filtered.append(hole)
        else:
            if area < avg_face_area * 100:
                filtered.append(hole)
    if not filtered:
        return faces
    new_faces_list = [faces]
    for hole in filtered:
        if len(hole) < 3:
            continue
        pts = vertices[hole, :3]
        is_bottom = (np.mean(pts[:, 1]) - y_min_all) < y_range_all * 0.2
        if is_bottom and ground_height is not None:
            pts_2d = pts[:, [0, 2]]
        else:
            centroid = np.mean(pts, axis=0)
            centered = pts - centroid
            cov = centered.T @ centered
            eigvals, eigvecs = np.linalg.eigh(cov)
            u = eigvecs[:, 1]
            v = eigvecs[:, 2]
            pts_2d = np.column_stack([centered @ u, centered @ v])
        indices = list(range(len(hole)))
        tri_faces = []
        while len(indices) >= 3:
            found = False
            for i in range(len(indices)):
                i0, i1, i2 = indices[(i - 1) % len(indices)], indices[i], indices[(i + 1) % len(indices)]
                cross = (pts_2d[i0][0] - pts_2d[i1][0]) * (pts_2d[i2][1] - pts_2d[i1][1]) - \
                        (pts_2d[i0][1] - pts_2d[i1][1]) * (pts_2d[i2][0] - pts_2d[i1][0])
                if cross > 0:
                    tri = np.array([pts_2d[i0], pts_2d[i1], pts_2d[i2]])
                    has_other = False
                    for j in indices:
                        if j in (i0, i1, i2):
                            continue
                        v0 = tri[2] - tri[0]; v1_ = tri[1] - tri[0]; v2_ = pts_2d[j] - tri[0]
                        dot00 = np.dot(v0, v0); dot01 = np.dot(v0, v1_); dot02 = np.dot(v0, v2_)
                        dot11 = np.dot(v1_, v1_); dot12 = np.dot(v1_, v2_)
                        inv = 1.0 / (dot00 * dot11 - dot01 * dot01 + 1e-10)
                        u_ = (dot11 * dot02 - dot01 * dot12) * inv
                        v_ = (dot00 * dot12 - dot01 * dot02) * inv
                        if u_ >= 0 and v_ >= 0 and u_ + v_ <= 1:
                            has_other = True
                            break
                    if not has_other:
                        tri_faces.append(np.array([[hole[i0], hole[i1], hole[i2]]], dtype=np.int32))
                        indices.pop(i)
                        found = True
                        break
            if not found:
                best_i, best_cross = 0, -float('inf')
                for i in range(len(indices)):
                    i0, i1, i2 = indices[(i - 1) % len(indices)], indices[i], indices[(i + 1) % len(indices)]
                    c = (pts_2d[i0][0] - pts_2d[i1][0]) * (pts_2d[i2][1] - pts_2d[i1][1]) - \
                        (pts_2d[i0][1] - pts_2d[i1][1]) * (pts_2d[i2][0] - pts_2d[i1][0])
                    if c > best_cross:
                        best_cross, best_i = c, i
                i0, i1, i2 = indices[(best_i - 1) % len(indices)], indices[best_i], indices[(best_i + 1) % len(indices)]
                tri_faces.append(np.array([[hole[i0], hole[i1], hole[i2]]], dtype=np.int32))
                indices.pop(best_i)
        if tri_faces:
            new_faces_list.append(np.vstack(tri_faces))
    closed_faces = np.vstack(new_faces_list) if len(new_faces_list) > 1 else faces
    return closed_faces


# ============================================================
# 辅助函数
# ============================================================

def _compute_ground_heights(vertices, building_ids, labels):
    road_mask = labels == 'road'
    road_y_vals = vertices[road_mask, 1]
    road_median_y = np.median(road_y_vals) if road_mask.sum() > 10 else np.percentile(vertices[:, 1], 10)
    unique_building_ids = set(building_ids[building_ids >= 0])
    ground_heights = {}
    road_verts_xy = vertices[road_mask][:, [0, 2]]
    road_tree = cKDTree(road_verts_xy) if len(road_verts_xy) > 10 else None
    for bid in unique_building_ids:
        mask = building_ids == bid
        vert_idxs = np.where(mask)[0]
        if len(vert_idxs) < 10:
            ground_heights[bid] = road_median_y
            continue
        building_center_xy = np.mean(vertices[vert_idxs][:, [0, 2]], axis=0)
        if road_tree is not None:
            nearby = road_tree.query_ball_point(building_center_xy, 50.0)
            ground_h = np.median(road_y_vals[nearby]) if len(nearby) > 5 else road_median_y
        else:
            ground_h = road_median_y
        ground_heights[bid] = ground_h
    print(f"    共 {len(unique_building_ids)} 个建筑")
    return ground_heights


def _crop_by_faces(vertices, faces, building_mask, building_ids, ground_heights):
    road_median_y = np.median(list(ground_heights.values()))
    above_ground = np.zeros(len(vertices), dtype=bool)
    for i in range(len(vertices)):
        if building_mask[i]:
            gh = ground_heights.get(building_ids[i], road_median_y)
            above_ground[i] = vertices[i, 1] >= gh - 0.5
    face_mask = np.array([any(above_ground[v] for v in f) for f in faces])
    building_faces = faces[face_mask]
    used_verts = np.unique(building_faces.flatten())
    old_to_new = {old: new for new, old in enumerate(used_verts)}
    new_faces = np.array([[old_to_new[v] for v in f] for f in building_faces], dtype=np.int32)
    new_vertices = vertices[used_verts].copy()
    print(f"    裁剪后: {len(new_vertices)} 顶点, {len(new_faces)} 面")
    return new_vertices, new_faces, building_ids[used_verts]


def _unify_ground_heights(vertices, building_ids, ground_heights):
    road_median_y = np.median(list(ground_heights.values()))
    n_corrected = 0
    for bid in set(building_ids[building_ids >= 0]):
        mask = building_ids == bid
        if mask.sum() < 10:
            continue
        gh = ground_heights.get(bid, road_median_y)
        below_mask = mask & (vertices[:, 1] < gh)
        if below_mask.sum() > 0:
            vertices[below_mask, 1] = gh
            n_corrected += below_mask.sum()
    print(f"    修正 {n_corrected} 个顶点到统一高度")
    return vertices


# ============================================================
# 建筑提取主流程 (仅裁剪，水密化等移到了 Step 3)
# ============================================================

def extract_building_mesh(vertices: np.ndarray, faces: np.ndarray,
                           osm_loader: OSMLoader,
                           origin_lat: float, origin_lon: float,
                           config: Config):
    """从场景网格中提取建筑部分（仅裁剪）"""
    print(f"\n{'=' * 60}")
    print("建筑提取 (裁剪)")
    print(f"{'=' * 60}")
    n_verts = len(vertices)
    print(f"  输入: {n_verts} 顶点, {len(faces)} 面")

    print("\n  [1] OSM 建筑分类...")
    lats, lons = world_to_latlon_batch(vertices[:, 0], vertices[:, 2], origin_lat, origin_lon)
    labels, building_ids = osm_loader.classify_batch(lons, lats)
    building_mask = labels == 'building'
    n_building_verts = building_mask.sum()
    print(f"    建筑顶点: {n_building_verts}/{n_verts} ({n_building_verts / n_verts * 100:.1f}%)")
    if n_building_verts < 100:
        print("    [WARN] 建筑顶点太少")
        return vertices, faces, np.full(len(vertices), -1, dtype=np.int32), {}

    print("\n  [2] 计算地面基准高度...")
    building_ground_heights = _compute_ground_heights(vertices, building_ids, labels)

    print("\n  [3] 按面裁剪...")
    new_vertices, new_faces, new_building_ids = _crop_by_faces(
        vertices, faces, building_mask, building_ids, building_ground_heights)

    print("\n  [4] 底面统一高度...")
    new_vertices = _unify_ground_heights(new_vertices, new_building_ids, building_ground_heights)

    print(f"\n  建筑提取完成: {len(new_vertices)} 顶点, {len(new_faces)} 面")
    return new_vertices, new_faces, new_building_ids, building_ground_heights


# ============================================================
# 按连通分量分离建筑
# ============================================================

def separate_building_components(vertices, faces, building_ids, min_vertices=10):
    adj = build_adjacency(faces, len(vertices))
    visited = np.zeros(len(vertices), dtype=bool)
    components = []
    for i in range(len(vertices)):
        if visited[i] or building_ids[i] < 0:
            continue
        queue, comp_verts, comp_bids = [i], [], set()
        visited[i] = True
        while queue:
            v = queue.pop(0)
            comp_verts.append(v)
            if building_ids[v] >= 0:
                comp_bids.add(building_ids[v])
            for nb in adj[v]:
                if not visited[nb] and building_ids[nb] >= 0:
                    visited[nb] = True
                    queue.append(nb)
        if len(comp_verts) >= min_vertices:
            vert_set = set(comp_verts)
            face_mask = np.array([any(v in vert_set for v in f) for f in faces])
            comp_faces = faces[face_mask]
            used_verts = np.unique(comp_faces.flatten())
            old2new = {old: new for new, old in enumerate(used_verts)}
            comp_faces_remapped = np.array([[old2new[v] for v in f] for f in comp_faces], dtype=np.int32)
            comp_verts_xyz = vertices[used_verts].copy()
            components.append((len(components), comp_verts_xyz, comp_faces_remapped, sorted(comp_bids)))
    return components