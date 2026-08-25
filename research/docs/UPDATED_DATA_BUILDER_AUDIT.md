# Updated `data_builder` audit for the thesis report

Audit date: 13 August 2026 (Europe/London)  
Scope: `E:\UCL\Project\data_builder` and its generated data  
Policy: read-only with respect to `data_builder`; derived evidence was written only under this thesis workspace.

## 1. Bottom line

No observable `data_builder` update occurred after the previous 10 August audit.

- The outer `E:\UCL\Project\data_builder` directory is not a Git repository. The authoritative local Git source remains the nested repository `E:\UCL\Project\data_builder\data_builder`.
- The nested repository is on `main` at `57cb966ba6321b4f16781a2f4eddfc860ec9599d` (`First Upload`, 26 July 2026). Its local `origin/main` ref is the same commit, and tracked-file status is clean.
- There are **20,142 untracked generated files**. Consequently, most imagery, masks and auxiliary rasters do not have commit-level provenance.
- The newest non-`.git` file timestamp inside the authoritative tree is 26 July 2026 21:15 UTC, before the previous thesis audit. File timestamps can be preserved during copying, so this is supporting evidence rather than a cryptographic history.
- Every principal value in the 10 August evidence ledger was reproduced: 2,343 inputs, 2,333 matches, 1,633/350/350 split, 1,205 unique hashes among 2,333 equirectangular panoramas, 6,987/9,332 out-of-bounds candidate labels, and 113/127 fully valid DSM patches.

The defensible report update is therefore not “new `data_builder` results”. It is a clearer and more visual audit of the existing data, with several newly quantified limitations: channel coverage is misaligned, the four-candidate validation export is absent, the 10%-overlap grid misses two panorama queries, and the London legacy data are byte-for-byte duplicated beneath a directory named `Seattle`.

## 2. Repository authority and comparison boundary

### 2.1 Nested Git source

| Check | Result |
|---|---|
| Authoritative path | `E:\UCL\Project\data_builder\data_builder` |
| Branch / HEAD | `main` / `57cb966ba6321b4f16781a2f4eddfc860ec9599d` |
| Local `origin/main` ref | same commit |
| Tracked worktree changes | 0 |
| Untracked generated files | 20,142 |
| Non-Git files / total size | 20,241 / 3,279,293,166 bytes |

No `git fetch` was performed because this was a read-only local audit. “Local `origin/main` equals HEAD” does **not** prove that the current GitHub branch has no newer commit.

### 2.2 Outer working-copy duplicate

After excluding the nested repository itself, the outer directory contains 20,238 files. The nested tree contains the same 20,238 data/source relative paths and sizes, plus `.gitattributes`, `.gitignore`, and `LICENSE`. SHA-256 was also checked for four representative high-value artefacts (`builder.ipynb`, the four-candidate label file, the auxiliary quality report, and `building.geojson`), and each outer/inner pair matched. A complete SHA-256 comparison of every shared file was not run; the path-and-size comparison is not equivalent to full byte identity.

All thesis measurements below use the nested path only, preventing the outer duplicate from doubling counts.

## 3. Recomputed dataset evidence

### 3.1 Input points, matching and splits

| Measure | Recomputed value | Interpretation |
|---|---:|---|
| Input GeoJSON features | 2,343 Points | all input coordinates are unique |
| Feature bounding box | about 1,220.765 m × 889.681 m | contradicts the `500x500` filename as a literal extent |
| Matched panorama rows | 2,333 / 2,343 = 99.5732% | ten source indices are unmatched |
| Unmatched source indices | 160, 166, 190, 193, 448, 692, 1,125, 1,151, 1,974, 1,975 | indices follow GeoJSON feature order |
| Maximum label-coordinate rounding difference | 0.066 m | labels use rounded coordinates |
| Legacy split | 1,633 / 350 / 350 | 69.996% / 15.002% / 15.002% |
| Split ID overlap | 0 | row identities are disjoint |

The split is latitude ordered with effectively no spatial buffer:

- train latitude: 51.503131–51.508385;
- validation latitude: 51.508386–51.509027;
- test latitude: 51.509035–51.510742.

The IDs are disjoint, but exact image contents still leak across adjacent splits because different query rows can contain the same returned equirectangular panorama.

### 3.2 Image inventories and duplication

