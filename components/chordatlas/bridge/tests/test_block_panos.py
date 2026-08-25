import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from myproject import block_panos as bp
from myproject import streetview_panos as svi
from myproject.cli import main as cli_main


class SequenceClient:
    def __init__(self, pano_ids):
        self.pano_ids = list(pano_ids)
        self.calls = 0
        self._api_key = "secret-must-not-be-recorded"

    def fetch_metadata(self, entry):
        pano_id = self.pano_ids[self.calls]
        self.calls += 1
        return svi.PanoMetadata(
            pano_id,
            entry.latitude + self.calls * 1e-7,
            entry.longitude,
            "2026-08",
            "Google",
        )


class FailingClient:
    _api_key = "secret-must-not-be-recorded"

    def fetch_metadata(self, entry):
        raise svi.ImportFailure("metadata", "no panorama", code="ZERO_RESULTS")


class BlockPanoramaTests(unittest.TestCase):
    def workspace_request(self, root: Path):
        workspace = root / "workspace"
        request_dir = workspace / "generated_blocks" / "selection-a" / "references" / "panoramas"
        request_dir.mkdir(parents=True)
        (workspace / "manifest.json").write_text(
            json.dumps(
                {
                    "frame": {
                        "origin_lat": 51.5074,
                        "origin_lon": -0.1277,
                        "axes": {"x": "east", "y": "up", "z": "south"},
                    }
                }
            ),
            encoding="utf-8",
        )
        request = request_dir / "request.json"
        request.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": bp.REQUEST_KIND,
                    "workspace": str(workspace),
                    "selection_id": "selection-a",
                    "footprints": [
                        {
                            "id": "footprint-a",
                            "points": [[0, 0], [20, 0], [20, 20], [0, 20]],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return workspace, request, request_dir / "plan_report.json"

    def write_batch_report(self, path, workspace, scoped_todo):
        path.write_text(
            json.dumps(
                {
                    "schema": "chordatlas-static-pano-report-v1",
                    "mode": "batch",
                    "dry_run": False,
                    "todo_sha256": svi.sha256_file(scoped_todo),
                    "output_dir": str(workspace / "panos"),
                    "summary": {"failed": 0},
                    "prevalidated_sample": {"sha256": "sample-sha"},
                    "finished_utc": "2026-08-14T12:00:00Z",
                }
            ),
            encoding="utf-8",
        )

    def test_local_frame_inverse_matches_x_east_z_south(self):
        frame = bp.LocalFrame(51.5, -0.1)
        lat, lon = bp.local_to_wgs84(100, 100, frame)
        self.assertLess(lat, frame.origin_lat)
        self.assertGreater(lon, frame.origin_lon)

    def test_perimeter_seeds_are_deterministic_outward_and_capped(self):
        footprint = bp.Footprint(
            "f", ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0))
        )
        first = bp.build_seeds([footprint], spacing_m=5, offset_m=8, max_seeds=6)
        second = bp.build_seeds([footprint], spacing_m=5, offset_m=8, max_seeds=6)
        self.assertEqual(first, second)
        self.assertEqual(6, len(first))
        self.assertTrue(
            all(seed.x < 0 or seed.x > 20 or seed.z < 0 or seed.z > 20 for seed in first)
        )

    def test_fake_metadata_dedupes_and_atomically_writes_todo(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, report_path = self.workspace_request(Path(temporary))
            scoped_todo = request.parent / "todo.list"
            live_todo = workspace / "panos" / "todo.list"
            live_todo.parent.mkdir()
            live_todo.write_text("old-live-plan\n", encoding="utf-8")
            client = SequenceClient(["pano-a", "pano-a", "pano-b", "pano-b"])
            report = bp.prepare_block_panos(
                request,
                todo_path=scoped_todo,
                report_path=report_path,
                spacing_m=10,
                max_seeds=4,
                client=client,
            )
            self.assertEqual("READY", report["status"])
            self.assertEqual(4, report["summary"]["metadata_ok"])
            self.assertEqual(2, report["summary"]["unique_panoramas"])
            self.assertEqual(2, report["summary"]["duplicates"])
            entries = svi.parse_todo(scoped_todo)
            self.assertEqual(["pano-a", "pano-b"], [entry.old_pano_id for entry in entries])
            self.assertEqual(svi.sha256_file(scoped_todo), report["todo"]["sha256"])
            self.assertEqual("old-live-plan\n", live_todo.read_text(encoding="utf-8"))
            text = report_path.read_text(encoding="utf-8")
            self.assertNotIn(client._api_key, text)
            self.assertFalse(list((workspace / "panos").glob("*.part")))

    def test_cli_dry_run_needs_no_key_and_does_not_publish_todo(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, report = self.workspace_request(Path(temporary))
            with redirect_stdout(io.StringIO()):
                status = cli_main(
                    [
                        "prepare-block-panos",
                        "--request",
                        str(request),
                        "--todo",
                        str(request.parent / "todo.list"),
                        "--report",
                        str(report),
                        "--dry-run",
                    ]
                )
            self.assertEqual(0, status)
            self.assertEqual("PLANNED", json.loads(report.read_text())["status"])
            self.assertFalse((request.parent / "todo.list").exists())
            self.assertFalse((workspace / "panos" / "todo.list").exists())

    def test_failed_plan_keeps_preexisting_live_todo_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, report = self.workspace_request(Path(temporary))
            live = workspace / "panos" / "todo.list"
            live.parent.mkdir()
            live.write_text("keep-this-live-plan\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no Street View panorama"):
                bp.prepare_block_panos(
                    request,
                    todo_path=request.parent / "todo.list",
                    report_path=report,
                    max_seeds=2,
                    client=FailingClient(),
                )
            self.assertEqual("keep-this-live-plan\n", live.read_text(encoding="utf-8"))
            self.assertFalse((request.parent / "todo.list").exists())

    def test_failed_sample_with_scoped_todo_keeps_live_todo_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, plan = self.workspace_request(Path(temporary))
            scoped = request.parent / "todo.list"
            bp.prepare_block_panos(
                request,
                todo_path=scoped,
                report_path=plan,
                max_seeds=1,
                client=SequenceClient(["pano-a"]),
            )
            live = workspace / "panos" / "todo.list"
            live.parent.mkdir()
            live.write_text("keep-live-on-sample-failure\n", encoding="utf-8")
            before = live.read_bytes()
            sample = svi.run_import(
                entries=svi.parse_todo(scoped),
                todo_path=scoped,
                output_dir=live.parent,
                report_path=request.parent / "sample_report.json",
                client=FailingClient(),
                dry_run=False,
                mode="sample",
                output_width=64,
                jpeg_quality=95,
                bilinear=True,
                overwrite=False,
                keep_panos_cache=False,
                radius_metres=50,
            )
            self.assertEqual(1, sample["summary"]["failed"])
            self.assertEqual(before, live.read_bytes())

    def test_promotion_backs_up_and_replaces_live_todo(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, plan = self.workspace_request(Path(temporary))
            scoped = request.parent / "todo.list"
            bp.prepare_block_panos(
                request,
                todo_path=scoped,
                report_path=plan,
                max_seeds=2,
                client=SequenceClient(["pano-a", "pano-b"]),
            )
            live = workspace / "panos" / "todo.list"
            live.parent.mkdir()
            live.write_text("previous-live\n", encoding="utf-8")
            previous_live = live.read_bytes()
            batch = request.parent / "batch_report.json"
            self.write_batch_report(batch, workspace, scoped)
            result = bp.promote_block_panos(
                request,
                todo_path=scoped,
                plan_report_path=plan,
                batch_report_path=batch,
                report_path=request.parent / "promotion_report.json",
            )
            self.assertEqual("PROMOTED", result["status"])
            self.assertEqual(scoped.read_bytes(), live.read_bytes())
            backup = Path(result["live_todo"]["backup"])
            self.assertEqual(previous_live, backup.read_bytes())

    def test_promotion_replace_failure_rolls_back_live_todo(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, plan = self.workspace_request(Path(temporary))
            scoped = request.parent / "todo.list"
            bp.prepare_block_panos(
                request,
                todo_path=scoped,
                report_path=plan,
                max_seeds=1,
                client=SequenceClient(["pano-a"]),
            )
            live = workspace / "panos" / "todo.list"
            live.parent.mkdir()
            live.write_text("previous-live\n", encoding="utf-8")
            batch = request.parent / "batch_report.json"
            promotion = request.parent / "promotion_report.json"
            self.write_batch_report(batch, workspace, scoped)
            with patch("myproject.block_panos._atomic_replace", side_effect=OSError("simulated")):
                with self.assertRaisesRegex(OSError, "simulated"):
                    bp.promote_block_panos(
                        request,
                        todo_path=scoped,
                        plan_report_path=plan,
                        batch_report_path=batch,
                        report_path=promotion,
                    )
            self.assertEqual("previous-live\n", live.read_text(encoding="utf-8"))
            self.assertEqual("FAILED", json.loads(promotion.read_text())["status"])

    def test_promotion_rejects_raw_live_todo_symlink_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, _ = self.workspace_request(Path(temporary))
            raw_live = workspace / "panos" / "todo.list"
            original = Path.is_symlink

            def pretend_live_is_link(path):
                return (
                    path.name.lower() == "todo.list" and path.parent.name.lower() == "panos"
                ) or original(path)

            with patch.object(Path, "is_symlink", pretend_live_is_link):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    bp.promote_block_panos(
                        request,
                        todo_path=request.parent / "todo.list",
                        plan_report_path=request.parent / "plan_report.json",
                        batch_report_path=request.parent / "batch_report.json",
                    )
            self.assertFalse(raw_live.parent.exists())

    def test_prepare_rejects_report_collision_without_overwriting_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, request, _ = self.workspace_request(Path(temporary))
            before = request.read_bytes()
            with self.assertRaisesRegex(ValueError, "conflicts"):
                bp.prepare_block_panos(
                    request,
                    todo_path=request.parent / "todo.list",
                    report_path=request,
                    dry_run=True,
                )
            self.assertEqual(before, request.read_bytes())

    def test_prepare_rejects_workspace_manifest_as_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, _ = self.workspace_request(Path(temporary))
            manifest = workspace / "manifest.json"
            before = manifest.read_bytes()
            with self.assertRaisesRegex(ValueError, "escapes"):
                bp.prepare_block_panos(
                    request,
                    todo_path=request.parent / "todo.list",
                    report_path=manifest,
                    dry_run=True,
                )
            self.assertEqual(before, manifest.read_bytes())

    def test_promotion_rejects_raw_report_symlink_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request, _ = self.workspace_request(Path(temporary))
            raw_report = request.parent / "promotion_report.json"
            original = Path.is_symlink

            def pretend_report_is_link(path):
                return path == raw_report or original(path)

            with patch.object(Path, "is_symlink", pretend_report_is_link):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    bp.promote_block_panos(
                        request,
                        todo_path=request.parent / "todo.list",
                        plan_report_path=request.parent / "plan_report.json",
                        batch_report_path=request.parent / "batch_report.json",
                        report_path=raw_report,
                    )
            self.assertFalse((workspace / "panos").exists())


if __name__ == "__main__":
    unittest.main()
