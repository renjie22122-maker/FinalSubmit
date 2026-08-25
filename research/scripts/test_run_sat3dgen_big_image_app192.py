import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_sat3dgen_big_image_app192 import (
    accumulate_official_box,
    axis_stops,
    fractional_origins,
    raised_cosine_patch_weights,
    splat_density_patch,
)


class FractionalFeatherFusionTests(unittest.TestCase):
    def test_fractional_constant_patches_remain_constant_without_holes(self):
        origins, output_length = fractional_origins(
            [0, 160, 320, 480, 640],
            image_length=1280,
            image_window=640,
            density_length=154,
        )
        self.assertEqual(origins, [0.0, 38.5, 77.0, 115.5, 154.0])
        self.assertEqual(output_length, 308)

        density_sum = np.zeros((10, 11, 2), dtype=np.float32)
        weight_sum = np.zeros((10, 11), dtype=np.float32)
        patch = np.full((6, 6, 2), 3.25, dtype=np.float32)
        row_origins = [0.0, 3.5]
        column_origins = [0.0, 4.5]
        for row_index, row_origin in enumerate(row_origins):
            for column_index, column_origin in enumerate(column_origins):
                patch_weight = raised_cosine_patch_weights(
                    6,
                    6,
                    2,
                    touches_top=row_index == 0,
                    touches_bottom=row_index == len(row_origins) - 1,
                    touches_left=column_index == 0,
                    touches_right=column_index == len(column_origins) - 1,
                )
                splat_density_patch(
                    density_sum,
                    weight_sum,
                    patch,
                    patch_weight,
                    row_origin,
                    column_origin,
                )

        self.assertTrue(np.all(weight_sum > 0), "fractional fusion left holes")
        fused = density_sum / weight_sum[..., None]
        np.testing.assert_allclose(fused, 3.25, rtol=0.0, atol=1e-6)

    def test_rectangular_forced_end_tiles_leave_no_coverage_holes(self):
        image_window = 8
        density_length = 6
        image_rows = axis_stops(23, image_window, 2)
        image_columns = axis_stops(19, image_window, 2)
        row_origins, output_height = fractional_origins(
            image_rows, 23, image_window, density_length
        )
        column_origins, output_width = fractional_origins(
            image_columns, 19, image_window, density_length
        )
        self.assertEqual(image_rows[-1] - image_rows[-2], 1)
        self.assertEqual(image_columns[-1] - image_columns[-2], 1)

        density_sum = np.zeros((output_height, output_width, 1), dtype=np.float32)
        weight_sum = np.zeros((output_height, output_width), dtype=np.float32)
        patch = np.full((density_length, density_length, 1), 2.0, dtype=np.float32)
        for row_index, row_origin in enumerate(row_origins):
            for column_index, column_origin in enumerate(column_origins):
                patch_weight = raised_cosine_patch_weights(
                    density_length,
                    density_length,
                    1,
                    touches_top=row_index == 0,
                    touches_bottom=row_index == len(row_origins) - 1,
                    touches_left=column_index == 0,
                    touches_right=column_index == len(column_origins) - 1,
                )
                splat_density_patch(
                    density_sum,
                    weight_sum,
                    patch,
                    patch_weight,
                    row_origin,
                    column_origin,
                )

        self.assertTrue(np.all(weight_sum > 0))
        fused = density_sum / weight_sum[..., None]
        np.testing.assert_allclose(fused, 2.0, rtol=0.0, atol=1e-6)

    def test_raised_cosine_boundary_is_smoother_than_official_box(self):
        output_shape = (8, 40, 1)
        zero_patch = np.zeros((8, 32, 1), dtype=np.float32)
        one_patch = np.ones((8, 32, 1), dtype=np.float32)

        box_sum = np.zeros(output_shape, dtype=np.float32)
        box_weight = np.zeros(output_shape[:2], dtype=np.float32)
        accumulate_official_box(
            box_sum,
            box_weight,
            zero_patch,
            0,
            0,
            4,
            touches_top=True,
            touches_bottom=True,
            touches_left=True,
            touches_right=False,
        )
        accumulate_official_box(
            box_sum,
            box_weight,
            one_patch,
            0,
            8,
            4,
            touches_top=True,
            touches_bottom=True,
            touches_left=False,
            touches_right=True,
        )
        self.assertTrue(np.all(box_weight > 0))
        box_profile = (box_sum / box_weight[..., None])[0, :, 0]

        feather_sum = np.zeros(output_shape, dtype=np.float32)
        feather_weight = np.zeros(output_shape[:2], dtype=np.float32)
        left_weight = raised_cosine_patch_weights(
            8,
            32,
            4,
            touches_top=True,
            touches_bottom=True,
            touches_left=True,
            touches_right=False,
        )
        right_weight = raised_cosine_patch_weights(
            8,
            32,
            4,
            touches_top=True,
            touches_bottom=True,
            touches_left=False,
            touches_right=True,
        )
        splat_density_patch(
            feather_sum, feather_weight, zero_patch, left_weight, 0.0, 0.0
        )
        splat_density_patch(
            feather_sum, feather_weight, one_patch, right_weight, 0.0, 8.0
        )
        self.assertTrue(np.all(feather_weight > 0))
        feather_profile = (
            feather_sum / feather_weight[..., None]
        )[0, :, 0]

        box_max_jump = float(np.abs(np.diff(box_profile)).max())
        feather_max_jump = float(np.abs(np.diff(feather_profile)).max())
        self.assertLess(feather_max_jump, box_max_jump)


if __name__ == "__main__":
    unittest.main()
