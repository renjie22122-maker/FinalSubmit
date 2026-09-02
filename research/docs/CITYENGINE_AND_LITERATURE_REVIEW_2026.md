# CityEngine 对比与扩展文献综述备忘录（2026）

> English version: [CITYENGINE_AND_LITERATURE_REVIEW_2026_EN.md](CITYENGINE_AND_LITERATURE_REVIEW_2026_EN.md)

更新日期：2026-08-13（Europe/London）  
用途：供论文 Introduction/Background、Discussion、References 更新时采用。  
证据状态：CityEngine 结论来自 Esri 官方产品文档、官方 SDK 文档和原始论文；本地论文工作区没有检索到 CityEngine 实验记录，因此下文的 CityEngine 集成与实验均标为“建议”，不能写成“已实现”或“已验证”。

## 1. 可直接采用的核心定位

CityEngine、Sat3DGen 与本项目并非三个完成同一任务的替代系统，而是处于城市三维内容链条的不同位置：

- **CityEngine** 是以 GIS 初始形状、对象属性、资产和 CGA 规则为输入的程序化城市设计/生成环境，重点是可控生成、情景迭代、参数编辑和 GIS/内容制作互操作。
- **Sat3DGen** 是从单幅卫星图像推断街景尺度三维场景的学习式方法，重点是从观测条件恢复或生成几何与外观，并输出可渲染的显式网格。
- **当前 ChordAtlas 集成** 研究的不是新生成模型，而是如何把 Sat3DGen-derived mesh 连同坐标、DSM、footprint、cache identity、manifest 和失败状态可靠地发布到一个程序化城市系统中。

建议论文使用以下英文表述（需按最终章节语气微调）：

> CityEngine and Sat3DGen represent complementary rather than interchangeable urban-modelling paradigms. CityEngine derives controllable polygonal city content from initial shapes, GIS attributes and authored CGA rules, whereas Sat3DGen infers scene geometry and appearance from a single overhead observation using a learned, geometry-first representation. The present work does not claim to replace either paradigm. It investigates the coordinate, provenance, geometry and publication contracts required for satellite-derived meshes to coexist with structured procedural content in ChordAtlas.

