# ChordAtlas Bridge

> Chinese version: [README.md](README.md)
> Detailed GUI workflow: [GUI_WORKFLOW_EN.md](GUI_WORKFLOW_EN.md)

This bridge connects geospatial inputs and Sat3DGen mesh outputs to a
ChordAtlas workspace. It validates paths, prepares selections, processes mesh
geometry, publishes footprint-level assets, and records the states consumed by
the ChordAtlas GUI.

It does not replace Sat3DGen, ChordAtlas, panorama acquisition, or FrankenGAN.

## Supported mesh sources

The bridge supports three mesh-source modes:

- **Loaded workspace mesh (legacy)** reuses a mesh already loaded by
  ChordAtlas.
- **Satellite tiles on demand** obtains the required imagery and runs the
  configured per-tile Sat3DGen workflow.
- **Big image app192 on demand** runs contiguous overlapping inference, fuses
  density before one global surface extraction, and can recover vertex colour
  in a second pass.

## Repository layout

Run commands from the packaged FinalSubmit repository. Code is expected at:

~~~text
components/chordatlas/                 ChordAtlas source and built JAR
components/chordatlas/bridge/          This bridge
components/sat3dgen/Sat3DGen/          Sat3DGen application and inference code
components/sat3dgen/mesh_pipeline/     Mesh-space processing
research/scripts/                      Data and large-image utilities
external/                              Local runtime configuration and logs
~~~

Keep Sat3DGen, the mesh pipeline, bridge scripts, and ChordAtlas code inside
FinalSubmit. Satellite imagery, DSM rasters, OSM extracts, model weights,
panoramas, third-party facade systems, workspace outputs, and runtime logs may
remain external.

## Requirements

Depending on the selected route, the workflow may require:

- Python and the configured Conda environment;
- CUDA-enabled PyTorch for practical Sat3DGen inference;
- Java 8-compatible tooling for ChordAtlas;
- Maven when rebuilding the ChordAtlas JAR;
- the external data and model paths named by the runtime configuration.

Apply the packaged Sat3DGen compatibility patch once if required:

~~~powershell
git apply --directory=components/sat3dgen/Sat3DGen components/sat3dgen/patches/0001-device-compatibility-and-einops.patch
~~~

## Quick start

Set the packaged bridge entry point, a local runtime configuration, and the
Conda executable:

~~~powershell
$Bridge = "E:\UCL\Project\FinalSubmit\components\chordatlas\bridge\run.py"
$Config = "E:\UCL\Project\FinalSubmit\external\runtime_on_demand.json"
$Conda = "C:\Users\Renjie_Li\anaconda3\Scripts\conda.exe"
~~~

Inspect the resolved plan and dependencies:

~~~powershell
& $Conda run --no-capture-output -n sat3dgen python -B $Bridge --config $Config plan
& $Conda run --no-capture-output -n sat3dgen python -B $Bridge --config $Config doctor --no-probes
& $Conda run --no-capture-output -n sat3dgen python -B $Bridge --config $Config doctor
~~~

Validate an existing workspace:

~~~powershell
& $Conda run --no-capture-output -n sat3dgen python -B $Bridge --config $Config validate
~~~

Preview and perform the ChordAtlas launch:

~~~powershell
& $Conda run --no-capture-output -n sat3dgen python -B $Bridge --config $Config launch --dry-run
& $Conda run --no-capture-output -n sat3dgen python -B $Bridge --config $Config launch
~~~

For selection, footprint processing, and GUI layer controls after launch, see
[GUI_WORKFLOW_EN.md](GUI_WORKFLOW_EN.md).

## Configuration modes

The runtime JSON separates packaged code from machine-specific data. Use
repository-relative paths for packaged scripts where possible and explicit
absolute paths for external datasets, weights, and workspaces.

### Cached mode

Cached mode resolves previously generated meshes from a validated cache. It
does not require new Sat3DGen inference. Confirm that every selected mesh
belongs to the intended geographic region before combining a cache batch.

### On-demand tile mode

On-demand tile mode validates or obtains satellite tiles and runs the
configured per-tile Sat3DGen workflow. Each generated tile is subsequently
handled by the mesh-space crop, placement, lower-surface removal, stitch, DSM,
and extraction route.

### On-demand large-image mode

On-demand large-image mode prepares one geographic satellite mosaic, runs
overlapping inference, fuses the density contributions, extracts one global
mesh, and optionally recovers vertex RGB.

