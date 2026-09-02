# ChordAtlas GUI Operation and Acceptance Guide

> 中文版本: [GUI_WORKFLOW.md](GUI_WORKFLOW.md)

## Full OSM Display and On-Selection MiniMesh Generation

This is the currently recommended entry point. At project startup, only the complete OSM building outlines are required; a city MiniMesh does not need to exist in advance:

```powershell
Set-Location E:\UCL\Project\myProject\bridge
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json validate
.\scripts\start_sat3dgen.ps1
$env:GOOGLE_MAPS_API_KEY = '<new-restricted-key>'
.\scripts\launch_gui.ps1 -Config .\config\data_builder_london_on_demand.json
```

The GUI must be launched from the same PowerShell session in which `GOOGLE_MAPS_API_KEY` was set. The key is not written to disk or placed on the command line; do not reuse a key that has already been exposed publicly.

GUI procedure:

1. Only the GIS base layer should initially appear on the left. It is normal for there to be no initial MiniMesh; all 984 OSM building outlines remain visible and are not clipped because a mesh is missing.
2. Click the GIS layer row. Under Options, choose one of the three `mesh source on Select` settings:
   - `loaded workspace mesh (legacy)`: the original workflow, which generates a Block from a Mesh/MiniMesh that has already been loaded;
   - `satellite tiles on demand`: retains the original per-tile download, inference, OSM prealignment, stitch, and DSM workflow;
   - `big image app192 on demand`: downloads a large mosaic for the current footprint, performs a single fusion using app192, raw 640, 75% overlap, and fractional feathering, then runs Sat3DGen vertex colouring, coordinate alignment, and DSM correction.
3. Select `select` at the top, aim the view at the target, and right-click the orange building outline.
4. The status displays the current source, imagery preparation, Sat3DGen, and MiniMesh conversion stages. It becomes `Ready` after success. Adjacent footprints that share exactly the same vertices are processed as one connected selection.
5. On success, the GUI adds a hidden MiniMesh for each building (for spatial operations) and a selectable semantic Block. If OBJ vertex colours are available, it also adds a selectable colour layer. After selecting the Block, you can continue with `find profiles` and profiles `optimize`. The per-tile and large-image routes use different selection IDs, so both can be loaded at the same time and shown independently from Layers.
6. On failure, no scene, incomplete OBJ, or partial MiniMesh is loaded. The GIS outlines remain visible, and the task result can be inspected before retrying.

Diagnostic files for each task are stored at:

```text
<workspace>\_selection_jobs\<selection-id>\tile_manifest.json
<workspace>\_selection_jobs\<selection-id>\result.json
<workspace>\logs\selected-mesh\<selection-id>.log
```

Large-image tasks use `big-image-<geometry-hash>` and also contain:

```text
<workspace>\_selection_jobs\big-image-<hash>\big_image_plan.json
<workspace>\_selection_jobs\big-image-<hash>\big_image\mosaic.png
<workspace>\_selection_jobs\big-image-<hash>\big_image\inference\mesh.ply
<workspace>\_selection_jobs\big-image-<hash>\big_image\inference\mesh_colored.ply
<workspace>\_selection_jobs\big-image-<hash>\big_image\inference\color_metadata.json
```

If the validated coloured large-image cache configured for the project completely covers the current footprint plus 30 m of context, it is reused offline; otherwise, imagery is downloaded and inference is run on demand. After geometry generation, a second memory-bounded Sat3DGen colour MLP pass uses the same fractional-feather window weights to generate genuine per-vertex RGB. Publication occurs only after strict validation of the hash, coverage, vertex/face counts, unchanged geometry, and opaque alpha. Colours are preserved through coordinate transformation, spatial cropping, DSM correction, and ground-zero normalisation into each building's `cropped.obj`; the GUI displays the colour layer by default. If genuine colours are unavailable, RGB is not fabricated and the workflow safely falls back to semantic BlockGen.

The large-image PLY is treated as an upstream result that has already been fused and had its underside removed. The bridge **does not** repeat underside removal, stitching, duplicate-face removal, or small-component removal; it performs only one coordinate transformation, spatial crop, mandatory DSM correction, common ground-zero normalisation, and Block publication.

