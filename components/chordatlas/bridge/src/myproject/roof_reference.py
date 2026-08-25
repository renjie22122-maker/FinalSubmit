"""Build per-building roof style references from cached satellite tiles.

The module is deliberately offline and geometry-neutral.  It consumes the
exact Web-Mercator tile manifest already written by :mod:`myproject.selection`,
never downloads a tile, and only writes appearance artefacts below a building's
``references/roof`` directory.  ``reference.json`` is written last and acts as
the commit marker for a usable reference.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
from typing import Any, Iterable, Mapping, Sequence
import uuid
import zlib

from .geo import LocalFrame


WEB_MERCATOR_RADIUS_M = 6_378_137.0
WEB_MERCATOR_HALF_WORLD_M = math.pi * WEB_MERCATOR_RADIUS_M
WEB_MERCATOR_WORLD_M = 2.0 * WEB_MERCATOR_HALF_WORLD_M
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
REFERENCE_SCHEMA_VERSION = 1
GENERATOR_VERSION = "roof-reference-v1"
_OUTPUT_NAMES = {
    "satellite_north_up": "satellite_north_up.png",
    "source_valid_mask": "source_valid_mask.png",
    "footprint_mask": "footprint_mask.png",
    "roof_style_mask": "roof_style_mask.png",
    "roof_reference": "roof_reference.png",
    "roof_reference_rgba": "roof_reference_rgba.png",
}
_OUTPUT_FILENAMES = frozenset(_OUTPUT_NAMES.values())


@dataclass(frozen=True)
class RoofReferenceFootprint:
    identifier: str
    points: tuple[tuple[float, float], ...]


@dataclass
class _Raster:
    width: int
    height: int
    rgba: bytes


@dataclass
class _SourceTile:
    tile_id: str
    stem: str
    path: Path
    bounds: tuple[float, float, float, float]
    size_px: int
    zoom: int
    sha256: str | None = None
    image: _Raster | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_bytes(path, payload)


def _remove_path(path: Path) -> None:
    """Remove only an exact generated staging/backup path, never a symlink target."""

    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _new_staging_root(destination_root: Path) -> Path:
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    staging = destination_root.parent / f".{destination_root.name}.staging-{uuid.uuid4().hex}"
    staging.mkdir()
    return staging


def _commit_reference_directory(staging: Path, destination_root: Path) -> None:
    """Install one complete reference directory, rolling back a failed swap."""

    staging_parent = staging.parent.resolve(strict=True)
    destination_parent = destination_root.parent.resolve(strict=True)
    if staging_parent != destination_parent or not staging.is_dir() or staging.is_symlink():
        raise ValueError("roof reference staging directory is outside its destination parent")
    backup = destination_root.parent / f".{destination_root.name}.backup-{uuid.uuid4().hex}"
    moved_old = False
    try:
        if destination_root.exists() or destination_root.is_symlink():
            os.replace(destination_root, backup)
            moved_old = True
        os.replace(staging, destination_root)
    except BaseException:
        if moved_old and not (destination_root.exists() or destination_root.is_symlink()):
            os.replace(backup, destination_root)
        raise
    else:
        if moved_old:
            try:
                _remove_path(backup)
            except OSError:
                pass


def _safe_output_paths(root: Path, outputs: Any) -> dict[str, Path] | None:
    """Validate the fixed output contract and resolve every path below ``root``."""

    if not isinstance(outputs, Mapping) or set(outputs) != set(_OUTPUT_NAMES):
        return None
    resolved_root = root.resolve(strict=False)
    paths: dict[str, Path] = {}
    for key, expected_name in _OUTPUT_NAMES.items():
        name = outputs.get(key)
        if (
            not isinstance(name, str)
            or name != expected_name
            or name not in _OUTPUT_FILENAMES
            or Path(name).is_absolute()
            or Path(name).name != name
        ):
            return None
        candidate = (root / name).resolve(strict=False)
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            return None
        paths[key] = candidate
    return paths


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _encode_png(width: int, height: int, color_type: int, pixels: bytes) -> bytes:
    channels = {0: 1, 2: 3, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"unsupported output PNG colour type: {color_type}")
    expected = width * height * channels
    if width <= 0 or height <= 0 or len(pixels) != expected:
        raise ValueError("invalid output PNG dimensions or pixel length")
    stride = width * channels
    scanlines = b"".join(
        b"\x00" + pixels[offset : offset + stride]
        for offset in range(0, len(pixels), stride)
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(scanlines, 6))
        + _png_chunk(b"IEND", b"")
    )


def _paeth(a: int, b: int, c: int) -> int:
    estimate = a + b - c
    distance_a = abs(estimate - a)
    distance_b = abs(estimate - b)
    distance_c = abs(estimate - c)
    if distance_a <= distance_b and distance_a <= distance_c:
        return a
    if distance_b <= distance_c:
        return b
    return c


def _decode_png(path: Path) -> _Raster:
    """Decode non-interlaced 8-bit PNGs, including Google's indexed PNG8."""

    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    offset = len(PNG_SIGNATURE)
    width = height = bit_depth = color_type = interlace = None
    palette: bytes | None = None
    transparency: bytes | None = None
    compressed: list[bytes] = []
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("truncated PNG payload")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError("PNG CRC mismatch")
        if kind == b"IHDR":
            if length != 13 or width is not None:
                raise ValueError("invalid PNG IHDR")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if compression != 0 or filtering != 0:
                raise ValueError("unsupported PNG compression or filter method")
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            compressed.append(payload)
        elif kind == b"IEND":
            saw_iend = True
            break
        offset = end
    if None in (width, height, bit_depth, color_type, interlace) or not compressed or not saw_iend:
        raise ValueError("incomplete PNG")
    if bit_depth != 8 or interlace != 0:
        raise ValueError("only non-interlaced 8-bit PNGs are supported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(int(color_type))
    if channels is None:
        raise ValueError(f"unsupported PNG colour type: {color_type}")
    if color_type == 3 and (not palette or len(palette) % 3):
        raise ValueError("indexed PNG has no valid palette")
    stride = int(width) * channels
    raw = zlib.decompress(b"".join(compressed))
    if len(raw) != (stride + 1) * int(height):
        raise ValueError("PNG scanline length does not match IHDR")
    rows: list[bytes] = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(int(height)):
        filter_type = raw[cursor]
        cursor += 1
        encoded = raw[cursor : cursor + stride]
        cursor += stride
        row = bytearray(stride)
        for index, value in enumerate(encoded):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth(left, above, upper_left)
            else:
                raise ValueError(f"unsupported PNG row filter: {filter_type}")
            row[index] = (value + predictor) & 0xFF
        rows.append(bytes(row))
        previous = row

    rgba = bytearray(int(width) * int(height) * 4)
    output = 0
    for row in rows:
        for column in range(int(width)):
            source = column * channels
            if color_type == 0:
                red = green = blue = row[source]
                alpha = 255
            elif color_type == 2:
                red, green, blue = row[source : source + 3]
                alpha = 255
            elif color_type == 3:
                palette_index = row[source]
                palette_offset = palette_index * 3
                if palette_offset + 3 > len(palette or b""):
                    raise ValueError("PNG palette index is out of range")
                red, green, blue = (palette or b"")[palette_offset : palette_offset + 3]
                alpha = transparency[palette_index] if transparency and palette_index < len(transparency) else 255
            elif color_type == 4:
                red = green = blue = row[source]
                alpha = row[source + 1]
            else:
                red, green, blue, alpha = row[source : source + 4]
            rgba[output : output + 4] = bytes((red, green, blue, alpha))
            output += 4
    return _Raster(int(width), int(height), bytes(rgba))


def _lonlat_to_web_mercator(lon: float, lat: float) -> tuple[float, float]:
    limited_lat = max(-85.05112878, min(85.05112878, float(lat)))
    return (
        WEB_MERCATOR_RADIUS_M * math.radians(float(lon)),
        WEB_MERCATOR_RADIUS_M
        * math.log(math.tan(math.pi / 4.0 + math.radians(limited_lat) / 2.0)),
    )


def _normalise_footprint(value: Any) -> RoofReferenceFootprint:
    if isinstance(value, RoofReferenceFootprint):
        return value
    if isinstance(value, Mapping):
        identifier = value.get("id", value.get("identifier"))
        points = value.get("points")
    else:
        identifier = getattr(value, "identifier", None)
        points = getattr(value, "points", None)
    if not isinstance(identifier, str) or not identifier or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
        for character in identifier
    ):
        raise ValueError("roof footprint identifier is not path-safe")
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise ValueError(f"roof footprint {identifier!r} needs at least three points")
    normalised: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"roof footprint {identifier!r} contains an invalid point")
        x, z = float(point[0]), float(point[1])
        if not math.isfinite(x) or not math.isfinite(z):
            raise ValueError(f"roof footprint {identifier!r} contains a non-finite point")
        normalised.append((x, z))
    if len(normalised) > 1 and normalised[0] == normalised[-1]:
        normalised.pop()
    return RoofReferenceFootprint(identifier, tuple(normalised))


