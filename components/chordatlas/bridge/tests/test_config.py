from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from myproject.config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def make_config(self, **changes):
        raw = {
            "schema_version": 1,
            "project_id": "demo",
            "output_root": "../projects",
            "conda_environment": "sat3dgen",
            "area": {
                "target_bbox_wgs84": [-0.13, 51.50, -0.12, 51.51],
                "fetch_bbox_wgs84": [-0.14, 51.49, -0.11, 51.52],
            },
            "paths": {
                "chordatlas_root": "C:/ca",
                "sat3dgen_root": "C:/sat",
                "data_builder_root": "C:/db",
                "facade_pytorch_root": "C:/facade",
                "frankengan_root": "C:/franken",
                "conda_executable": "C:/conda.exe",
            },
            "footprints": {"mode": "osm", "source_geojson": "building.geojson"},
            "mesh": {"mode": "existing", "source_obj": "scene.obj"},
            "panoramas": {"enabled": False},
        }
        raw.update(changes)
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "project.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return temp, path

    def test_loads_and_resolves_relative_paths(self):
        temp, path = self.make_config()
        self.addCleanup(temp.cleanup)
        config = load_config(path)
        self.assertEqual(config.project_id, "demo")
        self.assertEqual(config.target_bbox, (-0.13, 51.5, -0.12, 51.51))
        self.assertTrue(os.path.samefile(config.footprint_source.parent, path.parent))

    def test_rejects_fetch_bbox_that_does_not_contain_target(self):
        temp, path = self.make_config(
            area={
                "target_bbox_wgs84": [-0.13, 51.50, -0.12, 51.51],
                "fetch_bbox_wgs84": [-0.13, 51.50, -0.121, 51.51],
            }
        )
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(ConfigError, "must contain"):
            load_config(path)

    def test_refuses_environment_name_change(self):
        temp, path = self.make_config(conda_environment="other")
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(ConfigError, "sat3dgen"):
            load_config(path)

    def test_on_demand_mesh_does_not_require_an_initial_obj(self):
        temp, path = self.make_config(
            mesh={
                "mode": "on_demand",
                "mesh_resolution": 192,
                "apply_dsm": True,
                "dsm_dir": "dsm",
                "dsm_crs": "EPSG:27700",
                "dsm_files": ["tile.tif"],
                "osm_dir": "osm",
            }
        )
        self.addCleanup(temp.cleanup)

        config = load_config(path)

        self.assertEqual(config.mesh["mode"], "on_demand")
        self.assertEqual(config.mesh["mesh_resolution"], 192)
        self.assertIsNone(config.mesh_source)

    def test_on_demand_mesh_requires_explicit_dsm(self):
        temp, path = self.make_config(mesh={"mode": "on_demand"})
        self.addCleanup(temp.cleanup)

        with self.assertRaisesRegex(ConfigError, "apply_dsm"):
            load_config(path)

    def test_complete_only_policy_refuses_clipping(self):
        temp, path = self.make_config(
            footprints={
                "mode": "osm",
                "source_geojson": "building.geojson",
                "selection_policy": "fully_contained",
                "clip_to_mesh_bounds": True,
            }
        )
        self.addCleanup(temp.cleanup)

        with self.assertRaisesRegex(ConfigError, "cannot be clipped"):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
