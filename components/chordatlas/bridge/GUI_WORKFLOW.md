# ChordAtlas GUI 操作与验收

> English version: [GUI_WORKFLOW_EN.md](GUI_WORKFLOW_EN.md)


## OSM 完整显示、按选择生成 MiniMesh

这是当前推荐入口。项目启动时只有完整 OSM 建筑外轮廓，不要求预先拥有城市 MiniMesh：

```powershell
Set-Location E:\UCL\Project\myProject\bridge
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json validate
.\scripts\start_sat3dgen.ps1
$env:GOOGLE_MAPS_API_KEY = '<new-restricted-key>'
.\scripts\launch_gui.ps1 -Config .\config\data_builder_london_on_demand.json
```

必须从设置了 `GOOGLE_MAPS_API_KEY` 的同一个 PowerShell 启动 GUI。key 不会写入磁盘或命令行；请不要复用已经公开的 key。

GUI 操作：

1. 左侧应只有 GIS 基础层，不出现初始 MiniMesh 属于正常状态；984 个 OSM 建筑外轮廓可见且不会因网格缺失而被裁掉。
2. 单击 GIS 图层行，在 Options 的 `mesh source on Select` 三选一：
   - `loaded workspace mesh (legacy)`：原有逻辑，从已经加载的 Mesh/MiniMesh 生成 Block；
   - `satellite tiles on demand`：保留原来的逐瓦片下载、推理、OSM 预对齐、stitch、DSM 路径；
   - `big image app192 on demand`：为当前地块下载大幅 mosaic，使用 app192、raw 640、75% overlap、fractional feather 一次融合，再运行 Sat3DGen 顶点着色、坐标对齐和 DSM。
3. 顶部选择 `select`，把视角对准目标后右击橙色建筑轮廓。
4. 状态会显示当前来源、影像准备、Sat3DGen 与 MiniMesh 转换阶段，成功后为 `Ready`。共享完全相同顶点的相邻 footprint 会作为一个连通选择处理。
5. 成功时 GUI 为每栋建筑新增隐藏的 MiniMesh（供空间操作）和可勾选的语义 Block；有 OBJ 顶点色时还新增可勾选的 colour layer。选中 Block 后可继续 `find profiles`、profiles `optimize`。逐瓦片与大图使用不同 selection ID，可同时加载，并在 Layers 中分别勾选显示。
6. 失败时不会加载 scene、残缺 OBJ 或半成品 MiniMesh，GIS 轮廓保持可见，可查看任务结果后重试。

每个任务的诊断文件位于：

```text
<workspace>\_selection_jobs\<selection-id>\tile_manifest.json
<workspace>\_selection_jobs\<selection-id>\result.json
<workspace>\logs\selected-mesh\<selection-id>.log
```

大图任务使用 `big-image-<geometry-hash>`，另有：

```text
<workspace>\_selection_jobs\big-image-<hash>\big_image_plan.json
<workspace>\_selection_jobs\big-image-<hash>\big_image\mosaic.png
<workspace>\_selection_jobs\big-image-<hash>\big_image\inference\mesh.ply
<workspace>\_selection_jobs\big-image-<hash>\big_image\inference\mesh_colored.ply
<workspace>\_selection_jobs\big-image-<hash>\big_image\inference\color_metadata.json
```

若配置中的已验证彩色大图缓存完整覆盖当前地块加 30m 上下文，会离线复用；否则才下载并按需推理。几何完成后，第二遍 memory-bounded Sat3DGen colour MLP 会使用同一 fractional-feather 窗口权重生成真实逐顶点 RGB；严格验证 hash、覆盖率、顶点/面数量、几何不变和不透明 alpha 后才发布。颜色会经过坐标变换、空间裁取、DSM 和地面归零原样保留到每栋 `cropped.obj`；GUI 默认显示 colour layer。无真实颜色时不会伪造 RGB，而是安全回退语义 BlockGen。

大图 PLY 被视为上游已经融合、已经去下表面的结果，bridge **不会**再次去底面、stitch、去重复面或丢小组件；它只做一次坐标变换、空间裁取、强制 DSM、统一地面归零和 Block 发布。

