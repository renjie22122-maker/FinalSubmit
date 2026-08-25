# Third-party notices

## Sat3DGen

- Upstream: <https://github.com/qianmingduowan/Sat3DGen>
- Snapshot: `882cc66c363aa16b82fb2e494be7600003076890`
- Licence: MIT, retained at `components/sat3dgen/Sat3DGen/LICENSE`
- Paper/model attribution: Ming Qian et al.; model checkpoint hosted separately as `qian43/Sat3DGen`

The upstream snapshot is clean. Dissertation-specific algorithms are under `components/sat3dgen/extensions/`; local runtime compatibility changes are recorded as a separate patch.

## ChordAtlas

- Upstream: <https://github.com/twak/chordatlas>
- Snapshot: `1e6f5cf174e30f7bd2e5a374ad70ced75fe75fd9`
- Licence: Apache License 2.0, retained at `components/chordatlas/LICENSE`

The snapshot is overlaid with the Java files changed or added for this dissertation and with the project bridge. See `components/chordatlas/UPSTREAM.md` and `CONTRIBUTIONS.md`.

## Data and services not redistributed

Google Maps/Street View imagery, DSM GeoTIFFs, OSM extracts, CityEngine assets, proprietary Gurobi artifacts, neural-network checkpoints, and external façade/FrankenGAN repositories are not included. Their providers' terms and licences apply when obtained separately.

## Thesis and template assets

The thesis PDF and source are included as the submitted academic artifact. UCL logos and template graphics remain subject to UCL branding and template terms. The root MIT licence does not apply to those assets or to third-party content depicted in result figures.
