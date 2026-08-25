from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BRIDGE_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from myproject.footprints import (  # noqa: E402
    GeoJSONError,
    export_geojson_footprints,
    load_geojson_footprints,
    write_footprints_obj,
)
from myproject.geo import (  # noqa: E402
    BBox,
    CoordinateError,
    LocalBBox,
    LocalFrame,
    METERS_PER_DEGREE_LAT,
)


def polygon_feature(rings, feature_id=None):
    feature = {
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon", "coordinates": rings},
    }
    if feature_id is not None:
        feature["id"] = feature_id
    return feature


class GeoTests(unittest.TestCase):
    def test_frame_uses_east_x_and_south_z_and_round_trips(self):
        frame = LocalFrame(origin_lat=51.0, origin_lon=-0.1)
        x, z = frame.to_local(-0.099, 51.001)

        self.assertGreater(x, 0.0)
        self.assertAlmostEqual(
            x,
            0.001 * METERS_PER_DEGREE_LAT * math.cos(math.radians(51.0)),
            places=8,
        )
        self.assertAlmostEqual(z, -0.001 * METERS_PER_DEGREE_LAT, places=8)
        lon, lat = frame.to_wgs84(x, z)
        self.assertAlmostEqual(lon, -0.099, places=12)
        self.assertAlmostEqual(lat, 51.001, places=12)
        self.assertEqual(frame.local_to_latlon(x, z), (lat, lon))

    def test_bbox_converts_all_corners_despite_inverted_north_z(self):
        bbox = BBox(-0.101, 50.999, -0.099, 51.001)
        frame = LocalFrame.from_bbox(bbox)
        local = bbox.to_local(frame)

        self.assertLess(local.min_x, 0.0)
        self.assertGreater(local.max_x, 0.0)
        self.assertAlmostEqual(local.min_z, -111.32, places=6)
        self.assertAlmostEqual(local.max_z, 111.32, places=6)
        self.assertTrue(local.contains(0.0, 0.0))

    def test_invalid_bbox_and_polar_frame_are_rejected(self):
        with self.assertRaises(CoordinateError):
            BBox(-0.1, 51.0, -0.2, 51.1)
        with self.assertRaises(CoordinateError):
            LocalFrame(90.0, 0.0)


