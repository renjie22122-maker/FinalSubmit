"""
数据下载模块
----------
Google 卫星图下载、街景（全景）下载、多位置全景图计算。
"""

import math
import numpy as np
from pathlib import Path
from typing import List, Tuple, Optional, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

import requests

from .config import Config
from .types import GeoBBox, GridTile


# ============================================================
# 卫星图下载
# ============================================================

def download_satellite_tile(lat: float, lon: float, api_key: str,
                            save_dir: Path, zoom: int = 20,
                            size: int = 640) -> bool:
    """下载单个 Google 卫星图 tile，返回是否成功"""
    from .utils import get_satellite_filename

    filename = get_satellite_filename(lat, lon)
    save_path = save_dir / filename

    if save_path.exists():
        return True

    url = (
        "https://maps.googleapis.com/maps/api/staticmap"
        f"?center={lat:.6f},{lon:.6f}"
        f"&zoom={zoom}"
        f"&size={size}x{size}"
        "&maptype=satellite"
        "&format=png"
        f"&key={api_key}"
    )

    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.content) > 5000:
            save_path.write_bytes(r.content)
            return True
        return False
    except Exception as e:
        print(f"    下载失败 {filename}: {e}")
        return False


def download_satellite_tiles(tiles: List[GridTile], api_key: str,
                             config: Config) -> Dict[str, bool]:
    """并行下载所有卫星图 tile"""
    print(f"\n  下载卫星图 ({len(tiles)} tiles)...")
    config.sat_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    with ThreadPoolExecutor(max_workers=config.download_workers) as executor:
        futures = {}
        for tile in tiles:
            future = executor.submit(
                download_satellite_tile,
                tile.lat, tile.lon, api_key,
                config.sat_dir, config.zoom, config.img_size,
            )
            futures[future] = tile

        for future in as_completed(futures):
            tile = futures[future]
            success = future.result()
            results[tile.filename] = success
            if not success:
                print(f"    [x] {tile.filename}")

    sat_ok = sum(1 for v in results.values() if v)
    print(f"  卫星图: {sat_ok}/{len(tiles)} 成功")
    return results


# ============================================================
# 街景/全景图下载
# ============================================================

def download_panorama(lat: float, lon: float, api_key: str,
                      save_dir: Path, fov: int = 90, size: int = 640,
                      headings: List[int] = None) -> Optional[Path]:
    """
    下载多角度拼接全景街景图。

    从 Google Street View API 下载多个方向的街景图并水平拼接。

    Parameters
    ----------
    lat, lon : 街景位置
    api_key : Google API Key
    save_dir : 保存目录
    fov : 每个方向的 FOV
    size : 每张图的尺寸
    headings : 方向列表

    Returns
    -------
    全景图路径，或 None
    """
    if headings is None:
        headings = [0, 90, 180, 270]

    save_path = save_dir / f"panorama_{lat:.6f}_{lon:.6f}.jpg"
    if save_path.exists():
        return save_path

    tiles = []
    for heading in headings:
        tile_path = save_dir / f"streetview_{lat:.6f}_{lon:.6f}_h{heading}.jpg"
        if tile_path.exists():
            tiles.append(np.array(Image.open(str(tile_path)).convert("RGB")))
            continue

        url = (
            "https://maps.googleapis.com/maps/api/streetview"
            f"?size={size}x{size}"
            f"&location={lat},{lon}"
            f"&fov={fov}"
            f"&heading={heading}"
            "&source=outdoor"
            f"&key={api_key}"
        )

        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200 and len(r.content) > 5000:
                content = r.content
                if b"sorry" not in content.lower() and b"maperror" not in content.lower():
                    tile_path.write_bytes(content)
                    tiles.append(np.array(Image.open(str(tile_path)).convert("RGB")))
                    continue
            print(f"    [WARN] heading={heading} 下载失败 (HTTP {r.status_code})")
        except Exception as e:
            print(f"    [WARN] heading={heading} 下载异常: {e}")

    if not tiles:
        print(f"    [ERROR] 所有方向的街景都下载失败")
        return None

    if len(tiles) == 1:
        Image.fromarray(tiles[0]).save(str(save_path))
    else:
        pano = np.concatenate(tiles, axis=1)
        Image.fromarray(pano).save(str(save_path))
        print(f"    -> 全景街景 ({len(tiles)} 张拼接): {save_path}")

    return save_path


