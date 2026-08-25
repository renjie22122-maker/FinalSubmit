from __future__ import annotations

import json
import hashlib
import math
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

from myproject.big_image_selection import (
    BIG_IMAGE_STAGE_ORDER,
    BigImagePlan,
    BigImageSettings,
    build_big_image_selection,
    _load_ply_arrays,
    _numpy_to_obj_mesh,
    _ensure_big_image_vertex_colours,
    crop_arrays_to_local_bounds,
    reverse_inward_face_winding,
    transform_big_image_vertices,
    validate_big_image_output,
)
from myproject.geo import LocalFrame
from myproject.selection import SelectionBridgeError, SelectedFootprint, SelectionRequest
from myproject.selection import write_obj_subset


OFFICIAL_OUTPUT = Path(
    r"X:\fixture\Sat3DGen\results\official_big_image_target_bbox_20260816"
    r"\inference_zoom20_app192_raw640_overlap75_fractional_feather"
)


def settings(root: Path, *, validated=()) -> BigImageSettings:
    download = root / "download.py"
    inference = root / "infer.py"
    colorize = root / "colorize.py"
    repo = root / "repo"
    download.write_text("print('download')\n", encoding="utf-8")
    inference.write_text("print('infer')\n", encoding="utf-8")
    colorize.write_text("print('colorize')\n", encoding="utf-8")
    repo.mkdir(exist_ok=True)
    return BigImageSettings(
        zoom=20,
        request_size_px=640,
        retained_cell_size_px=512,
        context_padding_m=30.0,
        image_window_size_px=640,
        overlap=0.75,
        mesh_resolution=192,
        mesh_level=4.5,
        fusion_mode="fractional_feather",
        preserve_source_pixels=True,
        max_cells=400,
        max_windows=5000,
        download_timeout_s=10,
        inference_timeout_s=10,
        color_timeout_s=10,
        color_batch_size=131072,
        color_spatial_bin_size=4.0,
        color_model_path="qian43/Sat3DGen",
        source_crop_padding_m=8,
        building_crop_padding_m=1,
        ground_percentile=2,
        download_script=download,
        inference_script=inference,
        colorize_script=colorize,
        repo_root=repo,
        validated_cache_outputs=tuple(validated),
    )


