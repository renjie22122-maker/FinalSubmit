# Sat3DGen 原方法与本项目活动管线对比（审计备忘录，2026-08-13）

[English version](SAT3DGEN_METHOD_COMPARISON_2026_EN.md)

## 0. 结论先行

1. **Sat3DGen 的原创贡献位于学习式生成模型与训练约束层。** 原论文采用冻结的 DINOv3 编码器、三平面 NeRF 表示、空间 token、重力方向密度变化损失、卫星视角相对深度先验和全景到透视视角训练，并通过 Marching Cubes 导出网格。
2. **本项目没有训练新的 Sat3DGen，也没有改变其损失函数或已发布权重。** 本地上游克隆中已跟踪的模型代码改动主要把硬编码 `.cuda()` 改为随张量/模型设备分配；这些是运行兼容性修补，不是网络或训练方法创新。
3. **本项目相对原方法的实质新增主要位于网格生成之后。** 活动根包增加了地理文件名解析、局部到共享场景坐标的映射、逐 tile 裁剪、OSM 跨 tile 屋顶预对齐、底面删除、网格域 KD-tree 缝合、外部 DSM/OSM 语义高度修正、建筑提取与独立导出，以及后续 ChordAtlas/MiniMesh 交付。
4. **原 Sat3DGen 的大图方法与本项目不是同一种融合策略。** 论文在重叠滑窗的密度体上进行融合，再统一运行 Marching Cubes；活动管线对每张 tile 独立得到的 OBJ 做变换、裁剪与网格域拼接。后者应称为 *mesh-domain downstream integration strategy*，不能称为新的 Sat3DGen 模型。
5. **当前 `test_mesh_pipeline_merge.py` 只验证了缓存 OBJ 的后处理。** 它递归读取 15 个现存 OBJ；卫星下载、全景图下载、神经推理、训练、视频渲染和模型原论文评测均未在该实验中执行。
6. **不能把 Sat3DGen 论文指标归给本项目，也不能声称本项目优于 Sat3DGen。** 原论文的 FID、KID、DINO、DSM MAE/RMSE 来自 VIGOR-OOD 协议；本项目是 London 数据、不同输入构造和后处理目标，没有复现实验协议，也没有独立的同任务基线。
7. 最强且可辩护的论文定位是：**对已发布 Sat3DGen 资产的工程集成、地理坐标显式化、确定性后处理、工作流编排和证据驱动的可靠性审计**。现有证据不支持“new generative model”“improved reconstruction accuracy”或“outperforms Sat3DGen”。

---

## 1. 审计范围与证据边界

### 1.1 本地证据

- 原论文 PDF：`E:\UCL\Project\Sat3DGen\2605.14984v1.pdf`
  - 标题：*Sat3DGen: Comprehensive Street-Level 3D Scene Generation from Single Satellite Image*
  - PDF SHA-256：`8A060AF1A18AFC59810D9718E34FD3F116A993D5AF0E2ADF12137EEAFC12577F`
- 官方仓库本地克隆：`E:\UCL\Project\Sat3DGen\Sat3DGen`
  - remote：`https://github.com/qianmingduowan/Sat3DGen.git`
  - HEAD：`882cc66c363aa16b82fb2e494be7600003076890`
  - commit date：2026-05-18
  - 克隆为 dirty working tree；具体改动见 §1.3。
- 本项目活动入口：`E:\UCL\Project\Sat3DGen\test_mesh_pipeline_merge.py`
- 本项目活动实现：`E:\UCL\Project\Sat3DGen\mesh_pipeline\*.py`
- **明确排除**：`E:\UCL\Project\Sat3DGen\mesh_pipeline\mesh_generate_merge_pipeline`
  - 此子树不得用于描述本论文所分析的活动实现，即使其中存在更强的验证或几何机制。

### 1.2 活动代码审计哈希

| 文件 | SHA-256 |
|---|---|
| `test_mesh_pipeline_merge.py` | `DA3B13E5B2CC66544AF8CDBBE6FBDC4584014DCD3665C77704DA11D6786565F6` |
| `mesh_pipeline/pipeline.py` | `3D7F8869CBEA592B14CC71A0F5A85E9C3EF5CEF148A1AD2CD1B721115D56A50D` |
| `mesh_pipeline/mesh_merging.py` | `C398FCB2A1345E32016B029E3709C0A00C7F359A331AE3D1149C2F12B8F8EEDE` |
| `mesh_pipeline/height_correction.py` | `2E565C4C4CFAC40849B76D4F758F61AF45058C0EAA167B3EBE63D0E8A22D5920` |

### 1.3 官方克隆与本地改动的边界

固定上游 HEAD 已包含训练、单图推理、大图切片推理、DSM 导出/评估和 Gradio demo。本地 tracked diff 为 27 additions / 11 deletions：

- `source/generator.py`：以 `_current_device` 和 `.to(device)` 取代若干硬编码 CUDA 分配；
- `source/rendering/point_representer.py`：把查询张量移动到特征设备；
- `source/rendering/sat2density_transform_eg3d.py`：把硬编码 `.cuda()` 改为当前设备；
- `inference/demo_inference.py`：修复 docstring 后缺少换行导致的语法/格式问题；
- `requirements.txt`：增加一项依赖。

