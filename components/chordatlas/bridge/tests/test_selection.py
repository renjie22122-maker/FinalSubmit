from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import inspect
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT / "src"))

from myproject.cli import main as cli_main  # noqa: E402
from myproject.geo import LocalFrame  # noqa: E402
from myproject.mesh_pipeline import (  # noqa: E402
    GeoBBox,
    TopLevelPipelineRequest,
    build_top_level_conda_command,
)
from myproject.selection import (  # noqa: E402
    ObjMesh,
    PlannedTile,
    SelectedFootprint,
    _local_ground,
    _verified_https_context,
    assess_footprint_completeness,
    assign_building_faces,
    build_selection,
    crop_face_indices_bbox,
    lonlat_to_web_mercator,
    plan_web_mercator_tiles,
    redact_text,
    rebase_and_ground_mesh,
    sample_bbox,
    tile_coverage_ratio,
    validate_png,
    web_mercator_to_lonlat,
)
from myproject.top_level_mesh_driver import (  # noqa: E402
    MERGE_STAGE_ORDER as DRIVER_MERGE_STAGE_ORDER,
    _convert_glb_to_obj,
    _load_exact_tile_manifest,
    _needs_inference_connection,
    run as run_top_level_driver,
)


def _png(path: Path, width: int, height: int) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind)
        crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    scanline = b"\x00" + b"\x00\x00\x00" * width
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanline * height))
        + chunk(b"IEND", b"")
    )