def _point_on_segment(point, first, second, epsilon: float = 1e-7) -> bool:
    px, py = point
    ax, ay = first
    bx, by = second
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > epsilon:
        return False
    return min(ax, bx) - epsilon <= px <= max(ax, bx) + epsilon and min(
        ay, by
    ) - epsilon <= py <= max(ay, by) + epsilon


def _point_in_polygon(point, polygon: Sequence[tuple[float, float]]) -> bool:
    inside = False
    x, y = point
    for first, second in zip(polygon, polygon[1:] + polygon[:1]):
        if _point_on_segment(point, first, second):
            return True
        x1, y1 = first
        x2, y2 = second
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
    return inside


def _point_segment_distance(point, first, second) -> float:
    px, py = point
    ax, ay = first
    bx, by = second
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.hypot(px - ax, py - ay)
    fraction = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared))
    return math.hypot(px - (ax + fraction * dx), py - (ay + fraction * dy))


def _bbox_intersects(first, second) -> bool:
    return not (
        first[2] <= second[0]
        or second[2] <= first[0]
        or first[3] <= second[1]
        or second[3] <= first[1]
    )


def _load_tiles(document: Mapping[str, Any]) -> list[_SourceTile]:
    raw_tiles = document.get("tiles")
    if not isinstance(raw_tiles, list):
        raise ValueError("tile manifest has no tiles array")
    tiles: list[_SourceTile] = []
    for index, raw in enumerate(raw_tiles):
        if not isinstance(raw, Mapping):
            raise ValueError(f"tiles[{index}] is not an object")
        bounds_raw = raw.get("bounds_mercator_m")
        if not isinstance(bounds_raw, list) or len(bounds_raw) != 4:
            raise ValueError(f"tiles[{index}] has invalid bounds_mercator_m")
        bounds = tuple(float(item) for item in bounds_raw)
        if not all(math.isfinite(item) for item in bounds) or not (
            bounds[0] < bounds[2] and bounds[1] < bounds[3]
        ):
            raise ValueError(f"tiles[{index}] has invalid Web-Mercator bounds")
        size_px = int(raw.get("size_px", 0))
        zoom = int(raw.get("zoom", document.get("plan", {}).get("zoom", 0)))
        source_path = raw.get("satellite_path")
        if size_px <= 0 or zoom < 0 or not isinstance(source_path, str):
            raise ValueError(f"tiles[{index}] has incomplete raster metadata")
        tiles.append(
            _SourceTile(
                tile_id=str(raw.get("tile_id", f"tile-{index}")),
                stem=str(raw.get("stem", Path(source_path).stem)),
                path=Path(source_path).expanduser().resolve(strict=False),
                bounds=bounds,
                size_px=size_px,
                zoom=zoom,
            )
        )
    return tiles


