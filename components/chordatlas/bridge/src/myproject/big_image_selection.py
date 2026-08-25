"""On-demand Sat3DGen big-image selection adapter.

The big-image network output is a single, already fused surface. This module
therefore performs exactly one geographic transform, a selection crop, the
mandatory DSM correction, and publication. It deliberately does *not* run the
legacy per-tile bottom-face removal, stitching, face deduplication, or
small-component filtering stages.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .selection import (
    BIG_IMAGE_PIPELINE_CONTRACT_VERSION,
    BUILDING_PUBLICATION_VERSION,
    ObjMesh,
    SelectionBridgeError,
    SelectionRequest,
    _atomic_json,
    _commit_ready,
    _configured_mesh_path,
    _configured_path,
    _request_config_document,
    _triangle_near_polygon,
    building_subset_metrics,
    load_selection_request,
    load_source_feature_ids,
    redact_text,
    stage_building_publication,
    write_gis_obj,
    write_obj_mesh,
    write_obj_subset,
)


WEB_MERCATOR_RADIUS_M = 6_378_137.0
WEB_MERCATOR_WORLD_M = 2.0 * math.pi * WEB_MERCATOR_RADIUS_M
BIG_IMAGE_STAGE_ORDER = [
    "big_image_fractional_feather_fusion_upstream",
    "sat3dgen_fractional_feather_vertex_colour",
    "coordinate_transform_once",
    "selection_context_crop",
    "dsm_height_correction",
    "workspace_ground_normalization",
    "publish_per_footprint",
]


@dataclass(frozen=True)
class BigImageSettings:
    zoom: int
    request_size_px: int
    retained_cell_size_px: int
    context_padding_m: float
    image_window_size_px: int
    overlap: float
    mesh_resolution: int
    mesh_level: float
    fusion_mode: str
    preserve_source_pixels: bool
    max_cells: int
    max_windows: int
    download_timeout_s: float
    inference_timeout_s: float
    color_timeout_s: float
    color_batch_size: int
    color_spatial_bin_size: float
    color_model_path: str
    source_crop_padding_m: float
    building_crop_padding_m: float
    ground_percentile: float
    download_script: Path
    inference_script: Path
    colorize_script: Path
    repo_root: Path
    validated_cache_outputs: tuple[Path, ...]


@dataclass(frozen=True)
class BigImagePlan:
    center_lat: float
    center_lon: float
    columns: int
    rows: int
    input_pixel_resolution_m: float
    target_bbox_wgs84: tuple[float, float, float, float]
    mosaic_bbox_wgs84: tuple[float, float, float, float]
    mosaic_size_px: tuple[int, int]
    estimated_window_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "center": [self.center_lat, self.center_lon],
            "grid": [self.columns, self.rows],
            "input_pixel_resolution_m": self.input_pixel_resolution_m,
            "target_bbox_wgs84": list(self.target_bbox_wgs84),
            "mosaic_bbox_wgs84": list(self.mosaic_bbox_wgs84),
            "mosaic_size_px": list(self.mosaic_size_px),
            "estimated_window_count": self.estimated_window_count,
        }


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionBridgeError("big_image_invalid", f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SelectionBridgeError("big_image_invalid", f"{label} must be a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SelectionBridgeError("big_image_config_invalid", f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise SelectionBridgeError("big_image_config_invalid", f"{label} must be finite")
    return result


def _int_setting(raw: Mapping[str, Any], name: str, default: int, minimum: int = 1) -> int:
    value = raw.get(name, default)
    if isinstance(value, bool):
        raise SelectionBridgeError("big_image_config_invalid", f"mesh.big_image.{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise SelectionBridgeError("big_image_config_invalid", f"mesh.big_image.{name} must be an integer") from exc
    if result < minimum:
        raise SelectionBridgeError("big_image_config_invalid", f"mesh.big_image.{name} must be >= {minimum}")
    return result


def _resolve_config_path(value: Any, config_path: Path | None, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise SelectionBridgeError("big_image_config_invalid", f"{label} is required")
    path = Path(value).expanduser()
    if not path.is_absolute() and config_path is not None:
        path = config_path.parent / path
    return path.resolve(strict=False)


def load_big_image_settings(request: SelectionRequest) -> tuple[BigImageSettings, dict[str, Any], Path | None]:
    config, config_path = _request_config_document(request)
    mesh = config.get("mesh")
    if not isinstance(mesh, Mapping):
        raise SelectionBridgeError("big_image_config_invalid", "workspace source config has no mesh object")
    raw = mesh.get("big_image", {})
    if not isinstance(raw, Mapping) or raw.get("enabled", True) is not True:
        raise SelectionBridgeError("big_image_disabled", "mesh.big_image.enabled must be true")

    zoom = _int_setting(raw, "zoom", 20, 0)
    request_size = _int_setting(raw, "request_size_px", 640)
    cell_size = _int_setting(raw, "retained_cell_size_px", 512)
    if cell_size > request_size or (request_size - cell_size) % 2:
        raise SelectionBridgeError(
            "big_image_config_invalid",
            "request_size_px-retained_cell_size_px must be non-negative and even",
        )
    overlap = _finite_number(raw.get("overlap", 0.75), "mesh.big_image.overlap")
    if not 0.0 <= overlap < 1.0:
        raise SelectionBridgeError("big_image_config_invalid", "mesh.big_image.overlap must be in [0,1)")
    fusion = str(raw.get("fusion_mode", "fractional_feather"))
    if fusion != "fractional_feather":
        raise SelectionBridgeError(
            "big_image_config_invalid",
            "the validated on-demand contract requires fusion_mode=fractional_feather",
        )
    if raw.get("preserve_source_pixels", True) is not True:
        raise SelectionBridgeError(
            "big_image_config_invalid",
            "the validated on-demand contract requires preserve_source_pixels=true",
        )

    submission_root = Path(__file__).resolve().parents[5]
    sat_root = _configured_path(
        request,
        "sat3dgen_root",
        config,
        config_path,
        "sat3dgen_root",
        submission_root / "components" / "sat3dgen",
    )
    repo_default = sat_root / "Sat3DGen"
    scripts_default = submission_root / "research" / "scripts"
    download_script = _resolve_config_path(
        raw.get("download_script", scripts_default / "download_google_static_zoom_mosaic.py"),
        config_path,
        "mesh.big_image.download_script",
    )
    inference_script = _resolve_config_path(
        raw.get("inference_script", scripts_default / "run_sat3dgen_big_image_app192.py"),
        config_path,
        "mesh.big_image.inference_script",
    )
    colorize_script = _resolve_config_path(
        raw.get("colorize_script", scripts_default / "colorize_sat3dgen_big_mesh.py"),
        config_path,
        "mesh.big_image.colorize_script",
    )
    repo_root = _resolve_config_path(raw.get("repo_root", repo_default), config_path, "mesh.big_image.repo_root")
    for label, path in (
        ("download script", download_script),
        ("inference script", inference_script),
        ("colour script", colorize_script),
        ("Sat3DGen app root", repo_root),
    ):
        if not path.exists():
            raise SelectionBridgeError("big_image_dependency_missing", f"{label} is missing: {path}")

    cache_values = raw.get("validated_cache_outputs", [])
    if not isinstance(cache_values, list):
        raise SelectionBridgeError("big_image_config_invalid", "validated_cache_outputs must be an array")
    caches = tuple(
        _resolve_config_path(value, config_path, f"validated_cache_outputs[{index}]")
        for index, value in enumerate(cache_values)
    )
    settings = BigImageSettings(
        zoom=zoom,
        request_size_px=request_size,
        retained_cell_size_px=cell_size,
        context_padding_m=_finite_number(raw.get("context_padding_m", mesh.get("context_padding_m", 30.0)), "context_padding_m"),
        image_window_size_px=_int_setting(raw, "image_window_size_px", 640),
        overlap=overlap,
        mesh_resolution=_int_setting(raw, "mesh_resolution", 192),
        mesh_level=_finite_number(raw.get("mesh_level", 4.5), "mesh_level"),
        fusion_mode=fusion,
        preserve_source_pixels=True,
        max_cells=_int_setting(raw, "max_cells", 400),
        max_windows=_int_setting(raw, "max_windows", 5000),
        download_timeout_s=_finite_number(raw.get("download_timeout_s", 1800), "download_timeout_s"),
        inference_timeout_s=_finite_number(raw.get("inference_timeout_s", 3600), "inference_timeout_s"),
        color_timeout_s=_finite_number(raw.get("color_timeout_s", 3600), "color_timeout_s"),
        color_batch_size=_int_setting(raw, "color_batch_size", 131072),
        color_spatial_bin_size=_finite_number(
            raw.get("color_spatial_bin_size", 4.0), "color_spatial_bin_size"
        ),
        color_model_path=str(raw.get("color_model_path", "qian43/Sat3DGen")).strip(),
        source_crop_padding_m=_finite_number(raw.get("source_crop_padding_m", 8.0), "source_crop_padding_m"),
        building_crop_padding_m=_finite_number(raw.get("building_crop_padding_m", 1.0), "building_crop_padding_m"),
        ground_percentile=_finite_number(raw.get("ground_percentile", 2.0), "ground_percentile"),
        download_script=download_script,
        inference_script=inference_script,
        colorize_script=colorize_script,
        repo_root=repo_root,
        validated_cache_outputs=caches,
    )
    if settings.context_padding_m < 30.0:
        raise SelectionBridgeError("big_image_config_invalid", "big-image context_padding_m must be at least 30m")
    if not 0.0 <= settings.ground_percentile <= 25.0:
        raise SelectionBridgeError("big_image_config_invalid", "ground_percentile must be in [0,25]")
    if settings.color_timeout_s <= 0 or settings.color_spatial_bin_size <= 0:
        raise SelectionBridgeError(
            "big_image_config_invalid", "colour timeout and spatial bin size must be positive"
        )
    if not settings.color_model_path:
        raise SelectionBridgeError("big_image_config_invalid", "color_model_path must not be empty")
    return settings, config, config_path


def _world_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    scale = 256.0 * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    sine = math.sin(math.radians(max(-85.05112878, min(85.05112878, lat))))
    y = (0.5 - math.log((1.0 + sine) / (1.0 - sine)) / (4.0 * math.pi)) * scale
    return x, y


def _world_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    scale = 256.0 * (2**zoom)
    lon = x / scale * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / scale
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def _selection_local_bounds(request: SelectionRequest, padding: float) -> tuple[float, float, float, float]:
    return (
        min(footprint.bounds[0] for footprint in request.footprints) - padding,
        min(footprint.bounds[1] for footprint in request.footprints) - padding,
        max(footprint.bounds[2] for footprint in request.footprints) + padding,
        max(footprint.bounds[3] for footprint in request.footprints) + padding,
    )


def _local_bounds_wgs84(request: SelectionRequest, bounds: Sequence[float]) -> tuple[float, float, float, float]:
    points = (
        request.frame.to_wgs84(bounds[0], bounds[1]),
        request.frame.to_wgs84(bounds[0], bounds[3]),
        request.frame.to_wgs84(bounds[2], bounds[1]),
        request.frame.to_wgs84(bounds[2], bounds[3]),
    )
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return min(lons), min(lats), max(lons), max(lats)


def plan_big_image(request: SelectionRequest, settings: BigImageSettings) -> BigImagePlan:
    target = _local_bounds_wgs84(request, _selection_local_bounds(request, settings.context_padding_m))
    west_x, north_y = _world_pixel(target[0], target[3], settings.zoom)
    east_x, south_y = _world_pixel(target[2], target[1], settings.zoom)
    columns = max(2, int(math.ceil((east_x - west_x) / settings.retained_cell_size_px)))
    rows = max(2, int(math.ceil((south_y - north_y) / settings.retained_cell_size_px)))
    if columns * rows > settings.max_cells:
        raise SelectionBridgeError(
            "big_image_plan_too_large",
            f"planned mosaic requires {columns * rows} cells; configured maximum is {settings.max_cells}",
        )
    center_x = (west_x + east_x) / 2.0
    center_y = (north_y + south_y) / 2.0
    center_lon, center_lat = _world_lonlat(center_x, center_y, settings.zoom)
    half_width = columns * settings.retained_cell_size_px / 2.0
    half_height = rows * settings.retained_cell_size_px / 2.0
    mosaic_west, mosaic_north = _world_lonlat(center_x - half_width, center_y - half_height, settings.zoom)
    mosaic_east, mosaic_south = _world_lonlat(center_x + half_width, center_y + half_height, settings.zoom)
    width = columns * settings.retained_cell_size_px
    height = rows * settings.retained_cell_size_px
    step = max(1, round(settings.image_window_size_px * (1.0 - settings.overlap)))
    x_windows = 1 if width <= settings.image_window_size_px else math.ceil((width - settings.image_window_size_px) / step) + 1
    y_windows = 1 if height <= settings.image_window_size_px else math.ceil((height - settings.image_window_size_px) / step) + 1
    windows = int(x_windows * y_windows)
    if windows > settings.max_windows:
        raise SelectionBridgeError(
            "big_image_plan_too_large",
            f"planned inference requires about {windows} windows; configured maximum is {settings.max_windows}",
        )
    mpp = math.cos(math.radians(center_lat)) * WEB_MERCATOR_WORLD_M / (256.0 * 2**settings.zoom)
    return BigImagePlan(
        center_lat=center_lat,
        center_lon=center_lon,
        columns=columns,
        rows=rows,
        input_pixel_resolution_m=mpp,
        target_bbox_wgs84=target,
        mosaic_bbox_wgs84=(mosaic_west, mosaic_south, mosaic_east, mosaic_north),
        mosaic_size_px=(width, height),
        estimated_window_count=windows,
    )


def _bbox_covers(outer: Sequence[float], inner: Sequence[float], epsilon: float = 1e-10) -> bool:
    return (
        outer[0] <= inner[0] + epsilon
        and outer[1] <= inner[1] + epsilon
        and outer[2] >= inner[2] - epsilon
        and outer[3] >= inner[3] - epsilon
    )


def _manifest_bbox(document: Mapping[str, Any]) -> tuple[float, float, float, float]:
    raw = document.get("bounds_wgs84")
    if not isinstance(raw, Mapping):
        raise SelectionBridgeError("big_image_cache_invalid", "mosaic manifest has no bounds_wgs84")
    try:
        result = tuple(float(raw[key]) for key in ("west", "south", "east", "north"))
    except (KeyError, TypeError, ValueError) as exc:
        raise SelectionBridgeError("big_image_cache_invalid", "mosaic manifest bounds are invalid") from exc
    if not result[0] < result[2] or not result[1] < result[3]:
        raise SelectionBridgeError("big_image_cache_invalid", "mosaic manifest bounds are empty")
    return result  # type: ignore[return-value]


def _mosaic_manifest_for(metadata: Mapping[str, Any], output_root: Path) -> Path:
    source = metadata.get("source_image")
    candidates: list[Path] = []
    if isinstance(source, str) and source.strip():
        image = Path(source).expanduser()
        candidates.extend((image.with_name(image.stem + "_manifest.json"), image.with_suffix(".json")))
    candidates.extend((output_root / "mosaic_manifest.json", output_root.parent / "mosaic_manifest.json"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise SelectionBridgeError(
        "big_image_cache_invalid",
        f"cannot locate the mosaic manifest referenced by {output_root / 'run_metadata.json'}",
    )


def _ply_header(path: Path) -> dict[str, Any]:
    """Read the bounded ASCII header of a PLY without loading its large body."""

    elements: dict[str, int] = {}
    vertex_properties: dict[str, str] = {}
    current_element: str | None = None
    try:
        with path.open("rb") as stream:
            if stream.readline().strip() != b"ply":
                raise ValueError("missing ply signature")
            for _ in range(4096):
                raw = stream.readline(4096)
                if not raw:
                    raise ValueError("missing end_header")
                try:
                    line = raw.decode("ascii").strip()
                except UnicodeDecodeError as exc:
                    raise ValueError("header is not ASCII") from exc
                if line == "end_header":
                    break
                tokens = line.split()
                if len(tokens) == 3 and tokens[0] == "element":
                    current_element = tokens[1]
                    elements[current_element] = int(tokens[2])
                elif current_element == "vertex" and len(tokens) == 3 and tokens[0] == "property":
                    vertex_properties[tokens[2]] = tokens[1].lower()
            else:
                raise ValueError("header exceeds safety limit")
    except (OSError, ValueError) as exc:
        raise SelectionBridgeError("big_image_ply_invalid", f"invalid PLY header {path}: {exc}") from exc
    vertex_count = int(elements.get("vertex", 0))
    face_count = int(elements.get("face", 0))
    if vertex_count < 3 or face_count < 1:
        raise SelectionBridgeError("big_image_ply_invalid", f"PLY has no mesh elements: {path}")
    return {
        "vertex_count": vertex_count,
        "face_count": face_count,
        "vertex_properties": vertex_properties,
    }


def _validate_colour_bundle(
    output_root: Path,
    geometry_mesh: Path,
    geometry_header: Mapping[str, Any],
    settings: BigImageSettings,
    *,
    colour_root: Path | None = None,
) -> dict[str, Any]:
    root = (colour_root or output_root).resolve(strict=False)
    coloured_mesh = root / "mesh_colored.ply"
    metadata_path = root / "color_metadata.json"
    preflight_path = root / "color_preflight.json"
    if not coloured_mesh.is_file() or coloured_mesh.stat().st_size < 1024:
        raise SelectionBridgeError("big_image_colour_missing", f"coloured PLY is missing: {coloured_mesh}")
    if not metadata_path.is_file() or not preflight_path.is_file():
        raise SelectionBridgeError("big_image_colour_missing", f"colour reports are incomplete: {root}")

    metadata = _read_json(metadata_path, "big-image colour metadata")
    preflight = _read_json(preflight_path, "big-image colour preflight")
    coloured_header = _ply_header(coloured_mesh)
    properties = coloured_header["vertex_properties"]
    for name in ("red", "green", "blue", "alpha"):
        if properties.get(name) not in {"uchar", "uint8"}:
            raise SelectionBridgeError(
                "big_image_colour_invalid", f"mesh_colored.ply lacks uchar {name}: {coloured_mesh}"
            )
    if coloured_header["vertex_count"] != geometry_header["vertex_count"] or (
        coloured_header["face_count"] != geometry_header["face_count"]
    ):
        raise SelectionBridgeError("big_image_colour_invalid", "colour pass changed mesh element counts")
    if int(metadata.get("vertex_count", -1)) != coloured_header["vertex_count"] or (
        int(metadata.get("face_count", -1)) != coloured_header["face_count"]
    ):
        raise SelectionBridgeError("big_image_colour_invalid", "colour metadata count does not match PLY")
    if int(preflight.get("vertex_count", -1)) != coloured_header["vertex_count"]:
        raise SelectionBridgeError("big_image_colour_invalid", "colour preflight vertex count does not match PLY")
    if int(metadata.get("zero_weight_vertices", -1)) != 0 or int(preflight.get("zero_weight_vertices", -1)) != 0:
        raise SelectionBridgeError("big_image_colour_invalid", "colour pass left vertices without contributions")
    if int(metadata.get("contributors_per_vertex_min", 0)) < 1:
        raise SelectionBridgeError("big_image_colour_invalid", "colour contribution coverage is empty")
    if metadata.get("geometry_preserved") is not True:
        raise SelectionBridgeError("big_image_colour_invalid", "colour pass did not preserve geometry")
    verification = metadata.get("verification")
    if not isinstance(verification, Mapping) or any(
        verification.get(key) is not True
        for key in ("geometry_unchanged", "rgb_roundtrip_exact", "alpha_opaque")
    ):
        raise SelectionBridgeError("big_image_colour_invalid", "colour round-trip verification failed")
    if str(metadata.get("model_path", "")) != settings.color_model_path:
        raise SelectionBridgeError("big_image_colour_mismatch", "colour model path does not match configuration")
    if int(metadata.get("color_batch_size", -1)) != settings.color_batch_size:
        raise SelectionBridgeError("big_image_colour_mismatch", "colour batch size does not match configuration")
    if not math.isclose(
        float(metadata.get("spatial_bin_size_density_voxels", math.nan)),
        settings.color_spatial_bin_size,
        abs_tol=1e-12,
    ):
        raise SelectionBridgeError("big_image_colour_mismatch", "colour spatial bin size does not match")

    geometry_sha256 = _sha256(geometry_mesh)
    coloured_sha256 = _sha256(coloured_mesh)
    if metadata.get("source_mesh_sha256") != geometry_sha256:
        raise SelectionBridgeError("big_image_colour_mismatch", "colour metadata references another geometry mesh")
    if metadata.get("output_mesh_sha256") != coloured_sha256:
        raise SelectionBridgeError("big_image_colour_invalid", "coloured PLY hash does not match metadata")
    return {
        "mesh_ply": str(coloured_mesh.resolve()),
        "geometry_mesh_ply": str(geometry_mesh.resolve()),
        "color_metadata": str(metadata_path.resolve()),
        "color_preflight": str(preflight_path.resolve()),
        "mesh_size_bytes": coloured_mesh.stat().st_size,
        "geometry_mesh_size_bytes": geometry_mesh.stat().st_size,
        "vertex_colors": True,
        "vertex_color_encoding": "PLY uchar RGBA; published OBJ v x y z r g b",
        "color_output_sha256": coloured_sha256,
        "color": metadata,
    }


def validate_big_image_output(
    output_root: Path,
    plan: BigImagePlan,
    settings: BigImageSettings,
    *,
    require_vertex_colors: bool = True,
) -> dict[str, Any]:
    output_root = output_root.resolve(strict=False)
    mesh_path = output_root / "mesh.ply"
    metadata_path = output_root / "run_metadata.json"
    if not mesh_path.is_file() or mesh_path.stat().st_size < 1024 or not metadata_path.is_file():
        raise SelectionBridgeError("big_image_cache_invalid", f"big-image output is incomplete: {output_root}")
    metadata = _read_json(metadata_path, "big-image run metadata")
    exact = {
        "image_window_size_px": settings.image_window_size_px,
        "mesh_resolution": settings.mesh_resolution,
        "fusion_mode": settings.fusion_mode,
        "preserve_source_pixels": settings.preserve_source_pixels,
    }
    for key, expected in exact.items():
        if metadata.get(key) != expected:
            raise SelectionBridgeError(
                "big_image_cache_mismatch",
                f"cached {key}={metadata.get(key)!r} does not match required {expected!r}: {output_root}",
            )
    if not math.isclose(float(metadata.get("requested_overlap_fraction", -1)), settings.overlap, abs_tol=1e-12):
        raise SelectionBridgeError("big_image_cache_mismatch", "cached overlap does not match 0.75")
    if not math.isclose(float(metadata.get("mesh_level", math.nan)), settings.mesh_level, abs_tol=1e-9):
        raise SelectionBridgeError("big_image_cache_mismatch", "cached mesh level does not match")
    if int(metadata.get("fusion_zero_weight_cells", -1)) != 0:
        raise SelectionBridgeError("big_image_cache_invalid", "big-image fusion contains uncovered density cells")
    manifest_path = _mosaic_manifest_for(metadata, output_root)
    manifest = _read_json(manifest_path, "big-image mosaic manifest")
    if int(manifest.get("zoom", -1)) != settings.zoom:
        raise SelectionBridgeError("big_image_cache_mismatch", "cached mosaic zoom does not match")
    if int(manifest.get("request_size_px", -1)) != settings.request_size_px:
        raise SelectionBridgeError("big_image_cache_mismatch", "cached raw request size does not match")
    if int(manifest.get("retained_cell_size_px", -1)) != settings.retained_cell_size_px:
        raise SelectionBridgeError("big_image_cache_mismatch", "cached retained cell size does not match")
    bounds = _manifest_bbox(manifest)
    if not _bbox_covers(bounds, plan.target_bbox_wgs84):
        raise SelectionBridgeError(
            "big_image_cache_outside_selection",
            "cached mosaic does not cover the selected footprint plus context",
            details={"cache_bounds": bounds, "required_bounds": plan.target_bbox_wgs84},
        )
    source = Path(str(metadata.get("source_image", ""))).expanduser()
    if not source.is_file():
        raise SelectionBridgeError("big_image_cache_invalid", f"cached source mosaic is missing: {source}")
    prepared = output_root / "prepared_input.png"
    if not prepared.is_file() or prepared.stat().st_size < 5000:
        raise SelectionBridgeError("big_image_cache_invalid", f"prepared input is missing: {prepared}")
    # preserve_source_pixels=true is part of this contract.  Byte identity is
    # therefore a cheap content-address check that prevents reusing an old PLY
    # after a same-named mosaic has been replaced.
    source_sha256 = _sha256(source)
    prepared_sha256 = _sha256(prepared)
    if source_sha256 != prepared_sha256:
        raise SelectionBridgeError(
            "big_image_cache_mismatch",
            "prepared_input.png does not match the source mosaic for preserve_source_pixels=true",
        )
    geometry_header = _ply_header(mesh_path)
    if "mesh_vertices" in metadata and int(metadata["mesh_vertices"]) != geometry_header["vertex_count"]:
        raise SelectionBridgeError("big_image_cache_invalid", "run metadata vertex count does not match PLY")
    if "mesh_faces" in metadata and int(metadata["mesh_faces"]) != geometry_header["face_count"]:
        raise SelectionBridgeError("big_image_cache_invalid", "run metadata face count does not match PLY")
    report = {
        "output_root": str(output_root),
        "mesh_ply": str(mesh_path.resolve()),
        "geometry_mesh_ply": str(mesh_path.resolve()),
        "run_metadata": str(metadata_path.resolve()),
        "mosaic": str(source.resolve()),
        "mosaic_manifest": str(manifest_path),
        "prepared_input": str(prepared.resolve()),
        "source_image_sha256": source_sha256,
        "mosaic_bounds_wgs84": list(bounds),
        "mesh_size_bytes": mesh_path.stat().st_size,
        "geometry_mesh_size_bytes": mesh_path.stat().st_size,
        "vertex_colors": False,
        "metadata": metadata,
        "manifest": manifest,
    }
    if require_vertex_colors:
        report.update(_validate_colour_bundle(output_root, mesh_path, geometry_header, settings))
    return report


def find_reusable_big_image_output(job_root: Path, plan: BigImagePlan, settings: BigImageSettings) -> dict[str, Any] | None:
    candidates = (job_root / "inference", *settings.validated_cache_outputs)
    failures: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            report = validate_big_image_output(candidate, plan, settings)
            report["cache_hit"] = True
            report["cache_kind"] = "job" if candidate == job_root / "inference" else "validated_external"
            report["rejected_candidates"] = failures
            return report
        except SelectionBridgeError as exc:
            failures.append({"path": str(candidate), "code": exc.code, "message": redact_text(exc)})
    return None


def _run_logged(
    argv: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    timeout_s: float,
    stage: str,
) -> None:
    """Run a fixed argv without a shell and retain a redacted bounded log."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        with log_path.open("w", encoding="utf-8", newline="\n") as log:
            process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
                env=os.environ.copy(),
                text=True,
            )
            try:
                exit_code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                raise SelectionBridgeError(
                    "big_image_timeout",
                    f"{stage} exceeded its {timeout_s:.0f}s timeout; see {log_path}",
                ) from exc
    except SelectionBridgeError:
        raise
    except OSError as exc:
        raise SelectionBridgeError(
            "big_image_process_error", f"cannot start {stage}: {redact_text(exc)}"
        ) from exc
    if exit_code != 0:
        tail = ""
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            pass
        raise SelectionBridgeError(
            "big_image_process_failed",
            f"{stage} exited with {exit_code} after {time.monotonic() - started:.1f}s; "
            f"see {log_path}; tail={redact_text(tail)}",
        )