运行到 mesh pipeline 后，任务目录还会出现 `pipeline.stdout.log`、`pipeline.stderr.log` 与 `top_level_pipeline_manifest.json`。`PLANNED` 或 `FAILED` 结果只写入 `_selection_jobs`；只有成功发布时才会生成 `<workspace>\generated_blocks\<selection-id>\result.json`。

只有发布结果为 `READY` 且三个 OBJ 验证通过后才会提交 GUI。成功目录还包含 `cropped.obj`、`gis.obj`、`gis_footprints.obj` 和 Java 转换出的 `minimesh\index.xml`。重开 GUI 后，相同选择会复用合法 READY 缓存；失败的重试不会破坏上一次成功结果。

常见失败含义：

- 所需卫星瓦片未缓存且缺少 `GOOGLE_MAPS_API_KEY`：任务在联网前停止，不运行 Sat3DGen。
- HTTP 403、非 PNG 或尺寸错误：卫星瓦片未通过验证，不进入推理。
- Gradio 服务未启动或 `/generate_mesh` 失败：不发布模型。
- 某个必需瓦片或屋顶覆盖不足：判定为部分模型，不加载 GUI。
- GUI 仍只显示 OSM：先查看 `_selection_jobs\<selection-id>\result.json` 和 `logs\selected-mesh\<selection-id>.log`，这通常是安全门禁生效，不是 GIS 消失。

FrankenGAN 和 facade segmentation 不参与这一段“卫星图 → MiniMesh”的生成；它们分别在后续材质生成和 panorama/立面特征流程中使用。

## 以下章节：预生成 MiniMesh 的 smoke/full 工作区

下面原有的 `load all`、从既有 MiniMesh 裁剪到 `scratch` 等步骤只适用于批处理生成的 smoke/full workspace，不适用于上面的 OSM-only on-demand 工作区。

本页说明自动 workspace 启动后的最短主路径：

```text
OSM/DSM footprint -> GIS -> Select -> Block -> find profiles
                              +
                     Sat3DGen mesh -> MiniMesh
```

这条主路径不需要启动 facade_pytorch、FrankenGAN 或 panorama 下载服务。

> 当前自动验收：6-tile 顶层场景已生成，GIS/MiniMesh/tweed.xml/JAR 已验证，Windows GUI 已成功启动。下面的 Select/Profile 是需要在当前可见 GUI 中完成的手动视觉验收；完整 AOI 的性能和模型质量尚未宣称完成。

## 0. 启动前检查

在 PowerShell 中：

```powershell
Set-Location E:\UCL\Project\myProject\bridge

.\scripts\doctor.ps1
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json validate
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json launch --dry-run
```

只有 `validate` 返回 `status: ok` 后再启动：

```powershell
.\scripts\launch_gui.ps1
```

启动器会把目标 workspace 作为 `--project` 传给当前 JAR，并写日志：

```text
<workspace>\logs\chordatlas-gui.log
```

不要先双击旧 JAR，也不要在另一个 ChordAtlas 目录中手选 `tweed.xml`；那样很容易加载旧插件或旧 workspace。

## 1. 确认自动加载的层

GUI 左侧 generator/layer 列表至少应出现：

- `gis(o) footprints.obj`：橙色 GIS footprint；
- `minimesh`：蓝色三维网格；
- `panos panos`：仅配置并成功导入合规全景时出现。

顶部/工具区应有 `select`。右侧 Options 会随当前选中的 layer 或生成器变化；刚启动时没有选中对象，Options 为空不一定是错误。

验收：

- GIS 与 MiniMesh 两项都存在；
- `select` 可选；
- 日志中没有 `tweed.xml` 反序列化、`index.xml`、`model.obj` 或插件加载异常。

如果 Tools 整栏为空，先关闭 GUI，检查：

```powershell
.\scripts\build_chordatlas.ps1
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json validate
.\scripts\launch_gui.ps1
```

仍为空时查看 `chordatlas-gui.log`，确认启动的是：

```text
E:\UCL\Project\myProject\target\chordatlas-0.0.1.jar
```

## 2. 加载 MiniMesh

1. 在左侧点击 `minimesh`。
2. 右侧 Options 中点击 `load all`。
3. 等待所有分块加载；大网格会有明显延迟。

为什么需要这一步：MiniGen 启动时只持有 `minimesh/index.xml` 的分块索引；`load all` 才把全部 bounds 加入加载范围并读取每个 `<tile>\model.obj`。

