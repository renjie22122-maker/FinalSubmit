"""Generate publication-ready plan and oblique views of the audited London DSM.

The script reads the four Environment Agency GeoTIFFs and the centres of the
10%-overlap satellite grid from the authoritative nested data_builder snapshot.
It never reads or embeds the satellite images themselves.  Nodata remains
masked; the figure does not interpolate missing source coverage.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib import cm
from matplotlib.colors import Normalize
from matplotlib.ticker import FuncFormatter, MaxNLocator
from pyproj import Transformer
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject


WORKSPACE = Path(__file__).resolve().parents[2]
SATELLITE_DIR = WORKSPACE / "external" / "data" / "satellite"
DSM_DIR = WORKSPACE / "external" / "data" / "dsm"
FIGURE_DIR = WORKSPACE / "figures" / "generated"
SUMMARY_PATH = (
    WORKSPACE / "research" / "results" / "update_data_builder" / "dsm_views_summary.json"
)

SATELLITE_PATTERN = re.compile(
    r"(?:sat|satellite)_([0-9.\-]+)_([0-9.\-]+)\.png$", re.IGNORECASE
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--satellite-dir", type=Path, default=SATELLITE_DIR)
    parser.add_argument("--dsm-dir", type=Path, default=DSM_DIR)
    parser.add_argument("--figure-dir", type=Path, default=FIGURE_DIR)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--grid-width", type=int, default=720)
    parser.add_argument("--zoom", type=int, default=20)
    parser.add_argument("--tile-size", type=int, default=640)
    parser.add_argument("--vertical-exaggeration", type=float, default=3.0)
    return parser.parse_args()


def satellite_bounds(
    satellite_dir: Path, zoom: int, tile_size: int
) -> tuple[float, float, float, float, int]:
    """Return the grid's full WGS84 extent and number of coordinate-stem tiles."""
    centres: list[tuple[float, float]] = []
    for path in sorted(satellite_dir.glob("*.png")):
        match = SATELLITE_PATTERN.fullmatch(path.name)
        if match:
            centres.append((float(match.group(1)), float(match.group(2))))
    if not centres:
        raise RuntimeError(f"No coordinate-stem PNG files found in {satellite_dir}")

    lats = np.asarray([item[0] for item in centres], dtype=float)
    lons = np.asarray([item[1] for item in centres], dtype=float)
    centre_lat = float((lats.min() + lats.max()) / 2.0)
    metres_per_pixel = 156543.03392 * math.cos(math.radians(centre_lat)) / (2**zoom)
    half_metres = tile_size * metres_per_pixel / 2.0
    half_lat = half_metres / 111320.0
    half_lon = half_metres / (111320.0 * math.cos(math.radians(centre_lat)))
    return (
        float(lons.min() - half_lon),
        float(lats.min() - half_lat),
        float(lons.max() + half_lon),
        float(lats.max() + half_lat),
        len(centres),
    )


def projected_bounds(
    lonlat_bounds: tuple[float, float, float, float], transformer: Transformer
) -> tuple[float, float, float, float]:
    min_lon, min_lat, max_lon, max_lat = lonlat_bounds
    lons = [min_lon, max_lon, min_lon, max_lon]
    lats = [min_lat, min_lat, max_lat, max_lat]
    eastings, northings = transformer.transform(lons, lats)
    return (
        float(min(eastings)),
        float(min(northings)),
        float(max(eastings)),
        float(max(northings)),
    )


def sample_mosaic(
    paths: list[Path], bounds: tuple[float, float, float, float], width: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, object]]:
    left, bottom, right, top = bounds
    height = max(1, round(width * (top - bottom) / (right - left)))
    transform = from_bounds(left, bottom, right, top, width, height)
    mosaic = np.full((height, width), np.nan, dtype=np.float32)
    source_details: list[dict[str, object]] = []

    for path in paths:
        with rasterio.open(path) as source:
            if source.crs is None or source.crs.to_epsg() != 27700:
                raise ValueError(f"Expected EPSG:27700 DSM source: {path} ({source.crs})")
            patch = np.full_like(mosaic, np.nan)
            reproject(
                source=rasterio.band(source, 1),
                destination=patch,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source.nodata,
                dst_transform=transform,
                dst_crs=source.crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
                init_dest_nodata=True,
            )
            fill = ~np.isfinite(mosaic) & np.isfinite(patch)
            mosaic[fill] = patch[fill]
            source_details.append(
                {
                    "file": path.name,
                    "crs": source.crs.to_string(),
                    "resolution_m": [float(source.res[0]), float(source.res[1])],
                    "nodata": float(source.nodata) if source.nodata is not None else None,
                    "filled_render_cells": int(fill.sum()),
                }
            )

    eastings = np.linspace(left, right, width, dtype=float)
    northings = np.linspace(top, bottom, height, dtype=float)
    metadata: dict[str, object] = {
        "render_width": width,
        "render_height": height,
        "projected_bounds_epsg27700": [left, bottom, right, top],
        "source_tiles": source_details,
    }
    return mosaic, eastings, northings, metadata


