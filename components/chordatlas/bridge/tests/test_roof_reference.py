from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zlib


BRIDGE = Path(__file__).resolve().parents[1]
SRC = BRIDGE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from myproject.geo import LocalFrame
from myproject.roof_reference import _decode_png, generate_roof_references
from myproject.selection import _attach_roof_reference_metadata


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _rgb_png(
    path: Path,
    width: int,
    height: int,
    north: bytes = b"\xff\x00\x00",
    south: bytes = b"\x00\x00\xff",
) -> None:
    rows = []
    for row in range(height):
        colour = north if row < height // 2 else south
        rows.append(b"\x00" + colour * width)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _chunk(b"IEND", b"")
    )


class RoofReferenceTests(unittest.TestCase):
    def _fixture(self, root: Path, *, missing: bool = False):
        frame = LocalFrame(0.0, 0.0)
        tile = root / "job" / "satellite" / "sat_0.000000_0.000000.png"
        if not missing:
            _rgb_png(tile, 20, 20)
        manifest = root / "job" / "tile_manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "myProject.selection.exact_tiles",
                    "plan": {"zoom": 20},
                    "tiles": [
                        {
                            "tile_id": "synthetic",
                            "stem": "sat_0.000000_0.000000",
                            "zoom": 20,
                            "size_px": 20,
                            "bounds_mercator_m": [-10.0, -10.0, 10.0, 10.0],
                            "satellite_path": str(tile),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        footprint = {
            "id": "footprint-one",
            "points": [[-2.0, -2.0], [2.0, -2.0], [2.0, 2.0], [-2.0, 2.0]],
        }
        return frame, manifest, footprint

    def test_generates_north_up_reference_and_masks_from_cached_tile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame, manifest, footprint = self._fixture(root)
            buildings = root / "publication" / "buildings"

            report = generate_roof_references(
                tile_manifest_path=manifest,
                buildings_root=buildings,
                footprints=[footprint],
                frame=frame,
                padding_m=0.0,
                erosion_m=0.0,
                minimum_source_coverage=1.0,
                minimum_mask_pixels=1,
            )

            reference = report["buildings"]["footprint-one"]
            self.assertEqual(reference["status"], "READY")
            self.assertTrue(reference["north_up"])
            self.assertEqual(reference["crs"], "EPSG:3857")
            self.assertLess(reference["affine_gdal"][5], 0)
            roof_root = buildings / "footprint-one" / "references" / "roof"
            for filename in reference["outputs"].values():
                self.assertTrue((roof_root / filename).is_file(), filename)
            satellite = _decode_png(roof_root / "satellite_north_up.png")
            top = satellite.rgba[(satellite.width // 2) * 4 : (satellite.width // 2) * 4 + 3]
            bottom_offset = ((satellite.height - 1) * satellite.width + satellite.width // 2) * 4
            bottom = satellite.rgba[bottom_offset : bottom_offset + 3]
            self.assertEqual(top, b"\xff\x00\x00")
            self.assertEqual(bottom, b"\x00\x00\xff")
            self.assertGreater(reference["quality"]["style_mask_pixels"], 0)

    def test_second_identical_call_is_an_in_place_cache_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame, manifest, footprint = self._fixture(root)
            buildings = root / "publication" / "buildings"
            options = dict(
                tile_manifest_path=manifest,
                buildings_root=buildings,
                footprints=[footprint],
                frame=frame,
                padding_m=0.0,
                erosion_m=0.0,
                minimum_source_coverage=1.0,
                minimum_mask_pixels=1,
            )
            first = generate_roof_references(**options)
            marker = buildings / "footprint-one" / "references" / "roof" / "reference.json"
            first_mtime = marker.stat().st_mtime_ns
            time.sleep(0.01)
            second = generate_roof_references(**options)

            self.assertEqual(first["ready"], 1)
            self.assertTrue(second["buildings"]["footprint-one"]["cache_hit"])
            self.assertEqual(marker.stat().st_mtime_ns, first_mtime)

    def test_missing_cached_tile_writes_unavailable_without_reference_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame, manifest, footprint = self._fixture(root, missing=True)
            buildings = root / "publication" / "buildings"

            report = generate_roof_references(
                tile_manifest_path=manifest,
                buildings_root=buildings,
                footprints=[footprint],
                frame=frame,
            )

            reference = report["buildings"]["footprint-one"]
            roof_root = buildings / "footprint-one" / "references" / "roof"
            self.assertEqual(report["status"], "UNAVAILABLE")
            self.assertEqual(reference["status"], "UNAVAILABLE")
            self.assertEqual(reference["fallback"]["geometry_modified"], False)
            self.assertTrue((roof_root / "reference.json").is_file())
            self.assertFalse((roof_root / "roof_reference.png").exists())

    def test_selection_metadata_registration_is_additive_and_relative(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            building_dir = staging / "buildings" / "footprint-one"
            roof_dir = building_dir / "references" / "roof"
            roof_dir.mkdir(parents=True)
            (roof_dir / "reference.json").write_text("{}", encoding="utf-8")
            (building_dir / "building.json").write_text("{}", encoding="utf-8")
            entries = [
                {
                    "id": "footprint-one",
                    "publishable": True,
                    "relative_dir": "buildings/footprint-one",
                    "outputs": {"cropped_obj": "buildings/footprint-one/cropped.obj"},
                }
            ]
            index = {"status": "READY", "buildings": entries}
            report = {
                "status": "READY",
                "requested": 1,
                "ready": 1,
                "unavailable": 0,
                "source_tile_manifest": "tile_manifest.json",
                "buildings": {
                    "footprint-one": {
                        "status": "READY",
                        "outputs": {
                            "roof_reference": "roof_reference.png",
                            "roof_style_mask": "roof_style_mask.png",
                        },
                    }
                },
            }

            summary = _attach_roof_reference_metadata(staging, entries, index, report)

            self.assertEqual(summary["ready"], 1)
            self.assertEqual(
                entries[0]["outputs"]["roof_reference"],
                "buildings/footprint-one/references/roof/roof_reference.png",
            )
            self.assertEqual(entries[0]["appearance"]["roof"]["status"], "READY")
            saved = json.loads((building_dir / "building.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["outputs"]["cropped_obj"], "buildings/footprint-one/cropped.obj")

    def test_cache_rejects_output_path_escape_and_rebuilds_inside_roof_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame, manifest, footprint = self._fixture(root)
            buildings = root / "publication" / "buildings"
            options = dict(
                tile_manifest_path=manifest,
                buildings_root=buildings,
                footprints=[footprint],
                frame=frame,
                padding_m=0.0,
                erosion_m=0.0,
                minimum_source_coverage=1.0,
                minimum_mask_pixels=1,
            )
            generate_roof_references(**options)
            roof_root = buildings / "footprint-one" / "references" / "roof"
            outside = roof_root.parent / "escaped.png"
            outside.write_bytes(b"do-not-read-or-overwrite")
            marker = json.loads((roof_root / "reference.json").read_text(encoding="utf-8"))
            marker["outputs"]["roof_reference"] = "../escaped.png"
            (roof_root / "reference.json").write_text(json.dumps(marker), encoding="utf-8")

            rebuilt = generate_roof_references(**options)["buildings"]["footprint-one"]

            self.assertEqual(rebuilt["status"], "READY")
            self.assertFalse(rebuilt.get("cache_hit", False))
            self.assertEqual(outside.read_bytes(), b"do-not-read-or-overwrite")
            clean = json.loads((roof_root / "reference.json").read_text(encoding="utf-8"))
            self.assertEqual(clean["outputs"]["roof_reference"], "roof_reference.png")

    def test_cache_hash_tamper_forces_a_clean_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame, manifest, footprint = self._fixture(root)
            buildings = root / "publication" / "buildings"
            options = dict(
                tile_manifest_path=manifest,
                buildings_root=buildings,
                footprints=[footprint],
                frame=frame,
                padding_m=0.0,
                erosion_m=0.0,
                minimum_source_coverage=1.0,
                minimum_mask_pixels=1,
            )
            generate_roof_references(**options)
            roof_root = buildings / "footprint-one" / "references" / "roof"
            tampered = roof_root / "roof_reference.png"
            tampered.write_bytes(b"tampered")

            rebuilt = generate_roof_references(**options)["buildings"]["footprint-one"]

            self.assertEqual(rebuilt["status"], "READY")
            self.assertFalse(rebuilt.get("cache_hit", False))
            self.assertNotEqual(tampered.read_bytes(), b"tampered")
            self.assertEqual(
                rebuilt["output_sha256"]["roof_reference"],
                __import__("hashlib").sha256(tampered.read_bytes()).hexdigest(),
            )

    def test_cache_without_output_hashes_forces_a_clean_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame, manifest, footprint = self._fixture(root)
            buildings = root / "publication" / "buildings"
            options = dict(
                tile_manifest_path=manifest,
                buildings_root=buildings,
                footprints=[footprint],
                frame=frame,
                padding_m=0.0,
                erosion_m=0.0,
                minimum_source_coverage=1.0,
                minimum_mask_pixels=1,
            )
            generate_roof_references(**options)
            roof_root = buildings / "footprint-one" / "references" / "roof"
            marker_path = roof_root / "reference.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker.pop("output_sha256")
            marker_path.write_text(json.dumps(marker), encoding="utf-8")

            rebuilt = generate_roof_references(**options)["buildings"]["footprint-one"]

            self.assertEqual(rebuilt["status"], "READY")
            self.assertFalse(rebuilt.get("cache_hit", False))
            clean = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(set(clean["output_sha256"]), set(clean["outputs"]))

    def test_interrupted_rebuild_keeps_old_ready_directory_byte_for_byte(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame, manifest, footprint = self._fixture(root)
            buildings = root / "publication" / "buildings"
            options = dict(
                tile_manifest_path=manifest,
                buildings_root=buildings,
                footprints=[footprint],
                frame=frame,
                padding_m=0.0,
                erosion_m=0.0,
                minimum_source_coverage=1.0,
                minimum_mask_pixels=1,
            )
            generate_roof_references(**options)
            roof_root = buildings / "footprint-one" / "references" / "roof"
            before = {path.name: path.read_bytes() for path in roof_root.iterdir() if path.is_file()}
            tile = root / "job" / "satellite" / "sat_0.000000_0.000000.png"
            _rgb_png(tile, 20, 20, b"\x00\xff\x00", b"\xff\xff\x00")

            def crash_on_staged_output(path: Path, payload: bytes) -> None:
                if ".roof.staging-" in path.parent.name:
                    raise SimulatedCrash()
                path.write_bytes(payload)

            with patch("myproject.roof_reference._atomic_bytes", side_effect=crash_on_staged_output):
                with self.assertRaises(SimulatedCrash):
                    generate_roof_references(**options)

            after = {path.name: path.read_bytes() for path in roof_root.iterdir() if path.is_file()}
            self.assertEqual(after, before)
            self.assertEqual(list(roof_root.parent.glob(".roof.staging-*")), [])


if __name__ == "__main__":
    unittest.main()
