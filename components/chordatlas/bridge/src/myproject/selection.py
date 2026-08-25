"""Selection-scoped satellite and Sat3DGen bridge for the ChordAtlas GUI.

The GUI writes a small JSON request containing workspace-local footprint
polygons.  This module freezes an exact Web-Mercator tile allowlist before any
network or GPU work, validates every satellite PNG, invokes only the guarded
top-level Sat3DGen driver, and publishes each requested footprint as an
independent building only when that building passes its geometry gates.

The implementation intentionally uses the standard library.  In particular it
does not import Sat3DGen into the GUI/bridge process and it never accepts or
serialises an API key; the explicitly executed child inherits
``GOOGLE_MAPS_API_KEY`` from the environment.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import ssl
import struct
import sys
import time
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid
import zlib

from .geo import LocalFrame, METERS_PER_DEGREE_LAT
from .mesh_pipeline import (
    GeoBBox,
    TopLevelPipelineRequest,
    inspect_obj,
    run_top_level_pipeline,
)
from .roof_reference import generate_roof_references


WEB_MERCATOR_RADIUS_M = 6_378_137.0
WEB_MERCATOR_LIMIT_LAT = 85.05112878
WEB_MERCATOR_HALF_WORLD_M = math.pi * WEB_MERCATOR_RADIUS_M
WEB_MERCATOR_WORLD_M = 2.0 * WEB_MERCATOR_HALF_WORLD_M
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SECRET_QUERY_RE = re.compile(r"(?i)(?:key|api[_-]?key)=([^&\s]+)")
PIPELINE_CONTRACT_VERSION = "osm-prealign-v1"
BIG_IMAGE_PIPELINE_CONTRACT_VERSION = "big-image-app192-v3-vertex-colour"
BUILDING_PUBLICATION_VERSION = "per-footprint-v2"
MERGE_STAGE_ORDER = [
    "coordinate_transform",
    "osm_semantic_prealign",
    "remove_bottom_faces",
    "stitch_tiles",
    "dsm_height_correction",
    "export_scene",
]


class SelectionBridgeError(RuntimeError):
    """Structured, user-safe failure from a selection job."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.details = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "status": "FAILED",
            "error": {"code": self.code, "message": redact_text(str(self))},
        }
        if self.details:
            value["error"]["details"] = redact_value(self.details)
        return value


def redact_text(value: object, secrets: Iterable[str] = ()) -> str:
    """Remove API-key shaped query values and known secret strings."""

    text = str(value)
    text = SECRET_QUERY_RE.sub(lambda match: match.group(0).split("=", 1)[0] + "=<redacted>", text)
    environment_secret = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    for secret in tuple(secrets) + ((environment_secret,) if environment_secret else ()):
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if re.search(r"(?i)(api.?key|secret|token)", str(key)):
                output[str(key)] = "<redacted>"
            else:
                output[str(key)] = redact_value(item)
        return output
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, (str, os.PathLike)):
        return redact_text(value)
    return value


def _finite(name: str, value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SelectionBridgeError("invalid_request", f"{name} must be a number") from exc
    if not math.isfinite(result):
        raise SelectionBridgeError("invalid_request", f"{name} must be finite")
    return result


def _number_option(
    options: Mapping[str, Any],
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    maximum_inclusive: bool = True,
) -> float:
    value = _finite(f"options.{name}", options.get(name, default))
    if minimum is not None and value < minimum:
        raise SelectionBridgeError("invalid_request", f"options.{name} must be >= {minimum}")
    if maximum is not None:
        invalid = value > maximum if maximum_inclusive else value >= maximum
        if invalid:
            operator = "<=" if maximum_inclusive else "<"
            raise SelectionBridgeError("invalid_request", f"options.{name} must be {operator} {maximum}")
    return value


def _integer_option(
    options: Mapping[str, Any], name: str, default: int, *, minimum: int = 1
) -> int:
    raw = options.get(name, default)
    if isinstance(raw, bool):
        raise SelectionBridgeError("invalid_request", f"options.{name} must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise SelectionBridgeError("invalid_request", f"options.{name} must be an integer") from exc
    if value != raw and not (isinstance(raw, str) and str(value) == raw.strip()):
        raise SelectionBridgeError("invalid_request", f"options.{name} must be an integer")
    if value < minimum:
        raise SelectionBridgeError("invalid_request", f"options.{name} must be >= {minimum}")
    return value


def _identifier(name: str, value: Any) -> str:
    result = str(value or "").strip()
    if not IDENTIFIER_RE.fullmatch(result):
        raise SelectionBridgeError(
            "invalid_request",
            f"{name} must match {IDENTIFIER_RE.pattern}",
        )
    return result


def _signed_area(points: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(
        x1 * z2 - x2 * z1
        for (x1, z1), (x2, z2) in zip(points, points[1:] + points[:1])
    )


def _orientation(a, b, c, epsilon: float = 1e-9) -> int:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(cross) <= epsilon:
        return 0
    return 1 if cross > 0 else -1


def _point_on_segment(point, a, b, epsilon: float = 1e-8) -> bool:
    if _orientation(a, b, point, epsilon) != 0:
        return False
    return (
        min(a[0], b[0]) - epsilon <= point[0] <= max(a[0], b[0]) + epsilon
        and min(a[1], b[1]) - epsilon <= point[1] <= max(a[1], b[1]) + epsilon
    )


def _segments_intersect(a, b, c, d) -> bool:
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _point_on_segment(c, a, b))
        or (o2 == 0 and _point_on_segment(d, a, b))
        or (o3 == 0 and _point_on_segment(a, c, d))
        or (o4 == 0 and _point_on_segment(b, c, d))
    )


def _validate_simple_polygon(identifier: str, points: Sequence[tuple[float, float]]) -> None:
    if len(points) < 3:
        raise SelectionBridgeError("invalid_request", f"footprint {identifier} needs at least 3 points")
    if abs(_signed_area(points)) < 0.01:
        raise SelectionBridgeError("invalid_request", f"footprint {identifier} has near-zero area")
    count = len(points)
    for index in range(count):
        a, b = points[index], points[(index + 1) % count]
        if math.dist(a, b) <= 1e-8:
            raise SelectionBridgeError(
                "invalid_request", f"footprint {identifier} contains a zero-length edge"
            )
        for other in range(index + 1, count):
            if other in {index, (index + 1) % count} or index in {other, (other + 1) % count}:
                continue
            c, d = points[other], points[(other + 1) % count]
            if _segments_intersect(a, b, c, d):
                raise SelectionBridgeError(
                    "invalid_request", f"footprint {identifier} is self-intersecting"
                )


@dataclass(frozen=True)
class SelectedFootprint:
    identifier: str
    points: tuple[tuple[float, float], ...]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [point[0] for point in self.points]
        zs = [point[1] for point in self.points]
        return min(xs), min(zs), max(xs), max(zs)

    @property
    def area_m2(self) -> float:
        return abs(_signed_area(self.points))


@dataclass(frozen=True)
class SelectionRequest:
    source: Path
    workspace: Path
    output_dir: Path
    job_dir: Path
    selection_id: str
    stable_id: str
    footprints: tuple[SelectedFootprint, ...]
    options: Mapping[str, Any]
    workspace_manifest: Mapping[str, Any]
    frame: LocalFrame


@dataclass(frozen=True)
class PlannedTile:
    tile_id: str
    grid_x: int
    grid_y: int
    zoom: int
    size_px: int
    latitude: float
    longitude: float
    stem: str
    bounds_mercator_m: tuple[float, float, float, float]
    effective_mesh_bounds_mercator_m: tuple[float, float, float, float]
    satellite_path: Path
    mesh_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_id": self.tile_id,
            "grid_x": self.grid_x,
            "grid_y": self.grid_y,
            "zoom": self.zoom,
            "size_px": self.size_px,
            "lat": self.latitude,
            "lon": self.longitude,
            "stem": self.stem,
            "bounds_mercator_m": list(self.bounds_mercator_m),
            "effective_mesh_bounds_mercator_m": list(
                self.effective_mesh_bounds_mercator_m
            ),
            "satellite_path": str(self.satellite_path),
            "mesh_path": str(self.mesh_path),
            # The contract revision changes scene alignment/merge only.  A
            # validated per-tile inference OBJ is content-addressed by the
            # stable selection/tile stem and is safe to reuse offline.
            "reuse_existing_mesh": True,
        }


@dataclass
class ObjMesh:
    vertices: list[tuple[float, ...]]
    faces: list[tuple[int, int, int]]


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionBridgeError("invalid_request", f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SelectionBridgeError("invalid_request", f"{label} must contain a JSON object")
    return value


def _stable_from_selection(selection_id: str) -> str:
    value = selection_id[len("selection-") :] if selection_id.startswith("selection-") else selection_id
    return _identifier("selection stable id", value)


def _canonical_footprint_id(points: Sequence[tuple[float, float]]) -> str:
    """Reproduce SelectedBlockMeshService.canonicalLoop() and its stable ID."""

    formatted = [f"{x:.6f},{z:.6f}" for x, z in points]
    if not formatted:
        raise ValueError("cannot canonicalise an empty footprint")
    candidates: list[str] = []
    count = len(formatted)
    for direction in (1, -1):
        for start in range(count):
            candidates.append(
                ";".join(formatted[(start + direction * offset) % count] for offset in range(count))
            )
    digest = hashlib.sha256(min(candidates).encode("utf-8")).hexdigest()[:12]
    return f"footprint-{digest}"


def load_source_feature_ids(
    workspace: Path, wanted_ids: Iterable[str]
) -> dict[str, str]:
    """Recover OSM feature IDs from the workspace footprint OBJ when present.

    Java requests intentionally contain only geometry and its stable hash.  The
    workspace OBJ retains comments such as ``# feature_id way/4266528``; this
    parser joins them without guessing or treating Sat3DGen array positions as
    OSM identifiers.  Metadata recovery is optional and never blocks geometry
    publication.
    """

    wanted = set(wanted_ids)
    if not wanted:
        return {}
    path = workspace / "footprints.obj"
    if not path.is_file():
        return {}
    vertices: list[tuple[float, float]] = []
    feature_id: str | None = None
    matched: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8-sig", errors="strict") as stream:
            for raw in stream:
                stripped = raw.strip()
                if stripped.startswith("o "):
                    feature_id = None
                elif stripped.startswith("# feature_id "):
                    feature_id = stripped[len("# feature_id ") :].strip() or None
                elif stripped.startswith("v "):
                    words = stripped.split()
                    if len(words) >= 4:
                        vertices.append((float(words[1]), float(words[3])))
                elif stripped.startswith("f ") and feature_id:
                    polygon: list[tuple[float, float]] = []
                    for word in stripped.split()[1:]:
                        raw_index = int(word.split("/", 1)[0])
                        index = raw_index - 1 if raw_index > 0 else len(vertices) + raw_index
                        if not 0 <= index < len(vertices):
                            polygon = []
                            break
                        polygon.append(vertices[index])
                    if len(polygon) >= 3:
                        stable_id = _canonical_footprint_id(polygon)
                        if stable_id in wanted:
                            matched[stable_id] = feature_id
                            if len(matched) == len(wanted):
                                break
    except (OSError, UnicodeError, ValueError):
        return {}
    return matched


def load_selection_request(
    request_path: str | os.PathLike[str],
    *,
    pipeline_contract_version: str = PIPELINE_CONTRACT_VERSION,
    require_osm_prealign: bool = True,
) -> SelectionRequest:
    source = Path(request_path).expanduser().resolve(strict=False)
    document = _read_json_object(source, "selection request")
    for key in document:
        if re.search(r"(?i)(api.?key|secret|token)", str(key)):
            raise SelectionBridgeError(
                "secret_in_request",
                "API keys and tokens are not accepted in request JSON; use the process environment",
            )
    workspace_raw = document.get("workspace")
    if not isinstance(workspace_raw, (str, os.PathLike)):
        raise SelectionBridgeError("invalid_request", "workspace is required")
    workspace = Path(workspace_raw).expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise SelectionBridgeError("workspace_missing", f"workspace does not exist: {workspace}")
    manifest_path = workspace / "manifest.json"
    manifest = _read_json_object(manifest_path, "workspace manifest")
    frame_data = manifest.get("frame")
    if not isinstance(frame_data, Mapping):
        raise SelectionBridgeError("workspace_frame_missing", "workspace manifest has no frame object")
    if str(frame_data.get("units", "m")) != "m":
        raise SelectionBridgeError("workspace_frame_invalid", "workspace frame units must be metres")
    axes = frame_data.get("axes", {})
    if isinstance(axes, Mapping):
        if axes.get("x", "east") != "east" or axes.get("z", "south") != "south":
            raise SelectionBridgeError(
                "workspace_frame_invalid", "workspace frame must use X east and Z south"
            )
    try:
        frame = LocalFrame(frame_data["origin_lat"], frame_data["origin_lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SelectionBridgeError("workspace_frame_invalid", f"invalid workspace frame: {exc}") from exc

    selection_id = _identifier("selection_id", document.get("selection_id"))
    stable_id = _stable_from_selection(selection_id)
    raw_footprints = document.get("footprints")
    if not isinstance(raw_footprints, list) or not raw_footprints:
        raise SelectionBridgeError("invalid_request", "footprints must be a non-empty array")
    footprints: list[SelectedFootprint] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_footprints):
        if not isinstance(raw, Mapping):
            raise SelectionBridgeError("invalid_request", f"footprints[{index}] must be an object")
        identifier = _identifier(f"footprints[{index}].id", raw.get("id"))
        if identifier in seen:
            raise SelectionBridgeError("invalid_request", f"duplicate footprint id: {identifier}")
        seen.add(identifier)
        raw_points = raw.get("points")
        if not isinstance(raw_points, list):
            raise SelectionBridgeError(
                "invalid_request", f"footprints[{index}].points must be an array"
            )
        points: list[tuple[float, float]] = []
        for point_index, raw_point in enumerate(raw_points):
            if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
                raise SelectionBridgeError(
                    "invalid_request",
                    f"footprints[{index}].points[{point_index}] must be [x,z]",
                )
            points.append(
                (
                    _finite(f"footprints[{index}].points[{point_index}][0]", raw_point[0]),
                    _finite(f"footprints[{index}].points[{point_index}][1]", raw_point[1]),
                )
            )
        if len(points) > 1 and math.dist(points[0], points[-1]) <= 1e-8:
            points.pop()
        _validate_simple_polygon(identifier, points)
        footprints.append(SelectedFootprint(identifier, tuple(points)))

    raw_options = document.get("options", {})
    if not isinstance(raw_options, Mapping):
        raise SelectionBridgeError("invalid_request", "options must be an object")
    for key in raw_options:
        if re.search(r"(?i)(api.?key|secret|token)", str(key)):
            raise SelectionBridgeError(
                "secret_in_request",
                "API keys and tokens are not accepted in request JSON; use the process environment",
            )
    if raw_options.get("require_complete_buildings", True) is not True:
        raise SelectionBridgeError(
            "partial_publication_forbidden",
            "require_complete_buildings must remain true for GUI selection jobs",
        )
    options = dict(raw_options)
    contract_version = str(
        options.get("pipeline_contract_version", pipeline_contract_version)
    ).strip()
    if contract_version != pipeline_contract_version:
        raise SelectionBridgeError(
            "unsupported_pipeline_contract",
            f"pipeline_contract_version must be {pipeline_contract_version}",
        )
    if require_osm_prealign and options.get("osm_prealign", True) is not True:
        raise SelectionBridgeError(
            "osm_prealign_required",
            "osm_prealign must remain true for this pipeline contract",
        )
    # Old request.json files predate these fields.  Treat absence as the
    # current contract so their satellite and per-tile mesh cache can be
    # re-merged without changing the stable selection directory.
    options["pipeline_contract_version"] = pipeline_contract_version
    if require_osm_prealign:
        options["osm_prealign"] = True

    generated_root = (workspace / "generated_blocks").resolve(strict=False)
    requested_parent = source.parent.resolve(strict=False)
    if requested_parent.parent == generated_root:
        output_dir = requested_parent
    else:
        output_dir = generated_root / stable_id
    try:
        output_dir.relative_to(generated_root)
    except ValueError as exc:
        raise SelectionBridgeError("invalid_request", "selection output escapes generated_blocks") from exc
    job_dir = workspace / "_selection_jobs" / stable_id
    return SelectionRequest(
        source=source,
        workspace=workspace,
        output_dir=output_dir,
        job_dir=job_dir,
        selection_id=selection_id,
        stable_id=stable_id,
        footprints=tuple(footprints),
        options=options,
        workspace_manifest=manifest,
        frame=frame,
    )


def lonlat_to_web_mercator(lon: float, lat: float) -> tuple[float, float]:
    lon = _finite("longitude", lon)
    lat = _finite("latitude", lat)
    if not -180.0 <= lon <= 180.0:
        raise SelectionBridgeError("coordinate_out_of_range", "longitude is outside [-180,180]")
    lat = max(-WEB_MERCATOR_LIMIT_LAT, min(WEB_MERCATOR_LIMIT_LAT, lat))
    x = WEB_MERCATOR_RADIUS_M * math.radians(lon)
    y = WEB_MERCATOR_RADIUS_M * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))
    return x, y


