# myProject Bridge（Windows）

## 全景导入与门窗约束

推荐直接使用与原版 ChordAtlas 相同位置的 Block 后续入口，不复用 `data_builder` 以前的 panorama 数据：选中由当前 OSM footprint 建立的 `block`，在 Block Options 点击 `get Street View panoramas`。程序只根据这个 Block 的 footprint 和 workspace `manifest.json` 地理原点规划候选相机，查询 metadata，并按 pano id 去重；随后先下载一张样本并显示预览。只有在 `Approve selected-block Street View batch` 对话框点击 `Yes` 才会继续批量，点击 `No` 会保留样本并停止。完成后 GUI 会自动创建或刷新当前 workspace `panos` 目录的 PanoGen 图层，再按 `render panoramas -> find image features -> find profiles -> optimize` 使用真实门窗约束。

启动 GUI 前必须在同一进程环境设置 `GOOGLE_MAPS_API_KEY`。密钥只从环境变量读取，不写入项目或命令行。每个新 panorama 除 metadata 外还需要 6 次 640×640 图像请求来合成 2560×1280 JPEG；批量可能产生费用，确认样本、候选数量、API 配额和账单设置后再点 `Yes`。已有 JPEG 会按缓存规则复用。

以下 `import-streetview-panos` CLI 仍保留，适用于已经有与目标区域匹配的 ChordAtlas `todo.list` 的情况。

`import-streetview-panos` 把 ChordAtlas `todo.list` 作为清单，通过 Google Static Street View 的 metadata 与六个立方体视图生成严格 2:1 JPEG。API key **只**从当前进程的 `GOOGLE_MAPS_API_KEY` 读取，不接受命令行 key，也不会写进报告或请求日志。

先做完全离线的计划；未指定 `--output` 时，输出固定为当前配置的 `<workspace>\panos`：

```powershell
$todo = 'E:\path\to\current_workspace_todo.list'
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json import-streetview-panos `
  --todo $todo --dry-run --limit 1
```

`todo.list` 的 WGS84 坐标必须覆盖这个 config 对应的 workspace/目标建筑。`datasets\regent_osm\panos\todo.list` 只适用于 Regent 项目，绝不能与 `data_builder_london_on_demand` 或其他区域混用。

实时运行先只允许一张。检查生成的 JPEG 与 `streetview_sample_report.json` 后，才可显式确认并运行剩余记录：

```powershell
$env:GOOGLE_MAPS_API_KEY = '<restricted-key>'
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json import-streetview-panos `
  --todo $todo --limit 1
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json import-streetview-panos `
  --todo $todo --all --sample-approved
