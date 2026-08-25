# Updated `myProject` audit (13 August 2026)

## Scope and evidence rules

This is a read-only refresh of `E:\UCL\Project\myProject`, focused on the
selection bridge and its Java GUI boundary. It does **not** infer authorship or
chronology from file modification times. The directory is not a Git worktree,
so an exact VCS classification such as “9 modified / 8 added” cannot be
independently established from this checkout. Such a classification must be
cited to a separate before/after inventory or commit history; it must not be
presented as a conclusion of this audit.

Status vocabulary used below:

- **implemented**: present in current source;
- **exercised**: a current artefact or log shows that the path ran;
- **validated**: an explicit automated check passed;
- **unresolved**: the available evidence does not establish the claim.

The audit re-ran all bridge tests with `python -B -m unittest discover -s tests
-p 'test_*.py' -v`: **73/73 passed** in 1.707 s. The two focus modules comprise
**31/31 passing tests** (21 in `test_selection.py`, 10 in
`test_building_split.py`). These are unit/contract tests, not a reconstruction-
accuracy or GUI-appearance benchmark.

## Correct publication denominators

Two populations answer different questions and must not be mixed:

1. The workspace retains **10 root `result.json` files whose root status is
   `READY`** under `projects/data_builder_london_on_demand/generated_blocks`.
   Nine use a per-building metric schema (`per-footprint-v1` or
   `per-footprint-v2`) and contain **17 building entries**. The remaining legacy
   root predates a per-building publication and contains no building entries.
2. The current contract subset consists of **4 `per-footprint-v2` root
   publications**, made on 13 August, containing **8 building entries**. This is
   the appropriate denominator for claims about v2 face ownership, ground
   clipping/capping, component metrics, RGB OBJ and current Java loading.

Thus “10 retained READY roots / 9 metric-schema roots / 17 buildings” is an
archive-population statement; “4 current v2 roots / 8 v2 buildings” is the
current-method statement. It is incorrect to call all 17 buildings v2.

### Current `per-footprint-v2` roots

| stable ID | tiles = selected meshes | corrected scene reused | requested / published | completeness mode |
|---|---:|---:|---:|---|
| `ae59f5189867d3246dc7` | 16 = 16 | true | 1 / 1 | `coarse_bbox` |
| `be4b2e248b857bc6f4ff` | 20 = 20 | true | 1 / 1 | `coarse_bbox` |
| `789c85bcafa271b24a5c` | 36 = 36 | false | 4 / 4 | `coarse_bbox` |
| `de948cdb29aa69a872d7` | 9 = 9 | true | 2 / 2 | `coarse_bbox` |

All four root results are `READY`, use `osm-prealign-v1`, record DSM
`status=APPLIED`, and report source coverage and mesh-vertex coverage of 1.0.
However, strict projected completeness is disabled in all four. Consequently,
**8/8 buildings are `COARSE_READY`; 0/8 are building-level `READY`**. Root
`READY` is a publication/commit condition, not a model-completeness or accuracy
grade.

Evidence paths:

- `projects/data_builder_london_on_demand/generated_blocks/<stable-id>/result.json`
- `.../buildings/index.json`
- `.../buildings/<footprint-id>/building.json`
- corresponding `_selection_jobs/<stable-id>/tile_manifest.json` and
  `top_level_pipeline_manifest.json`

## Exact allowlist and guarded top-level Sat3DGen invocation

**Status: implemented, exercised, validated.**

`bridge/src/myproject/selection.py:563-684` builds a deterministic, globally
anchored Web-Mercator grid against post-crop effective mesh bounds and clamps
context padding to at least 30 m. `selection.py:2228-2253` writes the exact
tile manifest before network/GPU work. `selection.py:968-1072` passes it to the
top-level driver with `tile_source="exact_manifest"`, `allow_partial=False`,
mandatory OSM prealignment and mandatory DSM. `selection.py:2437-2454` refuses
publication unless selected mesh count exactly equals planned tile count and
the missing list is empty.

`bridge/src/myproject/top_level_mesh_driver.py:545-569` constructs an allowlist
from that manifest and explicitly records/ignores unrelated or stale OBJ files.
The four current v2 publications exercise exact sets of 16, 20, 36 and 9
meshes, respectively (**81 selected meshes for 81 planned tiles**). Tests verify
global anchoring, effective post-crop coverage, partial-set rejection, exclusion
of stale same-name and extra OBJ files, command construction, audited stage
order, cached operation without a Gradio connection, and failure preservation.

This establishes exact input selection and fail-closed publication. It does not
establish geometric accuracy.

## Per-footprint v2 extraction

**Status: implemented, exercised, contract-validated; geometric fidelity
unresolved.**

