from __future__ import annotations

import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh


SCRIPT_PATH = Path(__file__).with_name("colorize_sat3dgen_big_mesh.py")
SPEC = importlib.util.spec_from_file_location("big_mesh_colorizer", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FractionalWeightTests(unittest.TestCase):
    @staticmethod
    def explicit_splat(weights: np.ndarray, origin: float) -> tuple[int, np.ndarray]:
        base = math.floor(origin)
        fraction = origin - base
        result = np.zeros(len(weights) + 1, dtype=np.float32)
        result[:-1] += np.float32(1.0 - fraction) * weights
        result[1:] += np.float32(fraction) * weights
        return base, result

    def test_axis_weight_matches_explicit_splat_then_interpolation(self) -> None:
        source = np.array([0.0, 0.25, 1.0, 0.4], dtype=np.float32)
        for origin in (0.0, 2.2, 7.5):
            base, splatted = self.explicit_splat(source, origin)
            points = np.linspace(base - 0.9, base + len(source) + 0.9, 101)
            support_positions = np.arange(
                -1, len(splatted) + 1, dtype=np.float64
            ) + base
            support_values = np.concatenate(
                (
                    np.zeros(1, dtype=np.float32),
                    splatted,
                    np.zeros(1, dtype=np.float32),
                )
            )
            expected = np.interp(
                points,
                support_positions,
                support_values,
                left=0.0,
                right=0.0,
            ).astype(np.float32)
            actual = MODULE.splatted_axis_weight_at(points, origin, source)
            np.testing.assert_allclose(actual, expected, rtol=0.0, atol=2e-7)

    def test_fractional_tail_retains_weight_but_query_clamps(self) -> None:
        row_weights = np.ones(6, dtype=np.float32)
        window = MODULE.WindowSpec(
            image_row=0,
            image_column=0,
            density_row=11.25,
            density_column=4.5,
            row_weights=row_weights,
            column_weights=row_weights,
        )
        # Both coordinates extend beyond the final local sample (index 5),
        # but remain inside the fractional splat/interpolation footprint.
        vertex = np.array([[17.0, 10.0, 3.0]], dtype=np.float32)
        row_weight = MODULE.splatted_axis_weight_at(
            vertex[:, 0], window.density_row, row_weights
        )
        column_weight = MODULE.splatted_axis_weight_at(
            vertex[:, 1], window.density_column, row_weights
        )
        self.assertGreater(float(row_weight[0] * column_weight[0]), 0.0)
        coordinates = MODULE.model_query_coordinates(
            vertex,
            window,
            density_xy=6,
            density_height=8,
            pad=2,
            mesh_resolution=10,
        )
        expected_index = np.array([[7.0, 7.0, 5.0]], dtype=np.float32)
        expected = expected_index * np.float32(2.0 / 9.0) - 1.0
        np.testing.assert_allclose(coordinates, expected, rtol=0.0, atol=1e-6)

    def test_query_mapping_swaps_asymmetric_row_and_column_axes(self) -> None:
        weights = np.ones(6, dtype=np.float32)
        window = MODULE.WindowSpec(
            image_row=0,
            image_column=0,
            density_row=10.25,
            density_column=20.5,
            row_weights=weights,
            column_weights=weights,
        )
        vertex = np.array([[11.25, 23.5, 4.0]], dtype=np.float32)
        coordinates = MODULE.model_query_coordinates(
            vertex,
            window,
            density_xy=6,
            density_height=8,
            pad=2,
            mesh_resolution=10,
        )
        # Global [row, col, z] must become model [x=col, y=row, z].
        expected_index = np.array([[5.0, 3.0, 6.0]], dtype=np.float32)
        expected = expected_index * np.float32(2.0 / 9.0) - 1.0
        np.testing.assert_allclose(coordinates, expected, rtol=0.0, atol=1e-6)


class SpatialIndexAndBlendTests(unittest.TestCase):
    def test_bins_and_exact_weights_cover_fractional_halo(self) -> None:
        vertices = np.array(
            [
                [0.0, 0.0, 0.0],
                [2.5, 3.5, 0.0],
                [6.0, 6.0, 0.0],
                [7.0, 7.0, 0.0],
                [9.0, 9.0, 0.0],
            ],
            dtype=np.float32,
        )
        index = MODULE.build_spatial_bins(vertices, 10, 10, 2.0)
        weights = np.ones(6, dtype=np.float32)
        window = MODULE.WindowSpec(0, 0, 0.5, 0.5, weights, weights)
        ids, contribution = MODULE.window_vertex_weights(vertices, index, window)
        self.assertIn(2, ids.tolist())
        self.assertNotIn(4, ids.tolist())
        self.assertTrue(np.all(contribution > 0.0))

    def test_float_rgb_blend_uses_weighted_mean(self) -> None:
        weights_a = np.array([0.25, 0.75], dtype=np.float32)
        weights_b = np.array([0.75, 0.25], dtype=np.float32)
        red = np.array([[1.0, 0.0, 0.0]] * 2, dtype=np.float32)
        blue = np.array([[0.0, 0.0, 1.0]] * 2, dtype=np.float32)
        numerator = red * weights_a[:, None] + blue * weights_b[:, None]
        denominator = weights_a + weights_b
        blended = numerator / denominator[:, None]
        np.testing.assert_allclose(
            blended,
            np.array([[0.25, 0.0, 0.75], [0.75, 0.0, 0.25]]),
            rtol=0.0,
            atol=1e-7,
        )


class PlyRoundTripTests(unittest.TestCase):
    def test_colour_export_preserves_geometry_and_rgb(self) -> None:
        vertices = np.array(
            [[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32
        )
        faces = np.array([[0, 1, 2]], dtype=np.int64)
        source = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
        rgb = np.array([[1, 2, 3], [10, 20, 30], [250, 251, 252]], dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "coloured.ply"
            MODULE.export_coloured_mesh(source, rgb, path)
            header = path.read_bytes()[:1024].split(b"end_header", 1)[0]
            self.assertIn(b"property uchar red", header)
            self.assertIn(b"property uchar green", header)
            self.assertIn(b"property uchar blue", header)
            self.assertIn(b"property uchar alpha", header)
            result = MODULE.verify_coloured_mesh(source, rgb, path)
        self.assertTrue(result["geometry_unchanged"])
        self.assertTrue(result["rgb_roundtrip_exact"])
        self.assertTrue(result["alpha_opaque"])


if __name__ == "__main__":
    unittest.main()
