# External data and models

Large, generated, credential-bearing, or licence-restricted artifacts are intentionally outside Git. Use this repository layout for local reproduction:

```text
external/
  data/
    satellite/          # authorised overhead inputs
    panoramas/          # self-owned or separately licensed 2:1 panoramas
    osm_features/       # building.geojson and related OSM extracts
    dsm/                # EPSG:27700 GeoTIFF tiles
    meshes/             # optional cached OBJ inputs
  facade_pytorch/       # optional external façade segmentation repository
  FrankenGAN/bikegan/   # optional external appearance repository
  checkpoints/          # downloaded model weights
```

The portable bridge configurations already point to these locations. Sat3DGen weights are downloaded separately from the model source documented by the upstream project. Gurobi is proprietary and must be installed through its own licence; no Gurobi JAR or native library is included.

For Google downloads, set `GOOGLE_MAPS_API_KEY` in the process environment. Never place a key in a JSON file, notebook, source file, command history intended for publication, or Git commit. Restrict the key by API and application, and rotate any key that has previously been exposed.

The raw `modelsExample2` meshes are not stored in Git: the collection is hundreds of MiB and contains an OBJ larger than GitHub's ordinary 100 MB object limit. The final matched comparison figures and their compact audit metadata are included instead.