`selection.py:1577-1660` assigns each triangle to at most one requested
footprint using positive-area projected intersection evidence and a stable tie
break, rather than the older `any(vertex)` rule or whole-scene fallback.
`selection.py:1663-1725` removes degenerate and same-winding geometric duplicate
triangles while deliberately retaining reverse windings. The 8 current v2
buildings contain **1,049,601 vertices and 2,141,596 faces** after preparation;
**10,957 same-winding duplicate faces** were removed. Tests validate unique,
deterministic ownership, boundary-crossing retention, point-contact rejection,
geometric duplicate handling and preservation of opposite winding.

Each published building has its own `cropped.obj`, `gis.obj`,
`gis_footprints.obj`, `building.json`, MiniMesh directory and identity. The
Python publisher writes `buildings/index.json`; root `result.json` is installed
last with rollback logic (`selection.py:2125-2225`). Java checks result/index
identity, exact footprint membership, supported version, summary counts,
contained paths, per-building metadata equality and usable OBJ files
(`SelectedBlockMeshService.java:386-575`). The standalone publication validator
passed against a real v2 root.

Claim limit: per-footprint identity is not roof/wall/window semantics, and
unique face ownership does not prove that every assigned face belongs to the
intended real building.

## Ground clipping and capping

**Status: implemented, unit-validated, partly exercised.**

`selection.py:1728-1808` clips triangles exactly at the local ground plane,
interpolating every stored vertex attribute, including RGB. `selection.py:
1870-1977` caps only simple, non-nested boundary loops lying on that plane;
complex, overlarge, non-cycle, self-intersecting, nested or untriangulable loops
are rejected rather than closed indiscriminately. A unit test verifies RGB
interpolation; another verifies that a ground boundary is capped while a roof
hole is not.

Across the 8 current v2 buildings:

- **503 input faces intersected the ground plane** (501 in
  `footprint-c2b8278e5a15`, 2 in `footprint-be4b2e248b85`);
- **0 faces were wholly discarded below ground**;
- **40 ground-loop candidates** were found, **9 capped**, **31 rejected**;
- **45 cap triangles** were added;
- all observed caps occur in `footprint-c2b8278e5a15`.

Therefore the operations are not merely dormant code. But only one building
exercised capping, most buildings exercised the no-intersection/no-loop branch,
and the large rejected-loop count prevents a claim that ground sealing produces
closed solids generally.

## Component filtering

**Status: implemented and unit-validated; configurable dropping not exercised
by current publications.**

`selection.py:2009-2086` computes deterministic shared-edge face components and
supports minimum face and relative-size thresholds. The v2 defaults are one
face and ratio zero, intentionally retaining every owned component. Across the
8 v2 buildings, **292 raw components were found, 292 retained, 0 dropped and 0
faces dropped**. A synthetic test proves that stricter thresholds can drop a
one-face fragment, while another proves that defaults preserve disconnected
owned panels.

The report should say the component filter is available and its metrics were
exercised, but not claim that real-data fragment removal improved these eight
models.

## GLB -> RGB OBJ -> Java vertex-colour display

**Status: implemented; conversion unit-validated; RGB OBJ and Java parsing
exercised/validated; origin and visual accuracy unresolved.**

`top_level_mesh_driver.py:210-270` converts a returned GLB into triangular OBJ,
retaining vertex RGB when present (otherwise assigning neutral grey), and
`top_level_mesh_driver.py:312-356` uses the returned readable OBJ first, then a
returned GLB, with atomic destination replacement. The test
`test_unique_gradio_glb_converts_to_vertex_colour_obj` passed on a synthetic
GLB. Current job logs do not explicitly record a GLB conversion, so it is not
safe to claim that the 8 real publications necessarily traversed the GLB
fallback rather than starting from RGB OBJ.

All 8 v2 `cropped.obj` files have `v x y z r g b` records. All 8 have a valid
per-building MiniMesh index and collectively **371 `model.obj` tiles**. The new
Java `VertexColorObjGen` parser accepts RGB in 0--1 or 0--255 form, triangulates
n-gons, supports negative indices, rejects non-finite/out-of-range data,
generates normals and uses 32-bit indices. It renders through jMonkeyEngine's
`Unshaded.j3md` with `VertexColor=true`. On a real published OBJ,
`VertexColorObjGenValidation` passed with **790 vertices / 1,351 triangles**;
its synthetic validation also passed.

`SelectedBlockMeshService.java:411-445` parses colour OBJ on its worker thread
and falls back to semantic `BlockGen` on failure. `GISGen.java:584-629`
constructs all MiniGen/BlockGen/colour layers before exposing them, makes colour
visible by default when available, and rolls back all added layers if final GUI
validation fails. The GUI log contains read events for all 8 current building
OBJs and no `Vertex-colour display unavailable` message. It also contains
warnings that the ordinary OBJ loader sees no normals; those warnings do not
apply to `VertexColorObjGen`, which generates normals, but should be retained as
GUI-log evidence rather than hidden.

No screenshot, framebuffer capture or colour-reference comparison establishes
that the displayed colours are visually correct. Vertex RGB demonstrates
appearance availability, not appearance accuracy.