def _workspace(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "frame": {
                    "origin_lat": 51.5074,
                    "origin_lon": -0.1278,
                    "units": "m",
                    "axes": {"x": "east", "y": "up", "z": "south"},
                },
                "chordatlas": {"conda_environment": "sat3dgen"},
            }
        ),
        encoding="utf-8",
    )
    output = workspace / "generated_blocks" / "abc123"
    output.mkdir(parents=True)
    request = output / "request.json"
    request.write_text(
        json.dumps(
            {
                "workspace": str(workspace),
                "selection_id": "selection-abc123",
                "footprints": [
                    {
                        "id": "footprint-one",
                        "points": [[0, 0], [12, 0], [12, 9], [0, 9]],
                    }
                ],
                "options": {
                    "block_mesh_padding": 5,
                    "require_complete_buildings": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return workspace, request


class PlannerAndValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frame = LocalFrame(51.5074, -0.1278)
        self.footprint = SelectedFootprint(
            "building-a", ((0.0, 0.0), (12.0, 0.0), (12.0, 9.0), (0.0, 9.0))
        )

    def test_fixed_web_mercator_allowlist_covers_footprint_plus_thirty_metres(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, first_plan = plan_web_mercator_tiles(
                [self.footprint], self.frame, root / "job-a", padding_m=1
            )
            second, second_plan = plan_web_mercator_tiles(
                [self.footprint], self.frame, root / "job-b", padding_m=30
            )

            self.assertEqual([tile.tile_id for tile in first], [tile.tile_id for tile in second])
            self.assertEqual([tile.stem for tile in first], [tile.stem for tile in second])
            self.assertEqual(first_plan["padding_m"], 30.0)
            bounds = (-30.0, -30.0, 42.0, 39.0)
            ratio = tile_coverage_ratio(sample_bbox(bounds, 2.5), self.frame, first)
            self.assertAlmostEqual(ratio, 1.0)

    def test_partial_tile_set_is_never_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tiles, _ = plan_web_mercator_tiles(
                [self.footprint], self.frame, Path(temporary), padding_m=30
            )
            samples = sample_bbox((-30.0, -30.0, 42.0, 39.0), 2.0)
            self.assertEqual(tile_coverage_ratio(samples, self.frame, tiles, []), 0.0)
            self.assertEqual(
                tile_coverage_ratio(samples, self.frame, tiles, [tile.stem for tile in tiles]),
                1.0,
            )

    def test_raw_satellite_edge_does_not_count_after_mesh_crop(self) -> None:
        lon, lat = self.frame.to_wgs84(0.0, 0.0)
        center_x, center_y = lonlat_to_web_mercator(lon, lat)
        edge_lon, edge_lat = web_mercator_to_lonlat(center_x + 9.5, center_y)
        edge_local = self.frame.to_local(edge_lon, edge_lat)
        tile = PlannedTile(
            tile_id="edge",
            grid_x=0,
            grid_y=0,
            zoom=20,
            size_px=640,
            latitude=lat,
            longitude=lon,
            stem="sat_51.507400_-0.127800",
            bounds_mercator_m=(center_x - 10, center_y - 10, center_x + 10, center_y + 10),
            effective_mesh_bounds_mercator_m=(
                center_x - 9,
                center_y - 9,
                center_x + 9,
                center_y + 9,
            ),
            satellite_path=Path("sat.png"),
            mesh_path=Path("mesh.obj"),
        )
        self.assertEqual(tile_coverage_ratio([edge_local], self.frame, [tile]), 0.0)

    def test_png_parser_checks_crc_dimensions_and_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sat.png"
            _png(path, 8, 8)
            report = validate_png(path, 8)
            self.assertEqual((report["width"], report["height"]), (8, 8))
            damaged = bytearray(path.read_bytes())
            damaged[-1] ^= 1
            path.write_bytes(damaged)
            with self.assertRaisesRegex(RuntimeError, "CRC"):
                validate_png(path, 8)


class GeometryTests(unittest.TestCase):
    def _mesh(self, partial: bool = False) -> ObjMesh:
        # Four local ground references followed by a 10x10 roof at Y=5.
        vertices = [
            (-5.0, 0.0, -5.0),
            (15.0, 0.0, -5.0),
            (15.0, 0.0, 15.0),
            (-5.0, 0.0, 15.0),
            (0.0, 5.0, 0.0),
            (10.0, 5.0, 0.0),
            (10.0, 5.0, 10.0),
            (0.0, 5.0, 10.0),
        ]
        faces = [(4, 5, 6)] if partial else [(4, 5, 6), (4, 6, 7)]
        return ObjMesh(vertices, faces)

    def test_above_ground_projected_coverage_excludes_partial_model(self) -> None:
        footprint = SelectedFootprint(
            "square", ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        )
        complete = assess_footprint_completeness(
            self._mesh(False), footprint, sample_spacing_m=1.0, minimum_projected_coverage=0.85
        )
        partial = assess_footprint_completeness(
            self._mesh(True), footprint, sample_spacing_m=1.0, minimum_projected_coverage=0.85
        )
        self.assertTrue(complete["complete"])
        self.assertFalse(partial["complete"])
        self.assertLess(partial["projected_coverage_ratio"], 0.85)

    def test_fast_bbox_crop_is_a_single_coarse_spatial_gate(self) -> None:
        footprint = SelectedFootprint(
            "square", ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        )
        mesh = ObjMesh(
            [
                (1.0, 0.0, 1.0),
                (2.0, 0.0, 1.0),
                (1.0, 0.0, 2.0),
                (20.0, 0.0, 20.0),
                (21.0, 0.0, 20.0),
                (20.0, 0.0, 21.0),
            ],
            [(0, 1, 2), (3, 4, 5)],
        )

        self.assertEqual(crop_face_indices_bbox(mesh, footprint), {0})

    def test_building_assignment_keeps_boundary_crossing_face(self) -> None:
        footprint = SelectedFootprint(
            "square", ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        )
        mesh = ObjMesh(
            [
                (-3.0, 5.0, 4.0),
                (1.0, 5.0, 4.0),
                (-3.0, 5.0, 6.0),
                (-5.0, 0.0, -5.0),
                (15.0, 0.0, 15.0),
            ],
            [(0, 1, 2)],
        )

        assignments, _ = assign_building_faces(mesh, [footprint])

        self.assertEqual(assignments["square"], {0})

    def test_building_assignment_gives_overlap_one_deterministic_owner(self) -> None:
        first = SelectedFootprint(
            "a", ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        )
        second = SelectedFootprint(
            "b", ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        )
        mesh = ObjMesh([(2.0, 5.0, 2.0), (4.0, 5.0, 2.0), (2.0, 5.0, 4.0)], [(0, 1, 2)])

        assignments, _ = assign_building_faces(mesh, [second, first])

        self.assertEqual(assignments["a"], {0})
        self.assertEqual(assignments["b"], set())

    def test_building_assignment_rejects_outside_point_contact(self) -> None:
        footprint = SelectedFootprint(
            "square", ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        )
        mesh = ObjMesh([(-2.0, 5.0, -1.0), (0.0, 5.0, 0.0), (-1.0, 5.0, -2.0)], [(0, 1, 2)])

        assignments, _ = assign_building_faces(mesh, [footprint])

        self.assertEqual(assignments["square"], set())

    def test_local_ground_excludes_roof_vertices_inside_footprint(self) -> None:
        footprint = SelectedFootprint(
            "square", ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))
        )
        mesh = ObjMesh(
            [(2.0, 20.0, 2.0), (8.0, 20.0, 8.0), (-2.0, 2.0, 5.0), (12.0, 2.0, 5.0)],
            [],
        )

        self.assertEqual(_local_ground(mesh, footprint), 2.0)

    def test_local_ground_rejects_neighbouring_roof_as_ground(self) -> None:
        footprint = SelectedFootprint(
            "square", ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0))
        )
        mesh = ObjMesh(
            [
                (1.0, 4.0, 1.0),
                (3.0, 4.0, 3.0),
                (5.0, 5.0, 1.0),
                (6.0, 5.0, 2.0),
                (5.0, 5.0, 3.0),
            ],
            [(2, 3, 4)],
        )

        self.assertEqual(_local_ground(mesh, footprint), 0.0)

    def test_job_origin_rebases_to_workspace_and_ground_is_zero(self) -> None:
        mesh = ObjMesh(
            [(0.0, 10.0, 0.0), (1.0, 20.0, 0.0), (0.0, 10.0, 1.0)],
            [(0, 1, 2)],
        )
        frame = LocalFrame(51.0, -0.2)
        transformed, report = rebase_and_ground_mesh(
            mesh, 51.001, -0.199, frame, ground_percentile=0
        )
        expected_x = 0.001 * frame.meters_per_degree_lon
        expected_z = -0.001 * 111_320.0
        self.assertAlmostEqual(transformed.vertices[0][0], expected_x, places=5)
        self.assertAlmostEqual(transformed.vertices[0][2], expected_z, places=5)
        self.assertEqual(min(vertex[1] for vertex in transformed.vertices), 0.0)
        self.assertEqual(report["job_ground_reference_y"], 10.0)


class CliAndAllowlistTests(unittest.TestCase):
    def test_cli_dry_run_writes_plan_without_satellite_mesh_or_cropped_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request = _workspace(Path(temporary))
            output = StringIO()
            with redirect_stdout(output):
                returncode = cli_main(["build-selection", "--request", str(request)])
            report = json.loads(output.getvalue())
            self.assertEqual(returncode, 0)
            self.assertEqual(report["status"], "PLANNED")
            manifest = workspace / "_selection_jobs" / "abc123" / "tile_manifest.json"
            self.assertTrue(manifest.is_file())
            self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["status"], "PLANNED")
            self.assertFalse((request.parent / "cropped.obj").exists())
            self.assertFalse((request.parent / "result.json").exists())
            self.assertEqual(
                json.loads((manifest.parent / "result.json").read_text())["status"], "PLANNED"
            )
            planned_result = json.loads((manifest.parent / "result.json").read_text())
            tile_plan = json.loads(manifest.read_text(encoding="utf-8"))
            expected_order = [
                "coordinate_transform",
                "osm_semantic_prealign",
                "remove_bottom_faces",
                "stitch_tiles",
                "dsm_height_correction",
                "export_scene",
            ]
            self.assertEqual(planned_result["pipeline_contract_version"], "osm-prealign-v1")
            self.assertTrue(planned_result["osm_prealign"])
            self.assertEqual(planned_result["merge_stage_order"], expected_order)
            self.assertEqual(tile_plan["pipeline_contract_version"], "osm-prealign-v1")
            self.assertTrue(all(tile["reuse_existing_mesh"] for tile in tile_plan["tiles"]))

    def test_failed_retry_preserves_previous_ready_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, request = _workspace(Path(temporary))
            output = request.parent
            old_files = {
                "cropped.obj": "old cropped\n",
                "gis.obj": "old gis\n",
                "gis_footprints.obj": "old gis footprints\n",
                "result.json": json.dumps({"status": "READY", "generation": "old"}),
            }
            for name, content in old_files.items():
                (output / name).write_text(content, encoding="utf-8")
            minimesh = output / "minimesh"
            minimesh.mkdir()
            (minimesh / "index.xml").write_text("old minimesh", encoding="utf-8")
            failure = {
                "tile_id": "x",
                "stem": "sat_0.000000_0.000000",
                "error": {"code": "network", "message": "offline"},
            }

            with patch("myproject.selection.ensure_satellites", return_value=([], [failure])):
                report = build_selection(request, execute=True)

            self.assertEqual(report["status"], "FAILED")
            for name, content in old_files.items():
                self.assertEqual((output / name).read_text(encoding="utf-8"), content)
            self.assertEqual((minimesh / "index.xml").read_text(encoding="utf-8"), "old minimesh")
            job_result = workspace / "_selection_jobs" / "abc123" / "result.json"
            self.assertEqual(json.loads(job_result.read_text(encoding="utf-8"))["status"], "FAILED")

    def test_exact_manifest_does_not_reuse_stale_same_name_or_extra_obj(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / "job"
            satellite = work / "satellite" / "sat_51.500000_-0.120000.png"
            _png(satellite, 8, 8)
            mesh = work / "meshes" / "sat_51.500000_-0.120000" / "sat_51.500000_-0.120000.obj"
            mesh.parent.mkdir(parents=True)
            mesh.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n" + "#" * 1200, encoding="utf-8")
            extra = work / "meshes" / "sat_51.600000_-0.130000" / "sat_51.600000_-0.130000.obj"
            extra.parent.mkdir(parents=True)
            extra.write_text(mesh.read_text(encoding="utf-8"), encoding="utf-8")
            manifest = work / "tile_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "tiles": [
                            {
                                "stem": "sat_51.500000_-0.120000",
                                "lat": 51.5,
                                "lon": -0.12,
                                "satellite_path": str(satellite),
                                "mesh_path": str(mesh),
                                "reuse_existing_mesh": False,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            desired, satellites, meshes, destinations = _load_exact_tile_manifest(manifest, work)

            self.assertEqual([item[0] for item in desired], ["sat_51.500000_-0.120000"])
            self.assertEqual(set(satellites), {"sat_51.500000_-0.120000"})
            self.assertEqual(meshes, {})
            self.assertEqual(destinations["sat_51.500000_-0.120000"], mesh.resolve())
            self.assertNotIn("sat_51.600000_-0.130000", destinations)

    def test_exact_manifest_is_passed_to_only_top_level_driver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sat3dgen = root / "Sat3DGen"
            pipeline = sat3dgen / "mesh_pipeline"
            pipeline.mkdir(parents=True)
            (pipeline / "pipeline.py").write_text("# marker\n", encoding="utf-8")
            manifest = root / "tile_manifest.json"
            manifest.write_text('{"tiles":[]}', encoding="utf-8")
            driver = BRIDGE_ROOT / "top_level_mesh_driver.py"
            request = TopLevelPipelineRequest(
                bbox=GeoBBox(-0.13, 51.50, -0.12, 51.51),
                work_dir=root / "work",
                sat3dgen_root=sat3dgen,
                driver_path=driver,
                tile_source="exact_manifest",
                exact_tile_manifest=manifest,
            )
            command = build_top_level_conda_command(request)
            self.assertIn("--exact-tile-manifest", command.argv)
            self.assertNotIn("mesh_generate_merge_pipeline", " ".join(command.argv))
            self.assertEqual(Path(command.argv[7]).resolve(), driver.resolve())

            direct_request = TopLevelPipelineRequest(
                bbox=GeoBBox(-0.13, 51.50, -0.12, 51.51),
                work_dir=root / "work-direct",
                sat3dgen_root=sat3dgen,
                driver_path=driver,
                tile_source="exact_manifest",
                exact_tile_manifest=manifest,
                use_current_python=True,
            )
            direct = build_top_level_conda_command(direct_request)
            self.assertEqual(Path(direct.argv[0]).resolve(), Path(sys.executable).resolve())
            self.assertTrue(Path(direct.argv[0]).name.lower().startswith("python"))
            self.assertEqual(direct.argv[1:3], ("-B", str(driver.resolve())))
            self.assertNotIn("run", direct.argv[:3])

            contracted = TopLevelPipelineRequest(
                bbox=GeoBBox(-0.13, 51.50, -0.12, 51.51),
                work_dir=root / "work-contract",
                sat3dgen_root=sat3dgen,
                driver_path=driver,
                tile_source="exact_manifest",
                exact_tile_manifest=manifest,
                pipeline_contract_version="osm-prealign-v1",
                osm_dir=root / "osm",
                dsm_dir=root / "dsm",
                dsm_files=("dsm.tif",),
                dsm_crs="EPSG:27700",
                osm_prealign=True,
                apply_dsm=True,
            )
            contracted_command = build_top_level_conda_command(contracted)
            self.assertIn("--osm-prealign", contracted_command.argv)
            self.assertIn("--apply-dsm", contracted_command.argv)
            self.assertEqual(
                contracted_command.argv[
                    contracted_command.argv.index("--pipeline-contract-version") + 1
                ],
                "osm-prealign-v1",
            )

    def test_driver_stage_order_matches_audited_contract(self) -> None:
        expected = [
            "coordinate_transform",
            "osm_semantic_prealign",
            "remove_bottom_faces",
            "stitch_tiles",
            "dsm_height_correction",
            "export_scene",
        ]
        self.assertEqual(DRIVER_MERGE_STAGE_ORDER, expected)
        source = inspect.getsource(run_top_level_driver)
        positions = [
            source.index("load_and_merge_tiles("),
            source.index("_safe_remove_bottom_faces("),
            source.index("stitch_tiles("),
            source.index("semantic_height_correction("),
            source.index("export_model("),
        ]
        self.assertEqual(positions, sorted(positions))

    def test_fully_cached_exact_job_does_not_connect_to_gradio(self) -> None:
        desired = [
            ("sat_51.500000_-0.120000", 51.5, -0.12),
            ("sat_51.500000_-0.119000", 51.5, -0.119),
        ]
        cached = {
            "sat_51.500000_-0.120000": Path("a.obj"),
            "sat_51.500000_-0.119000": Path("b.obj"),
        }
        self.assertFalse(_needs_inference_connection(True, desired, cached))
        self.assertTrue(
            _needs_inference_connection(
                True, desired, {"sat_51.500000_-0.120000": Path("a.obj")}
            )
        )

    def test_download_error_text_redacts_api_key(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_MAPS_API_KEY": "not-for-logs-123"}):
            text = redact_text(
                "https://example.invalid/?key=not-for-logs-123 api_key=not-for-logs-123"
            )
        self.assertNotIn("not-for-logs-123", text)
        self.assertIn("<redacted>", text)

    def test_https_context_uses_explicit_conda_ca_without_windows_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cafile = root / "cacert.pem"
            certdir = root / "certs"
            cafile.write_text("test bundle", encoding="ascii")
            certdir.mkdir()
            expected = object()
            with patch.dict(
                os.environ,
                {"SSL_CERT_FILE": str(cafile), "SSL_CERT_DIR": str(certdir)},
            ), patch("myproject.selection.ssl.create_default_context", return_value=expected) as create:
                actual = _verified_https_context()
            self.assertIs(actual, expected)
            create.assert_called_once_with(
                cafile=str(cafile.resolve()), capath=str(certdir.resolve())
            )

    def test_unique_gradio_glb_converts_to_vertex_colour_obj(self) -> None:
        import numpy as np
        import trimesh

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mesh = trimesh.creation.icosphere(subdivisions=2)
            mesh.visual.vertex_colors = np.tile(
                np.array([[25, 100, 200, 255]], dtype=np.uint8),
                (len(mesh.vertices), 1),
            )
            glb = root / "unique-result.glb"
            obj = root / "tile.obj.part"
            mesh.export(glb, file_type="glb")

            _convert_glb_to_obj(glb, obj)

            text = obj.read_text(encoding="utf-8")
            self.assertIn("# Converted from the unique Sat3DGen Gradio GLB result", text)
            self.assertIn(" 0.0980392 0.392157 0.784314", text)
            self.assertGreater(text.count("\nv "), 100)
            self.assertGreater(text.count("\nf "), 100)


if __name__ == "__main__":
    unittest.main()