验收：

- 视图中出现蓝色/材质化三维场景；
- 日志出现 `loading mesh <id> from <workspace>\minimesh`；
- 选择 `wireframe` 时能看到三角网格；
- GIS 轮廓与网格在 X/Z 平面基本重合，不应相隔数百米、镜像或旋转 90°。

如果网格不可见：

- 确认点中的是 `minimesh`，不是 GIS；
- 再点一次 `load all` 并等待；
- 查看 `validation_report.json` 的 minimesh bounds；
- 若 GIS 与 mesh 错位，停止后续步骤，核对 `manifest.json` 中的 `origin_lat/origin_lon`、mesh bounds 和 vertical offset。

## 3. Select 生成 Block

1. 选择工具 `select`。
2. 确保橙色 GIS 和已加载的 MiniMesh 都可见。
3. 在三维视图中**右击**一个与 mesh 重叠的橙色 footprint 面（左击/勾选图层不会创建 Block）。

当前实现会：

1. 从所点 GIS 连通 block 得到 footprint loops；
2. 按 `blockMeshPadding` 计算凸包；
3. 从 MiniMesh 裁出该范围；
4. 在 workspace 下创建本次 block 的中间文件；
5. 新增并自动选中名为 `block` 的 generator。

中间文件位于：

```text
<workspace>\scratch\meshes\<n>\
├── cropped.obj
├── gis.obj
└── gis_footprints.obj
```

验收标准：

- 左侧新增 `block`；
- 右侧 Options 自动切换到 Block UI，能看到 `find profiles`、`render panoramas`、`find image features` 等按钮；
- `cropped.obj` 存在且含 `v` 与 `f`；
- `gis.obj`/`gis_footprints.obj` 存在；
- 画面中的 block 只覆盖选中的街区附近，不是全部 mesh，也不是空模型。

如果右击没有生成 Block：

- 工具必须是精确的 `select`，不是 Facade、Align 等工具；
- 点击 GIS 面，不是只点击蓝色 mesh；
- MiniMesh 必须先 `load all`；
- 该 footprint 必须与 mesh 覆盖区相交；
- 查看日志是否出现 `Failed to find mesh from minimesh or gml layers`、裁剪异常或空 OBJ。

## 4. find profiles

1. 如果当前选中项不是 `block`，先在左侧点击该项。
2. 在右侧 Options 点击 `find profiles`。
3. 等待后台线程完成；不要连续重复点击。

Profile 不是下载的数据，也不是 BigSUR/FrankenGAN 输出。当前 `ProfileGen` 使用：

- Block 的 `cropped.obj` 三维网格；
- 所选 GIS footprint loops；
- 从网格计算的 extent、水平切片与 façade/profile 线。

计算结束后它会新增名为 `profiles` 的 generator 并自动选中。

验收标准：

- 左侧新增 `profiles`，而不是只有原来的 `block`；
- 当前选中项切换到 `profiles`；
- 视图出现 profile/水平线等派生几何；
- GUI 保持响应，日志没有线程异常、空 extent 或 OBJ 读取错误。

如果你看到“model profile”，它就是从当前 Block 裁剪网格中算法提取的剖面模型；来源链是 `MiniMesh -> cropped.obj + GIS loops -> ProfileGen`。

## 5. 全景门窗约束与实景材质参考

这部分是可选的实景增强链，不是 OSM、MiniMesh、Block 或 Profile 的前置条件。两个影像入口用途不同：

```text
当前 Block 的 OSM footprint -> get Street View panoramas -> 样本预览/批准批量
                                                      -> 2:1 panorama -> render panoramas
                                                                       -> find image features -> 门/窗/商铺几何约束

正视、裁好的真实立面图 -> Facade texture 的 8 维风格向量 -> FrankenGAN 生成立面材质
所选建筑的卫星屋顶裁图 -> Roof texture 的 8 维风格向量   -> FrankenGAN 生成屋顶材质
```

全景与 `facade_pytorch` 负责从真实立面观测中提取门窗等位置约束；Joint 编辑器中的立面/屋顶参考图负责颜色与材质风格。参考图不会被直接贴成 UV 纹理，也不会代替 `find image features` 决定门窗位置。

