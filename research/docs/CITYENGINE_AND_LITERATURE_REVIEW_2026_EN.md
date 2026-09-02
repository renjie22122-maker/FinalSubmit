# CityEngine Comparison and Extended Literature Review Memo (2026)

> 中文版本: [CITYENGINE_AND_LITERATURE_REVIEW_2026.md](CITYENGINE_AND_LITERATURE_REVIEW_2026.md)

Updated: 2026-08-13 (Europe/London)
Purpose: For use when updating the dissertation's Introduction/Background, Discussion, and References.
Evidence status: Conclusions about CityEngine are based on official Esri product documentation, official SDK documentation, and original research papers. No record of a CityEngine experiment was found in the local dissertation workspace; therefore, all CityEngine integration and experiments described below are labelled as "proposed" and must not be presented as "implemented" or "validated".

## 1. Core Positioning That Can Be Used Directly

CityEngine, Sat3DGen, and this project are not three alternative systems that complete the same task. They occupy different positions in the 3D urban-content pipeline:

- **CityEngine** is a procedural city design/generation environment that takes GIS initial shapes, object attributes, assets, and CGA rules as input. Its focus is controllable generation, scenario iteration, parameter editing, and interoperability with GIS and content-production workflows.
- **Sat3DGen** is a learned method that infers a street-scale 3D scene from a single satellite image. Its focus is recovering or generating geometry and appearance from an observation and producing a renderable explicit mesh.
- **The current ChordAtlas integration** does not investigate a new generative model. It investigates how to publish a Sat3DGen-derived mesh, together with coordinates, DSM data, footprints, cache identity, a manifest, and failure state, reliably into a procedural city system.

