# Evidence ledger

This file records the evidence used to write the dissertation.  It is an
internal research artefact, not part of the submitted thesis.  Claims are
classified as **implemented**, **exercised**, **validated**, **planned**, or
**unresolved**.  A generated file proves that an artefact exists; it does not,
by itself, prove the exact command, model, configuration, or authorship that
produced it.

## Repository authority

- `E:\UCL\Project\chordatlas` is the baseline Git repository (`stable`,
  commit `1e6f5cf174e30f7bd2e5a374ad70ced75fe75fd9`, 1 November 2024).
- `E:\UCL\Project\myProject` is a non-Git copy of ChordAtlas with a bridge,
  additional Java classes, tests, workspaces, logs, and outputs.  Personal
  authorship and chronology are unresolved and must remain a thesis TODO.
- `E:\UCL\Project\data_builder\data_builder` is the authoritative nested Git
  repository.  The outer directory is a byte-identical working-copy duplicate.
- The active Sat3DGen implementation for `test_mesh_pipeline_merge.py` resolves
  exclusively to `E:\UCL\Project\Sat3DGen\mesh_pipeline\*.py`.  The nested
  `mesh_generate_merge_pipeline` directory is excluded from implementation
  evidence.

## Research framing

Recommended research question:

> To what extent can a manifest-driven, coordinate-explicit workflow integrate
> satellite-derived urban meshes and geospatial context into ChordAtlas through
> static and on-demand execution, and what reliability limits are revealed by
> its recorded artefacts, tests, and failure cases?

The defensible contribution is an engineering integration and evidence-led
reliability audit.  There is no evidence for a new generative model, improved
reconstruction accuracy, or Level 4--6 methodological contribution.

## Data-builder evidence

| Item | Measured value | Status/qualification |
|---|---:|---|
| Input highway points | 2,343 | Producer absent; filename says 500x500 but feature bbox is about 1,220.8 x 889.7 m |
| Matched panoramas | 2,333 | 99.57% of input points |
| Legacy split | 1,633 / 350 / 350 | Latitude-sorted train/validation/test; no buffer |
| Legacy panoramas | 2,333 at 640x320 | Original Google requests; returned pano IDs/locations not recorded |
| Sparse satellite tiles | 127 at 640x640 | Original acquisition branch |
| Filled grid | 272 = 16x17 | Cartesian repair of observed centres |
| 50% overlap grid | 990 = 30x33 | About 50.02% after filename rounding |
| 10% overlap grid | 306 = 17x18 | Matches 306 batch OBJ meshes one-for-one |
| Equirectangular files / unique hashes | 2,333 / 1,205 | 1,555 files lie in duplicate-hash groups; largest group 26 |
| Exact train--validation leakage | 7 hashes | Affects 20 training and 11 validation rows |
| Exact validation--test leakage | 3 hashes | Affects 4 validation and 3 test rows |
| Four-candidate labels outside +-320 px | 6,987 / 9,332 (74.87%) | Maximum absolute offset 948.7 px; no query has four containing tiles |
| DSM rasters | four 5,000x5,000 1 m tiles | EPSG:27700, Environment Agency Composite 2022 |
| DSM patch validity | 113/127 full; 14 partial | Minimum valid ratio 0.56875; single-raster selection does not mosaic seams |
| OSM support-node records | 57,623 / 67,281 | Recursive support nodes contaminate masks; counts are category-duplicated |

The old `sat_depth_dsm` channel has no surviving producer and metadata says all
127 centres are outside its extent.  It is not evidence for metric depth.
Per-patch DSM p5--p95 normalisation removes absolute elevation comparability.

## Active Sat3DGen mesh experiment

The current experiment selects all recursive OBJ files in
`pipeline_output/meshes`; it does not use a planned allow-list.  Fifteen inputs
form two disjoint clusters (9 east, 6 west) whose centres are 666.438 m apart.
`final_v36` has no manifest, hashes, model record, configuration sidecar, or run
log.  Acquisition and inference are implemented but not exercised by this
merge experiment.

| Stage | Vertices | Faces |
|---|---:|---:|
| Raw 15 OBJ files | 3,640,214 | 7,281,608 |
| Cropped | 2,828,613 | 5,615,039 |
| Bottom removed, before stitch | 2,252,373 | 4,474,289 |
| Stitched scene | 1,915,455 | 4,097,374 |
| DSM-corrected scene | 1,915,455 | 4,097,374 |
| DSM building extraction | 1,001,507 | 2,116,136 |
| No-DSM building extraction | 907,370 | 1,911,272 |

