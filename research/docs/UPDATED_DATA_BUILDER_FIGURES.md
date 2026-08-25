# Updated data_builder figure manifest

Generated on 13 August 2026 from audited local JSON/CSV only. No Google, Street View, Airbus or other source imagery is embedded in these figures.

## Reproduction

Run from the thesis workspace:

```powershell
python research\scripts\generate_updated_data_builder_figures.py
```

Generator:

- `research/scripts/generate_updated_data_builder_figures.py`

Audited inputs:

- `research/results/update_data_builder/data_builder_audit.json`
- `research/results/update_data_builder/label_candidates.csv`
- `research/results/update_data_builder/label_candidate_rank_summary.csv`
- `research/results/update_data_builder/osm_category_summary.csv`
- `research/results/update_data_builder/london_seattle_alias_comparison.csv`
- `research/results/update_data_builder/spatial_points.csv`
- `research/results/update_data_builder/satellite_centres.csv`

## Figure inventory and suggested captions

### DB-1: Pipeline and data volumes

Files:

- `figures/generated/data_builder_pipeline_funnel.pdf`
- `figures/generated/data_builder_pipeline_funnel.png`

Suggested label: `fig:data-builder-flow`

Suggested caption:

> Data-builder artefact flow and observed volumes. Of 2,343 input points, 2,333 matched panorama queries were divided into 1,633 training, 350 validation and 350 test rows. Satellite products expanded from 127 sparse tiles to a 272-tile base grid and 990/306 overlap variants, while stored raster-DSM patches and OSM masks remained on the 127-tile branch. No dedicated strict four-candidate validation export was present. Counts describe stored artefacts rather than one provenance-locked end-to-end run.

### DB-2: Coverage and channel availability

Files:

- `figures/generated/data_builder_coverage_channels.pdf`
- `figures/generated/data_builder_coverage_channels.png`

Suggested label: `fig:data-builder-coverage`

Suggested caption:

> Estimated grid-footprint union and stored-channel availability. The sparse, filled, nominal 50%-overlap and nominal 10%-overlap products cover approximately 0.449, 0.961, 0.932 and 0.888 square kilometres respectively under a local-scale Web-Mercator estimate. The 272-tile filled grid and model-depth channel are complete, whereas raster-DSM patches and each OSM mask category contain only 127/272 records; the strict four-candidate validation export contains 0/350. Availability is an artefact count, not a quality or accuracy measure.

### DB-3: VIGOR candidate-offset audit

Files:

- `figures/generated/data_builder_vigor_candidate_offsets.pdf`
- `figures/generated/data_builder_vigor_candidate_offsets.png`

Suggested label: `fig:data-builder-candidate-offsets`

Suggested caption:

> Recorded offsets for all 9,332 four-candidate label entries. The dashed square denotes the nominal containing region within 320 pixels of the image centre on both axes. Although every rank-one candidate contains its query, 6,987/9,332 entries (74.87%) lie outside this region; 2,321 queries have one containing candidate, 12 have two, and none has three or four. The export therefore represents four nearest tiles rather than four containing tiles.

### DB-4: Integrity and semantic audit

Files:

- `figures/generated/data_builder_integrity_semantics.pdf`
- `figures/generated/data_builder_integrity_semantics.png`

Suggested label: `fig:data-builder-integrity`

Suggested caption:

> Data-integrity and semantic audit. The 2,333 equirectangular panorama files reduce to 1,205 exact hashes, with exact-image leakage between train-validation and validation-test splits. Untagged recursive support nodes account for 57,623/67,281 OSM category records and are drawn into the stored masks. In addition, all 4,920 compared London panorama, satellite, sky-mask and model-depth files are byte-identical to files placed beneath the `Seattle` compatibility directory; this alias is not independent cross-city evidence.

### DB-5: Non-imagery spatial support schematic

Files:

- `figures/generated/data_builder_spatial_support.pdf`
- `figures/generated/data_builder_spatial_support.png`

Suggested label: `fig:data-builder-spatial-support`

Suggested caption:

> Non-imagery schematic of data-builder spatial support in local equirectangular offsets. The 2,343 input points span approximately 1,220.8 by 889.7 metres and the latitude-ordered split has no explicit spatial buffer. The filled 16-by-17 lattice adds 145 centres to the 127 sparse acquisitions. Its nominal 10%-overlap derivative covers 2,331/2,333 matched queries and misses two closely spaced eastern-edge queries.

## Suggested LaTeX inclusion

Use the vector PDF at full text width:

```tex
\begin{figure}[t]
  \centering
  \includegraphics[width=\linewidth]{figures/generated/data_builder_pipeline_funnel.pdf}
  \caption{...}
  \label{fig:data-builder-flow}
\end{figure}
```

The spatial schematic is optional if the report is space-constrained. The first four are the priority set requested for the report update.

## Verification

- Each PDF is a single page.
- DejaVu Sans regular and bold fonts are embedded with Unicode maps.
- `pdfimages -list` reports zero embedded raster images for every PDF; chart elements remain vector objects.
- PDFs were rendered with Poppler at 160 dpi and every rendered page was visually inspected after the final export.
- Extracted text was scanned for `TODO`, `TBD`, undefined values, non-finite values and replacement characters; none was found.
- PNG counterparts are 300 dpi and intended for preview only; use the PDF versions in LaTeX.