def add_scale_bar(ax: plt.Axes, eastings: np.ndarray, northings: np.ndarray) -> None:
    length = 200.0
    x0 = float(eastings.min() + 0.06 * np.ptp(eastings))
    y0 = float(northings.min() + 0.06 * np.ptp(northings))
    ax.plot([x0, x0 + length], [y0, y0], color="black", linewidth=2.2, zorder=5)
    ax.plot([x0, x0], [y0 - 8, y0 + 8], color="black", linewidth=1.3, zorder=5)
    ax.plot(
        [x0 + length, x0 + length], [y0 - 8, y0 + 8], color="black", linewidth=1.3, zorder=5
    )
    ax.text(x0 + length / 2, y0 + 18, "200 m", ha="center", va="bottom", fontsize=8)


def make_figure(
    elevation: np.ndarray,
    eastings: np.ndarray,
    northings: np.ndarray,
    vertical_exaggeration: float,
) -> plt.Figure:
    valid = np.isfinite(elevation)
    if not valid.any():
        raise RuntimeError("The requested area contains no valid DSM samples")

    minimum = float(np.nanmin(elevation))
    maximum = float(np.nanmax(elevation))
    norm = Normalize(vmin=minimum, vmax=maximum)
    cmap = mpl.colormaps["terrain"].copy()
    cmap.set_bad("white", alpha=0.0)

    fig = plt.figure(figsize=(11.6, 5.15), constrained_layout=True)
    grid = fig.add_gridspec(1, 3, width_ratios=[1.02, 1.15, 0.045], wspace=0.10)
    plan = fig.add_subplot(grid[0, 0])
    oblique = fig.add_subplot(grid[0, 1], projection="3d")
    color_axis = fig.add_subplot(grid[0, 2])

    plan.imshow(
        np.ma.masked_invalid(elevation),
        extent=[eastings.min(), eastings.max(), northings.min(), northings.max()],
        origin="upper",
        cmap=cmap,
        norm=norm,
        interpolation="bilinear",
        rasterized=True,
    )
    plan.set_aspect("equal", adjustable="box")
    plan.set_title("(a) North-up DSM", loc="left", fontweight="bold", fontsize=10)
    plan.set_xlabel("Easting (m, EPSG:27700)")
    plan.set_ylabel("Northing (m, EPSG:27700)")
    plan.xaxis.set_major_locator(MaxNLocator(5))
    plan.yaxis.set_major_locator(MaxNLocator(5))
    comma = FuncFormatter(lambda value, _position: f"{value:,.0f}")
    plan.xaxis.set_major_formatter(comma)
    plan.yaxis.set_major_formatter(comma)
    plan.tick_params(labelsize=8)
    add_scale_bar(plan, eastings, northings)
    plan.text(
        0.965,
        0.965,
        "N",
        transform=plan.transAxes,
        ha="center",
        va="top",
        fontsize=9,
        fontweight="bold",
    )
    plan.annotate(
        "",
        xy=(0.965, 0.91),
        xytext=(0.965, 0.82),
        xycoords="axes fraction",
        arrowprops={"arrowstyle": "-|>", "color": "black", "linewidth": 1.1},
    )

    stride = max(1, int(math.ceil(max(elevation.shape) / 260)))
    x_grid, y_grid = np.meshgrid(eastings[::stride], northings[::stride])
    z_grid = elevation[::stride, ::stride]
    face_colours = cmap(norm(z_grid))
    face_colours[~np.isfinite(z_grid)] = (1.0, 1.0, 1.0, 0.0)
    oblique.plot_surface(
        x_grid,
        y_grid,
        np.ma.masked_invalid(z_grid),
        facecolors=face_colours,
        linewidth=0,
        antialiased=False,
        shade=True,
        rasterized=True,
    )
    oblique.view_init(elev=32, azim=-58)
    width = float(np.ptp(eastings))
    depth = float(np.ptp(northings))
    relief = max(maximum - minimum, 1.0)
    oblique.set_box_aspect((width, depth, vertical_exaggeration * relief))
    oblique.set_title("(b) Oblique DSM", loc="left", fontweight="bold", fontsize=10)
    oblique.set_xlabel("Easting (m)", labelpad=7)
    oblique.set_ylabel("Northing (m)", labelpad=7)
    oblique.set_zlim(minimum, maximum)
    oblique.xaxis.set_major_locator(MaxNLocator(4))
    oblique.yaxis.set_major_locator(MaxNLocator(4))
    oblique.zaxis.set_major_locator(MaxNLocator(5))
    oblique.xaxis.set_major_formatter(comma)
    oblique.yaxis.set_major_formatter(comma)
    oblique.tick_params(labelsize=7, pad=0)
    display_spacing = max(
        abs(float(eastings[1] - eastings[0])),
        abs(float(northings[1] - northings[0])),
    ) * stride
    oblique.text2D(
        0.03,
        0.91,
        (
            f"{vertical_exaggeration:g}x vertical exaggeration\n"
            f"Source pixels: 1 m; display mesh: {display_spacing:.1f} m"
        ),
        transform=oblique.transAxes,
        fontsize=8,
    )

    mappable = cm.ScalarMappable(norm=norm, cmap=cmap)
    mappable.set_array(elevation)
    colorbar = fig.colorbar(mappable, cax=color_axis)
    colorbar.set_label("Surface elevation (m ODN)")
    colorbar.ax.tick_params(labelsize=8)
    return fig


