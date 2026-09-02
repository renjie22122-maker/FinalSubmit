# Sat3DGen Method Comparison (2026)

[中文版本](SAT3DGEN_METHOD_COMPARISON_2026.md)

## 1. Scope and evidence

This note distinguishes the published Sat3DGen method from the extensions exercised in this project.
It is an engineering and dissertation-writing reference, not a claim that a new generative model was trained.
The comparison covers single-tile inference, published large-image density fusion, the active large-image path,
mesh-space assembly, vertex-colour recovery, DSM correction, and downstream ChordAtlas publication.

The principal code locations in the submission are:

- `components/sat3dgen/Sat3DGen` -- packaged upstream Sat3DGen code.
- `components/sat3dgen/mesh_pipeline` -- independent-mesh regional processing.
- `research/scripts/run_sat3dgen_big_image_app192.py` -- active large-image inference entry point.
- `research/scripts/colorize_sat3dgen_big_mesh.py` -- second-pass colour recovery.
- `research/scripts/download_google_static_zoom_mosaic.py` -- satellite mosaic acquisition helper.
- `components/chordatlas` -- the downstream ChordAtlas bridge and application code.

Representative retained evidence outside the code package includes:

- `E:\UCL\Project\Sat3DGen\pipeline_output\final_v36\test_merge_scene.obj`.
- `E:\UCL\Project\Sat3DGen\pipeline_output\final_v36\test_merge_scene_corrected.obj`.
- `E:\UCL\Project\Sat3DGen\pipeline_output\final_v36\debug_per_tile_no_bottom\tile_09.obj`.
- `E:\UCL\Project\Sat3DGen\pipeline_output\final_v36\debug_per_tile_crop\tile_09.obj`.

These files are evidence for the reported runs; they are not substitutes for the published method description.

## 2. Published Sat3DGen method

Sat3DGen conditions urban 3D reconstruction on overhead satellite imagery.
The learned model predicts a volumetric density representation from an image crop.
Surface geometry is recovered from the density field with Marching Cubes.
The standard single-image setting is associated with zoom-20, 256-by-256 satellite inputs.
That relationship matters because changing zoom changes both ground coverage and the visual scale learned by the model.

The published large-image route is a density-field method rather than an OBJ-stitching method.
It evaluates overlapping image windows, places their predicted density volumes in a common volume,
blends overlapping density samples, and applies Marching Cubes once to the fused field.
This avoids extracting and then welding a separate mesh for every window.

The upstream large-image implementation uses integer-aligned placement where its assumptions hold.
It removes an edge band before combining the remaining density values.
The retained project comparison describes this as a 32-cell hard crop followed by equal-weight averaging.
The exact upstream behaviour must always be attributed to the inspected revision, not generalized to every fork.

The original large-image route does not, by itself, establish a coloured final OBJ in the project format.
Single-tile appearance outputs and large-image density fusion should therefore be discussed separately.

## 3. Active project large-image path

The active path keeps zoom-20 image clarity while covering a larger geographic area with overlapping windows.
It does not reduce the source imagery to zoom 19 merely to reduce GPU use.
The retained operational settings are a 640-pixel inference window, `grid_size=192`, and 75 per cent overlap.
Here `grid_size=192` is the volumetric mesh resolution, not the satellite-image zoom or final geographic extent.

Fractional placement permits a predicted density sample to land at a non-integer global coordinate,
for example 38.5 rather than exactly 38 or 39.
Its contribution is deposited through bilinear sub-voxel weighting instead of being rounded to one grid location.
This reduces placement discontinuities when geographic window spacing is not an integer multiple of density cells.

The active overlap band uses a 19-cell raised-cosine transition.
Weights rise smoothly inside an incoming window and fall smoothly near its outgoing boundary.
Overlapping values are accumulated as weighted density sums and divided by accumulated weights.
Marching Cubes is then run once on the fused density field.

This method improves seam continuity but cannot guarantee perfect reconstruction.
Local prediction disagreement, insufficient context, threshold sensitivity, or model-domain mismatch can remain visible.
The smooth weighting may also soften a genuine sharp feature when adjacent windows predict incompatible surfaces.

## 4. Colour recovery

The project adds a second pass for vertex RGB recovery after the global surface has been extracted.
Each global mesh vertex is projected into every relevant inference window.
Candidate colour observations are sampled from the corresponding satellite-conditioned appearance output.
The same spatial support and confidence logic are used to combine valid observations.
The final RGB value is a weighted average rather than an arbitrary colour copied from one tile.

Colour recovery does not make the model photogrammetrically textured.
The colours are derived from real overhead imagery but façades remain underconstrained from a nadir view.
Roof colour and plan-view context are better observed than occluded street-level surfaces.
Seams can still occur if windows disagree, projections are inaccurate, or coverage weights become sparse.

## 5. DSM correction and mesh-space processing

The DSM branch samples the Environment Agency surface raster in its projected coordinate system.
WGS84 locations are transformed into EPSG:27700 before raster sampling.
Bilinear interpolation avoids a staircase response when a query lies between DSM pixel centres.
The local 5th--95th percentile interval is used as a robust relative height range.
This suppresses isolated extreme cells but removes direct cross-patch comparability in absolute elevation.

DSM correction adjusts the generated mesh vertically toward the observed surface profile.
It should be described as relative surface-height correction unless a metric calibration is explicitly demonstrated.
The DSM contains buildings, vegetation, vehicles, and terrain and is not a bare-earth DTM.
It also combines surveys acquired at different times, so it is not exact contemporaneous ground truth.

