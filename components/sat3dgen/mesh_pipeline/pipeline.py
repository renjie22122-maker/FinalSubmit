"""
管线编排器
--------
编排所有模块，执行完整的建筑 3D 重建管线。

Usage:
    from mesh_pipeline import Config, Pipeline
    config = Config(google_api_key="...")
    pipeline = Pipeline(config)
    pipeline.run(lat=51.5109, lon=-0.1349)
"""

import time
import math
import re
from pathlib import Path
from typing import List, Optional, Dict, Tuple

import numpy as np

from .config import Config
from .types import GeoBBox, GridTile
from .tile_grid import compute_satellite_grid
from .downloader import DataDownloader
from .inference import Sat3DGenRunner
from .mesh_merging import load_and_merge_tiles, stitch_tiles, _remove_bottom_faces_preserving_ranges
from .height_correction import semantic_height_correction
from .building_extraction import (
    extract_building_mesh, separate_building_components,
    clip_faces_to_ground, _close_mesh_holes, _remove_internal_faces,
)
from .facade_enhancement import FacadeEnhancer
from .export import export_model
from .osm_loader import OSMLoader, fetch_building_from_osm, get_building_bbox
from .dsm_loader import DSMLoader
from .utils import (
    extract_lat_lon_from_filename,
    world_to_latlon_batch,
    build_adjacency,
)


