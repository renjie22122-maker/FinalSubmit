from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Sequence


class DsmFootprintError(RuntimeError):
    """Raised when DSM data cannot be converted into candidate footprints."""


def _dependencies():
    try:
        import numpy as np
        import rasterio
        from pyproj import Transformer
        from rasterio.features import shapes
        from rasterio.merge import merge
        from rasterio.warp import Resampling, reproject, transform_bounds
        from scipy import ndimage
        from shapely.geometry import box, mapping, shape
        from shapely.ops import transform
    except ImportError as exc:  # pragma: no cover - depends on the existing conda env
        raise DsmFootprintError(
            "DSM mode needs numpy, rasterio, scipy, shapely and pyproj in the existing "
            "sat3dgen environment; no package was installed or changed"
        ) from exc
    return {
        "np": np,
        "rasterio": rasterio,
        "Transformer": Transformer,
        "shapes": shapes,
        "merge": merge,
        "Resampling": Resampling,
        "reproject": reproject,
        "transform_bounds": transform_bounds,
        "ndimage": ndimage,
        "box": box,
        "mapping": mapping,
        "shape": shape,
        "transform": transform,
    }


def _normalise_paths(paths: Iterable[str | Path], label: str) -> list[Path]:
    resolved = [Path(path).resolve() for path in paths]
    if not resolved:
        raise DsmFootprintError(f"at least one {label} raster is required")
    missing = [str(path) for path in resolved if not path.is_file()]
    if missing:
        raise DsmFootprintError(f"missing {label} raster(s): " + ", ".join(missing))
    return resolved


def _open_and_merge(paths, bounds_wgs84, deps):
    rasterio = deps["rasterio"]
    datasets = [rasterio.open(path) for path in paths]
    try:
        crs = datasets[0].crs
        if crs is None:
            raise DsmFootprintError(f"raster has no CRS: {paths[0]}")
        for dataset in datasets[1:]:
            if dataset.crs != crs:
                raise DsmFootprintError("all DSM/DTM rasters in one set must use the same CRS")
        min_lon, min_lat, max_lon, max_lat = bounds_wgs84
        projected = deps["transform_bounds"](
            "EPSG:4326", crs, min_lon, min_lat, max_lon, max_lat, densify_pts=21
        )
        array, affine = deps["merge"](
            datasets, bounds=projected, indexes=[1], masked=True
        )
        return array[0], affine, crs
    finally:
        for dataset in datasets:
            dataset.close()


def _estimate_ground(dsm, valid, pixel_size: float, window_metres: float, deps):
    np = deps["np"]
    ndimage = deps["ndimage"]
    valid_values = dsm[valid]
    if not valid_values.size:
        raise DsmFootprintError("DSM crop contains no valid elevation samples")
    fill = float(np.median(valid_values))
    working = np.where(valid, dsm, fill)
    size = max(3, int(round(window_metres / max(pixel_size, 1e-6))))
    if size % 2 == 0:
        size += 1
    return ndimage.percentile_filter(working, percentile=15, size=size, mode="nearest")


def _clean_mask(mask, pixel_area: float, minimum_area_m2: float, deps):
    np = deps["np"]
    ndimage = deps["ndimage"]
    structure = ndimage.generate_binary_structure(2, 2)
    cleaned = ndimage.binary_opening(mask, structure=structure, iterations=1)
    cleaned = ndimage.binary_closing(cleaned, structure=structure, iterations=1)
    cleaned = ndimage.binary_fill_holes(cleaned)
    labels, count = ndimage.label(cleaned, structure=structure)
    if count == 0:
        return cleaned
    counts = np.bincount(labels.ravel())
    keep = counts * pixel_area >= minimum_area_m2
    keep[0] = False
    return keep[labels]


