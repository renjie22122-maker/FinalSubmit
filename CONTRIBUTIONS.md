# Individual contribution statement

Renjie Li developed the dissertation-specific system integration, experiments, and evaluation represented by this repository. The principal project contributions are:

- a large-image Sat3DGen inference path using overlapping windows, fractional sub-voxel density placement, raised-cosine feathering, one Marching Cubes extraction over the fused field, and a second pass that recovers per-vertex colour from the source mosaic;
- the active mesh-space processing pipeline for geographic tile placement, cropping, bottom removal, stitching experiments, DSM-based height correction, building extraction, and OBJ export;
- the Python/Java bridge that connects geographic selection and project manifests to Sat3DGen processing and ChordAtlas asset publication;
- ChordAtlas extensions for selected-block mesh/panorama services, vertex-colour OBJ handling, MiniTransform/Workspace command-line paths, reference-style inputs, and their validators;
- London data-builder scripts, controlled tests, audit programs, figures, experimental analysis, and the dissertation text.

The neural architecture, original training code, and published Sat3DGen baseline are the work of Qian et al. The ChordAtlas base application is the work of Tom Kelly and its contributors. These upstream codebases are retained as identifiable snapshots with their original licences and commit identifiers; project modifications are not presented as upstream work.

CityEngine was used only as a comparative modelling route. Esri software and generated source assets are not distributed here. FrankenGAN/facade-processing systems, Google imagery, Ordnance Survey/Environment Agency-style DSM inputs, OSM data, and model checkpoints are external dependencies or inputs and are not claimed as original project code.