### 5.1 从当前 Block 直接获得全景（推荐）

这个入口复现原版 ChordAtlas 的 Block 后续全景流程，但不依赖 `data_builder` 以前保存的 panorama、label 或 `todo.list`：

1. 用 `select` 右击 GIS footprint，等待目标 `block` 出现并选中它。
2. 在 Block Options 点击 `get Street View panoramas`。程序只把这个 Block 当前的 OSM footprint（本地米制坐标）交给 bridge；bridge 根据 workspace `manifest.json` 的地理原点规划临街候选点，查询 metadata，并按 Google pano id 去重后生成当前区域的 `panos\todo.list`。
3. 程序先下载 **一张** 2:1 全景并弹出预览。只有确认画面与街区正确后，在 `Approve selected-block Street View batch` 对话框点击 `Yes`，才下载其余去重后的候选全景；点击 `No` 会保留样本，但停止批量。
4. 样本发布和批量完成时，GUI 会自动创建指向当前 workspace `panos` 目录的 PanoGen 图层，或刷新已经存在的同目录图层，不需要手工执行 `Layers '+' -> panos (jpg)`。
5. 回到 Block Options，依次点击 `render panoramas -> find image features`；成功后再执行 `find profiles -> optimize`。

启动 GUI 前必须在同一进程环境中设置 `GOOGLE_MAPS_API_KEY`；密钥不会写入项目文件或命令行。每个新 panorama 会请求 1 次 metadata，并下载 6 个 640×640 方向图再合成为 2560×1280 JPEG；批量会产生 Google API 请求和可能的费用，应先检查样本、候选数量及 Google Cloud 配额/账单设置再点击 `Yes`。已有 JPEG 会按缓存规则复用。

### 5.2 使用已有 `todo.list` 的 CLI 入口（保留）

`todo.list` 的每个非空行沿用 ChordAtlas 七字段格式：

```text
latitude_longitude_altitude_heading_tilt_roll_panoId
```

前六项必须是有限数值，前两项是 WGS84 纬度/经度，最后一项是安全的源 panorama 标识。可以使用当前区域已有的 ChordAtlas/panoscraper 清单，也可以从当前 AOI 道路附近的候选拍摄点整理同格式清单；导入器会按每行经纬度调用 metadata，刷新成当前 Google pano id。输入姿态会写入报告供追溯，发布文件的方向由 bridge 统一规范化。

空间匹配是硬要求：清单坐标必须覆盖当前 workspace，最好覆盖准备选择的建筑及其可见街道。`datasets\regent_osm\panos\todo.list` 只适用于 Regent 数据，不能复制给 `data_builder_london_on_demand` 或其他区域；坐标不匹配时即使下载成功，`render panoramas` 也无法为所选立面提供有效观测。

先离线计划，再只下载一张样本。下面的 `$todo` 必须替换为当前区域的清单：

```powershell
Set-Location E:\UCL\Project\myProject\bridge
$todo = 'E:\path\to\current_workspace_todo.list'

.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json `
  import-streetview-panos --todo $todo --dry-run --limit 1

$env:GOOGLE_MAPS_API_KEY = '<restricted-key>'
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json `
  import-streetview-panos --todo $todo --limit 1
```

API key 只从当前 PowerShell 进程的 `GOOGLE_MAPS_API_KEY` 读取，不接受命令行 key，也不会写入报告、文件名或请求日志。检查 `<workspace>\panos\streetview_sample_report.json` 和生成的 2560×1280 JPEG，确认坐标、方向与画面后再执行批量：

```powershell
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json `
  import-streetview-panos --todo $todo --all --sample-approved
