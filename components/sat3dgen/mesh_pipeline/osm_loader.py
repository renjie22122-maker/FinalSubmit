"""
OSM 数据加载与分类模块
--------------------
加载本地 OSM GeoJSON 数据，使用 STRtree 空间索引进行快速批量分类。
同时提供获取建筑的接口（Overpass API / 本地数据）。
"""

import math
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import numpy as np
import requests
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

from .config import Config
from .types import GeoBBox


class OSMLoader:
    """
    OSM 数据加载器（使用 STRtree 空间索引）。

    加载 building / building_with_height / water / green / road geojson，
    提供批量分类和建筑查询功能。
    """

    def __init__(self, config: Config):
        """
        Parameters
        ----------
        config : Config
        """
        self.buildings: List[Tuple] = []     # (Polygon, height or None)
        self.water_bodies: List = []
        self.greens: List = []
        self.road_polys: List = []

        # STRtree 索引
        self.building_tree: Optional[STRtree] = None
        self.water_tree: Optional[STRtree] = None
        self.green_tree: Optional[STRtree] = None
        self.road_tree: Optional[STRtree] = None

        self._load_osm_data(config)

    # ---- 加载 ----

    def _load_osm_data(self, config: Config):
        """从本地 GeoJSON 加载所有 OSM 数据"""
        print("\n[OSM] 加载 OSM 数据...")
        osm_dir = config.osm_data_dir

        def _load_geojson(filename: str) -> list:
            path = osm_dir / filename
            if not path.exists():
                return []
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)['features']

        # 建筑（普通）
        for feat in _load_geojson('building.geojson'):
            try:
                poly = shape(feat['geometry'])
                if poly.is_valid:
                    self.buildings.append((poly, None))
            except Exception:
                pass

        # 建筑（带高度）
        for feat in _load_geojson('building_with_height.geojson'):
            try:
                poly = shape(feat['geometry'])
                if poly.is_valid:
                    h = feat['properties'].get('height')
                    self.buildings.append((poly, float(h) if h else None))
            except Exception:
                pass

        # 水体
        for feat in _load_geojson('water.geojson'):
            try:
                poly = shape(feat['geometry'])
                if poly.is_valid:
                    self.water_bodies.append(poly)
            except Exception:
                pass

        # 绿地
        for feat in _load_geojson('green.geojson'):
            try:
                poly = shape(feat['geometry'])
                if poly.is_valid:
                    self.greens.append(poly)
            except Exception:
                pass

        # 道路（线条，做 buffer）
        for feat in _load_geojson('road.geojson'):
            try:
                line = shape(feat['geometry'])
                if line.is_valid:
                    buf = line.buffer(0.000009, cap_style=2, join_style=2)
                    if buf.is_valid and not buf.is_empty:
                        self.road_polys.append(buf)
            except Exception:
                pass

        # 构建 STRtree 索引
        if self.buildings:
            self.building_tree = STRtree([b[0] for b in self.buildings])
        if self.water_bodies:
            self.water_tree = STRtree(self.water_bodies)
        if self.greens:
            self.green_tree = STRtree(self.greens)
        if self.road_polys:
            self.road_tree = STRtree(self.road_polys)

        print(f"  建筑: {len(self.buildings)}, 水体: {len(self.water_bodies)}, "
              f"绿地: {len(self.greens)}, 道路缓冲: {len(self.road_polys)}")

    # ---- 分类 ----

    def classify_batch(self, lons: np.ndarray, lats: np.ndarray
                       ) -> Tuple[np.ndarray, np.ndarray]:
        """
        批量分类顶点。

        Parameters
        ----------
        lons, lats : (N,) 经纬度数组

        Returns
        -------
        labels : (N,)  'building' | 'water' | 'road' | 'green' | 'other'
        building_ids : (N,)  -1 或建筑在 self.buildings 中的索引
        """
        n = len(lons)
        labels = np.full(n, 'other', dtype=object)
        building_ids = np.full(n, -1, dtype=np.int32)
        poly_to_idx = {id(p): i for i, (p, _) in enumerate(self.buildings)}

        for i in range(n):
            pt = Point(lons[i], lats[i])
            lbl = 'other'
            bid = -1

            if self.building_tree:
                for idx in self.building_tree.query(pt):
                    poly, _ = self.buildings[idx]
                    if poly.contains(pt) or poly.touches(pt):
                        lbl = 'building'
                        bid = poly_to_idx.get(id(poly), -1)
                        break

            if lbl != 'building' and self.water_tree:
                for idx in self.water_tree.query(pt):
                    if self.water_bodies[idx].contains(pt) or self.water_bodies[idx].touches(pt):
                        lbl = 'water'
                        break

            if lbl not in ('building', 'water') and self.road_tree:
                for idx in self.road_tree.query(pt):
                    if self.road_polys[idx].contains(pt) or self.road_polys[idx].touches(pt):
                        lbl = 'road'
                        break

            if lbl not in ('building', 'water', 'road') and self.green_tree:
                for idx in self.green_tree.query(pt):
                    if self.greens[idx].contains(pt) or self.greens[idx].touches(pt):
                        lbl = 'green'
                        break

            labels[i] = lbl
            building_ids[i] = bid

        return labels, building_ids

    def classify_single_point(self, lon: float, lat: float) -> Tuple[str, int]:
        """分类单点"""
        lons = np.array([lon])
        lats = np.array([lat])
        labels, bids = self.classify_batch(lons, lats)
        return labels[0], int(bids[0])

    # ---- 建筑查询 ----

    def get_buildings_in_bbox(self, bbox: GeoBBox) -> List[int]:
        """获取在包围盒内的建筑索引列表"""
        from shapely.geometry import box
        bbox_poly = box(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat)
        result = []
        for i, (poly, _) in enumerate(self.buildings):
            if poly.intersects(bbox_poly):
                result.append(i)
        return result

    def get_building_height(self, building_id: int) -> Optional[float]:
        """获取建筑高度（如果有）"""
        if 0 <= building_id < len(self.buildings):
            return self.buildings[building_id][1]
        return None