class BigImageSelectionTests(unittest.TestCase):
    @staticmethod
    def _write_geometry_only_output(root: Path) -> BigImagePlan:
        root.mkdir(parents=True)
        mosaic = root.parent / "mosaic.png"
        mosaic.write_bytes(b"m" * 6000)
        (root / "prepared_input.png").write_bytes(mosaic.read_bytes())
        (root.parent / "mosaic_manifest.json").write_text(json.dumps({
            "zoom": 20,
            "request_size_px": 640,
            "retained_cell_size_px": 512,
            "grid": [2, 2],
            "mosaic_size_px": [1024, 1024],
            "center": [51.503, -0.1245],
            "bounds_wgs84": {
                "west": -0.126, "south": 51.501, "east": -0.123, "north": 51.505,
            },
        }), encoding="utf-8")
        padding = "comment " + ("x" * 1200)
        (root / "mesh.ply").write_text("\n".join((
            "ply", "format ascii 1.0", padding, "element vertex 3",
            "property float x", "property float y", "property float z",
            "element face 1", "property list uchar int vertex_indices", "end_header",
            "0 0 0", "1 0 0", "0 1 0", "3 0 1 2", "",
        )), encoding="ascii")
        (root / "run_metadata.json").write_text(json.dumps({
            "source_image": str(mosaic),
            "image_window_size_px": 640,
            "mesh_resolution": 192,
            "fusion_mode": "fractional_feather",
            "preserve_source_pixels": True,
            "requested_overlap_fraction": 0.75,
            "mesh_level": 4.5,
            "fusion_zero_weight_cells": 0,
            "mesh_vertices": 3,
            "mesh_faces": 1,
        }), encoding="utf-8")
        return BigImagePlan(
            center_lat=51.503,
            center_lon=-0.1245,
            columns=2,
            rows=2,
            input_pixel_resolution_m=0.09293,
            target_bbox_wgs84=(-0.125, 51.502, -0.124, 51.504),
            mosaic_bbox_wgs84=(-0.126, 51.501, -0.123, 51.505),
            mosaic_size_px=(1024, 1024),
            estimated_window_count=9,
        )

    def test_stage_contract_never_repeats_per_tile_cleanup(self):
        joined = " ".join(BIG_IMAGE_STAGE_ORDER)
        self.assertNotIn("remove_bottom", joined)
        self.assertNotIn("stitch_tiles", joined)
        self.assertNotIn("dedup", joined)
        self.assertNotIn("component_filter", joined)

    def test_source_crop_preserves_duplicates_winding_and_components(self):
        vertices = np.asarray(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 1.0],
                [5.0, 0.0, 5.0],
                [6.0, 0.0, 5.0],
                [5.0, 1.0, 6.0],
            ],
            dtype=float,
        )
        colours = np.arange(18, dtype=float).reshape(6, 3) / 17.0
        vertices = np.column_stack((vertices, colours))
        faces = np.asarray(
            [
                [0, 1, 2],
                [0, 1, 2],  # literal duplicate must remain
                [2, 1, 0],  # reverse winding must remain
                [3, 4, 5],  # disconnected component must remain
            ],
            dtype=np.int64,
        )
        cropped_vertices, cropped_faces, report = crop_arrays_to_local_bounds(
            vertices, faces, (-1, -1, 7, 7)
        )
        self.assertEqual(4, len(cropped_faces))
        self.assertTrue(np.array_equal(faces, cropped_faces))
        self.assertEqual(6, len(cropped_vertices))
        self.assertTrue(np.array_equal(colours, cropped_vertices[:, 3:]))
        self.assertFalse(report["geometry_cleanup_applied"])

    def test_inward_winding_is_reversed_once_without_topology_change(self):
        vertices = np.asarray(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float
        )
        inward = np.asarray(
            [[1, 3, 2], [0, 2, 3], [0, 3, 1], [0, 1, 2]], dtype=np.int64
        )
        expected = inward[:, (0, 2, 1)]
        first, report = reverse_inward_face_winding(inward)
        second, _ = reverse_inward_face_winding(inward)

        self.assertTrue(np.array_equal(expected, first))
        self.assertTrue(np.array_equal(first, second))
        self.assertTrue(np.array_equal(inward, np.asarray(
            [[1, 3, 2], [0, 2, 3], [0, 3, 1], [0, 1, 2]], dtype=np.int64
        )))
        signed_volume = np.einsum(
            "ij,ij->i",
            vertices[first[:, 0]],
            np.cross(vertices[first[:, 1]], vertices[first[:, 2]]),
        ).sum() / 6.0
        self.assertGreater(signed_volume, 0.0)
        self.assertEqual(4, report["face_count_before"])
        self.assertEqual(4, report["face_count_after"])
        self.assertEqual(0, report["faces_removed"])
        self.assertFalse(report["topology_changed"])

    def test_transform_maps_columns_east_rows_south_and_height_up(self):
        frame = LocalFrame(51.5, -0.125)
        request = SelectionRequest(
            source=Path("request.json"),
            workspace=Path("."),
            output_dir=Path("out"),
            job_dir=Path("job"),
            selection_id="big-image-test",
            stable_id="big-image-test",
            footprints=(SelectedFootprint("footprint-a", ((0, 0), (1, 0), (1, 1))),),
            options={},
            workspace_manifest={},
            frame=frame,
        )
        metadata = {
            "density_to_image_scale": 1.0,
            "prepared_pixel_resolution_m": 2.0,
            "prepared_image_size_px": [100, 100],
        }
        manifest = {
            "mosaic_size_px": [100, 100],
            "bounds_wgs84": {
                "west": -0.126,
                "south": 51.499,
                "east": -0.124,
                "north": 51.501,
            },
        }
        raw = np.asarray(
            [[0, 0, 0, 0.0, 1.0 / 255.0, 2.0 / 255.0], [100, 100, 3, 1.0, 0.5, 0.25]],
            dtype=float,
        )
        transformed, report = transform_big_image_vertices(
            raw,
            metadata,
            manifest,
            request,
        )
        self.assertLess(transformed[0, 0], transformed[1, 0])
        self.assertLess(transformed[0, 2], transformed[1, 2])
        self.assertAlmostEqual(6.0, transformed[1, 1])
        self.assertTrue(np.array_equal(raw[:, 3:], transformed[:, 3:]))
        self.assertEqual(1, report["transform_count"])
        self.assertTrue(report["vertex_colors_preserved"])

    def test_coloured_ply_to_obj_preserves_normalized_rgb(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ply = root / "coloured.ply"
            ply.write_text(
                "\n".join((
                    "ply",
                    "format ascii 1.0",
                    "element vertex 3",
                    "property float x",
                    "property float y",
                    "property float z",
                    "property uchar red",
                    "property uchar green",
                    "property uchar blue",
                    "property uchar alpha",
                    "element face 1",
                    "property list uchar int vertex_indices",
                    "end_header",
                    "0 0 0 0 1 2 255",
                    "1 0 0 255 128 64 255",
                    "0 1 0 10 20 30 255",
                    "3 0 1 2",
                    "",
                )),
                encoding="ascii",
            )
            vertices, faces = _load_ply_arrays(ply)
            self.assertEqual((3, 6), vertices.shape)
            self.assertTrue(np.allclose([0, 1 / 255, 2 / 255], vertices[0, 3:]))
            self.assertTrue(np.allclose([1, 128 / 255, 64 / 255], vertices[1, 3:]))
            mesh = _numpy_to_obj_mesh(vertices, faces)
            output = root / "model.obj"
            write_obj_subset(mesh, {0}, output)
            vertex_lines = [line for line in output.read_text(encoding="utf-8").splitlines() if line.startswith("v ")]
            self.assertEqual(3, len(vertex_lines))
            self.assertTrue(all(len(line.split()) == 7 for line in vertex_lines))
            self.assertAlmostEqual(1 / 255, float(vertex_lines[0].split()[5]))

    def test_uncoloured_ply_is_rejected_instead_of_getting_fake_rgb(self):
        with tempfile.TemporaryDirectory() as temporary:
            ply = Path(temporary) / "plain.ply"
            ply.write_text(
                "\n".join((
                    "ply", "format ascii 1.0", "element vertex 3",
                    "property float x", "property float y", "property float z",
                    "element face 1", "property list uchar int vertex_indices", "end_header",
                    "0 0 0", "1 0 0", "0 1 0", "3 0 1 2", "",
                )),
                encoding="ascii",
            )
            with self.assertRaises(SelectionBridgeError) as raised:
                _load_ply_arrays(ply)
            self.assertEqual("big_image_colour_invalid", raised.exception.code)

    def test_geometry_only_job_runs_only_colour_pass_then_reuses_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = settings(root)
            output = root / "inference"
            plan = self._write_geometry_only_output(output)
            calls: list[list[str]] = []

            def fake_colour(argv, **_kwargs):
                argv = list(argv)
                calls.append(argv)
                target = Path(argv[argv.index("--output_path") + 1])
                target.parent.mkdir(parents=True, exist_ok=True)
                padding = "comment " + ("c" * 1200)
                target.write_text("\n".join((
                    "ply", "format ascii 1.0", padding, "element vertex 3",
                    "property float x", "property float y", "property float z",
                    "property uchar red", "property uchar green", "property uchar blue",
                    "property uchar alpha", "element face 1",
                    "property list uchar int vertex_indices", "end_header",
                    "0 0 0 0 1 2 255", "1 0 0 255 128 64 255",
                    "0 1 0 10 20 30 255", "3 0 1 2", "",
                )), encoding="ascii")
                source_sha = hashlib.sha256((output / "mesh.ply").read_bytes()).hexdigest()
                output_sha = hashlib.sha256(target.read_bytes()).hexdigest()
                (target.parent / "color_preflight.json").write_text(json.dumps({
                    "vertex_count": 3, "zero_weight_vertices": 0,
                }), encoding="utf-8")
                (target.parent / "color_metadata.json").write_text(json.dumps({
                    "source_mesh_sha256": source_sha,
                    "output_mesh_sha256": output_sha,
                    "model_path": "qian43/Sat3DGen",
                    "vertex_count": 3,
                    "face_count": 1,
                    "color_batch_size": 131072,
                    "spatial_bin_size_density_voxels": 4.0,
                    "zero_weight_vertices": 0,
                    "contributors_per_vertex_min": 1,
                    "geometry_preserved": True,
                    "verification": {
                        "geometry_unchanged": True,
                        "rgb_roundtrip_exact": True,
                        "alpha_opaque": True,
                    },
                }), encoding="utf-8")

            with mock.patch("myproject.big_image_selection._run_logged", side_effect=fake_colour):
                report = _ensure_big_image_vertex_colours(output, root / "job", plan, configured)
            self.assertTrue(report["vertex_colors"])
            self.assertEqual(1, len(calls))
            self.assertIn(str(configured.colorize_script), calls[0])
            self.assertNotIn(str(configured.inference_script), calls[0])
            with mock.patch(
                "myproject.big_image_selection._run_logged",
                side_effect=AssertionError("complete colour cache must not run a subprocess"),
            ):
                reused = _ensure_big_image_vertex_colours(output, root / "job", plan, configured)
            self.assertTrue(reused["vertex_colors"])

    def test_dry_run_writes_big_plan_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            source_config = root / "config.json"
            download = root / "download.py"
            inference = root / "infer.py"
            colorize = root / "colorize.py"
            repo = root / "repo"
            download.write_text("pass\n", encoding="utf-8")
            inference.write_text("pass\n", encoding="utf-8")
            colorize.write_text("pass\n", encoding="utf-8")
            repo.mkdir()
            source_config.write_text(
                json.dumps(
                    {
                        "paths": {"sat3dgen_root": str(root)},
                        "mesh": {
                            "context_padding_m": 30,
                            "apply_dsm": True,
                            "dsm_dir": str(root / "dsm"),
                            "dsm_files": ["a.tif"],
                            "dsm_crs": "EPSG:27700",
                            "osm_dir": str(root / "osm"),
                            "big_image": {
                                "enabled": True,
                                "download_script": str(download),
                                "inference_script": str(inference),
                                "colorize_script": str(colorize),
                                "repo_root": str(repo),
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "manifest.json").write_text(
                json.dumps(
                    {
                        "config": str(source_config),
                        "frame": {
                            "origin_lat": 51.503,
                            "origin_lon": -0.125,
                            "units": "m",
                            "axes": {"x": "east", "z": "south"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            publication = workspace / "generated_blocks" / "big-image-test"
            publication.mkdir(parents=True)
            request_path = publication / "request.json"
            request_path.write_text(
                json.dumps(
                    {
                        "workspace": str(workspace),
                        "selection_id": "big-image-test",
                        "footprints": [
                            {"id": "footprint-a", "points": [[-5, -5], [5, -5], [5, 5], [-5, 5]]}
                        ],
                        "options": {
                            "model_source": "big_image",
                            "pipeline_contract_version": "big-image-app192-v3-vertex-colour",
                            "require_complete_buildings": True,
                        },
                    }
                ),
                encoding="utf-8",
            )
            report = build_big_image_selection(request_path, execute=False)
            self.assertEqual("PLANNED", report["status"])
            self.assertEqual("big_image", report["model_source"])
            self.assertTrue((workspace / "_selection_jobs" / "big-image-test" / "big_image_plan.json").is_file())
            self.assertFalse((workspace / "_selection_jobs" / "big-image-test" / "big_image" / "mosaic.png").exists())

    def test_cache_rejects_prepared_input_that_differs_from_mosaic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = settings(root)
            output = root / "output"
            output.mkdir()
            (output / "mesh.ply").write_bytes(b"p" * 1024)
            mosaic = root / "mosaic.png"
            mosaic.write_bytes(b"a" * 6000)
            (output / "prepared_input.png").write_bytes(b"b" * 6000)
            (output / "run_metadata.json").write_text(
                json.dumps({
                    "source_image": str(mosaic),
                    "image_window_size_px": 640,
                    "mesh_resolution": 192,
                    "fusion_mode": "fractional_feather",
                    "preserve_source_pixels": True,
                    "requested_overlap_fraction": 0.75,
                    "mesh_level": 4.5,
                    "fusion_zero_weight_cells": 0,
                }),
                encoding="utf-8",
            )
            mosaic.with_name("mosaic_manifest.json").write_text(
                json.dumps({
                    "zoom": 20,
                    "request_size_px": 640,
                    "retained_cell_size_px": 512,
                    "bounds_wgs84": {
                        "west": -0.126,
                        "south": 51.501,
                        "east": -0.123,
                        "north": 51.505,
                    },
                }),
                encoding="utf-8",
            )
            plan = BigImagePlan(
                center_lat=51.503,
                center_lon=-0.1245,
                columns=2,
                rows=2,
                input_pixel_resolution_m=0.09293,
                target_bbox_wgs84=(-0.125, 51.502, -0.124, 51.504),
                mosaic_bbox_wgs84=(-0.126, 51.501, -0.123, 51.505),
                mosaic_size_px=(1024, 1024),
                estimated_window_count=9,
            )
            with self.assertRaises(SelectionBridgeError) as raised:
                validate_big_image_output(output, plan, configured)
            self.assertEqual("big_image_cache_mismatch", raised.exception.code)

    @unittest.skipUnless(OFFICIAL_OUTPUT.is_dir(), "official big-image fixture is not present")
    def test_official_fixture_matches_validated_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = settings(root, validated=(OFFICIAL_OUTPUT,))
            plan = BigImagePlan(
                center_lat=51.5029,
                center_lon=-0.1247,
                columns=2,
                rows=2,
                input_pixel_resolution_m=0.09293,
                target_bbox_wgs84=(-0.1255, 51.5020, -0.1240, 51.5035),
                mosaic_bbox_wgs84=(-0.1256, 51.5019, -0.1239, 51.5036),
                mosaic_size_px=(1024, 1024),
                estimated_window_count=9,
            )
            report = validate_big_image_output(OFFICIAL_OUTPUT, plan, configured)
            self.assertEqual(192, report["metadata"]["mesh_resolution"])
            self.assertEqual("fractional_feather", report["metadata"]["fusion_mode"])
            self.assertTrue(report["metadata"]["preserve_source_pixels"])
            self.assertEqual(0, report["metadata"]["fusion_zero_weight_cells"])
            self.assertEqual(64, len(report["source_image_sha256"]))
            self.assertTrue(report["vertex_colors"])
            self.assertEqual(OFFICIAL_OUTPUT / "mesh_colored.ply", Path(report["mesh_ply"]))
            self.assertEqual(
                "e90ead99971a424298bc530967ed65a8ba6d55fa57e8fdea43d9703066e591b6",
                report["color_output_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