```

批量详情写入 `streetview_batch_report.json`。发布新图时已有 `panos.xml` 会先改名为带时间戳的备份，避免继续读取旧扫描缓存。MyProject 本地坐标是 X=东、Y=上、Z=南，因此默认 `myproject-local` 输出文件使用 heading 180；只有导入原版 geographic workspace 时才传 `--coordinate-mode original-geographic`。

若全景图来自自有或另行许可的数据而不是上述下载器，仍可继续使用 `prepare-panos` 导入严格 2:1 JPEG；不要混用两个区域，也不要把普通透视照片伪装成 equirectangular panorama。

### 5.3 CLI 导入后的 PanoGen 图层

这里仅说明上面保留的 CLI `import-streetview-panos`：它只发布文件，**不会修改 `tweed.xml`，也不会自动创建 PanoGen 图层**。CLI 首次导入后必须：

1. 在 GUI 选择 `Layers '+' -> panos (jpg)`。
2. 指向当前 workspace 自己的 `<workspace>\panos`，不要指向其他项目的目录。
3. 选中新增的 panorama 图层，在 Options 确认显示 `coordinates: myProject local (X east / Z south)`。如果显示 `original geographic`，先检查当前 workspace 的 `manifest.json` 中 `frame.origin_lat`、`frame.origin_lon` 与 axes，不能继续做门窗投影。
4. 后续增加或替换 JPEG 时，选中已有 panorama 图层并点击 `refresh panoramas`；这个按钮只重扫已有图层，不能代替第一次创建图层。

Block 的 `get Street View panoramas` 不受这个限制，它会自动创建或刷新正确的 PanoGen 图层。

### 5.4 从全景提取门窗约束

对当前目标执行以下顺序：

1. 用 `select` 右击 GIS footprint，等待目标建筑的 Block 成功加载。
2. 选中这个 `block`，点击 `render panoramas`，让 ChordAtlas 从空间匹配的全景生成各立面的 `rendered.png`。
3. 确认有有效立面渲染后点击 `find image features`。GUI 会按需运行现有环境中的 PyTorch 模块：

   ```text
   conda run --no-capture-output -n sat3dgen python -B -m facade_pytorch ...
   ```

4. 分割成功后 Java 会立即刷新 `FeatureCache`；已经存在完整输出时也会刷新，不需要重启 GUI。
5. 再执行 `find profiles -> profiles 中 optimize`。后续规则化与 FrankenGAN 阶段可以使用提取出的 window、door、shop 等矩形约束。

`facade_pytorch` 的工作目录是 `E:\UCL\Project\facade-segmentation`，包目录是 `facade_pytorch`。它是点击时启动的子进程，不是常驻服务，不需要预先单独启动，也不会修改 `sat3dgen` 环境。某个 façade 的详情在对应 feature 目录的 `facade-pytorch.log`；没有覆盖当前建筑的全景、立面被遮挡或渲染为空时，结果可能缺失，此时保留程序原有的规则/生成式门窗逻辑。

### 5.5 卫星屋顶参考的来源与状态

按需 mesh 选择成功并拆分建筑时，bridge 会复用该选择已经验证过的卫星瓦片，为每个 building 生成北向上的屋顶裁图；仅为参考图不会再次联网。路径是：

```text
<workspace>\generated_blocks\<selection-id>\buildings\<building-id>\references\roof\
├── satellite_north_up.png
├── source_valid_mask.png
├── footprint_mask.png
├── roof_style_mask.png
├── roof_reference.png
├── roof_reference_rgba.png
└── reference.json
```

`reference.json` 是发布完成标志。只有 `status: READY` 且当前选中 Block 正是该 building 时，Joint 编辑器才会启用 `Use satellite roof reference`。覆盖不足、输入缺失或裁图失败会发布 `status: UNAVAILABLE`；这是软失败，不会把已通过检查的 mesh、MiniMesh 或 Block 判失败，Roof texture 会继续使用原有随机/手动参考回退。

卫星图能提供屋顶的主色和视觉风格，但阴影、树木、遮挡、低分辨率和拍摄时间都会影响编码；它不是逐像素屋顶贴图，也不保证还原真实瓦片排布。

### 5.5 启动 FrankenGAN 兼容 watcher

只有生成网络材质或把参考图编码成风格向量时才需要 watcher：

```powershell
Set-Location E:\UCL\Project\myProject\bridge
.\scripts\start_frankengan.ps1
.\scripts\start_frankengan.ps1 -Execute
```

第一条只显示命令，第二条才启动。使用 MyProject 兼容入口，不要同时启动另一个直接写同一 FrankenGAN `input/output` 的 watcher。Joint 编辑器应显示 `FrankenGAN encoder ready`；如果显示 `Start FrankenGAN watcher before loading`，先恢复 watcher，不能把残留目录当作可用编码器。

### 5.6 在 Joint 中载入真实立面与屋顶风格

完成 `optimize` 并进入建筑 appearance/FrankenGAN 编辑后：

1. 选择 `Joint` style source，打开 joint distribution editor。
2. 选择 `Facade texture` 图标/页签。在 `Load/drop facade reference` 区域单击选择文件，或把图片拖入该区域。推荐输入单栋、单立面、尽量正视且已经裁好的真实 façade；先去掉大面积天空、道路、相邻建筑和强透视畸变。
3. 等待 `Encoding reference...` 消失。成功时参考图被编码为 8 维 latent 并更新当前 Joint mode；它不是把原图直接贴到模型上。
4. 选择 `Roof texture` 图标/页签。优先点击 `Use satellite roof reference` 使用当前 building 的 READY 裁图；也可以在 `Load/drop satellite roof reference` 区域载入另一张合法屋顶参考图。
5. 点击 `ok` 返回，然后点击 `redraw distribution` 重新生成 façade、roof、window/door 等网络材质。参考图只固定当前 mode 的外观中心，Joint 的多 mode、概率、sigma 与其他网络设置仍然保留。

任一页的 `Clear / use random` 会清除参考预览并恢复载入前的随机 Gaussian mean；不是把向量强制设成一种新的固定风格。读取或编码失败时旧 latent、旧预览和已有 mode 保持不变，拖到 `+` 的失败导入也不会留下空 mode。

参考图必须是 Java ImageIO 可读图片，文件不超过 32 MB、解码尺寸不超过 16 megapixels，且任一边不超过 8192 px；编码网络必须正好返回 8 个有限数值。当前自动实景入口只覆盖 `Facade texture` 与 `Roof texture`；窗、门等独立材质仍沿用 Joint 的随机/手动分布，但其几何位置可由 5.3 的真实全景特征约束。BigSUR/FrankenGAN checkpoint 不生成 footprint、MiniMesh、Block 或 Profile。

## 6. 一页验收表

| 阶段 | 必须看到 | 失败时先看 |
|---|---|---|
| Workspace | `tweed.xml`、`manifest.json`、`footprints.obj`、`minimesh/index.xml` | `workspace-descriptor.log`、`minimesh-conversion.log` |
| GUI 启动 | `gis(o) footprints.obj`、`minimesh`、`select` | `chordatlas-gui.log`、是否启动当前 JAR |
| MiniMesh | 点击 `load all` 后出现 3D 网格 | `validation_report.json` bounds、`model.obj` |
| Select | 左侧新增 `block`，scratch 下有三个 OBJ | GIS/mesh 是否重合、工具是否为 `select` |
| Profiles | 左侧新增并选中 `profiles` | `cropped.obj` 是否非空、GUI 日志线程异常 |
| Panoramas | 当前区域的 2560×1280 JPEG；图层显示 `myProject local` | `streetview_sample_report.json`、坐标是否属于当前 workspace |
| façade features | 每个目标目录生成 `parameters.yml`，完成后 cache 已刷新 | `rendered.png`、`facade-pytorch.log`；这不是主路径前置条件 |
| roof reference | 当前 building 的 `reference.json` 为 `READY` | 同目录 masks；`UNAVAILABLE` 只回退材质，不使模型失败 |
| 网络纹理 | Joint 显示 encoder ready；参考编码后可 redraw | watcher 日志；`Clear / use random` 可恢复旧随机逻辑 |

## 7. 日志和可复现信息

请在报告问题时一并保留：

- 所用 config JSON；
- `<workspace>\manifest.json`；
- `<workspace>\validation_report.json`；
- `<workspace>\panos\streetview_sample_report.json` 或 `streetview_batch_report.json`（涉及全景时）；
- generate 模式下 `_mesh_job\top_level_pipeline_manifest.json`；
- `<workspace>\logs\chordatlas-gui.log`；
- 对应 `scratch\meshes\<n>\cropped.obj` 的路径和大小；
- 对应 façade feature 目录的 `facade-pytorch.log`；
- 对应 building 的 `references\roof\reference.json`（涉及卫星屋顶参考时）。

不要只报告“没有模型”。上述文件能区分 bbox/tile 选择、origin、MiniMesh 加载、Block 裁剪和 Profile 计算分别在哪一步失败。
