"""Prepare licensed equirectangular panoramas for ChordAtlas.

This module deliberately contains no downloader.  It accepts a CSV manifest
whose rows explicitly confirm that each input image is owned or licensed by
the user, validates the images, and copies them using ChordAtlas' filename
convention::

    latitude_longitude_elevation_heading_tilt_roll_id.jpg

Required CSV columns are ``source_path``, ``lat``, ``lon``, ``elevation``,
``heading``, ``tilt``, ``roll``, ``id``, and ``ownership_confirmed``.  Relative
source paths are resolved relative to the manifest file.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import filecmp
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Iterable


REQUIRED_COLUMNS = (
    "source_path",
    "lat",
    "lon",
    "elevation",
    "heading",
    "tilt",
    "roll",
    "id",
    "ownership_confirmed",
)
REPORT_FILENAME = "panorama_import_report.json"

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}
_STANDALONE_MARKERS = {0x01, 0xD8, 0xD9, *range(0xD0, 0xD8)}


class JpegFormatError(ValueError):
    """Raised when a file is not a parseable JPEG with a size marker."""


@dataclass(frozen=True)
class _Panorama:
    row: int
    source: Path
    destination: Path
    identifier: str
    width: int
    height: int
    action: str = "copy"


def read_jpeg_dimensions(path: str | os.PathLike[str]) -> tuple[int, int]:
    """Return ``(width, height)`` using JPEG markers and the standard library.

    Pixel data is not decoded.  Baseline, progressive, lossless, and the
    common differential JPEG frame markers are supported.
    """

    jpeg_path = Path(path)
    with jpeg_path.open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            raise JpegFormatError("missing JPEG SOI marker")

        while True:
            prefix = stream.read(1)
            if not prefix:
                raise JpegFormatError("JPEG ended before a frame-size marker")
            if prefix != b"\xff":
                continue

            marker_byte = stream.read(1)
            while marker_byte == b"\xff":
                marker_byte = stream.read(1)
            if not marker_byte:
                raise JpegFormatError("truncated JPEG marker")

            marker = marker_byte[0]
            if marker == 0x00:  # Escaped 0xff byte in entropy-coded data.
                continue
            if marker == 0xD9:
                raise JpegFormatError("JPEG has no frame-size marker")
            if marker in _STANDALONE_MARKERS:
                continue

            raw_length = stream.read(2)
            if len(raw_length) != 2:
                raise JpegFormatError("truncated JPEG segment length")
            segment_length = int.from_bytes(raw_length, "big")
            if segment_length < 2:
                raise JpegFormatError("invalid JPEG segment length")

            if marker in _SOF_MARKERS:
                if segment_length < 7:
                    raise JpegFormatError("truncated JPEG frame header")
                frame = stream.read(5)
                if len(frame) != 5:
                    raise JpegFormatError("truncated JPEG frame header")
                height = int.from_bytes(frame[1:3], "big")
                width = int.from_bytes(frame[3:5], "big")
                if width <= 0 or height <= 0:
                    raise JpegFormatError("JPEG dimensions must be positive")
                return width, height

            # A valid JPEG frame header precedes its first scan.
            if marker == 0xDA:
                raise JpegFormatError("JPEG scan encountered before frame header")

            stream.seek(segment_length - 2, os.SEEK_CUR)


def _new_report(manifest: Path, output_dir: Path) -> dict[str, Any]:
    return {
        "status": "pending",
        "manifest": str(manifest),
        "output_dir": str(output_dir),
        "total_rows": 0,
        "ownership_confirmed": False,
        "copied": 0,
        "skipped": 0,
        "errors": [],
        "output_files": [],
        "copied_files": [],
        "skipped_files": [],
    }


def _error(
    report: dict[str, Any], code: str, message: str, row: int | None = None
) -> None:
    item: dict[str, Any] = {"code": code, "message": message}
    if row is not None:
        item["row"] = row
    report["errors"].append(item)


def _write_report(report: dict[str, Any], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report["report_path"] = str(report_path)
    temporary = report_path.with_name(report_path.name + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(report_path)


def _finish(
    report: dict[str, Any], report_path: Path, status: str
) -> dict[str, Any]:
    report["status"] = status
    _write_report(report, report_path)
    return report


def _read_rows(
    manifest: Path, report: dict[str, Any]
) -> list[tuple[int, dict[str, str | None]]]:
    try:
        stream = manifest.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        _error(report, "manifest_unreadable", f"Cannot read manifest: {exc}")
        return []

    with stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            _error(report, "missing_header", "Manifest has no CSV header")
            return []

        headers = [header.strip() if header is not None else "" for header in reader.fieldnames]
        if len(set(headers)) != len(headers):
            _error(report, "duplicate_header", "Manifest contains duplicate column names")
            return []
        reader.fieldnames = headers

        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            _error(
                report,
                "missing_columns",
                "Manifest is missing required columns: " + ", ".join(missing),
            )
            return []

        rows: list[tuple[int, dict[str, str | None]]] = []
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                _error(
                    report,
                    "extra_values",
                    "Row has more values than the CSV header",
                    row_number,
                )
                continue
            if all(value is None or not value.strip() for value in row.values()):
                continue
            rows.append((row_number, row))

    report["total_rows"] = len(rows)
    if not rows and not report["errors"]:
        _error(report, "empty_manifest", "Manifest contains no panorama rows")
    return rows


def _required_value(
    row: dict[str, str | None], field: str, row_number: int, report: dict[str, Any]
) -> str | None:
    value = row.get(field)
    if value is None or not value.strip():
        _error(report, "missing_value", f"{field} must not be empty", row_number)
        return None
    return value.strip()


def _parse_decimal(
    row: dict[str, str | None], field: str, row_number: int, report: dict[str, Any]
) -> Decimal | None:
    raw = _required_value(row, field, row_number, report)
    if raw is None:
        return None
    if len(raw) > 64:
        _error(report, "invalid_number", f"{field} is too long", row_number)
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        _error(report, "invalid_number", f"{field} is not a number: {raw!r}", row_number)
        return None
    if not value.is_finite():
        _error(report, "invalid_number", f"{field} must be finite", row_number)
        return None
    return value


def _format_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _is_same_file(source: Path, destination: Path) -> bool:
    try:
        if source.samefile(destination):
            return True
    except OSError:
        pass
    try:
        return filecmp.cmp(source, destination, shallow=False)
    except OSError:
        return False


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Copy without overwriting a destination created after preflight."""

    created = False
    try:
        with source.open("rb") as source_stream:
            with destination.open("xb") as destination_stream:
                created = True
                shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
        shutil.copystat(source, destination)
    except Exception:
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        raise