这些改动没有引入新的编码器、三平面结构、渲染器、损失函数、训练数据或权重，故应分类为 **implementation compatibility fixes**，而不是模型创新。

以下文件是本地未跟踪实验文件，且不是 `test_mesh_pipeline_merge.py` 的活动依赖：

- `inference/london_batch_mesh_pipeline.py`
- `inference/global_batch_fusion_pipeline.py`
- `inference/global_full_pipeline_fusion.py`
- `inference/local_block_fusion_from_scratch.py`
- `inference/stitch_london_satellite_mosaic.py`
- `docs/london_reconstruction.md`
- `run_overlap10*.bat`

除非另行建立调用关系和实验记录，不应把这些文件中存在的 RANSAC、ICP 或其它机制描述为本论文活动方法。

### 1.4 上游元数据与文档中的不一致

- 本地官方 README 的 News 段把 arXiv 发布和代码发布写成 2025 年 5 月/4 月，但论文编号 `2605.14984`、OpenReview 的 ICLR 2026 记录和本地上游 commit 日期都指向 2026。论文中应引用 OpenReview/arXiv/commit 的可核查日期，不应把 README News 年份当作发布史证据。
- 原论文和模型配置的神经输入是 256×256。官方 Hugging Face Space 的说明建议上传 zoom-20、512×512 图像，而本地 Gradio `SAT_TRANSFORM` 实际把任意上传尺寸 resize 到 `PATCH_SIZE*16`，当前配置为 256×256。本项目的 640×640 缓存 tile 因而是 acquisition artefact，不代表模型以 640×640 直接推理。
- 大图步长在论文 prose 中为 128，而固定官方 release script 默认值为 64。论文若讨论“原方法”，应明确标注 paper setting 与 released-code default，避免把二者合并为一个参数事实。

---

## 2. Sat3DGen 原方法：应如何准确描述

### 2.1 研究任务与表示

Sat3DGen 的任务是从单张卫星图像生成可渲染的街道级三维场景。原论文把卫星图编码为 token 网格，解码为三平面特征，并用轻量 MLP 预测体密度与颜色；同一表示可以渲染卫星视图、透视街景和全景街景，也可以在密度网格上运行 Marching Cubes 导出显式网格。

论文明确说明它不是从零发明一个全新的 feed-forward image-to-3D 主干，而是在三平面 NeRF 类基线之上，用针对卫星—地面极端视角差和稀疏监督的几何约束改善结果。因此，相关工作应把它放在 **proxy-based satellite-to-3D / neural rendering** 路线中，而不是传统 GIS 建模或纯程序化建模路线中。

### 2.2 原论文的模型与训练贡献

原论文可归纳为以下几项：

1. 冻结 DINOv3 ViT 将 256×256 卫星图映射为 16×16×1024 token 网格；
2. 每侧增加两个 spatial tokens，使 token 网格扩展为 20×20；
3. 解码为 320×320×32×3 的三平面特征；
4. gravity-based density variation loss 约束较高位置的密度不应无条件大于较低位置的密度；
5. 使用 Depth Anything V2 产生的相对深度伪标签和尺度/平移不变损失约束屋顶顺序；
6. 将全景图投影为随机透视视图，增加训练视角覆盖；
7. 延续 Sat2Density++ 的照明条件与天空分支，支持可控街景渲染。

这些均属于 Sat3DGen 作者的上游贡献。本项目只是消费发布模型/网格，不拥有这些贡献。

### 2.3 原论文的训练与评测协议

- 训练城市：Chicago、New York、San Francisco；
- OOD 测试城市：Seattle；
- 数据：VIGOR GPS 匹配卫星—全景图对；
- 训练对：78,188；定量评测对：11,875；
- 输入：zoom 20、256×256；
- 训练：8× NVIDIA H20、batch size 32、600,000 iterations。

原论文报告的代表性结果如下。它们只可写为 **Qian et al. reported**，不得改写为本项目结果。

| 指标（VIGOR-OOD） | Sat2Density++ | Sat3DGen full model |
|---|---:|---:|
| FID ↓ | 40.8 | 19.2 |
| KID ↓ | 0.035 | 0.014 |
| DINO similarity ↑ | 0.465 | 0.525 |
| SSIM ↑ | 0.34 | 0.37 |
| PSNR ↑ | 12.51 | 12.83 |
| LPIPS-Alex ↓ | 0.44 | 0.38 |
| LPIPS-Squeeze ↓ | 0.34 | 0.30 |
| DSM MAE (m) ↓ | 4.72 | 3.47 |
| DSM RMSE (m) ↓ | 6.76 | 5.20 |
| DSM error < 2.5 m ↑ | 49.69% | 62.69% |
| DSM error < 7.5 m ↑ | 83.65% | 88.68% |

### 2.4 原方法的大范围网格策略

论文 Appendix B.3 描述：

- 将一张较大的卫星图调整到训练数据的像素分辨率；
- 采用 256×256 滑窗，paper 中 step=128；
- 在重叠区平均密度；
- 在融合后的整体密度体上统一运行 Marching Cubes；
- 示例是 zoom 19、约 150 m × 150 m。