# ============================================================
# 建筑查询（Overpass API + 本地 OSM 回退）
# ============================================================

def fetch_building_from_osm(lat: float, lon: float,
                            config: Config) -> Optional[Dict]:
    """
    从 OSM 数据中获取指定坐标附近的建筑。

    先尝试 Overpass API，失败则回退到本地 GeoJSON。

    Returns
    -------
    建筑 GeoJSON Feature dict，或 None
    """
    radius_m = config.osm_search_radius_m

    # 尝试 Overpass API
    lat_delta = radius_m / 111320.0
    lon_delta = radius_m / (111320.0 * math.cos(math.radians(lat)))
    bbox_str = f"{lon - lon_delta},{lat - lat_delta},{lon + lon_delta},{lat + lat_delta}"

    overpass_url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:30];
    (
      way["building"]({bbox_str});
      relation["building"]({bbox_str});
    );
    out body geom;
    """

    try:
        r = requests.get(overpass_url, params={"data": query}, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                "Building3DPipeline/1.0"})
        if r.status_code == 200:
            data = r.json()
            elements = data.get('elements', [])
            if elements:
                min_dist = float('inf')
                best = None
                for el in elements:
                    if 'geometry' not in el:
                        continue
                    geom = el['geometry']
                    clat = sum(g['lat'] for g in geom) / len(geom)
                    clon = sum(g['lon'] for g in geom) / len(geom)
                    dist = math.hypot(clat - lat, clon - lon)
                    if dist < min_dist:
                        min_dist = dist
                        best = el
                if best:
                    tags = best.get('tags', {})
                    name = tags.get('name', tags.get('building', 'unknown'))
                    height = tags.get('height', tags.get('building:levels', '?'))
                    print(f"  找到建筑: {name}, 高度={height}, "
                          f"顶点数={len(best['geometry'])}")
                    return best
        else:
            print(f"  Overpass API 返回 {r.status_code}，回退到本地数据...")
    except Exception as e:
        print(f"  Overpass API 请求失败: {e}，回退到本地数据...")

    # 回退到本地
    return _fetch_building_from_local(lat, lon, config.osm_data_dir)


def _fetch_building_from_local(lat: float, lon: float,
                                osm_data_dir: Path) -> Optional[Dict]:
    """从本地 OSM GeoJSON 中查找建筑"""
    print(f"  从本地 OSM 数据中查找 ({lat:.6f}, {lon:.6f}) 附近的建筑...")
    pt = Point(lon, lat)

    for geojson_file in ['building_with_height.geojson', 'building.geojson']:
        path = osm_data_dir / geojson_file
        if not path.exists():
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            best_dist = float('inf')
            best_feat = None

            for feat in data['features']:
                try:
                    poly = shape(feat['geometry'])
                    if not poly.is_valid:
                        continue
                    if poly.contains(pt) or poly.touches(pt):
                        best_feat = feat
                        best_dist = 0
                        break
                    dist = poly.distance(pt)
                    if dist < best_dist and dist < 0.001:
                        best_dist = dist
                        best_feat = feat
                except Exception:
                    continue

            if best_feat:
                tags = best_feat.get('properties', {})
                name = tags.get('name', tags.get('building', 'unknown'))
                height = tags.get('height', tags.get('building:levels', '?'))
                print(f"  从本地 {geojson_file} 找到建筑: {name}, 高度={height}")
                return best_feat
        except Exception as e:
            print(f"  读取 {geojson_file} 失败: {e}")

    print(f"  本地 OSM 数据中也未找到建筑")
    return None


def get_building_bbox(building: Dict, padding_m: float = 30) -> GeoBBox:
    """
    从建筑 GeoJSON 计算带 padding 的包围盒。

    兼容 Overpass API 和本地 GeoJSON 两种格式。
    """
    geom = building['geometry']

    if isinstance(geom, list):
        lats = [g['lat'] for g in geom]
        lons = [g['lon'] for g in geom]
    elif isinstance(geom, dict):
        coords = geom.get('coordinates', [])
        if coords and isinstance(coords[0], list):
            ring = coords[0]
            lons = [c[0] for c in ring]
            lats = [c[1] for c in ring]
        else:
            lats = [51.5]
            lons = [-0.13]
    else:
        lats = [51.5]
        lons = [-0.13]

    lat_pad = padding_m / 111320.0
    lon_pad = padding_m / (111320.0 * math.cos(math.radians(np.mean(lats))))

    return GeoBBox(
        min_lon=min(lons) - lon_pad,
        min_lat=min(lats) - lat_pad,
        max_lon=max(lons) + lon_pad,
        max_lat=max(lats) + lat_pad,
    )