class FootprintTests(unittest.TestCase):
    def setUp(self):
        self.frame = LocalFrame(origin_lat=51.0, origin_lon=-0.1)
        self.selection = LocalBBox(-100.0, -100.0, 100.0, 100.0)

    def test_polygon_multipolygon_filtering_holes_and_statistics(self):
        inside_outer = [
            [-0.1002, 50.9998],
            [-0.0998, 50.9998],
            [-0.0998, 51.0002],
            [-0.1002, 51.0002],
            [-0.1002, 50.9998],
        ]
        inside_hole = [
            [-0.10005, 50.99995],
            [-0.10005, 51.00005],
            [-0.09995, 51.00005],
            [-0.09995, 50.99995],
            [-0.10005, 50.99995],
        ]
        second_inside = [
            [-0.0997, 50.9999],
            [-0.0995, 50.9999],
            [-0.0995, 51.0001],
            [-0.0997, 51.0001],
            [-0.0997, 50.9999],
        ]
        far_outside = [
            [0.1, 51.0],
            [0.101, 51.0],
            [0.101, 51.001],
            [0.1, 51.001],
            [0.1, 51.0],
        ]
        unclosed = [
            [-0.1, 51.0],
            [-0.0999, 51.0],
            [-0.0999, 51.0001],
            [-0.1, 51.0001],
        ]
        document = {
            "type": "FeatureCollection",
            "features": [
                polygon_feature([inside_outer, inside_hole], "building-a"),
                {
                    "type": "Feature",
                    "properties": {"osm_id": 42},
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[second_inside], [far_outside]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Point", "coordinates": [-0.1, 51.0]},
                },
                polygon_feature([unclosed], "bad"),
            ],
        }

        result = load_geojson_footprints(document, self.frame, self.selection)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].feature_id, "building-a")
        self.assertEqual(result[1].feature_id, "42")
        self.assertEqual(len(result[0].points), 4)
        self.assertEqual(
            result.stats.as_dict(),
            {
                "features": 4,
                "polygon_features": 3,
                "polygons": 4,
                "exported": 2,
                "holes": 1,
                "invalid": 1,
                "filtered": 2,
                "clipped": 0,
            },
        )

    def test_intersection_selection_keeps_whole_ring_by_default(self):
        local_ring = ((-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0))
        geo_ring = [list(self.frame.to_wgs84(x, z)) for x, z in local_ring]
        geo_ring.append(geo_ring[0])
        document = {"type": "FeatureCollection", "features": [polygon_feature([geo_ring])]}
        selection = LocalBBox(-10.0, -10.0, 10.0, 10.0)

        result = load_geojson_footprints(document, self.frame, selection)

        self.assertEqual(len(result), 1)
        self.assertLess(result[0].bounds.min_x, selection.min_x)
        self.assertGreater(result[0].bounds.max_z, selection.max_z)
        self.assertEqual(result.stats.clipped, 0)

    def test_explicit_clip_trims_to_local_bbox(self):
        local_ring = ((-20.0, -20.0), (20.0, -20.0), (20.0, 20.0), (-20.0, 20.0))
        geo_ring = [list(self.frame.to_wgs84(x, z)) for x, z in local_ring]
        geo_ring.append(geo_ring[0])
        document = {"type": "FeatureCollection", "features": [polygon_feature([geo_ring])]}
        selection = LocalBBox(-10.0, -10.0, 10.0, 10.0)

        result = load_geojson_footprints(document, self.frame, selection, clip=True)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.stats.clipped, 1)
        bounds = result[0].bounds
        self.assertAlmostEqual(bounds.min_x, -10.0, places=8)
        self.assertAlmostEqual(bounds.max_x, 10.0, places=8)
        self.assertAlmostEqual(bounds.min_z, -10.0, places=8)
        self.assertAlmostEqual(bounds.max_z, 10.0, places=8)

    def test_fully_contained_policy_rejects_partial_polygon_without_clipping(self):
        partial = ((-20.0, -5.0), (5.0, -5.0), (5.0, 5.0), (-20.0, 5.0))
        complete = ((-5.0, -4.0), (4.0, -4.0), (4.0, 4.0), (-5.0, 4.0))

        def geo_ring(points):
            ring = [list(self.frame.to_wgs84(x, z)) for x, z in points]
            return ring + [ring[0]]

        document = {
            "type": "FeatureCollection",
            "features": [
                polygon_feature([geo_ring(partial)], "partial"),
                polygon_feature([geo_ring(complete)], "complete"),
            ],
        }
        selection = LocalBBox(-10.0, -10.0, 10.0, 10.0)

        result = load_geojson_footprints(
            document,
            self.frame,
            selection,
            selection_policy="fully_contained",
        )

        self.assertEqual([item.feature_id for item in result], ["complete"])
        self.assertEqual(result.stats.partial, 1)
        self.assertEqual(result.stats.clipped, 0)

    def test_fully_contained_policy_cannot_clip(self):
        with self.assertRaisesRegex(ValueError, "cannot also be clipped"):
            load_geojson_footprints(
                {"type": "FeatureCollection", "features": []},
                self.frame,
                self.selection,
                clip=True,
                selection_policy="fully_contained",
            )

    def test_self_intersection_and_outside_hole_are_invalid(self):
        bow_tie = [
            [-0.1002, 50.9998],
            [-0.0998, 51.0002],
            [-0.1002, 51.0002],
            [-0.0998, 50.9998],
            [-0.1002, 50.9998],
        ]
        outer = [
            [-0.1002, 50.9998],
            [-0.0998, 50.9998],
            [-0.0998, 51.0002],
            [-0.1002, 51.0002],
            [-0.1002, 50.9998],
        ]
        outside_hole = [
            [-0.2, 51.0],
            [-0.199, 51.0],
            [-0.199, 51.001],
            [-0.2, 51.001],
            [-0.2, 51.0],
        ]
        document = {
            "type": "FeatureCollection",
            "features": [polygon_feature([bow_tie]), polygon_feature([outer, outside_hole])],
        }

        result = load_geojson_footprints(document, self.frame)

        self.assertEqual(len(result), 0)
        self.assertEqual(result.stats.invalid, 2)
        self.assertEqual(result.stats.holes, 1)

    def test_obj_has_y_zero_open_ring_vertices_and_one_face_per_outer(self):
        ring_a = [
            [-0.1001, 50.9999],
            [-0.0999, 50.9999],
            [-0.0999, 51.0001],
            [-0.1001, 51.0001],
            [-0.1001, 50.9999],
        ]
        ring_b = [
            [-0.0998, 50.9999],
            [-0.0996, 50.9999],
            [-0.0996, 51.0001],
            [-0.0998, 51.0001],
            [-0.0998, 50.9999],
        ]
        document = {
            "type": "FeatureCollection",
            "features": [polygon_feature([ring_a], "a"), polygon_feature([ring_b], "b")],
        }
        result = load_geojson_footprints(document, self.frame)

        with tempfile.TemporaryDirectory() as temporary:
            path = write_footprints_obj(result, Path(temporary) / "gis" / "footprints.obj")
            lines = path.read_text(encoding="utf-8").splitlines()

        vertices = [line for line in lines if line.startswith("v ")]
        faces = [line for line in lines if line.startswith("f ")]
        self.assertEqual(len(vertices), 8)
        self.assertTrue(all(line.split()[2] == "0" for line in vertices))
        self.assertEqual(faces, ["f 1 2 3 4", "f 5 6 7 8"])

    def test_path_source_and_convenience_export(self):
        ring = [
            [-0.1001, 50.9999],
            [-0.0999, 50.9999],
            [-0.0999, 51.0001],
            [-0.1001, 51.0001],
            [-0.1001, 50.9999],
        ]
        document = {"type": "FeatureCollection", "features": [polygon_feature([ring])]}

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "buildings.geojson"
            output = Path(temporary) / "footprints.obj"
            source.write_text(json.dumps(document), encoding="utf-8")
            result = export_geojson_footprints(source, output, self.frame)
            self.assertEqual(len(result), 1)
            self.assertTrue(output.is_file())

    def test_invalid_document_root_raises(self):
        with self.assertRaises(GeoJSONError):
            load_geojson_footprints({"type": "Polygon", "coordinates": []}, self.frame)
        with self.assertRaises(GeoJSONError):
            load_geojson_footprints({"type": "FeatureCollection", "features": {}}, self.frame)


if __name__ == "__main__":
    unittest.main()