Never store an API key in a committed JSON file. Supply credentials through an
environment variable or another local secret mechanism.

## Large-image contract

The packaged large-image route normally uses:

- satellite imagery at zoom 20 unless an experiment states otherwise;
- a 640-pixel inference window;
- grid size or mesh resolution 192;
- stride equal to 25 per cent of the window width, giving 75 per cent overlap;
- fractional density placement on the global grid;
- raised-cosine weighting across overlap regions;
- one Marching Cubes extraction from the fused density field;
- an optional memory-bounded second pass for vertex RGB.

Changing zoom, image scale, window size, grid size, stride, or overlap changes
the spatial contract. Update dependent parameters together and retain them in
the execution metadata. A lower zoom level is not equivalent to a smaller
inference window at the trained zoom level.

## Bounding boxes and coordinate systems

Geographic inputs use WGS84 longitude and latitude unless a field explicitly
declares another coordinate reference system. DSM rasters may use British
National Grid (EPSG:27700) and must be transformed before sampling. Web
Mercator tile indices are planning indices, not local OBJ coordinates.

A bounding box must have an explicit CRS and coordinate ordering. Follow the
configuration field names; do not infer longitude/latitude order from numeric
magnitude. It must cover the selected footprints and every satellite window
required by the configured overlap plan.

## Local mesh axes

The project-local mesh convention is:

~~~text
X = east
Y = height
Z = south
~~~

Longitude does not map directly to OBJ X without the configured
geographic-to-local transformation. Preserve the geographic extent, CRS, local
origin, geographic-to-local transformation, and mesh-axis convention whenever
an asset is moved or published.

## Workspace outputs

A prepared ChordAtlas project contains tweed.xml and route-dependent outputs
such as:

- tile plans and validated satellite inputs;
- raw and cropped per-tile meshes;
- lower-surface-removed and stitched meshes;
- DSM-corrected meshes;
- a contiguous global large-image mesh;
- an optional vertex-coloured global mesh;
- footprint-linked building OBJ assets;
- MiniMesh or other ChordAtlas consumer assets;
- manifests, execution records, validation reports, and READY state markers.

READY means that the bridge completed its publication contract for the
corresponding asset. File existence alone does not prove that every downstream
processing or appearance stage has run. Use validation_report.json and the
recorded execution state to distinguish prepared, failed, and published
selections.

Do not merge disconnected geographic cache regions into one scene. Confirm
filename coordinates, bounding boxes, and tile-plan membership before
processing a batch.

## Panorama boundary

Panoramas are optional for the base satellite-mesh publication route. They are
required only by workflows that explicitly use street-level appearance or
panorama-derived evidence. Panoramas remain externally licensed data; their
presence in a local workflow does not grant permission to redistribute them.

## FrankenGAN boundary

FrankenGAN is a separate downstream facade-detail system. The bridge may expose
structured building assets to ChordAtlas, but it does not convert satellite
vertex colour into ground-truth facade texture.

FrankenGAN outputs are generated appearance assets based on its inputs, model
weights, and procedural context. Keep its repository, model weights, panorama
inputs, and generated textures outside version control unless redistribution
is explicitly permitted.

## Security and data restrictions

- Never commit API keys, access tokens, credentials, or private URLs.
- Do not redistribute Google satellite imagery through this repository.
- Attribute OpenStreetMap and comply with the Open Database Licence.
- Follow Environment Agency and Open Government Licence requirements for DSM
  data.
- Do not commit model checkpoints or third-party facade weights without
  explicit redistribution rights.
- Treat panorama imagery and location-linked outputs as licensed data rather
  than source code.
- Keep private machine paths and runtime logs in the ignored external area.
- Inspect the resolved plan before downloading data or running GPU inference
  for a new bounding box.

## Validation and troubleshooting

Use plan to inspect resolved paths and the selected processing mode. Use doctor
to check dependencies and configured external resources. Use validate to check
an existing workspace.

A successful GUI launch does not itself prove that a selection is READY.
Inspect the bridge validation report and recorded workspace state.

If ChordAtlas cannot open the project, check the built JAR, Java version,
tweed.xml, configured workspace path, and plugin/resource paths.

If Sat3DGen inference fails, check the Conda environment, CUDA visibility, model
cache, input image scale, configured window and grid sizes, and available GPU
memory.

For the exact interaction sequence after launch, continue with
[GUI_WORKFLOW_EN.md](GUI_WORKFLOW_EN.md).