After the task reaches the mesh pipeline, its directory also contains `pipeline.stdout.log`, `pipeline.stderr.log`, and `top_level_pipeline_manifest.json`. `PLANNED` or `FAILED` results are written only under `_selection_jobs`; `<workspace>\generated_blocks\<selection-id>\result.json` is generated only after successful publication.

The result is committed to the GUI only when the publication status is `READY` and all three OBJ files pass validation. A successful directory also contains `cropped.obj`, `gis.obj`, `gis_footprints.obj`, and the Java-generated `minimesh\index.xml`. When the GUI is reopened, the same selection reuses a valid READY cache; a failed retry does not damage the previous successful result.

Common failure meanings:

- Required satellite tiles are not cached and `GOOGLE_MAPS_API_KEY` is missing: the task stops before making a network request and does not run Sat3DGen.
- HTTP 403, a non-PNG response, or an incorrect image size: the satellite tile fails validation and does not enter inference.
- The Gradio service is not running or `/generate_mesh` fails: no model is published.
- A required tile or roof has insufficient coverage: the result is classified as a partial model and is not loaded into the GUI.
- The GUI still shows only OSM: first inspect `_selection_jobs\<selection-id>\result.json` and `logs\selected-mesh\<selection-id>.log`. This normally means that a validation gate was triggered, not that GIS disappeared.

FrankenGAN and facade segmentation do not participate in this “satellite image -> MiniMesh” generation stage. They are used respectively in the later material-generation and panorama/façade-feature workflows.

## The Following Sections: Smoke/Full Workspaces with Pre-generated MiniMesh

The original steps below, including `load all` and cropping an existing MiniMesh into `scratch`, apply only to batch-generated smoke/full workspaces. They do not apply to the OSM-only, on-demand workspace described above.

This page describes the shortest primary workflow after an automatically generated workspace starts:

```text
OSM/DSM footprint -> GIS -> Select -> Block -> find profiles
                              +
                     Sat3DGen mesh -> MiniMesh
```

This primary workflow does not require the facade_pytorch, FrankenGAN, or panorama download service to be started.

> Current automated acceptance status: the 6-tile top-level scene has been generated; GIS, MiniMesh, tweed.xml, and the JAR have been validated; and the Windows GUI has started successfully. Select/Profile below is a manual visual acceptance step to be completed in the currently visible GUI. Performance and model quality for the full AOI have not yet been claimed as complete.

## 0. Pre-launch Checks

In PowerShell:

```powershell
Set-Location E:\UCL\Project\myProject\bridge

.\scripts\doctor.ps1
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json validate
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json launch --dry-run
```

Launch only after `validate` returns `status: ok`:

```powershell
.\scripts\launch_gui.ps1
```

The launcher passes the target workspace to the current JAR as `--project` and writes the log to:

```text
<workspace>\logs\chordatlas-gui.log
```

Do not first double-click an old JAR or manually select `tweed.xml` from another ChordAtlas directory. Doing so can easily load an old plugin or an old workspace.

## 1. Confirm the Automatically Loaded Layers

At minimum, the generator/layer list on the left side of the GUI should contain:

- `gis(o) footprints.obj`: orange GIS footprints;
- `minimesh`: blue 3D mesh;
- `panos panos`: appears only when compliant panoramas have been configured and imported successfully.

The top/tool area should include `select`. The Options panel on the right changes with the currently selected layer or generator. Nothing is selected immediately after startup, so an empty Options panel is not necessarily an error.

Acceptance checks:

- both GIS and MiniMesh are present;
- `select` is available;
- the log contains no `tweed.xml` deserialisation, `index.xml`, `model.obj`, or plugin-loading error.

If the entire Tools section is empty, close the GUI first and check:

```powershell
.\scripts\build_chordatlas.ps1
.\scripts\myproject.ps1 --config .\config\data_builder_london_smoke.json validate
.\scripts\launch_gui.ps1
```

If it is still empty, inspect `chordatlas-gui.log` and confirm that the following file was launched:

```text
E:\UCL\Project\myProject\target\chordatlas-0.0.1.jar
```