def web_mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(float(x) / WEB_MERCATOR_RADIUS_M)
    lat = math.degrees(2.0 * math.atan(math.exp(float(y) / WEB_MERCATOR_RADIUS_M)) - math.pi / 2.0)
    return lon, lat


def _expanded_local_bounds(
    footprints: Sequence[SelectedFootprint], padding_m: float
) -> tuple[float, float, float, float]:
    min_x = min(item.bounds[0] for item in footprints) - padding_m
    min_z = min(item.bounds[1] for item in footprints) - padding_m
    max_x = max(item.bounds[2] for item in footprints) + padding_m
    max_z = max(item.bounds[3] for item in footprints) + padding_m
    return min_x, min_z, max_x, max_z


def _local_bbox_to_mercator(
    bounds: tuple[float, float, float, float], frame: LocalFrame
) -> tuple[float, float, float, float]:
    min_x, min_z, max_x, max_z = bounds
    converted = []
    for x in (min_x, max_x):
        for z in (min_z, max_z):
            lon, lat = frame.to_wgs84(x, z)
            converted.append(lonlat_to_web_mercator(lon, lat))
    return (
        min(point[0] for point in converted),
        min(point[1] for point in converted),
        max(point[0] for point in converted),
        max(point[1] for point in converted),
    )


def plan_web_mercator_tiles(
    footprints: Sequence[SelectedFootprint],
    frame: LocalFrame,
    job_dir: Path,
    *,
    zoom: int = 20,
    size_px: int = 640,
    overlap_ratio: float = 0.10,
    padding_m: float = 30.0,
    crop_ratio: float = 0.05,
) -> tuple[list[PlannedTile], dict[str, Any]]:
    """Plan a globally anchored, deterministic Static Maps image grid.

    ``padding_m`` is clamped to at least 30 m.  Tile indices are anchored at
    the Web-Mercator world edge, so neighbouring GUI selections choose the same
    centres rather than selection-relative grids.
    """

    if not footprints:
        raise SelectionBridgeError("invalid_request", "at least one footprint is required")
    if isinstance(zoom, bool) or not isinstance(zoom, int) or not 0 <= zoom <= 23:
        raise SelectionBridgeError("invalid_request", "zoom must be an integer in [0,23]")
    if isinstance(size_px, bool) or not isinstance(size_px, int) or not 1 <= size_px <= 640:
        raise SelectionBridgeError("invalid_request", "tile_size must be an integer in [1,640]")
    overlap_ratio = _finite("overlap_ratio", overlap_ratio)
    if not 0.0 <= overlap_ratio < 1.0:
        raise SelectionBridgeError("invalid_request", "overlap_ratio must be in [0,1)")
    crop_ratio = _finite("crop_ratio", crop_ratio)
    if not 0.0 <= crop_ratio < 0.5:
        raise SelectionBridgeError("invalid_request", "crop_ratio must be in [0,0.5)")
    padding_m = max(30.0, _finite("padding_m", padding_m))
    if padding_m < 0:
        raise SelectionBridgeError("invalid_request", "padding_m must be non-negative")

    local_bounds = _expanded_local_bounds(footprints, padding_m)
    target = _local_bbox_to_mercator(local_bounds, frame)
    image_span = WEB_MERCATOR_WORLD_M * size_px / (256.0 * (2**zoom))
    stride = image_span * (1.0 - overlap_ratio)
    half = image_span / 2.0
    effective_half = half * (1.0 - crop_ratio)
    if stride > effective_half * 2.0 + 1e-9:
        raise SelectionBridgeError(
            "tile_plan_gap",
            "overlap_ratio is too small for crop_ratio; effective mesh tiles would have gaps",
            details={"overlap_ratio": overlap_ratio, "crop_ratio": crop_ratio},
        )
    anchor = -WEB_MERCATOR_HALF_WORLD_M + half

    def index_range(low: float, high: float) -> range:
        # Plan against the post-crop mesh footprint, not the larger raw image.
        first = math.ceil((low - effective_half - anchor) / stride - 1e-12)
        last = math.floor((high + effective_half - anchor) / stride + 1e-12)
        return range(first, last + 1)

    planned: list[PlannedTile] = []
    seen_stems: set[str] = set()
    satellite_dir = job_dir / "satellite"
    mesh_dir = job_dir / "meshes"
    for grid_y in index_range(target[1], target[3]):
        center_y = anchor + grid_y * stride
        for grid_x in index_range(target[0], target[2]):
            center_x = anchor + grid_x * stride
            lon, lat = web_mercator_to_lonlat(center_x, center_y)
            lat, lon = round(lat, 6), round(lon, 6)
            rounded_x, rounded_y = lonlat_to_web_mercator(lon, lat)
            stem = f"sat_{lat:.6f}_{lon:.6f}"
            if stem in seen_stems:
                continue
            seen_stems.add(stem)
            planned.append(
                PlannedTile(
                    tile_id=f"z{zoom}_x{grid_x}_y{grid_y}",
                    grid_x=grid_x,
                    grid_y=grid_y,
                    zoom=zoom,
                    size_px=size_px,
                    latitude=lat,
                    longitude=lon,
                    stem=stem,
                    bounds_mercator_m=(
                        rounded_x - half,
                        rounded_y - half,
                        rounded_x + half,
                        rounded_y + half,
                    ),
                    effective_mesh_bounds_mercator_m=(
                        rounded_x - effective_half,
                        rounded_y - effective_half,
                        rounded_x + effective_half,
                        rounded_y + effective_half,
                    ),
                    satellite_path=satellite_dir / f"{stem}.png",
                    mesh_path=mesh_dir / stem / f"{stem}.obj",
                )
            )
    if not planned:
        raise SelectionBridgeError("tile_plan_empty", "Web-Mercator tile planner produced no tiles")

    center_lat = sum(tile.latitude for tile in planned) / len(planned)
    lon_span_degrees = math.degrees(image_span / WEB_MERCATOR_RADIUS_M)
    _, north = web_mercator_to_lonlat(0.0, sum(tile.bounds_mercator_m[3] for tile in planned) / len(planned))
    _, south = web_mercator_to_lonlat(0.0, sum(tile.bounds_mercator_m[1] for tile in planned) / len(planned))
    lat_span_degrees = abs(north - south)
    metadata = {
        "scheme": "web_mercator_global_anchor_v1",
        "zoom": zoom,
        "size_px": size_px,
        "overlap_ratio": overlap_ratio,
        "crop_ratio": crop_ratio,
        "padding_m": padding_m,
        "image_span_web_mercator_m": image_span,
        "effective_mesh_span_web_mercator_m": effective_half * 2.0,
        "stride_web_mercator_m": stride,
        "target_local_bounds": list(local_bounds),
        "target_mercator_bounds": list(target),
        "target_bbox_wgs84": list(_local_bounds_to_wgs84(local_bounds, frame)),
        "representative_latitude": center_lat,
        # The top-level merger multiplies these by (1 + overlap_ratio).
        "upstream_lon_step": lon_span_degrees / (1.0 + overlap_ratio),
        "upstream_lat_step": lat_span_degrees / (1.0 + overlap_ratio),
    }
    return planned, metadata


def _local_bounds_to_wgs84(bounds, frame: LocalFrame) -> tuple[float, float, float, float]:
    min_x, min_z, max_x, max_z = bounds
    coordinates = [frame.to_wgs84(x, z) for x in (min_x, max_x) for z in (min_z, max_z)]
    return (
        min(item[0] for item in coordinates),
        min(item[1] for item in coordinates),
        max(item[0] for item in coordinates),
        max(item[1] for item in coordinates),
    )


def validate_png(path: str | os.PathLike[str], expected_size: int | None = None) -> dict[str, Any]:
    """Validate PNG framing, chunk CRCs, IHDR dimensions, IDAT and IEND."""

    image = Path(path)
    if not image.is_file():
        raise SelectionBridgeError("satellite_missing", f"satellite PNG is missing: {image}")
    try:
        data = image.read_bytes()
    except OSError as exc:
        raise SelectionBridgeError("satellite_unreadable", f"cannot read satellite PNG: {image}") from exc
    if not data.startswith(PNG_SIGNATURE):
        raise SelectionBridgeError(
            "satellite_not_png",
            f"satellite response is not PNG: {image}",
            details={"bytes": len(data)},
        )
    offset = len(PNG_SIGNATURE)
    width = height = None
    saw_idat = saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise SelectionBridgeError("satellite_png_truncated", f"truncated PNG chunk: {image}")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise SelectionBridgeError("satellite_png_truncated", f"truncated PNG data: {image}")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(kind)
        actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise SelectionBridgeError("satellite_png_crc", f"PNG CRC mismatch: {image}")
        if kind == b"IHDR":
            if length != 13 or width is not None:
                raise SelectionBridgeError("satellite_png_ihdr", f"invalid PNG IHDR: {image}")
            width, height = struct.unpack(">II", payload[:8])
        elif kind == b"IDAT":
            saw_idat = saw_idat or bool(payload)
        elif kind == b"IEND":
            saw_iend = True
            if length != 0:
                raise SelectionBridgeError("satellite_png_iend", f"invalid PNG IEND: {image}")
            break
        offset = end
    if width is None or not saw_idat or not saw_iend:
        raise SelectionBridgeError("satellite_png_incomplete", f"incomplete PNG: {image}")
    if expected_size is not None and (width != expected_size or height != expected_size):
        raise SelectionBridgeError(
            "satellite_wrong_size",
            f"satellite PNG must be {expected_size}x{expected_size}: {image}",
            details={"actual_width": width, "actual_height": height},
        )
    return {"path": str(image), "width": width, "height": height, "bytes": len(data)}