固定官方代码 `inference/big_image_slice_inference.py` 与论文文字略有实现差异：默认 `step_size=64`，内部 `crop_edge=32`，先把各窗口密度累积到同一个 `output_volume` 并除以计数，再运行一次 Marching Cubes。论文与发布代码共同的关键点是 **density-domain fusion before global isosurface extraction**。

### 2.5 原论文自行声明的局限

- 卫星图被视为理想正射投影，但现实并非总是如此；
- 全景图只有 GPS，缺少可靠内外参，并假定相机垂直于地面；
- 生成模型对训练数据中罕见的异常建筑泛化有限；
- 假设局部地面近似平坦，未建模显著丘陵地形；
- VIGOR 是稀疏、非连续静态图像集合，无法直接评估时间稳定性或密集多视角一致性；
- OOD 评估仍是同一数据源的 unseen-city 测试，并不等价于跨图像供应商、跨 zoom 或跨 GSD 泛化。

---

## 3. 本项目活动网格管线：实际做了什么

### 3.1 `test_mesh_pipeline_merge.py` 的实际执行路径

活动实验按以下顺序运行：

1. `sorted(config.mesh_dir.rglob("*.obj"))` 递归选择全部缓存 OBJ；
2. 按文件名解析 tile 纬经度；
3. 在各 OBJ 的归一化 X/Z 范围中裁剪边缘；
4. 用近似米/经纬度公式和硬编码 tile span 把各局部网格放入共享 X-east / Y-up / Z-south 场景；
5. 对上表面顶点进行 OSM building ID 分类，并把跨 tile 同 building 的较低屋顶中位数向最高 tile 抬升；
6. 按 tile 删除最低层附近的面；
7. 依据平均顶点法线分为 upper/lower/side，在每组的 X/Z 平面中用 KD-tree 缝合；
8. 导出未 DSM 修正场景；
9. 使用 WGS84→EPSG:27700 和外部 DSM、OSM 分类进行语义高度平移与边界平滑；
10. 导出 DSM 修正场景；
11. 对 DSM 与 no-DSM 两个分支分别做建筑提取、连通分量分离、地面裁切、尝试补洞、内部面删除和独立 OBJ/PLY 导出。

### 3.2 实现存在但当前合并实验没有执行的能力

活动根包还包含：

- OSM/Overpass 建筑查询；
- Google Static Maps 卫星 tile 下载；
- Street View 多 heading 下载与拼接；
- 通过 Gradio API 调用 Sat3DGen；
- `Pipeline.run` 编排；
- FrankenGAN 立面增强。

这些能力不能仅因为源码存在就写为已验证。特别是当前 Gradio 封装需要单独验证接口契约：`app.py::generate_mesh` 返回三个输出（GLB 路径、OBJ 路径、state），而 `mesh_pipeline/inference.py` 把 `/generate_mesh` 的结果直接传给 `Path(...)`，随后又无参数调用需要 `mesh_path` 输入的 `/download_mesh`。Gradio 是否隐藏 state 或如何序列化多输出需要运行时确认；静态检查至少说明此调用契约**未被当前缓存合并实验验证，并存在不匹配风险**。

### 3.3 已审计实验事实

- 读取 15 个缓存 OBJ；
- 它们由两个不相连的集群组成，中心相距约 666.438 m；
- 输出目录没有 manifest、模型哈希、配置 sidecar 或完整 run log；
- 因而 `final_v36` 不应被描述为一个连续 London 场景，也不能证明一次完整新推理。

| 阶段 | 顶点 | 面 |
|---|---:|---:|
| 原始 15 OBJ | 3,640,214 | 7,281,608 |
| 裁剪后 | 2,828,613 | 5,615,039 |
| 去底面、缝合前 | 2,252,373 | 4,474,289 |
| 缝合场景 | 1,915,455 | 4,097,374 |
| DSM 修正场景 | 1,915,455 | 4,097,374 |
| DSM 建筑提取 | 1,001,507 | 2,116,136 |
| no-DSM 建筑提取 | 907,370 | 1,911,272 |

DSM 修正改变了场景中全部 1,915,455 个顶点的 Y 值，且全部向上：中位数 +36.081838 m，均值 +42.236210 m，范围 +15.811809 至 +62.634977 m。该行为与 README/模块 docstring 中“只修正上表面”相矛盾；当前实现实际上先按语义修改所有有条件的顶点，仅在后续边界平滑阶段排除 lower surface。

一个成对的代表性建筑（DSM/no-DSM 均为 2,804 vertices）在边界边计数检查下都不是 watertight。它只说明后处理结果没有保留/建立水密性；不能反向推断原 Sat3DGen 单 tile 原始 Marching Cubes 输出必然不水密。

### 3.4 已确认的活动代码限制

- bottom removal 使用 face vertex minimum：只要一个顶点接近全局最低 Y，三角形即可被删除；
- stitch 只查询 X/Z，`max_y_diff_for_merge` 未使用；合成回归证明 Y 相差 10 m、X/Z 重合的顶点仍会合并；
- stitch 搜索所有其它 tile，而非仅地理相邻 tile；
- 被合并顶点继承更早顶点的位置和颜色，不做位置/颜色平均；
- OBJ parser 对负索引和 n-gon 的支持不完整，并缺乏 finite/index validation；
- 输出写入不是原子操作，历史上存在不完整版本目录；
- `Pipeline.run` 当前把返回四项的 `extract_building_mesh` 解包为两项，完整 orchestrator 路径存在接口错误；
- 活动 `mesh_pipeline` 本身不是可追溯的 Git 仓库，需通过文件哈希固定分析对象。