# ============================================================
# 多位置全景图位置计算
# ============================================================

def compute_panorama_positions(bbox: GeoBBox, offset_m: float = 15,
                               spacing_m: float = 35) -> List[Tuple[float, float]]:
    """
    根据建筑包围盒计算四周的全景图位置。

    对每一边（北/南/东/西），向外偏移 offset_m 米，
    每隔 spacing_m 米放置一个全景图位置。

    Returns
    -------
    List[Tuple[lat, lon]]
    """
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(bbox.center_lat))
    offset_deg_lat = offset_m / m_per_deg_lat
    offset_deg_lon = offset_m / m_per_deg_lon

    positions = []

    # 北边
    side_len_m = (bbox.max_lon - bbox.min_lon) * m_per_deg_lon
    n = max(1, int(round(side_len_m / spacing_m)))
    for i in range(n):
        frac = (i + 0.5) / n
        lon = bbox.min_lon + frac * (bbox.max_lon - bbox.min_lon)
        lat = bbox.max_lat + offset_deg_lat
        positions.append((lat, lon))

    # 南边
    for i in range(n):
        frac = (i + 0.5) / n
        lon = bbox.max_lon - frac * (bbox.max_lon - bbox.min_lon)
        lat = bbox.min_lat - offset_deg_lat
        positions.append((lat, lon))

    # 东边
    side_len_m = (bbox.max_lat - bbox.min_lat) * m_per_deg_lat
    n = max(1, int(round(side_len_m / spacing_m)))
    for i in range(n):
        frac = (i + 0.5) / n
        lon = bbox.max_lon + offset_deg_lon
        lat = bbox.min_lat + frac * (bbox.max_lat - bbox.min_lat)
        positions.append((lat, lon))

    # 西边
    for i in range(n):
        frac = (i + 0.5) / n
        lon = bbox.min_lon - offset_deg_lon
        lat = bbox.max_lat - frac * (bbox.max_lat - bbox.min_lat)
        positions.append((lat, lon))

    print(f"  计算了 {len(positions)} 个全景图位置")
    return positions


def download_multi_panoramas(positions: List[Tuple[float, float]],
                              api_key: str,
                              config: Config) -> Dict[str, Path]:
    """
    在多个位置下载全景街景图。

    Returns
    -------
    Dict[str, Path] : 位置键 -> 全景图路径
    """
    print(f"\n  下载全景街景（{len(positions)} 个位置）...")
    config.pano_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for i, (lat, lon) in enumerate(positions):
        pano_path = download_panorama(
            lat, lon, api_key, config.pano_dir,
            config.pano_fov, config.pano_size, config.pano_headings,
        )
        if pano_path:
            key = f"pano_{i:03d}_{lat:.6f}_{lon:.6f}"
            results[key] = pano_path
            print(f"  [v] 位置 {i + 1}/{len(positions)}: ({lat:.6f}, {lon:.6f})")
        else:
            print(f"  [x] 位置 {i + 1}/{len(positions)}: ({lat:.6f}, {lon:.6f})")

    print(f"  全景街景: {len(results)}/{len(positions)} 成功")
    return results


# ============================================================
# 综合下载器
# ============================================================

class DataDownloader:
    """数据下载管理器"""

    def __init__(self, config: Config):
        self.config = config

    def download_all(self, tiles: List[GridTile], bbox: GeoBBox) -> Dict:
        """下载全部所需数据"""
        api_key = self.config.google_api_key
        if not api_key:
            print("  未提供 API Key，跳过下载")
            return {"satellite": {}, "streetview": {}}

        print(f"\n{'=' * 60}")
        print(f"数据下载 ({len(tiles)} tiles)")
        print(f"{'=' * 60}")

        results = {"satellite": {}, "streetview": {}}

        # 卫星图
        results["satellite"] = download_satellite_tiles(tiles, api_key, self.config)

        # 全景街景
        pano_positions = compute_panorama_positions(
            bbox,
            offset_m=self.config.pano_offset_m,
            spacing_m=self.config.pano_spacing_m,
        )
        pano_results = download_multi_panoramas(pano_positions, api_key, self.config)
        results["streetview"]["multi"] = len(pano_results) > 0
        results["streetview"]["paths"] = pano_results

        return results