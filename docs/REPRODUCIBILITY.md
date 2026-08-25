# Reproducibility guide

## Preserved implementation states

The Sat3DGen and ChordAtlas bases are pinned by commit in their `UPSTREAM.md` files. Sat3DGen compatibility edits are a standalone patch; dissertation extensions are separate files. ChordAtlas is represented as the pinned upstream tree overlaid with the explicitly attributed project modifications and bridge.

The `research/results/` directory contains compact JSON/CSV/Markdown evidence from the evaluated runs. Absolute Windows paths that occur inside historical evidence identify the original execution location; they are provenance fields, not required runtime locations. Active bridge configuration files and launch scripts are repository-relative.

## Large-image inference

The evaluated path is implemented by:

- `research/scripts/run_sat3dgen_big_image_app192.py`
- `research/scripts/colorize_sat3dgen_big_mesh.py`

The run keeps the published 256-pixel model input while using a 192-voxel output grid, 75% overlap, fractional/bilinear density placement, raised-cosine fusion weights, one global Marching Cubes extraction, and a second colour-projection pass. See each script's `--help` output for configurable inputs and output locations.

## Independent-mesh route

The evaluated implementation is the top-level package at `components/sat3dgen/mesh_pipeline/`. The similarly named historical nested repository was not part of the evaluated path and is therefore not bundled. The bridge rejects that legacy location explicitly.

## Tests

Run `scripts/run_tests.ps1` from PowerShell. It executes the 124 bridge tests and 9 large-image unit tests without requiring the large external datasets. The active mesh driver is a data-dependent integration run, not a self-contained unit test; invoke it with `scripts/run_mesh_integration.ps1 -DataRoot <path>` after restoring the London `pipeline_output` and `LondonDataSet` layout. GPU inference, Java validators against a built shaded JAR, and full geographic runs are separate integration checks because they require checkpoints, proprietary or external dependencies, and local data.

## Thesis

The final PDF is `Report.pdf`. To rebuild, run `latexmk -pdf Report.tex` from the repository root. The repository retains the original template layout, bibliography, headers/footers, and only the 17 figure PDFs referenced by the final LaTeX source.