---

## 4. 原方法与活动管线的逐项对照

| 维度 | Sat3DGen 原论文/官方发布 | 本项目活动路径 | 关系与论文分类 |
|---|---|---|---|
| 研究目标 | 从单张卫星图学习可渲染三维场景 | 将生成网格放入地理场景并交付下游城市建模工具 | 目标不同；下游工程集成 |
| 学习模型 | DINOv3 + decoder + tri-plane NeRF + MLP | 直接消费发布模型/缓存 OBJ | **复用**；无新模型 |
| 训练 | VIGOR 三城训练、Seattle OOD、600k iterations | 未训练、未微调、未消融 | **缺失/不适用** |
| 几何先验 | spatial tokens、gravity loss、relative-depth prior | 没有改变这些模块 | **复用上游贡献** |
| 输入 | zoom-20 256×256 训练输入 | 缓存卫星图为 640×640，Gradio 端点缩放至 256×256 | 输入适配；活动合并实验未执行缩放/推理 |
| 单 tile 输出 | 密度/颜色场，Marching Cubes 网格 | 缓存 OBJ 作为管线输入 | 上游输出接口 |
| 大范围处理 | 重叠滑窗密度融合后统一 Marching Cubes | 独立 OBJ 的坐标变换、裁剪、预对齐和网格域缝合 | **修改/新增的工程策略**，非模型创新 |
| paper/release 大图细节 | paper step=128；release 默认 step=64、crop edge 32 | hard-coded lat/lon steps、10% overlap、5% mesh crop | 参数与域不同，不是同协议复现 |
| 坐标参考 | 模型归一化/局部场景坐标 | 文件名 WGS84 → 近似局部米制 X/Y/Z；DSM 使用 EPSG:27700 | **新增地理集成层** |
| 语义信息 | 学习的外观/结构；semantic-map app 另用 ControlNet+SDXL | OSM footprint/road/water/green 分类直接约束后处理 | **新增确定性语义后处理**，与原 app 不同 |
| 高程 | Depth Anything V2 相对深度用于训练；可从模型渲染 DSM | 外部 1 m DSM 在推理后移动网格顶点 | **新增外部监督后处理**；不是原论文“无 metric-depth training”的 DSM 预测 |
| tile 接缝 | 密度域融合 | normal-group X/Z KD-tree 顶点折叠 | **替代性实现适配**；当前非 height-aware |
| 地形 | 原论文承认局部平地假设 | 外部 DSM 试图锚定绝对/相对高程 | 设计目标合理，但当前数值与语义验证不足 |
| 建筑单体 | 生成完整多语义场景 | OSM-guided building extraction、components、clip/close/export | **新增资产提取层** |
| 街景/视频 | 支持 panorama/perspective/illumination control | 当前活动合并入口不调用 | **原能力未验证/未使用** |
| ChordAtlas/MiniMesh | 无 | manifest、MiniMesh、workspace、on-demand publication | **新增系统集成层** |
| 原论文定量指标 | FID/KID/DINO/SSIM/PSNR/LPIPS 与 DSM MAE/RMSE | 未按 VIGOR-OOD 复现 | **不可直接比较** |
| 本项目证据 | 不适用 | 15 cached OBJ 后处理、stage counts、failure cases、tests | 支持可靠性审计，不支持模型优越性 |
| 水密性 | 论文把固定 isovalue MC 网格描述为 watertight | 去底面、缝合、提取后代表建筑不是 watertight | 下游处理改变拓扑；不能归咎上游 |
| 可追溯性 | 官方 Git commit、HF model/data cards | `final_v36` 缺模型/配置/run manifest；活动包以哈希冻结 | 项目发现的可靠性缺口 |

### 4.1 “相同、修改、新增、缺失、未验证”汇总

**相同/复用**

- 发布的 Sat3DGen 模型和权重；
- 256×256 神经输入（由 API resize 实现）；
- 三平面/密度场和 Marching Cubes 的单 tile 生成逻辑；
- Sat3DGen 的模型级几何先验。

**修改/适配**

- 设备分配兼容性修补；
- 由 density-domain global fusion 改为 mesh-domain tile integration；
- 从归一化模型空间映射到共享近似米制场景；
- 独立网格的边缘裁剪和分组缝合。

**新增**

- OSM 语义/建筑 ID 驱动的预对齐和提取；
- 外部 DSM 驱动的后验高度平移；
- bottom removal、DSM/no-DSM 双分支、独立建筑导出；
- ChordAtlas/MiniMesh、manifest、on-demand 和发布契约（属于更广项目链）。

**原能力缺失或未进入活动入口**

- 训练与微调；
- 原论文的 panorama/perspective/sky/illumination 输出；
- 原论文 density-volume overlap averaging；
- VIGOR-OOD 指标复现；
- semantic-map→satellite→3D 应用；
- 模型自身的 satellite-to-DSM 评测。