| Channel | Files | Dimensions | Exact hashes | Qualification |
|---|---:|---:|---:|---|
| Legacy panorama | 2,333 | 640×320 | 2,333 unique | Street View request branch; returned pano IDs/snapped coordinates were not retained |
| Equirectangular panorama | 2,333 | 1,536×768 | 1,205 unique | 427 duplicate-hash groups; 1,555 files in such groups; largest group 26 |
| Sparse satellite | 127 | 640×640 | 127 unique | original incomplete 16×17 centre lattice |
| Filled satellite | 272 | 640×640 | 272 unique | complete 16×17 lattice |
| 50%-overlap satellite | 990 | 640×640 | not re-hashed | complete 30×33 lattice |
| 10%-overlap satellite | 306 | 640×640 | not re-hashed | complete 17×18 lattice |
| Equirectangular sky mask | 2,333 | 1,536×768 | not re-hashed | one mask per equirectangular file |
| Filled-grid model depth | 272 | 640×640 | not re-hashed | relative DPT output, not metric depth |
| Sparse-grid DSM raster channel | 127 | 640×640 | not re-hashed | per-patch normalised DSM, not metric depth |

Exact equirectangular-image leakage:

| Split pair | Shared hashes | Rows affected in first split | Rows affected in second split |
|---|---:|---:|---:|
| train–validation | 7 | 20 | 11 |
| train–test | 0 | 0 | 0 |
| validation–test | 3 | 4 | 3 |

### 3.3 Four-candidate label geometry

The full VIGOR-style file contains 2,333 queries and four candidates per query (9,332 candidate records). A candidate is counted as containing the query when both recorded offsets lie inside the 640×640 image half-width, `|dx| <= 320` and `|dy| <= 320`.

| Candidate rank | Inside | Outside | Outside rate | Largest absolute axis offset |
|---:|---:|---:|---:|---:|
| 1 | 2,333 | 0 | 0% | 319.0 px |
| 2 | 12 | 2,321 | 99.4856% | 628.3 px |
| 3 | 0 | 2,333 | 100% | 820.6 px |
| 4 | 0 | 2,333 | 100% | 948.7 px |
| **all ranks** | **2,345** | **6,987** | **74.8714%** | **948.7 px** |

2,321 queries have one containing candidate and 12 have two. None has three or four. All referenced satellite files exist, so this is a label-semantics problem rather than a missing-file problem.

The strict four-candidate exporter writes a full file, a 1,633-row training file and a 350-row test file, but it does not accept or write a validation source. Therefore **350 validation IDs have no dedicated four-candidate validation export**. The separate legacy one-candidate validation file still exists; the two formats must not be described as one complete strict VIGOR split.

## 4. Satellite and panorama coverage

Areas below are Web-Mercator rectangle unions converted using metres per pixel at the grid's mean latitude. They are suitable as local engineering estimates, not cadastral areas.

| Grid | Centres | Lattice | Estimated union area | Queries covered | Mean covering tiles/query | Global redundant-area fraction |
|---|---:|---:|---:|---:|---:|---:|
| sparse | 127 | 16×17 with 145 missing centres | 448,946 m² | 2,333/2,333 | 1.003 | 0.046% |
| filled base | 272 | 16×17 complete | 961,447 m² | 2,333/2,333 | 1.003 | 0.055% |
| nominal 50% overlap | 990 | 30×33 complete | 931,883 m² | 2,333/2,333 | 4.007 | 73.385% |
| nominal 10% overlap | 306 | 17×18 complete | 887,780 m² | 2,331/2,333 | 1.230 | 17.966% |

“Global redundant-area fraction” is `1 - union area / sum of individual tile areas`; it is not the pairwise overlap printed by the generator. Fifty per cent overlap on each axis naturally produces roughly fourfold interior coverage.

The original sparse tiles cover all matched panorama positions because acquisition was concentrated around those queries. Filling the Cartesian lattice primarily adds background coverage rather than additional query coverage.

The 10%-overlap grid misses two eastern-edge queries:

- `pano_184`: 51.510645, −0.121092;
- `pano_827`: 51.510608, −0.121190.

Thus the 306 meshes may align one-for-one with the 306 images, but the grid is not a complete replacement for the query-supporting base grid.

## 5. DSM coverage and semantics

Four Environment Agency rasters are present in EPSG:27700. Each is 5,000×5,000 at 1 m resolution; their non-overlapping rectangle union is 100,000,000 m². All 2,343 input points and all 272 filled-grid centres lie inside at least one raster extent.

The realised DSM auxiliary branch is nevertheless limited to the 127 sparse centres:

| Measure | Value |
|---|---:|
| Raster-derived patch records | 127 / 272 filled-grid centres (46.6912%) |
| Fully valid patches | 113 / 127 (88.9764%) |
| Partial patches | 14 / 127 |
| Empty patches | 0 |
| Minimum / median / mean valid ratio | 0.56875 / 1.0 / 0.963632 |
| Selected source tiles | TQ27ne: 10; TQ28se: 54; TQ38sw: 63; TQ37nw: 0 |

This distinguishes **source availability** from **realised auxiliary coverage**: the source rasters physically cover the whole study region, while only 127 stored patches were produced. The generator selects a single raster based on each satellite centre and does not mosaic neighbouring rasters, explaining partial patches near seams. It then applies patch-wise p5–p95 normalisation, so the stored PNG values are not comparable absolute elevations.