## 2. Load MiniMesh

1. Click `minimesh` on the left.
2. Click `load all` under Options on the right.
3. Wait for all chunks to load; a large mesh causes a noticeable delay.

Why this step is required: at startup, MiniGen holds only the chunk index from `minimesh/index.xml`. `load all` adds every bound to the loading range and reads each `<tile>\model.obj`.

Acceptance checks:

- a blue/materialised 3D scene appears in the view;
- the log contains `loading mesh <id> from <workspace>\minimesh`;
- selecting `wireframe` reveals the triangle mesh;
- GIS outlines and the mesh broadly coincide in the X/Z plane; they should not be hundreds of metres apart, mirrored, or rotated by 90°.

If the mesh is not visible:

- confirm that `minimesh`, rather than GIS, is selected;
- click `load all` again and wait;
- inspect the MiniMesh bounds in `validation_report.json`;
- if GIS and the mesh are misaligned, stop the subsequent steps and check `origin_lat/origin_lon`, mesh bounds, and vertical offset in `manifest.json`.

## 3. Generate a Block with Select

1. Choose the `select` tool.
2. Ensure that both the orange GIS layer and the loaded MiniMesh are visible.
3. In the 3D view, **right-click** an orange footprint face that overlaps the mesh (left-clicking or selecting a layer checkbox does not create a Block).

The current implementation then:

1. derives footprint loops from the connected GIS block that was clicked;
2. calculates a convex hull using `blockMeshPadding`;
3. crops that extent from the MiniMesh;
4. creates the intermediate files for this block under the workspace;
5. adds and automatically selects a generator named `block`.

Intermediate files are located at:

```text
<workspace>\scratch\meshes\<n>\
├── cropped.obj
├── gis.obj
└── gis_footprints.obj
```

Acceptance criteria:

- a new `block` appears on the left;
- Options automatically switches to the Block UI, where buttons including `find profiles`, `render panoramas`, and `find image features` are visible;
- `cropped.obj` exists and contains both `v` and `f` records;
- `gis.obj` and `gis_footprints.obj` exist;
- the displayed block covers only the vicinity of the selected urban block, rather than the entire mesh or an empty model.

If right-clicking does not generate a Block:

- the tool must be exactly `select`, not Facade, Align, or another tool;
- click a GIS face, rather than clicking only the blue mesh;
- the MiniMesh must first be loaded with `load all`;
- the footprint must intersect the mesh coverage area;
- inspect the log for `Failed to find mesh from minimesh or gml layers`, a cropping exception, or an empty OBJ.

## 4. find profiles

1. If `block` is not currently selected, click it on the left first.
2. Click `find profiles` under Options on the right.
3. Wait for the background thread to finish; do not click repeatedly in quick succession.

A Profile is neither downloaded data nor BigSUR/FrankenGAN output. The current `ProfileGen` uses:

- the Block's `cropped.obj` 3D mesh;
- the selected GIS footprint loops;
- the extent, horizontal slices, and façade/profile lines computed from the mesh.

When computation finishes, it adds a generator named `profiles` and selects it automatically.

Acceptance criteria:

- a new `profiles` item appears on the left, rather than only the original `block`;
- the current selection changes to `profiles`;
- derived geometry such as profiles/horizontal lines appears in the view;
- the GUI remains responsive, and the log contains no thread exception, empty extent, or OBJ read error.

If you see “model profile”, it is the profile model extracted algorithmically from the cropped mesh of the current Block. Its provenance chain is `MiniMesh -> cropped.obj + GIS loops -> ProfileGen`.

## 5. Panorama-based Door/Window Constraints and Real-world Material References

This is an optional real-world enhancement path, not a prerequisite for OSM, MiniMesh, Block, or Profile. The two image inputs serve different purposes:

```text
OSM footprint of current Block -> get Street View panoramas -> sample preview/approve batch
                                                               -> 2:1 panorama -> render panoramas
                                                                                -> find image features -> door/window/shop geometry constraints

Rectified, cropped real façade image -> Facade texture 8D style vector -> FrankenGAN façade material generation
Selected building satellite roof crop -> Roof texture 8D style vector  -> FrankenGAN roof material generation
```