```

每次运行原子写入报告和 JPEG。成功发布新图后，已有 `panos.xml` 会原子改名为带时间戳的备份，避免 GUI 继续使用旧目录缓存。

重要：这里的 CLI importer **不会修改 `tweed.xml` 或自动创建图层**。CLI 首次使用必须在 GUI 选择 `Layers '+' → panos (jpg)` 并指向该 workspace 的 `panos` 目录。这个入口会严格读取 workspace `manifest.json` 的 `frame.origin_lat/origin_lon/axes`，检测到 X=东、Y=上、Z=南后自动启用 local 坐标；图层 UI 应显示 `coordinates: myProject local (X east / Z south)`。如果显示 `original geographic`，说明 manifest 缺失或无效，应先修复工作区而不是继续门窗投影。已有 PanoGen 图层可点击 `refresh panoramas` 重新扫描；按钮只刷新已有图层。Block Options 的 `get Street View panoramas` 则会自动创建或刷新正确的图层。随后按 Block 的 `render panoramas → find image features`，成功后 Java 会刷新 `FeatureCache`，再继续 profiles/optimize。

方向契约来自实际 ChordAtlas `Pano.castTo()`：图像中心为北，左右接缝为南，1/4 为西，3/4 为东，上边为天顶、下边为天底。myProject workspace 使用 X=东、Z=南，因此默认文件名为 `lat_lon_alt_180_90_0.001_panoId.jpg`，用 180° heading 抵消原 Pano 类按 X=西、Z=北解释方向的差异。只有把图导入原版 geographic 坐标工作区时才显式传 `--coordinate-mode original-geographic`，此时 heading 为 0°。`0.001°` roll 是绕过旧 PanoGen “90/0 无效记录”过滤的近零兼容值。报告会记录 coordinate mode 与最终 heading。离线测试覆盖方向、sample gate、密钥脱敏、原子发布/cache 失效及 CLI 项目输出：

```powershell
python -m unittest bridge.tests.test_streetview_panos -v
```

## 推荐工作流：先显示完整 OSM，选中后按需生成 MiniMesh

现在可以在没有初始 MiniMesh 的情况下启动 GUI。基础 GIS 图层会显示完整、未按现有网格裁剪的 OSM 建筑外轮廓；在 `select` 工具下右击某个轮廓后，GUI 才会为该选择创建独立任务：

1. 按建筑及至少 30 m 上下文规划固定 Web Mercator 卫星瓦片；
2. 先下载并验证全部必需的 640×640 PNG；
3. 仅调用 `E:\UCL\Project\Sat3DGen\mesh_pipeline` 顶层模块生成并合并网格，运行时明确拒绝 `mesh_generate_merge_pipeline` 子目录；
4. 检查有效瓦片覆盖、坐标和高于地面的屋顶覆盖；
5. 只有整个选择都通过时才发布 OBJ、转换 MiniMesh，并在 GUI 中同时加载 MiniGen 与 BlockGen。

下载、推理或完整性检查失败时，原 OSM 轮廓仍保留并可再次选择，部分模型不会进入 GUI。当前已准备好的项目是：

`E:\UCL\Project\myProject\projects\data_builder_london_on_demand`

它包含 984 个未裁剪的建筑外轮廓，并且有意不包含初始 MiniMesh。当前机器可直接验证和启动：

```powershell
Set-Location E:\UCL\Project\myProject\bridge
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json validate
.\scripts\launch_gui.ps1 -Config .\config\data_builder_london_on_demand.json
```

要让右击后的在线生成真正运行，必须先启动 Sat3DGen 的 Gradio 服务，并在**启动 GUI 的同一个 PowerShell 会话**中设置一个新建、受限的 Static Maps API key：

```powershell
.\scripts\start_sat3dgen.ps1
$env:GOOGLE_MAPS_API_KEY = '<new-restricted-key>'
.\scripts\launch_gui.ps1 -Config .\config\data_builder_london_on_demand.json
```

API key 只通过子进程环境继承，不写入 request、命令行、manifest 或日志。之前在聊天中公开过的 key 应先轮换并限制 API 与来源。首次创建或需要重建该工作区时运行：

```powershell
.\scripts\setup_on_demand.ps1 -Force
```

GUI 中选择 GIS 图层后，`mesh source on Select` 可选原有 workspace mesh、原有逐瓦片按需流程，或新增的 `big image app192 on demand`。选择顶部 `select`，右击橙色建筑轮廓；任务成功后会出现独立的 MiniMesh/Block 图层，随后选中 Block 使用 `find profiles` 等后续功能。逐瓦片和大图缓存互不覆盖，两个结果可同时加载并在 Layers 中分别勾选显示。更完整的界面步骤与故障位置见 [GUI_WORKFLOW.md](GUI_WORKFLOW.md)。

大图模式使用 zoom 20、Static Maps raw 640/retained 512 mosaic、app192、75% overlap 和 fractional-feather 融合。几何后自动运行 memory-bounded Sat3DGen 顶点着色，并严格验证 `mesh_colored.ply`、颜色 metadata/hash、全顶点覆盖和几何不变；RGB 会随裁取、DSM 和发布一直保留为 OBJ `v x y z r g b`，GUI 默认显示 colour layer。它按当前地块加 30m 上下文规划并缓存；只有严格覆盖且参数完全一致的彩色输出才会复用。上游 PLY 按已合并、已去下表面处理，bridge 不再运行去底面、stitch、重复面删除或小组件过滤，只执行一次地理坐标变换、选择裁取、必需 DSM 校正和标准 Block 发布；若没有经过验证的真实 RGB，则回退语义 BlockGen，绝不生成占位颜色。

注意：这里的 OBJ GIS 导出保留完整外环但不表达建筑内院孔洞；源 GeoJSON 的孔洞会被验证和统计。真实 Google 下载与 GPU 推理尚未在本次离线构建中执行，当前验收覆盖了规划、缓存/错误门禁、发布事务、Java 热加载集成、52 项 Python 测试以及最终 JAR 构建。

这个目录把 `data_builder`、Sat3DGen 顶层 `mesh_pipeline` 与当前 ChordAtlas GUI 串成一个可检查、可重复的 Windows 工作流。它不使用 Docker，也不会创建、更新或安装任何 Conda 包；所有 Python 子进程都通过现有的 `sat3dgen` 环境运行。

> 当前验证状态（2026-08-09）：6 个连续的顶层缓存 tile 已通过本 bridge 重新合并，生成 697,366 顶点 / 1,490,722 三角面的场景；已转换为 201 个 MiniMesh tile，导出 13 个相交 GIS footprint，`validate` 与 `doctor` 均为 `ok`，GUI 已成功启动并保持响应。完整 London AOI 的网络下载和 289-tile GPU 推理尚未执行，GUI 中的手动 Select/Profile 仍需按验收步骤点击。

## 固定边界

- ChordAtlas：`E:\UCL\Project\myProject`
- Sat3DGen：`E:\UCL\Project\Sat3DGen`
- data_builder：`E:\UCL\Project\data_builder`
- PyTorch 立面分割：`E:\UCL\Project\facade-segmentation\facade_pytorch`
- FrankenGAN：`E:\UCL\Project\FrankenGAN-test\bikegan`
- Conda 环境：已有的 `sat3dgen`

Sat3DGen 只允许导入 `E:\UCL\Project\Sat3DGen\mesh_pipeline\*.py` 顶层模块。bridge **绝不调用**：

```text
E:\UCL\Project\Sat3DGen\mesh_pipeline\mesh_generate_merge_pipeline
```

兼容驱动在导入后还会检查 `mesh_pipeline.__file__`；如果实际模块来自上述嵌套目录，会立即拒绝继续。
`test_mesh_pipeline_merge.py` 只作为行为审计参考，不是生产入口；bridge 不执行该脚本，而是按本次 bbox/tile 清单调用所需的顶层模块函数。

## 为什么由 myProject helper 驱动 mesh

bridge 不调用上游 `Pipeline.run()`，也不直接使用旧的 `mesh_pipeline.inference.run_sat3dgen_inference()`。当前顶层源码存在几项不适合作为 GUI 后端的已知问题：

1. `Pipeline.run()` 只接受单点 `lat/lon`，内部再解析建筑范围，没有直接的 bbox 入口。
2. 当前 `Pipeline.run()` 按两个值解包 `extract_building_mesh()`，而该函数实际返回四个值；完整流程可能在场景 OBJ 导出后失败。
3. 旧 inference 对 Gradio 返回值和 `/download_mesh` 的假设较脆弱。
4. 顶层测试脚本用 `mesh_dir.rglob("*.obj")` 读取整个历史缓存，可能把不同地理任务的 tile 合并到同一个场景。
5. 顶层底面清理以“面顶点最小 Y”判断，会把接触底部的侧面一起删除。

`src/myproject/top_level_mesh_driver.py` 仍复用顶层模块的 `Config`、tile grid、下载、merge、stitch、OSM/DSM 修正和 export，但补上以下边界：

- bbox 内的明确 tile 清单，不扫描并合并无关历史任务；
- 每次写 `top_level_pipeline_manifest.json`，记录计划、缺失、失败、实际选择、origin 和输出；
- 直接调用 `/generate_mesh`，递归解析返回路径、验证 OBJ，并用临时文件原子落盘；
- 只有三个顶点都位于底部带内时才删除三角面；
- 默认 dry-run，只有显式 `--execute` 才启动外部进程。

## 坐标与数据契约

### bbox

所有 bridge JSON/CLI bbox 均使用 WGS84，顺序固定为：

```text
[min_lon, min_lat, max_lon, max_lat]
```

`data_builder\osm_features\meta.json` 使用具名字段：

```json
{
  "bbox": {
    "min_lat": 51.4999,
    "min_lon": -0.1379,
    "max_lat": 51.513913,
    "max_lon": -0.118167
  }
}
```

映射到 bridge 数组时必须调整为 `[-0.1379, 51.4999, -0.118167, 51.513913]`。

配置中的两个范围用途不同：

- `area.target_bbox_wgs84`：最终进入 ChordAtlas、裁选 footprint，并用于覆盖率检查的目标区域。
- `area.fetch_bbox_wgs84`：下载 OSM/DSM 等源数据时允许使用的外扩区域，必须包含 target bbox。

data_builder 的 `meta.json` bbox 是卫星中心范围再外扩 margin 后的 **OSM 查询范围**。它不是原有卫星图的精确 tile 清单。根目录 `satellite/` 有 127 张稀疏图；`LondonDataSet/London/satellite/` 有 272 张（16×17），而 notebook 目标网格按相同公式应为 289 张（17×17），缺少最后一行 17 张。把外扩 bbox 直接交给矩形网格生成器会规划约 896 个额外 tile，因此不能这样重建原数据。

### 本地坐标

bridge 与 Sat3DGen 顶层合并约定一致：

- X：向东，单位米；
- Y：向上，单位米；
- Z：向南，单位米（向北是负 Z）；
- WGS84 局部换算：纬度使用 `111320 m/deg`，经度使用 `111320*cos(origin_lat) m/deg`；
- origin：本次**实际选择的 tile** 中独立取 `min(latitude)` 与 `min(longitude)`。

Sat3DGen 场景的 Y 通常不是 0。构建 MiniMesh 时，bridge 根据配置的地面基准（默认 Y 百分位数）施加一次垂直平移；GIS footprint OBJ 始终写成 `v x 0 z`。

### 输入与输出

| 数据 | 输入要求 | bridge 产物/用途 |
|---|---|---|
| OSM footprint | GeoJSON `Polygon` / `MultiPolygon`，WGS84 | 严格过滤、bbox 相交选择，导出 `footprints.obj`（Y=0）作为 GIS |
| DSM footprint | 可选 DSM/DTM，经 `extract-dsm` 先生成候选 GeoJSON | 再走与 OSM 相同的 footprint 导出路径 |
| 三维网格 | Y-up、米制、与 origin 对齐的 OBJ | 转换为 `minimesh/index.xml` 与分块 `model.obj` |
| 网格纹理 | 可选，同目录 MTL 与图片 | 源 MTL 会检查；没有 MTL 时仍可用于几何/Profile，之后再做纹理 |
| 全景图 | 可选、自有或另行许可、严格 2:1 equirectangular JPG | 按 manifest 校验后复制到 `panos/` |

## 配置模式

样例配置位于 `config/`：

- `data_builder_london_smoke.json`：从顶层 cache 精确选择同一连续区域的 6 个 `sat_*.obj`，再由 bridge 调用顶层 merge/stitch/export；不复用来源不明的 `improved_6tile_scene.obj`。它只覆盖目标 bbox 的约 0.96%，不能当作完整 London 结果。
- `data_builder_london_full.json`：按 notebook 的 17×17 网格公式规划全范围。现有 272 张卫星图缺 17 张；配置只从 `GOOGLE_MAPS_API_KEY` 环境变量补齐，并通过本机 Gradio 服务做推理，未显式执行前不会产生费用或 GPU 任务。

`mesh.mode`：

- `existing`：检查并转换 `mesh.source_obj`，不执行 Sat3DGen。
- `generate`：通过兼容驱动规划或执行顶层 Sat3DGen mesh 流程。

生成模式的 `mesh.tile_source`：

- `discovered`：从声明的 satellite/mesh 目录发现 bbox 内 `sat_<lat>_<lon>` 文件；最适合已有或稀疏 data_builder 数据。
- `data_builder_grid`：从 bbox 最小经纬度开始，按显式 `lat_step/lon_step` 规划；只有命名和步长与数据集完全一致时才使用。
- `top_grid`：调用顶层 `mesh_pipeline.tile_grid.compute_satellite_grid()`；其中心偏移和 overlap 规则与 data_builder 原图不一定相同。

生成任务应优先把 `mesh.origin.mode` 设为 `top_level_manifest`，让工作区使用实际选择 tile 产生的 origin；`existing` 模式可以使用经核验的 `explicit` origin。不要从整个共享 cache 的目录名盲算 origin。

## 建议执行顺序

在 PowerShell 中：

```powershell
Set-Location E:\UCL\Project\myProject\bridge

