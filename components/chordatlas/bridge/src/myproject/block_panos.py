"""Build a ChordAtlas Street View todo.list from one selected OSM block."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .streetview_panos import (
    FILENAME_ROLL_DEG,
    FILENAME_TILT_DEG,
    KEY_ENV,
    GoogleStreetViewClient,
    ImportFailure,
    PanoMetadata,
    TodoEntry,
    atomic_write_json,
    format_angle,
    format_coordinate,
    parse_todo,
    redact_text,
    sha256_file,
)


REQUEST_KIND = "myProject.block_panoramas.request"
REPORT_SCHEMA = "myProject.block_panoramas.plan-v1"
PROMOTION_SCHEMA = "myProject.block_panoramas.promotion-v1"
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
METRES_PER_DEGREE = 111320.0
_atomic_replace = os.replace


@dataclass(frozen=True)
class LocalFrame:
    origin_lat: float
    origin_lon: float


@dataclass(frozen=True)
class Footprint:
    footprint_id: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Request:
    source: Path
    workspace: Path
    selection_id: str
    footprints: tuple[Footprint, ...]


@dataclass(frozen=True)
class Seed:
    seed_id: str
    footprint_id: str
    edge_index: int
    x: float
    z: float


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _contained(root: Path, child: Path, label: str) -> Path:
    root, child = root.resolve(), child.resolve(strict=False)
    try:
        child.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes workspace") from exc
    return child


def _read_json(path: Path, label: str, limit: int = 4 * 1024 * 1024) -> dict:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"invalid {label}: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} root must be an object")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def load_request(path: str | os.PathLike[str]) -> Request:
    source = Path(path).resolve()
    data = _read_json(source, "block panorama request")
    if data.get("schema_version") != 1 or data.get("kind") != REQUEST_KIND:
        raise ValueError("unsupported block panorama request schema/kind")
    workspace_raw = data.get("workspace")
    if not isinstance(workspace_raw, str):
        raise ValueError("workspace must be a path string")
    workspace = Path(workspace_raw).resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace directory not found: {workspace}")
    _contained(workspace, source, "request")
    selection_id = data.get("selection_id")
    if not isinstance(selection_id, str) or not SAFE_ID.fullmatch(selection_id):
        raise ValueError("selection_id is unsafe")
    raw_footprints = data.get("footprints")
    if not isinstance(raw_footprints, list) or not 1 <= len(raw_footprints) <= 100:
        raise ValueError("footprints must contain 1..100 polygons")
    footprints = []
    seen = set()
    for index, raw in enumerate(raw_footprints):
        if not isinstance(raw, dict):
            raise ValueError(f"footprints[{index}] must be an object")
        footprint_id = raw.get("id")
        if (
            not isinstance(footprint_id, str)
            or not SAFE_ID.fullmatch(footprint_id)
            or footprint_id in seen
        ):
            raise ValueError(f"footprints[{index}].id is invalid or duplicated")
        seen.add(footprint_id)
        raw_points = raw.get("points")
        if not isinstance(raw_points, list) or not 3 <= len(raw_points) <= 10000:
            raise ValueError(f"footprint {footprint_id} needs 3..10000 points")
        points = []
        for point_index, raw_point in enumerate(raw_points):
            if not isinstance(raw_point, list) or len(raw_point) != 2:
                raise ValueError(f"footprint {footprint_id} point {point_index} must be [x,z]")
            x = _number(raw_point[0], "x")
            z = _number(raw_point[1], "z")
            if abs(x) > 10_000_000 or abs(z) > 10_000_000:
                raise ValueError("implausible local footprint coordinate")
            points.append((x, z))
        if points[0] == points[-1]:
            points.pop()
        if len(points) < 3:
            raise ValueError(f"footprint {footprint_id} collapses after closure removal")
        footprints.append(Footprint(footprint_id, tuple(points)))
    return Request(source, workspace, selection_id, tuple(footprints))


def load_frame(workspace: Path) -> tuple[LocalFrame, Path]:
    manifest = _contained(workspace, workspace / "manifest.json", "manifest")
    data = _read_json(manifest, "workspace manifest", 1024 * 1024)
    frame = data.get("frame")
    axes = frame.get("axes") if isinstance(frame, dict) else None
    if not isinstance(frame, dict) or not isinstance(axes, dict):
        raise ValueError("workspace manifest frame.axes is missing")
    if axes != {"x": "east", "y": "up", "z": "south"}:
        raise ValueError("workspace frame must be X=east, Y=up, Z=south")
    lat = _number(frame.get("origin_lat"), "frame.origin_lat")
    lon = _number(frame.get("origin_lon"), "frame.origin_lon")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("workspace origin is outside WGS84")
    if abs(math.cos(math.radians(lat))) < 1e-6:
        raise ValueError("workspace origin is too close to a pole")
    return LocalFrame(lat, lon), manifest


def local_to_wgs84(x: float, z: float, frame: LocalFrame) -> tuple[float, float]:
    """Invert the approximate local transform used by MyProject PanoGen."""
    lat = frame.origin_lat - z / METRES_PER_DEGREE
    lon = frame.origin_lon + x / (
        METRES_PER_DEGREE * math.cos(math.radians(frame.origin_lat))
    )
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("panorama query lies outside WGS84")
    return lat, lon


def _area(points: Sequence[tuple[float, float]]) -> float:
    closed = list(points[1:]) + [points[0]]
    return 0.5 * sum(
        ax * bz - bx * az for (ax, az), (bx, bz) in zip(points, closed)
    )


def _sample_one(footprint: Footprint, spacing_m: float, offset_m: float) -> list[Seed]:
    area = _area(footprint.points)
    if abs(area) < 1e-6:
        raise ValueError(f"footprint {footprint.footprint_id} has zero area")
    following = list(footprint.points[1:]) + [footprint.points[0]]
    segments = []
    perimeter = 0.0
    for edge, ((ax, az), (bx, bz)) in enumerate(zip(footprint.points, following)):
        dx, dz = bx - ax, bz - az
        length = math.hypot(dx, dz)
        if length > 1e-6:
            segments.append((edge, ax, az, dx, dz, length))
            perimeter += length
    if not segments:
        raise ValueError(f"footprint {footprint.footprint_id} has no perimeter")
    count = max(1, math.ceil(perimeter / spacing_m))
    seeds, cursor, segment_start = [], 0, 0.0
    for sample_index in range(count):
        distance = (sample_index + 0.5) * perimeter / count
        while cursor + 1 < len(segments) and distance > segment_start + segments[cursor][5]:
            segment_start += segments[cursor][5]
            cursor += 1
        edge, ax, az, dx, dz, length = segments[cursor]
        along = max(0.0, min(1.0, (distance - segment_start) / length))
        x, z = ax + along * dx, az + along * dz
        # Positive X/Z shoelace area is counter-clockwise: exterior is right.
        nx, nz = ((dz / length, -dx / length) if area > 0 else (-dz / length, dx / length))
        seeds.append(
            Seed(
                f"seed-{footprint.footprint_id}-{sample_index:04d}",
                footprint.footprint_id,
                edge,
                x + offset_m * nx,
                z + offset_m * nz,
            )
        )
    return seeds


def build_seeds(
    footprints: Sequence[Footprint],
    spacing_m: float = 18.0,
    offset_m: float = 8.0,
    max_seeds: int = 24,
) -> list[Seed]:
    if not 3 <= spacing_m <= 100 or not 0 <= offset_m <= 50 or not 1 <= max_seeds <= 64:
        raise ValueError("invalid spacing/offset/max-seeds")
    all_seeds = [seed for footprint in footprints for seed in _sample_one(footprint, spacing_m, offset_m)]
    unique, seen = [], set()
    for seed in all_seeds:
        key = (round(seed.x, 3), round(seed.z, 3))
        if key not in seen:
            seen.add(key)
            unique.append(seed)
    if len(unique) <= max_seeds:
        return unique
    return [unique[(index * len(unique)) // max_seeds] for index in range(max_seeds)]


def _todo_line(metadata: PanoMetadata) -> str:
    return "_".join(
        (
            format_coordinate(metadata.latitude),
            format_coordinate(metadata.longitude),
            "0",
            "0",
            format_angle(FILENAME_TILT_DEG),
            format_angle(FILENAME_ROLL_DEG),
            metadata.pano_id,
        )
    )


def _publish_todo(path: Path, text: str, backup_label: str = "block-plan") -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = text.encode("utf-8")
    if path.exists() and path.read_bytes() == encoded:
        return {"status": "unchanged", "path": str(path), "backup": None}
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(
            f"todo.list.pre-{backup_label}.{stamp}.{sha256_file(path)[:10]}.bak"
        )
        suffix = 1
        while backup.exists():
            backup = path.with_name(f"{backup.name}.{suffix}")
            suffix += 1
        backup.write_bytes(path.read_bytes())
    fd, temporary_name = tempfile.mkstemp(prefix=".todo.", suffix=".part", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        _atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "published",
        "path": str(path),
        "backup": str(backup) if backup else None,
    }


def prepare_block_panos(
    request_path: str | os.PathLike[str],
    *,
    todo_path: str | os.PathLike[str],
    report_path: Optional[str | os.PathLike[str]] = None,
    spacing_m: float = 18.0,
    offset_m: float = 8.0,
    max_seeds: int = 24,
    radius: int = 50,
    timeout: float = 30.0,
    retries: int = 2,
    dry_run: bool = False,
    client: Optional[GoogleStreetViewClient] = None,
) -> dict:
    if not 1 <= radius <= 1000 or timeout <= 0 or retries < 0:
        raise ValueError("invalid radius/timeout/retries")
    request = load_request(request_path)
    frame, manifest = load_frame(request.workspace)
    raw_report = Path(report_path) if report_path else request.source.parent / "plan_report.json"
    raw_todo = Path(todo_path)
    if raw_report.is_symlink() or raw_todo.is_symlink():
        raise ValueError("plan report and scoped todo paths must not be symlinks")
    report = _contained(request.source.parent, raw_report, "plan report")
    todo = _contained(request.source.parent, raw_todo, "selection-scoped todo.list")
    if todo.name.lower() != "todo.list":
        raise ValueError("selection-scoped todo path must end in todo.list")
    live_todo = (request.workspace / "panos" / "todo.list").resolve(strict=False)
    if todo == live_todo:
        raise ValueError("planning must not write the live workspace panos/todo.list")
    if report == request.source or report == todo:
        raise ValueError("plan report path conflicts with a request or todo input")
    seeds = build_seeds(request.footprints, spacing_m, offset_m, max_seeds)
    payload = {
        "schema": REPORT_SCHEMA,
        "status": "PLANNED" if dry_run else "RESOLVING",
        "started_utc": _utc_now(),
        "finished_utc": None,
        "request_path": str(request.source),
        "request_sha256": sha256_file(request.source),
        "workspace": str(request.workspace),
        "workspace_manifest": str(manifest),
        "selection_id": request.selection_id,
        "config": {
            "spacing_m": spacing_m,
            "outward_offset_m": offset_m,
            "max_seeds": max_seeds,
            "metadata_radius_m": radius,
            "api_key_source": KEY_ENV,
            "api_key_recorded": False,
            "coordinate_mode": "myproject-local",
        },
        "queries": [],
        "todo": {"path": str(todo), "status": "not_written", "sha256": None},
        "summary": {
            "seeds": len(seeds),
            "metadata_ok": 0,
            "unique_panoramas": 0,
            "duplicates": 0,
            "failed": 0,
        },
    }
    for seed in seeds:
        lat, lon = local_to_wgs84(seed.x, seed.z, frame)
        payload["queries"].append(
            {
                "seed_id": seed.seed_id,
                "footprint_id": seed.footprint_id,
                "edge_index": seed.edge_index,
                "local": [seed.x, seed.z],
                "query_wgs84": [lon, lat],
                "status": "planned",
            }
        )
    atomic_write_json(report, payload)
    if dry_run:
        payload["finished_utc"] = _utc_now()
        atomic_write_json(report, payload)
        return payload

    if client is None:
        api_key = os.environ.get(KEY_ENV)
        if not api_key:
            raise ValueError(f"{KEY_ENV} is not set; inject it into the GUI process environment")
        client = GoogleStreetViewClient(
            api_key, radius_metres=radius, timeout_seconds=timeout, retries=retries
        )
    return _resolve_and_publish(request, seeds, payload, report, todo, client)


def _resolve_and_publish(
    request: Request,
    seeds: Sequence[Seed],
    payload: dict,
    report: Path,
    todo: Path,
    client: GoogleStreetViewClient,
) -> dict:
    unique_metadata, first_seed_by_pano = [], {}
    secret = getattr(client, "_api_key", "")
    for line_number, (seed, item) in enumerate(zip(seeds, payload["queries"]), 1):
        lon, lat = item["query_wgs84"]
        entry = TodoEntry(
            line_number,
            "",
            lat,
            lon,
            0.0,
            "0",
            0.0,
            FILENAME_TILT_DEG,
            FILENAME_ROLL_DEG,
            seed.seed_id,
        )
        try:
            metadata = client.fetch_metadata(entry)
            payload["summary"]["metadata_ok"] += 1
            item["metadata"] = {
                "pano_id": metadata.pano_id,
                "latitude": metadata.latitude,
                "longitude": metadata.longitude,
                "date": metadata.date,
                "copyright": metadata.copyright,
            }
            if metadata.pano_id in first_seed_by_pano:
                item["status"] = "duplicate"
                item["duplicate_of"] = first_seed_by_pano[metadata.pano_id]
                payload["summary"]["duplicates"] += 1
            else:
                item["status"] = "unique"
                first_seed_by_pano[metadata.pano_id] = seed.seed_id
                unique_metadata.append(metadata)
        except ImportFailure as exc:
            item["status"] = "failed"
            item["error"] = exc.as_report((secret,))
            payload["summary"]["failed"] += 1
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = {
                "stage": "metadata",
                "code": "UNEXPECTED_ERROR",
                "message": redact_text(exc, (secret,))[:2000],
            }
            payload["summary"]["failed"] += 1
        atomic_write_json(report, payload)

    payload["summary"]["unique_panoramas"] = len(unique_metadata)
    if not unique_metadata:
        payload["status"] = "FAILED"
        payload["finished_utc"] = _utc_now()
        atomic_write_json(report, payload)
        raise ValueError("no Street View panorama resolved; see plan_report.json")
    publication = _publish_todo(
        todo, "\n".join(_todo_line(metadata) for metadata in unique_metadata) + "\n"
    )
    payload["todo"] = {
        **publication,
        "sha256": sha256_file(todo),
        "records": len(unique_metadata),
    }
    payload["status"] = "READY"
    payload["finished_utc"] = _utc_now()
    atomic_write_json(report, payload)
    return payload


def promote_block_panos(
    request_path: str | os.PathLike[str],
    *,
    todo_path: str | os.PathLike[str],
    plan_report_path: str | os.PathLike[str],
    batch_report_path: str | os.PathLike[str],
    report_path: Optional[str | os.PathLike[str]] = None,
) -> dict:
    """Promote a scoped todo only after its approved batch report validates."""
    request = load_request(request_path)
    raw_live_dir = request.workspace / "panos"
    raw_live = raw_live_dir / "todo.list"
    # Check the lexical workspace targets before canonicalisation follows a
    # link.  Promotion must never replace another in-workspace or external file.
    if raw_live_dir.is_symlink() or raw_live.is_symlink():
        raise ValueError("workspace panos/todo.list path must not be a symlink")
    raw_scoped = Path(todo_path)
    raw_plan = Path(plan_report_path)
    raw_batch = Path(batch_report_path)
    raw_report = Path(report_path) if report_path else request.source.parent / "promotion_report.json"
    if any(path.is_symlink() for path in (raw_scoped, raw_plan, raw_batch, raw_report)):
        raise ValueError("promotion input/report paths must not be symlinks")
    scoped = _contained(request.source.parent, raw_scoped, "selection-scoped todo.list")
    plan_path = _contained(request.source.parent, raw_plan, "plan report")
    batch_path = _contained(request.source.parent, raw_batch, "batch report")
    report = _contained(request.source.parent, raw_report, "promotion report")
    if report in {request.source, scoped, plan_path, batch_path}:
        raise ValueError("promotion report path conflicts with an input")
    if scoped.name.lower() != "todo.list" or not scoped.is_file():
        raise ValueError("selection-scoped todo.list is missing or unsafe")
    records = parse_todo(scoped)
    scoped_sha = sha256_file(scoped)

    plan = _read_json(plan_path, "block panorama plan report")
    plan_todo = plan.get("todo") if isinstance(plan.get("todo"), dict) else {}
    if (
        plan.get("schema") != REPORT_SCHEMA
        or plan.get("status") != "READY"
        or plan.get("selection_id") != request.selection_id
        or Path(str(plan_todo.get("path", ""))).resolve(strict=False) != scoped
        or plan_todo.get("sha256") != scoped_sha
    ):
        raise ValueError("plan report does not authorize this scoped todo.list")

    batch = _read_json(batch_path, "Street View batch report")
    live_dir = _contained(request.workspace, raw_live_dir, "workspace panos directory")
    summary = batch.get("summary") if isinstance(batch.get("summary"), dict) else {}
    if (
        batch.get("schema") != "chordatlas-static-pano-report-v1"
        or batch.get("mode") != "batch"
        or batch.get("dry_run") is not False
        or batch.get("todo_sha256") != scoped_sha
        or Path(str(batch.get("output_dir", ""))).resolve(strict=False) != live_dir
        or summary.get("failed") != 0
        or not isinstance(batch.get("prevalidated_sample"), dict)
        or not batch.get("finished_utc")
    ):
        raise ValueError("approved batch report does not authorize todo.list promotion")

    live_dir.mkdir(parents=True, exist_ok=True)
    live = _contained(request.workspace, raw_live, "live todo.list")
    payload = {
        "schema": PROMOTION_SCHEMA,
        "status": "PROMOTING",
        "started_utc": _utc_now(),
        "finished_utc": None,
        "selection_id": request.selection_id,
        "scoped_todo": {"path": str(scoped), "sha256": scoped_sha, "records": len(records)},
        "live_todo": {"path": str(live), "sha256": None, "backup": None},
    }
    atomic_write_json(report, payload)
    try:
        publication = _publish_todo(
            live, scoped.read_text(encoding="utf-8"), backup_label="block-promotion"
        )
        live_sha = sha256_file(live)
        if live_sha != scoped_sha:
            raise OSError("promoted todo.list hash mismatch")
    except Exception as exc:
        payload["status"] = "FAILED"
        payload["finished_utc"] = _utc_now()
        payload["error"] = redact_text(exc)[:2000]
        atomic_write_json(report, payload)
        raise
    payload["status"] = "UNCHANGED" if publication["status"] == "unchanged" else "PROMOTED"
    payload["finished_utc"] = _utc_now()
    payload["live_todo"] = {
        "path": str(live),
        "sha256": live_sha,
        "backup": publication.get("backup"),
    }
    atomic_write_json(report, payload)
    return payload