def _download_satellite(tile: PlannedTile, *, minimum_bytes: int = 5000) -> dict[str, Any]:
    key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise SelectionBridgeError(
            "api_key_missing",
            "satellite download requires GOOGLE_MAPS_API_KEY in the process environment",
            details={"tile_id": tile.tile_id, "stem": tile.stem},
        )
    query = urlencode(
        {
            "center": f"{tile.latitude:.6f},{tile.longitude:.6f}",
            "zoom": tile.zoom,
            "size": f"{tile.size_px}x{tile.size_px}",
            "maptype": "satellite",
            "format": "png",
            "key": key,
        }
    )
    request = Request(
        "https://maps.googleapis.com/maps/api/staticmap?" + query,
        headers={"User-Agent": "myProject-selection-bridge/1"},
    )
    context = _verified_https_context()
    tile.satellite_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tile.satellite_path.with_suffix(".png.part")
    try:
        with urlopen(request, timeout=30, context=context) as response:
            status = int(getattr(response, "status", 200))
            content_type = str(response.headers.get("Content-Type", ""))
            body = response.read()
    except HTTPError as exc:
        try:
            preview = exc.read(512).decode("utf-8", "replace")
        except Exception:
            preview = ""
        raise SelectionBridgeError(
            "satellite_http_error",
            f"satellite download failed with HTTP {exc.code}",
            details={
                "tile_id": tile.tile_id,
                "stem": tile.stem,
                "http_status": int(exc.code),
                "reason": redact_text(exc.reason),
                "response_preview": redact_text(preview),
            },
        ) from None
    except URLError as exc:
        raise SelectionBridgeError(
            "satellite_network_error",
            "satellite download could not reach the provider",
            details={
                "tile_id": tile.tile_id,
                "stem": tile.stem,
                "reason": redact_text(exc.reason),
            },
        ) from None
    except OSError as exc:
        raise SelectionBridgeError(
            "satellite_io_error",
            "satellite download failed while writing data",
            details={"tile_id": tile.tile_id, "stem": tile.stem, "reason": redact_text(exc)},
        ) from None
    if status != 200:
        raise SelectionBridgeError(
            "satellite_http_error",
            f"satellite download failed with HTTP {status}",
            details={"tile_id": tile.tile_id, "stem": tile.stem, "http_status": status},
        )
    if "png" not in content_type.lower():
        raise SelectionBridgeError(
            "satellite_content_type",
            "satellite provider did not return PNG data",
            details={"tile_id": tile.tile_id, "stem": tile.stem, "content_type": content_type},
        )
    if len(body) < minimum_bytes:
        raise SelectionBridgeError(
            "satellite_response_too_small",
            "satellite PNG response is unexpectedly small",
            details={"tile_id": tile.tile_id, "stem": tile.stem, "bytes": len(body)},
        )
    try:
        temporary.write_bytes(body)
        validation = validate_png(temporary, tile.size_px)
        os.replace(temporary, tile.satellite_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    validation["path"] = str(tile.satellite_path)
    validation["source"] = "downloaded"
    return validation


def _verified_https_context() -> ssl.SSLContext:
    """Build a verified TLS context without loading the Windows cert store.

    Python 3.10's ``load_default_certs`` also imports every Windows certificate.
    A single malformed system certificate can therefore abort otherwise valid
    HTTPS requests with ``ASN1: NOT_ENOUGH_DATA``.  Conda already exposes its
    maintained CA bundle through SSL_CERT_FILE; passing that bundle explicitly
    keeps verification enabled while avoiding the unrelated Windows-store
    import.  No certificate or Conda setting is modified.
    """

    defaults = ssl.get_default_verify_paths()
    cafile = os.environ.get("SSL_CERT_FILE") or defaults.cafile
    capath = os.environ.get("SSL_CERT_DIR") or defaults.capath
    arguments: dict[str, str] = {}
    if cafile and Path(cafile).is_file():
        arguments["cafile"] = str(Path(cafile).resolve(strict=False))
    if capath and Path(capath).is_dir():
        arguments["capath"] = str(Path(capath).resolve(strict=False))
    if not arguments:
        raise SelectionBridgeError(
            "tls_ca_missing",
            "no explicit CA bundle or certificate directory is available for satellite HTTPS",
        )
    try:
        return ssl.create_default_context(**arguments)
    except (OSError, ssl.SSLError) as exc:
        raise SelectionBridgeError(
            "tls_ca_invalid",
            "the configured CA bundle could not initialise satellite HTTPS",
            details={"reason": redact_text(exc)},
        ) from None


def ensure_satellites(
    tiles: Sequence[PlannedTile], *, download_missing: bool = True, minimum_bytes: int = 5000
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    validations: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for tile in tiles:
        try:
            validation = validate_png(tile.satellite_path, tile.size_px)
            validation["source"] = "existing-job-file"
            validations.append({"tile_id": tile.tile_id, "stem": tile.stem, **validation})
            continue
        except SelectionBridgeError as existing_error:
            if not download_missing:
                failures.append({
                    "tile_id": tile.tile_id,
                    "stem": tile.stem,
                    "error": existing_error.to_dict()["error"],
                })
                continue
        try:
            validation = _download_satellite(tile, minimum_bytes=minimum_bytes)
            validations.append({"tile_id": tile.tile_id, "stem": tile.stem, **validation})
        except SelectionBridgeError as exc:
            failures.append({"tile_id": tile.tile_id, "stem": tile.stem, "error": exc.to_dict()["error"]})
    return validations, failures


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(redact_value(document), indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _request_config_document(request: SelectionRequest) -> tuple[dict[str, Any], Path | None]:
    configured = request.workspace_manifest.get("config")
    if not isinstance(configured, str) or not configured.strip():
        return {}, None
    config_path = Path(configured).expanduser()
    if not config_path.is_absolute():
        config_path = request.workspace / config_path
    config_path = config_path.resolve(strict=False)
    if not config_path.is_file():
        return {}, config_path
    return _read_json_object(config_path, "workspace source config"), config_path


def _configured_path(
    request: SelectionRequest,
    option_name: str,
    config: Mapping[str, Any],
    config_path: Path | None,
    config_name: str,
    fallback: str,
) -> Path:
    raw = request.options.get(option_name)
    if raw is None and isinstance(config.get("paths"), Mapping):
        raw = config["paths"].get(config_name)
    if raw is None:
        raw = fallback
    path = Path(str(raw)).expanduser()
    if not path.is_absolute() and config_path is not None:
        path = config_path.parent / path
    return path.resolve(strict=False)


def _configured_mesh_path(
    request: SelectionRequest,
    config: Mapping[str, Any],
    config_path: Path | None,
    name: str,
) -> Path:
    mesh_config = config.get("mesh")
    raw = request.options.get(name)
    if raw is None and isinstance(mesh_config, Mapping):
        raw = mesh_config.get(name)
    if not isinstance(raw, (str, os.PathLike)) or not str(raw).strip():
        raise SelectionBridgeError("dsm_required", f"mesh.{name} is required for on-demand DSM correction")
    path = Path(raw).expanduser()
    if not path.is_absolute() and config_path is not None:
        path = config_path.parent / path
    return path.resolve(strict=False)


def _pipeline_request(
    request: SelectionRequest,
    tiles: Sequence[PlannedTile],
    plan: Mapping[str, Any],
    tile_manifest_path: Path,
) -> TopLevelPipelineRequest:
    config, config_path = _request_config_document(request)
    mesh_config = config.get("mesh")
    if not isinstance(mesh_config, Mapping):
        raise SelectionBridgeError("dsm_required", "workspace source config has no mesh object")
    if request.options.get("apply_dsm", mesh_config.get("apply_dsm")) is not True:
        raise SelectionBridgeError("dsm_required", "on-demand selection requires mesh.apply_dsm=true")
    dsm_dir = _configured_mesh_path(request, config, config_path, "dsm_dir")
    osm_dir = _configured_mesh_path(request, config, config_path, "osm_dir")
    raw_dsm_files = request.options.get("dsm_files", mesh_config.get("dsm_files"))
    if not isinstance(raw_dsm_files, list) or not raw_dsm_files:
        raise SelectionBridgeError("dsm_required", "mesh.dsm_files must be a non-empty array")
    dsm_files: list[str] = []
    for index, value in enumerate(raw_dsm_files):
        if not isinstance(value, str) or Path(value).name != value or Path(value).suffix.lower() not in {".tif", ".tiff"}:
            raise SelectionBridgeError(
                "dsm_required", f"mesh.dsm_files[{index}] must be a GeoTIFF basename"
            )
        if value in dsm_files:
            raise SelectionBridgeError("dsm_required", f"duplicate DSM filename: {value}")
        dsm_files.append(value)
    dsm_crs = str(request.options.get("dsm_crs", mesh_config.get("dsm_crs", ""))).upper()
    if dsm_crs != "EPSG:27700":
        raise SelectionBridgeError("dsm_required", "on-demand DSM CRS must be EPSG:27700")
    missing_dsm = [str(dsm_dir / name) for name in dsm_files if not (dsm_dir / name).is_file()]
    if missing_dsm:
        raise SelectionBridgeError(
            "dsm_missing", "one or more mandatory DSM GeoTIFF files are missing",
            details={"missing": missing_dsm},
        )
    if not (osm_dir / "building.geojson").is_file():
        raise SelectionBridgeError(
            "dsm_osm_missing",
            "DSM semantic correction requires osm_dir/building.geojson",
            details={"osm_dir": str(osm_dir)},
        )
    submission_root = Path(__file__).resolve().parents[5]
    sat3dgen_root = _configured_path(
        request,
        "sat3dgen_root",
        config,
        config_path,
        "sat3dgen_root",
        submission_root / "components" / "sat3dgen",
    )
    conda_executable = _configured_path(
        request,
        "conda_executable",
        config,
        config_path,
        "conda_executable",
        "conda",
    )
    if str(conda_executable).lower().endswith("conda") and not conda_executable.exists():
        conda_value = "conda"
    else:
        conda_value = str(conda_executable)
    bbox = plan["target_bbox_wgs84"]
    environment = str(
        request.options.get(
            "conda_environment",
            request.workspace_manifest.get("chordatlas", {}).get("conda_environment", "sat3dgen"),
        )
    )
    return TopLevelPipelineRequest(
        bbox=GeoBBox(*bbox),
        work_dir=request.job_dir,
        sat3dgen_root=sat3dgen_root,
        driver_path=Path(__file__).resolve().parents[2] / "top_level_mesh_driver.py",
        name=f"selection_{request.stable_id}",
        conda_environment=environment,
        conda_executable=conda_value,
        exact_tile_manifest=tile_manifest_path,
        pipeline_contract_version=PIPELINE_CONTRACT_VERSION,
        tile_source="exact_manifest",
        lat_step=float(plan["upstream_lat_step"]),
        lon_step=float(plan["upstream_lon_step"]),
        overlap_ratio=float(plan["overlap_ratio"]),
        crop_ratio=float(plan["crop_ratio"]),
        download_missing=False,
        run_inference=bool(request.options.get("run_inference", True)),
        allow_partial=False,
        osm_dir=osm_dir,
        osm_prealign=True,
        dsm_dir=dsm_dir,
        dsm_files=tuple(dsm_files),
        dsm_crs=dsm_crs,
        apply_dsm=True,
        mesh_resolution=_integer_option(
            request.options,
            "mesh_resolution",
            int(mesh_config.get("mesh_resolution", 192)),
        ),
        zoom=int(plan["zoom"]),
        tile_size=int(plan["size_px"]),
        gradio_url=str(request.options.get("gradio_url", "http://localhost:7860")),
        use_current_python=(
            os.environ.get("CONDA_DEFAULT_ENV", "").strip().casefold()
            == environment.strip().casefold()
        ),
    )


def load_reusable_pipeline_manifest(
    path: Path,
    request: SelectionRequest,
    tiles: Sequence[PlannedTile],
    pipeline_request: TopLevelPipelineRequest,
) -> dict[str, Any] | None:
    """Return a still-valid DSM-corrected scene manifest, otherwise ``None``.

    This cache is intentionally stricter than per-tile inference reuse.  The
    scene must use the current merge contract, exact mesh allowlist and DSM
    files, live below this selection job's ``final`` directory, and be no older
    than any mesh/DSM/OSM input.  Thus a publication-format-only upgrade can
    split an existing corrected scene without repeating GPU, merge or DSM work.
    """

    if request.options.get("reuse_corrected_scene", True) is not True or not path.is_file():
        return None
    try:
        document = _read_json_object(path, "cached top-level pipeline manifest")
        if (
            document.get("status") != "ok"
            or document.get("pipeline_contract_version") != PIPELINE_CONTRACT_VERSION
            or document.get("osm_prealign") is not True
            or document.get("merge_stage_order") != MERGE_STAGE_ORDER
            or document.get("missing_mesh") not in ([], None)
        ):
            return None
        scene_raw = document.get("output_scene_obj")
        selected_raw = document.get("selected_meshes")
        if not isinstance(scene_raw, str) or not isinstance(selected_raw, list):
            return None
        scene = Path(scene_raw).resolve(strict=False)
        scene.relative_to((request.job_dir / "final").resolve(strict=False))
        if not scene.is_file() or scene.stat().st_size <= 0:
            return None
        expected_meshes = {tile.mesh_path.resolve(strict=False) for tile in tiles}
        selected_meshes = {
            Path(value).resolve(strict=False) for value in selected_raw if isinstance(value, str)
        }
        if selected_meshes != expected_meshes or not all(item.is_file() for item in expected_meshes):
            return None
        dsm = document.get("dsm")
        if (
            not isinstance(dsm, Mapping)
            or dsm.get("required") is not True
            or dsm.get("status") != "APPLIED"
            or str(dsm.get("crs", "")).upper() != pipeline_request.dsm_crs
            or not math.isclose(
                _finite("cached DSM mesh vertex coverage", dsm.get("mesh_vertex_coverage_ratio")),
                1.0,
                abs_tol=1e-12,
            )
        ):
            return None
        raw_dsm_files = dsm.get("files")
        if not isinstance(raw_dsm_files, list):
            return None
        cached_dsm_names = {
            str(item.get("name")) for item in raw_dsm_files if isinstance(item, Mapping)
        }
        if cached_dsm_names != set(pipeline_request.dsm_files):
            return None
        inputs = list(expected_meshes)
        inputs.extend(pipeline_request.dsm_dir / name for name in pipeline_request.dsm_files)
        inputs.extend(sorted(pipeline_request.osm_dir.glob("*.geojson")))
        if not inputs or not all(item.is_file() for item in inputs):
            return None
        scene_mtime = scene.stat().st_mtime_ns
        if any(item.stat().st_mtime_ns > scene_mtime for item in inputs):
            return None
        return document
    except (OSError, TypeError, ValueError, SelectionBridgeError):
        return None


def read_obj(path: str | os.PathLike[str]) -> ObjMesh:
    obj = Path(path)
    vertices: list[tuple[float, ...]] = []
    faces: list[tuple[int, int, int]] = []
    try:
        with obj.open("r", encoding="utf-8-sig", errors="strict") as stream:
            for line_number, raw in enumerate(stream, 1):
                stripped = raw.lstrip()
                if stripped.startswith("v "):
                    words = stripped.split("#", 1)[0].split()[1:]
                    if len(words) < 3:
                        raise ValueError("vertex needs three coordinates")
                    values = tuple(float(word) for word in words)
                    if not all(math.isfinite(value) for value in values):
                        raise ValueError("vertex values must be finite")
                    vertices.append(values)
                elif stripped.startswith("f "):
                    words = stripped.split("#", 1)[0].split()[1:]
                    if len(words) < 3:
                        raise ValueError("face needs three vertices")
                    indices = []
                    for word in words:
                        raw_index = int(word.split("/", 1)[0])
                        index = raw_index - 1 if raw_index > 0 else len(vertices) + raw_index
                        if not 0 <= index < len(vertices):
                            raise ValueError(f"face index {raw_index} is out of range")
                        indices.append(index)
                    for fan in range(1, len(indices) - 1):
                        faces.append((indices[0], indices[fan], indices[fan + 1]))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SelectionBridgeError(
            "mesh_obj_invalid", f"cannot parse OBJ {obj} at line {locals().get('line_number', 0)}: {exc}"
        ) from exc
    if not vertices or not faces:
        raise SelectionBridgeError("mesh_obj_empty", f"OBJ has no usable mesh: {obj}")
    return ObjMesh(vertices, faces)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise SelectionBridgeError("mesh_obj_empty", "cannot calculate ground from no vertices")
    ordered = sorted(values)
    rank = (float(percentile) / 100.0) * (len(ordered) - 1)
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return float(ordered[low])
    fraction = rank - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def rebase_and_ground_mesh(
    mesh: ObjMesh,
    job_origin_lat: float,
    job_origin_lon: float,
    workspace_frame: LocalFrame,
    *,
    ground_percentile: float = 2.0,
) -> tuple[ObjMesh, dict[str, float]]:
    """Convert job-origin X/Z into workspace-local X/Z and normalise ground Y."""

    ground_percentile = _finite("ground_percentile", ground_percentile)
    if not 0 <= ground_percentile <= 25:
        raise SelectionBridgeError("invalid_request", "ground_percentile must be in [0,25]")
    ground_y = _percentile([vertex[1] for vertex in mesh.vertices], ground_percentile)
    job_lon_scale = METERS_PER_DEGREE_LAT * math.cos(math.radians(job_origin_lat))
    workspace_lon_scale = workspace_frame.meters_per_degree_lon
    if abs(job_lon_scale) < 1e-9:
        raise SelectionBridgeError("mesh_origin_invalid", "job origin is too close to a pole")
    x_scale = workspace_lon_scale / job_lon_scale
    x_offset = (job_origin_lon - workspace_frame.origin_lon) * workspace_lon_scale
    z_offset = -(job_origin_lat - workspace_frame.origin_lat) * METERS_PER_DEGREE_LAT
    transformed: list[tuple[float, ...]] = []
    for vertex in mesh.vertices:
        transformed.append(
            (
                vertex[0] * x_scale + x_offset,
                vertex[1] - ground_y,
                vertex[2] + z_offset,
                *vertex[3:],
            )
        )
    return ObjMesh(transformed, mesh.faces), {
        "job_ground_reference_y": ground_y,
        "applied_y_offset": -ground_y,
        "x_scale_job_to_workspace": x_scale,
        "x_offset_m": x_offset,
        "z_offset_m": z_offset,
    }


def point_in_polygon(point, polygon: Sequence[tuple[float, float]], *, inclusive: bool = True) -> bool:
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if inclusive and _point_on_segment(point, a, b):
            return True
    x, z = point
    inside = False
    for (x1, z1), (x2, z2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (z1 > z) != (z2 > z):
            intersection = (x2 - x1) * (z - z1) / (z2 - z1) + x1
            if x < intersection:
                inside = not inside
    return inside


def sample_polygon_interior(
    polygon: Sequence[tuple[float, float]], spacing_m: float = 1.5
) -> list[tuple[float, float]]:
    spacing = _finite("sample spacing", spacing_m)
    if spacing <= 0:
        raise SelectionBridgeError("invalid_request", "sample spacing must be positive")
    xs, zs = [p[0] for p in polygon], [p[1] for p in polygon]
    min_x, max_x, min_z, max_z = min(xs), max(xs), min(zs), max(zs)
    samples: list[tuple[float, float]] = []
    x = min_x + spacing / 2.0
    while x < max_x:
        z = min_z + spacing / 2.0
        while z < max_z:
            if point_in_polygon((x, z), polygon):
                samples.append((x, z))
            z += spacing
        x += spacing
    if not samples:
        centroid = (sum(xs) / len(xs), sum(zs) / len(zs))
        if point_in_polygon(centroid, polygon):
            samples.append(centroid)
    return samples


def sample_bbox(
    bounds: tuple[float, float, float, float], spacing_m: float = 5.0
) -> list[tuple[float, float]]:
    min_x, min_z, max_x, max_z = bounds
    spacing = max(0.25, float(spacing_m))
    columns = max(2, math.ceil((max_x - min_x) / spacing) + 1)
    rows = max(2, math.ceil((max_z - min_z) / spacing) + 1)
    return [
        (
            min_x + (max_x - min_x) * x_index / (columns - 1),
            min_z + (max_z - min_z) * z_index / (rows - 1),
        )
        for x_index in range(columns)
        for z_index in range(rows)
    ]


def tile_coverage_ratio(
    local_samples: Sequence[tuple[float, float]],
    frame: LocalFrame,
    tiles: Sequence[PlannedTile],
    available_stems: Iterable[str] | None = None,
) -> float:
    available = set(available_stems) if available_stems is not None else {tile.stem for tile in tiles}
    # Completeness is gated by the geometry that survives top-level
    # crop_boundary(), not by the larger downloaded raster viewport.
    rectangles = [
        tile.effective_mesh_bounds_mercator_m
        for tile in tiles
        if tile.stem in available
    ]
    if not local_samples or not rectangles:
        return 0.0
    covered = 0
    for x, z in local_samples:
        lon, lat = frame.to_wgs84(x, z)
        mx, my = lonlat_to_web_mercator(lon, lat)
        if any(left <= mx <= right and bottom <= my <= top for left, bottom, right, top in rectangles):
            covered += 1
    return covered / len(local_samples)


def _point_in_triangle(point, a, b, c, epsilon: float = 1e-8) -> bool:
    o1 = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
    o2 = (c[0] - b[0]) * (point[1] - b[1]) - (c[1] - b[1]) * (point[0] - b[0])
    o3 = (a[0] - c[0]) * (point[1] - c[1]) - (a[1] - c[1]) * (point[0] - c[0])
    has_negative = o1 < -epsilon or o2 < -epsilon or o3 < -epsilon
    has_positive = o1 > epsilon or o2 > epsilon or o3 > epsilon
    return not (has_negative and has_positive)


def _bbox_intersects(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _triangle_bbox(points) -> tuple[float, float, float, float]:
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _triangle_polygon_intersects(triangle, polygon) -> bool:
    if any(point_in_polygon(point, polygon) for point in triangle):
        return True
    if any(_point_in_triangle(point, *triangle) for point in polygon):
        return True
    tri_edges = list(zip(triangle, triangle[1:] + triangle[:1]))
    polygon_edges = list(zip(polygon, polygon[1:] + polygon[:1]))
    return any(_segments_intersect(a, b, c, d) for a, b in tri_edges for c, d in polygon_edges)


def _point_strictly_in_polygon(point, polygon) -> bool:
    """Return true only for polygon interior, never for its boundary."""

    if any(_point_on_segment(point, a, b) for a, b in zip(polygon, polygon[1:] + polygon[:1])):
        return False
    return point_in_polygon(point, polygon, inclusive=False)


def _point_strictly_in_triangle(point, a, b, c, epsilon: float = 1e-8) -> bool:
    orientations = (
        (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]),
        (c[0] - b[0]) * (point[1] - b[1]) - (c[1] - b[1]) * (point[0] - b[0]),
        (a[0] - c[0]) * (point[1] - c[1]) - (a[1] - c[1]) * (point[0] - c[0]),
    )
    return all(value > epsilon for value in orientations) or all(
        value < -epsilon for value in orientations
    )


def _segments_cross_properly(a, b, c, d, epsilon: float = 1e-8) -> bool:
    """Exclude endpoint/collinear contact, which has no projected area."""

    def cross(first, second, third):
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    ab_c, ab_d = cross(a, b, c), cross(a, b, d)
    cd_a, cd_b = cross(c, d, a), cross(c, d, b)
    return ab_c * ab_d < -(epsilon * epsilon) and cd_a * cd_b < -(epsilon * epsilon)


def _point_segment_distance(point, a, b) -> float:
    dx, dz = b[0] - a[0], b[1] - a[1]
    length_squared = dx * dx + dz * dz
    if length_squared <= 1e-16:
        return math.dist(point, a)
    fraction = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / length_squared))
    return math.dist(point, (a[0] + fraction * dx, a[1] + fraction * dz))


def _triangle_near_polygon(triangle, polygon, padding: float) -> bool:
    if _triangle_polygon_intersects(triangle, polygon):
        return True
    if padding <= 0:
        return False
    tri_edges = list(zip(triangle, triangle[1:] + triangle[:1]))
    polygon_edges = list(zip(polygon, polygon[1:] + polygon[:1]))
    return (
        min(_point_segment_distance(point, a, b) for point in triangle for a, b in polygon_edges) <= padding
        or min(_point_segment_distance(point, a, b) for point in polygon for a, b in tri_edges) <= padding
    )


def _local_ground(mesh: ObjMesh, footprint: SelectedFootprint, fallback: float = 0.0) -> float:
    """Estimate ground outside a building without accepting a neighbour roof.

    Selection meshes are rebased so the fallback ground is normally Y=0.  An
    outside-ring percentile more than three metres from that baseline is more
    plausibly another building surface than local terrain, so it is rejected.
    """

    min_x, min_z, max_x, max_z = footprint.bounds
    nearby = [
        vertex[1]
        for vertex in mesh.vertices
        if min_x - 10 <= vertex[0] <= max_x + 10 and min_z - 10 <= vertex[2] <= max_z + 10
        and not point_in_polygon((vertex[0], vertex[2]), footprint.points)
    ]
    if nearby:
        estimate = _percentile(nearby, 10.0)
        if abs(estimate - fallback) <= 3.0:
            return estimate
    # A rebased selection normally has ground close to zero.  If its footprint
    # fills the whole crop, or the ring contains only a neighbouring roof,
    # prefer that known baseline over treating roof vertices as ground.
    return fallback


def assess_footprint_completeness(
    mesh: ObjMesh,
    footprint: SelectedFootprint,
    *,
    sample_spacing_m: float = 1.5,
    minimum_above_ground_m: float = 2.5,
    minimum_projected_coverage: float = 0.85,
) -> dict[str, Any]:
    samples = sample_polygon_interior(footprint.points, sample_spacing_m)
    local_ground = _local_ground(mesh, footprint)
    threshold_y = local_ground + minimum_above_ground_m
    footprint_bbox = footprint.bounds
    triangles: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    for face in mesh.faces:
        vertices = [mesh.vertices[index] for index in face]
        if min(vertex[1] for vertex in vertices) < threshold_y:
            continue
        projected = tuple((vertex[0], vertex[2]) for vertex in vertices)
        if abs(_signed_area(projected)) <= 1e-4:
            continue
        if not _bbox_intersects(_triangle_bbox(projected), footprint_bbox):
            continue
        triangles.append(projected)  # type: ignore[arg-type]
    covered = sum(any(_point_in_triangle(sample, *triangle) for triangle in triangles) for sample in samples)
    ratio = covered / len(samples) if samples else 0.0
    return {
        "local_ground_y": local_ground,
        "above_ground_threshold_y": threshold_y,
        "sample_count": len(samples),
        "covered_sample_count": covered,
        "projected_coverage_ratio": ratio,
        "minimum_projected_coverage": minimum_projected_coverage,
        "qualifying_triangle_count": len(triangles),
        "complete": bool(samples) and ratio >= minimum_projected_coverage,
    }


def crop_face_indices(
    mesh: ObjMesh,
    footprint: SelectedFootprint,
    *,
    crop_padding_m: float = 1.0,
    minimum_surface_height_m: float = 0.5,
) -> set[int]:
    local_ground = _local_ground(mesh, footprint)
    min_x, min_z, max_x, max_z = footprint.bounds
    expanded = (
        min_x - crop_padding_m,
        min_z - crop_padding_m,
        max_x + crop_padding_m,
        max_z + crop_padding_m,
    )
    selected: set[int] = set()
    for index, face in enumerate(mesh.faces):
        vertices = [mesh.vertices[item] for item in face]
        if max(vertex[1] for vertex in vertices) < local_ground + minimum_surface_height_m:
            continue
        projected = tuple((vertex[0], vertex[2]) for vertex in vertices)
        if not _bbox_intersects(_triangle_bbox(projected), expanded):
            continue
        if _triangle_near_polygon(projected, footprint.points, crop_padding_m):
            selected.add(index)
    return selected


def crop_face_indices_bbox(
    mesh: ObjMesh,
    footprint: SelectedFootprint,
    *,
    crop_padding_m: float = 1.0,
) -> set[int]:
    """Fast coarse crop used when strict model completeness is disabled.

    This deliberately performs one linear face scan and only applies an X/Z
    bounding-box test. It does not claim that the generated building geometry
    is complete; tile, mesh and mandatory DSM gates are enforced separately.
    """

    min_x, min_z, max_x, max_z = footprint.bounds
    expanded = (
        min_x - crop_padding_m,
        min_z - crop_padding_m,
        max_x + crop_padding_m,
        max_z + crop_padding_m,
    )
    selected: set[int] = set()
    for index, face in enumerate(mesh.faces):
        projected = tuple((mesh.vertices[item][0], mesh.vertices[item][2]) for item in face)
        if _bbox_intersects(_triangle_bbox(projected), expanded):
            selected.add(index)
    return selected


def write_obj_subset(mesh: ObjMesh, face_indices: Iterable[int], path: Path) -> dict[str, int]:
    selected_faces = [mesh.faces[index] for index in sorted(set(face_indices))]
    used = sorted({vertex for face in selected_faces for vertex in face})
    if not used or not selected_faces:
        raise SelectionBridgeError("cropped_mesh_empty", "no mesh faces intersect the complete footprints")
    remap = {old: new + 1 for new, old in enumerate(used)}
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# myProject selection mesh: X east, Y up, Z south; workspace-local metres"]
    for index in used:
        values = mesh.vertices[index]
        lines.append("v " + " ".join(format(value, ".9g") for value in values))
    for face in selected_faces:
        lines.append("f " + " ".join(str(remap[index]) for index in face))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"vertex_count": len(used), "face_count": len(selected_faces)}


def _compact_mesh(mesh: ObjMesh, face_indices: Iterable[int]) -> ObjMesh:
    """Return a vertex-compact mesh without dropping per-vertex attributes."""

    selected_faces = [mesh.faces[index] for index in sorted(set(face_indices))]
    used = sorted({vertex for face in selected_faces for vertex in face})
    remap = {old: new for new, old in enumerate(used)}
    return ObjMesh(
        [mesh.vertices[index] for index in used],
        [tuple(remap[index] for index in face) for face in selected_faces],
    )


def write_obj_mesh(mesh: ObjMesh, path: Path) -> dict[str, int]:
    """Write a complete, already isolated mesh using the legacy OBJ contract."""

    return write_obj_subset(mesh, range(len(mesh.faces)), path)


def write_gis_obj(footprints: Sequence[SelectedFootprint], path: Path) -> dict[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# myProject selection GIS: X east, Y=0, Z south; workspace-local metres"]
    next_index = 1
    vertex_count = 0
    for footprint in footprints:
        lines.append(f"o {footprint.identifier}")
        face = []
        for x, z in footprint.points:
            lines.append(f"v {x:.9g} 0 {z:.9g}")
            face.append(str(next_index))
            next_index += 1
            vertex_count += 1
        lines.append("f " + " ".join(face))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {"footprint_count": len(footprints), "vertex_count": vertex_count}


def assign_building_faces(
    mesh: ObjMesh,
    footprints: Sequence[SelectedFootprint],
    *,
    minimum_surface_height_m: float = 0.5,
    spatial_cell_m: float = 1.0,
) -> tuple[dict[str, set[int]], dict[str, float]]:
    """Assign every scene triangle to at most one requested footprint.

    The top-level Sat3DGen extractor is deliberately not used here: its
    historical ``any(vertex)`` rule can pull neighbouring geometry into a
    building and its low-sample fallback can return the complete scene.  A
    A triangle is owned only when it has positive-area projected overlap with a
    footprint.  This retains legitimate faces crossing a footprint boundary
    without reviving the historical ``any(vertex)`` neighbour leakage.  If
    footprints overlap, geometric evidence wins, followed by the smallest
    footprint and stable identifier, making ownership deterministic and
    duplicate-free.
    """

    minimum_height = _finite("minimum building surface height", minimum_surface_height_m)
    if minimum_height < 0:
        raise SelectionBridgeError(
            "invalid_request", "minimum building surface height must be non-negative"
        )
    cell_size = _finite("building assignment spatial cell", spatial_cell_m)
    if cell_size <= 0:
        raise SelectionBridgeError(
            "invalid_request", "building assignment spatial cell must be positive"
        )
    ordered = sorted(footprints, key=lambda item: (item.area_m2, item.identifier))
    assignments = {footprint.identifier: set() for footprint in ordered}
    grounds = {footprint.identifier: _local_ground(mesh, footprint) for footprint in ordered}
    polygon_edges: dict[str, tuple[tuple[tuple[float, float], tuple[float, float]], ...]] = {}
    for footprint in ordered:
        polygon_edges[footprint.identifier] = tuple(
            zip(footprint.points, footprint.points[1:] + footprint.points[:1])
        )
    for face_index, face in enumerate(mesh.faces):
        vertices = [mesh.vertices[index] for index in face]
        projected = tuple((vertex[0], vertex[2]) for vertex in vertices)
        if abs(_signed_area(projected)) <= 1e-8:
            continue
        triangle_bounds = _triangle_bbox(projected)
        centre = (
            sum(vertex[0] for vertex in vertices) / 3.0,
            sum(vertex[2] for vertex in vertices) / 3.0,
        )
        candidates: list[tuple[tuple[int, int, int, int], float, str, SelectedFootprint]] = []
        for footprint in ordered:
            if not _bbox_intersects(triangle_bounds, footprint.bounds):
                continue
            centre_inside = point_in_polygon(centre, footprint.points)
            vertices_inside = sum(
                _point_strictly_in_polygon(point, footprint.points) for point in projected
            )
            footprint_vertices_inside = sum(
                _point_strictly_in_triangle(point, *projected) for point in footprint.points
            )
            proper_crossings = sum(
                _segments_cross_properly(a, b, c, d)
                for a, b in zip(projected, projected[1:] + projected[:1])
                for c, d in polygon_edges[footprint.identifier]
            )
            if not (centre_inside or vertices_inside or footprint_vertices_inside or proper_crossings):
                continue
            score = (
                int(centre_inside),
                vertices_inside,
                footprint_vertices_inside,
                proper_crossings,
            )
            candidates.append((score, footprint.area_m2, footprint.identifier, footprint))
        if not candidates:
            continue
        best_score = max(item[0] for item in candidates)
        owner = min(
            (item for item in candidates if item[0] == best_score),
            key=lambda item: (item[1], item[2]),
        )[3]
        ground = grounds[owner.identifier]
        if max(vertex[1] for vertex in vertices) >= ground + minimum_height:
            assignments[owner.identifier].add(face_index)
    return assignments, grounds


def deduplicate_face_indices(
    mesh: ObjMesh, face_indices: Iterable[int]
) -> tuple[set[int], int]:
    """Drop same-winding geometric duplicates, preserving opposite windings.

    Sat3DGen can emit coincident vertices with different indices, so the key is
    based on quantised XYZ rather than vertex indices.  Cyclic rotations are
    equivalent; reversing a triangle is deliberately *not* equivalent because
    opposite windings can be the two intentional sides of a thin surface.
    """

    retained: set[int] = set()
    seen: set[tuple[tuple[int, int, int], ...]] = set()
    duplicate_count = 0
    for face_index in sorted(set(face_indices)):
        face = mesh.faces[face_index]
        coordinates = tuple(
            tuple(int(round(mesh.vertices[index][axis] * 100_000_000.0)) for axis in range(3))
            for index in face
        )
        canonical = min(
            coordinates,
            coordinates[1:] + coordinates[:1],
            coordinates[2:] + coordinates[:2],
        )
        if canonical in seen:
            duplicate_count += 1
            continue
        seen.add(canonical)
        retained.add(face_index)
    return retained, duplicate_count


def clean_face_indices(
    mesh: ObjMesh,
    face_indices: Iterable[int],
    *,
    twice_area_epsilon_m2: float = 1e-10,
) -> tuple[set[int], dict[str, int]]:
    """Remove only provably degenerate and same-winding duplicate triangles."""

    candidates: set[int] = set()
    degenerate = 0
    threshold_squared = float(twice_area_epsilon_m2) ** 2
    for face_index in sorted(set(face_indices)):
        face = mesh.faces[face_index]
        a, b, c = (mesh.vertices[index] for index in face)
        ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        if sum(value * value for value in cross) <= threshold_squared:
            degenerate += 1
        else:
            candidates.add(face_index)
    retained, duplicates = deduplicate_face_indices(mesh, candidates)
    return retained, {
        "degenerate_face_count_removed": degenerate,
        "same_winding_duplicate_face_count_removed": duplicates,
    }


def clip_mesh_to_ground(
    mesh: ObjMesh,
    face_indices: Iterable[int],
    ground_height_m: float,
    *,
    epsilon_m: float = 1e-8,
) -> tuple[ObjMesh, dict[str, int | float]]:
    """Clip selected triangles exactly at Y=ground and interpolate attributes."""

    ground = _finite("building ground height", ground_height_m)
    selected = sorted(set(face_indices))
    output_vertices: list[tuple[float, ...]] = []
    output_faces: list[tuple[int, int, int]] = []
    source_vertex_cache: dict[int, int] = {}
    edge_cache: dict[tuple[int, int], int] = {}
    intersected_faces = 0
    discarded_faces = 0

    def source_vertex(index: int) -> int:
        if index not in source_vertex_cache:
            source_vertex_cache[index] = len(output_vertices)
            output_vertices.append(mesh.vertices[index])
        return source_vertex_cache[index]

    def intersection(first: int, second: int) -> int:
        key = (min(first, second), max(first, second))
        if key in edge_cache:
            return edge_cache[key]
        a, b = mesh.vertices[first], mesh.vertices[second]
        denominator = b[1] - a[1]
        fraction = 0.0 if abs(denominator) <= epsilon_m else (ground - a[1]) / denominator
        fraction = max(0.0, min(1.0, fraction))
        width = max(len(a), len(b))
        values: list[float] = []
        for axis in range(width):
            av = a[axis] if axis < len(a) else b[axis]
            bv = b[axis] if axis < len(b) else a[axis]
            values.append(av + fraction * (bv - av))
        values[1] = ground
        edge_cache[key] = len(output_vertices)
        output_vertices.append(tuple(values))
        return edge_cache[key]

    for face_index in selected:
        face = mesh.faces[face_index]
        inside = [mesh.vertices[index][1] >= ground - epsilon_m for index in face]
        if not any(inside):
            discarded_faces += 1
            continue
        if not all(inside):
            intersected_faces += 1
        polygon: list[int] = []
        for position, current in enumerate(face):
            previous = face[position - 1]
            previous_inside = inside[position - 1]
            current_inside = inside[position]
            if current_inside:
                if not previous_inside:
                    polygon.append(intersection(previous, current))
                polygon.append(source_vertex(current))
            elif previous_inside:
                polygon.append(intersection(previous, current))
        for offset in range(1, len(polygon) - 1):
            output_faces.append((polygon[0], polygon[offset], polygon[offset + 1]))

    clipped = ObjMesh(output_vertices, output_faces)
    clean, cleanup = clean_face_indices(clipped, range(len(clipped.faces)))
    clipped = _compact_mesh(clipped, clean)
    return clipped, {
        "ground_clip_height_m": ground,
        "ground_clip_input_face_count": len(selected),
        "ground_clip_intersected_face_count": intersected_faces,
        "ground_clip_discarded_face_count": discarded_faces,
        "ground_clip_output_face_count": len(clipped.faces),
        "ground_clip_degenerate_face_count_removed": cleanup[
            "degenerate_face_count_removed"
        ],
        "ground_clip_same_winding_duplicate_face_count_removed": cleanup[
            "same_winding_duplicate_face_count_removed"
        ],
    }


def _simple_projected_loop(mesh: ObjMesh, loop: Sequence[int]) -> bool:
    edges = list(zip(loop, loop[1:] + loop[:1]))
    for first_index, (a0, a1) in enumerate(edges):
        a = (mesh.vertices[a0][0], mesh.vertices[a0][2])
        b = (mesh.vertices[a1][0], mesh.vertices[a1][2])
        for second_index in range(first_index + 1, len(edges)):
            if second_index in {first_index, first_index + 1} or (
                first_index == 0 and second_index == len(edges) - 1
            ):
                continue
            b0, b1 = edges[second_index]
            c = (mesh.vertices[b0][0], mesh.vertices[b0][2])
            d = (mesh.vertices[b1][0], mesh.vertices[b1][2])
            if _segments_intersect(a, b, c, d):
                return False
    return True


def _triangulate_ground_loop(mesh: ObjMesh, loop: Sequence[int]) -> list[tuple[int, int, int]]:
    """Ear-clip one simple X/Z loop, oriented with its normal downwards."""

    ordered = list(loop)
    points = [(mesh.vertices[index][0], mesh.vertices[index][2]) for index in ordered]
    if abs(_signed_area(points)) <= 1e-8:
        return []
    if _signed_area(points) < 0:
        ordered.reverse()
        points.reverse()
    remaining = list(range(len(ordered)))
    triangles: list[tuple[int, int, int]] = []
    guard = len(remaining) * len(remaining)
    while len(remaining) > 3 and guard > 0:
        guard -= 1
        clipped = False
        for at in range(len(remaining)):
            before = remaining[at - 1]
            current = remaining[at]
            after = remaining[(at + 1) % len(remaining)]
            a, b, c = points[before], points[current], points[after]
            turn = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if turn <= 1e-10:
                continue
            if any(
                _point_in_triangle(points[other], a, b, c, epsilon=1e-10)
                for other in remaining
                if other not in {before, current, after}
            ):
                continue
            triangles.append((ordered[before], ordered[current], ordered[after]))
            remaining.pop(at)
            clipped = True
            break
        if not clipped:
            return []
    if len(remaining) == 3:
        triangles.append(tuple(ordered[index] for index in remaining))
    return triangles


def close_ground_boundary_loops(
    mesh: ObjMesh,
    ground_height_m: float,
    *,
    epsilon_m: float = 1e-6,
    maximum_loop_edges: int = 2048,
) -> tuple[ObjMesh, dict[str, int]]:
    """Close only simple boundary loops lying exactly on the clipping plane."""

    edge_faces: dict[tuple[int, int], list[int]] = {}
    for face_index, face in enumerate(mesh.faces):
        for first, second in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge_faces.setdefault((min(first, second), max(first, second)), []).append(face_index)
    ground_edges = [
        edge
        for edge, owners in edge_faces.items()
        if len(owners) == 1
        and abs(mesh.vertices[edge[0]][1] - ground_height_m) <= epsilon_m
        and abs(mesh.vertices[edge[1]][1] - ground_height_m) <= epsilon_m
    ]
    adjacency: dict[int, list[int]] = {}
    for first, second in ground_edges:
        adjacency.setdefault(first, []).append(second)
        adjacency.setdefault(second, []).append(first)
    unused = set(ground_edges)
    loops: list[list[int]] = []
    rejected = 0
    while unused:
        seed = min(unused)
        component_vertices = {seed[0], seed[1]}
        stack = list(component_vertices)
        while stack:
            vertex = stack.pop()
            for neighbour in adjacency.get(vertex, ()):
                edge = (min(vertex, neighbour), max(vertex, neighbour))
                unused.discard(edge)
                if neighbour not in component_vertices:
                    component_vertices.add(neighbour)
                    stack.append(neighbour)
        if (
            len(component_vertices) < 3
            or len(component_vertices) > maximum_loop_edges
            or any(len(adjacency.get(vertex, ())) != 2 for vertex in component_vertices)
        ):
            rejected += 1
            continue
        start = min(component_vertices)
        loop = [start]
        previous: int | None = None
        current = start
        while True:
            candidates = sorted(adjacency[current])
            following = candidates[0] if candidates[0] != previous else candidates[1]
            if following == start:
                break
            if following in loop:
                loop = []
                break
            loop.append(following)
            previous, current = current, following
        if len(loop) != len(component_vertices) or not _simple_projected_loop(mesh, loop):
            rejected += 1
            continue
        loops.append(loop)

    candidate_count = len(loops) + rejected
    faces = list(mesh.faces)
    capped = 0
    added = 0
    nested: set[int] = set()
    for first in range(len(loops)):
        first_point = (
            mesh.vertices[loops[first][0]][0],
            mesh.vertices[loops[first][0]][2],
        )
        for second in range(first + 1, len(loops)):
            second_point = (
                mesh.vertices[loops[second][0]][0],
                mesh.vertices[loops[second][0]][2],
            )
            first_polygon = [
                (mesh.vertices[index][0], mesh.vertices[index][2]) for index in loops[first]
            ]
            second_polygon = [
                (mesh.vertices[index][0], mesh.vertices[index][2]) for index in loops[second]
            ]
            if point_in_polygon(first_point, second_polygon) or point_in_polygon(
                second_point, first_polygon
            ):
                nested.update((first, second))
    rejected += len(nested)
    for loop_index, loop in enumerate(loops):
        if loop_index in nested:
            continue
        triangles = _triangulate_ground_loop(mesh, loop)
        if not triangles:
            rejected += 1
            continue
        faces.extend(triangles)
        capped += 1
        added += len(triangles)
    return ObjMesh(list(mesh.vertices), faces), {
        "ground_boundary_edge_count": len(ground_edges),
        "ground_loop_candidate_count": candidate_count,
        "ground_loop_capped_count": capped,
        "ground_loop_rejected_count": rejected,
        "ground_cap_face_count_added": added,
    }


def prepare_building_mesh(
    mesh: ObjMesh,
    face_indices: Iterable[int],
    *,
    ground_height_m: float,
) -> tuple[ObjMesh, dict[str, int | float]]:
    """Create a per-building v2 mesh without scene fallback or face leakage."""

    cleaned_indices, before = clean_face_indices(mesh, face_indices)
    clipped, clipping = clip_mesh_to_ground(mesh, cleaned_indices, ground_height_m)
    capped, capping = close_ground_boundary_loops(clipped, ground_height_m)
    final_indices, after = clean_face_indices(capped, range(len(capped.faces)))
    final_mesh = _compact_mesh(capped, final_indices)
    return final_mesh, {
        "preclip_degenerate_face_count_removed": before[
            "degenerate_face_count_removed"
        ],
        "preclip_same_winding_duplicate_face_count_removed": before[
            "same_winding_duplicate_face_count_removed"
        ],
        **clipping,
        **capping,
        "post_cap_degenerate_face_count_removed": after["degenerate_face_count_removed"],
        "post_cap_same_winding_duplicate_face_count_removed": after[
            "same_winding_duplicate_face_count_removed"
        ],
    }


def _face_components(mesh: ObjMesh, face_indices: Iterable[int]) -> list[set[int]]:
    """Return deterministic face components connected by a shared edge."""

    selected = set(face_indices)
    if not selected:
        return []
    edge_faces: dict[tuple[int, int], list[int]] = {}
    for face_index in selected:
        face = mesh.faces[face_index]
        for first, second in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge_faces.setdefault((min(first, second), max(first, second)), []).append(face_index)
    unvisited = set(selected)
    components: list[set[int]] = []
    while unvisited:
        start = min(unvisited)
        unvisited.remove(start)
        component = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            face = mesh.faces[current]
            for first, second in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
                for neighbour in edge_faces.get((min(first, second), max(first, second)), ()):
                    if neighbour in unvisited:
                        unvisited.remove(neighbour)
                        component.add(neighbour)
                        stack.append(neighbour)
        components.append(component)
    components.sort(key=lambda item: (-len(item), min(item)))
    return components


def filter_building_components(
    mesh: ObjMesh,
    face_indices: Iterable[int],
    *,
    minimum_component_faces: int = 1,
    minimum_component_ratio: float = 0.0,
) -> tuple[set[int], dict[str, int | float]]:
    """Retain edge-connected components; v2 defaults preserve every component."""

    if minimum_component_faces < 1:
        raise SelectionBridgeError(
            "invalid_request", "minimum_component_faces must be at least one"
        )
    ratio = _finite("minimum_component_ratio", minimum_component_ratio)
    if not 0.0 <= ratio <= 1.0:
        raise SelectionBridgeError(
            "invalid_request", "minimum_component_ratio must be in [0,1]"
        )
    components = _face_components(mesh, face_indices)
    if not components:
        return set(), {
            "raw_component_count": 0,
            "retained_component_count": 0,
            "dropped_component_count": 0,
            "dropped_face_count": 0,
            "largest_component_face_count": 0,
            "minimum_component_faces": minimum_component_faces,
            "minimum_component_ratio": ratio,
        }
    largest = len(components[0])
    retained = [components[0]]
    dropped: list[set[int]] = []
    for component in components[1:]:
        if len(component) >= minimum_component_faces and len(component) / largest >= ratio:
            retained.append(component)
        else:
            dropped.append(component)
    return set().union(*retained), {
        "raw_component_count": len(components),
        "retained_component_count": len(retained),
        "dropped_component_count": len(dropped),
        "dropped_face_count": sum(len(item) for item in dropped),
        "largest_component_face_count": largest,
        "minimum_component_faces": minimum_component_faces,
        "minimum_component_ratio": ratio,
    }


def building_subset_metrics(
    mesh: ObjMesh, face_indices: Iterable[int], *, ground_height_m: float
) -> dict[str, Any]:
    selected = [mesh.faces[index] for index in sorted(set(face_indices))]
    used = sorted({vertex for face in selected for vertex in face})
    if not selected or not used:
        return {
            "vertex_count": 0,
            "face_count": 0,
            "ground_height_m": ground_height_m,
            "maximum_height_m": None,
            "relief_m": 0.0,
            "boundary_edge_count": 0,
            "nonmanifold_edge_count": 0,
            "watertight": False,
        }
    maximum_y = max(mesh.vertices[index][1] for index in used)
    edges: dict[tuple[int, int], int] = {}
    for face in selected:
        for first, second in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = (min(first, second), max(first, second))
            edges[edge] = edges.get(edge, 0) + 1
    boundary_edges = sum(count == 1 for count in edges.values())
    nonmanifold_edges = sum(count > 2 for count in edges.values())
    return {
        "vertex_count": len(used),
        "face_count": len(selected),
        "ground_height_m": ground_height_m,
        "maximum_height_m": maximum_y,
        "relief_m": max(0.0, maximum_y - ground_height_m),
        "boundary_edge_count": boundary_edges,
        "nonmanifold_edge_count": nonmanifold_edges,
        "watertight": bool(edges) and boundary_edges == 0 and nonmanifold_edges == 0,
    }


def stage_building_publication(
    staging: Path,
    mesh: ObjMesh,
    prepared_buildings: Sequence[
        tuple[SelectedFootprint, set[int] | ObjMesh, dict[str, Any]]
    ],
    building_entries: Sequence[dict[str, Any]],
    *,
    selection_id: str,
    stable_id: str,
    pipeline_contract_version: str = PIPELINE_CONTRACT_VERSION,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Write only publishable buildings and finish with their index marker."""

    if not prepared_buildings:
        raise SelectionBridgeError(
            "no_publishable_buildings",
            "no selected footprint passed the per-building geometry gates",
            details={"buildings": list(building_entries)},
        )
    for footprint, building_geometry, entry in prepared_buildings:
        building_dir = staging / "buildings" / footprint.identifier
        building_mesh_stats = (
            write_obj_mesh(building_geometry, building_dir / "cropped.obj")
            if isinstance(building_geometry, ObjMesh)
            else write_obj_subset(mesh, building_geometry, building_dir / "cropped.obj")
        )
        building_gis_stats = write_gis_obj((footprint,), building_dir / "gis.obj")
        shutil.copy2(building_dir / "gis.obj", building_dir / "gis_footprints.obj")
        entry["mesh"] = building_mesh_stats
        entry["gis"] = building_gis_stats
        _atomic_json(building_dir / "building.json", entry)
    summary = {
        "requested": len(building_entries),
        "ready": len(prepared_buildings),
        "coarse_ready": sum(item["status"] == "COARSE_READY" for item in building_entries),
        "rejected": sum(item["status"] == "REJECTED" for item in building_entries),
        "empty": sum(item["status"] == "EMPTY" for item in building_entries),
        "failed": 0,
    }
    index = {
        "schema_version": 1,
        "kind": "myProject.selection.buildings",
        "building_publication_version": BUILDING_PUBLICATION_VERSION,
        "pipeline_contract_version": pipeline_contract_version,
        "selection_id": selection_id,
        "stable_id": stable_id,
        "status": "READY",
        "coordinate_frame": "workspace-local X east, Y up, Z south; metres",
        "diagnostic_scene": "../cropped.obj",
        "summary": summary,
        "buildings": list(building_entries),
    }
    # index.json is the building-set commit marker.  The root result.json is
    # still installed last by _commit_ready().
    _atomic_json(staging / "buildings" / "index.json", index)
    return summary, index


def _attach_roof_reference_metadata(
    staging: Path,
    building_entries: Sequence[dict[str, Any]],
    building_index: dict[str, Any],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    """Register additive appearance outputs without changing geometry status."""

    compact = {
        "status": str(report.get("status", "UNAVAILABLE")),
        "source": "cached_satellite_tiles",
        "source_tile_manifest": report.get("source_tile_manifest"),
        "requested": int(report.get("requested", 0)),
        "ready": int(report.get("ready", 0)),
        "unavailable": int(report.get("unavailable", 0)),
        "geometry_modified": False,
        "fallback": "preserve_existing_roof_geometry_and_texture_material_pipeline",
    }
    raw_buildings = report.get("buildings", {})
    building_reports = raw_buildings if isinstance(raw_buildings, Mapping) else {}
    output_keys = {
        "satellite_north_up": "roof_satellite_north_up",
        "source_valid_mask": "roof_source_valid_mask",
        "footprint_mask": "roof_footprint_mask",
        "roof_style_mask": "roof_style_mask",
        "roof_reference": "roof_reference",
        "roof_reference_rgba": "roof_reference_rgba",
    }
    for entry in building_entries:
        identifier = str(entry.get("id", ""))
        published = entry.get("publishable") is True
        if not published:
            entry.setdefault("appearance", {})["roof"] = {
                "status": "NOT_APPLICABLE",
                "reason": "building_not_publishable",
                "geometry_modified": False,
            }
            continue
        raw_reference = building_reports.get(identifier, {})
        reference = raw_reference if isinstance(raw_reference, Mapping) else {}
        status = str(reference.get("status", "UNAVAILABLE"))
        relative_dir = str(entry.get("relative_dir", f"buildings/{identifier}"))
        manifest_relative = f"{relative_dir}/references/roof/reference.json"
        manifest_exists = (
            staging / "buildings" / identifier / "references" / "roof" / "reference.json"
        ).is_file()
        outputs = entry.get("outputs")
        if not isinstance(outputs, dict):
            outputs = {}
            entry["outputs"] = outputs
        if manifest_exists:
            outputs["roof_reference_manifest"] = manifest_relative
        registered: dict[str, str] = {}
        raw_outputs = reference.get("outputs", {})
        if status == "READY" and isinstance(raw_outputs, Mapping):
            for source_key, entry_key in output_keys.items():
                filename = raw_outputs.get(source_key)
                if isinstance(filename, str) and Path(filename).name == filename:
                    relative = f"{relative_dir}/references/roof/{filename}"
                    outputs[entry_key] = relative
                    registered[source_key] = relative
        error = reference.get("error")
        roof_appearance: dict[str, Any] = {
            "status": status,
            "source": "cached_satellite_tiles",
            "north_up": status == "READY",
            "reference_manifest": manifest_relative if manifest_exists else None,
            "style_reference": registered.get("roof_reference"),
            "style_mask": registered.get("roof_style_mask"),
            "quality": reference.get("quality"),
            "cache_hit": bool(reference.get("cache_hit", False)),
            "fallback": "preserve_existing_roof_geometry_and_texture_material_pipeline",
            "geometry_modified": False,
        }
        if isinstance(error, Mapping):
            roof_appearance["error"] = dict(error)
        entry.setdefault("appearance", {})["roof"] = roof_appearance
        _atomic_json(staging / "buildings" / identifier / "building.json", entry)
    building_index["appearance"] = {"roof_references": compact}
    _atomic_json(staging / "buildings" / "index.json", building_index)
    return compact


def _commit_ready(request: SelectionRequest, staging: Path) -> None:
    """Replace a READY publication at commit time, with rollback on failure."""

    request.output_dir.mkdir(parents=True, exist_ok=True)
    backup = request.job_dir / "previous" / time.strftime("%Y%m%d-%H%M%S")
    moved: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        for name in (
            "cropped.obj",
            "gis.obj",
            "gis_footprints.obj",
            "buildings",
            "result.json",
            "minimesh",
        ):
            current = request.output_dir / name
            if not current.exists():
                continue
            backup.mkdir(parents=True, exist_ok=True)
            destination = backup / name
            if destination.exists():
                destination = backup / f"{uuid.uuid4().hex[:8]}-{name}"
            os.replace(current, destination)
            moved.append((current, destination))
        for name in ("cropped.obj", "gis.obj", "gis_footprints.obj", "buildings"):
            destination = request.output_dir / name
            os.replace(staging / name, destination)
            installed.append(destination)
        # READY is the commit marker and is deliberately installed last.
        destination = request.output_dir / "result.json"
        os.replace(staging / "result.json", destination)
        installed.append(destination)
    except BaseException:
        failed_install = request.job_dir / f"failed-commit-{uuid.uuid4().hex[:8]}"
        failed_install.mkdir(parents=True, exist_ok=True)
        for current in reversed(installed):
            if current.exists():
                os.replace(current, failed_install / current.name)
        for current, saved in reversed(moved):
            if saved.exists():
                os.replace(saved, current)
        raise


def _tile_manifest_document(
    request: SelectionRequest, tiles: Sequence[PlannedTile], plan: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "myProject.selection.exact_tiles",
        "pipeline_contract_version": PIPELINE_CONTRACT_VERSION,
        "building_publication_version": BUILDING_PUBLICATION_VERSION,
        "osm_prealign": True,
        "merge_stage_order": list(MERGE_STAGE_ORDER),
        "status": "PLANNED",
        "selection_id": request.selection_id,
        "stable_id": request.stable_id,
        "workspace": str(request.workspace),
        "frame": {
            "origin_lat": request.frame.origin_lat,
            "origin_lon": request.frame.origin_lon,
            "axes": {"x": "east", "y": "up", "z": "south"},
            "units": "m",
        },
        "plan": dict(plan),
        "tile_count": len(tiles),
        "tiles": [tile.to_dict() for tile in tiles],
        "satellite_validation": [],
        "download_failures": [],
    }


def _base_result(
    request: SelectionRequest,
    status: str,
    tile_manifest_path: Path,
    *,
    plan: Mapping[str, Any],
    tiles: Sequence[PlannedTile],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "myProject.selection.result",
        "pipeline_contract_version": PIPELINE_CONTRACT_VERSION,
        "building_publication_version": BUILDING_PUBLICATION_VERSION,
        "osm_prealign": True,
        "merge_stage_order": list(MERGE_STAGE_ORDER),
        "status": status,
        "selection_id": request.selection_id,
        "stable_id": request.stable_id,
        "workspace": str(request.workspace),
        "request": str(request.source),
        "output_dir": str(request.output_dir),
        "job_dir": str(request.job_dir),
        "tile_manifest": str(tile_manifest_path),
        "tile_count": len(tiles),
        "plan": dict(plan),
        "footprints": [
            {"id": footprint.identifier, "area_m2": footprint.area_m2, "status": "PENDING"}
            for footprint in request.footprints
        ],
        "minimesh": {
            "status": "NOT_BUILT",
            "source_obj": None,
            "note": "cropped.obj is the validated source for Java MiniTransformCLI hot-load",
        },
    }


def _failure_result(base: Mapping[str, Any], error: SelectionBridgeError) -> dict[str, Any]:
    output = dict(base)
    output.update(error.to_dict())
    output["finished_local_time"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return output


def build_selection(
    request_path: str | os.PathLike[str], *, execute: bool = False
) -> dict[str, Any]:
    """Plan or execute one GUI selection.

    Dry-run always writes ``tile_manifest.json`` and a ``PLANNED`` result but
    performs no network/GPU work.  Execution keeps a combined diagnostic OBJ
    and publishes only the individual buildings that pass; zero publishable
    buildings fails without replacing the previous READY publication.
    """

    request = load_selection_request(request_path)
    request.job_dir.mkdir(parents=True, exist_ok=True)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    zoom = _integer_option(request.options, "zoom", 20, minimum=0)
    if zoom > 23:
        raise SelectionBridgeError("invalid_request", "options.zoom must be <= 23")
    tile_size = _integer_option(request.options, "tile_size", 640)
    overlap = _number_option(request.options, "overlap_ratio", 0.10, minimum=0.0, maximum=1.0, maximum_inclusive=False)
    crop_ratio = _number_option(
        request.options,
        "crop_ratio",
        0.05,
        minimum=0.0,
        maximum=0.5,
        maximum_inclusive=False,
    )
    requested_padding = request.options.get(
        "block_mesh_padding",
        request.options.get("block_mesh_padding_m", 30.0),
    )
    padding = max(30.0, _finite("options.block_mesh_padding", requested_padding))
    tiles, plan = plan_web_mercator_tiles(
        request.footprints,
        request.frame,
        request.job_dir,
        zoom=zoom,
        size_px=tile_size,
        overlap_ratio=overlap,
        padding_m=padding,
        crop_ratio=crop_ratio,
    )
    tile_manifest_path = request.job_dir / "tile_manifest.json"
    tile_manifest = _tile_manifest_document(request, tiles, plan)
    # Contract: this is the first durable job artefact, before network/GPU work.
    _atomic_json(tile_manifest_path, tile_manifest)
    base = _base_result(request, "PLANNED", tile_manifest_path, plan=plan, tiles=tiles)
    _atomic_json(request.job_dir / "result.json", base)
    if not execute:
        return base

    try:
        pipeline_request = _pipeline_request(request, tiles, plan, tile_manifest_path)
        tile_manifest["dsm_requirement"] = {
            "status": "FILES_READY",
            "directory": str(pipeline_request.dsm_dir),
            "files": list(pipeline_request.dsm_files),
            "crs": pipeline_request.dsm_crs,
            "mandatory": True,
        }
        _atomic_json(tile_manifest_path, tile_manifest)
        validations, download_failures = ensure_satellites(
            tiles,
            download_missing=bool(request.options.get("download_missing", True)),
            minimum_bytes=_integer_option(request.options, "minimum_satellite_bytes", 5000),
        )
        tile_manifest["satellite_validation"] = validations
        tile_manifest["download_failures"] = download_failures
        if download_failures or len(validations) != len(tiles):
            tile_manifest["status"] = "SATELLITE_FAILED"
            _atomic_json(tile_manifest_path, tile_manifest)
            raise SelectionBridgeError(
                "satellite_set_incomplete",
                "all planned satellite PNGs must validate before inference starts",
                details={
                    "planned": len(tiles),
                    "validated": len(validations),
                    "failures": download_failures,
                },
            )
        tile_manifest["status"] = "SATELLITES_READY"
        _atomic_json(tile_manifest_path, tile_manifest)

        pipeline_manifest_path = request.job_dir / "top_level_pipeline_manifest.json"
        pipeline_manifest = load_reusable_pipeline_manifest(
            pipeline_manifest_path, request, tiles, pipeline_request
        )
        corrected_scene_reused = pipeline_manifest is not None
        if pipeline_manifest is None:
            timeout = _number_option(request.options, "timeout_seconds", 7200.0, minimum=1.0)
            pipeline_result = run_top_level_pipeline(
                pipeline_request,
                dry_run=False,
                timeout=timeout,
                check=False,
            )
            (request.job_dir / "pipeline.stdout.log").write_text(
                redact_text(pipeline_result.stdout), encoding="utf-8", newline="\n"
            )
            (request.job_dir / "pipeline.stderr.log").write_text(
                redact_text(pipeline_result.stderr), encoding="utf-8", newline="\n"
            )
            if not pipeline_result.ok:
                raise SelectionBridgeError(
                    "mesh_pipeline_failed",
                    f"top-level mesh pipeline exited with code {pipeline_result.returncode}",
                    details={
                        "returncode": pipeline_result.returncode,
                        "stdout_log": str(request.job_dir / "pipeline.stdout.log"),
                        "stderr_log": str(request.job_dir / "pipeline.stderr.log"),
                    },
                )
            pipeline_manifest = _read_json_object(
                pipeline_manifest_path, "top-level pipeline manifest"
            )
        else:
            (request.job_dir / "pipeline.reuse.log").write_text(
                "Reused validated DSM-corrected scene; GPU inference, merge and DSM were not rerun.\n",
                encoding="utf-8",
                newline="\n",
            )
        dsm_report = pipeline_manifest.get("dsm")
        if (
            not isinstance(dsm_report, Mapping)
            or dsm_report.get("required") is not True
            or dsm_report.get("status") != "APPLIED"
            or not math.isclose(
                _finite("DSM mesh vertex coverage", dsm_report.get("mesh_vertex_coverage_ratio")),
                1.0,
                abs_tol=1e-12,
            )
        ):
            raise SelectionBridgeError(
                "dsm_not_applied",
                "mandatory DSM correction was not fully applied to the merged mesh",
                details={"dsm": dsm_report},
            )
        selected_meshes = pipeline_manifest.get("selected_meshes", [])
        missing_mesh = pipeline_manifest.get("missing_mesh", [])
        if (
            pipeline_manifest.get("status") != "ok"
            or missing_mesh
            or not isinstance(selected_meshes, list)
            or len(selected_meshes) != len(tiles)
        ):
            raise SelectionBridgeError(
                "mesh_tile_set_incomplete",
                "exact tile mesh set is incomplete; partial buildings will not be published",
                details={
                    "expected": len(tiles),
                    "selected": len(selected_meshes) if isinstance(selected_meshes, list) else 0,
                    "missing": missing_mesh,
                    "pipeline_status": pipeline_manifest.get("status"),
                },
            )
        scene_raw = pipeline_manifest.get("output_scene_obj")
        if not isinstance(scene_raw, str):
            raise SelectionBridgeError("mesh_scene_missing", "pipeline manifest has no output_scene_obj")
        scene = Path(scene_raw).resolve(strict=False)
        scene_inspection = inspect_obj(scene, y_percentiles=(0, 2, 100))
        if not scene_inspection.vertex_count or not scene_inspection.triangulated_face_count:
            raise SelectionBridgeError("mesh_scene_empty", f"pipeline scene is empty: {scene}")
        job_origin_lat = _finite("pipeline origin_lat", pipeline_manifest.get("origin_lat"))
        job_origin_lon = _finite("pipeline origin_lon", pipeline_manifest.get("origin_lon"))
        mesh = read_obj(scene)
        ground_percentile = _number_option(request.options, "ground_percentile", 2.0, minimum=0.0, maximum=25.0)
        mesh, transform = rebase_and_ground_mesh(
            mesh,
            job_origin_lat,
            job_origin_lon,
            request.frame,
            ground_percentile=ground_percentile,
        )

        config_document, _ = _request_config_document(request)
        configured_mesh = config_document.get("mesh")
        if not isinstance(configured_mesh, Mapping):
            configured_mesh = {}
        strict_model_completeness = request.options.get(
            "strict_model_completeness",
            configured_mesh.get("strict_model_completeness", True),
        )
        if not isinstance(strict_model_completeness, bool):
            raise SelectionBridgeError(
                "invalid_request",
                "strict_model_completeness must be true or false",
            )

        spacing = _number_option(request.options, "coverage_sample_spacing_m", 1.5, minimum=0.25)
        min_height = _number_option(request.options, "minimum_above_ground_m", 2.5, minimum=0.1)
        min_coverage = _number_option(
            request.options,
            "minimum_projected_coverage",
            0.85,
            minimum=0.0,
            maximum=1.0,
        )
        crop_padding = _number_option(request.options, "crop_padding_m", 1.0, minimum=0.0)
        available_stems = {
            Path(str(path)).stem for path in selected_meshes if isinstance(path, str)
        }
        footprint_reports: list[dict[str, Any]] = []
        all_face_indices: set[int] = set()
        for footprint in request.footprints:
            expanded = (
                footprint.bounds[0] - padding,
                footprint.bounds[1] - padding,
                footprint.bounds[2] + padding,
                footprint.bounds[3] + padding,
            )
            tile_samples = sample_bbox(expanded, max(2.0, min(5.0, spacing * 3.0)))
            tile_ratio = tile_coverage_ratio(tile_samples, request.frame, tiles, available_stems)
            if strict_model_completeness:
                projected = assess_footprint_completeness(
                    mesh,
                    footprint,
                    sample_spacing_m=spacing,
                    minimum_above_ground_m=min_height,
                    minimum_projected_coverage=min_coverage,
                )
                faces = crop_face_indices(mesh, footprint, crop_padding_m=crop_padding)
                complete = (
                    math.isclose(tile_ratio, 1.0, abs_tol=1e-12)
                    and projected["complete"]
                    and bool(faces)
                )
                report_status = "COMPLETE" if complete else "INCOMPLETE"
            else:
                projected = {
                    "model_completeness_check_enabled": False,
                    "selection_mode": "coarse_bbox",
                    "projected_coverage_ratio": None,
                    "minimum_projected_coverage": None,
                    "qualifying_triangle_count": None,
                    "complete": None,
                    "warning": (
                        "strict projected model completeness is temporarily disabled; "
                        "the coarse crop may contain incomplete or neighbouring geometry"
                    ),
                }
                faces = crop_face_indices_bbox(mesh, footprint, crop_padding_m=crop_padding)
                complete = math.isclose(tile_ratio, 1.0, abs_tol=1e-12) and bool(faces)
                report_status = "COARSE_READY" if complete else "INCOMPLETE"
            if complete:
                all_face_indices.update(faces)
            footprint_reports.append(
                {
                    "id": footprint.identifier,
                    "area_m2": footprint.area_m2,
                    "status": report_status,
                    "tile_coverage_ratio_with_padding": tile_ratio,
                    "required_tile_coverage_ratio": 1.0,
                    "crop_face_count": len(faces),
                    **projected,
                }
            )
        minimum_surface_height = _number_option(
            request.options, "minimum_building_surface_height_m", 0.5, minimum=0.0
        )
        building_assignment_cell = _number_option(
            request.options, "building_assignment_cell_m", 1.0, minimum=0.1
        )
        minimum_building_faces = _integer_option(
            request.options, "minimum_building_faces", 12
        )
        minimum_building_vertices = _integer_option(
            request.options, "minimum_building_vertices", 8
        )
        minimum_component_faces = _integer_option(
            request.options, "minimum_building_component_faces", 1
        )
        minimum_component_ratio = _number_option(
            request.options,
            "minimum_building_component_ratio",
            0.0,
            minimum=0.0,
            maximum=1.0,
        )
        minimum_relief = _number_option(
            request.options,
            "minimum_building_relief_m",
            float(configured_mesh.get("minimum_building_relief_m", 1.5)),
            minimum=0.1,
        )
        assigned_faces, building_grounds = assign_building_faces(
            mesh,
            request.footprints,
            minimum_surface_height_m=minimum_surface_height,
            spatial_cell_m=building_assignment_cell,
        )
        source_feature_ids = load_source_feature_ids(
            request.workspace, (footprint.identifier for footprint in request.footprints)
        )
        report_by_id = {str(report["id"]): report for report in footprint_reports}
        prepared_buildings: list[
            tuple[SelectedFootprint, ObjMesh, dict[str, Any]]
        ] = []
        building_entries: list[dict[str, Any]] = []
        publishable_status = "READY" if strict_model_completeness else "COARSE_READY"
        for footprint in request.footprints:
            identifier = footprint.identifier
            raw_faces = assigned_faces[identifier]
            deduplicated_faces, source_cleanup = clean_face_indices(mesh, raw_faces)
            filtered_faces, component_metrics = filter_building_components(
                mesh,
                deduplicated_faces,
                minimum_component_faces=minimum_component_faces,
                minimum_component_ratio=minimum_component_ratio,
            )
            building_mesh, cleanup_metrics = prepare_building_mesh(
                mesh,
                filtered_faces,
                ground_height_m=building_grounds[identifier],
            )
            metrics = building_subset_metrics(
                building_mesh,
                range(len(building_mesh.faces)),
                ground_height_m=building_grounds[identifier],
            )
            metrics.update(component_metrics)
            metrics.update(
                {
                    "assigned_face_count_before_deduplication": len(raw_faces),
                    "duplicate_face_count_removed": source_cleanup[
                        "same_winding_duplicate_face_count_removed"
                    ],
                    "degenerate_face_count_removed": source_cleanup[
                        "degenerate_face_count_removed"
                    ],
                    "assigned_face_count_before_component_filter": len(deduplicated_faces),
                    "dsm_coverage_ratio": float(dsm_report["mesh_vertex_coverage_ratio"]),
                    "unique_face_ownership": True,
                    "component_connectivity": "shared_edge",
                    "all_owned_components_retained_by_default": (
                        minimum_component_faces == 1 and minimum_component_ratio == 0.0
                    ),
                    **cleanup_metrics,
                }
            )
            footprint_report = report_by_id[identifier]
            reasons: list[str] = []
            if not math.isclose(
                float(footprint_report["tile_coverage_ratio_with_padding"]),
                1.0,
                abs_tol=1e-12,
            ):
                reasons.append("tile_coverage_incomplete")
            if strict_model_completeness and footprint_report.get("complete") is not True:
                reasons.append("projected_model_incomplete")
            if metrics["face_count"] == 0:
                status = "EMPTY"
                reasons.append("no_strictly_owned_above-ground_faces")
            else:
                if int(metrics["face_count"]) < minimum_building_faces:
                    reasons.append("face_count_below_minimum")
                if int(metrics["vertex_count"]) < minimum_building_vertices:
                    reasons.append("vertex_count_below_minimum")
                if float(metrics["relief_m"]) < minimum_relief:
                    reasons.append("building_relief_below_minimum")
                status = "REJECTED" if reasons else publishable_status

            source_feature_id = source_feature_ids.get(identifier)
            osm_type: str | None = None
            osm_id: str | None = None
            if source_feature_id and re.fullmatch(r"(?:node|way|relation)/[0-9]+", source_feature_id):
                osm_type, osm_id = source_feature_id.split("/", 1)
            relative_dir = f"buildings/{identifier}"
            published = status in {"READY", "COARSE_READY"}
            entry: dict[str, Any] = {
                "id": identifier,
                "component_id": identifier,
                "footprint_id": identifier,
                "footprint_ids": [identifier],
                "source_feature_id": source_feature_id,
                "osm_type": osm_type,
                "osm_id": osm_id,
                "status": status,
                "publishable": published,
                "relative_dir": relative_dir,
                "outputs": (
                    {
                        "cropped_obj": f"{relative_dir}/cropped.obj",
                        "gis_obj": f"{relative_dir}/gis.obj",
                        "gis_footprints_obj": f"{relative_dir}/gis_footprints.obj",
                    }
                    if published
                    else None
                ),
                "extraction_method": (
                    "unique_centroid_owner_then_exact_ground_clip_and_ground_cap_v2"
                ),
                "metrics": metrics,
                "minimums": {
                    "face_count": minimum_building_faces,
                    "vertex_count": minimum_building_vertices,
                    "relief_m": minimum_relief,
                    "surface_height_above_local_ground_m": minimum_surface_height,
                    "assignment_spatial_cell_m": building_assignment_cell,
                },
                "reasons": reasons,
                "warnings": (
                    []
                    if strict_model_completeness
                    else [
                        "strict projected completeness is disabled; this building is COARSE_READY"
                    ]
                ),
            }
            building_entries.append(entry)
            footprint_report["source_feature_id"] = source_feature_id
            footprint_report["building_status"] = status
            footprint_report["building_metrics"] = metrics
            footprint_report["building_reasons"] = reasons
            if published:
                prepared_buildings.append((footprint, building_mesh, entry))

        if not prepared_buildings:
            raise SelectionBridgeError(
                "no_publishable_buildings",
                "no selected footprint passed the per-building geometry gates",
                details={"footprints": footprint_reports, "buildings": building_entries},
            )

        staging = request.job_dir / f"publish-{uuid.uuid4().hex[:12]}"
        staging.mkdir(parents=True)
        mesh_stats = write_obj_subset(mesh, all_face_indices, staging / "cropped.obj")
        gis_stats = write_gis_obj(request.footprints, staging / "gis.obj")
        shutil.copy2(staging / "gis.obj", staging / "gis_footprints.obj")
        building_summary, building_index = stage_building_publication(
            staging,
            mesh,
            prepared_buildings,
            building_entries,
            selection_id=request.selection_id,
            stable_id=request.stable_id,
        )
        if request.options.get("roof_reference_enabled", True) is False:
            roof_reference_report: dict[str, Any] = {
                "status": "DISABLED",
                "source_tile_manifest": str(tile_manifest_path),
                "requested": len(prepared_buildings),
                "ready": 0,
                "unavailable": len(prepared_buildings),
                "geometry_modified": False,
                "buildings": {
                    footprint.identifier: {
                        "status": "DISABLED",
                        "error": {
                            "code": "roof_reference_disabled",
                            "message": "offline roof reference generation is disabled by request option",
                        },
                    }
                    for footprint, _, _ in prepared_buildings
                },
            }
        else:
            try:
                roof_reference_report = generate_roof_references(
                    tile_manifest_path=tile_manifest_path,
                    buildings_root=staging / "buildings",
                    cache_buildings_root=request.output_dir / "buildings",
                    footprints=[footprint for footprint, _, _ in prepared_buildings],
                    frame=request.frame,
                    padding_m=float(request.options.get("roof_reference_padding_m", 2.0)),
                    erosion_m=float(request.options.get("roof_reference_erosion_m", 0.5)),
                    minimum_source_coverage=float(
                        request.options.get("roof_reference_minimum_coverage", 0.95)
                    ),
                    minimum_mask_pixels=int(
                        request.options.get("roof_reference_minimum_mask_pixels", 16)
                    ),
                    max_dimension_px=int(
                        request.options.get("roof_reference_max_dimension_px", 2048)
                    ),
                )
            except Exception as exc:
                # Appearance is optional.  A malformed option, corrupt image or
                # exhausted image dependency must never reject valid geometry.
                roof_reference_report = {
                    "status": "UNAVAILABLE",
                    "source_tile_manifest": str(tile_manifest_path),
                    "requested": len(prepared_buildings),
                    "ready": 0,
                    "unavailable": len(prepared_buildings),
                    "geometry_modified": False,
                    "buildings": {
                        footprint.identifier: {
                            "status": "UNAVAILABLE",
                            "error": {
                                "code": "roof_reference_soft_failure",
                                "message": redact_text(
                                    f"roof reference generation failed: {type(exc).__name__}: {exc}"
                                ),
                            },
                        }
                        for footprint, _, _ in prepared_buildings
                    },
                }
        roof_reference_summary = _attach_roof_reference_metadata(
            staging, building_entries, building_index, roof_reference_report
        )
        ready = dict(base)
        ready.update(
            {
                "status": "READY",
                "building_publication_version": BUILDING_PUBLICATION_VERSION,
                "footprints": footprint_reports,
                "buildings_summary": building_summary,
                "buildings": building_entries,
                "roof_references": roof_reference_summary,
                "model_completeness": {
                    "enabled": strict_model_completeness,
                    "mode": "strict_projected" if strict_model_completeness else "coarse_bbox",
                },
                "pipeline_manifest": str(pipeline_manifest_path),
                "pipeline": {
                    "kind": "Sat3DGen/mesh_pipeline top-level exact allowlist",
                    "pipeline_contract_version": PIPELINE_CONTRACT_VERSION,
                    "osm_prealign": True,
                    "merge_stage_order": list(MERGE_STAGE_ORDER),
                    "origin_lat": job_origin_lat,
                    "origin_lon": job_origin_lon,
                    "selected_mesh_count": len(selected_meshes),
                    "scene_obj": str(scene),
                    "corrected_scene_reused": corrected_scene_reused,
                },
                "dsm": dict(dsm_report),
                "workspace_frame": {
                    "origin_lat": request.frame.origin_lat,
                    "origin_lon": request.frame.origin_lon,
                },
                "transform": transform,
                "outputs": {
                    "cropped_obj": str(request.output_dir / "cropped.obj"),
                    "gis_obj": str(request.output_dir / "gis.obj"),
                    "gis_footprints_obj": str(request.output_dir / "gis_footprints.obj"),
                    "buildings_index": str(request.output_dir / "buildings" / "index.json"),
                },
                "mesh": mesh_stats,
                "gis": gis_stats,
                "minimesh": {
                    "status": "DIAGNOSTIC_ONLY_PENDING_JAVA_CONVERSION",
                    "source_obj": str(request.output_dir / "cropped.obj"),
                    "note": (
                        "cropped.obj is the whole-selection diagnostic scene; Java loads publishable "
                        "buildings as individual BlockGen instances"
                    ),
                },
                "finished_local_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )
        _atomic_json(staging / "result.json", ready)
        _commit_ready(request, staging)
        try:
            staging.rmdir()
        except OSError:
            pass
        tile_manifest["status"] = "READY"
        tile_manifest["dsm_requirement"] = dict(dsm_report)
        _atomic_json(tile_manifest_path, tile_manifest)
        _atomic_json(request.job_dir / "result.json", ready)
        return ready
    except SelectionBridgeError as exc:
        failed = _failure_result(base, exc)
        tile_manifest["status"] = "FAILED"
        tile_manifest["error"] = exc.to_dict()["error"]
        _atomic_json(tile_manifest_path, tile_manifest)
        _atomic_json(request.job_dir / "result.json", failed)
        return failed
    except BaseException as exc:
        safe = SelectionBridgeError(
            "selection_internal_error",
            f"selection job failed: {type(exc).__name__}: {redact_text(exc)}",
        )
        failed = _failure_result(base, safe)
        tile_manifest["status"] = "FAILED"
        tile_manifest["error"] = safe.to_dict()["error"]
        _atomic_json(tile_manifest_path, tile_manifest)
        _atomic_json(request.job_dir / "result.json", failed)
        return failed


__all__ = [
    "BIG_IMAGE_PIPELINE_CONTRACT_VERSION",
    "BUILDING_PUBLICATION_VERSION",
    "ObjMesh",
    "PlannedTile",
    "SelectedFootprint",
    "SelectionBridgeError",
    "SelectionRequest",
    "assess_footprint_completeness",
    "assign_building_faces",
    "building_subset_metrics",
    "build_selection",
    "crop_face_indices",
    "deduplicate_face_indices",
    "filter_building_components",
    "ensure_satellites",
    "load_selection_request",
    "load_reusable_pipeline_manifest",
    "lonlat_to_web_mercator",
    "load_source_feature_ids",
    "plan_web_mercator_tiles",
    "point_in_polygon",
    "read_obj",
    "rebase_and_ground_mesh",
    "redact_text",
    "sample_polygon_interior",
    "stage_building_publication",
    "tile_coverage_ratio",
    "validate_png",
    "web_mercator_to_lonlat",
]