# 只显示路径、bbox 与待执行命令
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json plan

# 实际扫描精确 tile 清单并写缺失详情；不联网、不推理
.\scripts\myproject.ps1 --config .\config\data_builder_london_full.json run-mesh --scan-inputs

# 文件检查与只读外部探针
.\scripts\doctor.ps1

# 运行 bridge 单元测试；不安装依赖
.\scripts\test.ps1
```

6-tile 顶层 cache 的 smoke workspace（场景不存在时自动合并；已有场景时复用）：

```powershell
.\scripts\setup_smoke.ps1
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json validate
```

需要强制重合并时使用 `setup_smoke.ps1 -RegenerateMesh -Force`；`-Force` 会保留旧 workspace 为时间戳备份。

如果验证为 `ok`，再启动 GUI：

```powershell
.\scripts\launch_gui.ps1
```

全范围模板默认只显示 mesh 命令，不执行 GPU/网络任务：

```powershell
.\scripts\setup_full.ps1
.\scripts\myproject.ps1 --config .\config\data_builder_london_full.json run-mesh
```

完整范围的已验证输入扫描报告位于：

```text
E:\UCL\Project\myProject\projects\_mesh_jobs\data_builder_london_full_top_level\top_level_pipeline_manifest.json
```

当前报告为 289 个目标、272 张已有卫星图、17 张缺失卫星图、289 个待生成 OBJ；缺失图是纬度 `51.511448` 的最北整行。

只有检查配置、数据来源、预计耗时/费用、磁盘空间和 Gradio 服务后，才显式执行。先启动当前 Sat3DGen app：

```powershell
.\scripts\start_sat3dgen.ps1
```

然后执行完整任务：

```powershell
.\scripts\myproject.ps1 --config .\config\data_builder_london_full.json run-mesh --execute --timeout 7200
```

如果配置设置 `download_missing: true`，API key 只从当前进程的 `GOOGLE_MAPS_API_KEY` 环境变量继承，不写入 JSON、manifest 或命令行。若设置 `run_inference: true`，需要预先有兼容的 Sat3DGen Gradio `/generate_mesh` 服务；bridge 不会替你修改环境或安装服务依赖。

曾经粘贴到聊天、日志或源码里的 key 应立即轮换；新 key 应限制到所需 API、预算/配额与当前公网 IP。不要把 key 写进本仓库。

构建完整 ChordAtlas workspace：

```powershell
.\scripts\myproject.ps1 --config .\config\data_builder_london_full.json build
.\scripts\myproject.ps1 --config .\config\data_builder_london_full.json validate
.\scripts\myproject.ps1 --config .\config\data_builder_london_full.json launch --dry-run
```

`build --run-mesh` 会真的执行 mesh 生成；没有这个参数时，generate 模式要求预期的 `*_scene.obj` 已存在。

## 生成的 workspace

默认位于 `E:\UCL\Project\myProject\projects\<project_id>\`：

```text
<project_id>/
├── tweed.xml
├── manifest.json
├── footprints.obj
├── minimesh/
│   ├── index.xml
│   └── <tile>/model.obj
├── panos/                       # 仅启用并通过许可校验时存在
├── features/
├── exports/
└── logs/
    ├── minimesh-conversion.log
    ├── workspace-descriptor.log
    └── chordatlas-gui.log       # 启动 GUI 后
