from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import tempfile
from types import SimpleNamespace
import unittest


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE_ROOT / "src"))

from myproject.selection import (  # noqa: E402
    ObjMesh,
    PlannedTile,
    SelectedFootprint,
    SelectionBridgeError,
    SelectionRequest,
    _canonical_footprint_id,
    assign_building_faces,
    building_subset_metrics,
    clip_mesh_to_ground,
    close_ground_boundary_loops,
    deduplicate_face_indices,
    filter_building_components,
    load_source_feature_ids,
    load_reusable_pipeline_manifest,
    stage_building_publication,
    write_obj_subset,
)
from myproject.geo import LocalFrame  # noqa: E402


class BuildingSplitTests(unittest.TestCase):
    def test_face_dedup_keeps_first_winding_and_obj_remaps_used_vertices(self) -> None:
        mesh = ObjMesh(
            [
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
                (99.0, 99.0, 99.0),
            ],
            [
                (0, 1, 2),
                (2, 1, 0),
                (1, 2, 0),
                (0, 2, 3),
            ],
        )

        retained, removed = deduplicate_face_indices(mesh, [3, 2, 1, 0, 2])

        # Face 1 is opposite-winding and must survive; face 2 is a cyclic,
        # same-winding copy of face 0 and is removed.
        self.assertEqual(retained, {0, 1, 3})
        self.assertEqual(removed, 1)
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "cropped.obj"
            stats = write_obj_subset(mesh, retained, output)
            lines = output.read_text(encoding="utf-8").splitlines()
        self.assertEqual(stats, {"vertex_count": 4, "face_count": 3})
        self.assertEqual(
            [line for line in lines if line.startswith("f ")],
            ["f 1 2 3", "f 3 2 1", "f 1 3 4"],
        )

    def test_geometric_duplicate_with_distinct_indices_is_removed_but_reverse_survives(self) -> None:
        mesh = ObjMesh(
            [
                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            ],
            [(0, 1, 2), (3, 4, 5), (5, 4, 3)],
        )

        retained, removed = deduplicate_face_indices(mesh, range(3))

        self.assertEqual(retained, {0, 2})
        self.assertEqual(removed, 1)

    def test_ground_clip_interpolates_vertex_rgb(self) -> None:
        mesh = ObjMesh(
            [
                (0.0, 2.0, 0.0, 1.0, 0.0, 0.0),
                (-1.0, -2.0, 1.0, 0.0, 1.0, 0.0),
                (1.0, -2.0, 1.0, 0.0, 0.0, 1.0),
            ],
            [(0, 1, 2)],
        )

        clipped, report = clip_mesh_to_ground(mesh, {0}, 0.0)

        self.assertEqual(len(clipped.faces), 1)
        self.assertEqual(report["ground_clip_intersected_face_count"], 1)
        ground_vertices = [vertex for vertex in clipped.vertices if vertex[1] == 0.0]
        self.assertEqual(len(ground_vertices), 2)
        self.assertTrue(all(len(vertex) == 6 for vertex in ground_vertices))
        self.assertIn((0.5, 0.5, 0.0), [vertex[3:] for vertex in ground_vertices])
        self.assertIn((0.5, 0.0, 0.5), [vertex[3:] for vertex in ground_vertices])

    def test_ground_only_boundary_is_capped_without_closing_roof_holes(self) -> None:
        vertices = [
            (0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            (2.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            (2.0, 0.0, 2.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 2.0, 1.0, 0.0, 0.0),
            (0.0, 2.0, 0.0, 0.0, 1.0, 0.0),
            (2.0, 2.0, 0.0, 0.0, 1.0, 0.0),
            (2.0, 2.0, 2.0, 0.0, 1.0, 0.0),
            (0.0, 2.0, 2.0, 0.0, 1.0, 0.0),
        ]
        # Four walls, no top and no bottom. Only the ground loop may be capped.
        faces = [
            (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
            (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
        ]

        capped, report = close_ground_boundary_loops(ObjMesh(vertices, faces), 0.0)

        self.assertEqual(report["ground_loop_capped_count"], 1)
        self.assertEqual(report["ground_cap_face_count_added"], 2)
        self.assertEqual(len(capped.faces), 10)
        self.assertTrue(all(capped.vertices[index][1] == 0.0 for face in capped.faces[-2:] for index in face))

    def test_default_component_filter_preserves_disconnected_owned_panels(self) -> None:
        mesh = ObjMesh(
            [
                (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
                (10.0, 0.0, 0.0), (11.0, 0.0, 0.0), (10.0, 1.0, 0.0),
            ],
            [(0, 1, 2), (3, 4, 5)],
        )

        retained, metrics = filter_building_components(mesh, {0, 1})

        self.assertEqual(retained, {0, 1})
        self.assertEqual(metrics["raw_component_count"], 2)
        self.assertEqual(metrics["retained_component_count"], 2)
        self.assertEqual(metrics["dropped_face_count"], 0)

    def test_each_face_has_at_most_one_deterministic_owner(self) -> None:
        # Twelve unused ground samples make the local 10th percentile exactly
        # zero; the three raised faces exercise A-only, overlap and B-only.
        vertices = [(float(index % 4), 0.0, float(index // 4)) for index in range(12)]
        vertices.extend(
            [
                (0.5, 3.0, 1.0),
                (1.5, 3.0, 1.0),
                (1.0, 3.0, 2.0),
                (2.5, 4.0, 1.0),
                (3.5, 4.0, 1.0),
                (3.0, 4.0, 2.0),
                (4.5, 5.0, 1.0),
                (5.5, 5.0, 1.0),
                (5.0, 5.0, 2.0),
            ]
        )
        mesh = ObjMesh(vertices, [(12, 13, 14), (15, 16, 17), (18, 19, 20)])
        footprints = (
            SelectedFootprint("footprint-a", ((0, 0), (4, 0), (4, 4), (0, 4))),
            SelectedFootprint("footprint-b", ((2, 0), (6, 0), (6, 4), (2, 4))),
        )

        assigned, _ = assign_building_faces(mesh, footprints)

        self.assertEqual(assigned["footprint-a"], {0, 1})
        self.assertEqual(assigned["footprint-b"], {2})
        self.assertFalse(assigned["footprint-a"] & assigned["footprint-b"])

    def test_component_filter_keeps_main_shell_and_drops_fragment(self) -> None:
        mesh = ObjMesh(
            [
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 1.0, 0.0),
                (0.0, 1.0, 0.0),
                (10.0, 0.0, 0.0),
                (11.0, 0.0, 0.0),
                (10.0, 1.0, 0.0),
            ],
            [(0, 1, 2), (0, 2, 3), (4, 5, 6)],
        )

        retained, metrics = filter_building_components(
            mesh, {0, 1, 2}, minimum_component_faces=2, minimum_component_ratio=0.5
        )

        self.assertEqual(retained, {0, 1})
        self.assertEqual(metrics["raw_component_count"], 2)
        self.assertEqual(metrics["dropped_component_count"], 1)
        self.assertEqual(metrics["dropped_face_count"], 1)
        subset = building_subset_metrics(mesh, retained, ground_height_m=0.0)
        self.assertEqual(subset["face_count"], 2)
        self.assertEqual(subset["vertex_count"], 4)

    def test_workspace_obj_recovers_real_feature_id_by_java_hash(self) -> None:
        points = ((1.0, 2.0), (5.0, 2.0), (5.0, 6.0), (1.0, 6.0))
        stable_id = _canonical_footprint_id(points)
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / "footprints.obj").write_text(
                "\n".join(
                    [
                        "o footprint_000001",
                        "# feature_id way/4266528",
                        "v 1 0 2",
                        "v 5 0 2",
                        "v 5 0 6",
                        "v 1 0 6",
                        "f 1 2 3 4",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            mapped = load_source_feature_ids(workspace, [stable_id])

        self.assertEqual(mapped, {stable_id: "way/4266528"})

    def test_publication_writes_three_objs_only_for_publishable_buildings(self) -> None:
        mesh = ObjMesh(
            [(0.0, 0.0, 0.0), (2.0, 3.0, 0.0), (0.0, 3.0, 2.0)],
            [(0, 1, 2)],
        )
        ready_footprint = SelectedFootprint(
            "footprint-ready", ((-1, -1), (3, -1), (3, 3), (-1, 3))
        )
        ready = {
            "id": "footprint-ready",
            "status": "COARSE_READY",
            "publishable": True,
        }
        rejected = {
            "id": "footprint-rejected",
            "status": "REJECTED",
            "publishable": False,
        }
        with tempfile.TemporaryDirectory() as raw:
            staging = Path(raw)
            summary, index = stage_building_publication(
                staging,
                mesh,
                [(ready_footprint, {0}, ready)],
                [ready, rejected],
                selection_id="selection-test",
                stable_id="test",
            )
            ready_dir = staging / "buildings" / "footprint-ready"
            self.assertTrue((ready_dir / "cropped.obj").is_file())
            self.assertTrue((ready_dir / "gis.obj").is_file())
            self.assertTrue((ready_dir / "gis_footprints.obj").is_file())
            self.assertFalse((staging / "buildings" / "footprint-rejected").exists())
            self.assertEqual(summary["requested"], 2)
            self.assertEqual(summary["ready"], 1)
            self.assertEqual(summary["rejected"], 1)
            self.assertEqual(index["building_publication_version"], "per-footprint-v2")
            self.assertEqual(
                (staging / "buildings" / "index.json").is_file(), True
            )

            with self.assertRaisesRegex(SelectionBridgeError, "no selected footprint"):
                stage_building_publication(
                    staging / "none",
                    mesh,
                    [],
                    [rejected],
                    selection_id="selection-none",
                    stable_id="none",
                )

    def test_corrected_scene_cache_requires_exact_fresh_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            job = workspace / "_selection_jobs" / "stable"
            output = workspace / "generated_blocks" / "stable"
            mesh_path = job / "meshes" / "sat" / "sat.obj"
            dsm_dir = root / "dsm"
            osm_dir = root / "osm"
            scene = job / "final" / "selection_stable_scene.obj"
            for directory in (output, mesh_path.parent, dsm_dir, osm_dir, scene.parent):
                directory.mkdir(parents=True, exist_ok=True)
            mesh_path.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
            (dsm_dir / "tile.tif").write_bytes(b"dsm")
            (osm_dir / "building.geojson").write_text("{}", encoding="utf-8")
            scene.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
            request = SelectionRequest(
                source=output / "request.json",
                workspace=workspace,
                output_dir=output,
                job_dir=job,
                selection_id="selection-stable",
                stable_id="stable",
                footprints=(
                    SelectedFootprint("footprint-a", ((0, 0), (1, 0), (0, 1))),
                ),
                options={},
                workspace_manifest={},
                frame=LocalFrame(51.5, -0.12),
            )
            tile = PlannedTile(
                tile_id="tile",
                grid_x=0,
                grid_y=0,
                zoom=20,
                size_px=640,
                latitude=51.5,
                longitude=-0.12,
                stem="sat",
                bounds_mercator_m=(0, 0, 1, 1),
                effective_mesh_bounds_mercator_m=(0, 0, 1, 1),
                satellite_path=job / "satellite" / "sat.png",
                mesh_path=mesh_path,
            )
            pipeline = SimpleNamespace(
                dsm_dir=dsm_dir,
                osm_dir=osm_dir,
                dsm_files=("tile.tif",),
                dsm_crs="EPSG:27700",
            )
            manifest_path = job / "top_level_pipeline_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "pipeline_contract_version": "osm-prealign-v1",
                        "osm_prealign": True,
                        "merge_stage_order": [
                            "coordinate_transform",
                            "osm_semantic_prealign",
                            "remove_bottom_faces",
                            "stitch_tiles",
                            "dsm_height_correction",
                            "export_scene",
                        ],
                        "missing_mesh": [],
                        "selected_meshes": [str(mesh_path)],
                        "output_scene_obj": str(scene),
                        "dsm": {
                            "required": True,
                            "status": "APPLIED",
                            "crs": "EPSG:27700",
                            "mesh_vertex_coverage_ratio": 1.0,
                            "files": [{"name": "tile.tif"}],
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertIsNotNone(
                load_reusable_pipeline_manifest(manifest_path, request, [tile], pipeline)
            )
            newer = scene.stat().st_mtime + 10.0
            os.utime(mesh_path, (newer, newer))
            self.assertIsNone(
                load_reusable_pipeline_manifest(manifest_path, request, [tile], pipeline)
            )


if __name__ == "__main__":
    unittest.main()
