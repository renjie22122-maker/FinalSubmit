# Mesh Pipeline — 3D Building Reconstruction Pipeline

A modular pipeline for reconstructing 3D building models from satellite imagery and OpenStreetMap data. Built on top of Sat3DGen, DSM height correction, and multi-tile mesh stitching.

> 中文版本: [README_CN.md](README_CN.md)

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Module Reference](#module-reference)
- [Quick Start](#quick-start)
- [Pipeline Workflow](#pipeline-workflow)
- [Core Algorithms](#core-algorithms)
- [Configuration Reference](#configuration-reference)
- [Output Structure](#output-structure)
- [Dependencies](#dependencies)
- [Testing](#testing)
- [FAQ](#faq)

---

## Architecture Overview

```
                   ┌──────────────────────────────────────────┐
                   │              Pipeline (orchestrator)      │
                   └──────────────────────────────────────────┘
                       │        │        │        │
                       ▼        ▼        ▼        ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ tile_grid│  │downloader│  │inference │  │mesh_    │  │height_   │
│          │  │          │  │          │  │merging  │  │correction│
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
     │              │              │              │              │
     ▼              ▼              ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│GeoBBox   │  │Google API│  │Gradio    │  │Group     │  │Upper-    │
│→ GridTile│  │Static    │  │Client    │  │Stitch    │  │surface   │
│          │  │Map+      │  │→ OBJ     │  │(normal-  │  │only      │
│          │  │StreetView│  │files     │  │based)    │  │translation│
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
                                                        │
                                                        ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│building_ │  │facade_   │  │export    │  │OSM       │  │DSM       │
│extraction│  │enhancement│  │          │  │Loader    │  │Loader    │
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
```

The pipeline reads satellite image tiles, generates 3D meshes via Sat3DGen, stitches them with surface-aware grouping, applies semantic height correction using DSM data, extracts individual buildings, and exports watertight models.

---

## Module Reference

### Core Modules

| Module | File | Description |
|--------|------|-------------|
| **Config** | `config.py` | Centralized `Config` dataclass. All tunable parameters (API keys, paths, tile sizes, stitch thresholds, DSM/OSM paths) are managed here. Uses `__post_init__` to auto-derive subdirectory paths. |
| **Types** | `types.py` | Shared data models: `GeoBBox`, `GeoCoord`, `GridTile`, `MeshData`, `TileMesh`, `BuildingComponent`. All inter-module communication uses these typed structures. |
| **Utils** | `utils.py` | Low-level utilities: WGS84 ↔ local coordinate transforms, filename parsing (`extract_lat_lon_from_filename`), adjacency graph builder (`build_adjacency`). |
| **IO** | `io.py` | OBJ and PLY reader/writer. `parse_obj` returns `(vertices, faces)` as numpy arrays; `write_obj`/`write_ply` handle serialization. |

### Data Acquisition

| Module | File | Description |
|--------|------|-------------|
| **Tile Grid** | `tile_grid.py` | Computes the satellite image tile grid covering a building's bounding box. Accounts for overlap ratio between adjacent tiles. |
| **Downloader** | `downloader.py` | Downloads Google Static Maps satellite tiles and Street View panoramas (multi-heading stitching). Uses `ThreadPoolExecutor` for parallel downloads. `DataDownloader` class orchestrates satellite + panorama downloads. |
| **OSM Loader** | `osm_loader.py` | Loads local OSM GeoJSON data (buildings, water, green, roads). Uses Shapely `STRtree` spatial index for fast batch classification. Also queries Overpass API with local-file fallback for building lookup. |
| **DSM Loader** | `dsm_loader.py` | Loads GeoTIFF DSM tiles into memory. Applies optional Gaussian filtering to remove tree/car noise. Provides `query_heights_batch` for efficient height lookup. |

### 3D Generation & Processing

| Module | File | Description |
|--------|------|-------------|
| **Inference** | `inference.py` | Calls Sat3DGen via Gradio API (`/generate_mesh` endpoint). `Sat3DGenRunner` class runs batch inference and loads resulting OBJ files with edge cropping. |
| **Mesh Merging** | `mesh_merging.py` | Core merging logic: loads all tile OBJs, crops overlap boundaries, transforms to world coordinates, performs **group-based stitching** (upper/lower/side surfaces stitched separately), and applies **OSM semantic prealignment** to align building heights across tiles. |
| **Height Correction** | `height_correction.py` | Semantic height correction using OSM labels + DSM data. Road/water/green surfaces use DSM baseline + clipped detail (±5m). Buildings use full DSM median + relative offset. **Only upper-surface vertices** are modified; lower surfaces remain unchanged. |
| **Building Extraction** | `building_extraction.py` | Extracts building meshes from the merged scene. Steps: OSM classification, ground height computation, face cropping, ground plane clipping (`clip_faces_to_ground`), internal face removal, bottom-hole closing (watertight). |

### Post-Processing & Export

| Module | File | Description |
|--------|------|-------------|
| **Facade Enhancement** | `facade_enhancement.py` | Optional FrankenGAN (bikeGAN) texture enhancement: BigSUR semantic segmentation → facade/window enhancement → door enhancement. Uses file-watch pattern for GPU inference. |
| **Export** | `export.py` | High-level export functions for OBJ + PLY formats. Handles vertex color encoding. |
| **Pipeline** | `pipeline.py` | Main orchestrator implementing the full 9-step workflow as a `Pipeline` class. Each step is a method that delegates to the appropriate module. |
| **CLI** | `cli.py` | Command-line interface via `argparse`. Supports `--api-key`, `--lat`, `--lon`, `--skip-download`, `--skip-inference` flags. |

---

## Quick Start

### Installation

```bash
# Core dependencies
pip install numpy scipy shapely rasterio pyproj requests Pillow

# For inference
pip install gradio_client

# Optional: interactive map
pip install folium ipyleaflet
```

### Python API

```python
from mesh_pipeline import Config, Pipeline

config = Config(
    google_api_key="YOUR_GOOGLE_API_KEY",
    work_dir="pipeline_output",
)

pipeline = Pipeline(config)
results = pipeline.run(
    lat=51.5109,
    lon=-0.1349,
    building_name="my_building",
)
```

### Command Line

```bash
# Full pipeline with download + inference + merge
python -m mesh_pipeline.cli \
    --api-key YOUR_KEY \
    --lat 51.5109 --lon -0.1349 \
    --name my_building

# Skip download (use existing images)
python -m mesh_pipeline.cli \
    --lat 51.5109 --lon -0.1349 \
    --skip-download

# Skip inference (use existing OBJs)
python -m mesh_pipeline.cli \
    --lat 51.5109 --lon -0.1349 \
    --skip-download --skip-inference
```

---

## Pipeline Workflow

### Step 1: Building Acquisition
Query building footprint from OSM (Overpass API → local GeoJSON fallback). Returns a `GeoBBox` with configurable padding.

### Step 2: Grid Computation
Calculate satellite image tile centers covering the building BBox with configurable overlap ratio (default 10%).

### Step 3: Data Download
Download Google Static Maps satellite tiles (parallel, 8 workers) and Street View panoramas (multi-heading, 4 directions stitched).

### Step 4: Sat3DGen Inference
Call Sat3DGen Gradio API (`/generate_mesh`) for each satellite tile. Results cached in `mesh_dir/`.

### Step 5: Merge & Prealign & Stitch

**OSM Semantic Prealignment:**
- Two-pass scan: collect per-building upper-surface Y medians across all tiles, then raise low tiles to the max roof height.
- Only affects building-labelled upper-surface vertices.
- Prevents same-building parts from having inconsistent heights after stitching.

**Group-Based Stitching:**
- Classify vertices by normal direction: upper surface (`dot > 0.3`), lower surface (`dot < -0.3`), side surface (otherwise).
- Build a separate KDTree for each group.
- Merge only within the same group → prevents lower-surface vertices from being merged with adjacent tile's upper-surface vertices.

### Step 6: Height Correction
Apply DSM-based height correction using OSM semantic labels:
- **Non-building** (road/water/green/other): DSM baseline + clipped detail (±5m)
- **Building**: DSM median + full relative offset (preserves roof shape)
- **Lower surface vertices are never modified**.
- Semantic boundary smoothing (excluding lower surface).

### Step 7: Building Extraction
- OSM classification → face-level cropping (keep faces with vertices above ground).
- Ground height unification: all <ground vertices clamped to ground height.
- Internal face removal (normal-direction based).
- Bottom-hole closing via Ear Clipping triangulation.

### Step 8: Building Separation & Watertight
- Separate connected components by BFS traversal.
- For each building:
  1. **Ground plane clipping** (`clip_faces_to_ground`): triangles crossing the ground plane are split, sub-ground portions discarded.
  2. **Hole closing**: boundary loops at ground level are triangulated to form a flat bottom face.
  3. **Internal face removal**: inward-facing triangles are culled.
  4. Export as individual OBJ files.

### Step 9: Facade Enhancement (Optional)
FrankenGAN texture enhancement pipeline: BigSUR segmentation → facade enhancement → door enhancement → texture mapping.

---

## Core Algorithms

### 1. Surface Classification (`compute_surface_labels`)

Each vertex is assigned a surface label based on its **vertex normal** (average of adjacent face normals):

| Label | Condition | Meaning |
|-------|-----------|---------|
| 0 | `normal · (0,1,0) > 0.3` | Upper surface |
| 1 | `normal · (0,1,0) < -0.3` | Lower surface |
| 2 | otherwise | Side surface |

### 2. Group-Based Stitching (`stitch_tiles`)

```
For each surface group (upper, lower, side):
    1. Filter vertices belonging to this group
    2. Build KDTree over (x, z) coordinates
    3. Query ball-point neighbours across tile boundaries
    4. Merge matched pairs → rebuild face indices

Result: Upper ↔ Upper, Lower ↔ Lower, Side ↔ Side
```

### 3. OSM Semantic Prealignment (`_apply_semantic_prealign`)

```
For each building_id appearing in ≥2 tiles:
    ref_height = max(median_upper_y across all tiles)
    For each tile with lower height:
        vertices[building & upper] += (ref_height - tile_median)

Only raises low tiles; never lowers high tiles.
```

### 4. Ground Plane Clipping (`clip_faces_to_ground`)

```
For each triangle face:
    if all 3 vertices >= ground_height → keep
    if all 3 vertices < ground_height → discard
    if 2 above + 1 below → split into 2 triangles
    if 1 above + 2 below → split into 1 triangle

Edge intersection computed by linear interpolation: y = gh
```

### 5. Height Correction Translation

```
For each vertex in a semantic region (road/building/water/etc):
    corrected_y = dsm_median + (vertex_y - model_mean)
    
Translation preserves local detail while aligning to DSM macro-height.
```

---

## Configuration Reference

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `google_api_key` | `""` | Google Cloud API key |
| `work_dir` | `pipeline_output` | Root output directory |
| `zoom` | 20 | Satellite image zoom level |
| `img_size` | 640 | Tile image size (px) |
| `overlap_ratio` | 0.10 | Adjacent tile overlap |
| `crop_ratio` | 0.05 | Edge crop ratio for OBJ files |
| `mesh_resolution` | 256 | Sat3DGen mesh extraction resolution |
| `stitch_distance` | 0.5 | KDTree merge radius (m) |
| `pano_fov` | 90 | Street View panorama FOV |
| `pano_headings` | [0, 90, 180, 270] | Panorama stitching directions |
| `download_workers` | 8 | Parallel download threads |
| `dsm_gaussian_sigma` | 3.0 | DSM Gaussian filter sigma |
| `building_padding_m` | 30.0 | Building BBox padding (m) |

### Path Configuration

All output paths are auto-derived from `work_dir`:

```
{work_dir}/
├── satellite/          # Downloaded satellite tiles
├── panorama/           # Street View panoramas
├── meshes/             # Sat3DGen output OBJs
├── final/              # Final merged + corrected models
│   └── buildings/      # Separated building OBJs
└── osm/                # Cached OSM data
```

---

## Output Structure

```
pipeline_output/final_vXX/
├── test_merge_scene.obj          # Merged scene (before DSM correction)
├── test_merge_scene_corrected.obj  # Merged scene (after DSM correction)
├── test_building_clean.obj       # Building extraction result
└── buildings/
    ├── building_20037.obj         # Individual watertight building
    ├── building_1172_1290_20342.obj
    └── ...
```

---

## Dependencies

```
numpy        # Array operations
scipy        # KDTree, gaussian_filter
shapely      # GeoJSON polygon operations
rasterio     # DSM GeoTIFF reading
pyproj       # Coordinate transformations (WGS84 ↔ EPSG:27700)
requests     # Google API & Overpass API HTTP calls
Pillow       # Image I/O
gradio_client # Sat3DGen API (optional)
folium       # Interactive map (optional)
```

---

## Testing

### Full Pipeline Test

```bash
python test_mesh_pipeline_merge.py
```

This script:
1. Finds all OBJ files in `pipeline_output/meshes/`
2. Runs merge + prealign + group stitch
3. Applies DSM height correction
4. Extracts buildings
5. Clips faces to ground plane
6. Makes buildings watertight
7. Removes internal faces
8. Exports individual building OBJs

Output goes to `pipeline_output/final_v{auto_increment}/`.

---

## FAQ

**Q: Why does group stitching use surface normals?**
A: Without grouping, lower-surface vertices from Tile A can be merged with upper-surface vertices from Tile B (both are spatially close in (x,z) but differ by 20+ meters in Y). Grouping by normal direction prevents this cross-surface error.

**Q: Why is OSM prealignment needed?**
A: Sat3DGen generates each tile independently. A building spanning multiple tiles may have different Y baselines in each tile. Prealignment brings them to a consistent height before stitching.

**Q: How is ground height determined per building?**
A: For each building, the median Y of nearby road-labelled vertices (within 50m radius) is used as the ground reference. Falls back to global road median if no nearby roads exist.

**Q: What does `clip_faces_to_ground` do?**
A: It removes geometry below the ground plane by exactly slicing crossing triangles at `y = ground_height`, replacing "stretched" degenerate faces with clean clipped ones. This produces a flat bottom boundary ready for watertight hole closing.

---

## License

MIT