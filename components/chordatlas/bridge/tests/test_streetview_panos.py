import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from PIL import Image

HERE = Path(__file__).resolve().parent
BRIDGE_ROOT = HERE.parent
PROJECT_ROOT = BRIDGE_ROOT.parent
SRC = BRIDGE_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from myproject import streetview_panos as svi
from myproject.cli import main as cli_main


COLORS = {
    "north": (255, 0, 0),
    "east": (0, 255, 0),
    "south": (0, 0, 255),
    "west": (255, 255, 0),
    "up": (255, 0, 255),
    "down": (0, 255, 255),
}


class FakeClient:
    def __init__(self):
        self._api_key = "test-secret-key"

    def fetch_metadata(self, entry):
        return svi.PanoMetadata("new_id_with_underscore", 51.5, -0.14, "2026-01", "Google")

    def fetch_cube_face(self, pano_id, label, heading, pitch):
        return Image.new("RGB", (svi.FACE_SIZE, svi.FACE_SIZE), COLORS[label])


class ExplodingOpener:
    def __call__(self, request, timeout):
        raise RuntimeError(request.full_url)


class StreetViewImporterTests(unittest.TestCase):
    def sample_line(self):
        return (
            "51.51714159911714_-0.1427284412583933_33.64159393310547_"
            "165.4361267089844_89.26107788085938_-0.3073043823242188_"
            "Nw8L9qpwlOL7dhzmvzPtSg"
        )

    def test_repository_todo_has_33_valid_records(self):
        todo = PROJECT_ROOT / "datasets" / "regent_osm" / "panos" / "todo.list"
        entries = svi.parse_todo(todo)
        self.assertEqual(33, len(entries))
        self.assertEqual(1, entries[0].line_number)

    def test_todo_parser_preserves_pano_id_underscores(self):
        line = self.sample_line().rsplit("_", 1)[0] + "_id_with_under_scores"
        entry = svi.parse_todo_line(line, 7)
        self.assertEqual("id_with_under_scores", entry.old_pano_id)
        self.assertEqual(7, entry.line_number)

    def test_current_metadata_pano_id_dot_is_filename_safe(self):
        entry = svi.parse_todo_line(self.sample_line(), 1)
        metadata = svi.PanoMetadata("current.id-with_safe_chars", 51.5, -0.14, None, None)
        name = svi.build_output_name(entry, metadata)
        self.assertTrue(name.endswith("_current.id-with_safe_chars.jpg"))

    def test_filename_is_parseable_and_level_pose_survives_loader_filter(self):
        entry = svi.parse_todo_line(self.sample_line(), 1)
        metadata = svi.PanoMetadata("fresh_id", 51.5001, -0.1402, None, None)
        name = svi.build_output_name(entry, metadata)
        fields = name[:-4].split("_", 6)
        self.assertEqual(7, len(fields))
        for value in fields[:6]:
            float(value)
        self.assertEqual("fresh_id", fields[6])
        self.assertEqual(180.0, float(fields[3]))
        self.assertEqual(90.0, float(fields[4]))
        self.assertEqual(0.001, float(fields[5]))
        self.assertTrue(svi.chordatlas_loader_would_drop(90.0, 0.0))
        self.assertFalse(
            svi.chordatlas_loader_would_drop(
                svi.FILENAME_TILT_DEG, svi.FILENAME_ROLL_DEG
            )
        )

    def test_coordinate_modes_encode_the_required_loader_yaw(self):
        entry = svi.parse_todo_line(self.sample_line(), 1)
        metadata = svi.PanoMetadata("fresh_id", 51.5001, -0.1402, None, None)
        local = svi.build_output_name(entry, metadata, "myproject-local")
        original = svi.build_output_name(entry, metadata, "original-geographic")
        self.assertEqual(180.0, float(local[:-4].split("_", 6)[3]))
        self.assertEqual(0.0, float(original[:-4].split("_", 6)[3]))
        self.assertEqual(180.0, svi.FILENAME_HEADING_DEG)

    def test_chordatlas_uv_cardinals(self):
        cases = {
            (0.0, 0.5): (0.0, 0.0, -1.0),
            (0.25, 0.5): (-1.0, 0.0, 0.0),
            (0.5, 0.5): (0.0, 0.0, 1.0),
            (0.75, 0.5): (1.0, 0.0, 0.0),
            (0.5, 0.0): (0.0, 1.0, 0.0),
            (0.5, 1.0): (0.0, -1.0, 0.0),
        }
        for uv, expected in cases.items():
            actual = svi.equirect_uv_to_enu(*uv)
            for got, wanted in zip(actual, expected):
                self.assertAlmostEqual(wanted, got, places=7)

    def test_cube_axis_selection(self):
        axes = {
            "north": (0, 0, 1),
            "east": (1, 0, 0),
            "south": (0, 0, -1),
            "west": (-1, 0, 0),
            "up": (0, 1, 0),
            "down": (0, -1, 0),
        }
        for label, axis in axes.items():
            selected, x, y = svi.cube_face_coordinates(*axis)
            self.assertEqual(label, selected)
            self.assertAlmostEqual(0, x)
            self.assertAlmostEqual(0, y)

    def test_composed_image_has_expected_cardinal_colours(self):
        faces = {label: Image.new("RGB", (16, 16), colour) for label, colour in COLORS.items()}
        pano = svi.compose_equirectangular(faces, 400, bilinear=True)
        self.assertEqual((400, 200), pano.size)
        self.assertEqual(COLORS["south"], pano.getpixel((0, 100)))
        self.assertEqual(COLORS["west"], pano.getpixel((100, 100)))
        self.assertEqual(COLORS["north"], pano.getpixel((200, 100)))
        self.assertEqual(COLORS["east"], pano.getpixel((300, 100)))
        self.assertEqual(COLORS["up"], pano.getpixel((200, 0)))
        self.assertEqual(COLORS["down"], pano.getpixel((200, 199)))

    def test_key_is_redacted_from_transport_failure(self):
        key = "AIza-not-for-a-report"
        client = svi.GoogleStreetViewClient(key, retries=0, opener=ExplodingOpener())
        entry = svi.parse_todo_line(self.sample_line(), 1)
        with self.assertRaises(svi.ImportFailure) as caught:
            client.fetch_metadata(entry)
        self.assertNotIn(key, str(caught.exception))
        self.assertIn("<redacted>", str(caught.exception))

    def test_dry_run_needs_no_key_and_writes_structured_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo = root / "todo.list"
            todo.write_text(self.sample_line() + "\n", encoding="utf-8")
            report_path = root / "dry.json"
            entry = svi.parse_todo(todo)
            report = svi.run_import(
                entries=entry,
                todo_path=todo,
                output_dir=root,
                report_path=report_path,
                client=None,
                dry_run=True,
                mode="dry-run",
                output_width=64,
                jpeg_quality=95,
                bilinear=True,
                overwrite=False,
                keep_panos_cache=False,
                radius_metres=50,
            )
            self.assertEqual("planned", report["items"][0]["status"])
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertFalse(loaded["config"]["api_key_recorded"])
            self.assertEqual("myproject-local", loaded["config"]["coordinate_mode"])
            self.assertEqual(180.0, loaded["config"]["filename_orientation"]["heading"])
            self.assertTrue(loaded["chordatlas_layer"]["layer_required"])
            self.assertFalse(loaded["chordatlas_layer"]["created_by_importer"])
            self.assertNotIn("AIza", report_path.read_text(encoding="utf-8"))

    def test_fake_live_run_atomically_publishes_strict_jpeg_and_backs_up_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo = root / "todo.list"
            todo.write_text(self.sample_line() + "\n", encoding="utf-8")
            cache = root / "panos.xml"
            cache.write_text("old cache", encoding="utf-8")
            report_path = root / "sample.json"
            report = svi.run_import(
                entries=svi.parse_todo(todo),
                todo_path=todo,
                output_dir=root,
                report_path=report_path,
                client=FakeClient(),
                dry_run=False,
                mode="sample",
                output_width=64,
                jpeg_quality=95,
                bilinear=False,
                overwrite=False,
                keep_panos_cache=False,
                radius_metres=50,
            )
            self.assertEqual(1, report["summary"]["succeeded"])
            output = Path(report["items"][0]["output"])
            validation = svi.validate_jpeg(output, 64)
            self.assertEqual((64, 32), (validation["width"], validation["height"]))
            self.assertFalse(cache.exists())
            self.assertEqual("backed_up", report["cache"]["status"])
            self.assertTrue(Path(report["cache"]["backup"]).exists())
            self.assertFalse(list(root.glob("*.part")))
            self.assertNotIn("test-secret-key", report_path.read_text(encoding="utf-8"))
            verified = svi.verify_sample_report(
                report_path, todo, 64, root, "myproject-local"
            )
            self.assertEqual(output.resolve(), Path(verified["output"]))
            with self.assertRaisesRegex(ValueError, "coordinate mode"):
                svi.verify_sample_report(
                    report_path, todo, 64, root, "original-geographic"
                )

    def test_live_batch_is_locked_before_any_api_key_or_network_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo = root / "todo.list"
            todo.write_text(self.sample_line() + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "batch is locked"):
                svi.import_streetview_panos(
                    todo_path=todo,
                    output_dir=root / "panos",
                    all_records=True,
                )

    def test_live_sample_accepts_key_only_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo = root / "todo.list"
            todo.write_text(self.sample_line() + "\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, svi.KEY_ENV):
                    svi.import_streetview_panos(
                        todo_path=todo,
                        output_dir=root / "panos",
                    )

    def test_myproject_cli_dry_run_uses_explicit_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo = root / "todo.list"
            output = root / "panos"
            todo.write_text(self.sample_line() + "\n", encoding="utf-8")
            captured = io.StringIO()
            with redirect_stdout(captured):
                status = cli_main(
                    [
                        "import-streetview-panos",
                        "--todo",
                        str(todo),
                        "--output",
                        str(output),
                        "--dry-run",
                        "--output-width",
                        "64",
                    ]
                )
            self.assertEqual(0, status)
            report = json.loads((output / "streetview_dry_run_report.json").read_text(encoding="utf-8"))
            self.assertEqual("dry-run", report["mode"])
            self.assertNotIn("AIza", captured.getvalue())

    def test_myproject_cli_defaults_to_configured_workspace_panos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo = root / "todo.list"
            todo.write_text(self.sample_line() + "\n", encoding="utf-8")
            config = root / "project.json"
            output_root = root / "projects"
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "project_id": "pano_test",
                        "output_root": str(output_root),
                        "area": {"target_bbox_wgs84": [-0.2, 51.0, -0.1, 51.1]},
                        "paths": {
                            "chordatlas_root": str(root / "chordatlas"),
                            "sat3dgen_root": str(root / "sat3dgen"),
                            "data_builder_root": str(root / "data_builder"),
                            "facade_pytorch_root": str(root / "facade_pytorch"),
                            "frankengan_root": str(root / "frankengan"),
                            "conda_executable": str(root / "conda.exe"),
                        },
                        "footprints": {"source_geojson": str(root / "buildings.geojson")},
                        "mesh": {"mode": "existing", "source_obj": str(root / "scene.obj")},
                        "panoramas": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                status = cli_main(
                    [
                        "--config",
                        str(config),
                        "import-streetview-panos",
                        "--todo",
                        str(todo),
                        "--dry-run",
                        "--output-width",
                        "64",
                    ]
                )
            self.assertEqual(0, status)
            self.assertTrue(
                (output_root / "pano_test" / "panos" / "streetview_dry_run_report.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