**未验证/不确定**

- 新鲜下载→Gradio inference→OBJ→merge 的完整活动根包运行；
- Gradio API 多输出契约；
- 15 个缓存 OBJ 的 checkpoint、参数和生成命令；
- 外部 DSM 修正的 metric correctness 与 OSM 分类准确率；
- tile seam 的 height-aware 正确性；
- 普遍 watertight/topological validity；
- 相对原论文大图策略的精度、视觉质量、速度、内存或规模优势。

---

## 5. 可直接写入论文的英文文献综述

### 5.1 Satellite-to-street and satellite-to-3D lineage

> Satellite-conditioned street-level generation is severely under-constrained because the overhead observation exposes roofs and planimetric context but not façades, street-level occlusions, or view-specific illumination. VIGOR formalised a non-centred cross-view setting in which a ground query may be covered by several aerial references rather than by one perfectly aligned image \cite{zhu2021vigor}. Sat2Density subsequently represented a satellite-conditioned scene as a neural density field learned from satellite--ground image pairs without metric depth supervision \cite{qian2023sat2density}. Sat2Density++ extended this proxy-based lineage with a tri-plane representation and explicit treatment of street-view illumination and sky, targeting multi-view-consistent panorama and video synthesis \cite{qian2026sat2densitypp}. These methods establish the relevance of learned three-dimensional intermediates, but their outputs remain neural assets whose coordinate frame, provenance and downstream software contract are outside the primary research task.

### 5.2 Geometry-colourisation versus proxy-based generation

> A complementary lineage separates geometry construction from appearance synthesis. Sat2Scene conditions three-dimensional diffusion and neural rendering on an input geometry, while Sat2City employs cascaded latent diffusion over sparse voxel representations and evaluates on a synthetic city dataset \cite{li2024sat2scene,hua2025sat2city}. Such geometry-colourisation approaches can provide clean building-focused structure, whereas proxy-based approaches such as Sat2Density++ preserve a wider range of image semantics but may produce coarse or unstable surfaces. The tasks, supervision and output representations differ, so their published scores do not form a directly transferable benchmark for the London integration evaluated here.

### 5.3 Sat3DGen and its actual novelty

> Sat3DGen belongs to the proxy-based family but introduces geometry-oriented constraints for sparse cross-view supervision \cite{qian2026sat3dgen}. A frozen DINOv3 encoder maps the satellite image to tokens that are decoded into a tri-plane radiance field, following the efficient explicit--implicit representation popularised by EG3D \cite{chan2022eg3d}. Spatial tokens provide capacity beyond the nominal satellite footprint; a gravity-based density-variation loss discourages unsupported floating density; a relative-depth prior derived from Depth Anything V2 constrains roof ordering; and panorama-to-perspective training increases effective view coverage \cite{yang2024depthanythingv2}. The model can render multiple camera types and export an isosurface mesh through Marching Cubes \cite{lorensen1987marching}. These components are contributions of the upstream Sat3DGen work and are not contributions of the present integration.

### 5.4 Gap leading to this project

> The published Sat3DGen method addresses learned scene generation, whereas a geospatial modelling system requires additional contracts: an explicit coordinate frame, deterministic tile selection, traceable input-to-output provenance, overlap handling, semantic association, terrain alignment, topology suitable for asset conversion, and failure-aware publication. Sat3DGen's large-image demonstration fuses overlapping density windows before extracting a global isosurface. The implementation studied in this dissertation instead receives independently generated tile meshes and introduces a downstream mesh-domain path for geographic placement, crop, OSM-guided alignment, stitching, DSM correction, building extraction and ChordAtlas delivery. The research contribution is therefore evaluated as an engineering integration and reliability problem, not as a competing satellite-to-3D model.

### 5.5 Adjacent city generation literature

> Unbounded generative city models address a related but different objective. CityDreamer composes instance-oriented building fields with background neural fields and is designed to synthesise diverse, editable city layouts \cite{xie2024citydreamer}. It is useful for positioning the broader 3D-city generation landscape, but it is not a direct baseline for geospecific reconstruction of a fixed London area. A fair comparison must distinguish scene synthesis from reconstruction, image-space realism from metric geometry, and a learned scene representation from an explicit GIS-linked asset.

---

## 6. 可直接写入论文的方法对比与结果限定措辞

### 6.1 Methodology paragraph

> The upstream Sat3DGen large-image procedure and the analysed integration operate in different domains. Sat3DGen predicts overlapping density volumes from sliding image windows, blends the density values and applies Marching Cubes once to the fused volume. The analysed path begins after per-tile isosurface extraction: it loads independent OBJ files, crops their boundaries, places them in a shared local frame inferred from latitude--longitude filenames, performs OSM-guided roof pre-alignment and grouped vertex stitching, and optionally translates the resulting geometry using an external DSM. This is a downstream mesh-integration adaptation; it neither changes the learned Sat3DGen representation nor retrains the model.

### 6.2 Results paragraph