The separate legacy `sat_depth_dsm_metadata.csv` contains 127/127 rows marked outside its recorded DSM extent, and no surviving producer establishes that channel as valid metric DSM/depth evidence.

## 6. OSM coverage and contamination

The substantive OSM export (`osm_features`) records a query bounding box of approximately 1,367.257 m × 1,559.927 m and produces eight category-mask sets. Each category contains 127 masks, aligned to the sparse branch, not to the 272 filled grid or either overlap grid. `LondonDataSet/London/osm_constraints/meta.json` describes 272 centres but its directory contains only metadata, not a realised 272-tile semantic export.

Across the eight category GeoJSON files:

- total category feature records: 67,281;
- untagged point records introduced from recursive support nodes: 57,623 (85.6453%);
- unique feature IDs across categories: 49,651;
- IDs appearing in multiple category exports: 14,270;
- maximum category multiplicity: 6.

The extraction function turns every returned OSM node into a point feature, including untagged nodes returned merely to support way geometry. The mask renderer then draws those points. Consequently, neither feature totals nor mask-pixel occupancy can be interpreted as semantic prevalence or ground-truth area without first removing support nodes and validating geometries.

Category summary:

| Category | GeoJSON records | Untagged support points | Non-empty masks / 127 |
|---|---:|---:|---:|
| building | 19,682 | 17,353 | 115 |
| building with height | 9,237 | 8,180 | 85 |
| road | 17,373 | 12,813 | 127 |
| water | 5,503 | 5,392 | 12 |
| land use | 4,177 | 3,998 | 61 |
| green | 2,097 | 1,999 | 46 |
| railway | 1,238 | 1,141 | 62 |
| barrier | 7,974 | 6,747 | 109 |

## 7. London data stored beneath the `Seattle` alias

Four legacy London channels under `london_vigor_root/London` were compared with `vigor_sat3dgen_root/Seattle` by common filename and SHA-256:

| Channel | London files | Seattle files | Byte-identical pairs |
|---|---:|---:|---:|
| panorama | 2,333 | 2,333 | 2,333 |
| satellite | 127 | 127 | 127 |
| panorama sky mask | 2,333 | 2,333 | 2,333 |
| model depth | 127 | 127 | 127 |

This looks like a compatibility alias for a model expecting a supported VIGOR city directory, not an independent Seattle experiment. It must be excluded from any cross-city sample count, generalisation claim or London-versus-Seattle comparison.

## 8. Six recommended thesis figures

### Figure DB-1 — Provenance-aware data-builder flow

**Design:** a branch diagram separating acquisition, grid repair, VIGOR-format export, auxiliary channels and the later Sat3DGen-compatible alias. Put counts and image dimensions on the arrows. Use solid arrows for code-backed transformations and dashed arrows where run provenance/model revision is not stored.

**Exact inputs:**

- `E:\UCL\Project\data_builder\data_builder\builder.ipynb`
- `fill_satellite_grid.py`, `resample_satellite_overlap50.py`, `resample_satellite_overlap10.py`
- `download_pano_equirect.py`, `align_london_vigor_format.py`, `generate_london_vigor4_labels.py`
- `generate_pano_sky_mask.py`, `generate_sat_depth.py`, `generate_sat_depth_from_dsm.py`, `download_osm_features.py`
- `research/results/update_data_builder/data_builder_audit.json`

**Caption message:** the pipeline expands 127 sparse satellite images to several grid products but the real DSM and OSM artefacts remain on the 127-tile branch.

### Figure DB-2 — Spatial support and grid footprints

**Design:** map the 2,343 source points by matched/unmatched status and split; overlay the footprints or centre lattices for sparse, filled, 50%-overlap and 10%-overlap products. Mark the two queries missed by the 10%-overlap grid.

**Exact derived inputs:**

- `research/results/update_data_builder/spatial_points.csv`
- `research/results/update_data_builder/satellite_centres.csv`

**Caption message:** the filename's nominal 500×500 extent does not describe the 1,220.8×889.7 m point envelope, and denser sampling changes redundancy more than query coverage.

### Figure DB-3 — Representative multimodal record

**Design:** a labelled panel with satellite image, model-relative depth, normalised DSM raster, equirectangular panorama, sky mask and selected OSM masks. Do not label either depth image as metric ground truth.

**Selection:** `sat_51.503434_-0.132325.png` has a full-validity DSM patch, all eight non-empty OSM masks, 27 rank-1 linked queries, and a closest linked query `pano_753` at 26.343 px.

**Exact paths and selection rule:** `research/results/update_data_builder/representative_multimodal_sample.json`.