def _mosaic_matches(path: Path, manifest_path: Path, plan: BigImagePlan, settings: BigImageSettings) -> bool:
    if not path.is_file() or path.stat().st_size < 5000 or not manifest_path.is_file():
        return False
    try:
        manifest = _read_json(manifest_path, "cached big-image mosaic manifest")
        bounds = _manifest_bbox(manifest)
        return (
            int(manifest.get("zoom", -1)) == settings.zoom
            and int(manifest.get("request_size_px", -1)) == settings.request_size_px
            and int(manifest.get("retained_cell_size_px", -1)) == settings.retained_cell_size_px
            and list(manifest.get("grid", [])) == [plan.columns, plan.rows]
            and list(manifest.get("mosaic_size_px", [])) == list(plan.mosaic_size_px)
            and _bbox_covers(bounds, plan.target_bbox_wgs84)
            and math.isclose(float(manifest.get("center", [math.nan, math.nan])[0]), plan.center_lat, abs_tol=1e-7)
            and math.isclose(float(manifest.get("center", [math.nan, math.nan])[1]), plan.center_lon, abs_tol=1e-7)
        )
    except (SelectionBridgeError, TypeError, ValueError, IndexError):
        return False


def _ensure_big_image_vertex_colours(
    output_root: Path,
    job_root: Path,
    plan: BigImagePlan,
    settings: BigImageSettings,
) -> dict[str, Any]:
    """Run the verified second-pass colour MLP and publish its marker last."""

    geometry = validate_big_image_output(
        output_root, plan, settings, require_vertex_colors=False
    )
    try:
        ready = validate_big_image_output(output_root, plan, settings)
        return ready
    except SelectionBridgeError:
        pass

    colour_stage = job_root / f"colour-stage-{uuid.uuid4().hex[:10]}"
    colour_stage.mkdir(parents=True)
    command = [
        sys.executable,
        "-B",
        str(settings.colorize_script),
        "--repo_root",
        str(settings.repo_root),
        "--result_dir",
        str(output_root),
        "--model_path",
        settings.color_model_path,
        "--mesh_path",
        str(output_root / "mesh.ply"),
        "--prepared_image_path",
        str(output_root / "prepared_input.png"),
        "--output_path",
        str(colour_stage / "mesh_colored.ply"),
        "--color_batch_size",
        str(settings.color_batch_size),
        "--spatial_bin_size",
        f"{settings.color_spatial_bin_size:.12g}",
    ]
    try:
        _run_logged(
            command,
            cwd=settings.colorize_script.parent,
            log_path=job_root / "colorize.log",
            timeout_s=settings.color_timeout_s,
            stage="Sat3DGen fractional-feather vertex colour pass",
        )
        # The upstream tool writes absolute paths for its temporary output.
        # Rewrite only those descriptive paths before validation/promotion so
        # the durable report points at the stable cache directory.
        colour_metadata = _read_json(
            colour_stage / "color_metadata.json", "staged big-image colour metadata"
        )
        colour_metadata.update({
            "source_run_metadata": str((output_root / "run_metadata.json").resolve()),
            "source_mesh": str((output_root / "mesh.ply").resolve()),
            "prepared_image": str((output_root / "prepared_input.png").resolve()),
            "output_mesh": str((output_root / "mesh_colored.ply").resolve()),
        })
        _atomic_json(colour_stage / "color_metadata.json", colour_metadata)
        geometry_header = _ply_header(Path(geometry["geometry_mesh_ply"]))
        _validate_colour_bundle(
            output_root,
            Path(geometry["geometry_mesh_ply"]),
            geometry_header,
            settings,
            colour_root=colour_stage,
        )
        # color_metadata.json is the commit marker. A hard interruption before
        # its final replace leaves a cache that strict validation rejects.
        os.replace(colour_stage / "mesh_colored.ply", output_root / "mesh_colored.ply")
        os.replace(colour_stage / "color_preflight.json", output_root / "color_preflight.json")
        os.replace(colour_stage / "color_metadata.json", output_root / "color_metadata.json")
    finally:
        shutil.rmtree(colour_stage, ignore_errors=True)
    return validate_big_image_output(output_root, plan, settings)


