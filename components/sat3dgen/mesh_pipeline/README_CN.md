# Mesh Pipeline — 建筑 3D 重构管线

基于卫星图像和 OpenStreetMap 数据的模块化建筑 3D 模型重构管线。底层依赖 Sat3DGen 生成基础模型、DSM 高度修正和多 tile 网格拼接。

## 目录

- [架构总览](#架构总览)
- [模块参考](#模块参考)
- [快速开始](#快速开始)
- [管线工作流](#管线工作流)
- [核心算法](#核心算法)
- [配置参考](#配置参考)
- [输出结构](#输出结构)
- [依赖项](#依赖项)
- [测试](#测试)
- [常见问题](#常见问题)

---

## 架构总览

```
                   ┌──────────────────────────────────────────┐
                   │              Pipeline (编排器)            │
                   └──────────────────────────────────────────┘
                       │        │        │        │
                       ▼        ▼        ▼        ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ tile_grid│  │downloader│  │inference │  │mesh_    │  │height_   │
│ 瓦片网格 │  │  数据下载 │  │ Sat3DGen│  │merging  │  │correction│
│          │  │          │  │  推理    │  │网格拼接 │  │  高度修正 │
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
     │              │              │              │              │
     ▼              ▼              ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│GeoBBox   │  │Google    │  │Gradio    │  │分组缝合  │  │仅修正    │
│→ GridTile│  │StaticMap │  │Client    │  │(法线分组)│  │上表面    │
│          │  │+全景街景 │  │→ OBJ文件 │  │          │  │          │
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
                                                        │
                                                        ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│building_ │  │facade_   │  │export    │  │OSM       │  │DSM       │
│extraction│  │enhancement│  │  导出    │  │Loader    │  │Loader    │
│建筑提取  │  │ 立面增强  │  │          │  │OSM加载器 │  │DSM加载器 │
└──────────┘  └──────────┘  └──────────┘  └──────────┘  └──────────┘
```

管线读取卫星图像瓦片，通过 Sat3DGen 生成 3D 网格，使用基于表面法线的分组缝合进行拼接，利用 DSM 数据进行语义高度修正，最终提取独立建筑并导出水密模型。

---

## 模块参考

### 核心模块

| 模块 | 文件 | 说明 |
|------|------|------|
| **Config** | `config.py` | 集中式 `Config` dataclass。所有可调参数（API 密钥、路径、瓦片尺寸、缝合阈值、DSM/OSM 路径）在此管理。使用 `__post_init__` 自动推导子目录路径。 |
| **Types** | `types.py` | 共享数据模型：`GeoBBox`、`GeoCoord`、`GridTile`、`MeshData`、`TileMesh`、`BuildingComponent`。所有模块间通信使用这些有类型结构。 |
| **Utils** | `utils.py` | 底层工具函数：WGS84 ↔ 局部坐标转换、文件名解析（`extract_lat_lon_from_filename`）、邻接图构建（`build_adjacency`）。 |
| **IO** | `io.py` | OBJ 和 PLY 读写器。`parse_obj` 以 numpy 数组形式返回 `(vertices, faces)`；`write_obj`/`write_ply` 处理序列化。 |

### 数据获取

| 模块 | 文件 | 说明 |
|------|------|------|
| **Tile Grid** | `tile_grid.py` | 计算覆盖建筑包围盒所需的卫星图像瓦片网格。考虑相邻瓦片之间的重叠比例。 |
| **Downloader** | `downloader.py` | 下载 Google 静态地图卫星瓦片和街景全景图（多方向拼接）。使用 `ThreadPoolExecutor` 进行并行下载。`DataDownloader` 类编排卫星图 + 全景图下载。 |
| **OSM Loader** | `osm_loader.py` | 加载本地 OSM GeoJSON 数据（建筑、水域、绿地、道路）。使用 Shapely `STRtree` 空间索引进行快速批量分类。同时查询 Overpass API，本地文件作为回退方案。 |
| **DSM Loader** | `dsm_loader.py` | 将 GeoTIFF DSM 瓦片加载到内存中。可选高斯滤波以去除树木/汽车噪声。提供 `query_heights_batch` 进行高效高度查询。 |

### 3D 生成与处理

| 模块 | 文件 | 说明 |
|------|------|------|
| **Inference** | `inference.py` | 通过 Gradio API 调用 Sat3DGen（`/generate_mesh` 端点）。`Sat3DGenRunner` 类运行批量推理并加载生成的 OBJ 文件及边缘裁剪。 |
| **Mesh Merging** | `mesh_merging.py` | 核心合并逻辑：加载所有瓦片 OBJ、裁剪重叠边界、转换到世界坐标、执行**分组缝合**（上/下/侧表面分别缝合）、应用**OSM 语义预对齐**以统一跨瓦片建筑高度。 |
| **Height Correction** | `height_correction.py` | 使用 OSM 标签 + DSM 数据进行语义高度修正。道路/水域/绿地表面使用 DSM 基准 + 裁剪细节（±5m）。建筑使用完整 DSM 中值 + 相对偏移。**仅修正上表面顶点**，下表面保持不变。 |
| **Building Extraction** | `building_extraction.py` | 从合并场景中提取建筑网格。步骤：OSM 分类、地面高度计算、面裁剪、地平面裁剪（`clip_faces_to_ground`）、内部面剔除、底部开口闭合（水密化）。 |

### 后处理与导出

| 模块 | 文件 | 说明 |
|------|------|------|
| **Facade Enhancement** | `facade_enhancement.py` | 可选的 FrankenGAN (bikeGAN) 纹理增强：BigSUR 语义分割 → 立面/窗户增强 → 门增强。使用文件监视模式进行 GPU 推理。 |
| **Export** | `export.py` | OBJ + PLY 格式的高级导出函数。处理顶点颜色编码。 |
| **Pipeline** | `pipeline.py` | 主编排器，将完整的 9 步工作流实现为 `Pipeline` 类。每一步是一个委托给相应模块的方法。 |
| **CLI** | `cli.py` | 通过 `argparse` 的命令行界面。支持 `--api-key`、`--lat`、`--lon`、`--skip-download`、`--skip-inference` 等标志。 |

---

## 快速开始

### 安装

```bash
# 核心依赖
pip install numpy scipy shapely rasterio pyproj requests Pillow

# 推理所需
pip install gradio_client

# 可选：交互式地图
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

### 命令行

```bash
# 完整管线：下载 + 推理 + 合并
python -m mesh_pipeline.cli \
    --api-key YOUR_KEY \
    --lat 51.5109 --lon -0.1349 \
    --name my_building

# 跳过下载（使用已有图像）
python -m mesh_pipeline.cli \
    --lat 51.5109 --lon -0.1349 \
    --skip-download

# 跳过推理（使用已有 OBJ 文件）
python -m mesh_pipeline.cli \
    --lat 51.5109 --lon -0.1349 \
    --skip-download --skip-inference
```

---

## 管线工作流

### 第 1 步：获取建筑数据
从 OSM 查询建筑足迹（Overpass API → 本地 GeoJSON 回退）。返回带有可配置填充的 `GeoBBox`。

### 第 2 步：计算瓦片网格
计算覆盖建筑包围盒且具有可配置重叠比例（默认 10%）的卫星图像瓦片中心。

### 第 3 步：下载数据
下载 Google 静态地图卫星瓦片（并行，8 个 worker）和街景全景图（多方向，4 个方向拼接）。

### 第 4 步：Sat3DGen 推理
为每个卫星瓦片调用 Sat3DGen Gradio API（`/generate_mesh`）。结果缓存在 `mesh_dir/` 中。

### 第 5 步：合并与预对齐与缝合

**OSM 语义预对齐：**
- 两遍扫描：收集所有瓦片中每个建筑的上表面 Y 中值，然后将低瓦片拉高到最高屋顶高度。
- 仅影响标记为建筑的上表面顶点。
- 防止同一建筑的不同部分在缝合后出现高度不一致。

**基于分组的缝合：**
- 按法线方向对顶点分类：上表面（`dot > 0.3`）、下表面（`dot < -0.3`）、侧面（其他）。
- 为每个组单独构建 KDTree。
- 仅在同一组内合并 → 防止下表面顶点与相邻瓦片的上表面顶点合并。

### 第 6 步：高度修正
使用 OSM 语义标签应用基于 DSM 的高度修正：
- **非建筑区域**（道路/水域/绿地/其他）：DSM 基准 + 裁剪细节（±5m）
- **建筑区域**：DSM 中值 + 完整相对偏移（保留屋顶形状）
- **下表面顶点永远不会被修改**。
- 语义边界平滑（排除下表面）。

### 第 7 步：建筑提取
- OSM 分类 → 面级别裁剪（保留有顶点在地面以上的面）。
- 地面高度统一：所有 <ground 的顶点夹紧到地面高度。
- 内部面剔除（基于法线方向）。
- 通过 Ear Clipping 三角剖分闭合底部开口。

### 第 8 步：建筑分离与水密化
- 通过 BFS 遍历分离连通分量。
- 对每个建筑：
  1. **地平面裁剪**（`clip_faces_to_ground`）：穿越地平面的三角形被分割，地下部分被丢弃。
  2. **开口闭合**：地面层的边界环被三角剖分形成平底。
  3. **内部面剔除**：法线朝内的三角形被剔除。
  4. 导出为独立的 OBJ 文件。

### 第 9 步：立面增强（可选）
FrankenGAN 纹理增强管线：BigSUR 分割 → 立面增强 → 门增强 → 纹理映射。

---

## 核心算法

### 1. 表面分类（`compute_surface_labels`）

每个顶点根据其**顶点法线**分配一个表面标签：

| 标签 | 条件 | 含义 |
|------|------|------|
| 0 | `normal · (0,1,0) > 0.3` | 上表面 |
| 1 | `normal · (0,1,0) < -0.3` | 下表面 |
| 2 | 其他 | 侧面 |

### 2. 分组缝合（`stitch_tiles`）

```
对每个表面组（上表面、下表面、侧面）：
    1. 过滤属于该组的顶点
    2. 在 (x, z) 坐标上构建 KDTree
    3. 查询跨越瓦片边界的球体内邻近顶点
    4. 合并匹配对 → 重建面索引

结果：上表面 ↔ 上表面，下表面 ↔ 下表面，侧面 ↔ 侧面
```

### 3. OSM 语义预对齐（`_apply_semantic_prealign`）

```
对每个出现于 ≥2 个瓦片中的 building_id：
    ref_height = max(所有瓦片的上表面Y中值)
    对每个具有较低高度的瓦片：
        vertices[building & 上表面] += (ref_height - 瓦片Y中值)

仅拉高低瓦片；从不降低高瓦片。
```

### 4. 地平面裁剪（`clip_faces_to_ground`）

```
对每个三角面：
    如果 3 个顶点全部 ≥ ground_height → 保留
    如果 3 个顶点全部 < ground_height → 丢弃
    如果 2 上 + 1 下 → 分割为 2 个三角形
    如果 1 上 + 2 下 → 分割为 1 个三角形

边交点通过线性插值计算：y = gh
```

### 5. 高度修正平移

```
对语义区域（道路/建筑/水域/等）中的每个顶点：
    corrected_y = dsm_中值 + (vertex_y - model_avg)
    
平移保留局部细节，同时对 DSM 宏观高度进行对齐。
```

---

## 配置参考

### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `google_api_key` | `""` | Google Cloud API 密钥 |
| `work_dir` | `pipeline_output` | 根输出目录 |
| `zoom` | 20 | 卫星图像缩放级别 |
| `img_size` | 640 | 瓦片图像尺寸（像素） |
| `overlap_ratio` | 0.10 | 相邻瓦片重叠比例 |
| `crop_ratio` | 0.05 | OBJ 文件边缘裁剪比例 |
| `mesh_resolution` | 256 | Sat3DGen 网格提取分辨率 |
| `stitch_distance` | 0.5 | KDTree 合并半径（米） |
| `pano_fov` | 90 | 街景全景视野角度 |
| `pano_headings` | [0, 90, 180, 270] | 全景拼接方向 |
| `download_workers` | 8 | 并行下载线程数 |
| `dsm_gaussian_sigma` | 3.0 | DSM 高斯滤波 σ |
| `building_padding_m` | 30.0 | 建筑包围盒填充（米） |

### 路径配置

所有输出路径从 `work_dir` 自动推导：

```
{work_dir}/
├── satellite/          # 已下载的卫星瓦片
├── panorama/           # 街景全景图
├── meshes/             # Sat3DGen 输出 OBJ
├── final/              # 最终合并+修正模型
│   └── buildings/      # 分离后的建筑 OBJ
└── osm/                # 缓存的 OSM 数据
```

---

## 输出结构

```
pipeline_output/final_vXX/
├── test_merge_scene.obj          # 合并场景（DSM 修正前）
├── test_merge_scene_corrected.obj  # 合并场景（DSM 修正后）
├── test_building_clean.obj       # 建筑提取结果
└── buildings/
    ├── building_20037.obj         # 独立水密建筑
    ├── building_1172_1290_20342.obj
    └── ...
```

---

## 依赖项

```
numpy        # 数组运算
scipy        # KDTree、高斯滤波
shapely      # GeoJSON 多边形运算
rasterio     # DSM GeoTIFF 读取
pyproj       # 坐标转换（WGS84 ↔ EPSG:27700）
requests     # Google API 和 Overpass API HTTP 调用
Pillow       # 图像读写
gradio_client # Sat3DGen API（可选）
folium       # 交互式地图（可选）
```

---

## 测试

### 完整管线测试

```bash
python test_mesh_pipeline_merge.py
```

该脚本：
1. 在 `pipeline_output/meshes/` 中查找所有 OBJ 文件
2. 运行合并 + 预对齐 + 分组缝合
3. 应用 DSM 高度修正
4. 提取建筑
5. 对地平面裁剪面
6. 使建筑成为水密模型
7. 移除内部面
8. 导出独立的建筑 OBJ

输出到 `pipeline_output/final_v{auto_increment}/`。

---

## 常见问题

**问：为什么分组缝合使用表面法线？**
答：不加分组，Tile A 的下表面顶点可能与 Tile B 的上表面顶点合并（两者在 (x,z) 上空间接近，但 Y 相差 20+ 米）。按法线方向分组可防止这种跨表面错误。

**问：为什么需要 OSM 预对齐？**
答：Sat3DGen 独立生成每个瓦片。跨越多个瓦片的建筑在每个瓦片中可能有不同的 Y 基线。预对齐在缝合前将它们统一到一致的高度。

**问：每个建筑的地面高度如何确定？**
答：对于每个建筑，使用附近道路标记顶点（50m 半径内）的 Y 中值作为地面参考。如果附近没有道路，回退到全局道路 Y 中值。

**问：`clip_faces_to_ground` 做了什么？**
答：它通过精确在 `y = ground_height` 处切割穿过的三角形，移除地平面以下的几何体，用干净的裁剪三角形替换"拉伸"的退化面。这为水密化开口闭合生成平坦的底部边界。

---

## License

MIT