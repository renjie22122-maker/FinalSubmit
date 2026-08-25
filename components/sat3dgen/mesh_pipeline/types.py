"""
共享数据模型
----------
所有模块间传递的核心数据结构。
"""

from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional


@dataclass
class GeoBBox:
    """地理包围盒（WGS84 经纬度）"""
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def center_lat(self) -> float:
        return (self.min_lat + self.max_lat) / 2.0

    @property
    def center_lon(self) -> float:
        return (self.min_lon + self.max_lon) / 2.0

    @property
    def width_lat(self) -> float:
        return self.max_lat - self.min_lat

    @property
    def width_lon(self) -> float:
        return self.max_lon - self.min_lon

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)

    @classmethod
    def from_tuple(cls, t: Tuple[float, float, float, float]) -> "GeoBBox":
        return cls(t[0], t[1], t[2], t[3])


@dataclass
class GeoCoord:
    """地理坐标（WGS84）"""
    lat: float
    lon: float

    def as_tuple(self) -> Tuple[float, float]:
        return (self.lat, self.lon)


@dataclass
class GridTile:
    """单个卫星图 tile"""
    lat: float
    lon: float
    filename: str
    index: int = 0

    @property
    def coord(self) -> GeoCoord:
        return GeoCoord(self.lat, self.lon)


@dataclass
class MeshData:
    """内存中的网格数据"""
    vertices: "np.ndarray"   # (N, 6)  [x, y, z, r, g, b]
    faces: "np.ndarray"      # (M, 3)  整数顶点索引

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def is_empty(self) -> bool:
        return self.vertex_count == 0 or self.face_count == 0


@dataclass
class TileMesh:
    """单个 tile 的网格数据（含地理元数据）"""
    mesh: MeshData
    lat: float
    lon: float
    origin_lat: float
    origin_lon: float

    @property
    def filename(self) -> str:
        return f"sat_{self.lat:.6f}_{self.lon:.6f}.obj"


@dataclass
class BuildingComponent:
    """已分离的建筑组件"""
    mesh: MeshData
    building_ids: List[int]
    component_index: int