"""
语义高度修正模块
-------------
使用 DSM 数据和 OSM 语义标签对 3D 模型进行高度修正：
  - 非建筑区域：DSM 宏观约束 + 小模型细节保留
  - 建筑区域：DSM 基准 + 模型相对起伏
  - 只修正上表面顶点，下表面保持不变
  - 语义边界平滑
"""

import time
import numpy as np
from typing import Tuple

from pyproj import Transformer

from .config import Config
from .dsm_loader import DSMLoader
from .osm_loader import OSMLoader
from .utils import world_to_latlon_batch, build_adjacency
from .mesh_merging import compute_surface_labels


def semantic_height_correction(vertices: np.ndarray, faces: np.ndarray,
                                osm_loader: OSMLoader, dsm_loader: DSMLoader,
                                origin_lat: float, origin_lon: float,
                                config: Config = None
                                ) -> Tuple[np.ndarray, np.ndarray]:
    """
    语义高度修正 — 只修正上表面。

    Parameters
    ----------
    vertices : (N, 6) 世界坐标 [x, y, z, r, g, b]
    faces : (M, 3)
    osm_loader : OSM 数据加载器
    dsm_loader : DSM 数据加载器
    origin_lat, origin_lon : 世界原点（WGS84）
    config : 配置（可选）

    Returns
    -------
    corrected_vertices : (N, 6)
    faces : (M, 3) 不变
    """
    print("\n" + "=" * 60)
    print("语义高度修正 (只改上表面)")
    print("=" * 60)

    n_verts = len(vertices)
    print(f"处理 {n_verts} 个顶点...")
    t_start = time.time()

    # 1. 坐标转换：世界 → WGS84 → EPSG:27700
    lats, lons = world_to_latlon_batch(
        vertices[:, 0], vertices[:, 2], origin_lat, origin_lon
    )
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    eastings, northings = transformer.transform(lons, lats)

    # 2. 批量 DSM 查询
    dsm_heights = dsm_loader.query_heights_batch(eastings, northings)
    valid_dsm = ~np.isnan(dsm_heights)
    print(f"  有效 DSM: {valid_dsm.sum()}/{n_verts}")

    if valid_dsm.sum() < 100:
        print("  DSM 数据不足，跳过")
        return vertices, faces

    # 3. OSM 语义分类
    labels, building_ids = osm_loader.classify_batch(lons, lats)
    unique_labels, counts = np.unique(labels, return_counts=True)
    print(f"\n  分类统计:")
    for lbl, cnt in sorted(zip(unique_labels, counts), key=lambda x: -x[1]):
        print(f"    {lbl}: {cnt} ({cnt / n_verts * 100:.1f}%)")
    unique_buildings = set(building_ids[building_ids >= 0])

    # 4. DSM 同语义内部平滑
    dsm_smoothed = dsm_heights.copy()
    for bid in unique_buildings:
        mask = building_ids == bid
        dsm_valid = ~np.isnan(dsm_heights[mask])
        if dsm_valid.sum() > 5:
            dsm_smoothed[mask] = np.median(dsm_heights[mask][dsm_valid])
    for lbl in unique_labels:
        if lbl == 'building':
            continue
        mask = (labels == lbl) & valid_dsm
        if mask.sum() > 10:
            dsm_smoothed[mask] = np.median(dsm_heights[mask])

    # 打印平滑后各区域高度
    print(f"\n  各区域平滑后 DSM 高度:")
    for lbl in unique_labels:
        mask = (labels == lbl) & valid_dsm
        if mask.sum() > 10:
            vals = dsm_smoothed[mask]
            print(f"    {lbl}: {np.min(vals):.1f}~{np.max(vals):.1f}m "
                  f"(中位数={np.median(vals):.1f}m)")

    # 5. 执行高度修正（按语义标签修正该标签下的所有顶点，不区分上/侧/下表面）
    t0 = time.time()
    corrected = vertices.copy()
    print(f"\n  执行高度修正 (按OSM语义标签)...")

    # 非建筑区域
    for lbl in ['road', 'water', 'green', 'other']:
        mask = labels == lbl
        if mask.sum() == 0:
            continue
        y_vals = vertices[mask, 1]
        vert_idxs = np.where(mask)[0]
        model_avg = np.mean(y_vals)
        dsm_vals = dsm_smoothed[mask]
        valid_dsm_mask = valid_dsm[mask]
        if valid_dsm_mask.sum() < 10:
            continue
        dsm_base = np.median(dsm_vals[valid_dsm_mask])
        max_detail = 5.0
        for idx in vert_idxs:
            if valid_dsm[idx]:
                detail = vertices[idx, 1] - model_avg
                detail = np.clip(detail, -max_detail, max_detail)
                corrected[idx, 1] = dsm_base + detail
        print(f"    {lbl}: {mask.sum()} 顶点, 模型平均={model_avg:.2f}, "
              f"DSM 基准={dsm_base:.1f}m")

    # 建筑区域 (V12 平移修正)
    n_buildings_corrected = 0
    for bid in unique_buildings:
        mask = building_ids == bid
        y_vals = vertices[mask, 1]
        dsm_vals = dsm_smoothed[mask]
        dsm_valid_mask = valid_dsm[mask]
        if dsm_valid_mask.sum() < 5:
            continue
        dsm_base = np.median(dsm_vals[dsm_valid_mask])
        model_avg = np.mean(y_vals)
        vert_idxs = np.where(mask)[0]
        for idx in vert_idxs:
            corrected[idx, 1] = dsm_base + (vertices[idx, 1] - model_avg)
        n_buildings_corrected += 1
        if n_buildings_corrected <= 5:
            print(f"    建筑#{bid}: {len(vert_idxs)} 顶点, "
                  f"模型平均={model_avg:.1f}m, DSM 基准={dsm_base:.1f}m")
    print(f"  建筑修正: {n_buildings_corrected}/{len(unique_buildings)} 栋")
    print(f"  修正执行: {time.time() - t0:.2f}s")

    # 6. 语义边界平滑（排除下表面）
    t0 = time.time()
    print(f"\n  边界平滑 (排除下表面)...")
    adj = build_adjacency(faces, n_verts)
    boundary_mask = np.zeros(n_verts, dtype=bool)
    for i in range(n_verts):
        lbl = labels[i]
        for nb in adj[i]:
            if labels[nb] != lbl:
                boundary_mask[i] = True
                break
    # 排除下表面顶点（保持底面平整）
    lower_mask = compute_surface_labels(corrected, faces) == 1
    boundary_mask[lower_mask] = False
    n_boundary = boundary_mask.sum()
    print(f"  边界顶点: {n_boundary} (已排除下表面)")
    if n_boundary > 0:
        smooth = corrected.copy()
        for i in np.where(boundary_mask)[0]:
            nb_ys = [corrected[nb, 1] for nb in adj[i]]
            if nb_ys:
                smooth[i, 1] = 0.7 * corrected[i, 1] + 0.3 * np.mean(nb_ys)
        corrected = smooth
    print(f"  边界平滑: {time.time() - t0:.2f}s")

    print(f"  语义高度修正总耗时: {time.time() - t_start:.1f}s")
    return corrected, faces