```

`WorkspaceCLI` 自动把 GIS、MiniMesh、可选 panos、地理 origin、PyTorch façade 路径、FrankenGAN 路径、Conda 可执行文件及环境名写入 `tweed.xml`。不要手工从另一个项目复制 `tweed.xml`。

## 可选全景图（合规路径）

bridge 不调用旧 Google panorama 下载入口。只导入明确自有或另行许可的 JPG：

1. 复制 `config/pano_manifest.example.csv`，每行填写绝对/相对源路径和姿态。
2. `ownership_confirmed` 必须逐行明确为 `true`。
3. 图片必须是可读 JPEG，宽度严格等于高度的两倍。
4. 运行：

```powershell
.\scripts\myproject.ps1 prepare-panos `
  --manifest .\config\my_panos.csv `
  --output E:\UCL\Project\myProject\projects\my_area\panos
```

整份 manifest 会先校验后复制；任一行失败都会拒绝导入，并生成 `panorama_import_report.json`。不要在 GUI 中使用旧的 `panos -> Download` 作为本工作流的数据来源。

## facade_pytorch 与 FrankenGAN 的边界

- `Select -> Block -> find profiles` 只需要 ChordAtlas、GIS 与 MiniMesh；**不用**启动 facade_pytorch 或 FrankenGAN。
- `find image features` 才会由 GUI 通过 `conda run -n sat3dgen python -B -m facade_pytorch` 启动 PyTorch 分割。它要求先有 `rendered.png` façade 输入；不是常驻服务。
- FrankenGAN watcher 只在 ChordAtlas 网络纹理生成阶段需要。需要时才运行：

```powershell
.\scripts\start_frankengan.ps1          # 先显示命令
.\scripts\start_frankengan.ps1 -Execute # 确认后启动
```

- BigSUR/FrankenGAN checkpoints 只影响可选 façade/window/door 纹理，不生成 GIS、Block、MiniMesh 或 Profile。

详细 GUI 操作和逐步验收见 [GUI_WORKFLOW.md](GUI_WORKFLOW.md)。
