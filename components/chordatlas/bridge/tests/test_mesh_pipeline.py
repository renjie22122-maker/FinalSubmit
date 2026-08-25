from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT / "src"))

from myproject.mesh_pipeline import (  # noqa: E402
    CommandBuildError,
    CommandExecutionError,
    GeoBBox,
    ObjInspectionError,
    TileOriginError,
    TopLevelPipelineRequest,
    build_top_level_conda_command,
    derive_tile_origin,
    inspect_obj,
    run_top_level_pipeline,
)


class InspectObjTests(unittest.TestCase):
    def test_streams_counts_materials_bounds_and_exact_small_percentiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj = Path(temporary) / "textured.obj"
            obj.write_text(
                "\n".join(
                    [
                        '# material declaration',
                        'mtllib "building materials.mtl" second.mtl',
                        'v 0 0 0 1 0 0',
                        'v 2 10 -1',
                        'v -3 20 4',
                        'v 1 30 2',
                        'usemtl "brick wall"',
                        'f 1/1/1 2/2/1 3/3/1 4/4/1',
                        'usemtl roof',
                        'f -4 -3 -2',
                    ]
                ),
                encoding="utf-8",
            )

            result = inspect_obj(obj, y_percentiles=(0, 50, 100), y_sample_size=100)

            self.assertEqual(result.vertex_count, 4)
            self.assertEqual(result.face_count, 2)
            self.assertEqual(result.triangulated_face_count, 3)
            self.assertEqual(result.material_libraries, ("building materials.mtl", "second.mtl"))
            self.assertEqual(result.materials, ("brick wall", "roof"))
            self.assertEqual(result.material_count, 2)
            self.assertIsNotNone(result.bounds)
            assert result.bounds is not None
            self.assertEqual(result.bounds.minimum, (-3.0, 0.0, -1.0))
            self.assertEqual(result.bounds.maximum, (2.0, 30.0, 4.0))
            self.assertEqual(result.bounds.size, (5.0, 30.0, 5.0))
            self.assertEqual(result.y_sample_size, 4)
            self.assertEqual(result.percentile(0), 0.0)
            self.assertEqual(result.percentile(50), 15.0)
            self.assertEqual(result.percentile(100), 30.0)
            self.assertEqual(result.to_dict()["vertex_count"], 4)

    def test_reservoir_is_bounded_while_bounds_remain_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj = Path(temporary) / "large.obj"
            obj.write_text(
                "\n".join(f"v {index} {index * 2} {-index}" for index in range(100)),
                encoding="utf-8",
            )

            result = inspect_obj(obj, y_percentiles=(10, 90), y_sample_size=7, random_seed=4)

            self.assertEqual(result.vertex_count, 100)
            self.assertEqual(result.y_sample_size, 7)
            assert result.bounds is not None
            self.assertEqual(result.bounds.minimum, (0.0, 0.0, -99.0))
            self.assertEqual(result.bounds.maximum, (99.0, 198.0, 0.0))

    def test_empty_obj_has_no_bounds_or_percentiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj = Path(temporary) / "empty.obj"
            obj.write_text("# empty\n", encoding="utf-8")

            result = inspect_obj(obj)

            self.assertEqual(result.vertex_count, 0)
            self.assertIsNone(result.bounds)
            self.assertEqual(result.y_percentiles, ())

    def test_bad_face_reports_path_line_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            obj = Path(temporary) / "bad.obj"
            obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n", encoding="utf-8")

            with self.assertRaises(ObjInspectionError) as raised:
                inspect_obj(obj)

            message = str(raised.exception)
            self.assertIn("bad.obj:4", message)
            self.assertIn("outside the 3 vertices", message)

    def test_missing_obj_is_explicit(self) -> None:
        missing = Path(tempfile.gettempdir()) / "definitely-missing-mesh-pipeline-test.obj"
        with self.assertRaisesRegex(ObjInspectionError, "does not exist"):
            inspect_obj(missing)


class TileOriginTests(unittest.TestCase):
    def test_derives_independent_minimum_latitude_and_longitude(self) -> None:
        result = derive_tile_origin(
            [
                "sat_51.508045_-0.125568.obj",
                r"E:\cache\sat_51.507180_-0.126958.obj.json",
                "sat_51.507613_-0.126263",
            ]
        )

        self.assertEqual(result.tile_count, 3)
        self.assertAlmostEqual(result.origin_latitude, 51.507180)
        self.assertAlmostEqual(result.origin_longitude, -0.126958)
        self.assertEqual(result.tiles[1].stem, "sat_51.507180_-0.126958")

    def test_single_string_is_one_tile_not_an_iterable_of_characters(self) -> None:
        result = derive_tile_origin("sat_51.5_-0.13.png")
        self.assertEqual(result.tile_count, 1)
        self.assertEqual((result.origin_latitude, result.origin_longitude), (51.5, -0.13))

    def test_invalid_or_empty_tile_input_is_explicit(self) -> None:
        with self.assertRaisesRegex(TileOriginError, "at least one"):
            derive_tile_origin([])
        with self.assertRaisesRegex(TileOriginError, "expected sat_<latitude>_<longitude>"):
            derive_tile_origin(["tile_51.5_-0.13.obj"])
        with self.assertRaisesRegex(TileOriginError, "latitude"):
            derive_tile_origin(["sat_91.0_0.0"])


