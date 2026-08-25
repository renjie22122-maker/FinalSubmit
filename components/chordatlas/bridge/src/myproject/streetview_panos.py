#!/usr/bin/env python3
"""Google Street View Static API importer for myProject/ChordAtlas workspaces.

The program deliberately uses only ``GOOGLE_MAPS_API_KEY`` from the process
environment.  It never accepts a key on the command line, never stores it in a
report, and never logs request URLs.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from PIL import Image, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover - exercised only on an unprepared host
    raise SystemExit(
        "Pillow is required, but is not installed in the selected Python. "
        "Use a Python that already provides Pillow; this tool never modifies Conda."
    ) from exc


KEY_ENV = "GOOGLE_MAPS_API_KEY"
METADATA_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMAGE_ENDPOINT = "https://maps.googleapis.com/maps/api/streetview"
FACE_SIZE = 640
DEFAULT_OUTPUT_WIDTH = 2560

# A perfectly level filename (tilt=90, roll=0) is removed by the original
# PanoGen.calculate(): rx == 0 && abs(rz - 2*pi) < 1e-6.  A 0.001 degree roll
# is visually negligible but robustly clears that loader sentinel.
DEFAULT_COORDINATE_MODE = "myproject-local"
COORDINATE_MODE_HEADINGS = {
    # myProject's workspace-local mesh frame is X=east, Y=up, Z=south.
    # Pano.castTo() was authored for X=west, Z=north, so a 180 degree yaw is
    # required while retaining north at the centre of the equirectangular image.
    "myproject-local": 180.0,
    # Original ChordAtlas geographic projection uses X=west, Z=north.
    "original-geographic": 0.0,
}
FILENAME_HEADING_DEG = COORDINATE_MODE_HEADINGS[DEFAULT_COORDINATE_MODE]
FILENAME_TILT_DEG = 90.0
FILENAME_ROLL_DEG = 0.001
ORIENTATION_CONTRACT = "chordatlas-north-centre-v1"

VIEW_SPECS: Tuple[Tuple[str, float, float], ...] = (
    ("north", 0.0, 0.0),
    ("east", 90.0, 0.0),
    ("south", 180.0, 0.0),
    ("west", 270.0, 0.0),
    ("up", 0.0, 90.0),
    ("down", 0.0, -90.0),
)

# Current Static Street View metadata can return dots as well as the older
# URL-safe base64 alphabet.  Keep a conservative Windows-filename allow-list;
# never admit path separators, drive markers, wildcards, or control chars.
PANO_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
KEY_QUERY_RE = re.compile(r"([?&]key=)[^&\s]+", re.IGNORECASE)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_text(value: object, secrets: Iterable[str] = ()) -> str:
    text = KEY_QUERY_RE.sub(r"\1<redacted>", str(value))
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class TodoEntry:
    line_number: int
    raw: str
    latitude: float
    longitude: float
    altitude: float
    altitude_token: str
    source_heading: float
    source_tilt: float
    source_roll: float
    old_pano_id: str


@dataclass(frozen=True)
class PanoMetadata:
    pano_id: str
    latitude: float
    longitude: float
    date: Optional[str]
    copyright: Optional[str]


class ImportFailure(RuntimeError):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        code: str = "IMPORT_ERROR",
        http_status: Optional[int] = None,
        retryable: bool = False,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.attempts = attempts

    def as_report(self, secrets: Iterable[str] = ()) -> dict:
        return {
            "stage": self.stage,
            "code": self.code,
            "message": redact_text(str(self), secrets)[:2000],
            "http_status": self.http_status,
            "retryable": self.retryable,
            "attempts": self.attempts,
        }


def parse_todo_line(line: str, line_number: int) -> TodoEntry:
    value = line.strip()
    if not value:
        raise ValueError(f"todo.list line {line_number} is blank")
    fields = value.split("_", 6)
    if len(fields) != 7:
        raise ValueError(
            f"todo.list line {line_number} needs six numeric fields and a pano id"
        )
    try:
        numbers = [float(token) for token in fields[:6]]
    except ValueError as exc:
        raise ValueError(
            f"todo.list line {line_number} contains a non-numeric first-six field"
        ) from exc
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError(f"todo.list line {line_number} contains NaN or infinity")
    if not (-90.0 <= numbers[0] <= 90.0):
        raise ValueError(f"todo.list line {line_number} latitude is outside [-90, 90]")
    if not (-180.0 <= numbers[1] <= 180.0):
        raise ValueError(f"todo.list line {line_number} longitude is outside [-180, 180]")
    if not PANO_ID_RE.fullmatch(fields[6]):
        raise ValueError(f"todo.list line {line_number} has an unsafe pano id")
    return TodoEntry(
        line_number=line_number,
        raw=value,
        latitude=numbers[0],
        longitude=numbers[1],
        altitude=numbers[2],
        altitude_token=fields[2],
        source_heading=numbers[3],
        source_tilt=numbers[4],
        source_roll=numbers[5],
        old_pano_id=fields[6],
    )


def parse_todo(path: Path) -> List[TodoEntry]:
    entries: List[TodoEntry] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            entries.append(parse_todo_line(line, line_number))
    if not entries:
        raise ValueError(f"no panorama records found in {path}")
    return entries


def format_coordinate(value: float) -> str:
    result = f"{value:.14f}".rstrip("0").rstrip(".")
    return "0" if result in ("-0", "") else result


def format_angle(value: float) -> str:
    result = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if result in ("-0", "") else result


def heading_for_coordinate_mode(coordinate_mode: str) -> float:
    try:
        return COORDINATE_MODE_HEADINGS[coordinate_mode]
    except KeyError as exc:
        raise ValueError(
            "coordinate_mode must be one of: " + ", ".join(COORDINATE_MODE_HEADINGS)
        ) from exc


def build_output_name(
    entry: TodoEntry,
    metadata: PanoMetadata,
    coordinate_mode: str = DEFAULT_COORDINATE_MODE,
) -> str:
    if not PANO_ID_RE.fullmatch(metadata.pano_id):
        raise ImportFailure(
            "metadata",
            "Google returned a pano id that is unsafe for a ChordAtlas filename",
            code="INVALID_PANO_ID",
        )
    fields = (
        format_coordinate(metadata.latitude),
        format_coordinate(metadata.longitude),
        entry.altitude_token,
        format_angle(heading_for_coordinate_mode(coordinate_mode)),
        format_angle(FILENAME_TILT_DEG),
        format_angle(FILENAME_ROLL_DEG),
        metadata.pano_id,
    )
    return "_".join(fields) + ".jpg"


def chordatlas_loader_would_drop(tilt_deg: float, roll_deg: float) -> bool:
    """Model the original PanoGen invalid-orientation filter.

    This uses the mathematical intent of the Java code.  Tests separately lock
    the chosen compatibility values far enough away from its 1e-6-radian roll
    threshold.
    """
    rx = math.fmod(math.radians(tilt_deg) + 1.5 * math.pi, 2.0 * math.pi)
    rz = 2.0 * math.pi + math.radians(roll_deg)
    return abs(rx) < 1e-12 and abs(rz - 2.0 * math.pi) < 1e-6


class GoogleStreetViewClient:
    def __init__(
        self,
        api_key: str,
        *,
        radius_metres: int = 50,
        timeout_seconds: float = 30.0,
        retries: int = 2,
        opener: Callable[..., object] = urllib.request.urlopen,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError(f"{KEY_ENV} is empty")
        self._api_key = api_key.strip()
        self.radius_metres = radius_metres
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        if opener is urllib.request.urlopen:
            # Conda's OpenSSL build on Windows may not discover the Windows
            # certificate store (its compiled-in cert.pem path can be absent).
            # Prefer the already-installed certifi bundle without changing the
            # selected environment or disabling TLS verification.
            try:
                import certifi

                tls_context = ssl.create_default_context(cafile=certifi.where())
            except (ImportError, OSError, ssl.SSLError):
                tls_context = ssl.create_default_context()

            def verified_urlopen(request, timeout):
                return urllib.request.urlopen(
                    request, timeout=timeout, context=tls_context
                )

            self._opener = verified_urlopen
        else:
            self._opener = opener

    def _request_bytes(self, endpoint: str, params: Mapping[str, object], stage: str) -> bytes:
        query_params = dict(params)
        query_params["key"] = self._api_key
        # This complete URL is intentionally kept in a local variable and never
        # printed, returned, or included in an exception/report.
        url = endpoint + "?" + urllib.parse.urlencode(query_params)
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "ChordAtlas-Static-Pano-Importer/1.0"},
        )
        last_failure: Optional[ImportFailure] = None
        for attempt in range(1, self.retries + 2):
            try:
                response = self._opener(request, timeout=self.timeout_seconds)
                with response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                try:
                    body = exc.read(8192).decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                detail = _extract_error_detail(body)
                retryable = exc.code == 429 or 500 <= exc.code < 600
                last_failure = ImportFailure(
                    stage,
                    f"Google endpoint returned HTTP {exc.code}"
                    + (f": {detail}" if detail else ""),
                    code="HTTP_ERROR",
                    http_status=exc.code,
                    retryable=retryable,
                    attempts=attempt,
                )
            except urllib.error.URLError as exc:
                last_failure = ImportFailure(
                    stage,
                    "network request failed: "
                    + redact_text(getattr(exc, "reason", exc), (self._api_key,)),
                    code="NETWORK_ERROR",
                    retryable=True,
                    attempts=attempt,
                )
            except Exception as exc:
                last_failure = ImportFailure(
                    stage,
                    "request failed: " + redact_text(exc, (self._api_key,)),
                    code="REQUEST_ERROR",
                    retryable=False,
                    attempts=attempt,
                )
            if last_failure is None or not last_failure.retryable or attempt > self.retries:
                break
            time.sleep(min(2 ** (attempt - 1), 4))
        assert last_failure is not None
        raise last_failure

    def fetch_metadata(self, entry: TodoEntry) -> PanoMetadata:
        body = self._request_bytes(
            METADATA_ENDPOINT,
            {
                "location": f"{entry.latitude:.14f},{entry.longitude:.14f}",
                "radius": self.radius_metres,
            },
            "metadata",
        )
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise ImportFailure(
                "metadata",
                "Google metadata response was not valid UTF-8 JSON",
                code="INVALID_METADATA_JSON",
            ) from exc
        status = str(payload.get("status", "MISSING_STATUS"))
        if status != "OK":
            detail = redact_text(payload.get("error_message", ""), (self._api_key,))
            raise ImportFailure(
                "metadata",
                f"Google metadata status {status}" + (f": {detail}" if detail else ""),
                code=status,
                retryable=status in {"UNKNOWN_ERROR", "OVER_QUERY_LIMIT"},
            )
        try:
            pano_id = str(payload["pano_id"])
            location = payload["location"]
            latitude = float(location["lat"])
            longitude = float(location["lng"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportFailure(
                "metadata",
                "Google metadata response omitted pano_id or location.lat/lng",
                code="INCOMPLETE_METADATA",
            ) from exc
        if not PANO_ID_RE.fullmatch(pano_id):
            raise ImportFailure(
                "metadata", "Google returned an unsafe pano id", code="INVALID_PANO_ID"
            )
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ImportFailure(
                "metadata", "Google returned invalid panorama coordinates", code="INVALID_LOCATION"
            )
        return PanoMetadata(
            pano_id=pano_id,
            latitude=latitude,
            longitude=longitude,
            date=payload.get("date"),
            copyright=payload.get("copyright"),
        )

    def fetch_cube_face(self, pano_id: str, label: str, heading: float, pitch: float) -> Image.Image:
        body = self._request_bytes(
            IMAGE_ENDPOINT,
            {
                "size": f"{FACE_SIZE}x{FACE_SIZE}",
                "pano": pano_id,
                "heading": format_angle(heading),
                "pitch": format_angle(pitch),
                "fov": "90",
                "return_error_code": "true",
            },
            f"cube:{label}",
        )
        try:
            with Image.open(io.BytesIO(body)) as decoded:
                decoded.load()
                if decoded.size != (FACE_SIZE, FACE_SIZE):
                    raise ImportFailure(
                        f"cube:{label}",
                        f"decoded image is {decoded.width}x{decoded.height}; expected 640x640",
                        code="WRONG_CUBE_DIMENSIONS",
                    )
                return decoded.convert("RGB")
        except ImportFailure:
            raise
        except (UnidentifiedImageError, OSError) as exc:
            raise ImportFailure(
                f"cube:{label}",
                "response was not a decodable image",
                code="INVALID_CUBE_IMAGE",
            ) from exc


def _extract_error_detail(body: str) -> str:
    if not body:
        return ""
    try:
        payload = json.loads(body)
        detail = payload.get("error_message") or payload.get("error") or payload.get("status")
        if detail:
            return redact_text(detail)[:1000]
    except Exception:
        pass
    # Do not copy arbitrary HTML into the structured report.
    return ""


def equirect_uv_to_enu(u: float, v: float) -> Tuple[float, float, float]:
    """Return (east, up, north) for a ChordAtlas-compatible pano UV.

    u=0 is south, .25 west, .5 north, .75 east; v=0 is zenith and
    v=1 is nadir.  This is the inverse of Pano.castTo() for heading zero.
    """
    heading = math.pi + 2.0 * math.pi * u
    elevation = math.pi / 2.0 - math.pi * v
    horizontal = math.cos(elevation)
    return (
        math.sin(heading) * horizontal,
        math.sin(elevation),
        math.cos(heading) * horizontal,
    )


def cube_face_coordinates(east: float, up: float, north: float) -> Tuple[str, float, float]:
    """Select a cube face and return perspective coordinates in [-1, 1]."""
    ax, ay, az = abs(east), abs(up), abs(north)
    if ay >= ax and ay >= az:
        if up >= 0:
            denom = up or 1.0
            return "up", east / denom, -north / denom
        denom = -up or 1.0
        return "down", east / denom, north / denom
    if az >= ax:
        if north >= 0:
            denom = north or 1.0
            return "north", east / denom, up / denom
        denom = -north or 1.0
        return "south", -east / denom, up / denom
    if east >= 0:
        denom = east or 1.0
        return "east", -north / denom, up / denom
    denom = -east or 1.0
    return "west", north / denom, up / denom


def compose_equirectangular(
    faces: Mapping[str, Image.Image],
    width: int,
    *,
    bilinear: bool = True,
) -> Image.Image:
    if width < 8 or width % 2:
        raise ValueError("output width must be an even integer of at least 8")
    height = width // 2
    required = {spec[0] for spec in VIEW_SPECS}
    if set(faces) != required:
        raise ValueError(f"cube faces must be exactly {sorted(required)}")
    converted: Dict[str, Image.Image] = {}
    edge: Optional[int] = None
    for label in required:
        image = faces[label].convert("RGB")
        if image.width != image.height:
            raise ValueError(f"cube face {label} is not square")
        if edge is None:
            edge = image.width
        elif image.width != edge:
            raise ValueError("cube faces do not have identical dimensions")
        converted[label] = image
    assert edge is not None

    face_pixels = {label: list(image.getdata()) for label, image in converted.items()}
    target = bytearray(width * height * 3)
    sin_heading = []
    cos_heading = []
    for x in range(width):
        heading = math.pi + 2.0 * math.pi * ((x + 0.5) / width)
        sin_heading.append(math.sin(heading))
        cos_heading.append(math.cos(heading))

    max_index = edge - 1
    out_index = 0
    for y in range(height):
        elevation = math.pi / 2.0 - math.pi * ((y + 0.5) / height)
        horizontal = math.cos(elevation)
        direction_up = math.sin(elevation)
        for x in range(width):
            direction_east = sin_heading[x] * horizontal
            direction_north = cos_heading[x] * horizontal
            label, sx, sy = cube_face_coordinates(
                direction_east, direction_up, direction_north
            )
            sx = max(-1.0, min(1.0, sx))
            sy = max(-1.0, min(1.0, sy))
            fx = (sx + 1.0) * 0.5 * max_index
            fy = (1.0 - sy) * 0.5 * max_index
            pixels = face_pixels[label]
            if not bilinear:
                pixel = pixels[int(round(fy)) * edge + int(round(fx))]
                target[out_index : out_index + 3] = bytes(pixel)
            else:
                x0, y0 = int(fx), int(fy)
                x1, y1 = min(x0 + 1, max_index), min(y0 + 1, max_index)
                wx, wy = fx - x0, fy - y0
                p00 = pixels[y0 * edge + x0]
                p10 = pixels[y0 * edge + x1]
                p01 = pixels[y1 * edge + x0]
                p11 = pixels[y1 * edge + x1]
                for channel in range(3):
                    top = p00[channel] + (p10[channel] - p00[channel]) * wx
                    bottom = p01[channel] + (p11[channel] - p01[channel]) * wx
                    target[out_index + channel] = int(
                        round(top + (bottom - top) * wy)
                    )
            out_index += 3
    return Image.frombytes("RGB", (width, height), bytes(target))


def validate_jpeg(path: Path, expected_width: int) -> dict:
    try:
        with Image.open(path) as probe:
            image_format = probe.format
            probe.verify()
        with Image.open(path) as decoded:
            decoded.load()
            width, height = decoded.size
            mode = decoded.mode
    except (OSError, UnidentifiedImageError) as exc:
        raise ImportFailure(
            "validate", f"cannot decode output JPEG: {exc}", code="INVALID_OUTPUT_JPEG"
        ) from exc
    if image_format != "JPEG":
        raise ImportFailure(
            "validate", f"output format is {image_format}, not JPEG", code="WRONG_OUTPUT_FORMAT"
        )
    if (width, height) != (expected_width, expected_width // 2):
        raise ImportFailure(
            "validate",
            f"output is {width}x{height}; expected {expected_width}x{expected_width // 2}",
            code="WRONG_OUTPUT_DIMENSIONS",
        )
    if width != 2 * height:
        raise ImportFailure(
            "validate", f"output {width}x{height} is not exactly 2:1", code="NOT_EQUIRECTANGULAR"
        )
    if path.stat().st_size < 1024:
        raise ImportFailure("validate", "output JPEG is unexpectedly small", code="TRUNCATED_OUTPUT")
    return {
        "decoded": True,
        "format": image_format,
        "width": width,
        "height": height,
        "ratio": "2:1",
        "mode": mode,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def atomic_save_jpeg(image: Image.Image, destination: Path, quality: int) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.part"
    try:
        with temp_path.open("wb") as stream:
            image.save(stream, format="JPEG", quality=quality, subsampling=0, optimize=True)
            stream.flush()
            os.fsync(stream.fileno())
        validation = validate_jpeg(temp_path, image.width)
        os.replace(temp_path, destination)
        return validation
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.part"
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def invalidate_panos_cache(output_dir: Path) -> dict:
    cache = output_dir / "panos.xml"
    if not cache.exists():
        return {"status": "absent", "path": str(cache)}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = output_dir / f"panos.xml.pre-static-import.{stamp}.bak"
    counter = 1
    while backup.exists():
        backup = output_dir / f"panos.xml.pre-static-import.{stamp}.{counter}.bak"
        counter += 1
    os.replace(cache, backup)
    return {"status": "backed_up", "path": str(cache), "backup": str(backup)}


def _metadata_report(metadata: PanoMetadata) -> dict:
    return {
        "pano_id": metadata.pano_id,
        "latitude": metadata.latitude,
        "longitude": metadata.longitude,
        "date": metadata.date,
        "copyright": metadata.copyright,
    }


def _entry_report(entry: TodoEntry) -> dict:
    return {
        "line_number": entry.line_number,
        "input": {
            "latitude": entry.latitude,
            "longitude": entry.longitude,
            "altitude": entry.altitude,
            "source_heading": entry.source_heading,
            "source_tilt": entry.source_tilt,
            "source_roll": entry.source_roll,
            "old_pano_id": entry.old_pano_id,
        },
    }


def _report_config(
    width: int,
    radius: int,
    quality: int,
    bilinear: bool,
    coordinate_mode: str,
) -> dict:
    filename_heading = heading_for_coordinate_mode(coordinate_mode)
    return {
        "api_key_source": KEY_ENV,
        "api_key_recorded": False,
        "metadata_lookup": "location lat/lon",
        "metadata_radius_metres": radius,
        "cube_face_size": [FACE_SIZE, FACE_SIZE],
        "cube_views": [
            {"label": label, "heading": heading, "pitch": pitch, "fov": 90}
            for label, heading, pitch in VIEW_SPECS
        ],
        "output_width": width,
        "output_height": width // 2,
        "jpeg_quality": quality,
        "resampling": "bilinear" if bilinear else "nearest",
        "orientation_contract": ORIENTATION_CONTRACT,
        "coordinate_mode": coordinate_mode,
        "pixel_cardinals": {
            "x=0/1": "south",
            "x=0.25": "west",
            "x=0.5": "north",
            "x=0.75": "east",
            "y=0": "zenith",
            "y=0.5": "horizon",
            "y=1": "nadir",
        },
        "filename_orientation": {
            "heading": filename_heading,
            "tilt": FILENAME_TILT_DEG,
            "roll": FILENAME_ROLL_DEG,
            "roll_is_loader_compatibility_sentinel": True,
        },
    }


def run_import(
    *,
    entries: Sequence[TodoEntry],
    todo_path: Path,
    output_dir: Path,
    report_path: Path,
    client: Optional[GoogleStreetViewClient],
    dry_run: bool,
    mode: str,
    output_width: int,
    jpeg_quality: int,
    bilinear: bool,
    overwrite: bool,
    keep_panos_cache: bool,
    radius_metres: int,
    coordinate_mode: str = DEFAULT_COORDINATE_MODE,
    prevalidated_sample: Optional[dict] = None,
) -> dict:
    started = utc_now()
    report: dict = {
        "schema": "chordatlas-static-pano-report-v1",
        "mode": mode,
        "dry_run": dry_run,
        "started_utc": started,
        "finished_utc": None,
        "todo_path": str(todo_path.resolve()),
        "todo_sha256": sha256_file(todo_path),
        "todo_records_total": len(parse_todo(todo_path)),
        "selected_records": len(entries),
        "output_dir": str(output_dir.resolve()),
        "report_path": str(report_path.resolve()),
        "chordatlas_layer": {
            "layer_required": True,
            "created_by_importer": False,
            "instruction": (
                "If this workspace has no PanoGen layer, use GUI Layers '+' -> panos (jpg) "
                "and choose this output directory. The refresh panoramas button only rescans an existing layer."
            ),
        },
        "config": _report_config(
            output_width, radius_metres, jpeg_quality, bilinear, coordinate_mode
        ),
        "prevalidated_sample": prevalidated_sample,
        "items": [],
        "cache": {"status": "not_touched"},
        "summary": {"planned": len(entries), "succeeded": 0, "existing": 0, "failed": 0},
    }
    atomic_write_json(report_path, report)
    new_outputs = 0
    for position, entry in enumerate(entries, 1):
        item = _entry_report(entry)
        report["items"].append(item)
        if dry_run:
            item.update(
                {
                    "status": "planned",
                    "metadata": "would refresh pano_id using input latitude/longitude",
                    "cube_views": [label for label, _, _ in VIEW_SPECS],
                    "output_name_template": (
                        "lat_lon_alt_"
                        + format_angle(heading_for_coordinate_mode(coordinate_mode))
                        + "_90_0.001_refreshedPanoId.jpg"
                    ),
                }
            )
            atomic_write_json(report_path, report)
            continue
        assert client is not None
        stage = "metadata"
        print(f"[{position}/{len(entries)}] line {entry.line_number}: refreshing metadata")
        try:
            metadata = client.fetch_metadata(entry)
            item["metadata"] = _metadata_report(metadata)
            output_name = build_output_name(entry, metadata, coordinate_mode)
            output_path = output_dir / output_name
            item["output"] = str(output_path.resolve())
            if output_path.exists() and not overwrite:
                stage = "validate-existing"
                item["validation"] = validate_jpeg(output_path, output_width)
                item["status"] = "existing"
                report["summary"]["existing"] += 1
                print(f"[{position}/{len(entries)}] line {entry.line_number}: valid output already exists")
            else:
                faces: Dict[str, Image.Image] = {}
                for label, heading, pitch in VIEW_SPECS:
                    stage = f"cube:{label}"
                    print(
                        f"[{position}/{len(entries)}] line {entry.line_number}: "
                        f"downloading cube face {label}"
                    )
                    faces[label] = client.fetch_cube_face(metadata.pano_id, label, heading, pitch)
                stage = "cubemap-to-equirectangular"
                print(f"[{position}/{len(entries)}] line {entry.line_number}: projecting strict 2:1 JPEG")
                panorama = compose_equirectangular(faces, output_width, bilinear=bilinear)
                stage = "atomic-publish"
                item["validation"] = atomic_save_jpeg(panorama, output_path, jpeg_quality)
                # Re-open the published path, not just the temporary file.
                item["validation"] = validate_jpeg(output_path, output_width)
                item["status"] = "succeeded"
                report["summary"]["succeeded"] += 1
                new_outputs += 1
                print(f"[{position}/{len(entries)}] line {entry.line_number}: published {output_name}")
        except ImportFailure as exc:
            item["status"] = "failed"
            item["error"] = exc.as_report((getattr(client, "_api_key", ""),))
            report["summary"]["failed"] += 1
            print(
                f"[{position}/{len(entries)}] line {entry.line_number}: FAILED at "
                f"{item['error']['stage']}: {item['error']['message']}",
                file=sys.stderr,
            )
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = {
                "stage": stage,
                "code": "UNEXPECTED_ERROR",
                "message": redact_text(exc, (getattr(client, "_api_key", ""),))[:2000],
                "http_status": None,
                "retryable": False,
                "attempts": 1,
            }
            report["summary"]["failed"] += 1
            print(
                f"[{position}/{len(entries)}] line {entry.line_number}: FAILED at {stage}: "
                f"{item['error']['message']}",
                file=sys.stderr,
            )
        atomic_write_json(report_path, report)

    if dry_run:
        report["summary"]["planned"] = len(entries)
    elif new_outputs and not keep_panos_cache:
        try:
            report["cache"] = invalidate_panos_cache(output_dir)
        except Exception as exc:
            report["cache"] = {
                "status": "failed",
                "message": redact_text(exc, (getattr(client, "_api_key", ""),)),
            }
            report["summary"]["failed"] += 1
    report["finished_utc"] = utc_now()
    atomic_write_json(report_path, report)
    return report


def verify_sample_report(
    path: Path,
    todo_path: Path,
    output_width: int,
    output_dir: Optional[Path] = None,
    coordinate_mode: str = DEFAULT_COORDINATE_MODE,
) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot read sample report {path}: {exc}") from exc
    if payload.get("schema") != "chordatlas-static-pano-report-v1":
        raise ValueError("sample report has the wrong schema")
    if payload.get("mode") != "sample" or payload.get("dry_run"):
        raise ValueError("sample report is not a completed live one-sample run")
    if payload.get("todo_sha256") != sha256_file(todo_path):
        raise ValueError("todo.list changed after the sample run")
    config = payload.get("config") or {}
    if config.get("orientation_contract") != ORIENTATION_CONTRACT:
        raise ValueError("sample report used a different orientation contract")
    if config.get("coordinate_mode") != coordinate_mode:
        raise ValueError("sample report used a different coordinate mode")
    if config.get("filename_orientation", {}).get("heading") != heading_for_coordinate_mode(
        coordinate_mode
    ):
        raise ValueError("sample report used a different filename heading")
    if config.get("output_width") != output_width:
        raise ValueError("sample report used a different output width")
    items = payload.get("items") or []
    successful = [item for item in items if item.get("status") in {"succeeded", "existing"}]
    if len(items) != 1 or len(successful) != 1 or payload.get("summary", {}).get("failed"):
        raise ValueError("sample report does not contain exactly one successful panorama")
    output_path = Path(successful[0]["output"]).resolve()
    if output_dir is not None:
        expected_output_dir = output_dir.resolve()
        try:
            reported_output_dir = Path(payload["output_dir"]).resolve()
        except (KeyError, TypeError, OSError) as exc:
            raise ValueError("sample report has no valid output directory") from exc
        if reported_output_dir != expected_output_dir:
            raise ValueError("sample report belongs to a different panorama output directory")
        if output_path.parent != expected_output_dir:
            raise ValueError("sample JPEG is outside the selected panorama output directory")
    validation = validate_jpeg(output_path, output_width)
    if validation["sha256"] != successful[0].get("validation", {}).get("sha256"):
        raise ValueError("sample JPEG changed after validation")
    return {
        "report": str(path.resolve()),
        "line_number": successful[0]["line_number"],
        "output": str(output_path.resolve()),
        "sha256": validation["sha256"],
    }


def add_cli_arguments(parser: argparse.ArgumentParser, *, output_required: bool = True) -> None:
    """Add the importer options shared by the standalone and myProject CLIs."""

    parser.add_argument("--todo", type=Path, required=True, help="ChordAtlas todo.list manifest")
    parser.add_argument(
        "--output",
        "--output-dir",
        dest="output_dir",
        type=Path,
        required=output_required,
        help=(
            "panorama directory; the myProject CLI defaults to <configured workspace>/panos"
            if not output_required
            else "panorama output directory"
        ),
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument("--limit", type=int, default=1, help="safety default is exactly one panorama")
    parser.add_argument("--all", action="store_true", help="process remaining records after sample approval")
    parser.add_argument("--sample-report", type=Path)
    parser.add_argument(
        "--sample-approved",
        action="store_true",
        help="confirm that the validated sample was also visually inspected",
    )
    parser.add_argument("--dry-run", action="store_true", help="perform no API calls and require no key")
    parser.add_argument("--output-width", type=int, default=DEFAULT_OUTPUT_WIDTH)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--radius", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--nearest", action="store_true", help="debug-only nearest-neighbour cube sampling")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--keep-panos-cache", action="store_true")
    parser.add_argument(
        "--coordinate-mode",
        choices=tuple(COORDINATE_MODE_HEADINGS),
        default=DEFAULT_COORDINATE_MODE,
        help="myproject-local uses heading 180; original-geographic uses heading 0",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Static Street View cube faces and publish ChordAtlas 2:1 panoramas."
    )
    add_cli_arguments(parser)
    return parser


def import_streetview_panos(
    *,
    todo_path: Path,
    output_dir: Path,
    report_path: Optional[Path] = None,
    limit: int = 1,
    all_records: bool = False,
    sample_report: Optional[Path] = None,
    sample_approved: bool = False,
    dry_run: bool = False,
    output_width: int = DEFAULT_OUTPUT_WIDTH,
    jpeg_quality: int = 95,
    radius: int = 50,
    timeout: float = 30.0,
    retries: int = 2,
    nearest: bool = False,
    overwrite: bool = False,
    keep_panos_cache: bool = False,
    coordinate_mode: str = DEFAULT_COORDINATE_MODE,
) -> dict:
    """Run a guarded import; live batch mode requires a verified sample report."""

    todo_path = todo_path.resolve()
    output_dir = output_dir.resolve()
    if not todo_path.is_file():
        raise ValueError(f"todo.list not found: {todo_path}")
    if output_width < 8 or output_width % 2:
        raise ValueError("--output-width must be an even integer of at least 8")
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("--jpeg-quality must be between 1 and 100")
    if radius < 1:
        raise ValueError("--radius must be positive")
    if retries < 0:
        raise ValueError("--retries cannot be negative")
    if timeout <= 0:
        raise ValueError("--timeout must be positive")
    heading_for_coordinate_mode(coordinate_mode)
    entries = parse_todo(todo_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    prevalidated_sample = None
    if all_records:
        mode = "batch"
        if dry_run:
            selected = entries
        else:
            if not sample_approved:
                raise ValueError(
                    "batch is locked: visually inspect the one-sample JPEG, then pass --sample-approved"
                )
            verified_report = (
                sample_report.resolve()
                if sample_report
                else output_dir / "streetview_sample_report.json"
            )
            prevalidated_sample = verify_sample_report(
                verified_report, todo_path, output_width, output_dir, coordinate_mode
            )
            selected = [
                entry
                for entry in entries
                if entry.line_number != prevalidated_sample["line_number"]
            ]
    else:
        mode = "dry-run" if dry_run else "sample"
        if limit != 1 and not dry_run:
            raise ValueError(
                "live runs are limited to one until validated; use --all with an approved sample report"
            )
        if limit < 1:
            raise ValueError("--limit must be positive")
        selected = entries[:limit]

    if report_path:
        selected_report = report_path.resolve()
    elif mode == "sample":
        selected_report = output_dir / "streetview_sample_report.json"
    elif mode == "batch":
        selected_report = output_dir / "streetview_batch_report.json"
    else:
        selected_report = output_dir / "streetview_dry_run_report.json"

    client = None
    if not dry_run:
        api_key = os.environ.get(KEY_ENV)
        if not api_key:
            raise ValueError(
                f"{KEY_ENV} is not set; inject it into this process environment, never source code"
            )
        client = GoogleStreetViewClient(
            api_key,
            radius_metres=radius,
            timeout_seconds=timeout,
            retries=retries,
        )

    return run_import(
        entries=selected,
        todo_path=todo_path,
        output_dir=output_dir,
        report_path=selected_report,
        client=client,
        dry_run=dry_run,
        mode=mode,
        output_width=output_width,
        jpeg_quality=jpeg_quality,
        bilinear=not nearest,
        overwrite=overwrite,
        keep_panos_cache=keep_panos_cache,
        radius_metres=radius,
        coordinate_mode=coordinate_mode,
        prevalidated_sample=prevalidated_sample,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = import_streetview_panos(
            todo_path=args.todo,
            output_dir=args.output_dir,
            report_path=args.report,
            limit=args.limit,
            all_records=args.all,
            sample_report=args.sample_report,
            sample_approved=args.sample_approved,
            dry_run=args.dry_run,
            output_width=args.output_width,
            jpeg_quality=args.jpeg_quality,
            radius=args.radius,
            timeout=args.timeout,
            retries=args.retries,
            nearest=args.nearest,
            overwrite=args.overwrite,
            keep_panos_cache=args.keep_panos_cache,
            coordinate_mode=args.coordinate_mode,
        )
        print(
            f"report: {report['report_path']}\n"
            f"summary: succeeded={report['summary']['succeeded']}, "
            f"existing={report['summary']['existing']}, failed={report['summary']['failed']}"
        )
        return 2 if report["summary"]["failed"] else 0
    except (ValueError, OSError, ImportFailure) as exc:
        # No secret is in scope here except possibly an environment value; redact
        # it defensively before presenting a configuration/fatal error.
        print(redact_text(exc, (os.environ.get(KEY_ENV, ""),)), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