> The active merge experiment exercised only the post-inference segment. It loaded fifteen cached OBJ files and produced cropped, bottom-removed, stitched and DSM/no-DSM outputs. The selected files form two clusters separated by approximately 666.4 m, and no run manifest binds them to a checkpoint, configuration or generation command. The experiment therefore demonstrates that the post-processing functions accepted and transformed the available artefacts; it does not demonstrate a fresh end-to-end Sat3DGen run, a contiguous reconstruction, or reproduction of the VIGOR-OOD results reported by Qian et al.

### 6.3 Discussion paragraph

> The integration addresses a systems gap that is largely orthogonal to Sat3DGen's model contribution. Its strongest additions are coordinate-explicit asset placement, semantic and elevation-aware post-processing, workflow manifests, and delivery into ChordAtlas. These additions are technically substantive, but the current evaluation does not isolate their effect on reconstruction accuracy or perceptual quality. Accordingly, they are classified as implementation adaptations and engineering extensions rather than as a new generative model or an empirically demonstrated improvement over Sat3DGen.

### 6.4 DSM qualification

> Sat3DGen's satellite-view depth prior and DSM application must not be conflated with the external DSM correction used here. In the upstream method, relative monocular depth is a training regulariser and metric DSM is rendered from the learned representation without metric-depth training. In the analysed integration, an Environment Agency DSM is an additional post-inference input that directly changes mesh elevations. Agreement with that DSM after correction is therefore not an independent reconstruction-accuracy result; it is primarily a constraint-consistency measure unless evaluated against a separate withheld elevation source.

### 6.5 Prohibited/approved phrase pairs

| Avoid | Use instead |
|---|---|
| “We improve Sat3DGen.” | “We extend the downstream handling of Sat3DGen-derived meshes.” |
| “Our Sat3DGen model …” | “The published Sat3DGen model …” |
| “Our novel neural reconstruction method …” | “The proposed coordinate-explicit integration workflow …” |
| “The pipeline generates London end to end.” | “The evaluated merge reused cached tile meshes; acquisition and inference were not exercised in that run.” |
| “DSM improves accuracy.” | “DSM correction changes/anchors elevation; independent accuracy improvement was not measured.” |
| “Stitching is robust/height-aware.” | “The implementation groups vertices by normal class and matches in X/Z; vertical separation is not enforced.” |
| “The output is watertight.” | “The representative extracted output was not watertight under edge-incidence testing.” |
| “The project outperforms the original method.” | “No controlled same-input comparison with the original density-fusion path was performed.” |
| “FID fell from 40.8 to 19.2 in our work.” | “Qian et al. report FID 19.2 for Sat3DGen versus 40.8 for Sat2Density++ on VIGOR-OOD.” |

---

## 7. 建议放入论文的紧凑 LaTeX 对照表

```latex
\begin{table}[tbp]
\centering
\caption{Relationship between the published Sat3DGen method and the
analysed downstream integration. ``Exercised'' refers to the audited cached
mesh experiment, not to source-code availability.}
\label{tab:sat3dgen-method-comparison}
\small
\begin{tabularx}{\textwidth}{p{0.18\textwidth}XXp{0.15\textwidth}}
\toprule
Aspect & Published Sat3DGen & Analysed integration & Status \\
\midrule
Learned representation & DINOv3-conditioned tri-plane radiance field with
geometry-oriented training constraints & Released model or cached OBJ meshes
are consumed without retraining & Reused \\
Large-area fusion & Overlapping density windows are blended before global
Marching Cubes & Independently extracted meshes are cropped, positioned and
stitched in a shared local frame & Adapted \\
Geospatial frame & Normalised model/scene coordinates & Latitude--longitude
filenames, local metric axes and EPSG:27700 DSM queries & Added \\
Semantic/elevation data & Relative monocular depth prior during training;
semantic-map generation is a separate application & OSM labels and an external
DSM directly constrain post-inference geometry & Added downstream \\
Downstream assets & Rendered views, density/depth and mesh & Building-level OBJ,
MiniMesh, workspace and on-demand publication artefacts & Added downstream \\
Evaluated path & VIGOR-OOD image and DSM benchmarks & Fifteen cached OBJ files;
crop, bottom removal, stitch and DSM/no-DSM extraction & Different evidence \\
Model comparison & Sat2Density++ and controlled ablations & No same-input
reproduction of the original density-fusion baseline & Not established \\
\bottomrule
\end{tabularx}
\end{table}
```

---

## 8. 相关文献的作用与直接一手来源

