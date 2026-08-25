"""Read-only quantitative audit of the local data_builder artefacts.

The audited repository is never modified.  All derived JSON/CSV files are
written beside this script in the thesis workspace.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
from typing import Iterable

from PIL import Image
import rasterio
from rasterio.warp import transform as transform_coordinates


REPO = Path(__file__).resolve().parents[3] / "components" / "data_builder"
OUT = Path(__file__).resolve().parent
ZOOM = 20
TILE_SIZE = 640
WORLD_SCALE = 256.0 * (2**ZOOM)
SAT_RE = re.compile(r"^(?:sat|satellite)_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)\.png$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(REPO), *args], text=True, encoding="utf-8"
    ).strip()


def read_nonempty(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def world_px(lat: float, lon: float) -> tuple[float, float]:
    sin_lat = math.sin(math.radians(max(min(lat, 85.05112878), -85.05112878)))
    x = (lon + 180.0) / 360.0 * WORLD_SCALE
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * WORLD_SCALE
    return x, y


def world_latlon(x: float, y: float) -> tuple[float, float]:
    lon = x / WORLD_SCALE * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / WORLD_SCALE
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lon


def metres_per_pixel(lat: float) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2**ZOOM)


def bbox_summary(points: Iterable[tuple[float, float]]) -> dict[str, float]:
    values = list(points)
    lats = [p[0] for p in values]
    lons = [p[1] for p in values]
    mean_lat = statistics.fmean(lats)
    return {
        "min_lat": min(lats),
        "max_lat": max(lats),
        "min_lon": min(lons),
        "max_lon": max(lons),
        "width_m_local_approx": (max(lons) - min(lons)) * 111320.0 * math.cos(math.radians(mean_lat)),
        "height_m_local_approx": (max(lats) - min(lats)) * 111320.0,
    }


def rectangle_union_area(rectangles: list[tuple[float, float, float, float]]) -> float:
    """Area of axis-aligned rectangles in the rectangles' coordinate units."""
    xs = sorted({r[0] for r in rectangles} | {r[2] for r in rectangles})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        if right <= left:
            continue
        intervals = sorted(
            (bottom, top)
            for x1, bottom, x2, top in rectangles
            if x1 < right and x2 > left
        )
        if not intervals:
            continue
        merged_length = 0.0
        start, end = intervals[0]
        for next_start, next_end in intervals[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                merged_length += end - start
                start, end = next_start, next_end
        merged_length += end - start
        area += (right - left) * merged_length
    return area


def parse_satellite_centres(directory: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.png")):
        match = SAT_RE.fullmatch(path.name)
        if not match:
            continue
        lat, lon = float(match.group(1)), float(match.group(2))
        x, y = world_px(lat, lon)
        records.append({"file": path.name, "lat": lat, "lon": lon, "world_x": x, "world_y": y})
    return records


def grid_summary(name: str, directory: Path, panos: list[dict[str, object]]) -> tuple[dict[str, object], list[dict[str, object]]]:
    centres = parse_satellite_centres(directory)
    rectangles = [
        (
            float(c["world_x"]) - TILE_SIZE / 2,
            float(c["world_y"]) - TILE_SIZE / 2,
            float(c["world_x"]) + TILE_SIZE / 2,
            float(c["world_y"]) + TILE_SIZE / 2,
        )
        for c in centres
    ]
    mean_lat = statistics.fmean(float(c["lat"]) for c in centres)
    mpp = metres_per_pixel(mean_lat)
    union_px2 = rectangle_union_area(rectangles)
    min_x = min(r[0] for r in rectangles)
    max_x = max(r[2] for r in rectangles)
    min_y = min(r[1] for r in rectangles)
    max_y = max(r[3] for r in rectangles)
    north, west = world_latlon(min_x, min_y)
    south, east = world_latlon(max_x, max_y)
    coverage_counts: list[int] = []
    for pano in panos:
        px, py = world_px(float(pano["lat"]), float(pano["lon"]))
        coverage_counts.append(
            sum(x1 <= px <= x2 and y1 <= py <= y2 for x1, y1, x2, y2 in rectangles)
        )
    unique_lats = sorted({float(c["lat"]) for c in centres})
    unique_lons = sorted({float(c["lon"]) for c in centres})
    summary = {
        "name": name,
        "path": str(directory),
        "tile_count": len(centres),
        "distinct_latitudes": len(unique_lats),
        "distinct_longitudes": len(unique_lons),
        "cartesian_capacity": len(unique_lats) * len(unique_lons),
        "missing_cartesian_centres": len(unique_lats) * len(unique_lons) - len(centres),
        "centre_bbox": bbox_summary((float(c["lat"]), float(c["lon"])) for c in centres),
        "footprint_bbox": {"min_lat": south, "max_lat": north, "min_lon": west, "max_lon": east},
        "mean_latitude": mean_lat,
        "metres_per_pixel_at_mean_latitude": mpp,
        "nominal_tile_width_m": TILE_SIZE * mpp,
        "union_area_m2_local_scale_estimate": union_px2 * mpp * mpp,
        "sum_individual_tile_areas_m2": len(rectangles) * TILE_SIZE * TILE_SIZE * mpp * mpp,
        "overlap_fraction_from_rounded_centres": 1.0 - union_px2 / (len(rectangles) * TILE_SIZE * TILE_SIZE),
        "panoramas_covered": sum(value > 0 for value in coverage_counts),
        "panoramas_uncovered": sum(value == 0 for value in coverage_counts),
        "mean_covering_tiles_per_panorama": statistics.fmean(coverage_counts),
        "min_covering_tiles_per_panorama": min(coverage_counts),
        "max_covering_tiles_per_panorama": max(coverage_counts),
    }
    rows = [
        {
            "grid": name,
            "file": c["file"],
            "latitude": c["lat"],
            "longitude": c["lon"],
        }
        for c in centres
    ]
    return summary, rows


def image_inventory(directory: Path, pattern: str, calculate_hashes: bool = False) -> tuple[dict[str, object], dict[str, str]]:
    paths = sorted(directory.glob(pattern))
    dimensions: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    hashes: dict[str, str] = {}
    for path in paths:
        with Image.open(path) as image:
            dimensions[f"{image.width}x{image.height}"] += 1
            modes[image.mode] += 1
        if calculate_hashes:
            hashes[path.name] = sha256(path)
    groups: defaultdict[str, list[str]] = defaultdict(list)
    for filename, digest in hashes.items():
        groups[digest].append(filename)
    duplicate_groups = [files for files in groups.values() if len(files) > 1]
    report: dict[str, object] = {
        "path": str(directory),
        "count": len(paths),
        "bytes": sum(path.stat().st_size for path in paths),
        "dimensions": dict(dimensions),
        "modes": dict(modes),
    }
    if calculate_hashes:
        report.update(
            {
                "unique_sha256": len(groups),
                "duplicate_hash_groups": len(duplicate_groups),
                "files_in_duplicate_hash_groups": sum(len(group) for group in duplicate_groups),
                "largest_duplicate_hash_group": max((len(group) for group in duplicate_groups), default=1),
            }
        )
    return report, hashes


def pano_id_from_split_line(line: str) -> str:
    return Path(line.split()[0].split(",")[0]).stem


def parse_split(path: Path) -> dict[str, tuple[float, float]]:
    records: dict[str, tuple[float, float]] = {}
    for line in read_nonempty(path):
        fields = line.split()[0].split(",")
        records[Path(fields[0]).stem] = (float(fields[1]), float(fields[2]))
    return records


def parse_four_candidate_labels(path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    queries: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    for line in read_nonempty(path):
        tokens = line.split()
        header = tokens[0].split(",")
        pano_id = header[0]
        lat, lon = float(header[1]), float(header[2])
        if len(tokens[1:]) != 12:
            raise ValueError(f"Expected four candidate triples: {line[:100]}")
        inside_count = 0
        for rank in range(4):
            sat, dy, dx = tokens[1 + rank * 3 : 4 + rank * 3]
            dy_value, dx_value = float(dy), float(dx)
            inside = abs(dx_value) <= TILE_SIZE / 2 and abs(dy_value) <= TILE_SIZE / 2
            inside_count += int(inside)
            candidates.append(
                {
                    "pano_id": pano_id,
                    "latitude": lat,
                    "longitude": lon,
                    "rank": rank + 1,
                    "satellite": sat,
                    "dy_px": dy_value,
                    "dx_px": dx_value,
                    "inside_640px_tile": inside,
                    "distance_px": math.hypot(dx_value, dy_value),
                }
            )
        queries.append(
            {
                "pano_id": pano_id,
                "latitude": lat,
                "longitude": lon,
                "containing_candidate_count": inside_count,
            }
        )
    return queries, candidates


def write_csv(name: str, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    path = OUT / name
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = fieldnames or list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def duplicate_and_leakage(
    equirect_hashes: dict[str, str], splits: dict[str, dict[str, tuple[float, float]]]
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    id_to_hash: dict[str, str] = {}
    hash_to_files: defaultdict[str, list[str]] = defaultdict(list)
    for filename, digest in equirect_hashes.items():
        pano_id = filename.split(",", 1)[0]
        id_to_hash[pano_id] = digest
        hash_to_files[digest].append(filename)
    duplicate_rows: list[dict[str, object]] = []
    for digest, files in sorted(hash_to_files.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(files) > 1:
            duplicate_rows.append(
                {"sha256": digest, "file_count": len(files), "files": ";".join(sorted(files))}
            )
    split_hashes = {
        split: Counter(id_to_hash[pano_id] for pano_id in ids if pano_id in id_to_hash)
        for split, ids in splits.items()
    }
    leakage_rows: list[dict[str, object]] = []
    names = list(splits)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            overlap = set(split_hashes[first]) & set(split_hashes[second])
            leakage_rows.append(
                {
                    "split_a": first,
                    "split_b": second,
                    "shared_hashes": len(overlap),
                    "affected_rows_a": sum(split_hashes[first][digest] for digest in overlap),
                    "affected_rows_b": sum(split_hashes[second][digest] for digest in overlap),
                }
            )
    return (
        {
            "files": len(equirect_hashes),
            "unique_hashes": len(hash_to_files),
            "duplicate_hash_groups": len(duplicate_rows),
            "files_in_duplicate_groups": sum(int(row["file_count"]) for row in duplicate_rows),
            "largest_group": max((int(row["file_count"]) for row in duplicate_rows), default=1),
        },
        duplicate_rows,
        leakage_rows,
    )


def dsm_summary(points: list[tuple[float, float]], filled_centres: list[dict[str, object]]) -> tuple[dict[str, object], list[dict[str, object]]]:
    tif_paths = sorted((REPO / "LondonDataSet" / "London_DSM").glob("*.tif"))
    rasters: list[dict[str, object]] = []
    rectangles: list[tuple[float, float, float, float]] = []
    for path in tif_paths:
        with rasterio.open(path) as dataset:
            bounds = dataset.bounds
            rectangles.append((bounds.left, bounds.bottom, bounds.right, bounds.top))
            rasters.append(
                {
                    "file": path.name,
                    "crs": str(dataset.crs),
                    "width": dataset.width,
                    "height": dataset.height,
                    "resolution_x_m": dataset.res[0],
                    "resolution_y_m": dataset.res[1],
                    "min_x": bounds.left,
                    "min_y": bounds.bottom,
                    "max_x": bounds.right,
                    "max_y": bounds.top,
                    "nodata": dataset.nodata,
                    "dtype": dataset.dtypes[0],
                }
            )
    all_lon = [point[1] for point in points]
    all_lat = [point[0] for point in points]
    point_x, point_y = transform_coordinates("EPSG:4326", "EPSG:27700", all_lon, all_lat)
    point_inside = sum(
        any(left <= x <= right and bottom <= y <= top for left, bottom, right, top in rectangles)
        for x, y in zip(point_x, point_y)
    )
    centre_lon = [float(record["lon"]) for record in filled_centres]
    centre_lat = [float(record["lat"]) for record in filled_centres]
    centre_x, centre_y = transform_coordinates("EPSG:4326", "EPSG:27700", centre_lon, centre_lat)
    centre_inside = sum(
        any(left <= x <= right and bottom <= y <= top for left, bottom, right, top in rectangles)
        for x, y in zip(centre_x, centre_y)
    )
    metadata_path = REPO / "london_vigor_root" / "London" / "sat_depth_dsm_raster_metadata.csv"
    patch_rows: list[dict[str, object]] = []
    with metadata_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            ratio = float(row["valid_ratio"])
            patch_rows.append(
                {
                    **row,
                    "valid_ratio": ratio,
                    "fully_valid": ratio >= 1.0 - 1e-9,
                    "partial": 0.0 < ratio < 1.0 - 1e-9,
                    "empty": ratio == 0.0,
                }
            )
    ratios = [float(row["valid_ratio"]) for row in patch_rows]
    tile_counts = Counter(str(row["dsm_tile"]) for row in patch_rows)
    old_metadata_path = REPO / "london_vigor_root" / "London" / "sat_depth_dsm_metadata.csv"
    with old_metadata_path.open(newline="", encoding="utf-8") as handle:
        old_rows = list(csv.DictReader(handle))
    return (
        {
            "rasters": rasters,
            "raster_count": len(rasters),
            "union_area_m2": rectangle_union_area(rectangles),
            "input_points_inside_any_raster": point_inside,
            "input_point_count": len(points),
            "filled_grid_centres_inside_any_raster": centre_inside,
            "filled_grid_centre_count": len(filled_centres),
            "realised_raster_patch_count": len(patch_rows),
            "fully_valid_patch_count": sum(bool(row["fully_valid"]) for row in patch_rows),
            "partial_patch_count": sum(bool(row["partial"]) for row in patch_rows),
            "empty_patch_count": sum(bool(row["empty"]) for row in patch_rows),
            "valid_ratio_min": min(ratios),
            "valid_ratio_median": statistics.median(ratios),
            "valid_ratio_mean": statistics.fmean(ratios),
            "patches_by_selected_single_raster": dict(tile_counts),
            "legacy_dsm_metadata_rows": len(old_rows),
            "legacy_metadata_inside_extent_true": sum(row["inside_dsm_extent"].lower() == "true" for row in old_rows),
        },
        patch_rows,
    )


def osm_summary() -> tuple[dict[str, object], list[dict[str, object]]]:
    root = REPO / "osm_features"
    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    all_feature_ids: list[str] = []
    for category in meta["categories"]:
        raw = json.loads((root / f"osm_raw_{category}.json").read_text(encoding="utf-8"))
        elements = raw.get("elements", [])
        element_types = Counter(str(element.get("type")) for element in elements)
        untagged_nodes = sum(
            element.get("type") == "node" and not element.get("tags") for element in elements
        )
        geo = json.loads((root / f"{category}.geojson").read_text(encoding="utf-8"))
        features = geo.get("features", [])
        geometry_types = Counter(str(feature.get("geometry", {}).get("type")) for feature in features)
        support_points = sum(
            feature.get("geometry", {}).get("type") == "Point"
            and set(feature.get("properties", {})) <= {"osm_id", "osm_type"}
            for feature in features
        )
        ids = [str(feature.get("id")) for feature in features]
        all_feature_ids.extend(ids)
        mask_paths = sorted((root / "masks" / category).glob("*.png"))
        nonempty_masks = 0
        nonempty_pixels = 0
        total_pixels = 0
        for path in mask_paths:
            with Image.open(path) as image:
                channel = image.getchannel("A") if "A" in image.getbands() else image.convert("L")
                histogram = channel.histogram()
                pixels = image.width * image.height
                occupied = pixels - histogram[0]
                nonempty_masks += int(occupied > 0)
                nonempty_pixels += occupied
                total_pixels += pixels
        rows.append(
            {
                "category": category,
                "raw_elements": len(elements),
                "raw_nodes": element_types["node"],
                "raw_ways": element_types["way"],
                "raw_relations": element_types["relation"],
                "raw_untagged_support_nodes": untagged_nodes,
                "geojson_features": len(features),
                "geojson_point_features": geometry_types["Point"],
                "geojson_line_features": geometry_types["LineString"],
                "geojson_polygon_features": geometry_types["Polygon"] + geometry_types["MultiPolygon"],
                "geojson_untagged_support_points": support_points,
                "mask_count": len(mask_paths),
                "nonempty_mask_count": nonempty_masks,
                "occupied_pixel_fraction": nonempty_pixels / total_pixels if total_pixels else 0.0,
            }
        )
    id_counts = Counter(all_feature_ids)
    bbox = meta["bbox"]
    bbox_points = [
        (bbox["min_lat"], bbox["min_lon"]),
        (bbox["max_lat"], bbox["max_lon"]),
    ]
    return (
        {
            "meta": meta,
            "bbox_local_dimensions": bbox_summary(bbox_points),
            "total_category_feature_records": sum(int(row["geojson_features"]) for row in rows),
            "total_untagged_support_point_records": sum(int(row["geojson_untagged_support_points"]) for row in rows),
            "unique_feature_ids_across_categories": len(id_counts),
            "feature_ids_appearing_in_multiple_categories": sum(count > 1 for count in id_counts.values()),
            "maximum_category_multiplicity": max(id_counts.values(), default=0),
            "realised_mask_centres": int(meta["satellite_count"]),
        },
        rows,
    )


def compare_alias_directories() -> list[dict[str, object]]:
    pairs = [
        ("panorama", "*.jpg"),
        ("satellite", "*.png"),
        ("pano_sky_mask", "*.png"),
        ("sat_depth", "*.png"),
    ]
    london = REPO / "london_vigor_root" / "London"
    seattle = REPO / "vigor_sat3dgen_root" / "Seattle"
    rows: list[dict[str, object]] = []
    for subdir, pattern in pairs:
        left = {path.name: path for path in (london / subdir).glob(pattern)}
        right = {path.name: path for path in (seattle / subdir).glob(pattern)}
        common = set(left) & set(right)
        exact = 0
        for name in common:
            if left[name].stat().st_size == right[name].stat().st_size and sha256(left[name]) == sha256(right[name]):
                exact += 1
        rows.append(
            {
                "channel": subdir,
                "london_files": len(left),
                "seattle_files": len(right),
                "common_names": len(common),
                "byte_identical_common_files": exact,
                "london_only": len(set(left) - set(right)),
                "seattle_only": len(set(right) - set(left)),
            }
        )
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    input_geojson = json.loads((REPO / "london_highways_500x500.geojson").read_text(encoding="utf-8"))
    input_features = input_geojson["features"]
    input_points = [
        (float(feature["geometry"]["coordinates"][1]), float(feature["geometry"]["coordinates"][0]))
        for feature in input_features
    ]

    legacy_matches: dict[str, tuple[float, float]] = {}
    for line in read_nonempty(REPO / "pano_label_balanced.txt"):
        header = line.split()[0].split(",")
        legacy_matches[Path(header[0]).stem] = (float(header[1]), float(header[2]))

    splits = {
        "train": parse_split(REPO / "london_train.txt"),
        "validation": parse_split(REPO / "london_val.txt"),
        "test": parse_split(REPO / "london_test.txt"),
    }
    split_by_id = {pano_id: split for split, records in splits.items() for pano_id in records}
    matched_panos = [
        {"pano_id": pano_id, "lat": lat, "lon": lon, "split": split_by_id.get(pano_id, "")}
        for pano_id, (lat, lon) in legacy_matches.items()
    ]

    matched_ids = {int(pano_id.split("_")[1]) for pano_id in legacy_matches}
    unmatched_indices = sorted(set(range(len(input_features))) - matched_ids)
    coordinate_errors_m: list[float] = []
    spatial_rows: list[dict[str, object]] = []
    for index, (lat, lon) in enumerate(input_points):
        pano_id = f"pano_{index}"
        match = legacy_matches.get(pano_id)
        if match:
            error = math.hypot(
                (match[0] - lat) * 111320.0,
                (match[1] - lon) * 111320.0 * math.cos(math.radians(lat)),
            )
            coordinate_errors_m.append(error)
        spatial_rows.append(
            {
                "source_index": index,
                "source_id": input_features[index].get("id", ""),
                "latitude": lat,
                "longitude": lon,
                "matched": match is not None,
                "pano_id": pano_id if match else "",
                "split": split_by_id.get(pano_id, ""),
            }
        )

    queries, candidate_rows = parse_four_candidate_labels(
        REPO / "LondonDataSet" / "London" / "pano_label_balanced.txt"
    )

    image_reports: dict[str, object] = {}
    image_reports["legacy_panorama"], legacy_hashes = image_inventory(REPO / "panorama", "*.jpg", True)
    image_reports["equirectangular_panorama"], equirect_hashes = image_inventory(
        REPO / "LondonDataSet" / "London" / "panorama", "*.jpg", True
    )
    for key, directory, pattern, hashes in [
        ("sparse_satellite", REPO / "satellite", "*.png", True),
        ("filled_satellite", REPO / "LondonDataSet" / "London" / "satellite", "*.png", True),
        ("overlap50_satellite", REPO / "LondonDataSet" / "London" / "satellite_overlap50", "*.png", False),
        ("overlap10_satellite", REPO / "LondonDataSet" / "London" / "satellite_overlap10", "*.png", False),
        ("equirect_sky_mask", REPO / "LondonDataSet" / "London" / "pano_sky_mask", "*.png", False),
        ("filled_model_depth", REPO / "LondonDataSet" / "London" / "sat_depth", "*.png", False),
        ("sparse_dsm_raster_depth", REPO / "london_vigor_root" / "London" / "sat_depth_dsm_raster", "*.png", False),
    ]:
        image_reports[key], _ = image_inventory(directory, pattern, hashes)

    duplicate_summary, duplicate_rows, leakage_rows = duplicate_and_leakage(equirect_hashes, splits)

    grid_specs = [
        ("sparse_127", REPO / "satellite"),
        ("filled_272", REPO / "LondonDataSet" / "London" / "satellite"),
        ("overlap50_990", REPO / "LondonDataSet" / "London" / "satellite_overlap50"),
        ("overlap10_306", REPO / "LondonDataSet" / "London" / "satellite_overlap10"),
    ]
    grid_reports: list[dict[str, object]] = []
    satellite_rows: list[dict[str, object]] = []
    grid_centres: dict[str, list[dict[str, object]]] = {}
    for name, directory in grid_specs:
        report, rows = grid_summary(name, directory, matched_panos)
        grid_reports.append(report)
        satellite_rows.extend(rows)
        grid_centres[name] = parse_satellite_centres(directory)

    dsm_report, dsm_rows = dsm_summary(input_points, grid_centres["filled_272"])
    osm_report, osm_rows = osm_summary()
    alias_rows = compare_alias_directories()

    candidate_rank_rows: list[dict[str, object]] = []
    for rank in range(1, 5):
        ranked = [row for row in candidate_rows if int(row["rank"]) == rank]
        outside = sum(not bool(row["inside_640px_tile"]) for row in ranked)
        candidate_rank_rows.append(
            {
                "rank": rank,
                "candidate_count": len(ranked),
                "inside_count": len(ranked) - outside,
                "outside_count": outside,
                "outside_percent": outside / len(ranked) * 100,
                "max_absolute_offset_px": max(
                    max(abs(float(row["dx_px"])), abs(float(row["dy_px"]))) for row in ranked
                ),
            }
        )
    containing_distribution = Counter(int(query["containing_candidate_count"]) for query in queries)

    strict_train_ids = {pano_id_from_split_line(line) for line in read_nonempty(REPO / "LondonDataSet" / "London" / "same_area_balanced_train.txt")}
    strict_test_ids = {pano_id_from_split_line(line) for line in read_nonempty(REPO / "LondonDataSet" / "London" / "same_area_balanced_test.txt")}
    four_candidate_ids = {str(query["pano_id"]) for query in queries}

    all_files = [path for path in REPO.rglob("*") if path.is_file() and ".git" not in path.parts]
    untracked_files = git("ls-files", "--others", "--exclude-standard").splitlines()
    tracked_status = git("status", "--porcelain=v1", "--untracked-files=no")

    report = {
        "audit": {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "repository": str(REPO),
            "head": git("rev-parse", "HEAD"),
            "origin_main_local_ref": git("rev-parse", "origin/main"),
            "branch": git("branch", "--show-current"),
            "tracked_worktree_changes": 0 if not tracked_status else len(tracked_status.splitlines()),
            "untracked_file_count": len(untracked_files),
            "newest_non_git_file_utc": datetime.fromtimestamp(
                max(path.stat().st_mtime for path in all_files), tz=timezone.utc
            ).isoformat(),
            "non_git_file_count": len(all_files),
            "non_git_total_bytes": sum(path.stat().st_size for path in all_files),
        },
        "input_and_matching": {
            "input_features": len(input_features),
            "geometry_types": dict(Counter(feature["geometry"]["type"] for feature in input_features)),
            "unique_input_coordinates": len(set(input_points)),
            "input_bbox": bbox_summary(input_points),
            "matched_panoramas": len(legacy_matches),
            "match_percent": len(legacy_matches) / len(input_features) * 100,
            "unmatched_count": len(unmatched_indices),
            "unmatched_source_indices": unmatched_indices,
            "max_rounding_coordinate_error_m": max(coordinate_errors_m),
        },
        "splits": {
            "counts": {name: len(records) for name, records in splits.items()},
            "fractions": {name: len(records) / len(legacy_matches) for name, records in splits.items()},
            "id_union_count": len(set().union(*(set(records) for records in splits.values()))),
            "pairwise_id_overlaps": {
                "train_validation": len(set(splits["train"]) & set(splits["validation"])),
                "train_test": len(set(splits["train"]) & set(splits["test"])),
                "validation_test": len(set(splits["validation"]) & set(splits["test"])),
            },
            "latitude_ranges": {
                name: {
                    "min": min(lat for lat, _ in records.values()),
                    "max": max(lat for lat, _ in records.values()),
                    "mean": statistics.fmean(lat for lat, _ in records.values()),
                }
                for name, records in splits.items()
            },
            "four_candidate_full_ids": len(four_candidate_ids),
            "four_candidate_train_ids": len(strict_train_ids),
            "four_candidate_test_ids": len(strict_test_ids),
            "four_candidate_validation_ids_exported": len(set(splits["validation"]) & (strict_train_ids | strict_test_ids)),
            "four_candidate_ids_not_in_train_or_test_exports": len(four_candidate_ids - strict_train_ids - strict_test_ids),
        },
        "images": image_reports,
        "panorama_duplicates_and_leakage": {
            **duplicate_summary,
            "legacy_panorama_unique_hashes": len(set(legacy_hashes.values())),
            "pairwise_split_leakage": leakage_rows,
        },
        "four_candidate_geometry": {
            "query_count": len(queries),
            "candidate_count": len(candidate_rows),
            "inside_count": sum(bool(row["inside_640px_tile"]) for row in candidate_rows),
            "outside_count": sum(not bool(row["inside_640px_tile"]) for row in candidate_rows),
            "outside_percent": sum(not bool(row["inside_640px_tile"]) for row in candidate_rows) / len(candidate_rows) * 100,
            "maximum_absolute_offset_px": max(
                max(abs(float(row["dx_px"])), abs(float(row["dy_px"]))) for row in candidate_rows
            ),
            "containing_candidate_count_distribution": dict(sorted(containing_distribution.items())),
            "by_rank": candidate_rank_rows,
            "all_referenced_satellites_exist": all(
                (REPO / "LondonDataSet" / "London" / "satellite" / str(row["satellite"])).exists()
                for row in candidate_rows
            ),
        },
        "satellite_grids": grid_reports,
        "dsm": dsm_report,
        "osm": osm_report,
        "london_data_under_seattle_alias": alias_rows,
        "coverage_alignment": {
            "filled_base_satellite_tiles": len(grid_centres["filled_272"]),
            "sparse_satellite_tiles": len(grid_centres["sparse_127"]),
            "sparse_fraction_of_filled_grid": len(grid_centres["sparse_127"]) / len(grid_centres["filled_272"]),
            "real_dsm_raster_patches": len(dsm_rows),
            "osm_mask_tiles_per_category": min(int(row["mask_count"]) for row in osm_rows),
            "panorama_and_sky_mask_pairs": int(image_reports["equirect_sky_mask"]["count"]),
            "note": "DSM raster patches and OSM masks align to the 127-tile sparse branch, not the 272-tile filled base grid or the overlap grids.",
        },
    }

    (OUT / "data_builder_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_csv("spatial_points.csv", spatial_rows)
    write_csv("satellite_centres.csv", satellite_rows)
    write_csv("label_candidates.csv", candidate_rows)
    write_csv("label_candidate_rank_summary.csv", candidate_rank_rows)
    write_csv("panorama_duplicate_groups.csv", duplicate_rows)
    write_csv("split_hash_leakage.csv", leakage_rows)
    write_csv("dsm_patch_validity.csv", dsm_rows)
    write_csv("osm_category_summary.csv", osm_rows)
    write_csv("london_seattle_alias_comparison.csv", alias_rows)
    print(OUT / "data_builder_audit.json")


if __name__ == "__main__":
    main()