The independent-mesh route starts from already extracted OBJ tiles.
It crops geometry, removes unwanted bottom surfaces, places tiles in a shared local frame,
searches for stitch candidates, and applies DSM-related height correction in mesh space.
This route is experimentally useful but is not equivalent to the published fused-density large-image algorithm.

## 6. Direct comparison

| Aspect | Published/single-tile Sat3DGen | Published large-image route | Active project route |
|---|---|---|---|
| Conditioning | Satellite crop | Overlapping satellite crops | Zoom-20 geographic mosaic |
| Nominal image setting | 256 by 256 at zoom 20 | Fixed overlapping windows | 640-pixel windows |
| Volume resolution | Upstream configuration | Upstream configuration | `grid_size=192` |
| Window overlap | Not applicable | Overlapping | 75 per cent |
| Global placement | Local volume | Integer-aligned global field | Fractional global placement |
| Boundary treatment | Per-crop output | 32-cell hard crop | 19-cell raised-cosine weighting |
| Fusion | None | Equal-weight density averaging | Weighted density accumulation |
| Surface extraction | Marching Cubes | One Marching Cubes pass | One Marching Cubes pass |
| Large-image colour | Not established by density fusion | Not retained in the project output | Second-pass weighted vertex RGB |
| DSM correction | Not the central method | Not the central method | Optional EPSG:27700 surface correction |
| Independent OBJ stitching | No | No | Separate experimental route |

## 7. Thesis-ready wording

The following wording is suitable for the methods chapter:

> The project retains Sat3DGen's satellite-conditioned density representation but extends regional inference.
> Overlapping zoom-20 windows are placed in a shared density field using fractional coordinates,
> blended with a raised-cosine overlap weight, and converted to one surface with Marching Cubes.
> A second projection pass combines compatible appearance observations into per-vertex RGB values.

For comparison with the independent-mesh route:

> The large-image route fuses predictions before surface extraction, whereas the independent-tile route begins
> with separate OBJ meshes and performs cropping, placement, stitching, and height correction in mesh space.
> The two routes therefore evaluate different representations and should not be reported as interchangeable.

For DSM correction:

> DSM sampling supplies a robust relative surface-height channel in EPSG:27700.
> Bilinear sampling reduces raster quantization, while percentile scaling limits the effect of extreme cells;
> the resulting correction is evaluated as local relative height rather than absolute metric reconstruction.

## 8. Claim boundaries

Do not claim that the project trained a new Sat3DGen model unless a completed training run is documented.
Do not describe the independent OBJ stitcher as the published Sat3DGen large-image algorithm.
Do not claim that vertex RGB is a recovered street-level façade texture.
Do not claim that smooth density fusion eliminates every block boundary.
Do not equate `grid_size=192` with a 192-pixel satellite input.
Do not interpret a zoom change as a harmless memory optimization; it changes geographic scale.
Do not call percentile-normalized DSM values absolute depth or absolute elevation.
Do not use watertightness alone as evidence of geometric accuracy.

Claims about seam reduction, colour continuity, or DSM improvement should be tied to retained outputs.
Claims about the published method should be tied to the paper and the inspected upstream repository revision.
Engineering observations should be separated from conclusions about reconstruction accuracy.

## 9. Primary sources and BibTeX pointers

Use the dissertation bibliography rather than inventing replacement metadata.
The central BibTeX keys are `qian2026sat3dgen` and `qianSat3DGenCode2026`.
Related representation context is cited through `qian2023sat2density` and `qian2026sat2densitypp`.
Marching Cubes should use `lorensen1987marching`.
DSM and coordinate-system statements should use `environmentAgencyDSM2022`, `epsg27700`, and `epsg3857`.

The authoritative repository URL and paper URL recorded in the packaged bibliography should be preserved verbatim.
When code behaviour differs across revisions, cite the exact commit or packaged patch in addition to the project URL.
The complete BibTeX records remain in the dissertation `references.bib`; this note intentionally references their keys.

## 10. Minimum comparison experiment

Use the same geographic extent, zoom-20 imagery, model weights, density threshold, and coordinate frame.
Run the upstream-compatible large-image baseline and the active fractional/feathered route.
Record image dimensions, window count, overlap, `grid_size`, GPU peak memory, and elapsed time.
Export the fused density metadata and final mesh for each route.
Report vertex and face counts, connected components, boundary edges, and non-manifold edges.
Measure seam-region continuity separately from the interior of each window.
Evaluate vertex-colour coverage and colour discontinuity only where both routes provide comparable appearance data.
Apply DSM correction to a matched copy and report the vertical change without changing the horizontal geometry.
Keep figures at the same camera, projection, crop, and scale.

The minimum qualitative comparison should include a roof region, a window-overlap seam, and a cross-tile building.
The minimum quantitative comparison should distinguish topology, geometric continuity, colour coverage, and runtime.
One favourable building is an illustration, not a population-level accuracy benchmark.

## 11. Dissertation placement

The published method belongs in the literature review and the opening of the methods chapter.
Fractional placement, raised-cosine fusion, colour recovery, and DSM correction belong in project methodology.
Parameter values and retained paths belong in implementation details or an appendix.
Mesh statistics, seam observations, and matched before/after renders belong in Results and Analysis.
Limitations of nadir colour, DSM timing, and sample size belong in Discussion.

The final review position is concise: the project contribution is a tested regional inference and integration method
built around a published satellite-conditioned generator, not a replacement generative architecture.
Its strongest evidence is the explicit comparison of pre-extraction density fusion, post-extraction mesh processing,
colour recovery, and geospatial height correction under documented parameters and retained outputs.
