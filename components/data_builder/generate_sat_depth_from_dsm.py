from __future__ import annotations

from pathlib import Path
import argparse
import math
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
from pyproj import Transformer


def parse_sat_name(name: str) -> Tuple[float, float]:
    stem = name.removeprefix("sat_").removesuffix(".png")
    lat_s, lon_s = stem.split("_", 1)
    return float(lat_s), float(lon_s)


def meters_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)


def sat_bbox_lonlat(center_lat: float, center_lon: float, size: int, zoom: int) -> Tuple[float, float, float, float]:
    mpp = meters_per_pixel(center_lat, zoom)
    total_m = size * mpp
    half_m = total_m / 2.0

    lat_half_deg = half_m / 111320.0
    lon_half_deg = half_m / (111320.0 * max(math.cos(math.radians(center_lat)), 1e-9))

    min_lon = center_lon - lon_half_deg
    max_lon = center_lon + lon_half_deg
    min_lat = center_lat - lat_half_deg
    max_lat = center_lat + lat_half_deg
    return min_lon, min_lat, max_lon, max_lat


def choose_dataset(lat: float, lon: float, datasets: List[rasterio.io.DatasetReader], transformer: Transformer):
    x, y = transformer.transform(lon, lat)
    for ds in datasets:
        b = ds.bounds
        if b.left <= x <= b.right and b.bottom <= y <= b.top:
            return ds
    return None


def sample_patch_from_dsm(
    ds: rasterio.io.DatasetReader,
    lonlat_bbox: Tuple[float, float, float, float],
    size: int,
    transformer: Transformer,
) -> np.ndarray:
    min_lon, min_lat, max_lon, max_lat = lonlat_bbox

    x1, y1 = transformer.transform(min_lon, min_lat)
    x2, y2 = transformer.transform(max_lon, max_lat)
    min_x, max_x = min(x1, x2), max(x1, x2)
    min_y, max_y = min(y1, y2), max(y1, y2)

    dst = np.full((size, size), np.nan, dtype=np.float32)
    dst_transform = from_bounds(min_x, min_y, max_x, max_y, size, size)

    reproject(
        source=rasterio.band(ds, 1),
        destination=dst,
        src_transform=ds.transform,
        src_crs=ds.crs,
        src_nodata=ds.nodata,
        dst_transform=dst_transform,
        dst_crs=ds.crs,
        dst_nodata=np.nan,
        resampling=Resampling.bilinear,
    )
    return dst


def normalize_to_depth(sampled: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    valid = ~np.isnan(sampled)
    out = np.zeros_like(sampled, dtype=np.uint8)
    if not np.any(valid):
        return out, valid

    vals = sampled[valid]
    p5 = float(np.percentile(vals, 5))
    p95 = float(np.percentile(vals, 95))
    denom = max(p95 - p5, 1e-6)

    norm = (sampled - p5) / denom
    norm = np.clip(norm, 0.0, 1.0)
    norm = np.nan_to_num(norm, nan=0.0)

    # Higher DSM elevation => higher depth value in this auxiliary channel.
    depth = (norm * 255.0).astype(np.uint8)
    out[valid] = depth[valid]
    return out, valid


def run(
    satellite_dir: Path,
    dsm_tif_dir: Path,
    output_dir: Path,
    metadata_csv: Path,
    overwrite: bool,
    zoom: int,
    size: int,
) -> None:
    tif_files = sorted(dsm_tif_dir.glob("*.tif"))
    if not tif_files:
        raise FileNotFoundError(f"No .tif files found in {dsm_tif_dir}")

    datasets = [rasterio.open(p) for p in tif_files]
    if datasets[0].crs is None:
        raise ValueError("DSM raster CRS is missing.")

    transformer = Transformer.from_crs("EPSG:4326", datasets[0].crs, always_xy=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, str]] = []
    generated = 0

    try:
        for sat_path in sorted(satellite_dir.glob("*.png")):
            out_path = output_dir / sat_path.name
            if out_path.exists() and not overwrite:
                continue

            lat, lon = parse_sat_name(sat_path.name)
            ds = choose_dataset(lat, lon, datasets, transformer)

            if ds is None:
                depth = np.zeros((size, size), dtype=np.uint8)
                valid = np.zeros((size, size), dtype=bool)
                valid_vals = np.array([], dtype=np.float32)
            else:
                bbox = sat_bbox_lonlat(lat, lon, size=size, zoom=zoom)
                sampled = sample_patch_from_dsm(ds, bbox, size=size, transformer=transformer)
                depth, valid = normalize_to_depth(sampled)
                valid_vals = sampled[valid]

            Image.fromarray(depth, mode="L").save(out_path)
            generated += 1

            rows.append(
                {
                    "satellite": sat_path.name,
                    "lat": f"{lat:.6f}",
                    "lon": f"{lon:.6f}",
                    "dsm_tile": "" if ds is None else Path(ds.name).name,
                    "valid_ratio": f"{valid.mean():.6f}",
                    "elev_min": "" if valid_vals.size == 0 else f"{float(valid_vals.min()):.3f}",
                    "elev_max": "" if valid_vals.size == 0 else f"{float(valid_vals.max()):.3f}",
                    "elev_mean": "" if valid_vals.size == 0 else f"{float(valid_vals.mean()):.3f}",
                }
            )
    finally:
        for ds in datasets:
            ds.close()

    header = ["satellite", "lat", "lon", "dsm_tile", "valid_ratio", "elev_min", "elev_max", "elev_mean"]
    with metadata_csv.open("w", encoding="utf-8") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(r[k] for k in header) + "\n")

    print(f"DSM tiles used: {len(tif_files)}")
    print(f"Generated {generated} DSM-raster sat_depth maps")
    print(f"Output directory: {output_dir}")
    print(f"Metadata CSV: {metadata_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sat_depth from real DSM GeoTIFF tiles (non-destructive output)")
    parser.add_argument("--satellite-dir", type=Path, default=Path("london_vigor_root/London/satellite"))
    parser.add_argument("--dsm-tif-dir", type=Path, default=Path("dsm_raw_tiles"))
    parser.add_argument("--output-dir", type=Path, default=Path("london_vigor_root/London/sat_depth_dsm_raster"))
    parser.add_argument(
        "--metadata-csv",
        type=Path,
        default=Path("london_vigor_root/London/sat_depth_dsm_raster_metadata.csv"),
    )
    parser.add_argument("--zoom", type=int, default=20)
    parser.add_argument("--size", type=int, default=640)
    parser.add_argument("--overwrite", action="store_true", default=False)
    args = parser.parse_args()

    run(
        satellite_dir=args.satellite_dir.resolve(),
        dsm_tif_dir=args.dsm_tif_dir.resolve(),
        output_dir=args.output_dir.resolve(),
        metadata_csv=args.metadata_csv.resolve(),
        overwrite=args.overwrite,
        zoom=args.zoom,
        size=args.size,
    )


if __name__ == "__main__":
    main()
