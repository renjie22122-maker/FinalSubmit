"""
基础工具函数
----------
坐标转换、IO 工具等底层函数。
"""

import math
import re
import numpy as np
from pathlib import Path
from typing import Tuple, Optional


# ============================================================
# 地理坐标转换
# ============================================================

# 常量：赤道 1 度 ≈ 111320 米
METERS_PER_DEG_LAT = 111320.0


def meters_per_deg_lon(lat_deg: float) -> float:
    """计算指定纬度的每经度米数"""
    return METERS_PER_DEG_LAT * math.cos(math.radians(lat_deg))


def latlon_to_local_m(lat: float, lon: float,
                      origin_lat: float, origin_lon: float) -> Tuple[float, float]:
    """
    将 (lat, lon) 转换为局部 (east_m, north_m) 坐标。
    返回 (east_m, north_m)，其中 north = -delta_y（OpenGL 风格：Z 朝北为负）。
    """
    north_m = (lat - origin_lat) * METERS_PER_DEG_LAT
    east_m = (lon - origin_lon) * meters_per_deg_lon(origin_lat)
    return east_m, -north_m


def local_to_world(vertices: np.ndarray, tile_lat: float, tile_lon: float,
                   origin_lat: float, origin_lon: float,
                   lon_step: float, lat_step: float,
                   overlap_ratio: float = 0.10) -> np.ndarray:
    """
    将 OBJ 空间的顶点转换为世界坐标系。

    Parameters
    ----------
    vertices : (N, 6+)  [x, y, z, r, g, b]
    tile_lat, tile_lon : tile 中心纬度/经度
    origin_lat, origin_lon : 世界原点
    lon_step, lat_step : 每 tile 的经度/纬度跨度
    overlap_ratio : tile 重叠比例
    """
    center_x_m, center_z_m = latlon_to_local_m(tile_lat, tile_lon, origin_lat, origin_lon)

    m_per_lon = meters_per_deg_lon(tile_lat)
    tile_width_m = lon_step * m_per_lon
    tile_height_m = lat_step * METERS_PER_DEG_LAT

    # OBJ 空间：模型范围大约 [-0.81, 0.81]
    obj_half = 0.81
    model_width = tile_width_m * (1 + overlap_ratio)
    model_height = tile_height_m * (1 + overlap_ratio)
    scale_x = (model_width / 2) / obj_half
    scale_z = (model_height / 2) / obj_half

    obj_x = vertices[:, 0]
    obj_y = vertices[:, 1]
    obj_z = vertices[:, 2]

    # 旋转 90°：X → Z，Z → -X
    rx = -obj_z
    ry = obj_y
    rz = obj_x

    world_x = center_x_m + rx * scale_x
    world_y = ry * (scale_x + scale_z) / 2
    world_z = center_z_m + rz * scale_z

    result = np.column_stack([world_x, world_y, world_z])
    if vertices.shape[1] >= 6:
        result = np.column_stack([world_x, world_y, world_z, vertices[:, 3:]])
    return result


def world_to_latlon_batch(world_x: np.ndarray, world_z: np.ndarray,
                          origin_lat: float, origin_lon: float) -> Tuple[np.ndarray, np.ndarray]:
    """批量将世界坐标转回 WGS84 经纬度"""
    lat = -world_z / METERS_PER_DEG_LAT + origin_lat
    lon = world_x / meters_per_deg_lon(origin_lat) + origin_lon
    return lat, lon


def latlon_distance_m(lat1: float, lon1: float,
                      lat2: float, lon2: float) -> float:
    """两点间近似距离（米）"""
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * meters_per_deg_lon((lat1 + lat2) / 2)
    return math.hypot(dlat, dlon)


# ============================================================
# 文件名解析
# ============================================================

_SAT_FILENAME_RE = re.compile(r"sat_([\d\.\-]+)_([\d\.\-]+)\.")


def extract_lat_lon_from_filename(filename: str) -> Tuple[Optional[float], Optional[float]]:
    """从文件名中提取经纬度"""
    m = _SAT_FILENAME_RE.search(filename)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def get_satellite_filename(lat: float, lon: float) -> str:
    """根据经纬度生成卫星图文件名"""
    return f"sat_{lat:.6f}_{lon:.6f}.png"


# ============================================================
# 邻接图
# ============================================================

def build_adjacency(faces: np.ndarray, n_verts: int) -> list:
    """根据面表构建顶点邻接表"""
    adj = [[] for _ in range(n_verts)]
    for face in faces:
        for i in range(3):
            v0, v1, v2 = int(face[i]), int(face[(i + 1) % 3]), int(face[(i + 2) % 3])
            adj[v0].append(v1)
            adj[v0].append(v2)
    for i in range(n_verts):
        if adj[i]:
            adj[i] = list(set(adj[i]))
    return adj