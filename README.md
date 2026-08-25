# Large-Area Satellite-Derived Urban Reconstruction

This is the private final-submission repository for Renjie Li's UCL MSc dissertation, **Large-Area Satellite-Derived Urban Reconstruction and Manifest-Driven Integration into ChordAtlas**. It contains the submitted thesis, the project-authored reconstruction and integration code, clean snapshots of the two modified upstream projects, tests, and compact execution evidence.

## Repository layout

- `components/sat3dgen/Sat3DGen/`: clean Sat3DGen upstream snapshot at commit `882cc66c363aa16b82fb2e494be7600003076890`.
- `components/sat3dgen/patches/`: the local device-compatibility and `einops` dependency patch, kept separate from the upstream snapshot.
- `research/scripts/`: large-image density fusion, colour recovery, analysis, figure generation, and tests developed for the dissertation.
- `components/sat3dgen/mesh_pipeline/`: active independent-mesh crop, bottom-removal, stitching, DSM correction, and extraction implementation.
- `components/chordatlas/`: ChordAtlas upstream snapshot at commit `1e6f5cf174e30f7bd2e5a374ad70ced75fe75fd9`, overlaid with the project Java changes and the Python bridge.
- `components/data_builder/`: code used to assemble and audit the London inputs. Raw imagery, DSM rasters, panoramas, and derived datasets are not included.
- `research/`: compact audit results, seam-analysis code/results, and supporting research notes.
- repository root plus `figures/generated/` and `Logos/`: the original template layout containing the final LaTeX source, 17 referenced figure PDFs, bibliography, template assets, and compiled `Report.pdf`.

The exact project/upstream authorship boundary is recorded in `CONTRIBUTIONS.md`; third-party versions and licences are recorded in `THIRD_PARTY_NOTICES.md`.

## Quick start

Prerequisites are Conda, a CUDA-capable PyTorch installation for neural inference, Java 8, Maven, and a LaTeX distribution. Model checkpoints, proprietary Gurobi components, external façade models, and geographic source data must be obtained separately.

```powershell
conda env create -f environment.yml
conda activate sat3dgen
pip install -r requirements.txt
```

Apply the recorded Sat3DGen compatibility patch when reproducing the Windows/device-portable run:

```powershell
git apply --directory=components/sat3dgen/Sat3DGen components/sat3dgen/patches/0001-device-compatibility-and-einops.patch
```

Run the project-authored Python tests:

```powershell
./scripts/run_tests.ps1
```

Compile the thesis from the repository root with the unchanged LaTeX template structure:

```powershell
latexmk -pdf Report.tex
```

The bridge configurations in `components/chordatlas/bridge/config/` use repository-relative paths. Place separately licensed inputs under `external/` as described in `docs/DATA_AND_MODELS.md`.

## Credentials and data

Set Google credentials only at runtime:

```powershell
$env:GOOGLE_MAPS_API_KEY = '<your-key>'
```

Do not add credentials, downloaded Google imagery, panoramas, DSM GeoTIFFs, model checkpoints, or generated meshes to Git. A key used during development was removed from the submission snapshot and should be revoked/rotated in its provider console.

## Reproducibility scope

The repository preserves the executable algorithms and compact evidence used in the dissertation. Large or licence-restricted inputs and outputs are represented by documented acquisition steps, configuration contracts, filenames, and audit metadata rather than redistributed binaries. See `docs/REPRODUCIBILITY.md`.

## Licence

Project-authored software is licensed under the MIT terms in `LICENSE`. Component-specific upstream licences continue to apply. The MIT licence does not grant rights to the thesis text, UCL template assets, third-party software, geographic imagery, datasets, model weights, or generated third-party textures.