| 文献 | 在论文中的作用 | 一手来源 |
|---|---|---|
| Sat3DGen | 上游生成模型、训练贡献、原大图策略、原指标与局限 | https://openreview.net/forum?id=E7JzkZCofa ; https://arxiv.org/abs/2605.14984 ; https://github.com/qianmingduowan/Sat3DGen |
| Sat3DGen fixed local upstream commit | 固定本地审计所对应的上游代码 | https://github.com/qianmingduowan/Sat3DGen/tree/882cc66c363aa16b82fb2e494be7600003076890 |
| Sat3DGen model card | 权重、许可证、用法、正式 BibTeX | https://huggingface.co/qian43/Sat3DGen |
| VIGOR | 非一一对应、非中心交叉视角数据设定 | https://openaccess.thecvf.com/content/CVPR2021/html/Zhu_VIGOR_Cross-View_Image_Geo-Localization_Beyond_One-to-One_Retrieval_CVPR_2021_paper.html |
| Sat2Density | 无深度监督的卫星条件密度场前序工作 | https://openaccess.thecvf.com/content/ICCV2023/html/Qian_Sat2Density_Faithful_Density_Learning_from_Satellite-Ground_Image_Pairs_ICCV_2023_paper.html |
| Sat2Density++ | 三平面、天空/光照和街景视频生成的直接前序工作 | https://arxiv.org/abs/2505.17001 ; https://doi.org/10.1109/TPAMI.2026.3652860 ; https://github.com/qianmingduowan/Sat2Density |
| Sat2Scene | 几何颜色化/3D diffusion 路线 | https://openaccess.thecvf.com/content/CVPR2024/html/Li_Sat2Scene_3D_Urban_Scene_Generation_from_Satellite_Images_with_Diffusion_CVPR_2024_paper.html |
| Sat2City | sparse voxel + cascaded latent diffusion；synthetic city 数据 | https://openaccess.thecvf.com/content/ICCV2025/html/Hua_Sat2City_3D_City_Generation_from_A_Single_Satellite_Image_with_ICCV_2025_paper.html |
| EG3D | Sat3DGen 所采用三平面显式—隐式表示的基础 | https://openaccess.thecvf.com/content/CVPR2022/html/Chan_Efficient_Geometry-Aware_3D_Generative_Adversarial_Networks_CVPR_2022_paper.html |
| Depth Anything V2 | Sat3DGen 相对深度伪标签来源 | https://proceedings.neurips.cc/paper_files/paper/2024/hash/26cfdcd8fe6fd75cc53e92963a656c58-Abstract-Conference.html |
| DINOv3 | Sat3DGen 的冻结卫星图编码器 | https://arxiv.org/abs/2508.10104 |
| CityDreamer | 邻近但不同的 unbounded generative city 路线 | https://openaccess.thecvf.com/content/CVPR2024/html/Xie_CityDreamer_Compositional_Generative_Model_of_Unbounded_3D_Cities_CVPR_2024_paper.html |
| Marching Cubes | 从密度体导出显式等值面 | https://doi.org/10.1145/37402.37422 |

---

## 9. 建议 BibTeX

### 9.1 更新现有 Sat3DGen 条目

正式会议引用宜以 OpenReview 为主；不要把 arXiv DOI 同时写成会议 DOI。可保留 eprint 字段：

```bibtex
@inproceedings{qian2026sat3dgen,
  title     = {{Sat3DGen}: Comprehensive Street-Level 3D Scene Generation from Single Satellite Image},
  author    = {Qian, Ming and Xia, Zimin and Liu, Changkun and Ma, Shuailei and Wang, Wen and Ke, Zeran and Tan, Bin and Zhang, Hang and Xia, Gui-Song},
  booktitle = {The Fourteenth International Conference on Learning Representations},
  year      = {2026},
  url       = {https://openreview.net/forum?id=E7JzkZCofa},
  eprint    = {2605.14984},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CV}
}
```

### 9.2 补全 Sat2Density++ 页码

```bibtex
@article{qian2026sat2densitypp,
  author  = {Qian, Ming and Tan, Bin and Wang, Qiuyu and Zheng, Xianwei and Xiong, Hanjiang and Xia, Gui-Song and Shen, Yujun and Xue, Nan},
  title   = {Seeing Through Satellite Images at Street Views},
  journal = {IEEE Transactions on Pattern Analysis and Machine Intelligence},
  year    = {2026},
  volume  = {48},
  number  = {5},
  pages   = {5692--5709},
  doi     = {10.1109/TPAMI.2026.3652860},
  url     = {https://doi.org/10.1109/TPAMI.2026.3652860}
}
```

### 9.3 建议新增的模型基础文献