def generate_big_image_output(
    job_root: Path,
    plan: BigImagePlan,
    settings: BigImageSettings,
) -> dict[str, Any]:
    """Download a selection mosaic and run the verified app192 big-image script."""

    job_root.mkdir(parents=True, exist_ok=True)
    mosaic = job_root / "mosaic.png"
    mosaic_manifest = job_root / "mosaic_manifest.json"
    if not _mosaic_matches(mosaic, mosaic_manifest, plan, settings):
        if not os.environ.get("GOOGLE_MAPS_API_KEY", "").strip():
            raise SelectionBridgeError(
                "api_key_missing",
                "big-image mosaic download requires GOOGLE_MAPS_API_KEY in the GUI process environment",
            )
        staging = job_root / f"mosaic-stage-{uuid.uuid4().hex[:10]}"
        staging.mkdir(parents=True)
        staged_mosaic = staging / "mosaic.png"
        staged_manifest = staging / "mosaic_manifest.json"
        command = [
            sys.executable,
            "-B",
            str(settings.download_script),
            "--center_lat",
            f"{plan.center_lat:.12f}",
            "--center_lon",
            f"{plan.center_lon:.12f}",
            "--zoom",
            str(settings.zoom),
            "--columns",
            str(plan.columns),
            "--rows",
            str(plan.rows),
            "--request_size",
            str(settings.request_size_px),
            "--cell_size",
            str(settings.retained_cell_size_px),
            "--api_key_env",
            "GOOGLE_MAPS_API_KEY",
            "--output",
            str(staged_mosaic),
            "--manifest",
            str(staged_manifest),
        ]
        try:
            _run_logged(
                command,
                cwd=settings.download_script.parent,
                log_path=job_root / "download.log",
                timeout_s=settings.download_timeout_s,
                stage="Google Static Maps mosaic download",
            )
            if not _mosaic_matches(staged_mosaic, staged_manifest, plan, settings):
                raise SelectionBridgeError(
                    "big_image_download_invalid", "downloaded mosaic or manifest failed validation"
                )
            os.replace(staged_mosaic, mosaic)
            os.replace(staged_manifest, mosaic_manifest)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    inference = job_root / "inference"
    try:
        cached = validate_big_image_output(inference, plan, settings)
        cached["cache_hit"] = True
        cached["cache_kind"] = "job"
        return cached
    except SelectionBridgeError:
        pass
    try:
        validate_big_image_output(inference, plan, settings, require_vertex_colors=False)
    except SelectionBridgeError:
        pass
    else:
        coloured = _ensure_big_image_vertex_colours(inference, job_root, plan, settings)
        coloured["cache_hit"] = True
        coloured["cache_kind"] = "job_geometry_colour_generated"
        return coloured

    staging = job_root / f"inference-stage-{uuid.uuid4().hex[:10]}"
    command = [
        sys.executable,
        "-B",
        str(settings.inference_script),
        "--repo_root",
        str(settings.repo_root),
        "--satellite_img_path",
        str(mosaic),
        "--input_pixel_resolution",
        f"{plan.input_pixel_resolution_m:.15g}",
        "--preserve_source_pixels",
        "--image_window_size",
        str(settings.image_window_size_px),
        "--overlap",
        f"{settings.overlap:.12g}",
        "--mesh_resolution",
        str(settings.mesh_resolution),
        "--mesh_level",
        f"{settings.mesh_level:.12g}",
        "--fusion_mode",
        settings.fusion_mode,
        "--output_dir",
        str(staging),
    ]
    try:
        _run_logged(
            command,
            cwd=settings.inference_script.parent,
            log_path=job_root / "inference.log",
            timeout_s=settings.inference_timeout_s,
            stage="Sat3DGen app192 big-image inference",
        )
        # The upstream metadata points at the stable mosaic path. Install the
        # matching manifest beside that image before strict validation.
        validate_big_image_output(staging, plan, settings, require_vertex_colors=False)
        if inference.exists():
            retained = job_root / f"inference-invalid-{int(time.time())}"
            os.replace(inference, retained)
        os.replace(staging, inference)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    report = _ensure_big_image_vertex_colours(inference, job_root, plan, settings)
    report["cache_hit"] = False
    report["cache_kind"] = "generated_on_demand"
    return report