**Caption boundary:** these are stored input/auxiliary channels, not validated ground truth. Google/Airbus/Street View image-credit and redistribution requirements must be checked before submission.

### Figure DB-4 — Panorama duplication and split leakage

**Design:** left, files versus unique hashes and duplicate-group sizes; right, an UpSet-style or simple pairwise leakage chart for train/validation/test.

**Exact derived inputs:**

- `research/results/update_data_builder/panorama_duplicate_groups.csv`
- `research/results/update_data_builder/split_hash_leakage.csv`

**Caption message:** row-disjoint spatial splits do not prevent exact-image leakage in the later equirectangular export.

### Figure DB-5 — Four-candidate offset geometry

**Design:** scatter or density plot of `(dx,dy)` by candidate rank with a visible ±320 px square; add a rank-wise outside-rate bar chart.

**Exact derived inputs:**

- `research/results/update_data_builder/label_candidates.csv`
- `research/results/update_data_builder/label_candidate_rank_summary.csv`

**Caption message:** all nearest candidates contain the query, but 6,987/9,332 total candidates lie outside nominal bounds; the file is four-nearest, not four-containing.

### Figure DB-6 — Auxiliary-channel coverage and quality boundary

**Design:** combine (a) a channel coverage matrix (2,333 panorama/sky pairs, 272 filled satellite/model-depth pairs, 127 DSM/OSM tiles), (b) a DSM valid-ratio histogram, and (c) category bars separating target geometries from untagged OSM support points.

**Exact derived inputs:**

- `research/results/update_data_builder/data_builder_audit.json`
- `research/results/update_data_builder/dsm_patch_validity.csv`
- `research/results/update_data_builder/osm_category_summary.csv`

**Caption message:** physical source coverage is not the same as realised, aligned, validated auxiliary coverage.

## 9. Derived evidence files

All generated files are under `research/results/update_data_builder/`:

- `audit_data_builder.py` — read-only reproducibility script;
- `data_builder_audit.json` — full machine-readable summary;
- `spatial_points.csv` — 2,343 input points with match and split status;
- `satellite_centres.csv` — centres for all four grids;
- `label_candidates.csv` and `label_candidate_rank_summary.csv`;
- `panorama_duplicate_groups.csv` and `split_hash_leakage.csv`;
- `dsm_patch_validity.csv`;
- `osm_category_summary.csv`;
- `london_seattle_alias_comparison.csv`;
- `representative_multimodal_sample.json`.

## 10. Reproduction commands

Run from `E:\UCL\Project\UCL_Master_s_Thesis_Template2`:

```powershell
git -C E:\UCL\Project\data_builder\data_builder rev-parse HEAD
git -C E:\UCL\Project\data_builder\data_builder rev-parse origin/main
git -C E:\UCL\Project\data_builder\data_builder status --porcelain=v1 --untracked-files=no
git -C E:\UCL\Project\data_builder\data_builder ls-files --others --exclude-standard
python research\results\update_data_builder\audit_data_builder.py
```

The last command uses local Pillow and Rasterio. It reads the authoritative repository and writes only to the thesis workspace.

## 11. Claim and provenance limits

- Generated artefact existence does not establish the exact command, model revision, API response, time or author that created it.
- The default model IDs appear in code, but model revisions are not pinned in the stored outputs.
- Google panorama IDs and snapped acquisition coordinates were not retained; image hashes are a content-based diagnostic, not full provenance.
- The satellite union areas are local-scale Web-Mercator estimates.
- OSM masks are demonstrative artefacts, not validated semantic ground truth.
- The four-candidate file implements four nearest tiles, not four containing tiles.
- The 272 filled grid does not imply 272 aligned DSM and OSM records.
- No claim of new data, cross-city generalisation, reconstruction accuracy improvement or model contribution follows from this audit.

## 12. Suggested additions to `WorkingSkillForCodex_check_it_every_time.txt`

The following rules are ready to incorporate when the parent report update edits that file:

1. Audit only `E:\UCL\Project\data_builder\data_builder`; never recursively count the outer directory and nested clone together.
2. Record the exact Git commit and separately inventory untracked generated data.
3. Keep the 127 sparse branch, 272 filled branch, 990 50%-overlap branch and 306 10%-overlap branch distinct.
4. Describe strict labels as “four nearest candidates”; do not call them four containing tiles.
5. State that the strict four-candidate validation export is absent.
6. Treat DPT depth as relative and DSM PNGs as patch-normalised, not metric.
7. State that realised DSM and OSM auxiliary outputs cover 127/272 base tiles.
8. Do not use the `Seattle` alias as an independent city dataset.
9. Caption OSM figures as unvalidated because recursive support nodes enter the masks.
10. Check imagery attribution/licensing before embedding satellite or Street View pixels in the final PDF.