def main() -> None:
    args = parse_args()
    if args.grid_width < 100:
        raise ValueError("--grid-width must be at least 100")
    if args.vertical_exaggeration <= 0:
        raise ValueError("--vertical-exaggeration must be positive")

    lonlat_bounds = satellite_bounds(args.satellite_dir, args.zoom, args.tile_size)
    min_lon, min_lat, max_lon, max_lat, satellite_count = lonlat_bounds
    dsm_paths = sorted(args.dsm_dir.glob("*.tif"))
    if not dsm_paths:
        raise FileNotFoundError(f"No DSM GeoTIFFs found in {args.dsm_dir}")

    with rasterio.open(dsm_paths[0]) as source:
        if source.crs is None:
            raise ValueError(f"DSM has no CRS: {dsm_paths[0]}")
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
    bounds_27700 = projected_bounds(
        (min_lon, min_lat, max_lon, max_lat), transformer
    )
    elevation, eastings, northings, metadata = sample_mosaic(
        dsm_paths, bounds_27700, args.grid_width
    )
    valid = np.isfinite(elevation)
    valid_values = elevation[valid]
    metadata.update(
        {
            "satellite_coordinate_stem_count": satellite_count,
            "satellite_grid_lonlat_bounds": [min_lon, min_lat, max_lon, max_lat],
            "valid_render_cells": int(valid.sum()),
            "total_render_cells": int(valid.size),
            "valid_ratio": float(valid.mean()),
            "elevation_m": {
                "minimum": float(valid_values.min()),
                "maximum": float(valid_values.max()),
                "mean": float(valid_values.mean()),
                "p05": float(np.percentile(valid_values, 5)),
                "p95": float(np.percentile(valid_values, 95)),
            },
            "vertical_exaggeration": float(args.vertical_exaggeration),
            "oblique_display_mesh_spacing_m": float(
                max(
                    abs(float(eastings[1] - eastings[0])),
                    abs(float(northings[1] - northings[0])),
                )
                * max(1, int(math.ceil(max(elevation.shape) / 260)))
            ),
            "figure_statement": (
                "Qualitative source-DSM morphology; not a patch-completeness or "
                "building-height accuracy measurement."
            ),
        }
    )

    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.labelsize": 9,
            "axes.titlesize": 10,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    figure = make_figure(
        elevation, eastings, northings, float(args.vertical_exaggeration)
    )
    args.figure_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = args.figure_dir / "data_builder_dsm_views.pdf"
    png_path = args.figure_dir / "data_builder_dsm_views.png"
    figure.savefig(pdf_path, bbox_inches="tight")
    figure.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(figure)

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps({"pdf": str(pdf_path), "png": str(png_path), **metadata}, indent=2))


if __name__ == "__main__":
    main()