def _normalised_path_key(path: Path) -> str:
    return os.path.normcase(str(path)).casefold()


def _validate_rows(
    rows: Iterable[tuple[int, dict[str, str | None]]],
    manifest: Path,
    output_dir: Path,
    report: dict[str, Any],
) -> list[_Panorama]:
    panoramas: list[_Panorama] = []
    destinations: dict[str, int] = {}
    identifiers: dict[str, int] = {}
    sources: dict[str, int] = {}

    for row_number, row in rows:
        source_raw = _required_value(row, "source_path", row_number, report)
        identifier = _required_value(row, "id", row_number, report)
        lat = _parse_decimal(row, "lat", row_number, report)
        lon = _parse_decimal(row, "lon", row_number, report)
        elevation = _parse_decimal(row, "elevation", row_number, report)
        heading = _parse_decimal(row, "heading", row_number, report)
        tilt = _parse_decimal(row, "tilt", row_number, report)
        roll = _parse_decimal(row, "roll", row_number, report)

        if lat is not None and not (Decimal("-90") <= lat <= Decimal("90")):
            _error(report, "latitude_out_of_range", "lat must be between -90 and 90", row_number)
        if lon is not None and not (Decimal("-180") <= lon <= Decimal("180")):
            _error(report, "longitude_out_of_range", "lon must be between -180 and 180", row_number)
        if identifier is not None and not _SAFE_ID.fullmatch(identifier):
            _error(
                report,
                "invalid_id",
                "id must contain only ASCII letters, digits, dot, underscore, or hyphen",
                row_number,
            )

        values = (lat, lon, elevation, heading, tilt, roll)
        if source_raw is None or identifier is None or any(value is None for value in values):
            continue
        if not (Decimal("-90") <= lat <= Decimal("90")):
            continue
        if not (Decimal("-180") <= lon <= Decimal("180")):
            continue
        if not _SAFE_ID.fullmatch(identifier):
            continue

        source = Path(source_raw)
        if not source.is_absolute():
            source = manifest.parent / source
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            _error(report, "source_missing", f"Source image does not exist: {exc}", row_number)
            continue
        if not source.is_file():
            _error(report, "source_not_file", f"Source is not a file: {source}", row_number)
            continue
        if source.suffix.lower() not in {".jpg", ".jpeg"}:
            _error(report, "not_jpeg", "Only .jpg or .jpeg source files are accepted", row_number)
            continue

        try:
            width, height = read_jpeg_dimensions(source)
        except (OSError, JpegFormatError) as exc:
            _error(report, "invalid_jpeg", f"Cannot read JPEG dimensions: {exc}", row_number)
            continue
        if width != 2 * height:
            _error(
                report,
                "invalid_aspect_ratio",
                f"Panorama must be exactly 2:1; found {width}x{height}",
                row_number,
            )
            continue

        numeric_parts = [_format_decimal(value) for value in values]
        destination_name = "_".join([*numeric_parts, identifier]) + ".jpg"
        if len(destination_name) > 240:
            _error(report, "filename_too_long", "Generated filename is too long", row_number)
            continue
        destination = output_dir / destination_name

        destination_key = destination_name.casefold()
        if destination_key in destinations:
            _error(
                report,
                "duplicate_destination",
                f"Generated filename conflicts with row {destinations[destination_key]}: {destination_name}",
                row_number,
            )
            continue
        destinations[destination_key] = row_number

        identifier_key = identifier.casefold()
        if identifier_key in identifiers:
            _error(
                report,
                "duplicate_id",
                f"id conflicts with row {identifiers[identifier_key]}: {identifier}",
                row_number,
            )
            continue
        identifiers[identifier_key] = row_number

        source_key = _normalised_path_key(source)
        if source_key in sources:
            _error(
                report,
                "duplicate_source",
                f"Source image is already used by row {sources[source_key]}",
                row_number,
            )
            continue
        sources[source_key] = row_number

        action = "copy"
        if destination.is_symlink():
            _error(report, "destination_conflict", f"Destination is a symbolic link: {destination}", row_number)
            continue
        if destination.exists():
            if not destination.is_file() or not _is_same_file(source, destination):
                _error(
                    report,
                    "destination_conflict",
                    f"Destination already exists with different content: {destination}",
                    row_number,
                )
                continue
            action = "skip"

        panoramas.append(
            _Panorama(
                row=row_number,
                source=source,
                destination=destination,
                identifier=identifier,
                width=width,
                height=height,
                action=action,
            )
        )

    return panoramas