## GUI and Java publication boundary

**Status: implemented and exercised at file/load level; globally atomic GUI
publication unresolved.**

`GISGen.java:517-529` exposes the opt-in “satellite mesh on Select” control and
status label. `GISGen.java:531-654` prevents duplicate loads, delegates work off
the Swing event-dispatch thread, validates and constructs every independent
building before exposing layers, selects the colour layer when present and
removes already-added layers on a final-load exception. `SelectedBlockMeshService
.java:250-347` serialises one job at a time on a daemon worker, invokes the bridge
with `conda run ... build-selection --execute`, logs output, validates `READY`
and returns callbacks on the EDT.

The current GUI log is **514,353,332 bytes** and includes historical failures as
well as later successful OBJ reads, so raw error counts from the whole file are
not a current failure rate. It shows all 8 v2 building OBJs being read. A tail
inspection still found `Unable to delete ... scratch/...log`, an operational
cleanup warning that remains unresolved. Earlier repeated bridge failures for
some stable IDs coexist with later current `READY` results and must not be
reported as if the final publications failed.

Python root `READY` is committed before Java creates/validates per-building
MiniMesh and attaches GUI layers. Python publication is rollback-protected, and
Java GUI attachment has local rollback, but the cross-language process is not
one atomic transaction.

## Corrections required in the old report

1. Replace “64 Python tests” with **73 Python unit/contract tests passed** on 13
   August 2026. Preserve the earlier 64-test result only when explicitly dated
   as historical.
2. Replace any statement that on-demand output is a single combined BlockGen
   with the current `per-footprint-v2` publication: separate OBJ/GIS/metadata,
   MiniMesh and semantic BlockGen per requested footprint, with an optional
   visible vertex-colour layer.
3. Replace the old `any(vertex)`/whole-scene-fallback description for the
   current bridge with unique projected-overlap ownership, geometric
   same-winding deduplication, exact ground clipping and selective ground caps.
   Keep the older behaviour only as a comparison to the upstream/historical
   extractor.
4. Add exact-manifest evidence: 4 current v2 jobs planned and selected exactly
   **81/81** mesh tiles, with mandatory OSM prealignment and full recorded DSM
   source/vertex coverage.
5. Report both denominators: **10 retained root READY publications / 9 with a
   per-building metric schema / 17 building entries**, versus **4 current v2
   roots / 8 current v2 buildings**.
6. Do not call the current 8 buildings complete. Report **0/8 strict READY and
   8/8 COARSE_READY**, because `strict_model_completeness=false` and extraction
   uses `coarse_bbox`.
7. Add the current topology: **8/8 not watertight**; every building has boundary
   edges and **7/8** have at least one non-manifold edge. The meshes may be useful
   coarse visual assets, but the evidence does not support solid/topologically
   valid models.
8. Describe component filtering precisely: available and tested, but current
   defaults retained **292/292 components** and removed none.
9. Add the ground-operation denominator: 503 intersected faces, 9/40 candidate
   loops capped and 45 faces added; do not generalise from the one capped
   building.
10. Describe vertex colour as an implemented, file-validated appearance path.
    Do not state that all real jobs came from GLB, or that colour fidelity was
    evaluated.
11. Do not describe Python `READY` plus Java hot-load as a single atomic
    transaction.
12. Do not use modification time to assert authorship, contribution or an exact
    “modified/added” classification. The checkout has no `.git` authority.

## Compact evidence table

| Claim | Status | Exact evidence | Limitation |
|---|---|---|---|
| deterministic exact tile allowlist | implemented / exercised / validated | 81 planned = 81 selected across 4 v2 roots; allowlist tests pass | not accuracy evidence |
| per-footprint v2 publication | implemented / exercised / validated | 4 roots, 8 building dirs, Python + Java contract validators | all are coarse |
| unique ownership and dedup | implemented / exercised / validated | 10,957 duplicate faces removed; ownership tests pass | semantic ownership unmeasured |
| ground clipping | implemented / exercised / validated | 503 intersections; RGB interpolation unit test | 0 wholly discarded in this sample |
| selective ground caps | implemented / exercised / validated | 9/40 loops, 45 faces, one building | 31 candidates rejected; no watertight result |
| component filtering | implemented / exercised / validated | 292 components measured | 292 retained; no real-data drop |
| vertex-colour pipeline | implemented / partly exercised / validated | 8 RGB OBJs; Java real parse 790/1,351; 371 MiniMesh models | GLB origin and visual fidelity unresolved |
| GUI independent building load | implemented / exercised | log reads all 8 building OBJs; no colour-parser fallback message | no screenshot/interaction benchmark |
| strict complete models | unresolved / contradicted by status | 0/8 READY, 8/8 COARSE_READY | strict check disabled |
| watertight solids | contradicted | 0/8 watertight; 7/8 non-manifold | may still serve coarse visualisation |