def _failure_document(
    footprint: RoofReferenceFootprint,
    *,
    code: str,
    message: str,
    fingerprint: str,
    frame: LocalFrame,
    source_tile_manifest: Path,
    source_tiles: Sequence[Mapping[str, Any]] = (),
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "kind": "myProject.appearance.roof_reference",
        "generator_version": GENERATOR_VERSION,
        "status": "UNAVAILABLE",
        "building_id": footprint.identifier,
        "input_fingerprint": fingerprint,
        "source": "cached_satellite_tiles",
        "source_tile_manifest": str(source_tile_manifest),
        "source_tiles": list(source_tiles),
        "workspace_frame": {
            "origin_lat": frame.origin_lat,
            "origin_lon": frame.origin_lon,
            "axes": {"x": "east", "z": "south"},
            "units": "m",
        },
        "footprint_local_xz": [list(point) for point in footprint.points],
        "error": {"code": code, "message": message, "details": dict(details or {})},
        "fallback": {
            "policy": "preserve_existing_roof_geometry_and_texture_material_pipeline",
            "geometry_modified": False,
        },
        "limitations": [
            "The footprint mask is not a learned roof segmentation.",
            "No building-height parallax or occlusion correction is applied.",
        ],
    }


def _cache_document(root: Path, fingerprint: str) -> dict[str, Any] | None:
    manifest_path = root / "reference.json"
    if not manifest_path.is_file():
        return None
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or document.get("input_fingerprint") != fingerprint:
        return None
    if document.get("status") == "READY":
        outputs = document.get("outputs")
        output_paths = _safe_output_paths(root, outputs)
        if output_paths is None or any(not path.is_file() for path in output_paths.values()):
            return None
        hashes = document.get("output_sha256")
        if not isinstance(hashes, Mapping) or set(hashes) != set(_OUTPUT_NAMES):
            return None
        for key, path in output_paths.items():
            expected = hashes.get(key)
            if not isinstance(expected, str) or len(expected) != 64 or any(
                character not in "0123456789abcdef" for character in expected
            ):
                return None
            try:
                if _sha256_file(path) != expected:
                    return None
            except OSError:
                return None
    elif document.get("status") != "UNAVAILABLE":
        return None
    elif document.get("outputs") not in (None, {}):
        return None
    return document


