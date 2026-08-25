from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from myproject.panoramas import (  # noqa: E402
    REPORT_FILENAME,
    prepare_panoramas,
    read_jpeg_dimensions,
)


FIELDS = [
    "source_path",
    "lat",
    "lon",
    "elevation",
    "heading",
    "tilt",
    "roll",
    "id",
    "ownership_confirmed",
]


def _write_jpeg(path: Path, width: int, height: int, marker: int = 0xC0) -> None:
    """Write the marker subset needed for dimension validation tests."""

    if not (0 < width <= 65535 and 0 < height <= 65535):
        raise ValueError("test JPEG dimensions are outside the JPEG field range")
    app0_body = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + (len(app0_body) + 2).to_bytes(2, "big") + app0_body
    frame_body = (
        b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00"
    )
    frame = b"\xff" + bytes([marker]) + (len(frame_body) + 2).to_bytes(2, "big") + frame_body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8" + app0 + frame + b"\xff\xd9")


def _row(source_path: str, identifier: str = "camera-001", ownership: str = "true") -> dict[str, str]:
    return {
        "source_path": source_path,
        "lat": "51.505621",
        "lon": "-0.126707",
        "elevation": "0",
        "heading": "165.4361",
        "tilt": "89.2611",
        "roll": "-0.3073",
        "id": identifier,
        "ownership_confirmed": ownership,
    }


def _write_manifest(path: Path, rows: list[dict[str, str]], fields: list[str] | None = None) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class PanoramaTests(unittest.TestCase):
    def test_reads_baseline_and_progressive_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline.jpg"
            progressive = root / "progressive.jpg"
            _write_jpeg(baseline, 4096, 2048, marker=0xC0)
            _write_jpeg(progressive, 2048, 1024, marker=0xC2)
            self.assertEqual(read_jpeg_dimensions(baseline), (4096, 2048))
            self.assertEqual(read_jpeg_dimensions(progressive), (2048, 1024))

    def test_copies_authorised_two_to_one_jpeg_with_chordatlas_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "inputs" / "licensed.jpeg"
            manifest = root / "panoramas.csv"
            output = root / "project" / "panos"
            _write_jpeg(source, 4000, 2000)
            _write_manifest(manifest, [_row("inputs/licensed.jpeg")])

            report = prepare_panoramas(manifest, output)

            expected = output / "51.505621_-0.126707_0_165.4361_89.2611_-0.3073_camera-001.jpg"
            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["copied"], 1)
            self.assertEqual(report["skipped"], 0)
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["output_files"], [str(expected.resolve())])
            self.assertEqual(expected.read_bytes(), source.read_bytes())

            saved_report = json.loads((output / REPORT_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(saved_report["copied"], 1)
            self.assertTrue(saved_report["ownership_confirmed"])

    def test_any_unconfirmed_row_rejects_entire_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.jpg"
            second = root / "second.jpg"
            _write_jpeg(first, 2000, 1000)
            _write_jpeg(second, 2000, 1000)
            manifest = root / "panoramas.csv"
            _write_manifest(
                manifest,
                [
                    _row("first.jpg", identifier="first", ownership="TRUE"),
                    _row("second.jpg", identifier="second", ownership="false"),
                ],
            )
            output = root / "panos"

            report = prepare_panoramas(manifest, output)

            self.assertEqual(report["status"], "rejected")
            self.assertEqual(report["copied"], 0)
            self.assertFalse(report["ownership_confirmed"])
            self.assertIn("ownership_not_confirmed", {item["code"] for item in report["errors"]})
            self.assertEqual(list(output.glob("*.jpg")), [])

    def test_rejects_four_to_one_data_builder_style_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "not-equirectangular.jpg"
            _write_jpeg(source, 4000, 1000)
            manifest = root / "panoramas.csv"
            _write_manifest(manifest, [_row(source.name)])
            output = root / "panos"

            report = prepare_panoramas(manifest, output)

            self.assertEqual(report["status"], "rejected")
            self.assertEqual(report["copied"], 0)
            errors = {item["code"]: item["message"] for item in report["errors"]}
            self.assertIn("invalid_aspect_ratio", errors)
            self.assertIn("4000x1000", errors["invalid_aspect_ratio"])
            self.assertEqual(list(output.glob("*.jpg")), [])

    def test_existing_identical_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jpg"
            _write_jpeg(source, 2000, 1000)
            manifest = root / "panoramas.csv"
            _write_manifest(manifest, [_row(source.name)])
            output = root / "panos"
            output.mkdir()
            destination = output / "51.505621_-0.126707_0_165.4361_89.2611_-0.3073_camera-001.jpg"
            destination.write_bytes(source.read_bytes())

            report = prepare_panoramas(manifest, output)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["copied"], 0)
            self.assertEqual(report["skipped"], 1)
            self.assertEqual(report["output_files"], [str(destination.resolve())])

    def test_different_existing_destination_rejects_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jpg"
            _write_jpeg(source, 2000, 1000)
            manifest = root / "panoramas.csv"
            _write_manifest(manifest, [_row(source.name)])
            output = root / "panos"
            output.mkdir()
            destination = output / "51.505621_-0.126707_0_165.4361_89.2611_-0.3073_camera-001.jpg"
            destination.write_bytes(b"do not overwrite")

            report = prepare_panoramas(manifest, output)

            self.assertEqual(report["status"], "rejected")
            self.assertEqual(report["copied"], 0)
            self.assertIn("destination_conflict", {item["code"] for item in report["errors"]})
            self.assertEqual(destination.read_bytes(), b"do not overwrite")

    def test_missing_column_and_invalid_fields_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jpg"
            _write_jpeg(source, 2000, 1000)
            manifest = root / "missing-column.csv"
            fields = [field for field in FIELDS if field != "roll"]
            partial = _row(source.name)
            partial.pop("roll")
            _write_manifest(manifest, [partial], fields=fields)

            report = prepare_panoramas(manifest, root / "panos")

            self.assertEqual(report["status"], "rejected")
            self.assertIn("missing_columns", {item["code"] for item in report["errors"]})

            invalid_manifest = root / "invalid.csv"
            invalid = _row(source.name, identifier="../unsafe")
            invalid["lat"] = "91"
            _write_manifest(invalid_manifest, [invalid])
            invalid_report = prepare_panoramas(invalid_manifest, root / "panos-invalid")
            codes = {item["code"] for item in invalid_report["errors"]}
            self.assertIn("latitude_out_of_range", codes)
            self.assertIn("invalid_id", codes)


if __name__ == "__main__":
    unittest.main()