CityEngine 官方说明明确将其核心描述为程序化建模：道路形成街区、街区细分为地块，随后由 CGA 规则产生多边形建筑模型；输入阶段也可以从已有街区或 mass model 开始。参见 [Introduction to CityEngine](https://doc.arcgis.com/en/cityengine/latest/get-started/get-started-about-cityengine.htm)。Sat3DGen 的正式摘要则将其描述为 geometry-first 的单卫星图像到街景三维场景方法；参见 [ICLR 2026 / OpenReview](https://openreview.net/forum?id=E7JzkZCofa)、[arXiv:2605.14984](https://arxiv.org/abs/2605.14984) 和 [官方代码库](https://github.com/qianmingduowan/Sat3DGen)。

## 2. CityEngine：可核验的产品与方法事实

### 2.1 目标和生成范式

CityEngine 的主要抽象是“initial shape + rule file + attributes -> generated polygonal model”。CGA（Computer Generated Architecture）通过 `extrude`、`split`、texture 等操作迭代细化 shape；规则可以全局分配，也可以逐建筑分配，规则参数和随机种子可以逐对象覆盖。CityEngine 同时提供动态街道、街区、地块编辑，底层布局变化时依赖模型可重建。这些事实支持“规则驱动、可编辑、适合方案迭代”的定位，但不支持“自动从像素恢复真实建筑”的说法。

权威来源：

- [Esri: Introduction to CityEngine](https://doc.arcgis.com/en/cityengine/latest/get-started/get-started-about-cityengine.htm)
- [Esri: Essentials—Rule-based modeling](https://doc.arcgis.com/en/cityengine/latest/tutorials/essentials-rule-based-modeling.htm)
- Müller et al., *Procedural Modeling of Buildings*, DOI [10.1145/1141911.1141931](https://doi.org/10.1145/1141911.1141931)

### 2.2 输入、GIS 属性与坐标系统

CityEngine 能导入 FileGDB、SHP、OSM、KML、OBJ、glTF、USD 等多类 GIS/三维格式。FileGDB 导入时可保留非几何属性、字段数据类型、domain 及 relationship class；如果 CGA attribute 与导入的 object attribute 同名，属性可驱动生成。新 scene 可继承首个 FileGDB layer 的 coordinate system，后续不同 CRS 的层会转换到 scene CRS。这说明 CityEngine 的 GIS 语义比单纯 OBJ 更丰富，但它主要体现为 layer、object attribute、relationship 和 report，并不自动等于 CityGML 的完整城市对象本体或 wall/roof/window semantic decomposition。

权威来源：

- [Esri: Import FileGDB](https://doc.arcgis.com/en/cityengine/latest/help/help-import-fgdb.htm)
- [Esri: Work with GIS data](https://doc.arcgis.com/en/cityengine/latest/tutorials/essentials-work-with-gis-data.htm)
- [Esri: Supported data formats](https://doc.arcgis.com/en/cityengine/latest/help/import-data-types.htm)

### 2.3 显式网格的两种导入语义

这一点对 Sat3DGen 对比非常重要，不能笼统写成“CityEngine 不能编辑 OBJ”：

1. **作为 static model 导入**：OBJ/glTF 等几何按原样引用，可移动、缩放、旋转，但不能由 CGA 修改 geometry、texture 或 individual vertices。源文件移动或重命名会破坏引用。
2. **作为 shape 导入**：OBJ/glTF 可作为 CGA initial shape。外部 mass model 的 faces 可以用 `comp(f)` 等规则分为 roof/facade 再细化。

因此，一个 Sat3DGen dense triangular mesh 在 CityEngine 中可被用作视觉 reference/static model，也可能作为 shape 输入；但“可导入”不等于“已获得可用建筑语义”。若网格没有稳定的 per-building identity、朝向、watertightness、roof/wall 分解或合理面复杂度，CGA 规则并不会自动修复这些问题。

权威来源：

- [Esri: Work with static models](https://doc.arcgis.com/en/cityengine/latest/help/help-static-model.htm)
- [Esri: Shapes](https://doc.arcgis.com/en/cityengine/latest/help/help-shape-layer.htm)
- [Esri: Import initial shapes tutorial](https://doc.arcgis.com/en/cityengine/latest/tutorials/tutorial-5-import-initial-shapes.htm)

### 2.4 LOD 与语义 LOD 的边界

CityEngine 可由 CGA attribute 生成 low/medium/high 等多个模型版本，DATASMITH exporter 也能按 enum attribute 输出 LOD。这个 LOD 是作者控制的几何/材质复杂度变体。它不应自动称为 CityGML LOD0--LOD3：CityGML 3.0 的 LOD 是标准概念模型中的多重空间表示，且与城市对象语义相关。除非明确建立 mapping 并验证输出，否则论文应写“CGA-controlled detail variants”，而不是“CityGML-compliant LoD”。

权威来源：

- [Esri: Export DATASMITH—LOD attribute](https://doc.arcgis.com/en/cityengine/latest/help/help-export-unreal.htm)
- [OGC CityGML 3.0 Conceptual Model](https://docs.ogc.org/is/20-010/20-010.html)
- Biljecki, Ledoux & Stoter, *An improved LOD specification for 3D building models*, DOI [10.1016/j.compenvurbsys.2016.04.005](https://doi.org/10.1016/j.compenvurbsys.2016.04.005)

### 2.5 编辑、交互与自动化

CityEngine 支持 viewport handles、Inspector attributes、local edits、动态 street/block layout、scenario 和 CGA reports/dashboard。Python API 可编辑 scene data、调用 CGA、自动导入/导出以及控制 UI；PRT/SDK 和 PyPRT 可在外部应用中消费 CityEngine authoring 的 rule package。因此 CityEngine 的强项是可控再生成和方案交互，而不是仅呈现一份冻结网格。

可重复性不是自动成立的。一个可复现实验至少需固定：CityEngine/PRT 版本、scene CRS、输入数据和 hashes、CGA/RPK、asset versions、rule attributes、random seed、Python script、exporter 与设置、输出 hashes。仅保留 `.cej` 或截图不够。

权威来源：

- [Esri: Python scripting](https://doc.arcgis.com/en/cityengine/latest/python/cityengine-python-intro.htm)
- [Esri: Script-based export](https://doc.arcgis.com/en/cityengine/latest/python/python-script-based-export.htm)
- [Esri: CityEngine SDK / PRT](https://esri.github.io/cityengine/cityenginesdk)
- [Esri: Dashboards](https://doc.arcgis.com/en/cityengine/latest/help/help-dashboards.htm)

### 2.6 授权与“闭源”表述

最准确的描述不是一句“CityEngine 完全闭源”：

- CityEngine 是需通过 Esri user type/商业授权获得的产品；官方提供 trial。
- CityEngine SDK/PRT 可免费用于 personal、educational 和 non-commercial use；commercial use 要求相应商业授权，redistribution 或 web-service offering 需另行许可。
- SDK repository 中的 source-code examples 以 Apache-2.0 发布，但 PRT binary/runtime 仍受 Esri Terms of Use 约束。

因此建议写：**“CityEngine is a commercially licensed proprietary authoring/runtime ecosystem with publicly documented CGA interfaces and some Apache-licensed SDK examples; its runtime is not an unrestricted open-source dependency.”** 不要写“CityEngine SDK 全部 Apache-2.0”或“任何研究用途都必须购买商业许可”。

权威来源：

- [Esri: CityEngine SDK licensing](https://esri.github.io/cityengine/cityenginesdk)
- [Esri: CityEngine trial](https://www.esri.com/en-us/arcgis/products/arcgis-cityengine/trial)
- [Esri: 2025 user-type licensing migration](https://support.esri.com/en-us/knowledge-base/product-sales-update-arcgis-cityengine-000035055)

## 3. 三方对照表

| 维度 | CityEngine / CGA | Sat3DGen 原始方法 | 本项目的 ChordAtlas bridge |
|---|---|---|---|
| 核心问题 | 如何从 GIS shapes、attributes 和规则快速生成、编辑与迭代城市方案 | 如何从一幅 satellite image 生成街景尺度三维几何和外观 | 如何把 learned mesh 可靠地定位、处理、关联建筑并发布到程序化系统 |
| 典型输入 | streets、blocks、lots、footprints、mass models、GIS attributes、terrain、CGA/RPK、assets | 单幅卫星 RGB（推理）；训练阶段使用 cross-view supervision 和论文定义的几何约束 | satellite tiles、Sat3DGen meshes、OSM footprints、DSM、local frame、manifests、selection |
| 生成范式 | symbolic / rule-based / attribute-driven procedural generation | learned conditional generation，geometry-first feed-forward representation | deterministic orchestration、mesh post-processing、coordinate conversion、transactional publication |
| 主要输出 | polygonal building/street models、layers、attributes/reports、multiple export formats | neural/triplane scene representation、renderings、extracted textured mesh/large-scale patches | corrected scene/building OBJ、MiniMesh、generator graph、manifest/state records |
| GIS 语义 | 可保留 feature attributes、relationships 和 scene CRS；语义由输入与规则显式定义 | 论文中的“semantic diversity”主要指场景内容；导出的 triangle mesh 不是自动的 GIS ontology | footprint association 提供 coarse per-building identity；尚非完整 wall/roof/window semantic model |
| LOD | CGA parameter 可生成多个 detail variants；可按 exporter 输出 | extraction resolution/representation scale；不是 CityGML LOD | 当前没有经验证的 semantic LOD hierarchy |
| 编辑 | 强：attributes、handles、local edits、street/block/scenario regeneration | 弱于规则系统：通常需重新推理或在通用 mesh tool 中编辑 | selection、per-building loading 和 ChordAtlas procedural operations；learned surface 本身仍非参数化 |
| 坐标 | scene CRS 和 GIS reprojection；非地理 OBJ/glTF 需显式放置 | 原始论文的生成 output 不自动提供本项目所需的 world-coordinate contract | 明确 WGS84 origin 与 X-east/Y-up/Z-south local frame，但当前近似与 EPSG:27700 有已量化偏差 |
| 可重复性 | rules/scripts/seeds 可版本化，但必须固定 product/runtime/version/assets/export settings | code 已公开且 repo 标示 MIT；checkpoint、data、config、environment 仍需单独锁定 | manifest 设计是贡献点，但当前 active merge 与部分 cache 仍缺 input/model/config hashes |
| 授权 | 商业产品/受限 runtime；SDK examples 与 runtime 授权不同 | 代码仓库标示 MIT；具体数据和第三方 weights 的授权应逐项检查 | ChordAtlas、数据、Google/OSM/EA DSM 与每个 dependency 的条款需分别报告 |
| 合理评价 | controllability、edit persistence、semantic attribute retention、scenario/LOD generation、runtime | geometry RMSE、rendering quality、cross-view consistency、generalisation | coordinate error、coverage、topology、cache correctness、failure atomicity、end-to-end evidence |
| 本论文角色 | 相关工业/研究范式和潜在下游对照，不是已运行 baseline | upstream prior method，不是学生贡献 | 论文实际研究对象与可主张的工程贡献 |

### 3.1 关键解释

- CityEngine 中“attribute semantics”与 Sat3DGen 论文中“semantic diversity”不是同一个概念。前者通常是 machine-readable GIS/CGA attributes；后者可以只是视觉内容类别。
- CityEngine 的可编辑规则模型与 Sat3DGen 的观测条件几何各有不同优势。程序化模型可能几何整洁但依赖 footprint/height/rule；learned mesh 可能保留观测外观但拓扑和建筑语义不足。
- 本项目的价值正位于这两者之间：它尝试把 observation-derived mesh 变成可定位、可追踪、可按建筑消费的 asset，而不是证明 learned generation 在所有方面优于 procedural generation。

## 4. 建议的 CityEngine 集成定位

如果论文只做文献/产品比较，建议把 CityEngine 放在 Background 和 Discussion，不放入 Results。若后续真的执行 CityEngine 实验，才可以加入 Results。

建议的 hybrid architecture：

```text
                            +--> footprints + GIS attributes --> CityEngine/CGA variants
data_builder / geodata ----+
                            +--> satellite tiles --> Sat3DGen --> cleaned per-building OBJ
                                                               |
                                                               +--> ChordAtlas MiniMesh/BlockGen

shared contract: AOI + CRS/local frame + building ID + source hashes + model/rule version
```

最可行的互操作定位：

1. 把 Sat3DGen corrected per-building OBJ 作为 CityEngine static reference，用同一 footprint layer 生成 CGA building，做 side-by-side 分析。
2. 或把简化/分面后的 building volume 作为 CityEngine shape，再用 CGA 对 roof/facade 做程序化细化；在这之前必须验证 orientation、components 和 topology。
3. 把 footprint、building ID、height summary、source tile IDs、CRS、model/config hashes 和 quality flags 作为 FileGDB/CSV/sidecar attributes，而不是只交换 OBJ filename。
4. 使用 OBJ/glTF 做几何互换时，同时保留独立 manifest；OBJ 本身不携带完整 CRS、lineage、semantic hierarchy 或 execution status。

当前可写成 proposed integration path，不能写成 completed CityEngine interoperability。

## 5. 如果要做实证对比：最低可辩护协议

### 5.1 条件

对同一小型 contiguous AOI 建立至少三组结果：

- **CE-FP**：CityEngine 从同一 footprints 与可用 heights 生成的确定性 CGA massing；固定 rule 和 seed。
- **S3G-RAW**：同一 AOI 的 Sat3DGen raw mesh。
- **S3G-BRIDGE**：经过 active crop/bottom/stitch/DSM/building extraction 的输出。
- 可选 **HYBRID**：cleaned Sat3DGen mass/shape + CGA facade/roof refinement。

必须同时报告两种公平性设置：

- **equal-information**：两条路径只能使用预先规定的共同输入；
- **best-available**：CityEngine 可以使用 authoritative footprint/height/GIS attributes，Sat3DGen 使用其正常 satellite input。

若 CityEngine 使用 ground-truth height 而 Sat3DGen 不使用，不能把较低 height error 解释成生成算法本身更优。

### 5.2 指标

- 几何：registered surface/Chamfer distance、height MAE/RMSE、2D footprint precision/recall/IoU、completeness、component count。
- 拓扑：finite vertices、valid indices、degenerate/duplicate faces、boundary edges、non-manifold edges、watertightness。
- 语义：有稳定 building ID 的比例、GIS attribute retention、roof/wall semantic accuracy（仅在有人工/权威标签时）。
- 复杂度与性能：vertices/faces、file size、generation/export time、peak memory、rerun hash stability。
- 编辑性：修改 footprint、height 或 façade parameter 后的操作数/时间，以及局部修改是否在 regeneration 后保持。
- 可重复性：software version、hardware、rule/checkpoint/config hash、seed、CRS、command、input/output hash、cache decision。

### 5.3 不能混用的指标

- Sat3DGen 论文在 VIGOR-OOD/DSM benchmark 上的 5.20 m RMSE 与 FID 19 只能作为 published context，不能当成本地 London bridge 的结果。
- CityEngine 没有一个天然的、与 Sat3DGen FID 对齐的质量分数；必须固定 renderer、camera path、lighting、resolution 和 reference images 后才可比较 rendering metric。
- “模型更漂亮”“更真实”“更容易编辑”若没有 protocol，应写成 qualitative observation，不应写成 superiority claim。

## 6. 扩展文献综述：建议结构与论证链

### 6.1 程序化城市和交互控制

论证链：规则方法解决大规模重复生成和参数控制，但规则 authoring、输入语义和 unique buildings 仍需要人工或外部数据；因此 learned observation-driven geometry 与 procedural structure 的结合有价值。

1. Parish & Müller (2001) 用扩展 L-system 建模 street pattern 和 city；奠定大规模程序化城市生成背景，但不是从真实卫星像素恢复城市。DOI [10.1145/383259.383292](https://doi.org/10.1145/383259.383292)。
2. Wonka et al. (2003) 以 split grammar 和 attribute matching 生成建筑；说明 grammar 对风格和规模的优势。DOI [10.1145/882262.882324](https://doi.org/10.1145/882262.882324)。
3. Müller et al. (2006) 提出 CGA shape grammar；是 CityEngine/CGA 最直接的学术基础，现有 bibliography 已含。DOI [10.1145/1141911.1141931](https://doi.org/10.1145/1141911.1141931)。
4. Chen et al. (2008) 用 tensor field 与交互操作建模 street network；适合支持“procedural + interactive control”。DOI [10.1145/1360612.1360702](https://doi.org/10.1145/1360612.1360702)。
5. Lipp et al. (2011) 讨论 procedural content layer、valid layout 与 edit persistence；直接对应“修改后仍保持用户定制”的可编辑性。DOI [10.1111/j.1467-8659.2011.01865.x](https://doi.org/10.1111/j.1467-8659.2011.01865.x)。
6. Kelly & Wonka (2011) ProceX 用交互式 procedural extrusion 生成可编辑 two-manifold architecture；现有 bibliography 已含。DOI [10.1145/1944846.1944854](https://doi.org/10.1145/1944846.1944854)。
7. Vanegas et al. (2012) 处理 block-to-parcel subdivision 和 editing persistence；有助于说明 CityEngine pipeline 中 lot generation 的方法背景。DOI [10.1111/j.1467-8659.2012.03047.x](https://doi.org/10.1111/j.1467-8659.2012.03047.x)。
8. Smelik et al. (2014) 是以 control/interactivity 为重点的 procedural virtual-world survey，可用于精炼而不是扩大综述。DOI [10.1111/cgf.12276](https://doi.org/10.1111/cgf.12276)。

### 6.2 卫星/跨视角到三维

论证链：multi-view satellite photogrammetry 的目标是观测约束的 reconstruction；Sat2Density/Sat2Scene/Sat2City/Sat3DGen 则研究 satellite-conditioned generation。它们的输入、监督、表示和 evaluation protocol 不同，不能只按发表年份排列或直接转移指标。

1. Derksen & Izzo (2021), S-NeRF：使用多视角高分辨率卫星图像和已知视角，处理 sun/shadow；这是 multi-view reconstruction，而不是单图生成。[CVPRW paper](https://openaccess.thecvf.com/content/CVPR2021W/EarthVision/html/Derksen_Shadow_Neural_Radiance_Fields_for_Multi-View_Satellite_Photogrammetry_CVPRW_2021_paper.html)。
2. Marí et al. (2022), Sat-NeRF：把 RPC camera、transient objects 与 shadow modelling 引入多视角 satellite photogrammetry。[CVPRW paper](https://openaccess.thecvf.com/content/CVPR2022W/EarthVision/html/Mari_Sat-NeRF_Learning_Multi-View_Satellite_Photogrammetry_With_Transient_Objects_and_Shadow_CVPRW_2022_paper.html)。
3. Qian et al. (2023), Sat2Density：从 satellite-ground pairs 学 density field，无 depth supervision，现有 bibliography 已含。[ICCV paper](https://openaccess.thecvf.com/content/ICCV2023/html/Qian_Sat2Density_Faithful_Density_Learning_from_Satellite-Ground_Image_Pairs_ICCV_2023_paper.html)。
4. Li et al. (2024), Sat2Scene：在给定 geometry 上生成 point-level appearance 并以 neural renderer 输出 arbitrary views；不是与 Sat3DGen 完全相同的 explicit-mesh task，现有 bibliography 已含。[CVPR paper](https://openaccess.thecvf.com/content/CVPR2024/html/Li_Sat2Scene_3D_Urban_Scene_Generation_from_Satellite_Images_with_Diffusion_CVPR_2024_paper.html)。
5. Hua et al. (2025), Sat2City：在 synthetic city dataset 上以 sparse voxel + cascaded latent diffusion 生成三维城市；dataset domain 与 London/VIGOR 要明确区分，现有 bibliography 已含。[ICCV paper](https://openaccess.thecvf.com/content/ICCV2025/html/Hua_Sat2City_3D_City_Generation_from_A_Single_Satellite_Image_with_ICCV_2025_paper.html)。
6. Qian et al. (2026), Sat3DGen：geometry-first single-image method，是本项目 upstream method；需把 paper claim 与 local bridge evidence 分开。[OpenReview](https://openreview.net/forum?id=E7JzkZCofa)。

### 6.3 语义城市模型和 LOD

论证链：triangle mesh 能渲染不代表它是 semantic city model；building identity、boundary surfaces、CRS、LOD 和 relationships 是另一层数据合同。这正好支撑本项目从 OBJ 到 per-building publication/manifest 的研究动机。

1. OGC CityGML 3.0 定义 georeferenced 3D geometry + semantics + relationships，并支持 LOD0--3；它是概念对照，不要声称当前输出 compliant。[OGC 20-010](https://docs.ogc.org/is/20-010/20-010.html)。
2. Biljecki et al. (2015) 综述 3D city model applications，可用于说明 simulation/planning 等 downstream requirement。DOI [10.3390/ijgi4042842](https://doi.org/10.3390/ijgi4042842)。
3. Biljecki, Ledoux & Stoter (2016) 说明 LOD 不能只用“越细越高”概括，几何复杂度和语义粒度需要区分。DOI [10.1016/j.compenvurbsys.2016.04.005](https://doi.org/10.1016/j.compenvurbsys.2016.04.005)。
4. Ledoux et al. (2019) CityJSON 说明如何以较易实现的 JSON encoding 保留 CityGML data model；可作为未来 manifest/semantic export 的方向，而非当前已实现格式。DOI [10.1186/s40965-019-0064-0](https://doi.org/10.1186/s40965-019-0064-0)。

### 6.4 Mesh repair、拓扑与 3D GIS validity

论证链：下游 algorithm 对 manifoldness、closedness、valid indices 和 orientation 的要求各异；“repair”也可能改变 geometry。论文应把 current tests 定位为 contract checks，不要因为一个工具产生 watertight mesh 就默认更准确。

1. Guéziec et al. (2001) 处理 non-manifold polygon sets 的 cutting/stitching，适合引用于 topology-aware stitching。DOI [10.1109/2945.928166](https://doi.org/10.1109/2945.928166)。
2. Ju (2004) 从 arbitrary polygon soup 构造 inside/outside volume 并重建 closed surface；稳健，但 volumetric recontouring 会改变原始几何。DOI [10.1145/1015706.1015815](https://doi.org/10.1145/1015706.1015815)。
3. Attene (2010) 倾向于局部修改 defective neighborhoods；适合解释 local repair 与 global resampling 的权衡。DOI [10.1007/s00371-010-0416-3](https://doi.org/10.1007/s00371-010-0416-3)。
4. Campen, Attene & Kobbelt (2012) 系统整理 mesh defect、application-specific requirement 与 repair trade-off。DOI [10.2312/conf/EG2012/tutorials/t4](https://doi.org/10.2312/conf/EG2012/tutorials/t4)。
5. Ledoux (2018) 的 val3dity 依据 ISO 19107 检查 3D GIS primitive，直接连接 mesh topology 与 semantic city data validity。DOI [10.1186/s40965-018-0043-x](https://doi.org/10.1186/s40965-018-0043-x)。

### 6.5 Geospatial provenance 与可重复性

论证链：manifest 是载体，不是 provenance 的充分条件。有效 lineage 需要明确 entity、activity、agent/implementation、input/output、plan/configuration 和时间关系；cataloguing assets 也不等于记录 execution provenance。

1. Yue, Gong & Di (2010) 将 metadata tracking、geoprocessing service chains 与 geospatial product derivation 联系起来；非常贴合当前多阶段 bridge。DOI [10.1016/j.cageo.2009.09.002](https://doi.org/10.1016/j.cageo.2009.09.002)。
2. W3C PROV-O 提供 Entity/Activity/Agent 等可交换 provenance 概念模型。[W3C Recommendation](https://www.w3.org/TR/2013/REC-prov-o-20130430/)。
3. Zhang et al. (2020) 将 OGC WPS plan/execution 与 W3C PROV 结合，强调 construction、execution 和 provenance 三阶段。DOI [10.1016/j.cageo.2020.104419](https://doi.org/10.1016/j.cageo.2020.104419)。
4. STAC 1.1.0 可描述和发现 spatiotemporal assets、links 与 roles；它适合作为 imagery/DSM/mesh catalog layer，但不能代替 checkpoint/config/command/exit state 的 workflow provenance。[STAC 1.1.0](https://github.com/radiantearth/stac-spec/tree/v1.1.0)。
5. Sandve et al. (2013) 支持记录每一步、保留 raw data、记录 software version 与 random seed 等可重复计算原则。DOI [10.1371/journal.pcbi.1003285](https://doi.org/10.1371/journal.pcbi.1003285)。

## 7. 推荐优先级

### 必须加入或更新

- Esri CityEngine introduction、FileGDB attributes、static model/shape、SDK licensing（官方文档）。
- Parish & Müller (2001) 或 Wonka et al. (2003) 中至少一篇，补足 Müller (2006) 之前的方法线索。
- OGC CityGML 3.0 + Biljecki et al. (2016) LOD，用于严格区分 mesh、GIS semantics 与 LOD。
- Ju (2004) 或 Attene (2010) + Ledoux (2018)，用于支持 mesh repair/validity。
- W3C PROV-O + Yue et al. (2010)，用于支持 manifest/provenance 区分。
- 修正 Sat3DGen BibTeX：ICLR/OpenReview 为 venue，arXiv 只放 `eprint`，不要把 arXiv DOI 当作 ICLR DOI。
- 刷新 Sat2Density++ 的卷期页码：IEEE TPAMI 48(5), 5692--5709, DOI `10.1109/TPAMI.2026.3652860`（作者代码库也列出该元数据）。

### 有篇幅再加入

- Chen et al. (2008)、Lipp et al. (2011)、Vanegas et al. (2012)：交互 street/layout/parcel。
- Sat-NeRF：用来分开 multi-view satellite reconstruction 与 single-image generation。
- CityJSON：未来 semantic interchange。
- Campen et al. (2012)：mesh repair survey。
- Zhang et al. (2020)、STAC：未来 interoperable provenance/catalog design。

## 8. BibTeX 建议（已核对元数据）

以下条目可复制到 `references.bib` 后再按现有 key style 统一；不要一次全部加入而不在正文使用。

```bibtex
@inproceedings{parish2001procedural,
  author    = {Parish, Yoav I. H. and M{\"u}ller, Pascal},
  title     = {Procedural Modeling of Cities},
  booktitle = {Proceedings of the 28th Annual Conference on Computer Graphics and Interactive Techniques},
  year      = {2001},
  pages     = {301--308},
  publisher = {ACM},
  doi       = {10.1145/383259.383292},
  url       = {https://doi.org/10.1145/383259.383292}
}

@article{wonka2003instant,
  author  = {Wonka, Peter and Wimmer, Michael and Sillion, Fran{\c{c}}ois and Ribarsky, William},
  title   = {Instant Architecture},
  journal = {ACM Transactions on Graphics},
  year    = {2003},
  volume  = {22},
  number  = {3},
  pages   = {669--677},
  doi     = {10.1145/882262.882324},
  url     = {https://doi.org/10.1145/882262.882324}
}

@article{chen2008streets,
  author  = {Chen, Guoning and Esch, Gregory and Wonka, Peter and M{\"u}ller, Pascal and Zhang, Eugene},
  title   = {Interactive Procedural Street Modeling},
  journal = {ACM Transactions on Graphics},
  year    = {2008},
  volume  = {27},
  number  = {3},
  pages   = {1--10},
  doi     = {10.1145/1360612.1360702},
  url     = {https://doi.org/10.1145/1360612.1360702}
}

@article{lipp2011citylayouts,
  author  = {Lipp, Markus and Scherzer, Daniel and Wonka, Peter and Wimmer, Michael},
  title   = {Interactive Modeling of City Layouts Using Layers of Procedural Content},
  journal = {Computer Graphics Forum},
  year    = {2011},
  volume  = {30},
  number  = {2},
  pages   = {345--354},
  doi     = {10.1111/j.1467-8659.2011.01865.x},
  url     = {https://doi.org/10.1111/j.1467-8659.2011.01865.x}
}

@article{vanegas2012parcels,
  author  = {Vanegas, Carlos A. and Kelly, Tom and Weber, Basil and Halatsch, Jan and Aliaga, Daniel G. and M{\"u}ller, Pascal},
  title   = {Procedural Generation of Parcels in Urban Modeling},
  journal = {Computer Graphics Forum},
  year    = {2012},
  volume  = {31},
  number  = {2pt3},
  pages   = {681--690},
  doi     = {10.1111/j.1467-8659.2012.03047.x},
  url     = {https://doi.org/10.1111/j.1467-8659.2012.03047.x}
}

@article{smelik2014survey,
  author  = {Smelik, Ruben M. and Tutenel, Tim and Bidarra, Rafael and Benes, Bedrich},
  title   = {A Survey on Procedural Modelling for Virtual Worlds},
  journal = {Computer Graphics Forum},
  year    = {2014},
  volume  = {33},
  number  = {6},
  pages   = {31--50},
  doi     = {10.1111/cgf.12276},
  url     = {https://doi.org/10.1111/cgf.12276}
}

@misc{esriCityEngineIntro2026,
  author = {{Esri}},
  title  = {Introduction to {ArcGIS CityEngine}},
  year   = {2026},
  url    = {https://doc.arcgis.com/en/cityengine/latest/get-started/get-started-about-cityengine.htm},
  note   = {Living online documentation; accessed 13 August 2026}
}

@misc{esriCityEngineGIS2026,
  author = {{Esri}},
  title  = {Import {FileGDB} ({Esri File Geodatabase})},
  year   = {2026},
  url    = {https://doc.arcgis.com/en/cityengine/latest/help/help-import-fgdb.htm},
  note   = {ArcGIS CityEngine online documentation; accessed 13 August 2026}
}

@misc{esriCityEngineSDK2026,
  author = {{Esri}},
  title  = {{ArcGIS CityEngine SDK}: Procedural Runtime and Licensing Information},
  year   = {2026},
  url    = {https://esri.github.io/cityengine/cityenginesdk},
  note   = {Official SDK documentation; accessed 13 August 2026}
}

@techreport{ogcCityGML3,
  author      = {{Open Geospatial Consortium}},
  title       = {{OGC City Geography Markup Language (CityGML) Part 1: Conceptual Model Standard}},
  institution = {Open Geospatial Consortium},
  type        = {OGC Standard},
  number      = {20-010},
  year        = {2021},
  url         = {https://docs.ogc.org/is/20-010/20-010.html},
  note        = {Version 3.0.0}
}

@article{biljecki2016lod,
  author  = {Biljecki, Filip and Ledoux, Hugo and Stoter, Jantien},
  title   = {An Improved {LOD} Specification for {3D} Building Models},
  journal = {Computers, Environment and Urban Systems},
  year    = {2016},
  volume  = {59},
  pages   = {25--37},
  doi     = {10.1016/j.compenvurbsys.2016.04.005},
  url     = {https://doi.org/10.1016/j.compenvurbsys.2016.04.005}
}

@article{ledoux2019cityjson,
  author  = {Ledoux, Hugo and Arroyo Ohori, Ken and Kumar, Kavisha and Dukai, Bal{\'a}zs and Labetski, Anna and Vitalis, Stelios},
  title   = {{CityJSON}: A Compact and Easy-to-Use Encoding of the {CityGML} Data Model},
  journal = {Open Geospatial Data, Software and Standards},
  year    = {2019},
  volume  = {4},
  number  = {1},
  pages   = {4},
  doi     = {10.1186/s40965-019-0064-0},
  url     = {https://doi.org/10.1186/s40965-019-0064-0}
}

@article{gueziec2001cutting,
  author  = {Gu{\'e}ziec, Andr{\'e} and Taubin, Gabriel and Lazarus, Francis and Horn, Bill},
  title   = {Cutting and Stitching: Converting Sets of Polygons to Manifold Surfaces},
  journal = {IEEE Transactions on Visualization and Computer Graphics},
  year    = {2001},
  volume  = {7},
  number  = {2},
  pages   = {136--151},
  doi     = {10.1109/2945.928166},
  url     = {https://doi.org/10.1109/2945.928166}
}

@article{ju2004repair,
  author  = {Ju, Tao},
  title   = {Robust Repair of Polygonal Models},
  journal = {ACM Transactions on Graphics},
  year    = {2004},
  volume  = {23},
  number  = {3},
  pages   = {888--895},
  doi     = {10.1145/1015706.1015815},
  url     = {https://doi.org/10.1145/1015706.1015815}
}

@article{attene2010repair,
  author  = {Attene, Marco},
  title   = {A Lightweight Approach to Repairing Digitized Polygon Meshes},
  journal = {The Visual Computer},
  year    = {2010},
  volume  = {26},
  number  = {11},
  pages   = {1393--1406},
  doi     = {10.1007/s00371-010-0416-3},
  url     = {https://doi.org/10.1007/s00371-010-0416-3}
}

@inproceedings{campen2012meshrepair,
  author    = {Campen, Marcel and Attene, Marco and Kobbelt, Leif},
  title     = {A Practical Guide to Polygon Mesh Repairing},
  booktitle = {Eurographics 2012 -- Tutorials},
  year      = {2012},
  publisher = {The Eurographics Association},
  doi       = {10.2312/conf/EG2012/tutorials/t4},
  url       = {https://doi.org/10.2312/conf/EG2012/tutorials/t4}
}

@article{ledoux2018val3dity,
  author  = {Ledoux, Hugo},
  title   = {val3dity: Validation of {3D GIS} Primitives According to the International Standards},
  journal = {Open Geospatial Data, Software and Standards},
  year    = {2018},
  volume  = {3},
  number  = {1},
  pages   = {1--12},
  doi     = {10.1186/s40965-018-0043-x},
  url     = {https://doi.org/10.1186/s40965-018-0043-x}
}

@article{yue2010geoprovenance,
  author  = {Yue, Peng and Gong, Jianya and Di, Liping},
  title   = {Augmenting Geospatial Data Provenance Through Metadata Tracking in Geospatial Service Chaining},
  journal = {Computers \& Geosciences},
  year    = {2010},
  volume  = {36},
  number  = {3},
  pages   = {270--281},
  doi     = {10.1016/j.cageo.2009.09.002},
  url     = {https://doi.org/10.1016/j.cageo.2009.09.002}
}

@techreport{w3cProvO2013,
  author      = {Lebo, Timothy and Sahoo, Satya and McGuinness, Deborah},
  title       = {{PROV-O}: The {PROV} Ontology},
  institution = {World Wide Web Consortium},
  type        = {W3C Recommendation},
  year        = {2013},
  month       = apr,
  url         = {https://www.w3.org/TR/2013/REC-prov-o-20130430/}
}

@article{zhang2020provenance,
  author  = {Zhang, Mingda and Jiang, Liangcun and Zhao, Jing and Yue, Peng and Zhang, Xuequan},
  title   = {Coupling {OGC WPS} and {W3C PROV} for Provenance-Aware Geoprocessing Workflows},
  journal = {Computers \& Geosciences},
  year    = {2020},
  volume  = {138},
  pages   = {104419},
  doi     = {10.1016/j.cageo.2020.104419},
  url     = {https://doi.org/10.1016/j.cageo.2020.104419}
}

@misc{stac110,
  author = {{SpatioTemporal Asset Catalog Community}},
  title  = {{STAC} Specification},
  year   = {2024},
  note   = {Version 1.1.0, released 11 September 2024},
  url    = {https://github.com/radiantearth/stac-spec/tree/v1.1.0}
}

@article{sandve2013reproducible,
  author  = {Sandve, Geir Kjetil and Nekrutenko, Anton and Taylor, James and Hovig, Eivind},
  title   = {Ten Simple Rules for Reproducible Computational Research},
  journal = {PLOS Computational Biology},
  year    = {2013},
  volume  = {9},
  number  = {10},
  pages   = {e1003285},
  doi     = {10.1371/journal.pcbi.1003285},
  url     = {https://doi.org/10.1371/journal.pcbi.1003285}
}

@inproceedings{derksen2021snerf,
  author    = {Derksen, Dawa and Izzo, Dario},
  title     = {Shadow Neural Radiance Fields for Multi-View Satellite Photogrammetry},
  booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops},
  year      = {2021},
  pages     = {1152--1161},
  url       = {https://openaccess.thecvf.com/content/CVPR2021W/EarthVision/html/Derksen_Shadow_Neural_Radiance_Fields_for_Multi-View_Satellite_Photogrammetry_CVPRW_2021_paper.html}
}

@inproceedings{mari2022satnerf,
  author    = {Mar{\'i}, Roger and Facciolo, Gabriele and Ehret, Thibaud},
  title     = {{Sat-NeRF}: Learning Multi-View Satellite Photogrammetry With Transient Objects and Shadow Modeling Using {RPC} Cameras},
  booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops},
  year      = {2022},
  pages     = {1311--1321},
  url       = {https://openaccess.thecvf.com/content/CVPR2022W/EarthVision/html/Mari_Sat-NeRF_Learning_Multi-View_Satellite_Photogrammetry_With_Transient_Objects_and_Shadow_CVPRW_2022_paper.html}
}

@inproceedings{qian2026sat3dgen,
  author        = {Qian, Ming and Xia, Zimin and Liu, Changkun and Ma, Shuailei and Wang, Wen and Ke, Zeran and Tan, Bin and Zhang, Hang and Xia, Gui-Song},
  title         = {{Sat3DGen}: Comprehensive Street-Level {3D} Scene Generation from Single Satellite Image},
  booktitle     = {The Fourteenth International Conference on Learning Representations},
  year          = {2026},
  url           = {https://openreview.net/forum?id=E7JzkZCofa},
  eprint        = {2605.14984},
  archivePrefix = {arXiv},
  primaryClass  = {cs.CV}
}
```

## 9. 不应声称的边界

1. **不要声称本项目已经运行或集成 CityEngine。** 当前工作区只有 CityEngine/CGA 写作提示，没有 `.cej`、`.cga`、RPK、CityEngine log、export 或 screenshot evidence。
2. **不要把 CityEngine 称为 Sat3DGen accuracy baseline。** 两者输入和目标不同；没有 matched AOI/protocol 就没有 empirical superiority。
3. **不要声称 CityEngine 自动产生 GIS semantic buildings。** 它能保留/驱动 attributes，但 semantic quality 取决于 input schema 和 authored rule。
4. **不要把 CityEngine low/medium/high exporter LOD 自动映射为 CityGML LOD0--3。** 需显式 mapping 和 validation。
5. **不要写“OBJ 在 CityEngine 中不能被 CGA 使用”。** static model 不能应用 CGA，但 OBJ 也能以 shape 导入作为 initial shape。
6. **不要写“CityEngine 完全开源”或“SDK 全部 Apache-2.0”。** examples、PyPRT source 与 PRT runtime/产品条款不同。
7. **不要把 Sat3DGen paper metric 写成本地结果。** 本论文没有按 published benchmark 复现实验。
8. **不要把一份 manifest 等同完整 provenance。** 若缺 input allow-list/hash、model/config/code identity、activity state 和 output hash，它只是部分记录。
9. **不要把 watertightness 等同 geometric accuracy。** repair 可以闭合表面同时引入形状偏差；反之，open mesh 仍可能局部视觉合理。
10. **不要称当前输出 CityGML/CityJSON compliant。** 当前 per-building identity 和 manifest 是向 semantic city asset 迈进的一步，不是标准一致性证明。
11. **不要把 CityEngine 文档中的大规模生成能力转化为本项目 scalability result。** 产品能力与本地 289-tile path 的 exercised evidence 无关。
12. **不要把 CityEngine 比较写成贡献本身。** 如果未执行实验，它是 contextual comparison；实际贡献仍是 ChordAtlas bridge、verification 与 reliability boundary。

## 10. 推荐放入论文的位置

- **Introduction/Background**：增加约 1--1.5 页“Procedural city authoring and CityEngine”以及 0.5--1 页“Semantic city models and LOD”。
- **Methodology**：如果没有运行 CityEngine，只需说明 comparative dimensions 和 why no empirical baseline；不要创造实验步骤。
- **Results**：仅保留实际 ChordAtlas/Sat3DGen/data_builder 证据。CityEngine 只有真正输出并保存 provenance 后才能加入。
- **Discussion**：放三方对照表的精简版，并说明 learned geometry 与 procedural editability 的互补关系。
- **Future work**：提出 per-building OBJ + GIS attributes + authoritative manifest 到 CityEngine shape/static model 的可验证 interchange；可再考虑 CityJSON/CityGML export。
- **References**：优先加入 OGC CityGML、Biljecki LOD、mesh validity/repair、PROV-O/Yue，以及 2--3 篇程序化城市基础文献；避免无关的 broad survey 堆砌。