Panoramas and `facade_pytorch` extract positional constraints for doors, windows, and similar elements from real façade observations. The façade/roof reference images in the Joint editor control colour and material style. Reference images are not applied directly as UV textures and do not replace `find image features` when deciding door and window positions.

### 5.1 Obtain Panoramas Directly from the Current Block (Recommended)

This entry point reproduces the original post-Block panorama workflow in ChordAtlas without depending on panorama, label, or `todo.list` files previously saved by `data_builder`:

1. Right-click a GIS footprint with `select`; wait for the target `block` to appear and become selected.
2. In Block Options, click `get Street View panoramas`. The program sends only the current OSM footprint of this Block (in local metre coordinates) to the bridge. Using the geographic origin in the workspace `manifest.json`, the bridge plans street-facing candidate points, queries metadata, deduplicates by Google pano ID, and creates `panos\todo.list` for the current area.
3. The program first downloads **one** 2:1 panorama and opens a preview. Only after confirming that the image and urban block are correct should you click `Yes` in the `Approve selected-block Street View batch` dialog to download the remaining deduplicated candidate panoramas. Clicking `No` retains the sample but stops the batch.
4. When the sample is published and when the batch completes, the GUI automatically creates a PanoGen layer pointing to the current workspace's `panos` directory, or refreshes an existing layer that points to the same directory. There is no need to run `Layers '+' -> panos (jpg)` manually.
5. Return to Block Options and click `render panoramas -> find image features` in that order. After success, run `find profiles -> optimize`.

Before starting the GUI, `GOOGLE_MAPS_API_KEY` must be set in the same process environment. The key is not written to a project file or command line. Each new panorama requests metadata once and downloads six 640×640 directional images, which are combined into a 2560×1280 JPEG. A batch creates Google API requests and may incur charges, so inspect the sample and candidate count and check the Google Cloud quota/billing settings before clicking `Yes`. Existing JPEGs are reused according to the cache rules.

### 5.2 CLI Entry Point Using an Existing `todo.list` (Retained)

Each non-empty line of `todo.list` retains ChordAtlas's seven-field format:

```text
latitude_longitude_altitude_heading_tilt_roll_panoId
```

The first six fields must be finite numbers, the first two are WGS84 latitude/longitude, and the final field is a safe source-panorama identifier. You can use an existing ChordAtlas/panoscraper manifest for the current area or prepare a manifest in the same format from candidate capture points near roads in the current AOI. For each line, the importer calls metadata and refreshes the identifier to the current Google pano ID. The input orientation is written to the report for traceability, while the bridge standardises the orientation of published files.

Spatial matching is mandatory: the manifest coordinates must cover the current workspace and should preferably cover the building to be selected and its visible streets. `datasets\regent_osm\panos\todo.list` applies only to the Regent data and must not be copied to `data_builder_london_on_demand` or another area. Even if downloading succeeds, coordinates from the wrong area cannot provide valid observations of the selected façade to `render panoramas`.

Plan offline first, then download only one sample. `$todo` below must be replaced with the manifest for the current area:

```powershell
Set-Location E:\UCL\Project\myProject\bridge
$todo = 'E:\path\to\current_workspace_todo.list'

.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json `
  import-streetview-panos --todo $todo --dry-run --limit 1

$env:GOOGLE_MAPS_API_KEY = '<restricted-key>'
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json `
  import-streetview-panos --todo $todo --limit 1
```

The API key is read only from `GOOGLE_MAPS_API_KEY` in the current PowerShell process. A key on the command line is not accepted, and the key is not written to reports, filenames, or request logs. Inspect `<workspace>\panos\streetview_sample_report.json` and the generated 2560×1280 JPEG, confirming the coordinates, direction, and image before running the batch:

```powershell
.\scripts\myproject.ps1 --config .\config\data_builder_london_on_demand.json `
  import-streetview-panos --todo $todo --all --sample-approved