def _copy_cached_reference(source_root: Path, destination_root: Path, document: Mapping[str, Any]) -> bool:
    staging: Path | None = None
    try:
        status = document.get("status")
        output_paths = (
            _safe_output_paths(source_root, document.get("outputs")) if status == "READY" else {}
        )
        if output_paths is None:
            return False
        staging = _new_staging_root(destination_root)
        for key, source in output_paths.items():
            shutil.copy2(source, staging / _OUTPUT_NAMES[key])
        # The marker is copied last, then the complete directory is revalidated.
        shutil.copy2(source_root / "reference.json", staging / "reference.json")
        fingerprint = document.get("input_fingerprint")
        if not isinstance(fingerprint, str) or _cache_document(staging, fingerprint) is None:
            return False
        _commit_reference_directory(staging, destination_root)
        staging = None
        return True
    except OSError:
        return False
    finally:
        if staging is not None:
            try:
                _remove_path(staging)
            except OSError:
                pass


def _install_generated_reference(
    destination_root: Path,
    encoded_outputs: Mapping[str, bytes],
    document: Mapping[str, Any],
) -> None:
    """Generate and validate a complete sibling directory before committing it."""

    status = document.get("status")
    if status == "READY" and set(encoded_outputs) != set(_OUTPUT_NAMES):
        raise ValueError("READY roof reference does not contain the fixed output set")
    if status == "UNAVAILABLE" and encoded_outputs:
        raise ValueError("UNAVAILABLE roof reference must not publish image outputs")
    staging = _new_staging_root(destination_root)
    try:
        for key in _OUTPUT_NAMES:
            if key in encoded_outputs:
                _atomic_bytes(staging / _OUTPUT_NAMES[key], encoded_outputs[key])
        # reference.json is deliberately the final staging write and commit marker.
        _atomic_json(staging / "reference.json", document)
        fingerprint = document.get("input_fingerprint")
        if not isinstance(fingerprint, str) or _cache_document(staging, fingerprint) is None:
            raise ValueError("staged roof reference failed commit validation")
        _commit_reference_directory(staging, destination_root)
        staging = None
    finally:
        if staging is not None:
            try:
                _remove_path(staging)
            except OSError:
                pass


def _sample_tile(tile: _SourceTile, x: float, y: float) -> tuple[tuple[int, int, int], int] | None:
    if tile.image is None:
        return None
    west, south, east, north = tile.bounds
    if not (west <= x < east and south < y <= north):
        return None
    column = int((x - west) * tile.image.width / (east - west))
    row = int((north - y) * tile.image.height / (north - south))
    column = min(tile.image.width - 1, max(0, column))
    row = min(tile.image.height - 1, max(0, row))
    offset = (row * tile.image.width + column) * 4
    red, green, blue, alpha = tile.image.rgba[offset : offset + 4]
    if alpha == 0:
        return None
    edge_margin = min(column, tile.image.width - 1 - column, row, tile.image.height - 1 - row)
    return (red, green, blue), edge_margin