```bibtex
@inproceedings{chan2022eg3d,
  author    = {Chan, Eric R. and Lin, Connor Z. and Chan, Matthew A. and Nagano, Koki and Pan, Boxiao and De Mello, Shalini and Gallo, Orazio and Guibas, Leonidas J. and Tremblay, Jonathan and Khamis, Sameh and Karras, Tero and Wetzstein, Gordon},
  title     = {Efficient Geometry-Aware 3D Generative Adversarial Networks},
  booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition},
  year      = {2022},
  month     = jun,
  pages     = {16123--16133},
  url       = {https://openaccess.thecvf.com/content/CVPR2022/html/Chan_Efficient_Geometry-Aware_3D_Generative_Adversarial_Networks_CVPR_2022_paper.html}
}

@inproceedings{yang2024depthanythingv2,
  author    = {Yang, Lihe and Kang, Bingyi and Huang, Zilong and Zhao, Zhen and Xu, Xiaogang and Feng, Jiashi and Zhao, Hengshuang},
  title     = {Depth Anything V2},
  booktitle = {Advances in Neural Information Processing Systems},
  volume    = {37},
  year      = {2024},
  doi       = {10.52202/079017-0688},
  url       = {https://proceedings.neurips.cc/paper_files/paper/2024/hash/26cfdcd8fe6fd75cc53e92963a656c58-Abstract-Conference.html}
}

@article{simeoni2025dinov3,
  author  = {Sim{\'e}oni, Oriane and Vo, Huy V. and Seitzer, Maximilian and Baldassarre, Federico and Oquab, Maxime and Jose, Cijo and Khalidov, Vasil and Szafraniec, Marc and Yi, Seungeun and Ramamonjisoa, Micha{\"e}l and Massa, Francisco and Haziza, Daniel and Wehrstedt, Luca and Wang, Jianyuan and Darcet, Timoth{\'e}e and Moutakanni, Th{\'e}o and Sentana, Leonel and Roberts, Claire and Vedaldi, Andrea and Tolan, Jamie and Brandt, John and Couprie, Camille and Mairal, Julien and J{\'e}gou, Herv{\'e} and Labatut, Patrick and Bojanowski, Piotr},
  title   = {{DINOv3}},
  journal = {arXiv preprint arXiv:2508.10104},
  year    = {2025},
  url     = {https://arxiv.org/abs/2508.10104}
}

@inproceedings{xie2024citydreamer,
  author    = {Xie, Haozhe and Chen, Zhaoxi and Hong, Fangzhou and Liu, Ziwei},
  title     = {CityDreamer: Compositional Generative Model of Unbounded 3D Cities},
  booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition},
  year      = {2024},
  month     = jun,
  pages     = {9666--9675},
  url       = {https://openaccess.thecvf.com/content/CVPR2024/html/Xie_CityDreamer_Compositional_Generative_Model_of_Unbounded_3D_Cities_CVPR_2024_paper.html}
}
```

现有 `qian2023sat2density`、`li2024sat2scene`、`hua2025sat2city`、`zhu2021vigor` 与 `lorensen1987marching` 条目基本可用；应优先修正 Sat3DGen 会议/eprint 关系并补全 Sat2Density++ 卷期页码。

---

## 10. 若要形成真正的“与原方法对比”，最低实验设计

在没有以下实验前，论文只应做结构和能力对照。

1. 固定一个**连续** London AOI 和精确 tile allow-list，不再递归读取整个缓存目录；
2. 固定官方 commit、checkpoint SHA-256、模型配置、输入图像哈希、zoom/GSD、步长、crop 和 Marching Cubes threshold；
3. 对同一影像运行两条路径：
   - A：官方 density-domain sliding-window fusion → one global Marching Cubes；
   - B：per-tile Marching Cubes → 本项目 mesh-domain crop/place/stitch；
4. 在外部 DSM 修正**之前**比较：coverage、seam height residual、seam normal discontinuity、colour discontinuity、boundary/non-manifold edges、components、watertightness、runtime 和 peak VRAM；
5. 把外部 DSM 修正作为单独阶段。因为 DSM 是输入，不能用同一 DSM 证明独立“重建准确率提高”；应使用 withheld elevation source，或只报告 constraint residual；
6. 对 bottom removal、OSM pre-alignment、stitching、DSM correction 做逐项 ablation；
7. 保存每一步的 manifest、日志、命令、代码/模型/配置哈希和可视化；
8. 至少报告一个失败案例和两个不同形态/地形的 AOI；
9. 不把 Sat3DGen 论文的 VIGOR-OOD FID/RMSE 与 London 后处理指标混入同一“胜负”列，除非完整复现相同数据和协议。

可检验假设应写为：

> Compared with density-domain fusion, mesh-domain integration is designed to expose explicit geographic placement and building-level asset boundaries, but may incur seam and topology errors after independent isosurface extraction. The controlled experiment evaluates this trade-off rather than presupposing an improvement.

---

## 11. 对主论文更新的建议落点

- `2 Introduction and Background.tex`
  - 扩展为 §5 的四至五段叙事；
  - 加入 Sat3DGen 真实模型贡献和 project gap；
  - 引入 EG3D、Depth Anything V2、DINOv3、CityDreamer，但避免形成泛泛 survey。
- `3 Methodology.tex`
  - 插入 §6.1 和 §7 的对照表；
  - 明确 paper/release density fusion 与活动 mesh fusion 的差别；
  - 明确 Gradio/API、下载与推理是 implemented/not exercised，并记录接口风险。
- `4 Results and Analysis.tex`
  - 原论文指标只能作为 cited prior-work context；
  - 本项目结果继续报告 15 cached OBJ 的 stage counts、两集群、DSM displacement 和拓扑反例；
  - 不建立“Sat3DGen vs ours accuracy”列，除非完成 §10。
- `5 Discussion.tex`
  - 使用 §6.3–6.4；
  - 强调模型贡献、后处理贡献、系统贡献三层所有权；
  - 把 mesh-domain strategy 的优劣写为尚待 controlled comparison 的 trade-off。
- `references.bib`
  - 按 §9 修正/补充。

---

## 12. 最终审稿口径

论文应始终维持以下四个主语：

1. **Qian et al. / Sat3DGen**：模型、训练损失、原论文指标；
2. **the released Sat3DGen implementation**：官方代码与权重能力；
3. **the analysed active mesh pipeline**：本项目根 `mesh_pipeline` 的实际实现；
4. **the evaluated cached-mesh experiment**：真正执行且有本地 artefact 的部分。

只要这四个主语不混写，论文就能避免最严重的贡献归属与证据越界问题。