def _load_ply_arrays(path: Path):
    try:
        import numpy as np
        import trimesh
    except Exception as exc:
        raise SelectionBridgeError(
            "big_image_dependency_missing", f"PLY conversion requires numpy and trimesh: {exc}"
        ) from exc
    header = _ply_header(path)
    properties = header["vertex_properties"]
    if any(properties.get(name) not in {"uchar", "uint8"} for name in ("red", "green", "blue", "alpha")):
        raise SelectionBridgeError(
            "big_image_colour_invalid", f"PLY has no explicit uchar RGBA vertex properties: {path}"
        )
    try:
        loaded = trimesh.load(str(path), process=False, maintain_order=True)
    except Exception as exc:
        raise SelectionBridgeError("big_image_ply_invalid", f"cannot read {path}: {exc}") from exc
    if isinstance(loaded, trimesh.Scene):
        raise SelectionBridgeError("big_image_ply_invalid", f"coloured PLY must contain one mesh: {path}")
    vertices = np.asarray(getattr(loaded, "vertices", ()), dtype=np.float64)
    faces = np.asarray(getattr(loaded, "faces", ()), dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] < 3 or len(vertices) < 3:
        raise SelectionBridgeError("big_image_ply_invalid", f"PLY has no valid vertices: {path}")
    if faces.ndim != 2 or faces.shape[1] != 3 or len(faces) < 1:
        raise SelectionBridgeError("big_image_ply_invalid", f"PLY has no triangle faces: {path}")
    if not np.isfinite(vertices[:, :3]).all():
        raise SelectionBridgeError("big_image_ply_invalid", "PLY contains non-finite vertices")
    if int(faces.min()) < 0 or int(faces.max()) >= len(vertices):
        raise SelectionBridgeError("big_image_ply_invalid", "PLY contains out-of-range face indices")
    if len(vertices) != header["vertex_count"] or len(faces) != header["face_count"]:
        raise SelectionBridgeError("big_image_ply_invalid", "PLY body count does not match its header")
    raw_colours = np.asarray(getattr(getattr(loaded, "visual", None), "vertex_colors", ()))
    if raw_colours.ndim != 2 or raw_colours.shape[0] != len(vertices) or raw_colours.shape[1] < 4:
        raise SelectionBridgeError("big_image_colour_invalid", "PLY vertex colour array is missing or misaligned")
    colours = np.asarray(raw_colours[:, :4], dtype=np.float64)
    if not np.isfinite(colours).all() or float(colours.min()) < 0:
        raise SelectionBridgeError("big_image_colour_invalid", "PLY contains invalid RGBA values")
    if np.issubdtype(raw_colours.dtype, np.integer) or float(colours.max()) > 1.0:
        if float(colours.max()) > 255.0 or not np.all(colours[:, 3] == 255.0):
            raise SelectionBridgeError("big_image_colour_invalid", "PLY alpha is not opaque uchar RGBA")
        colours /= 255.0
    elif not np.allclose(colours[:, 3], 1.0, atol=0.0, rtol=0.0):
        raise SelectionBridgeError("big_image_colour_invalid", "PLY alpha is not opaque normalized RGBA")
    rgb = colours[:, :3]
    if float(rgb.max()) > 1.0:
        raise SelectionBridgeError("big_image_colour_invalid", "normalized PLY RGB exceeds one")
    return np.column_stack((vertices[:, :3], rgb)), faces