```

Batch details are written to `streetview_batch_report.json`. When new images are published, an existing `panos.xml` is first renamed to a timestamped backup so that an old scan cache is not reused. MyProject local coordinates use X=east, Y=up, and Z=south, so `myproject-local` output files use heading 180 by default. Pass `--coordinate-mode original-geographic` only when importing an original geographic workspace.

If panorama images come from your own or separately licensed data rather than the downloader above, you can continue to use `prepare-panos` to import strict 2:1 JPEGs. Do not mix two regions or disguise an ordinary perspective photograph as an equirectangular panorama.

### 5.3 PanoGen Layer after CLI Import

This section applies only to the retained CLI `import-streetview-panos` workflow above. It publishes files but **does not modify `tweed.xml` or automatically create a PanoGen layer**. After the first CLI import, you must:

1. Select `Layers '+' -> panos (jpg)` in the GUI.
2. Point it to the current workspace's own `<workspace>\panos` directory, not a directory from another project.
3. Select the new panorama layer and confirm under Options that it displays `coordinates: myProject local (X east / Z south)`. If it displays `original geographic`, first check `frame.origin_lat`, `frame.origin_lon`, and axes in the current workspace's `manifest.json`; do not continue with door/window projection.
4. After adding or replacing JPEGs, select the existing panorama layer and click `refresh panoramas`. This button only rescans an existing layer; it cannot replace the first-time creation of the layer.

The Block command `get Street View panoramas` does not have this limitation: it automatically creates or refreshes the correct PanoGen layer.

### 5.4 Extract Door/Window Constraints from Panoramas

Perform the following sequence for the current target:

1. Right-click a GIS footprint with `select` and wait for the target building's Block to load successfully.
2. Select this `block` and click `render panoramas`, allowing ChordAtlas to generate each façade's `rendered.png` from spatially matched panoramas.
3. After confirming that valid façade renders exist, click `find image features`. The GUI runs the PyTorch module from the existing environment on demand:

   ```text
   conda run --no-capture-output -n sat3dgen python -B -m facade_pytorch ...
   ```

4. After successful segmentation, Java immediately refreshes `FeatureCache`. It also refreshes when complete output already exists, so the GUI does not need to be restarted.
5. Then run `find profiles -> optimize` under profiles. Later regularisation and FrankenGAN stages can use the extracted rectangular constraints for windows, doors, shops, and similar elements.

The working directory for `facade_pytorch` is `E:\UCL\Project\facade-segmentation`, and its package directory is `facade_pytorch`. It is a child process launched on click, not a persistent service. It does not need to be started separately in advance and does not modify the `sat3dgen` environment. Details for a façade are stored in `facade-pytorch.log` in the corresponding feature directory. If no panorama covers the current building, the façade is occluded, or the render is empty, the result may be missing; in that case the program retains its original rule-based/generative door and window logic.

### 5.5 Source and Status of the Satellite Roof Reference

When an on-demand mesh selection succeeds and buildings are separated, the bridge reuses the satellite tiles already validated for that selection to generate a north-up roof crop for each building; it does not make another network request solely for the reference image. The path is:

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

`reference.json` is the publication-complete marker. The Joint editor enables `Use satellite roof reference` only when `status: READY` and the currently selected Block is that building. Insufficient coverage, missing input, or a crop failure publishes `status: UNAVAILABLE`. This is a soft failure: it does not fail an already validated mesh, MiniMesh, or Block, and Roof texture continues to use the original random/manual-reference fallback.

Satellite imagery can provide the roof's dominant colour and visual style, but shadows, trees, occlusion, low resolution, and capture time can all affect encoding. It is not a per-pixel roof texture and does not guarantee reconstruction of the real tile arrangement.

### 5.5 Start the FrankenGAN Compatibility Watcher

The watcher is required only when generating neural-network materials or encoding a reference image as a style vector:

```powershell
Set-Location E:\UCL\Project\myProject\bridge
.\scripts\start_frankengan.ps1
.\scripts\start_frankengan.ps1 -Execute
```

The first command only displays the command; the second starts it. Use the MyProject compatibility entry point and do not simultaneously run another watcher that writes directly to the same FrankenGAN `input/output` directories. The Joint editor should display `FrankenGAN encoder ready`. If it displays `Start FrankenGAN watcher before loading`, restore the watcher first; a leftover directory must not be treated as an available encoder.

### 5.6 Load Real Façade and Roof Styles in Joint

After `optimize` is complete and you have entered the building appearance/FrankenGAN editor:

1. Select `Joint` as the style source and open the joint distribution editor.
2. Select the `Facade texture` icon/tab. In the `Load/drop facade reference` area, click to choose a file or drag an image into the area. A real image showing one building and one façade, as front-on and tightly cropped as possible, is recommended. First remove large areas of sky and road, neighbouring buildings, and strong perspective distortion.
3. Wait for `Encoding reference...` to disappear. On success, the reference image is encoded as an 8D latent vector and updates the current Joint mode; the original image is not applied directly to the model.
4. Select the `Roof texture` icon/tab. Prefer `Use satellite roof reference` to use the READY crop for the current building, or load another valid roof reference in the `Load/drop satellite roof reference` area.
5. Click `ok` to return, then click `redraw distribution` to regenerate façade, roof, window/door, and other neural-network materials. The reference image fixes only the appearance centre of the current mode; Joint's multiple modes, probabilities, sigma, and other network settings are retained.

`Clear / use random` on either page clears the reference preview and restores the random Gaussian mean that existed before loading; it does not force the vector to a new fixed style. If reading or encoding fails, the old latent vector, preview, and existing mode remain unchanged. A failed import dragged onto `+` also does not leave an empty mode.

The reference image must be readable by Java ImageIO, no larger than 32 MB when stored, no larger than 16 megapixels when decoded, and no more than 8192 px on either side. The encoding network must return exactly eight finite values. The current automated real-world input supports only `Facade texture` and `Roof texture`; separate materials for windows, doors, and other elements retain Joint's random/manual distributions, although their geometric positions can be constrained by the real panorama features from 5.3. BigSUR/FrankenGAN checkpoints do not generate a footprint, MiniMesh, Block, or Profile.

## 6. One-page Acceptance Checklist

| Stage | Must be visible | Check first on failure |
|---|---|---|
| Workspace | `tweed.xml`, `manifest.json`, `footprints.obj`, `minimesh/index.xml` | `workspace-descriptor.log`, `minimesh-conversion.log` |
| GUI startup | `gis(o) footprints.obj`, `minimesh`, `select` | `chordatlas-gui.log`; whether the current JAR was launched |
| MiniMesh | A 3D mesh appears after clicking `load all` | Bounds in `validation_report.json`; `model.obj` |
| Select | A new `block` appears on the left; three OBJ files exist under scratch | Whether GIS and mesh overlap; whether the tool is `select` |
| Profiles | A new `profiles` item appears and is selected on the left | Whether `cropped.obj` is non-empty; thread exceptions in the GUI log |
| Panoramas | 2560×1280 JPEGs for the current area; the layer displays `myProject local` | `streetview_sample_report.json`; whether the coordinates belong to the current workspace |
| façade features | `parameters.yml` is generated in each target directory, and the cache is refreshed after completion | `rendered.png`, `facade-pytorch.log`; this is not a prerequisite of the primary workflow |
| roof reference | `reference.json` for the current building is `READY` | Masks in the same directory; `UNAVAILABLE` only falls back for the material and does not fail the model |
| Neural material | Joint reports that the encoder is ready; it can redraw after reference encoding | Watcher log; `Clear / use random` restores the original random logic |

## 7. Logs and Reproducibility Information

When reporting an issue, retain the following together:

- the config JSON used;
- `<workspace>\manifest.json`;
- `<workspace>\validation_report.json`;
- `<workspace>\panos\streetview_sample_report.json` or `streetview_batch_report.json` (when panoramas are involved);
- `_mesh_job\top_level_pipeline_manifest.json` in generate mode;
- `<workspace>\logs\chordatlas-gui.log`;
- the path and size of the corresponding `scratch\meshes\<n>\cropped.obj`;
- `facade-pytorch.log` in the corresponding façade feature directory;
- `references\roof\reference.json` for the corresponding building (when a satellite roof reference is involved).

Do not report only “no model”. The files above distinguish the exact failure stage among bbox/tile selection, origin, MiniMesh loading, Block cropping, and Profile computation.