def extract_dsm_footprints(
    dsm_paths: Sequence[str | Path],
    bbox_wgs84: Sequence[float],
    output_geojson: str | Path,
    *,
    dtm_paths: Sequence[str | Path] | None = None,
    minimum_height_m: float = 3.0,
    minimum_area_m2: float = 20.0,
    ground_window_m: float = 35.0,
    simplify_m: float = 0.5,
) -> dict:
    """Extract candidate building polygons from DSM/DTM elevation rasters.

    A DTM produces an nDSM and is the preferred path. Without a DTM a local
    low-percentile surface is estimated; that result is deliberately marked
    heuristic because trees and elevated infrastructure can remain.
    """

    if len(bbox_wgs84) != 4:
        raise DsmFootprintError("bbox must be [min_lon, min_lat, max_lon, max_lat]")
    bounds = tuple(float(value) for value in bbox_wgs84)
    if not (bounds[0] < bounds[2] and bounds[1] < bounds[3]):
        raise DsmFootprintError("bbox interval is invalid")
    if minimum_height_m <= 0 or minimum_area_m2 <= 0:
        raise DsmFootprintError("minimum height and area must be positive")

    dsm_files = _normalise_paths(dsm_paths, "DSM")
    dtm_files = _normalise_paths(dtm_paths, "DTM") if dtm_paths else []
    deps = _dependencies()
    np = deps["np"]
    dsm, affine, crs = _open_and_merge(dsm_files, bounds, deps)
    dsm_values = np.asarray(dsm.filled(np.nan), dtype="float32")
    valid = np.isfinite(dsm_values)
    pixel_x = abs(float(affine.a))
    pixel_y = abs(float(affine.e))
    pixel_area = pixel_x * pixel_y

    warnings: list[str] = []
    if dtm_files:
        dtm, dtm_affine, dtm_crs = _open_and_merge(dtm_files, bounds, deps)
        dtm_values = np.asarray(dtm.filled(np.nan), dtype="float32")
        if dtm_crs != crs or dtm_values.shape != dsm_values.shape or dtm_affine != affine:
            aligned = np.full(dsm_values.shape, np.nan, dtype="float32")
            deps["reproject"](
                source=dtm_values,
                destination=aligned,
                src_transform=dtm_affine,
                src_crs=dtm_crs,
                dst_transform=affine,
                dst_crs=crs,
                resampling=deps["Resampling"].bilinear,
                src_nodata=np.nan,
                dst_nodata=np.nan,
            )
            dtm_values = aligned
        valid &= np.isfinite(dtm_values)
        height = dsm_values - dtm_values
        quality = "ndsm"
    else:
        ground = _estimate_ground(dsm_values, valid, pixel_x, ground_window_m, deps)
        height = dsm_values - ground
        quality = "heuristic_without_dtm"
        warnings.append(
            "No DTM was supplied: local ground was estimated from the DSM; trees may be included."
        )

    mask = valid & np.isfinite(height) & (height >= minimum_height_m)
    mask = _clean_mask(mask, pixel_area, minimum_area_m2, deps)

    transformer = deps["Transformer"].from_crs(crs, "EPSG:4326", always_xy=True)
    to_wgs84 = lambda x, y, z=None: transformer.transform(x, y)
    target = deps["box"](*bounds)
    features = []
    for geometry, value in deps["shapes"](
        mask.astype("uint8"), mask=mask, transform=affine
    ):
        if not value:
            continue
        polygon = deps["shape"](geometry)
        if simplify_m:
            polygon = polygon.simplify(simplify_m, preserve_topology=True)
        polygon = deps["transform"](to_wgs84, polygon).intersection(target)
        if polygon.is_empty or not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            continue
        candidates = [polygon] if polygon.geom_type == "Polygon" else list(polygon.geoms)
        for candidate in candidates:
            if candidate.geom_type != "Polygon" or candidate.is_empty:
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "source": "dsm",
                        "quality": quality,
                        "minimum_height_m": minimum_height_m,
                    },
                    "geometry": deps["mapping"](candidate),
                }
            )

    output = Path(output_geojson).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "type": "FeatureCollection",
        "name": "dsm_candidate_buildings",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "mode": "dsm",
        "quality": quality,
        "output_geojson": str(output),
        "feature_count": len(features),
        "pixel_size_m": [pixel_x, pixel_y],
        "thresholds": {
            "minimum_height_m": minimum_height_m,
            "minimum_area_m2": minimum_area_m2,
            "ground_window_m": ground_window_m,
        },
        "warnings": warnings,
    }