def prepare_panoramas(
    manifest_csv: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    report_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Validate and copy explicitly licensed panoramas into a ChordAtlas folder.

    Validation is performed for the entire manifest before any image is
    copied.  The literal value ``true`` (case-insensitive, surrounding spaces
    ignored) is required in ``ownership_confirmed`` for every row.  Any other
    value rejects the whole manifest.

    A JSON-serialisable report is returned and also written to
    ``report_path``.  By default it is written as
    ``panorama_import_report.json`` inside ``output_dir``.
    """

    manifest = Path(manifest_csv).resolve()
    destination_root = Path(output_dir).resolve()
    report_file = (
        Path(report_path).resolve()
        if report_path is not None
        else destination_root / REPORT_FILENAME
    )
    report = _new_report(manifest, destination_root)

    rows = _read_rows(manifest, report)
    if report["errors"]:
        return _finish(report, report_file, "rejected")

    for row_number, row in rows:
        ownership = row.get("ownership_confirmed")
        if ownership is None or ownership.strip().casefold() != "true":
            _error(
                report,
                "ownership_not_confirmed",
                "ownership_confirmed must explicitly be true",
                row_number,
            )
    if report["errors"]:
        return _finish(report, report_file, "rejected")
    report["ownership_confirmed"] = True

    panoramas = _validate_rows(rows, manifest, destination_root, report)
    if report["errors"]:
        return _finish(report, report_file, "rejected")

    destination_root.mkdir(parents=True, exist_ok=True)
    copied_this_run: list[Path] = []
    skipped_files: list[str] = []

    for panorama in panoramas:
        destination_text = str(panorama.destination)
        if panorama.action == "skip":
            skipped_files.append(destination_text)
            continue
        try:
            _copy_exclusive(panorama.source, panorama.destination)
            copied_this_run.append(panorama.destination)
        except OSError as exc:
            _error(
                report,
                "copy_failed",
                f"Could not copy {panorama.source} to {panorama.destination}: {exc}",
                panorama.row,
            )
            for copied_path in copied_this_run:
                try:
                    copied_path.unlink()
                except OSError:
                    pass
            report["copied"] = 0
            report["copied_files"] = []
            report["skipped"] = len(skipped_files)
            report["skipped_files"] = skipped_files
            report["output_files"] = skipped_files
            return _finish(report, report_file, "rejected")

    copied_files = [str(path) for path in copied_this_run]
    output_files = [str(panorama.destination) for panorama in panoramas]
    report["copied"] = len(copied_files)
    report["skipped"] = len(skipped_files)
    report["copied_files"] = copied_files
    report["skipped_files"] = skipped_files
    report["output_files"] = output_files
    return _finish(report, report_file, "ok")


__all__ = [
    "JpegFormatError",
    "REPORT_FILENAME",
    "REQUIRED_COLUMNS",
    "prepare_panoramas",
    "read_jpeg_dimensions",
]