class Pipeline:
    """
    建筑 3D 重建主管线。

    工作流步骤：
      1. 获取建筑数据（OSM / Overpass API）
      2. 计算覆盖建筑的卫星图网格
      3. 下载卫星图 + 全景街景
      4. Sat3DGen 批量推理生成 mesh
      5. 多 tile 拼接 + 边界合并
      6. DSM 语义高度修正
      7. 建筑提取 + 网格清理
      8. 立面纹理增强（FrankenGAN）
      9. 导出最终模型
    """

    def __init__(self, config: Config):
        """
        Parameters
        ----------
        config : Config
            管线全局配置
        """
        self.config = config
        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保输出目录存在"""
        for d in [self.config.sat_dir, self.config.pano_dir,
                   self.config.mesh_dir, self.config.output_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # 运行管线
    # ============================================================

    def run(self, lat: Optional[float] = None,
            lon: Optional[float] = None,
            building_name: str = "building",
            skip_download: bool = False,
            skip_inference: bool = False) -> Dict[str, Path]:
        """
        运行完整管线。

        Parameters
        ----------
        lat, lon : 建筑坐标（WGS84）
        building_name : 建筑名称（用于输出文件命名）
        skip_download : 跳过数据下载
        skip_inference : 跳过 Sat3DGen 推理

        Returns
        -------
        Dict[str, Path] : 输出文件路径 {name: path}
        """
        t_total = time.time()
        config = self.config

        print("=" * 70)
        print("Building-to-3D Pipeline")
        print("=" * 70)
        print(f"工作目录: {config.work_dir.absolute()}")

        # ---- Step 1: 获取建筑 ----
        bbox = self._resolve_building(lat, lon)

        # ---- Step 2: 计算卫星图网格 ----
        tiles = self._compute_grid(bbox)

        # ---- Step 3: 下载数据 ----
        if not skip_download:
            self._download_data(tiles, bbox)

        # ---- Step 4: Sat3DGen 推理 ----
        if not skip_inference:
            obj_files = self._run_inference(tiles)
        else:
            obj_files = self._find_existing_objs(tiles)

        if not obj_files:
            print("\n没有可用的模型文件，管线终止")
            return {}

        # ---- Step 5-6: 合并 + 高度修正 ----
        scene_vertices, scene_faces, origin_lat, origin_lon = (
            self._merge_and_correct(obj_files)
        )

        # ---- 导出完整场景 ----
        obj_path, _ = export_model(
            scene_vertices, scene_faces,
            config.output_dir, f"{building_name}_scene"
        )

        # ---- Step 7: 建筑提取 + 网格清理 ----
        osm_loader = OSMLoader(config)
        building_verts, building_faces = extract_building_mesh(
            scene_vertices, scene_faces, osm_loader,
            origin_lat, origin_lon, config,
        )
        building_obj_path, _ = export_model(
            building_verts, building_faces,
            config.output_dir, f"{building_name}_clean"
        )

        # ---- 按建筑导出独立 OBJ ----
        per_building_paths = self._export_per_building(
            building_verts, building_faces, osm_loader,
            origin_lat, origin_lon,
        )

        # ---- Step 8: 立面纹理增强 ----
        enhanced_paths = self._enhance_facades(
            per_building_paths, building_verts, building_faces,
            osm_loader, obj_files, origin_lat, origin_lon,
        )

        if not enhanced_paths:
            # 回退：全场景增强
            pano_path = self._find_nearest_panorama(
                np.mean(building_verts[:, [0, 2]], axis=0),
                origin_lat, origin_lon
            )
            enhancer = FacadeEnhancer(config)
            enhanced_path = enhancer.enhance(
                building_obj_path, pano_path, config.output_dir
            )
            enhanced_paths["scene"] = enhanced_path

        # ---- 完成 ----
        elapsed = time.time() - t_total
        print(f"\n{'=' * 70}")
        print("管线完成!")
        print(f"  总耗时: {elapsed:.1f}s")
        for name, path in enhanced_paths.items():
            print(f"  {name}: {path}")
        print(f"{'=' * 70}")

        return enhanced_paths

    # ============================================================
    # 子步骤实现
    # ============================================================

    def _resolve_building(self, lat: Optional[float],
                           lon: Optional[float]) -> GeoBBox:
        """Step 1: 获取建筑 BBox"""
        print(f"\n{'=' * 60}")
        print("Step 1: 获取建筑数据")
        print(f"{'=' * 60}")

        building = None
        if lat is not None and lon is not None:
            print(f"  查询坐标 ({lat:.6f}, {lon:.6f}) 附近的建筑...")
            building = fetch_building_from_osm(lat, lon, self.config)

        if building is None:
            print("  未找到建筑，使用默认区域")
            bbox = GeoBBox(
                min_lon=(lon - 0.001) if lon else -0.1359,
                min_lat=(lat - 0.001) if lat else 51.5090,
                max_lon=(lon + 0.001) if lon else -0.1339,
                max_lat=(lat + 0.001) if lat else 51.5114,
            )
        else:
            bbox = get_building_bbox(building, self.config.building_padding_m)

        print(f"  BBox: ({bbox.min_lon:.6f}, {bbox.min_lat:.6f}) -> "
              f"({bbox.max_lon:.6f}, {bbox.max_lat:.6f})")
        return bbox

    def _compute_grid(self, bbox: GeoBBox) -> List[GridTile]:
        """Step 2: 计算卫星图网格"""
        print(f"\n{'=' * 60}")
        print("Step 2: 计算卫星图网格")
        print(f"{'=' * 60}")

        config = self.config
        tiles = compute_satellite_grid(
            bbox, config.lat_step, config.lon_step, config.overlap_ratio
        )
        print(f"  需要 {len(tiles)} 个卫星图 tile")
        for t in tiles:
            print(f"    [{t.index + 1}] ({t.lat:.6f}, {t.lon:.6f}) -> {t.filename}")
        return tiles

    def _download_data(self, tiles: List[GridTile], bbox: GeoBBox):
        """Step 3: 下载卫星图 + 街景"""
        downloader = DataDownloader(self.config)
        downloader.download_all(tiles, bbox)

    def _run_inference(self, tiles: List[GridTile]) -> List[Path]:
        """Step 4: Sat3DGen 推理"""
        runner = Sat3DGenRunner(self.config)
        return runner.run_batch(tiles)

    def _find_existing_objs(self, tiles: List[GridTile]) -> List[Path]:
        """查找已有的 OBJ 文件（跳过推理时使用）"""
        obj_files = []
        for tile in tiles:
            stem = Path(tile.filename).stem
            obj_path = self.config.mesh_dir / stem / f"{stem}.obj"
            if obj_path.exists():
                obj_files.append(obj_path)
        return sorted(obj_files)

    def _merge_and_correct(self, obj_files: List[Path]
                            ) -> Tuple[np.ndarray, np.ndarray, float, float]:
        """Step 5-6: 合并 tile + 删除底部面 + 语义高度修正"""
        config = self.config

        # 合并 + OSM预对齐
        osm_loader = OSMLoader(config)
        vertices, faces, origin_lat, origin_lon, tile_ranges = (
            load_and_merge_tiles(obj_files, config, osm_loader=osm_loader,
                                 remove_bottom=False)
        )

        # 删除底部面 (基于Y高度，10%裁剪后底面独立)
        vertices, faces, tile_ranges = _remove_bottom_faces_preserving_ranges(
            vertices, faces, tile_ranges
        )

        # 缝合 (分组: 上↔上, 下↔下, 侧↔侧)
        if len(obj_files) > 1:
            vertices, faces = stitch_tiles(
                vertices, faces, tile_ranges, config.stitch_distance
            )

        # DSM语义高度修正 (按OSM语义标签修正所有顶点)
        dsm_loader = DSMLoader(config)
        if not dsm_loader.is_empty:
            vertices, faces = semantic_height_correction(
                vertices, faces, osm_loader, dsm_loader,
                origin_lat, origin_lon, config,
            )

        return vertices, faces, origin_lat, origin_lon

    def _extract_and_export_buildings(self, vertices, faces, osm_loader,
                                       origin_lat, origin_lon, subdir):
        """提取建筑 + 水密化 + 导出到子目录"""
        building_verts, building_faces, building_ids, ground_heights = \
            extract_building_mesh(vertices, faces, osm_loader,
                                  origin_lat, origin_lon, self.config)

        lats, lons = world_to_latlon_batch(
            building_verts[:, 0], building_verts[:, 2], origin_lat, origin_lon
        )
        labels, bld_ids = osm_loader.classify_batch(lons, lats)
        components = separate_building_components(building_verts, building_faces, bld_ids)
        print(f"  共 {len(components)} 个连通分量")

        bdir = self.config.output_dir / subdir
        bdir.mkdir(parents=True, exist_ok=True)

        for comp_idx, comp_verts, comp_faces, comp_bids in components:
            bid_heights = [ground_heights[b] for b in comp_bids if b in ground_heights]
            gh = np.median(bid_heights) if bid_heights else np.percentile(comp_verts[:,1], 10)

            comp_verts, comp_faces = clip_faces_to_ground(comp_verts, comp_faces, gh)
            if len(comp_faces) > 0:
                comp_faces = _close_mesh_holes(comp_verts, comp_faces, ground_height=gh)
            comp_verts, comp_faces = _remove_internal_faces(comp_verts, comp_faces)

            bname = f"building_{list(comp_bids)[0]}" if len(comp_bids)==1 else \
                    "building_"+"_".join(str(b) for b in sorted(comp_bids))
            export_model(comp_verts, comp_faces, bdir, bname)

    def _export_per_building(self, vertices, faces, osm_loader,
                              origin_lat, origin_lon) -> Dict[int, Path]:
        """按连通分量分离并导出独立建筑 OBJ"""
        print(f"\n{'=' * 60}")
        print("按建筑导出独立 OBJ 文件")
        print(f"{'=' * 60}")

        config = self.config
        buildings_dir = config.output_dir / "buildings"
        buildings_dir.mkdir(parents=True, exist_ok=True)

        # 重新分类
        lats, lons = world_to_latlon_batch(
            vertices[:, 0], vertices[:, 2], origin_lat, origin_lon
        )
        labels, building_ids = osm_loader.classify_batch(lons, lats)

        # 分离连通分量
        components = separate_building_components(vertices, faces, building_ids)
        print(f"  共 {len(components)} 个连通分量")

        per_building = {}
        for comp_idx, comp_verts, comp_faces, comp_bids in components:
            bid_str = ",".join(str(b) for b in comp_bids)
            print(f"    分量{comp_idx + 1}: {len(comp_verts)} 顶点, "
                  f"OSM建筑ID={bid_str}")

            if len(comp_bids) == 1:
                bname = f"building_{list(comp_bids)[0]}"
            else:
                bname = "building_" + "_".join(str(b) for b in sorted(comp_bids))

            obj_p, _ = export_model(comp_verts, comp_faces, buildings_dir, bname)
            for bid in comp_bids:
                per_building[bid] = obj_p

        print(f"\n  建筑已保存到: {buildings_dir}")
        for bid, path in sorted(per_building.items()):
            print(f"    建筑 #{bid}: {path.name}")

        return per_building

    def _enhance_facades(self, per_building_paths: Dict[int, Path],
                          vertices, faces, osm_loader,
                          obj_files: List[Path],
                          origin_lat: float, origin_lon: float
                          ) -> Dict[int, Path]:
        """Step 8: 对每个建筑独立做立面增强"""
        print(f"\n{'=' * 60}")
        print("Step 7: 立面纹理增强")
        print(f"{'=' * 60}")

        if not per_building_paths:
            return {}

        config = self.config
        enhanced_dir = config.output_dir / "enhanced"
        enhanced_dir.mkdir(parents=True, exist_ok=True)

        enhancer = FacadeEnhancer(config)
        enhanced_paths = {}

        for bid, clean_obj_path in per_building_paths.items():
            print(f"\n  --- 建筑 #{bid} ---")

            # 找最近的全景图
            pano_path = self._find_nearest_panorama_for_building(
                bid, vertices, faces, osm_loader, origin_lat, origin_lon
            )
            if pano_path:
                print(f"    使用: {pano_path.name}")

            enhanced = enhancer.enhance(clean_obj_path, pano_path, enhanced_dir)
            enhanced_paths[bid] = enhanced

        return enhanced_paths

    def _find_nearest_panorama_for_building(self, bid: int,
                                             vertices, faces, osm_loader,
                                             origin_lat, origin_lon
                                             ) -> Optional[Path]:
        """为指定建筑找最近的全景图"""
        lats, lons = world_to_latlon_batch(
            vertices[:, 0], vertices[:, 2], origin_lat, origin_lon
        )
        labels, building_ids = osm_loader.classify_batch(lons, lats)

        mask = building_ids == bid
        if mask.sum() == 0:
            return None

        center_xy = np.mean(vertices[mask][:, [0, 2]], axis=0)
        return self._find_nearest_panorama(center_xy, origin_lat, origin_lon)

    def _find_nearest_panorama(self, center_xy: np.ndarray,
                                origin_lat: float, origin_lon: float
                                ) -> Optional[Path]:
        """根据世界坐标中心找最近的全景图"""
        config = self.config
        if not config.pano_dir.exists():
            return None

        pano_files = sorted(config.pano_dir.glob("panorama_*.jpg"))
        if not pano_files:
            return None

        # 转换中心到 WGS84
        blon = center_xy[0] / (111320.0 * math.cos(math.radians(origin_lat))) + origin_lon
        blat = -center_xy[1] / 111320.0 + origin_lat

        best_dist = float('inf')
        best_pano = None
        for pano_path in pano_files:
            m = re.search(r"panorama_([\d\.\-]+)_([\d\.\-]+)\.", pano_path.name)
            if m:
                plat, plon = float(m.group(1)), float(m.group(2))
                dist = math.hypot(plat - blat, plon - blon)
                if dist < best_dist:
                    best_dist = dist
                    best_pano = pano_path

        return best_pano