The following wording is recommended for the dissertation (with minor adjustments to match the final chapter's tone):

> CityEngine and Sat3DGen represent complementary rather than interchangeable urban-modelling paradigms. CityEngine derives controllable polygonal city content from initial shapes, GIS attributes and authored CGA rules, whereas Sat3DGen infers scene geometry and appearance from a single overhead observation using a learned, geometry-first representation. The present work does not claim to replace either paradigm. It investigates the coordinate, provenance, geometry and publication contracts required for satellite-derived meshes to coexist with structured procedural content in ChordAtlas.

CityEngine's official description explicitly presents procedural modelling as its core: roads form blocks, blocks are subdivided into lots, and CGA rules then produce polygonal building models. The input stage can also begin with existing blocks or mass models. See [Introduction to CityEngine](https://doc.arcgis.com/en/cityengine/latest/get-started/get-started-about-cityengine.htm). The formal Sat3DGen abstract describes it as a geometry-first method for generating a street-scale 3D scene from a single satellite image. See [ICLR 2026 / OpenReview](https://openreview.net/forum?id=E7JzkZCofa), [arXiv:2605.14984](https://arxiv.org/abs/2605.14984), and the [official repository](https://github.com/qianmingduowan/Sat3DGen).

## 2. CityEngine: Verifiable Product and Method Facts

### 2.1 Objective and Generation Paradigm

CityEngine's principal abstraction is "initial shape + rule file + attributes -> generated polygonal model". CGA (Computer Generated Architecture) iteratively refines a shape through operations such as `extrude`, `split`, and texture assignment. Rules can be assigned globally or per building, and rule parameters and random seeds can be overridden per object. CityEngine also supports dynamic editing of streets, blocks, and lots, and can regenerate dependent models when the underlying layout changes. These facts support describing it as rule-driven, editable, and suitable for design iteration, but they do not support a claim that it automatically recovers real buildings from pixels.

Authoritative sources:

- [Esri: Introduction to CityEngine](https://doc.arcgis.com/en/cityengine/latest/get-started/get-started-about-cityengine.htm)
- [Esri: Essentials—Rule-based modeling](https://doc.arcgis.com/en/cityengine/latest/tutorials/essentials-rule-based-modeling.htm)
- Müller et al., *Procedural Modeling of Buildings*, DOI [10.1145/1141911.1141931](https://doi.org/10.1145/1141911.1141931)

### 2.2 Inputs, GIS Attributes, and Coordinate Systems

CityEngine can import many GIS and 3D formats, including FileGDB, SHP, OSM, KML, OBJ, glTF, and USD. FileGDB import can retain non-geometric attributes, field data types, domains, and relationship classes. If a CGA attribute has the same name as an imported object attribute, the imported value can drive generation. A new scene can inherit the coordinate system of the first FileGDB layer, and subsequent layers with different CRSs are transformed into the scene CRS. This means that CityEngine can retain richer GIS semantics than a plain OBJ file. However, these semantics are primarily expressed through layers, object attributes, relationships, and reports; they are not automatically equivalent to a complete CityGML urban-object ontology or wall/roof/window semantic decomposition.

Authoritative sources:

- [Esri: Import FileGDB](https://doc.arcgis.com/en/cityengine/latest/help/help-import-fgdb.htm)
- [Esri: Work with GIS data](https://doc.arcgis.com/en/cityengine/latest/tutorials/essentials-work-with-gis-data.htm)
- [Esri: Supported data formats](https://doc.arcgis.com/en/cityengine/latest/help/import-data-types.htm)

### 2.3 Two Import Semantics for Explicit Meshes

This distinction is important to the Sat3DGen comparison; it would be inaccurate to state simply that "CityEngine cannot edit OBJ":

1. **Imported as a static model**: OBJ/glTF and similar geometry is referenced as-is. It can be moved, scaled, and rotated, but CGA cannot modify its geometry, texture, or individual vertices. Moving or renaming the source file breaks the reference.
2. **Imported as a shape**: OBJ/glTF can be used as a CGA initial shape. Faces of an external mass model can be separated into roof and façade components through rules such as `comp(f)` and then refined further.

A dense triangular mesh from Sat3DGen can therefore be used in CityEngine as a visual reference/static model and may also be used as a shape input. However, importability does not imply usable building semantics. If a mesh lacks stable per-building identity, orientation, watertightness, roof/wall decomposition, or reasonable face complexity, CGA rules do not automatically correct those issues.

Authoritative sources:

- [Esri: Work with static models](https://doc.arcgis.com/en/cityengine/latest/help/help-static-model.htm)
- [Esri: Shapes](https://doc.arcgis.com/en/cityengine/latest/help/help-shape-layer.htm)
- [Esri: Import initial shapes tutorial](https://doc.arcgis.com/en/cityengine/latest/tutorials/tutorial-5-import-initial-shapes.htm)

### 2.4 Boundaries Between LOD and Semantic LOD

CityEngine can use CGA attributes to generate low-, medium-, and high-detail model variants, and the DATASMITH exporter can output LODs according to an enum attribute. This LOD is an author-controlled variation in geometric and material complexity. It must not automatically be described as CityGML LOD0--LOD3: LOD in CityGML 3.0 is part of the standard conceptual model's multiple spatial representations and is related to urban-object semantics. Unless a mapping is explicitly established and the output is validated, the dissertation should use "CGA-controlled detail variants", not "CityGML-compliant LoD".

Authoritative sources:

- [Esri: Export DATASMITH—LOD attribute](https://doc.arcgis.com/en/cityengine/latest/help/help-export-unreal.htm)
- [OGC CityGML 3.0 Conceptual Model](https://docs.ogc.org/is/20-010/20-010.html)
- Biljecki, Ledoux & Stoter, *An improved LOD specification for 3D building models*, DOI [10.1016/j.compenvurbsys.2016.04.005](https://doi.org/10.1016/j.compenvurbsys.2016.04.005)

### 2.5 Editing, Interaction, and Automation

CityEngine supports viewport handles, Inspector attributes, local edits, dynamic street/block layouts, scenarios, and CGA reports/dashboards. Its Python API can edit scene data, invoke CGA, automate import and export, and control the UI. PRT/SDK and PyPRT allow external applications to consume rule packages authored in CityEngine. CityEngine's strength is therefore controllable regeneration and design interaction, rather than merely displaying a frozen mesh.

Reproducibility is not automatic. A reproducible experiment should fix at least the following: CityEngine/PRT version, scene CRS, input data and hashes, CGA/RPK, asset versions, rule attributes, random seed, Python script, exporter and settings, and output hashes. Retaining only a `.cej` file or screenshots is insufficient.

Authoritative sources:

- [Esri: Python scripting](https://doc.arcgis.com/en/cityengine/latest/python/cityengine-python-intro.htm)
- [Esri: Script-based export](https://doc.arcgis.com/en/cityengine/latest/python/python-script-based-export.htm)
- [Esri: CityEngine SDK / PRT](https://esri.github.io/cityengine/cityenginesdk)
- [Esri: Dashboards](https://doc.arcgis.com/en/cityengine/latest/help/help-dashboards.htm)

### 2.6 Licensing and the Description "Closed Source"

The most accurate description is more nuanced than simply stating that "CityEngine is entirely closed source":

- CityEngine is a product obtained through an Esri user type/commercial licence; an official trial is available.
- CityEngine SDK/PRT is free for personal, educational, and non-commercial use. Commercial use requires an appropriate commercial licence, and redistribution or provision as a web service requires separate permission.
- Source-code examples in the SDK repository are released under Apache-2.0, but the PRT binary/runtime remains subject to Esri's Terms of Use.

The recommended wording is therefore: **"CityEngine is a commercially licensed proprietary authoring/runtime ecosystem with publicly documented CGA interfaces and some Apache-licensed SDK examples; its runtime is not an unrestricted open-source dependency."** Do not state either that "the entire CityEngine SDK is Apache-2.0" or that "all research use requires purchasing a commercial licence".

Authoritative sources:

- [Esri: CityEngine SDK licensing](https://esri.github.io/cityengine/cityenginesdk)
- [Esri: CityEngine trial](https://www.esri.com/en-us/arcgis/products/arcgis-cityengine/trial)
- [Esri: 2025 user-type licensing migration](https://support.esri.com/en-us/knowledge-base/product-sales-update-arcgis-cityengine-000035055)

## 3. Three-Way Comparison

| Dimension | CityEngine / CGA | Original Sat3DGen method | This project's ChordAtlas bridge |
|---|---|---|---|
| Core question | How can urban proposals be generated, edited, and iterated quickly from GIS shapes, attributes, and rules? | How can street-scale 3D geometry and appearance be generated from one satellite image? | How can a learned mesh be positioned, processed, associated with buildings, and published reliably into a procedural system? |
| Typical input | streets, blocks, lots, footprints, mass models, GIS attributes, terrain, CGA/RPK, assets | A single satellite RGB image for inference; training uses cross-view supervision and the geometric constraints defined in the paper | satellite tiles, Sat3DGen meshes, OSM footprints, DSM, local frame, manifests, selection |
| Generation paradigm | symbolic / rule-based / attribute-driven procedural generation | learned conditional generation, geometry-first feed-forward representation | deterministic orchestration, mesh post-processing, coordinate conversion, transactional publication |
| Main output | polygonal building/street models, layers, attributes/reports, multiple export formats | neural/triplane scene representation, renderings, extracted textured mesh/large-scale patches | corrected scene/building OBJ, MiniMesh, generator graph, manifest/state records |
| GIS semantics | Can retain feature attributes, relationships, and scene CRS; semantics are explicitly defined by inputs and rules | "Semantic diversity" in the paper mainly refers to scene content; an exported triangle mesh is not automatically a GIS ontology | Footprint association supplies coarse per-building identity; this is not yet a complete wall/roof/window semantic model |
| LOD | CGA parameters can generate multiple detail variants for exporter output | extraction resolution/representation scale; not CityGML LOD | No validated semantic LOD hierarchy at present |
| Editing | Strong: attributes, handles, local edits, street/block/scenario regeneration | Weaker than a rule system: generally requires re-inference or editing in a general-purpose mesh tool | selection, per-building loading, and ChordAtlas procedural operations; the learned surface itself remains non-parametric |
| Coordinates | scene CRS and GIS reprojection; non-geographic OBJ/glTF requires explicit placement | Output generated by the original paper does not automatically provide the world-coordinate contract required by this project | Explicit WGS84 origin and X-east/Y-up/Z-south local frame, although the current approximation has a quantified discrepancy from EPSG:27700 |
| Reproducibility | rules/scripts/seeds can be versioned, but product/runtime/version/assets/export settings must be fixed | code is public and the repository is labelled MIT; checkpoints, data, configuration, and environment must still be locked separately | manifest design is a contribution, but the current active merge and some caches still lack input/model/config hashes |
| Licensing | commercial product/restricted runtime; licensing of SDK examples differs from that of the runtime | code repository is labelled MIT; licences for specific data and third-party weights should be checked individually | Terms for ChordAtlas, data, Google/OSM/EA DSM, and every dependency must be reported separately |
| Appropriate evaluation | controllability, edit persistence, semantic attribute retention, scenario/LOD generation, runtime | geometry RMSE, rendering quality, cross-view consistency, generalisation | coordinate error, coverage, topology, cache correctness, failure atomicity, end-to-end evidence |
| Role in this dissertation | Related industrial/research paradigm and potential downstream comparison, not an exercised baseline | upstream prior method, not the student's contribution | Actual object of study and defensible engineering contribution of the dissertation |

### 3.1 Key Interpretations

- "Attribute semantics" in CityEngine and "semantic diversity" in the Sat3DGen paper are not the same concept. The former generally consists of machine-readable GIS/CGA attributes; the latter may refer only to categories of visual content.
- CityEngine's editable rule models and Sat3DGen's observation-conditioned geometry offer different advantages. A procedural model may have clean geometry but depend on footprints, heights, and rules; a learned mesh may retain observed appearance while providing weaker topology and building semantics.
- This project's value lies between the two: it attempts to turn an observation-derived mesh into an asset that is positioned, traceable, and consumable per building, rather than proving that learned generation is superior to procedural generation in every respect.

## 4. Recommended Positioning for CityEngine Integration

If the dissertation only conducts a literature/product comparison, CityEngine should appear in Background and Discussion, not Results. It should be added to Results only if a CityEngine experiment is actually executed.

Recommended hybrid architecture:

```text
                            +--> footprints + GIS attributes --> CityEngine/CGA variants
data_builder / geodata ----+
                            +--> satellite tiles --> Sat3DGen --> cleaned per-building OBJ
                                                               |
                                                               +--> ChordAtlas MiniMesh/BlockGen

shared contract: AOI + CRS/local frame + building ID + source hashes + model/rule version
```

The most feasible interoperability positions are:

1. Use the corrected per-building OBJ files from Sat3DGen as static references in CityEngine, generate a CGA building from the same footprint layer, and conduct a side-by-side analysis.
2. Alternatively, use a simplified/segmented building volume as a CityEngine shape and then use CGA to refine the roof/façade. Before doing so, orientation, components, and topology must be validated.
3. Store the footprint, building ID, height summary, source tile IDs, CRS, model/config hashes, and quality flags as FileGDB/CSV/sidecar attributes rather than exchanging only an OBJ filename.
4. When OBJ/glTF is used for geometry exchange, retain a separate manifest as well. OBJ does not itself carry a complete CRS, lineage, semantic hierarchy, or execution state.

At present, this can be described as a proposed integration path, not completed CityEngine interoperability.

## 5. Minimum Defensible Protocol for an Empirical Comparison

### 5.1 Conditions

Produce at least three result sets for the same small contiguous AOI:

- **CE-FP**: deterministic CGA massing generated by CityEngine from the same footprints and available heights, with a fixed rule and seed.
- **S3G-RAW**: the raw Sat3DGen mesh for the same AOI.
- **S3G-BRIDGE**: output after active crop/bottom/stitch/DSM/building-extraction processing.
- Optional **HYBRID**: a cleaned Sat3DGen mass/shape with CGA façade/roof refinement.

Both of the following fairness settings must be reported:

- **equal-information**: the two paths may use only a predefined set of common inputs;
- **best-available**: CityEngine may use authoritative footprint/height/GIS attributes, while Sat3DGen uses its normal satellite input.

If CityEngine uses ground-truth height while Sat3DGen does not, a lower height error must not be interpreted as evidence that the generation algorithm itself is superior.

### 5.2 Metrics

- Geometry: registered surface/Chamfer distance, height MAE/RMSE, 2D footprint precision/recall/IoU, completeness, and component count.
- Topology: finite vertices, valid indices, degenerate/duplicate faces, boundary edges, non-manifold edges, and watertightness.
- Semantics: proportion with a stable building ID, GIS attribute retention, and roof/wall semantic accuracy (only when manual or authoritative labels are available).
- Complexity and performance: vertices/faces, file size, generation/export time, peak memory, and rerun hash stability.
- Editability: operation count/time after changing a footprint, height, or façade parameter, and whether local changes persist after regeneration.
- Reproducibility: software version, hardware, rule/checkpoint/config hash, seed, CRS, command, input/output hash, and cache decision.

### 5.3 Metrics That Must Not Be Conflated

- Sat3DGen's published 5.20 m RMSE and FID 19 on the VIGOR-OOD/DSM benchmark can be used only as published context, not as results of the local London bridge.
- CityEngine has no inherent quality score directly aligned with Sat3DGen's FID. A rendering metric can be compared only after fixing the renderer, camera path, lighting, resolution, and reference images.
- If statements such as "the model is more attractive", "more realistic", or "easier to edit" do not follow a protocol, they should be reported as qualitative observations, not superiority claims.

## 6. Extended Literature Review: Recommended Structure and Argument

### 6.1 Procedural Cities and Interactive Control

Argument: Rule-based methods support repeated large-scale generation and parameter control, but rule authoring, input semantics, and unique buildings still require human input or external data. Combining learned observation-driven geometry with procedural structure is therefore valuable.

1. Parish & Müller (2001) use an extended L-system to model street patterns and cities. This establishes the background of large-scale procedural city generation but does not recover a city from real satellite pixels. DOI [10.1145/383259.383292](https://doi.org/10.1145/383259.383292).
2. Wonka et al. (2003) generate buildings through split grammars and attribute matching, demonstrating the advantages of grammars for style and scale. DOI [10.1145/882262.882324](https://doi.org/10.1145/882262.882324).
3. Müller et al. (2006) introduce CGA shape grammar. This is the most direct academic foundation for CityEngine/CGA and is already present in the bibliography. DOI [10.1145/1141911.1141931](https://doi.org/10.1145/1141911.1141931).
4. Chen et al. (2008) use tensor fields and interactive operations to model street networks, supporting the combination of "procedural + interactive control". DOI [10.1145/1360612.1360702](https://doi.org/10.1145/1360612.1360702).
5. Lipp et al. (2011) discuss procedural content layers, valid layouts, and edit persistence, directly addressing editability in which user customisations remain after modification. DOI [10.1111/j.1467-8659.2011.01865.x](https://doi.org/10.1111/j.1467-8659.2011.01865.x).
6. Kelly & Wonka (2011) present ProceX, which uses interactive procedural extrusion to generate editable two-manifold architecture. This work is already present in the bibliography. DOI [10.1145/1944846.1944854](https://doi.org/10.1145/1944846.1944854).
7. Vanegas et al. (2012) address block-to-parcel subdivision and editing persistence, providing methodological background for lot generation in a CityEngine pipeline. DOI [10.1111/j.1467-8659.2012.03047.x](https://doi.org/10.1111/j.1467-8659.2012.03047.x).
8. Smelik et al. (2014) survey procedural virtual worlds with an emphasis on control and interactivity. It can be used to focus, rather than broaden, the review. DOI [10.1111/cgf.12276](https://doi.org/10.1111/cgf.12276).

### 6.2 Satellite/Cross-View Methods to 3D

Argument: Multi-view satellite photogrammetry targets observation-constrained reconstruction, whereas Sat2Density/Sat2Scene/Sat2City/Sat3DGen investigate satellite-conditioned generation. Their inputs, supervision, representations, and evaluation protocols differ; they must not be arranged only by publication year or compared through direct transfer of metrics.

1. Derksen & Izzo (2021), S-NeRF: uses multiple high-resolution satellite images and known viewpoints and handles sun/shadow effects. This is multi-view reconstruction, not single-image generation. [CVPRW paper](https://openaccess.thecvf.com/content/CVPR2021W/EarthVision/html/Derksen_Shadow_Neural_Radiance_Fields_for_Multi-View_Satellite_Photogrammetry_CVPRW_2021_paper.html).
2. Marí et al. (2022), Sat-NeRF: introduces RPC cameras, transient objects, and shadow modelling into multi-view satellite photogrammetry. [CVPRW paper](https://openaccess.thecvf.com/content/CVPR2022W/EarthVision/html/Mari_Sat-NeRF_Learning_Multi-View_Satellite_Photogrammetry_With_Transient_Objects_and_Shadow_CVPRW_2022_paper.html).
3. Qian et al. (2023), Sat2Density: learns a density field from satellite-ground pairs without depth supervision and is already present in the bibliography. [ICCV paper](https://openaccess.thecvf.com/content/ICCV2023/html/Qian_Sat2Density_Faithful_Density_Learning_from_Satellite-Ground_Image_Pairs_ICCV_2023_paper.html).
4. Li et al. (2024), Sat2Scene: generates point-level appearance on given geometry and uses a neural renderer to produce arbitrary views. Its task is not identical to Sat3DGen's explicit-mesh task, and it is already present in the bibliography. [CVPR paper](https://openaccess.thecvf.com/content/CVPR2024/html/Li_Sat2Scene_3D_Urban_Scene_Generation_from_Satellite_Images_with_Diffusion_CVPR_2024_paper.html).
5. Hua et al. (2025), Sat2City: generates 3D cities on a synthetic city dataset using sparse voxels and cascaded latent diffusion. The dataset domain must be distinguished explicitly from London/VIGOR; this work is already present in the bibliography. [ICCV paper](https://openaccess.thecvf.com/content/ICCV2025/html/Hua_Sat2City_3D_City_Generation_from_A_Single_Satellite_Image_with_ICCV_2025_paper.html).
6. Qian et al. (2026), Sat3DGen: a geometry-first single-image method and the upstream method for this project. Claims in the paper must be separated from evidence produced by the local bridge. [OpenReview](https://openreview.net/forum?id=E7JzkZCofa).

### 6.3 Semantic City Models and LOD

Argument: A renderable triangle mesh is not necessarily a semantic city model. Building identity, boundary surfaces, CRS, LOD, and relationships form a separate data contract. This distinction directly supports the motivation for progressing from OBJ files to per-building publication and manifests in this project.

1. OGC CityGML 3.0 defines georeferenced 3D geometry + semantics + relationships and supports LOD0--3. It is a conceptual reference; do not claim that the current output is compliant. [OGC 20-010](https://docs.ogc.org/is/20-010/20-010.html).
2. Biljecki et al. (2015) survey applications of 3D city models and can support discussion of downstream requirements for simulation, planning, and related uses. DOI [10.3390/ijgi4042842](https://doi.org/10.3390/ijgi4042842).
3. Biljecki, Ledoux & Stoter (2016) show that LOD cannot be reduced to "greater detail means higher LOD"; geometric complexity and semantic granularity must be distinguished. DOI [10.1016/j.compenvurbsys.2016.04.005](https://doi.org/10.1016/j.compenvurbsys.2016.04.005).
4. Ledoux et al. (2019) present CityJSON, showing how a relatively accessible JSON encoding can retain the CityGML data model. This is a direction for future manifest/semantic export, not a format implemented by the current work. DOI [10.1186/s40965-019-0064-0](https://doi.org/10.1186/s40965-019-0064-0).

### 6.4 Mesh Repair, Topology, and 3D GIS Validity

Argument: Downstream algorithms have different requirements for manifoldness, closedness, valid indices, and orientation, and "repair" can itself change geometry. The dissertation should position its current tests as contract checks; a watertight mesh produced by a tool should not be assumed to be more accurate.

1. Guéziec et al. (2001) address cutting and stitching of non-manifold polygon sets and are suitable for citation in relation to topology-aware stitching. DOI [10.1109/2945.928166](https://doi.org/10.1109/2945.928166).
2. Ju (2004) constructs an inside/outside volume from arbitrary polygon soup and reconstructs a closed surface. The method is robust, but volumetric recontouring changes the original geometry. DOI [10.1145/1015706.1015815](https://doi.org/10.1145/1015706.1015815).
3. Attene (2010) favours local modification of defective neighbourhoods and is suitable for explaining the trade-off between local repair and global resampling. DOI [10.1007/s00371-010-0416-3](https://doi.org/10.1007/s00371-010-0416-3).
4. Campen, Attene & Kobbelt (2012) provide a systematic treatment of mesh defects, application-specific requirements, and repair trade-offs. DOI [10.2312/conf/EG2012/tutorials/t4](https://doi.org/10.2312/conf/EG2012/tutorials/t4).
5. Ledoux's (2018) val3dity validates 3D GIS primitives according to ISO 19107, directly connecting mesh topology with semantic city-data validity. DOI [10.1186/s40965-018-0043-x](https://doi.org/10.1186/s40965-018-0043-x).

### 6.5 Geospatial Provenance and Reproducibility

Argument: A manifest is a carrier, not a sufficient condition for provenance. Effective lineage must identify entities, activities, agents/implementations, inputs/outputs, plans/configurations, and temporal relationships. Cataloguing assets is also not the same as recording execution provenance.

1. Yue, Gong & Di (2010) connect metadata tracking and geoprocessing service chains with the derivation of geospatial products, closely matching the current multi-stage bridge. DOI [10.1016/j.cageo.2009.09.002](https://doi.org/10.1016/j.cageo.2009.09.002).
2. W3C PROV-O provides an exchangeable provenance conceptual model based on Entity, Activity, Agent, and related concepts. [W3C Recommendation](https://www.w3.org/TR/2013/REC-prov-o-20130430/).
3. Zhang et al. (2020) combine OGC WPS plans/executions with W3C PROV and emphasise construction, execution, and provenance as three stages. DOI [10.1016/j.cageo.2020.104419](https://doi.org/10.1016/j.cageo.2020.104419).
4. STAC 1.1.0 can describe and discover spatiotemporal assets, links, and roles. It is suitable as a catalogue layer for imagery/DSM/meshes, but it cannot replace workflow provenance for checkpoints/configurations/commands/exit states. [STAC 1.1.0](https://github.com/radiantearth/stac-spec/tree/v1.1.0).
5. Sandve et al. (2013) support reproducible-computation principles such as recording every step, retaining raw data, and recording software versions and random seeds. DOI [10.1371/journal.pcbi.1003285](https://doi.org/10.1371/journal.pcbi.1003285).

## 7. Recommended Priorities

### Must Add or Update

- Official Esri documentation covering the CityEngine introduction, FileGDB attributes, static models/shapes, and SDK licensing.
- At least one of Parish & Müller (2001) or Wonka et al. (2003), to establish the methodological lineage before Müller (2006).
- OGC CityGML 3.0 + Biljecki et al. (2016) on LOD, to distinguish mesh geometry, GIS semantics, and LOD rigorously.
- Ju (2004) or Attene (2010) + Ledoux (2018), to support mesh repair/validity.
- W3C PROV-O + Yue et al. (2010), to support the distinction between a manifest and provenance.
- Correct the Sat3DGen BibTeX: ICLR/OpenReview is the venue; arXiv should appear only in `eprint`, and the arXiv DOI must not be presented as an ICLR DOI.
- Update the Sat2Density++ volume, issue, and pages: IEEE TPAMI 48(5), 5692--5709, DOI `10.1109/TPAMI.2026.3652860` (the authors' code repository also lists this metadata).

### Add If Space Permits

- Chen et al. (2008), Lipp et al. (2011), and Vanegas et al. (2012): interactive street/layout/parcel methods.
- Sat-NeRF: to distinguish multi-view satellite reconstruction from single-image generation.
- CityJSON: future semantic interchange.
- Campen et al. (2012): mesh-repair survey.
- Zhang et al. (2020) and STAC: future interoperable provenance/catalogue design.

## 8. Suggested BibTeX (Metadata Verified)

The following entries can be copied into `references.bib` and then adapted to the existing key style. Do not add all of them at once unless they are cited in the text.

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

## 9. Boundaries on Claims

1. **Do not claim that this project has run or integrated CityEngine.** The current workspace contains only writing notes about CityEngine/CGA and no `.cej`, `.cga`, RPK, CityEngine log, export, or screenshot evidence.
2. **Do not present CityEngine as an accuracy baseline for Sat3DGen.** Their inputs and objectives differ; without a matched AOI and protocol, no empirical superiority can be established.
3. **Do not claim that CityEngine automatically produces GIS-semantic buildings.** It can retain and use attributes to drive generation, but semantic quality depends on the input schema and authored rules.
4. **Do not automatically map CityEngine low/medium/high exporter LODs to CityGML LOD0--3.** Explicit mapping and validation are required.
5. **Do not write that "OBJ cannot be used by CGA in CityEngine".** A static model cannot have CGA applied to it, but an OBJ can also be imported as a shape and used as an initial shape.
6. **Do not state that "CityEngine is entirely open source" or that "the entire SDK is Apache-2.0".** The examples and PyPRT source are licensed differently from the PRT runtime/product.
7. **Do not report a Sat3DGen paper metric as a local result.** This dissertation did not reproduce the published benchmark experiment.
8. **Do not equate a manifest with complete provenance.** If it lacks an input allow-list/hash, model/config/code identity, activity state, and output hash, it is only a partial record.
9. **Do not equate watertightness with geometric accuracy.** Repair can close a surface while introducing shape error; conversely, an open mesh can still be locally visually plausible.
10. **Do not describe the current output as CityGML/CityJSON compliant.** The current per-building identity and manifest are a step towards a semantic city asset, not proof of standards compliance.
11. **Do not turn CityEngine's documented large-scale generation capability into a scalability result for this project.** Product capability is unrelated to the exercised evidence from the local 289-tile path.
12. **Do not present the CityEngine comparison itself as a contribution.** If no experiment is executed, it is contextual comparison; the actual contribution remains the ChordAtlas bridge, its verification, and its reliability boundary.

## 10. Recommended Placement in the Dissertation

- **Introduction/Background**: Add approximately 1--1.5 pages on "Procedural city authoring and CityEngine" and 0.5--1 page on "Semantic city models and LOD".
- **Methodology**: If CityEngine has not been run, explain only the comparative dimensions and why there is no empirical baseline; do not invent experimental steps.
- **Results**: Retain only evidence actually produced by ChordAtlas/Sat3DGen/data_builder. CityEngine can be included only after output has actually been generated and its provenance retained.
- **Discussion**: Include a concise version of the three-way comparison and explain the complementary relationship between learned geometry and procedural editability.
- **Future work**: Propose a verifiable interchange from per-building OBJ + GIS attributes + authoritative manifest to a CityEngine shape/static model; CityJSON/CityGML export can then be considered.
- **References**: Prioritise OGC CityGML, Biljecki on LOD, mesh validity/repair, PROV-O/Yue, and 2--3 foundational procedural-city papers; avoid accumulating unrelated broad surveys.