Each input lost exactly 38,416 vertices and 76,050 faces in bottom removal.
Across the stitched scene, all 1,915,455 vertex Y values changed positively
after DSM correction: median +36.081838 m, mean +42.236210 m, range +15.811809
to +62.634977 m.  This contradicts documentation that only upper surfaces are
changed.

Confirmed code defects/limits:

- `Pipeline.run` unpacks two values although `extract_building_mesh` returns four.
- Bottom removal uses a face minimum, so one bottom-near vertex is sufficient.
- Stitching uses X/Z only; `max_y_diff_for_merge` is unused and non-adjacent
  tiles are also considered.
- Merged vertices inherit the earlier position/colour rather than averaging.
- The OBJ parser mishandles negative indices and n-gons and lacks finite/index
  validation.
- Output writes are non-atomic; incomplete version directories exist.
- Watertightness and general topological validity were not established by the
  original experiment.

## Static ChordAtlas bridge

`projects/data_builder_london_smoke` is structurally valid and contains a
201-model MiniMesh.  Its mesh has 697,366 vertices and 1,490,722 faces.  The
target is about 999,545.956 m2, but mesh/AOI overlap is only 9,553.822 m2
(0.9558%).  The manifest records no materials.  Six desired satellite and mesh
files already existed; this run demonstrates cached conversion, not live
acquisition or inference.

The full-region manifest is planning-only: 289 desired tiles, 272 satellite
sources, 17 missing satellite tiles, and all 289 meshes missing at planning
time.

## On-demand bridge

Recorded outcomes include:

- duplicate-footprint request rejected before work;
- a 12-tile attempt whose satellite downloads all failed with an SSL ASN.1
  error, correctly stopping before inference;
- a READY 30-tile/three-building publication with corrected-scene cache reuse;
- a READY 16-tile/one-building publication where satellite files were present,
  all 16 meshes were initially missing, and no inference failure was recorded;
- all inspected published buildings are `COARSE_READY`, because strict model
  completeness is disabled.

Six historical Windows `AccessDeniedException` failures occurred while moving a
whole-selection `minimesh.part-*` directory.  Five partial trees remain.  Later
per-building conversions completed and reached BlockGen creation.  Closing the
writer and reducing the move size plausibly mitigate the failure, but no
controlled ablation identifies the root cause and the move helper still lacks
an AccessDenied retry/copy fallback.

Python publication reaches READY before Java creates and hot-loads the
per-building MiniMesh.  The overall operation is therefore not one atomic
transaction.

## Re-executed validations (10 August 2026)

- `bridge/scripts/test.ps1`: 64 Python unit tests passed in 1.168 s.  Scope:
  configuration, footprints, DSM prerequisites, OBJ inspection, exact-manifest
  commands, failure preservation, cache behaviour, tile planning, PNG CRC, and
  per-building publication.  It is not an end-to-end/model-quality/GUI test.
- `MiniTransformValidation`: passed using the shaded project JAR; a temporary
  tetrahedron was converted and `minimesh.part` moved to `minimesh` on Windows.
- `SelectedBlockMeshServiceValidation`: passed; stable selection identity and
  READY/publication contract fixtures were checked.
- `ObjSliceValidation`: passed; a degenerate face was ignored while a valid
  triangle remained sliceable.

These Java programs are standalone validation executables and are not invoked
by `test.ps1`.  Their passing synthetic checks do not prove the historical
AccessDenied defect is eliminated in full conversions.

## Claims prohibited by the evidence

- complete end-to-end reconstruction of the nominal 1 km2 region;
- a contiguous interpretation of `final_v36`;
- faithful four-containing-tile VIGOR labels or leakage-free validation;
- validated OSM semantic masks or metric DSM/model depth;
- height-aware/watertight stitching or upper-surface-only DSM correction;
- a fully atomic publication or conclusive AccessDenied fix;
- improved accuracy, speed, robustness, scalability, or generalisation;
- personal authorship of changed files until chronology and contributions are
  confirmed by the student/supervisors.

## Unresolved submission facts

- Student name, candidate number, programme title, supervisors, submission
  date, and formal word limit.
- Personal authorship and project chronology.
- Confirmed licences/redistribution permissions for Google imagery.
- Whether a controlled GUI smoke test was completed and documented.
- Whether one provenance-locked inference can be rerun before submission.
