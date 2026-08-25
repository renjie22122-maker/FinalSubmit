from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import zlib

BRIDGE = Path(__file__).resolve().parents[1]
SRC = BRIDGE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from myproject.roof_backfill import _selection_lock, backfill_cached_roof_references
from myproject.selection import SelectionBridgeError


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _png(path: Path) -> None:
    width = height = 20
    rows = [b"\x00" + b"\x90\x70\x50" * width for _ in range(height)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + _chunk(b"IDAT", zlib.compress(b"".join(rows))) + _chunk(b"IEND", b""))


class RoofBackfillTests(unittest.TestCase):
    def _fixture(self, root: Path, *, missing_tile: bool = False) -> tuple[Path, Path]:
        workspace = root / "workspace"
        publication = workspace / "generated_blocks" / "cache-id"
        building = publication / "buildings" / "footprint-one"
        building.mkdir(parents=True)
        (workspace / "manifest.json").write_text(json.dumps({"frame": {
            "origin_lat": 0.0, "origin_lon": 0.0, "units": "m",
            "axes": {"x": "east", "y": "up", "z": "south"}}}), encoding="utf-8")
        request = {"workspace": str(workspace), "selection_id": "cache-id",
                   "footprints": [{"id": "footprint-one", "points": [
                       [-2.0, -2.0], [2.0, -2.0], [2.0, 2.0], [-2.0, 2.0]]}],
                   "options": {"require_complete_buildings": True,
                               "pipeline_contract_version": "osm-prealign-v1",
                               "osm_prealign": True}}
        (publication / "request.json").write_text(json.dumps(request), encoding="utf-8")
        entry = {"id": "footprint-one", "component_id": "footprint-one",
                 "footprint_id": "footprint-one", "footprint_ids": ["footprint-one"],
                 "status": "COARSE_READY", "publishable": True,
                 "relative_dir": "buildings/footprint-one",
                 "outputs": {"cropped_obj": "buildings/footprint-one/cropped.obj",
                             "gis_obj": "buildings/footprint-one/gis.obj",
                             "gis_footprints_obj": "buildings/footprint-one/gis_footprints.obj"},
                 "metrics": {"vertex_count": 3, "face_count": 1}}
        summary = {"requested": 1, "ready": 1, "coarse_ready": 1,
                   "rejected": 0, "empty": 0, "failed": 0}
        index = {"schema_version": 1, "kind": "myProject.selection.buildings",
                 "building_publication_version": "per-footprint-v2",
                 "pipeline_contract_version": "osm-prealign-v1", "selection_id": "cache-id",
                 "stable_id": "cache-id", "status": "READY", "summary": summary,
                 "buildings": [entry]}
        result = {"schema_version": 1, "kind": "myProject.selection.result", "status": "READY",
                  "selection_id": "cache-id", "stable_id": "cache-id",
                  "pipeline_contract_version": "osm-prealign-v1",
                  "building_publication_version": "per-footprint-v2", "osm_prealign": True,
                  "buildings_summary": summary, "buildings": [entry],
                  "outputs": {"buildings_index": "buildings/index.json"}}
        (building / "building.json").write_text(json.dumps(entry), encoding="utf-8")
        (publication / "buildings" / "index.json").write_text(json.dumps(index), encoding="utf-8")
        (publication / "result.json").write_text(json.dumps(result), encoding="utf-8")
        job = workspace / "_selection_jobs" / "cache-id"
        tile = job / "satellite" / "tile.png"
        if not missing_tile:
            _png(tile)
        manifest = {"schema_version": 1, "kind": "myProject.selection.exact_tiles",
                    "pipeline_contract_version": "osm-prealign-v1",
                    "building_publication_version": "per-footprint-v2", "osm_prealign": True,
                    "status": "READY", "selection_id": "cache-id", "stable_id": "cache-id",
                    "workspace": str(workspace), "plan": {"zoom": 20}, "tiles": [{
                        "tile_id": "one", "stem": "tile", "zoom": 20, "size_px": 20,
                        "bounds_mercator_m": [-10.0, -10.0, 10.0, 10.0],
                        "satellite_path": str(tile)}]}
        manifest_path = job / "tile_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return publication, job

    @staticmethod
    def _metadata(publication: Path) -> list[Path]:
        return [publication / "result.json", publication / "buildings" / "index.json",
                publication / "buildings" / "footprint-one" / "building.json"]

    def test_ready_backfill_is_roof_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            publication, _ = self._fixture(Path(temporary))
            before = {path: path.read_bytes() for path in self._metadata(publication)}
            first = backfill_cached_roof_references(
                publication, padding_m=0, erosion_m=0,
                minimum_source_coverage=1, minimum_mask_pixels=1)
            marker = publication / "buildings" / "footprint-one" / "references" / "roof" / "reference.json"
            marker_mtime = marker.stat().st_mtime_ns
            time.sleep(0.01)
            second = backfill_cached_roof_references(
                publication, padding_m=0, erosion_m=0,
                minimum_source_coverage=1, minimum_mask_pixels=1)
            self.assertEqual(first["status"], "READY")
            self.assertFalse(first["publication_metadata_modified"])
            self.assertTrue(second["buildings"]["footprint-one"]["cache_hit"])
            self.assertEqual(marker.stat().st_mtime_ns, marker_mtime)
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_missing_cached_tile_is_soft_unavailable_and_metadata_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            publication, _ = self._fixture(Path(temporary), missing_tile=True)
            before = {path: path.read_bytes() for path in self._metadata(publication)}
            report = backfill_cached_roof_references(publication)
            roof = publication / "buildings" / "footprint-one" / "references" / "roof"
            self.assertEqual(report["status"], "UNAVAILABLE")
            self.assertTrue((roof / "reference.json").is_file())
            self.assertFalse((roof / "roof_reference.png").exists())
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_cross_process_lock_refuses_a_second_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            publication, job = self._fixture(Path(temporary))
            with _selection_lock(job / ".roof-reference-backfill.lock", 0):
                with self.assertRaises(SelectionBridgeError) as raised:
                    backfill_cached_roof_references(publication, lock_timeout=0)
            self.assertEqual(raised.exception.code, "roof_backfill_locked")

    def test_rejected_footprint_has_no_roof_directory_and_metadata_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            publication, _ = self._fixture(Path(temporary))
            request_path = publication / "request.json"
            request = json.loads(request_path.read_text())
            request["footprints"].append({"id": "footprint-rejected", "points": [
                [5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 6.0]]})
            request_path.write_text(json.dumps(request), encoding="utf-8")
            index_path = publication / "buildings" / "index.json"
            result_path = publication / "result.json"
            index = json.loads(index_path.read_text())
            rejected = {"id": "footprint-rejected", "component_id": "footprint-rejected",
                        "footprint_id": "footprint-rejected",
                        "footprint_ids": ["footprint-rejected"], "status": "REJECTED",
                        "publishable": False, "relative_dir": "buildings/footprint-rejected",
                        "outputs": None, "metrics": {"vertex_count": 0, "face_count": 0}}
            index["buildings"].append(rejected)
            index["summary"].update({"requested": 2, "rejected": 1})
            result = json.loads(result_path.read_text())
            result["buildings"] = index["buildings"]
            result["buildings_summary"] = index["summary"]
            index_path.write_text(json.dumps(index), encoding="utf-8")
            result_path.write_text(json.dumps(result), encoding="utf-8")
            before = {path: path.read_bytes() for path in [request_path, index_path, result_path]}

            report = backfill_cached_roof_references(
                publication, padding_m=0, erosion_m=0,
                minimum_source_coverage=1, minimum_mask_pixels=1)

            self.assertEqual(report["requested"], 1)
            self.assertFalse((publication / "buildings" / "footprint-rejected").exists())
            self.assertEqual({path: path.read_bytes() for path in before}, before)


if __name__ == "__main__":
    unittest.main()