def _web_mercator_numpy(lon, lat, np):
    x = WEB_MERCATOR_RADIUS_M * np.radians(lon)
    y = WEB_MERCATOR_RADIUS_M * np.log(np.tan(math.pi / 4.0 + np.radians(lat) / 2.0))
    return x, y


def transform_big_image_vertices(
    raw_vertices,
    metadata: Mapping[str, Any],
    mosaic_manifest: Mapping[str, Any],
    request: SelectionRequest,
):
    """Map marching-cubes (row, column, height) into workspace metres once."""

    import numpy as np

    try:
        density_scale = float(metadata["density_to_image_scale"])
        prepared_mpp = float(metadata["prepared_pixel_resolution_m"])
        image_size = metadata["prepared_image_size_px"]
        width, height = int(image_size[0]), int(image_size[1])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise SelectionBridgeError("big_image_metadata_invalid", "run metadata lacks scale/image size") from exc
    if density_scale <= 0 or prepared_mpp <= 0 or width < 2 or height < 2:
        raise SelectionBridgeError("big_image_metadata_invalid", "run metadata contains invalid scale/image size")
    manifest_size = mosaic_manifest.get("mosaic_size_px")
    if list(manifest_size or ()) != [width, height]:
        raise SelectionBridgeError(
            "big_image_metadata_invalid", "inference and mosaic manifests disagree on image size"
        )
    west, south, east, north = _manifest_bbox(mosaic_manifest)
    west_m, south_m = _web_mercator_numpy(west, south, np)
    east_m, north_m = _web_mercator_numpy(east, north, np)
    image_rows = raw_vertices[:, 0] / density_scale
    image_columns = raw_vertices[:, 1] / density_scale
    # Pixel coordinates are continuous edge coordinates in the fused density
    # field. Clamp only tiny marching-cubes interpolation overshoots.
    image_rows = np.clip(image_rows, -1e-5, float(height) + 1e-5)
    image_columns = np.clip(image_columns, -1e-5, float(width) + 1e-5)
    mercator_x = west_m + image_columns / float(width) * (east_m - west_m)
    mercator_y = north_m + image_rows / float(height) * (south_m - north_m)
    lon = np.degrees(mercator_x / WEB_MERCATOR_RADIUS_M)
    lat = np.degrees(2.0 * np.arctan(np.exp(mercator_y / WEB_MERCATOR_RADIUS_M)) - math.pi / 2.0)
    local_x = (lon - request.frame.origin_lon) * request.frame.meters_per_degree_lon
    local_z = -(lat - request.frame.origin_lat) * 111_320.0
    local_y = raw_vertices[:, 2] * (prepared_mpp / density_scale)
    attributes = raw_vertices[:, 3:]
    if attributes.shape[1] != 3:
        raise SelectionBridgeError("big_image_colour_invalid", "big-image vertices require exactly RGB attributes")
    transformed = np.column_stack((local_x, local_y, local_z, attributes))
    if not np.isfinite(transformed).all():
        raise SelectionBridgeError("big_image_transform_invalid", "coordinate transform produced non-finite vertices")
    return transformed, {
        "source_axes": {"x": "image_row_south", "y": "image_column_east", "z": "density_height"},
        "target_axes": {"x": "east", "y": "up", "z": "south"},
        "density_to_image_scale": density_scale,
        "metres_per_density_voxel": prepared_mpp / density_scale,
        "transform_count": 1,
        "vertex_colors_preserved": True,
    }


