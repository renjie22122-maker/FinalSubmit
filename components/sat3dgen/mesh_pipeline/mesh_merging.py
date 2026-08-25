"""
网格拼接/合并模块
--------------
多 tile 加载、裁切、坐标转换、拼接、合并相邻边界顶点。
"""

import time
import numpy as np
from pathlib import Path
from typing import List, Tuple

from scipy.spatial import cKDTree

from .config import Config
from .io import parse_obj
from .utils import extract_lat_lon_from_filename, local_to_world


# ============================================================
# 裁切
# ============================================================

def crop_boundary(vertices: np.ndarray, faces: np.ndarray,
                  crop_ratio: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
    """
    裁切边界：移除 OBJ 空间边缘的重叠区域，避免相邻 tile 边缘重复。
    """
    threshold = 0.81 * (1 - crop_ratio)
    obj_x, obj_z = vertices[:, 0], vertices[:, 2]
    keep_mask = (np.abs(obj_x) <= threshold) & (np.abs(obj_z) <= threshold)
    keep_indices = np.where(keep_mask)[0]

    old_to_new = {old: new for new, old in enumerate(keep_indices)}
    cropped_vertices = vertices[keep_mask]
    cropped_faces = []
    for face in faces:
        if all(v in old_to_new for v in face):
            cropped_faces.append([old_to_new[v] for v in face])

    result_faces = (np.array(cropped_faces, dtype=np.int32)
                    if cropped_faces
                    else np.zeros((0, 3), dtype=np.int32))
    return cropped_vertices, result_faces


# ============================================================
# 多 tile 加载与合并
# ============================================================

def _remove_bottom_faces_preserving_ranges(vertices: np.ndarray, faces: np.ndarray,
                                            tile_ranges: List[Tuple[int, int]],
                                            tol: float = 0.5
                                            ) -> Tuple[np.ndarray, np.ndarray, List[Tuple[int, int]]]:
    """
    对每个 tile 范围单独删除底部面，保持 tile_ranges 有效。
    在 load_and_merge_tiles 返回后使用，用于诊断导出后再删除底部面。

    Returns
    -------
    new_vertices, new_faces, new_tile_ranges
    """
    all_verts = []
    all_faces = []
    new_ranges = []
    offset = 0

    for start, end in tile_ranges:
        # 提取该 tile 的局部数据
        tile_face_mask = np.all((faces >= start) & (faces < end), axis=1)
        tile_faces = faces[tile_face_mask] - start  # 映射到局部索引
        tile_verts = vertices[start:end].copy()

        # 删除底部面
        cleaned_v, cleaned_f = _remove_bottom_faces(tile_verts, tile_faces, tol)

        if len(cleaned_f) > 0:
            all_verts.append(cleaned_v)
            all_faces.append(cleaned_f + offset)
            new_ranges.append((offset, offset + len(cleaned_v)))
            offset += len(cleaned_v)

    new_vertices = np.vstack(all_verts) if all_verts else np.empty((0, 6))
    new_faces = np.vstack(all_faces) if all_faces else np.empty((0, 3), dtype=np.int32)
    return new_vertices, new_faces, new_ranges


def _remove_bottom_faces(vertices, faces, tol=0.5):
    """
    删除每个 tile 最底层的面（基于 Y 高度检测，不是法线）。
    
    10% 边界裁剪后，底部与上表面/侧面完全分离，无共享顶点。
    检测：三个顶点都接近该 tile 最低 Y 的面 → 删除。
    
    这与 compute_surface_labels（基于顶点法线分类）完全不同。
    """
    if len(faces) == 0:
        return vertices, faces
    
    y_min = np.min(vertices[:, 1])
    # 三个顶点的最小 Y 都接近全局 y_min
    face_y_min = np.min(vertices[faces, 1], axis=1)
    bottom_mask = (face_y_min - y_min) <= tol
    
    if not bottom_mask.any():
        return vertices, faces
    
    kept_faces = faces[~bottom_mask]
    n_removed = bottom_mask.sum()
    
    if len(kept_faces) == 0:
        return vertices, faces
    
    used_verts = np.unique(kept_faces.flatten())
    old_to_new = {old: new for new, old in enumerate(used_verts)}
    new_faces = np.array([[old_to_new[v] for v in f] for f in kept_faces], dtype=np.int32)
    new_vertices = vertices[used_verts].copy()
    print(f"    删除底部面: {n_removed} 个 (Y<={y_min+tol:.1f})")
    return new_vertices, new_faces


def load_and_merge_tiles(obj_files: List[Path], config: Config,
                         osm_loader: "OSMLoader" = None,
                         remove_bottom: bool = False
                         ) -> Tuple[np.ndarray, np.ndarray, float, float, List[Tuple[int, int]]]:
    """
    加载所有 tile OBJ，裁切并拼接到世界坐标系。
    可选 OSM 语义预对齐 + 删除底部面。

    Parameters
    ----------
    obj_files : tile OBJ 路径列表
    config : 全局配置
    osm_loader : OSM 加载器（提供则做语义预对齐）
    remove_bottom : 是否在拼接前删除每个 tile 的底部面

    Returns
    -------
    all_vertices : (N, 6)
    all_faces : (M, 3)
    origin_lat, origin_lon : 世界原点
    tile_vertex_ranges : [(start, end), ...]
    """
    label = "合并所有 tile"
    if osm_loader:
        label += " (含OSM语义预对齐)"
    if remove_bottom:
        label += " + 删除底部面"
    print(f"\n{'=' * 60}")
    print(label)
    print(f"{'=' * 60}")

    # 计算原点
    all_lats, all_lons = [], []
    for obj_path in obj_files:
        lat, lon = extract_lat_lon_from_filename(obj_path.name)
        if lat is not None:
            all_lats.append(lat)
            all_lons.append(lon)
    origin_lat = min(all_lats)
    origin_lon = min(all_lons)
    print(f"  原点: ({origin_lat:.6f}, {origin_lon:.6f})")

    # ---- 第一遍：加载所有 tile，收集统计信息 ----
    tile_data = []
    building_medians_by_tile = {}  # {building_id: [(tile_idx, median_y), ...]}

    for idx, obj_path in enumerate(obj_files):
        lat, lon = extract_lat_lon_from_filename(obj_path.name)
        vertices, faces = parse_obj(obj_path)
        vertices, faces = crop_boundary(vertices, faces, config.crop_ratio)
        world_verts = local_to_world(
            vertices, lat, lon, origin_lat, origin_lon,
            config.lon_step, config.lat_step, config.overlap_ratio,
        )

        # 分类上下表面
        surface_labels = compute_surface_labels(world_verts, faces)
        upper_mask = surface_labels == 0

        # OSM 语义分类
        labels = None
        building_ids = None
        if osm_loader and upper_mask.sum() > 10:
            from .utils import world_to_latlon_batch
            u_lats, u_lons = world_to_latlon_batch(
                world_verts[upper_mask, 0], world_verts[upper_mask, 2],
                origin_lat, origin_lon
            )
            all_lats_arr = np.zeros(len(world_verts))
            all_lons_arr = np.zeros(len(world_verts))
            all_lats_arr[upper_mask] = u_lats
            all_lons_arr[upper_mask] = u_lons
            labels, building_ids = osm_loader.classify_batch(all_lons_arr, all_lats_arr)

            # 统计每个建筑上表面中值
            for bid in set(building_ids[building_ids >= 0]):
                bm = upper_mask & (building_ids == bid)
                if bm.sum() > 5:
                    median_y = np.median(world_verts[bm, 1])
                    building_medians_by_tile.setdefault(bid, []).append((idx, median_y))
        print(f"\n  [{idx + 1}/{len(obj_files)}] {obj_path.name}")
        print(f"    顶点: {len(world_verts)}, 面: {len(faces)}")
        print(f"    Y=[{world_verts[:, 1].min():.2f}, {world_verts[:, 1].max():.2f}]")

        tile_data.append((world_verts, faces, surface_labels, labels, building_ids))

    # ---- 计算全局建筑参考值（max 对齐到最高 tile 的屋顶） ----
    global_building_refs = {}
    for bid, tile_list in building_medians_by_tile.items():
        medians = [m for _, m in tile_list]
        if len(medians) >= 2:
            global_building_refs[bid] = max(medians)

    if global_building_refs:
        print(f"\n  跨tile建筑预对齐 (屋顶对齐): {len(global_building_refs)}个")
        for bid in sorted(global_building_refs)[:5]:
            ref = global_building_refs[bid]
            tile_info = [(t, f"{m:.1f}") for t, m in building_medians_by_tile[bid]]
            print(f"    building#{bid}: 参考={ref:.1f}m, tiles={tile_info}")

    # ---- 第二遍：应用预对齐，可选删除底部面，然后拼接 ----
    all_vertices = []
    all_faces = []
    vertex_offset = 0
    tile_vertex_ranges = []
    max_offset = 20.0

    for idx, (world_verts, faces, surface_labels, labels, building_ids) in enumerate(tile_data):
        if labels is not None and global_building_refs:
            world_verts = _apply_semantic_prealign(
                world_verts, surface_labels, labels, building_ids,
                global_building_refs,
                max_offset=max_offset,
            )

        # 可选：删除底部面（基于 Y 高度，不是法线）
        if remove_bottom:
            world_verts, faces = _remove_bottom_faces(world_verts, faces)

        tile_vertex_ranges.append((vertex_offset, vertex_offset + len(world_verts)))
        faces_adj = faces + vertex_offset
        all_vertices.append(world_verts)
        all_faces.append(faces_adj)
        vertex_offset += len(world_verts)

    all_vertices = np.vstack(all_vertices)
    all_faces = np.vstack(all_faces) if all_faces else np.zeros((0, 3), dtype=np.int32)
    print(f"\n  拼接前: {len(all_vertices)} 顶点, {len(all_faces)} 面")

    return all_vertices, all_faces, origin_lat, origin_lon, tile_vertex_ranges


def _apply_semantic_prealign(vertices: np.ndarray,
                              surface_labels: np.ndarray,
                              labels: np.ndarray,
                              building_ids: np.ndarray,
                              global_building_refs: dict,
                              max_offset: float = 20.0) -> np.ndarray:
    """
    对单个 tile 应用 OSM 语义预对齐（只对齐建筑上表面）。

    - 只修正上表面建筑顶点：同building_id跨tile对齐到全局参考（最高tile屋顶）
    - 只拉高不压低：offset > 0 才生效
    - 路面、其他语义、下表面、侧面：完全不动
    """
    upper_mask = surface_labels == 0
    corrected = vertices.copy()

    if len(global_building_refs) == 0:
        return vertices

    n_corrected = 0
    detail_parts = []
    for bid, global_ref in global_building_refs.items():
        bm = upper_mask & (building_ids == bid)
        if bm.sum() < 5:
            continue
        tile_median = np.median(vertices[bm, 1])
        bld_offset = global_ref - tile_median
        if bld_offset < 0.1:
            continue
        bld_offset = min(bld_offset, max_offset)
        corrected[bm, 1] += bld_offset
        n_corrected += 1
        detail_parts.append(f"#{bid}={tile_median:.1f}->{global_ref:.1f}(+{bld_offset:.1f})")

    if n_corrected > 0:
        print(f"    [OSM预对齐] 建筑={n_corrected}个: {', '.join(detail_parts[:5])}"
              + (f" ..." if len(detail_parts) > 5 else ""))
    return corrected


# ============================================================
# 顶点法线 + 上下表面分类
# ============================================================

def compute_surface_labels(vertices: np.ndarray, faces: np.ndarray
                           ) -> np.ndarray:
    """
    基于顶点法线方向分类顶点为上表面/下表面/侧面。

    对每个顶点，取相邻面的法线加权平均得到顶点法线。
    然后判断法线与 (0, 1, 0) 的点积：
      >  0.3 → 上表面 (label=0)
      < -0.3 → 下表面 (label=1)
      其他   → 侧面   (label=2)

    Returns
    -------
    labels : (N,) int  {0=upper, 1=lower, 2=side}
    """
    n_verts = len(vertices)
    n_faces = len(faces)
    if n_faces == 0:
        return np.zeros(n_verts, dtype=np.int32)

    v0 = vertices[faces[:, 0], :3]
    v1 = vertices[faces[:, 1], :3]
    v2 = vertices[faces[:, 2], :3]
    face_normals = np.cross(v1 - v0, v2 - v0)
    fnorms = np.linalg.norm(face_normals, axis=1, keepdims=True)
    face_normals = face_normals / (fnorms + 1e-10)

    vertex_normals = np.zeros((n_verts, 3))
    vertex_count = np.zeros(n_verts, dtype=np.int32)
    for fi, face in enumerate(faces):
        for v in face:
            vertex_normals[v] += face_normals[fi]
            vertex_count[v] += 1
    valid = vertex_count > 0
    vertex_normals[valid] /= vertex_count[valid, None]

    dots = vertex_normals[:, 1]
    labels = np.full(n_verts, 2, dtype=np.int32)
    labels[dots > 0.3] = 0
    labels[dots < -0.3] = 1

    n_upper = (labels == 0).sum()
    n_lower = (labels == 1).sum()
    n_side = (labels == 2).sum()
    print(f"  表面分类: 上表面={n_upper}, 下表面={n_lower}, 侧面={n_side}")

    return labels


# ============================================================
# 分组拼接（上接上，下接下）
# ============================================================

def stitch_tiles(all_vertices: np.ndarray, all_faces: np.ndarray,
                 tile_vertex_ranges: List[Tuple[int, int]],
                 stitch_distance: float = 0.5
                 ) -> Tuple[np.ndarray, np.ndarray]:
    """
    拼接相邻 tile 的边界顶点（按法线分组：上表面接上表面，下表面接下表面）。

    将顶点按法线分为上表面/下表面/侧面三组，分别在组内做 KDTree 拼接，
    杜绝下表面和相邻 tile 上表面错误合并。

    Parameters
    ----------
    all_vertices : (N, 6)  [x, y, z, r, g, b]
    all_faces : (M, 3)
    tile_vertex_ranges : 每个 tile 在数组中的 (start, end)
    stitch_distance : 合并半径（世界单位，米）

    Returns
    -------
    merged_vertices : (N', 6)
    merged_faces : (M', 3)
    """
    print(f"\n[拼接] 合并相邻 tile 边界顶点 (分组模式, 水平阈值={stitch_distance}m)...")
    t0 = time.time()
    n_verts = len(all_vertices)

    vertex_tile_id = np.zeros(n_verts, dtype=np.int32)
    for tile_id, (start, end) in enumerate(tile_vertex_ranges):
        vertex_tile_id[start:end] = tile_id

    surface_labels = compute_surface_labels(all_vertices, all_faces)

    all_matches = {}

    for group_id, group_name in enumerate(['上表面', '下表面', '侧面']):
        mask = surface_labels == group_id
        indices = np.where(mask)[0]
        if len(indices) < 2:
            print(f"  [{group_name}] 顶点太少，跳过")
            continue

        tree = cKDTree(all_vertices[indices][:, [0, 2]])
        group_matches = 0

        for local_i, global_i in enumerate(indices):
            pos = all_vertices[global_i, [0, 2]]
            neighbors = tree.query_ball_point(pos, stitch_distance)
            other = [indices[n] for n in neighbors
                     if vertex_tile_id[indices[n]] != vertex_tile_id[global_i]
                     and indices[n] > global_i]
            if other:
                all_matches[global_i] = other
                group_matches += 1

        print(f"  [{group_name}] 可合并顶点对: {group_matches}")

    print(f"  总计可合并顶点对: {len(all_matches)}")

    if len(all_matches) == 0:
        return all_vertices, all_faces

    merge_map = {}
    for keep_idx, merge_indices in all_matches.items():
        for merge_idx in merge_indices:
            merge_map[merge_idx] = keep_idx

    merged_indices = set(merge_map.keys())
    keep_mask = np.ones(n_verts, dtype=bool)
    keep_mask[list(merged_indices)] = False
    new_vertices = all_vertices[keep_mask]

    old_to_new = {}
    new_idx = 0
    for old_idx in range(n_verts):
        if old_idx in merged_indices:
            old_to_new[old_idx] = old_to_new[merge_map[old_idx]]
        else:
            old_to_new[old_idx] = new_idx
            new_idx += 1

    new_faces = []
    for face in all_faces:
        new_face = [old_to_new[v] for v in face]
        if len(set(new_face)) == 3:
            new_faces.append(new_face)

    print(f"  合并后: {len(new_vertices)} 顶点, {len(new_faces)} 面")
    print(f"  拼接耗时: {time.time() - t0:.1f}s")

    return new_vertices, np.array(new_faces, dtype=np.int32)