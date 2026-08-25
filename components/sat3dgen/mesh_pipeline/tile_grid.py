"""
卫星图网格计算模块
---------------
根据建筑 BBox 自动计算覆盖所需的卫星图 tile 网格。
"""

from typing import List

from .types import GeoBBox, GeoCoord, GridTile
from .utils import get_satellite_filename


def compute_satellite_grid(bbox: GeoBBox,
                           lat_step: float,
                           lon_step: float,
                           overlap_ratio: float = 0.10) -> List[GridTile]:
    """
    计算覆盖 bbox 所需的卫星图网格中心点。

    Parameters
    ----------
    bbox : 建筑包围盒
    lat_step, lon_step : 每 tile 的纬度/经度跨度
    overlap_ratio : 相邻 tile 的重叠比例

    Returns
    -------
    List[GridTile] : 按行优先排列的 tile 列表，每个带 index 编号
    """
    step_lat = lat_step * (1 - overlap_ratio)
    step_lon = lon_step * (1 - overlap_ratio)

    tiles = []
    idx = 0
    lat = bbox.min_lat + lat_step / 2
    while lat < bbox.max_lat:
        lon = bbox.min_lon + lon_step / 2
        while lon < bbox.max_lon:
            rlat, rlon = round(lat, 6), round(lon, 6)
            filename = get_satellite_filename(rlat, rlon)
            tiles.append(GridTile(lat=rlat, lon=rlon, filename=filename, index=idx))
            idx += 1
            lon += step_lon
        lat += step_lat

    return tiles


def get_tile_centers(tiles: List[GridTile]) -> List[GeoCoord]:
    """提取 GridTile 列表的 (lat, lon) 坐标"""
    return [GeoCoord(lat=t.lat, lon=t.lon) for t in tiles]