def crop_arrays_to_local_bounds(vertices, faces, bounds: Sequence[float]):
    """Retain every triangle whose projected bbox intersects a rectangle.

    This is a spatial source crop only. It does not inspect normals, winding,
    components, duplicate faces, or lower surfaces.
    """

    import numpy as np

    selected_chunks = []
    chunk_size = 250_000
    for start in range(0, len(faces), chunk_size):
        chunk = faces[start : start + chunk_size]
        tri_x = vertices[chunk, 0]
        tri_z = vertices[chunk, 2]
        keep = (
            (tri_x.max(axis=1) >= bounds[0])
            & (tri_x.min(axis=1) <= bounds[2])
            & (tri_z.max(axis=1) >= bounds[1])
            & (tri_z.min(axis=1) <= bounds[3])
        )
        if keep.any():
            selected_chunks.append(chunk[keep])
    if not selected_chunks:
        raise SelectionBridgeError("big_image_crop_empty", "big-image mesh does not intersect the selected context")
    selected_faces = np.concatenate(selected_chunks, axis=0)
    used = np.unique(selected_faces.reshape(-1))
    remap = np.full(len(vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    compact_vertices = vertices[used].copy()
    compact_faces = remap[selected_faces]
    return compact_vertices, compact_faces, {
        "source_vertex_count": int(len(vertices)),
        "source_face_count": int(len(faces)),
        "cropped_vertex_count": int(len(compact_vertices)),
        "cropped_face_count": int(len(compact_faces)),
        "crop_kind": "triangle_projected_bbox_intersection",
        "geometry_cleanup_applied": False,
    }


def reverse_inward_face_winding(faces):
    """Return an outward-wound copy of the Big Image marching-cubes faces.

    The validated app192 Big Image producer emits an inward-oriented surface.
    ChordAtlas uses back-face culling and expects outward winding.  Reversing
    the last two indices changes orientation only: it does not remove, merge,
    repair, deduplicate, or reconnect any triangle, and it never mutates the
    cached PLY face array.
    """

    import numpy as np

    source = np.asarray(faces)
    if source.ndim != 2 or source.shape[1] != 3:
        raise SelectionBridgeError(
            "big_image_faces_invalid", "big-image winding correction requires triangular faces"
        )
    corrected = source[:, (0, 2, 1)].copy()
    if corrected.shape != source.shape or len(corrected) != len(source):
        raise SelectionBridgeError(
            "big_image_winding_invalid", "big-image winding correction changed the face count"
        )
    return corrected, {
        "source_winding": "inward",
        "winding_correction": "reverse_all",
        "output_winding": "outward",
        "face_count_before": int(len(source)),
        "face_count_after": int(len(corrected)),
        "faces_removed": 0,
        "topology_changed": False,
    }


def _import_top_level_mesh_pipeline(sat3dgen_root: Path):
    root = sat3dgen_root.resolve()
    top = (root / "mesh_pipeline").resolve()
    forbidden = top / "mesh_generate_merge_pipeline"
    if not (top / "config.py").is_file():
        raise SelectionBridgeError(
            "big_image_dependency_missing", f"top-level mesh_pipeline is missing under {root}"
        )
    for name, module in tuple(sys.modules.items()):
        if name == "mesh_pipeline" or name.startswith("mesh_pipeline."):
            loaded = Path(getattr(module, "__file__", "")).resolve(strict=False)
            if forbidden == loaded or forbidden in loaded.parents or top not in loaded.parents:
                raise SelectionBridgeError(
                    "nested_mesh_pipeline_forbidden",
                    f"refusing already-loaded mesh_pipeline outside {top}: {loaded}",
                )
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from mesh_pipeline.config import Config
    from mesh_pipeline.dsm_loader import DSMLoader
    from mesh_pipeline.height_correction import semantic_height_correction
    from mesh_pipeline.osm_loader import OSMLoader

    for module_name in (
        "mesh_pipeline.config",
        "mesh_pipeline.dsm_loader",
        "mesh_pipeline.height_correction",
        "mesh_pipeline.osm_loader",
    ):
        loaded = Path(sys.modules[module_name].__file__).resolve()
        if forbidden == loaded or forbidden in loaded.parents or top not in loaded.parents:
            raise SelectionBridgeError(
                "nested_mesh_pipeline_forbidden", f"refusing mesh pipeline import: {loaded}"
            )
    return Config, DSMLoader, semantic_height_correction, OSMLoader


def _dsm_configuration(
    request: SelectionRequest,
    config_document: Mapping[str, Any],
    config_path: Path | None,
):
    mesh = config_document.get("mesh")
    if not isinstance(mesh, Mapping) or mesh.get("apply_dsm") is not True:
        raise SelectionBridgeError("dsm_required", "big-image publication requires mesh.apply_dsm=true")
    dsm_dir = _configured_mesh_path(request, config_document, config_path, "dsm_dir")
    osm_dir = _configured_mesh_path(request, config_document, config_path, "osm_dir")
    raw_files = mesh.get("dsm_files")
    if not isinstance(raw_files, list) or not raw_files:
        raise SelectionBridgeError("dsm_required", "mesh.dsm_files must be a non-empty allowlist")
    files: list[str] = []
    for value in raw_files:
        if not isinstance(value, str) or Path(value).name != value or Path(value).suffix.lower() not in {".tif", ".tiff"}:
            raise SelectionBridgeError("dsm_required", f"invalid DSM allowlist entry: {value!r}")
        if value not in files:
            files.append(value)
    missing = [str(dsm_dir / name) for name in files if not (dsm_dir / name).is_file()]
    if missing:
        raise SelectionBridgeError("dsm_missing", "mandatory DSM file is missing", details={"missing": missing})
    if not (osm_dir / "building.geojson").is_file():
        raise SelectionBridgeError("dsm_osm_missing", f"OSM building source is missing: {osm_dir}")
    crs = str(mesh.get("dsm_crs", "")).upper()
    if crs != "EPSG:27700":
        raise SelectionBridgeError("dsm_required", "big-image DSM CRS must be EPSG:27700")
    return dsm_dir, files, osm_dir, crs


def _validate_dsm_coverage(dsm_dir: Path, files: Sequence[str], bbox: Sequence[float]) -> dict[str, Any]:
    try:
        import rasterio
        from pyproj import Transformer
        from shapely.geometry import box
        from shapely.ops import unary_union
    except Exception as exc:
        raise SelectionBridgeError("dsm_dependency_missing", f"cannot validate mandatory DSM: {exc}") from exc
    pieces = []
    sources = []
    for name in files:
        path = (dsm_dir / name).resolve()
        try:
            with rasterio.open(path) as dataset:
                if dataset.crs is None or dataset.crs.to_epsg() != 27700:
                    raise SelectionBridgeError("dsm_invalid", f"DSM is not EPSG:27700: {path}")
                bounds = dataset.bounds
                pieces.append(box(bounds.left, bounds.bottom, bounds.right, bounds.top))
                sources.append({
                    "name": name,
                    "path": str(path),
                    "width": int(dataset.width),
                    "height": int(dataset.height),
                    "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                })
        except SelectionBridgeError:
            raise
        except Exception as exc:
            raise SelectionBridgeError("dsm_invalid", f"cannot read DSM {path}: {exc}") from exc
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    xs, ys = transformer.transform(
        [bbox[0], bbox[2], bbox[2], bbox[0]],
        [bbox[1], bbox[1], bbox[3], bbox[3]],
    )
    target = box(min(xs), min(ys), max(xs), max(ys))
    missing_area = float(target.difference(unary_union(pieces)).area)
    if missing_area > 0.001:
        raise SelectionBridgeError(
            "dsm_coverage_incomplete",
            f"mandatory DSM misses {missing_area:.6f} m2 of the selected big-image context",
        )
    return {
        "required": True,
        "status": "SOURCE_COVERAGE_READY",
        "crs": "EPSG:27700",
        "files": sources,
        "selection_bounds_epsg27700": list(target.bounds),
        "source_coverage_ratio": 1.0,
        "coverage_tolerance_m2": 0.001,
    }


def apply_mandatory_dsm(
    vertices,
    faces,
    request: SelectionRequest,
    settings: BigImageSettings,
    config_document: Mapping[str, Any],
    config_path: Path | None,
    target_bbox_wgs84: Sequence[float],
    job_root: Path,
):
    import numpy as np

    dsm_dir, dsm_files, osm_dir, crs = _dsm_configuration(request, config_document, config_path)
    report = _validate_dsm_coverage(dsm_dir, dsm_files, target_bbox_wgs84)
    Config, DSMLoader, semantic_height_correction, OSMLoader = _import_top_level_mesh_pipeline(
        settings.repo_root.parent
    )
    pipeline_config = Config(work_dir=job_root / "dsm-work")
    pipeline_config.dsm_dir = dsm_dir
    pipeline_config.dsm_files = list(dsm_files)
    pipeline_config.osm_data_dir = osm_dir
    osm_loader = OSMLoader(pipeline_config)
    dsm_loader = DSMLoader(
        pipeline_config,
        apply_gaussian_filter=True,
        sigma=pipeline_config.dsm_gaussian_sigma,
    )
    if dsm_loader.is_empty:
        raise SelectionBridgeError("dsm_invalid", f"no mandatory DSM loaded from {dsm_dir}")
    from pyproj import Transformer

    lats = -vertices[:, 2] / 111_320.0 + request.frame.origin_lat
    lons = vertices[:, 0] / request.frame.meters_per_degree_lon + request.frame.origin_lon
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    eastings, northings = transformer.transform(lons, lats)
    heights = dsm_loader.query_heights_batch(eastings, northings)
    valid = np.isfinite(heights)
    if len(heights) < 100 or int(valid.sum()) != len(heights):
        raise SelectionBridgeError(
            "dsm_vertex_coverage_incomplete",
            f"mandatory DSM has valid heights for {int(valid.sum())}/{len(heights)} mesh vertices",
        )
    before = vertices[:, 1].copy()
    colours_before = vertices[:, 3:].copy()
    corrected, corrected_faces = semantic_height_correction(
        vertices,
        faces,
        osm_loader,
        dsm_loader,
        request.frame.origin_lat,
        request.frame.origin_lon,
        pipeline_config,
    )
    if corrected_faces.shape != faces.shape or not np.array_equal(corrected_faces, faces):
        raise SelectionBridgeError("dsm_changed_topology", "DSM correction unexpectedly changed face topology")
    if corrected.shape != vertices.shape or not np.array_equal(corrected[:, 3:], colours_before):
        raise SelectionBridgeError("dsm_changed_vertex_colors", "DSM correction unexpectedly changed vertex RGB")
    if not np.isfinite(corrected).all():
        raise SelectionBridgeError("dsm_invalid", "DSM correction produced non-finite vertices")
    report.update({
        "status": "APPLIED",
        "mesh_vertex_count": int(len(corrected)),
        "valid_height_count": int(valid.sum()),
        "mesh_vertex_coverage_ratio": 1.0,
        "corrected_vertex_count": int(np.count_nonzero(np.abs(corrected[:, 1] - before) > 1e-9)),
        "maximum_vertical_delta_m": float(np.max(np.abs(corrected[:, 1] - before))),
        "topology_changed": False,
        "vertex_colors_preserved": True,
    })
    return corrected, faces, report


def _numpy_to_obj_mesh(vertices, faces) -> ObjMesh:
    return ObjMesh(
        vertices=[tuple(float(value) for value in vertex) for vertex in vertices],
        faces=[tuple(int(value) for value in face) for face in faces],
    )


def _building_faces(mesh: ObjMesh, footprint, padding: float) -> set[int]:
    bounds = (
        footprint.bounds[0] - padding,
        footprint.bounds[1] - padding,
        footprint.bounds[2] + padding,
        footprint.bounds[3] + padding,
    )
    selected: set[int] = set()
    polygon = footprint.points
    for index, face in enumerate(mesh.faces):
        triangle = tuple((mesh.vertices[vertex][0], mesh.vertices[vertex][2]) for vertex in face)
        xs = (triangle[0][0], triangle[1][0], triangle[2][0])
        zs = (triangle[0][1], triangle[1][1], triangle[2][1])
        if max(xs) < bounds[0] or min(xs) > bounds[2] or max(zs) < bounds[1] or min(zs) > bounds[3]:
            continue
        if _triangle_near_polygon(triangle, polygon, padding):
            selected.add(index)
    return selected


def _base_result(request: SelectionRequest, plan: BigImagePlan, status: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "myProject.selection.result",
        "pipeline_contract_version": BIG_IMAGE_PIPELINE_CONTRACT_VERSION,
        "building_publication_version": BUILDING_PUBLICATION_VERSION,
        "osm_prealign": False,
        "merge_stage_order": list(BIG_IMAGE_STAGE_ORDER),
        "status": status,
        "selection_id": request.selection_id,
        "stable_id": request.stable_id,
        "workspace": str(request.workspace),
        "request": str(request.source),
        "output_dir": str(request.output_dir),
        "job_dir": str(request.job_dir),
        "model_source": "big_image",
        "plan": plan.to_dict(),
        "geometry_policy": {
            "source_treated_as_already_merged": True,
            "source_treated_as_bottom_filtered": True,
            "source_winding": "inward",
            "winding_correction": "reverse_all",
            "output_winding": "outward",
            "additional_bottom_face_filter": False,
            "additional_stitch": False,
            "face_deduplication": False,
            "component_filtering": False,
            "normal_or_winding_filter": False,
            "vertex_colors_required": True,
            "vertex_color_source": "Sat3DGen colour MLP with fractional-feather fusion weights",
            "vertex_color_fallback": "semantic BlockGen only; never synthesize placeholder RGB",
        },
        "footprints": [
            {"id": item.identifier, "area_m2": item.area_m2, "status": "PENDING"}
            for item in request.footprints
        ],
        "minimesh": {
            "status": "NOT_BUILT",
            "source_obj": None,
            "note": "Java converts each published building OBJ and keeps BlockGen profile operations unchanged",
        },
    }


def _failed_result(base: Mapping[str, Any], error: SelectionBridgeError) -> dict[str, Any]:
    failed = dict(base)
    failed.update(error.to_dict())
    failed["finished_local_time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return failed


def build_big_image_selection(
    request_path: str | os.PathLike[str], *, execute: bool = False
) -> dict[str, Any]:
    """Plan or build one GUI-selected block with app192 big-image inference."""

    request = load_selection_request(
        request_path,
        pipeline_contract_version=BIG_IMAGE_PIPELINE_CONTRACT_VERSION,
        require_osm_prealign=False,
    )
    if str(request.options.get("model_source", "")).strip().lower() != "big_image":
        raise SelectionBridgeError(
            "unsupported_model_source", "big-image contract requires options.model_source=big_image"
        )
    request.job_dir.mkdir(parents=True, exist_ok=True)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    settings, config_document, config_path = load_big_image_settings(request)
    plan = plan_big_image(request, settings)
    base = _base_result(request, plan, "PLANNED")
    job_root = request.job_dir / "big_image"
    plan_document = {
        "schema_version": 1,
        "kind": "myProject.selection.big_image_plan",
        "pipeline_contract_version": BIG_IMAGE_PIPELINE_CONTRACT_VERSION,
        "selection_id": request.selection_id,
        "stable_id": request.stable_id,
        "status": "PLANNED",
        "plan": plan.to_dict(),
        "settings": {
            "zoom": settings.zoom,
            "request_size_px": settings.request_size_px,
            "retained_cell_size_px": settings.retained_cell_size_px,
            "image_window_size_px": settings.image_window_size_px,
            "overlap": settings.overlap,
            "mesh_resolution": settings.mesh_resolution,
            "mesh_level": settings.mesh_level,
            "fusion_mode": settings.fusion_mode,
            "preserve_source_pixels": settings.preserve_source_pixels,
            "color_model_path": settings.color_model_path,
            "color_batch_size": settings.color_batch_size,
            "color_spatial_bin_size": settings.color_spatial_bin_size,
        },
        "script_sha256": {
            "download": _sha256(settings.download_script),
            "inference": _sha256(settings.inference_script),
            "colorize": _sha256(settings.colorize_script),
        },
        "stage_order": list(BIG_IMAGE_STAGE_ORDER),
        "geometry_policy": base["geometry_policy"],
    }
    _atomic_json(request.job_dir / "big_image_plan.json", plan_document)
    if not execute:
        _atomic_json(request.job_dir / "result.json", base)
        return base

    try:
        source = find_reusable_big_image_output(job_root, plan, settings)
        if source is None:
            source = generate_big_image_output(job_root, plan, settings)
        if source.get("vertex_colors") is not True:
            raise SelectionBridgeError("big_image_colour_missing", "validated big-image source has no vertex RGB")
        raw_vertices, raw_faces = _load_ply_arrays(Path(source["mesh_ply"]))
        transformed, transform_report = transform_big_image_vertices(
            raw_vertices,
            source["metadata"],
            source["manifest"],
            request,
        )
        outward_faces, winding_report = reverse_inward_face_winding(raw_faces)
        crop_bounds = _selection_local_bounds(request, settings.source_crop_padding_m)
        vertices, faces, crop_report = crop_arrays_to_local_bounds(
            transformed, outward_faces, crop_bounds
        )
        # Release the city-scale arrays before DSM constructs its adjacency.
        del raw_vertices, raw_faces, outward_faces, transformed
        vertices, faces, dsm_report = apply_mandatory_dsm(
            vertices,
            faces,
            request,
            settings,
            config_document,
            config_path,
            plan.target_bbox_wgs84,
            job_root,
        )
        import numpy as np

        ground_reference = float(np.percentile(vertices[:, 1], settings.ground_percentile))
        vertices[:, 1] -= ground_reference
        if not np.isfinite(vertices).all():
            raise SelectionBridgeError("big_image_ground_invalid", "ground normalization produced non-finite vertices")
        mesh = _numpy_to_obj_mesh(vertices, faces)
        del vertices, faces

        source_feature_ids = load_source_feature_ids(
            request.workspace, (footprint.identifier for footprint in request.footprints)
        )
        configured_mesh = config_document.get("mesh")
        minimum_relief = 1.5
        if isinstance(configured_mesh, Mapping):
            minimum_relief = float(configured_mesh.get("minimum_building_relief_m", minimum_relief))
        prepared = []
        entries: list[dict[str, Any]] = []
        footprint_reports: list[dict[str, Any]] = []
        root_faces: set[int] = set()
        for footprint in request.footprints:
            selected_faces = _building_faces(mesh, footprint, settings.building_crop_padding_m)
            metrics = building_subset_metrics(mesh, selected_faces, ground_height_m=0.0)
            metrics.update({
                "source_face_count": len(selected_faces),
                "face_deduplication_applied": False,
                "component_filtering_applied": False,
                "bottom_face_filter_applied": False,
                "stitch_applied": False,
                "source_winding": "inward",
                "winding_correction": "reverse_all",
                "output_winding": "outward",
                "face_ownership": "nonexclusive_polygon_intersection",
                "dsm_coverage_ratio": dsm_report["mesh_vertex_coverage_ratio"],
            })
            reasons: list[str] = []
            if not selected_faces:
                status = "EMPTY"
                reasons.append("no_source_triangle_intersects_footprint")
            elif float(metrics["relief_m"]) < minimum_relief:
                status = "REJECTED"
                reasons.append("building_relief_below_minimum")
            else:
                status = "COARSE_READY"
            publishable = status == "COARSE_READY"
            source_id = source_feature_ids.get(footprint.identifier)
            osm_type = None
            osm_id = None
            if source_id and "/" in source_id:
                osm_type, osm_id = source_id.split("/", 1)
            relative_dir = f"buildings/{footprint.identifier}"
            entry = {
                "id": footprint.identifier,
                "component_id": footprint.identifier,
                "footprint_id": footprint.identifier,
                "footprint_ids": [footprint.identifier],
                "source_feature_id": source_id,
                "osm_type": osm_type,
                "osm_id": osm_id,
                "status": status,
                "publishable": publishable,
                "relative_dir": relative_dir,
                "outputs": ({
                    "cropped_obj": f"{relative_dir}/cropped.obj",
                    "gis_obj": f"{relative_dir}/gis.obj",
                    "gis_footprints_obj": f"{relative_dir}/gis_footprints.obj",
                } if publishable else None),
                "extraction_method": "non_destructive_triangle_polygon_intersection_big_image_v3_colour",
                "metrics": metrics,
                "minimums": {"relief_m": minimum_relief},
                "reasons": reasons,
                "warnings": [
                    "COARSE_READY: source is cropped spatially without face cleanup or component filtering"
                ] if publishable else [],
            }
            entries.append(entry)
            footprint_reports.append({
                "id": footprint.identifier,
                "area_m2": footprint.area_m2,
                "status": status,
                "source_feature_id": source_id,
                "crop_face_count": len(selected_faces),
                "building_metrics": metrics,
                "building_reasons": reasons,
            })
            if publishable:
                root_faces.update(selected_faces)
                prepared.append((footprint, selected_faces, entry))
        if not prepared:
            raise SelectionBridgeError(
                "no_publishable_buildings",
                "the big-image mesh contains no publishable selected building",
                details={"buildings": entries},
            )

        staging = request.job_dir / f"big-image-publish-{uuid.uuid4().hex[:12]}"
        staging.mkdir(parents=True)
        try:
            root_stats = write_obj_subset(mesh, root_faces, staging / "cropped.obj")
            gis_stats = write_gis_obj(request.footprints, staging / "gis.obj")
            shutil.copy2(staging / "gis.obj", staging / "gis_footprints.obj")
            summary, building_index = stage_building_publication(
                staging,
                mesh,
                prepared,
                entries,
                selection_id=request.selection_id,
                stable_id=request.stable_id,
                pipeline_contract_version=BIG_IMAGE_PIPELINE_CONTRACT_VERSION,
            )
            source_summary = {
                "cache_hit": bool(source.get("cache_hit")),
                "cache_kind": source.get("cache_kind"),
                "output_root": source.get("output_root"),
                "mesh_ply": source.get("mesh_ply"),
                "geometry_mesh_ply": source.get("geometry_mesh_ply"),
                "color_metadata": source.get("color_metadata"),
                "color_preflight": source.get("color_preflight"),
                "vertex_colors": source.get("vertex_colors"),
                "vertex_color_encoding": source.get("vertex_color_encoding"),
                "color_output_sha256": source.get("color_output_sha256"),
                "run_metadata": source.get("run_metadata"),
                "mosaic": source.get("mosaic"),
                "mosaic_manifest": source.get("mosaic_manifest"),
                "mesh_size_bytes": source.get("mesh_size_bytes"),
                "geometry_mesh_size_bytes": source.get("geometry_mesh_size_bytes"),
            }
            ready = dict(base)
            ready.update({
                "status": "READY",
                "footprints": footprint_reports,
                "buildings_summary": summary,
                "buildings": entries,
                "model_completeness": {"enabled": False, "mode": "coarse_spatial_crop"},
                "pipeline": {
                    "kind": "Sat3DGen app192 big-image on-demand",
                    "pipeline_contract_version": BIG_IMAGE_PIPELINE_CONTRACT_VERSION,
                    "stage_order": list(BIG_IMAGE_STAGE_ORDER),
                    "source": source_summary,
                },
                "dsm": dsm_report,
                "workspace_frame": {
                    "origin_lat": request.frame.origin_lat,
                    "origin_lon": request.frame.origin_lon,
                    "axes": {"x": "east", "y": "up", "z": "south"},
                    "units": "m",
                },
                "transform": transform_report,
                "winding": winding_report,
                "source_crop": crop_report,
                "vertical_normalization": {
                    "ground_percentile": settings.ground_percentile,
                    "subtracted_ground_reference_m": ground_reference,
                },
                "outputs": {
                    "cropped_obj": str(request.output_dir / "cropped.obj"),
                    "gis_obj": str(request.output_dir / "gis.obj"),
                    "gis_footprints_obj": str(request.output_dir / "gis_footprints.obj"),
                    "buildings_index": str(request.output_dir / "buildings" / "index.json"),
                },
                "mesh": root_stats,
                "gis": gis_stats,
                "minimesh": {
                    "status": "PENDING_JAVA_CONVERSION",
                    "source_obj": str(request.output_dir / "cropped.obj"),
                    "note": "Java converts each building cropped.obj and loads BlockGen for profiles",
                },
                "finished_local_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
            _atomic_json(staging / "result.json", ready)
            _commit_ready(request, staging)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        plan_document["status"] = "READY"
        plan_document["source"] = source_summary
        plan_document["dsm"] = dsm_report
        _atomic_json(request.job_dir / "big_image_plan.json", plan_document)
        _atomic_json(request.job_dir / "result.json", ready)
        return ready
    except SelectionBridgeError as exc:
        failed = _failed_result(base, exc)
        _atomic_json(request.job_dir / "result.json", failed)
        plan_document["status"] = "FAILED"
        plan_document["error"] = exc.to_dict()["error"]
        _atomic_json(request.job_dir / "big_image_plan.json", plan_document)
        return failed
    except BaseException as exc:
        safe = SelectionBridgeError(
            "big_image_internal_error",
            f"big-image selection failed: {type(exc).__name__}: {redact_text(exc)}",
        )
        failed = _failed_result(base, safe)
        _atomic_json(request.job_dir / "result.json", failed)
        plan_document["status"] = "FAILED"
        plan_document["error"] = safe.to_dict()["error"]
        _atomic_json(request.job_dir / "big_image_plan.json", plan_document)
        return failed


__all__ = [
    "BIG_IMAGE_STAGE_ORDER",
    "BigImagePlan",
    "BigImageSettings",
    "build_big_image_selection",
    "crop_arrays_to_local_bounds",
    "find_reusable_big_image_output",
    "load_big_image_settings",
    "plan_big_image",
    "reverse_inward_face_winding",
    "transform_big_image_vertices",
    "validate_big_image_output",
]