def _median_colour(rgb: bytes, mask: bytes) -> tuple[int, int, int]:
    channels = ([], [], [])
    for index, enabled in enumerate(mask):
        if enabled:
            offset = index * 3
            channels[0].append(rgb[offset])
            channels[1].append(rgb[offset + 1])
            channels[2].append(rgb[offset + 2])
    if not channels[0]:
        return (127, 127, 127)
    result = []
    for values in channels:
        values.sort()
        result.append(values[(len(values) - 1) // 2])
    return tuple(result)  # type: ignore[return-value]


def _build_one_reference(
    footprint: RoofReferenceFootprint,
    *,
    frame: LocalFrame,
    tile_manifest_path: Path,
    tile_manifest_sha256: str,
    tiles: Sequence[_SourceTile],
    destination_root: Path,
    cache_root: Path | None,
    padding_m: float,
    erosion_m: float,
    minimum_source_coverage: float,
    minimum_mask_pixels: int,
    max_dimension_px: int,
    hash_cache: dict[Path, str],
    image_cache: dict[Path, _Raster | Exception],
) -> dict[str, Any]:
    wgs84 = [frame.to_wgs84(x, z) for x, z in footprint.points]
    mercator = [_lonlat_to_web_mercator(lon, lat) for lon, lat in wgs84]
    center_lat = sum(point[1] for point in wgs84) / len(wgs84)
    scale = 1.0 / max(1e-9, math.cos(math.radians(center_lat)))
    padding_mercator = padding_m * scale
    erosion_mercator = erosion_m * scale
    xs = [point[0] for point in mercator]
    ys = [point[1] for point in mercator]
    footprint_bbox = (min(xs), min(ys), max(xs), max(ys))
    expanded_bbox = (
        footprint_bbox[0] - padding_mercator,
        footprint_bbox[1] - padding_mercator,
        footprint_bbox[2] + padding_mercator,
        footprint_bbox[3] + padding_mercator,
    )
    selected = [tile for tile in tiles if _bbox_intersects(tile.bounds, expanded_bbox)]
    source_records: list[dict[str, Any]] = []
    for tile in selected:
        if tile.path.is_file():
            try:
                if tile.path not in hash_cache:
                    hash_cache[tile.path] = _sha256_file(tile.path)
                tile.sha256 = hash_cache[tile.path]
            except OSError:
                tile.sha256 = None
        source_records.append(
            {
                "tile_id": tile.tile_id,
                "stem": tile.stem,
                "path": str(tile.path),
                "sha256": tile.sha256,
                "bounds_mercator_m": list(tile.bounds),
                "size_px": tile.size_px,
                "zoom": tile.zoom,
            }
        )
    parameters = {
        "padding_m": padding_m,
        "erosion_m": erosion_m,
        "minimum_source_coverage": minimum_source_coverage,
        "minimum_mask_pixels": minimum_mask_pixels,
        "max_dimension_px": max_dimension_px,
    }
    fingerprint = _json_hash(
        {
            "generator_version": GENERATOR_VERSION,
            "tile_manifest_sha256": tile_manifest_sha256,
            "building_id": footprint.identifier,
            "footprint_local_xz": footprint.points,
            "frame": [frame.origin_lat, frame.origin_lon],
            "parameters": parameters,
            "sources": source_records,
        }
    )
    cached = _cache_document(destination_root, fingerprint)
    if cached is not None:
        output = dict(cached)
        output["cache_hit"] = True
        return output
    if cache_root is not None and cache_root.resolve(strict=False) != destination_root.resolve(strict=False):
        reusable = _cache_document(cache_root, fingerprint)
        if reusable is not None and _copy_cached_reference(cache_root, destination_root, reusable):
            output = dict(reusable)
            output["cache_hit"] = True
            output["cache_source"] = "previous_publication"
            return output

    def unavailable(code: str, message: str, details: Mapping[str, Any] | None = None):
        document = _failure_document(
            footprint,
            code=code,
            message=message,
            fingerprint=fingerprint,
            frame=frame,
            source_tile_manifest=tile_manifest_path,
            source_tiles=source_records,
            details=details,
        )
        _install_generated_reference(destination_root, {}, document)
        return document

    if not selected:
        return unavailable("no_intersecting_satellite_tile", "no cached tile intersects the roof crop")

    decoded: list[_SourceTile] = []
    decode_failures: list[dict[str, str]] = []
    for tile in selected:
        if not tile.path.is_file():
            decode_failures.append({"tile_id": tile.tile_id, "code": "missing", "path": str(tile.path)})
            continue
        cached_image = image_cache.get(tile.path)
        if cached_image is None:
            try:
                cached_image = _decode_png(tile.path)
            except Exception as exc:  # corrupt cache is appearance fallback, never geometry failure
                cached_image = exc
            image_cache[tile.path] = cached_image
        if isinstance(cached_image, Exception):
            decode_failures.append(
                {"tile_id": tile.tile_id, "code": "decode_failed", "message": str(cached_image)}
            )
            continue
        if cached_image.width != tile.size_px or cached_image.height != tile.size_px:
            decode_failures.append(
                {
                    "tile_id": tile.tile_id,
                    "code": "size_mismatch",
                    "message": f"expected {tile.size_px}x{tile.size_px}, got {cached_image.width}x{cached_image.height}",
                }
            )
            continue
        tile.image = cached_image
        decoded.append(tile)
    if not decoded:
        return unavailable(
            "satellite_decode_failed",
            "none of the intersecting cached satellite PNGs could be decoded",
            {"failures": decode_failures},
        )

    source_gsd = min((tile.bounds[2] - tile.bounds[0]) / tile.size_px for tile in decoded)
    col0 = math.floor((expanded_bbox[0] + WEB_MERCATOR_HALF_WORLD_M) / source_gsd)
    col1 = math.ceil((expanded_bbox[2] + WEB_MERCATOR_HALF_WORLD_M) / source_gsd)
    row0 = math.floor((WEB_MERCATOR_HALF_WORLD_M - expanded_bbox[3]) / source_gsd)
    row1 = math.ceil((WEB_MERCATOR_HALF_WORLD_M - expanded_bbox[1]) / source_gsd)
    raw_width, raw_height = max(1, col1 - col0), max(1, row1 - row0)
    decimation = max(1, math.ceil(max(raw_width, raw_height) / max_dimension_px))
    gsd = source_gsd * decimation
    col0 = math.floor((expanded_bbox[0] + WEB_MERCATOR_HALF_WORLD_M) / gsd)
    col1 = math.ceil((expanded_bbox[2] + WEB_MERCATOR_HALF_WORLD_M) / gsd)
    row0 = math.floor((WEB_MERCATOR_HALF_WORLD_M - expanded_bbox[3]) / gsd)
    row1 = math.ceil((WEB_MERCATOR_HALF_WORLD_M - expanded_bbox[1]) / gsd)
    width, height = max(1, col1 - col0), max(1, row1 - row0)
    west = -WEB_MERCATOR_HALF_WORLD_M + col0 * gsd
    east = -WEB_MERCATOR_HALF_WORLD_M + col1 * gsd
    north = WEB_MERCATOR_HALF_WORLD_M - row0 * gsd
    south = WEB_MERCATOR_HALF_WORLD_M - row1 * gsd

    rgb = bytearray(width * height * 3)
    valid = bytearray(width * height)
    footprint_mask = bytearray(width * height)
    style_mask = bytearray(width * height)
    edges = list(zip(mercator, mercator[1:] + mercator[:1]))
    footprint_pixels = valid_footprint_pixels = 0
    for row in range(height):
        y = north - (row + 0.5) * gsd
        for column in range(width):
            x = west + (column + 0.5) * gsd
            pixel_index = row * width + column
            inside = _point_in_polygon((x, y), mercator)
            if inside:
                footprint_mask[pixel_index] = 255
                footprint_pixels += 1
            best_colour: tuple[int, int, int] | None = None
            best_margin = -1
            for tile in decoded:
                sample = _sample_tile(tile, x, y)
                if sample is not None and sample[1] > best_margin:
                    best_colour, best_margin = sample
            if best_colour is None:
                continue
            valid[pixel_index] = 255
            rgb_offset = pixel_index * 3
            rgb[rgb_offset : rgb_offset + 3] = bytes(best_colour)
            if inside:
                valid_footprint_pixels += 1
                if erosion_mercator <= 0 or min(
                    _point_segment_distance((x, y), first, second) for first, second in edges
                ) >= erosion_mercator:
                    style_mask[pixel_index] = 255

    source_coverage = valid_footprint_pixels / footprint_pixels if footprint_pixels else 0.0
    style_pixels = sum(bool(value) for value in style_mask)
    erosion_applied = erosion_m
    if style_pixels < minimum_mask_pixels and valid_footprint_pixels >= minimum_mask_pixels:
        for index in range(len(style_mask)):
            style_mask[index] = 255 if footprint_mask[index] and valid[index] else 0
        style_pixels = sum(bool(value) for value in style_mask)
        erosion_applied = 0.0
    quality = {
        "footprint_pixels": footprint_pixels,
        "valid_footprint_pixels": valid_footprint_pixels,
        "source_coverage_ratio": source_coverage,
        "minimum_source_coverage_ratio": minimum_source_coverage,
        "style_mask_pixels": style_pixels,
        "style_mask_ratio": style_pixels / footprint_pixels if footprint_pixels else 0.0,
        "erosion_applied_m": erosion_applied,
        "intersecting_tile_count": len(selected),
        "decoded_tile_count": len(decoded),
        "decode_failures": decode_failures,
    }
    if footprint_pixels == 0:
        return unavailable("empty_rasterised_footprint", "the roof footprint covers no output pixel", quality)
    if source_coverage < minimum_source_coverage:
        return unavailable(
            "insufficient_satellite_coverage",
            "cached satellite coverage over the roof footprint is below the required threshold",
            quality,
        )
    if style_pixels < minimum_mask_pixels:
        return unavailable(
            "roof_mask_too_small",
            "the valid roof style mask contains too few pixels",
            quality,
        )

    fill = _median_colour(bytes(rgb), bytes(style_mask))
    reference = bytearray(fill * (width * height))
    reference_rgba = bytearray(width * height * 4)
    for index, enabled in enumerate(style_mask):
        source_offset = index * 3
        output_offset = index * 4
        if enabled:
            reference[source_offset : source_offset + 3] = rgb[source_offset : source_offset + 3]
        reference_rgba[output_offset : output_offset + 3] = reference[
            source_offset : source_offset + 3
        ]
        reference_rgba[output_offset + 3] = 255 if enabled else 0

    encoded = {
        "satellite_north_up": _encode_png(width, height, 2, bytes(rgb)),
        "source_valid_mask": _encode_png(width, height, 0, bytes(valid)),
        "footprint_mask": _encode_png(width, height, 0, bytes(footprint_mask)),
        "roof_style_mask": _encode_png(width, height, 0, bytes(style_mask)),
        "roof_reference": _encode_png(width, height, 2, bytes(reference)),
        "roof_reference_rgba": _encode_png(width, height, 6, bytes(reference_rgba)),
    }
    output_hashes: dict[str, str] = {}
    for key, payload in encoded.items():
        output_hashes[key] = hashlib.sha256(payload).hexdigest()

    document: dict[str, Any] = {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "kind": "myProject.appearance.roof_reference",
        "generator_version": GENERATOR_VERSION,
        "status": "READY",
        "building_id": footprint.identifier,
        "input_fingerprint": fingerprint,
        "source": "cached_satellite_tiles",
        "source_tile_manifest": str(tile_manifest_path),
        "source_tile_manifest_sha256": tile_manifest_sha256,
        "source_tiles": source_records,
        "workspace_frame": {
            "origin_lat": frame.origin_lat,
            "origin_lon": frame.origin_lon,
            "axes": {"x": "east", "z": "south"},
            "units": "m",
        },
        "crs": "EPSG:3857",
        "north_up": True,
        "bounds_mercator_m": [west, south, east, north],
        "size_px": [width, height],
        "affine_gdal": [west, gsd, 0.0, north, 0.0, -gsd],
        "pixel_center_mapping": {
            "x": "west + (column + 0.5) * pixel_width",
            "y": "north - (row + 0.5) * pixel_height",
        },
        "gsd_mercator_m_per_px": gsd,
        "ground_gsd_m_per_px_approx": gsd * math.cos(math.radians(center_lat)),
        "decimation_from_source": decimation,
        "footprint_local_xz": [list(point) for point in footprint.points],
        "footprint_wgs84": [list(point) for point in wgs84],
        "footprint_mercator_m": [list(point) for point in mercator],
        "rectification": {
            "kind": "web_mercator_north_up_resample",
            "rotation_degrees": 0.0,
            "perspective_correction": "not_applied",
        },
        "mask": {
            "kind": "workspace_footprint_raster",
            "erosion_requested_m": erosion_m,
            "erosion_applied_m": erosion_applied,
        },
        "parameters": parameters,
        "quality": quality,
        "outputs": dict(_OUTPUT_NAMES),
        "output_sha256": output_hashes,
        "fallback": {
            "policy": "preserve_existing_roof_geometry_and_texture_material_pipeline",
            "geometry_modified": False,
        },
        "limitations": [
            "The footprint mask is not a learned roof segmentation.",
            "No building-height parallax, occlusion, seasonal or illumination correction is applied.",
            "The imagery licence and allowed derivative uses remain the responsibility of its provider and user.",
        ],
    }
    _install_generated_reference(destination_root, encoded, document)
    return document


def generate_roof_references(
    *,
    tile_manifest_path: str | os.PathLike[str],
    buildings_root: str | os.PathLike[str],
    footprints: Iterable[Any],
    frame: LocalFrame,
    cache_buildings_root: str | os.PathLike[str] | None = None,
    padding_m: float = 2.0,
    erosion_m: float = 0.5,
    minimum_source_coverage: float = 0.95,
    minimum_mask_pixels: int = 16,
    max_dimension_px: int = 2048,
) -> dict[str, Any]:
    """Generate repeatable roof references without network or geometry work.

    All per-building failures become ``UNAVAILABLE`` manifests.  The returned
    summary is therefore safe to call from the selection READY publication
    path: appearance failure cannot reject an otherwise valid building mesh.
    """

    if not isinstance(frame, LocalFrame):
        raise TypeError("frame must be a LocalFrame")
    padding_m, erosion_m = float(padding_m), float(erosion_m)
    minimum_source_coverage = float(minimum_source_coverage)
    if not math.isfinite(padding_m) or padding_m < 0:
        raise ValueError("padding_m must be a non-negative finite value")
    if not math.isfinite(erosion_m) or erosion_m < 0:
        raise ValueError("erosion_m must be a non-negative finite value")
    if not 0 <= minimum_source_coverage <= 1:
        raise ValueError("minimum_source_coverage must be in [0,1]")
    if minimum_mask_pixels < 1 or max_dimension_px < 1:
        raise ValueError("minimum_mask_pixels and max_dimension_px must be positive")

    normalised = [_normalise_footprint(item) for item in footprints]
    manifest_path = Path(tile_manifest_path).expanduser().resolve(strict=False)
    output_root = Path(buildings_root).expanduser().resolve(strict=False)
    cache_output_root = (
        Path(cache_buildings_root).expanduser().resolve(strict=False)
        if cache_buildings_root is not None
        else None
    )
    results: dict[str, dict[str, Any]] = {}
    try:
        raw = manifest_path.read_bytes()
        tile_manifest_sha256 = hashlib.sha256(raw).hexdigest()
        tile_document = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(tile_document, Mapping):
            raise ValueError("tile manifest root is not an object")
        tiles = _load_tiles(tile_document)
    except Exception as exc:
        tile_manifest_sha256 = "unavailable"
        tiles = []
        for footprint in normalised:
            destination = output_root / footprint.identifier / "references" / "roof"
            fingerprint = _json_hash(
                {
                    "generator_version": GENERATOR_VERSION,
                    "building_id": footprint.identifier,
                    "footprint": footprint.points,
                    "frame": [frame.origin_lat, frame.origin_lon],
                    "tile_manifest": str(manifest_path),
                    "manifest_error": str(exc),
                }
            )
            document = _failure_document(
                footprint,
                code="tile_manifest_unavailable",
                message="the cached exact-tile manifest cannot be read",
                fingerprint=fingerprint,
                frame=frame,
                source_tile_manifest=manifest_path,
                details={"message": str(exc)},
            )
            try:
                _install_generated_reference(destination, {}, document)
            except Exception:
                pass
            results[footprint.identifier] = document
        return _summary(manifest_path, results)

    hash_cache: dict[Path, str] = {}
    image_cache: dict[Path, _Raster | Exception] = {}
    for footprint in normalised:
        destination = output_root / footprint.identifier / "references" / "roof"
        cache_root = (
            cache_output_root / footprint.identifier / "references" / "roof"
            if cache_output_root is not None
            else None
        )
        try:
            results[footprint.identifier] = _build_one_reference(
                footprint,
                frame=frame,
                tile_manifest_path=manifest_path,
                tile_manifest_sha256=tile_manifest_sha256,
                tiles=tiles,
                destination_root=destination,
                cache_root=cache_root,
                padding_m=padding_m,
                erosion_m=erosion_m,
                minimum_source_coverage=minimum_source_coverage,
                minimum_mask_pixels=minimum_mask_pixels,
                max_dimension_px=max_dimension_px,
                hash_cache=hash_cache,
                image_cache=image_cache,
            )
        except Exception as exc:  # per-building appearance failure is always soft
            fingerprint = _json_hash(
                {
                    "generator_version": GENERATOR_VERSION,
                    "building_id": footprint.identifier,
                    "tile_manifest_sha256": tile_manifest_sha256,
                    "unexpected_error": type(exc).__name__,
                }
            )
            document = _failure_document(
                footprint,
                code="roof_reference_internal_error",
                message=f"roof reference generation failed: {type(exc).__name__}: {exc}",
                fingerprint=fingerprint,
                frame=frame,
                source_tile_manifest=manifest_path,
            )
            try:
                _install_generated_reference(destination, {}, document)
            except Exception:
                pass
            results[footprint.identifier] = document
    return _summary(manifest_path, results)


def _summary(manifest_path: Path, results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    ready = sum(document.get("status") == "READY" for document in results.values())
    unavailable = sum(document.get("status") == "UNAVAILABLE" for document in results.values())
    return {
        "schema_version": REFERENCE_SCHEMA_VERSION,
        "kind": "myProject.appearance.roof_reference.summary",
        "status": "READY" if unavailable == 0 else ("PARTIAL" if ready else "UNAVAILABLE"),
        "source_tile_manifest": str(manifest_path),
        "requested": len(results),
        "ready": ready,
        "unavailable": unavailable,
        "geometry_modified": False,
        "buildings": dict(results),
    }


def generate_roof_references_for_request(
    request_path: str | os.PathLike[str],
    *,
    tile_manifest_path: str | os.PathLike[str] | None = None,
    buildings_root: str | os.PathLike[str] | None = None,
    **options: Any,
) -> dict[str, Any]:
    """Repeat the offline appearance step from an existing selection request."""

    source = Path(request_path).expanduser().resolve(strict=False)
    request = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(request, Mapping):
        raise ValueError("selection request root is not an object")
    workspace = Path(str(request["workspace"])).expanduser().resolve(strict=False)
    workspace_manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8-sig"))
    frame_document = workspace_manifest["frame"]
    frame = LocalFrame(float(frame_document["origin_lat"]), float(frame_document["origin_lon"]))
    selection_id = str(request["selection_id"])
    stable_id = selection_id[len("selection-") :] if selection_id.startswith("selection-") else selection_id
    manifest = (
        Path(tile_manifest_path)
        if tile_manifest_path is not None
        else workspace / "_selection_jobs" / stable_id / "tile_manifest.json"
    )
    publication = Path(buildings_root) if buildings_root is not None else source.parent / "buildings"
    return generate_roof_references(
        tile_manifest_path=manifest,
        buildings_root=publication,
        footprints=request.get("footprints", ()),
        frame=frame,
        **options,
    )


__all__ = [
    "GENERATOR_VERSION",
    "REFERENCE_SCHEMA_VERSION",
    "RoofReferenceFootprint",
    "generate_roof_references",
    "generate_roof_references_for_request",
]