class TopLevelCommandTests(unittest.TestCase):
    DRIVER = (BRIDGE_ROOT / "top_level_mesh_driver.py").resolve()

    def _sat3dgen_root(self, base: Path) -> Path:
        root = base / "Sat3DGen"
        pipeline_dir = root / "mesh_pipeline"
        pipeline_dir.mkdir(parents=True)
        (pipeline_dir / "pipeline.py").write_text("# top-level pipeline marker\n", encoding="utf-8")
        return root

    def _request(
        self, root: Path, work_dir: Path, **overrides
    ) -> TopLevelPipelineRequest:
        values = {
            "bbox": GeoBBox(-0.136, 51.509, -0.133, 51.512),
            "work_dir": work_dir,
            "sat3dgen_root": root,
            "driver_path": self.DRIVER,
            "name": "regent_test",
            "satellite_dirs": (work_dir / "satellite input",),
            "mesh_dirs": (work_dir / "mesh input",),
            "osm_dir": work_dir / "osm",
            "dsm_dir": work_dir / "dsm",
            "dsm_files": ("tile-a.tif", "tile-b.tif"),
            "dsm_crs": "EPSG:27700",
            "tile_source": "data_builder_grid",
            "lat_step": 0.000534219395032275,
            "lon_step": 0.000858302958601891,
            "overlap_ratio": 0.0,
            "allow_partial": True,
            "osm_prealign": True,
            "apply_dsm": True,
        }
        values.update(overrides)
        return TopLevelPipelineRequest(**values)

    def _assert_top_level_only(self, argv: tuple[str, ...] | list[str]) -> None:
        self.assertEqual(Path(argv[7]).resolve(), self.DRIVER)
        self.assertEqual(Path(argv[7]).name, "top_level_mesh_driver.py")
        self.assertNotIn("-m", argv)
        self.assertNotIn("mesh_generate_merge_pipeline", " ".join(argv).lower())

    def test_builds_top_level_driver_conda_argv_without_nested_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._sat3dgen_root(base)
            work = base / "work dir"
            request = self._request(root, work)

            command = build_top_level_conda_command(request)

            self.assertEqual(
                command.argv[:8],
                (
                    "conda",
                    "run",
                    "--no-capture-output",
                    "-n",
                    "sat3dgen",
                    "python",
                    "-B",
                    str(self.DRIVER),
                ),
            )
            self._assert_top_level_only(command.argv)
            root_index = command.argv.index("--sat3dgen-root")
            self.assertEqual(command.argv[root_index + 1], str(root.resolve()))
            bbox_index = command.argv.index("--bbox")
            self.assertEqual(
                command.argv[bbox_index + 1 : bbox_index + 5],
                ("-0.136", "51.509", "-0.133", "51.512"),
            )
            self.assertIn("--satellite-dir", command.argv)
            self.assertIn("--mesh-dir", command.argv)
            self.assertIn("--osm-dir", command.argv)
            self.assertIn("--dsm-dir", command.argv)
            self.assertEqual(command.argv.count("--dsm-file"), 2)
            self.assertEqual(command.argv[command.argv.index("--dsm-crs") + 1], "EPSG:27700")
            self.assertIn("--allow-partial", command.argv)
            self.assertIn("--osm-prealign", command.argv)
            self.assertIn("--apply-dsm", command.argv)
            self.assertEqual(command.argv[command.argv.index("--tile-source") + 1], "data_builder_grid")
            self.assertNotIn("--api-key", command.argv)
            self.assertEqual(command.cwd, root.resolve())

    def test_default_run_is_dry_and_never_starts_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._sat3dgen_root(base)
            request = self._request(root, base / "output", conda_executable="missing-conda")

            with patch("myproject.mesh_pipeline.subprocess.run") as run_mock:
                result = run_top_level_pipeline(request)

            run_mock.assert_not_called()
            self.assertFalse(result.executed)
            self.assertIsNone(result.returncode)
            self.assertTrue(result.ok)
            self._assert_top_level_only(result.command.argv)

    def test_explicit_execution_returns_outputs_and_warnings_structurally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._sat3dgen_root(base)
            work = base / "output"
            request = self._request(root, work)
            command = build_top_level_conda_command(request)
            scene = work / "final" / "regent_test_scene.obj"
            completed = subprocess.CompletedProcess(
                command.argv,
                0,
                stdout=f"scene: {scene}\nwarning: DSM correction disabled\n",
                stderr="diagnostic log",
            )

            with patch("myproject.mesh_pipeline.subprocess.run", return_value=completed) as run_mock:
                result = run_top_level_pipeline(request, dry_run=False)

            self.assertTrue(result.executed)
            self.assertTrue(result.ok)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.output_files["scene"], scene)
            self.assertEqual(result.warnings, ("DSM correction disabled",))
            self.assertEqual(run_mock.call_args.kwargs["shell"], False)
            self.assertEqual(run_mock.call_args.kwargs["cwd"], str(root.resolve()))
            self._assert_top_level_only(run_mock.call_args.args[0])

    def test_check_true_raises_with_structured_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._sat3dgen_root(base)
            request = self._request(root, base / "output")
            command = build_top_level_conda_command(request)
            completed = subprocess.CompletedProcess(command.argv, 7, stdout="", stderr="mesh missing")

            with patch("myproject.mesh_pipeline.subprocess.run", return_value=completed):
                with self.assertRaises(CommandExecutionError) as raised:
                    run_top_level_pipeline(request, dry_run=False, check=True)

            self.assertIsNotNone(raised.exception.result)
            assert raised.exception.result is not None
            self.assertEqual(raised.exception.result.returncode, 7)
            self.assertIn("mesh missing", str(raised.exception))

    def test_rejects_bad_bbox_and_missing_sat3dgen_root(self) -> None:
        with self.assertRaises(CommandBuildError):
            GeoBBox(-0.13, 51.5, -0.14, 51.6)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            request = self._request(base / "missing", base / "output")
            with self.assertRaisesRegex(CommandBuildError, "does not exist"):
                build_top_level_conda_command(request)


if __name__ == "__main__":
    unittest.main()
