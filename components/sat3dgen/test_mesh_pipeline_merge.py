"""
测试 mesh_pipeline 合并流程 (V12 restored + 删除底部面 + 诊断导出 + 双版本建筑导出)
流程:
  1. load_and_merge_tiles (保留底部面)
  2. [诊断] 每个tile裁剪后导出
  3. 删除底部面
  4. [诊断] 每个tile去底部面后导出
  5. stitch_tiles
  6. 导出 test_merge_scene.obj
  7. DSM修正 → 导出 test_merge_scene_corrected.obj → 建筑提取 → 建筑导出到 buildings_dsm/
  8. 剪枝: 跳过DSM → 建筑提取 → 建筑导出到 buildings_no_dsm/
"""
import sys
from pathlib import Path
import numpy as np
from mesh_pipeline.config import Config
from mesh_pipeline.mesh_merging import load_and_merge_tiles, stitch_tiles, _remove_bottom_faces_preserving_ranges
from mesh_pipeline.height_correction import semantic_height_correction
from mesh_pipeline.building_extraction import extract_building_mesh, separate_building_components, clip_faces_to_ground, _close_mesh_holes, _remove_internal_faces
from mesh_pipeline.export import export_model
from mesh_pipeline.osm_loader import OSMLoader
from mesh_pipeline.dsm_loader import DSMLoader
from mesh_pipeline.utils import world_to_latlon_batch

VERSION = 6
while (Path(f"pipeline_output/final_v{VERSION}")).exists():
    VERSION += 1

config = Config(work_dir=Path("pipeline_output"))
config.output_dir = Path(f"pipeline_output/final_v{VERSION}")
config.output_dir.mkdir(parents=True, exist_ok=True)
print(f"\nV{VERSION}: {config.output_dir} (双版本建筑导出)")
print(f"找到 {len(sorted(config.mesh_dir.rglob('*.obj')))} 个 OBJ 文件")

osm_loader = OSMLoader(config)
dsm_loader = DSMLoader(config, apply_gaussian_filter=True, sigma=3.0)

# ---- Step 1: 合并 + OSM预对齐 (保留底部面) ----
vertices, faces, origin_lat, origin_lon, tile_ranges = load_and_merge_tiles(
    sorted(config.mesh_dir.rglob("*.obj")), config,
    osm_loader=osm_loader, remove_bottom=False,
)

# ---- 诊断快照 a: 每个tile裁剪后 (带底部面) ----
debug_dir_a = config.output_dir / "debug_per_tile_crop"
debug_dir_a.mkdir(parents=True, exist_ok=True)
for tile_idx, (start, end) in enumerate(tile_ranges):
    tile_face_mask = np.all((faces >= start) & (faces < end), axis=1)
    tile_faces = faces[tile_face_mask]
    if len(tile_faces) == 0: continue
    tile_verts_in_faces = np.unique(tile_faces.flatten())
    old_to_new = {old: new for new, old in enumerate(tile_verts_in_faces)}
    local_faces = np.array([[old_to_new[v] for v in f] for f in tile_faces], dtype=np.int32)
    local_verts = vertices[tile_verts_in_faces].copy()
    export_model(local_verts, local_faces, debug_dir_a, f"tile_{tile_idx:02d}")

# ---- Step 2: 删除底部面 ----
print("\n" + "=" * 60)
print("Step 2: 删除底部面")
print("=" * 60)
vertices, faces, tile_ranges = _remove_bottom_faces_preserving_ranges(vertices, faces, tile_ranges)

# ---- 诊断快照 b: 每个tile去底部面后 ----
debug_dir_b = config.output_dir / "debug_per_tile_no_bottom"
debug_dir_b.mkdir(parents=True, exist_ok=True)
for tile_idx, (start, end) in enumerate(tile_ranges):
    tile_face_mask = np.all((faces >= start) & (faces < end), axis=1)
    tile_faces = faces[tile_face_mask]
    if len(tile_faces) == 0: continue
    tile_verts_in_faces = np.unique(tile_faces.flatten())
    old_to_new = {old: new for new, old in enumerate(tile_verts_in_faces)}
    local_faces = np.array([[old_to_new[v] for v in f] for f in tile_faces], dtype=np.int32)
    local_verts = vertices[tile_verts_in_faces].copy()
    export_model(local_verts, local_faces, debug_dir_b, f"tile_{tile_idx:02d}")

# ---- Step 3: 分组缝合 ----
print("\n" + "=" * 60)
print("Step 3: 分组缝合")
print("=" * 60)
if len(tile_ranges) > 1:
    vertices, faces = stitch_tiles(vertices, faces, tile_ranges, config.stitch_distance)
export_model(vertices, faces, config.output_dir, "test_merge_scene")

# ---- 备份未DSM修正的网格 ----
vertices_no_dsm = vertices.copy()
faces_no_dsm = faces.copy()

# ---- Step 4: DSM 修正 ----
print("\n" + "=" * 60)
print("Step 4: DSM高度修正 (按OSM语义标签，所有顶点)")
print("=" * 60)
if not dsm_loader.is_empty:
    vertices, faces = semantic_height_correction(
        vertices, faces, osm_loader, dsm_loader, origin_lat, origin_lon, config,
    )
export_model(vertices, faces, config.output_dir, "test_merge_scene_corrected")

# ---- 辅助函数: 建筑提取 + 水密化 + 导出 ----
def extract_and_export_buildings(v, f, dir_name, label):
    """从给定网格提取建筑并导出到指定子目录"""
    print(f"\n  --- 建筑导出 ({label}) ---")
    bv, bf, bids, gh = extract_building_mesh(v, f, osm_loader, origin_lat, origin_lon, config)
    export_model(bv, bf, config.output_dir, f"test_building_clean_{label}")
    
    lats, lons = world_to_latlon_batch(bv[:, 0], bv[:, 2], origin_lat, origin_lon)
    labels, bld_ids = osm_loader.classify_batch(lons, lats)
    components = separate_building_components(bv, bf, bld_ids)
    
    bdir = config.output_dir / dir_name
    bdir.mkdir(parents=True, exist_ok=True)
    print(f"    共 {len(components)} 个连通分量")
    
    for comp_idx, comp_verts, comp_faces, comp_bids in components:
        bid_heights = [gh[b] for b in comp_bids if b in gh]
        ground_h = np.median(bid_heights) if bid_heights else np.percentile(comp_verts[:, 1], 10)
        
        comp_verts, comp_faces = clip_faces_to_ground(comp_verts, comp_faces, ground_h)
        if len(comp_faces) > 0:
            comp_faces = _close_mesh_holes(comp_verts, comp_faces, ground_height=ground_h)
        comp_verts, comp_faces = _remove_internal_faces(comp_verts, comp_faces)
        
        if len(comp_bids) == 1:
            bname = f"building_{list(comp_bids)[0]}"
        else:
            bname = "building_" + "_".join(str(b) for b in sorted(comp_bids))
        export_model(comp_verts, comp_faces, bdir, bname)

# ---- Step 5: DSM修正版本建筑导出 ----
extract_and_export_buildings(vertices, faces, "buildings_dsm", "dsm")

# ---- Step 6: 无DSM修正版本建筑导出 ----
extract_and_export_buildings(vertices_no_dsm, faces_no_dsm, "buildings_no_dsm", "no_dsm")

print(f"\n全部完成！输出目录: {config.output_dir}")