"""
3D Visualization of DSM (Digital Surface Model) data
over the satellite image coverage area.

Uses matplotlib for 3D rendering with the DSM as height map
and satellite imagery as texture overlay.
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image
import rasterio
from rasterio.warp import reproject, Resampling
from rasterio.transform import from_bounds
from pyproj import Transformer


SAT_PATTERN = re.compile(r"(?:sat|satellite)_([0-9.\-]+)_([0-9.\-]+)\.png$")


def meters_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)


def sat_bbox_lonlat(center_lat: float, center_lon: float, size: int, zoom: int):
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


def parse_sat_bounds(sat_dir: Path) -> tuple[float, float, float, float]:
    """Get the overall bounding box from satellite tile centers."""
    lats, lons = [], []
    for p in sat_dir.glob("*.png"):
        m = SAT_PATTERN.match(p.name)
        if not m:
            continue
        lats.append(float(m.group(1)))
        lons.append(float(m.group(2)))
    if not lats:
        raise RuntimeError(f"No satellite tiles found in {sat_dir}")
    return min(lats), max(lats), min(lons), max(lons)


def get_overlapping_datasets(
    lonlat_bbox: tuple[float, float, float, float],
    datasets: list,
    transformer: Transformer,
) -> list:
    """Return all DSM datasets that overlap with the given lon/lat bounding box."""
    min_lon, min_lat, max_lon, max_lat = lonlat_bbox
    # Convert bbox corners to projected CRS
    corners_lon = [min_lon, max_lon, min_lon, max_lon]
    corners_lat = [min_lat, min_lat, max_lat, max_lat]
    xs, ys = transformer.transform(corners_lon, corners_lat)
    proj_min_x, proj_max_x = min(xs), max(xs)
    proj_min_y, proj_max_y = min(ys), max(ys)

    overlapping = []
    for ds in datasets:
        b = ds.bounds
        # Check if the two bounding boxes intersect
        if (b.left < proj_max_x and b.right > proj_min_x and
                b.bottom < proj_max_y and b.top > proj_min_y):
            overlapping.append(ds)
    return overlapping


def sample_dsm_mosaic(
    datasets: list,
    lonlat_bbox: tuple[float, float, float, float],
    size: int,
    transformer: Transformer,
) -> np.ndarray:
    """
    Sample DSM data from multiple overlapping tiles and merge into a single
    elevation array. Uses first-encountered valid data (tile order priority)
    to handle overlapping regions.
    """
    min_lon, min_lat, max_lon, max_lat = lonlat_bbox
    x1, y1 = transformer.transform(min_lon, min_lat)
    x2, y2 = transformer.transform(max_lon, max_lat)
    proj_min_x, proj_max_x = min(x1, x2), max(x1, x2)
    proj_min_y, proj_max_y = min(y1, y2), max(y1, y2)

    dst = np.full((size, size), np.nan, dtype=np.float32)
    dst_transform = from_bounds(proj_min_x, proj_min_y, proj_max_x, proj_max_y, size, size)

    for ds in datasets:
        # Only reproject where we still have NaN (unfilled) pixels
        still_nan = np.isnan(dst)
        if not np.any(still_nan):
            break  # fully covered

        patch = np.full((size, size), np.nan, dtype=np.float32)
        reproject(
            source=rasterio.band(ds, 1),
            destination=patch,
            src_transform=ds.transform,
            src_crs=ds.crs,
            src_nodata=ds.nodata,
            dst_transform=dst_transform,
            dst_crs=ds.crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        # Fill NaN gaps in dst with valid data from this patch
        valid_in_patch = ~np.isnan(patch)
        fill_mask = still_nan & valid_in_patch
        dst[fill_mask] = patch[fill_mask]

    return dst


def main() -> None:
    parser = argparse.ArgumentParser(description="3D visualization of DSM with satellite overlay")
    parser.add_argument("--sat-dir", type=Path, default=Path("LondonDataSet/London/satellite_overlap10"))
    parser.add_argument("--dsm-dir", type=Path, default=Path("dsm_raw_tiles"))
    parser.add_argument("--output", type=Path, default=Path("dsm_3d_visualization.png"))
    parser.add_argument("--zoom", type=int, default=20)
    parser.add_argument("--tile-size", type=int, default=640)
    parser.add_argument("--dsm-resolution", type=int, default=500,
                        help="Resolution of the DSM grid for 3D rendering")
    parser.add_argument("--elevation-scale", type=float, default=3.0,
                        help="Vertical exaggeration factor")
    parser.add_argument("--view-angle", type=float, default=25,
                        help="Elevation angle for 3D view (degrees)")
    parser.add_argument("--view-azimuth", type=float, default=-60,
                        help="Azimuth angle for 3D view (degrees)")
    args = parser.parse_args()

    # 1. Get bounding box from satellite tiles
    min_lat, max_lat, min_lon, max_lon = parse_sat_bounds(args.sat_dir.resolve())
    print(f"Satellite bounds: lat=[{min_lat:.6f}, {max_lat:.6f}] lon=[{min_lon:.6f}, {max_lon:.6f}]")

    # Expand bounds slightly to cover full tile extents (not just centers)
    zoom = args.zoom
    tile_size = args.tile_size
    mpp = meters_per_pixel((min_lat + max_lat) / 2, zoom)
    half_deg_lat = (tile_size * mpp / 2) / 111320.0
    half_deg_lon = (tile_size * mpp / 2) / (111320.0 * math.cos(math.radians((min_lat + max_lat) / 2)))
    min_lat -= half_deg_lat
    max_lat += half_deg_lat
    min_lon -= half_deg_lon
    max_lon += half_deg_lon
    print(f"Expanded bounds (with tile extents): lat=[{min_lat:.6f}, {max_lat:.6f}] lon=[{min_lon:.6f}, {max_lon:.6f}]")

    # 2. Open DSM datasets
    dsm_dir = args.dsm_dir.resolve()
    tif_files = sorted(dsm_dir.glob("*.tif"))
    if not tif_files:
        raise FileNotFoundError(f"No .tif files found in {dsm_dir}")
    print(f"DSM tiles: {[p.name for p in tif_files]}")

    datasets = [rasterio.open(p) for p in tif_files]
    transformer = Transformer.from_crs("EPSG:4326", datasets[0].crs, always_xy=True)

    # 3. Sample DSM over the full bounding box, merging all overlapping tiles
    dsm_res = args.dsm_resolution
    full_bbox = (min_lon, min_lat, max_lon, max_lat)

    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2

    # Find all DSM tiles that overlap with the satellite coverage area
    overlapping_ds = get_overlapping_datasets(full_bbox, datasets, transformer)
    if not overlapping_ds:
        raise RuntimeError("No DSM tiles overlap with the satellite coverage area")

    print(f"Overlapping DSM tiles ({len(overlapping_ds)}): {[Path(d.name).name for d in overlapping_ds]}")

    # Merge data from all overlapping tiles
    elevation = sample_dsm_mosaic(overlapping_ds, full_bbox, dsm_res, transformer)

    # Close datasets
    for d in datasets:
        d.close()

    # 4. Fill NaN values
    valid_mask = ~np.isnan(elevation)
    if not np.any(valid_mask):
        raise RuntimeError("No valid DSM data in the satellite coverage area")

    # Interpolate NaN values
    from scipy import interpolate
    x_valid, y_valid = np.where(valid_mask)
    z_valid = elevation[valid_mask]
    x_all, y_all = np.meshgrid(np.arange(dsm_res), np.arange(dsm_res))
    points = np.column_stack((x_valid, y_valid))
    try:
        elevation_filled = interpolate.griddata(
            points, z_valid, (x_all, y_all), method='nearest', fill_value=np.nanmean(z_valid)
        )
    except Exception:
        elevation_filled = np.where(valid_mask, elevation, np.nanmean(z_valid))

    print(f"DSM elevation range: [{np.nanmin(elevation_filled):.2f}m, {np.nanmax(elevation_filled):.2f}m]")
    print(f"DSM elevation mean: {np.nanmean(elevation_filled):.2f}m")

    # 5. Create 3D visualization
    try:
        import matplotlib.pyplot as plt
        from matplotlib import cm
    except ImportError:
        print("matplotlib is required. Install with: pip install matplotlib")
        return

    try:
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    except ImportError:
        pass

    # Create coordinate grids (in meters relative to center)
    center_lat_rad = math.radians(center_lat)
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(center_lat_rad)

    lats_1d = np.linspace(min_lat, max_lat, dsm_res)
    lons_1d = np.linspace(min_lon, max_lon, dsm_res)
    lon_grid, lat_grid = np.meshgrid(lons_1d, lats_1d)

    # Convert to meters from center
    x_m = (lon_grid - center_lon) * m_per_deg_lon
    y_m = (lat_grid - center_lat) * m_per_deg_lat

    # Apply elevation scaling
    elev_scale = args.elevation_scale
    z_scaled = elevation_filled * elev_scale

    # 6. Plot
    fig = plt.figure(figsize=(18, 14))
    ax = fig.add_subplot(111, projection='3d')

    # Create surface plot with colormap based on elevation
    surf = ax.plot_surface(
        x_m, y_m, z_scaled,
        facecolors=cm.terrain((elevation_filled - np.nanmin(elevation_filled)) /
                              max(np.nanmax(elevation_filled) - np.nanmin(elevation_filled), 1e-6)),
        rstride=1, cstride=1,
        antialiased=True,
        shade=True,
        alpha=0.95,
    )

    # Set view angle
    ax.view_init(elev=args.view_angle, azim=args.view_azimuth)

    # Labels
    ax.set_xlabel('East-West (meters)', fontsize=12)
    ax.set_ylabel('North-South (meters)', fontsize=12)
    ax.set_zlabel(f'Elevation (m) × {elev_scale:.1f}x', fontsize=12)
    ax.set_title(
        f'London DSM 3D Visualization\n'
        f'Area: {abs(max_lon - min_lon) * m_per_deg_lon / 1000:.2f}km × '
        f'{abs(max_lat - min_lat) * m_per_deg_lat / 1000:.2f}km\n'
        f'Elevation range: {np.nanmin(elevation_filled):.1f}m - {np.nanmax(elevation_filled):.1f}m',
        fontsize=14, fontweight='bold'
    )

    # Colorbar
    mappable = cm.ScalarMappable(
        cmap=cm.terrain,
        norm=plt.Normalize(vmin=np.nanmin(elevation_filled), vmax=np.nanmax(elevation_filled))
    )
    mappable.set_array(elevation_filled)
    cbar = fig.colorbar(mappable, ax=ax, shrink=0.6, aspect=20, pad=0.1)
    cbar.set_label('Elevation (meters)', fontsize=12)

    # Add text annotation with stats
    stats_text = (
        f"DSM Resolution: 1m\n"
        f"Render Grid: {dsm_res}×{dsm_res}\n"
        f"Vertical Exaggeration: {elev_scale}×\n"
        f"Min Elev: {np.nanmin(elevation_filled):.1f}m\n"
        f"Max Elev: {np.nanmax(elevation_filled):.1f}m\n"
        f"Mean Elev: {np.nanmean(elevation_filled):.1f}m"
    )
    ax.text2D(0.02, 0.98, stats_text, transform=ax.transAxes,
              fontsize=10, verticalalignment='top',
              bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

    # Save
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=200, bbox_inches='tight')
    print(f"\n3D visualization saved to: {output_path}")

    # Also save a top-down view
    output_td = output_path.parent / f"{output_path.stem}_topdown{output_path.suffix}"
    ax.view_init(elev=90, azim=-90)
    plt.savefig(output_td, dpi=200, bbox_inches='tight')
    print(f"Top-down view saved to: {output_td}")

    plt.close()
    print("Done!")


if __name__ == "__main__":
    main()
