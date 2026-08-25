"""Run Sat3DGen density-field fusion over a rectangular satellite mosaic.

Each raw-image window is resized by the standard Sat3DGen transform to the
model's fixed 256 x 256 network input. ``mesh_resolution`` is passed directly
as ``grid_size``; with the default value 192 it therefore matches app.py's
Mesh Resolution setting. The model's spatial crop can make each returned
density block smaller than the raw-image window, so window positions are
mapped from image coordinates into density coordinates before fusion.

The upstream model, ``forward_grid``, and its query batch are not modified or
monkey-patched.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torchvision.transforms as T
import trimesh
from PIL import Image
from skimage import measure


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo_root", type=Path, required=True)
    parser.add_argument("--satellite_img_path", type=Path, required=True)
    parser.add_argument("--input_pixel_resolution", type=float, required=True)
    parser.add_argument("--prepared_pixel_resolution", type=float, default=0.28)
    parser.add_argument(
        "--preserve_source_pixels",
        action="store_true",
        help=(
            "Do not resample the full mosaic. Each source window is resized "
            "individually by the model transform, matching app.py."
        ),
    )
    parser.add_argument("--image_window_size", type=int, default=640)
    parser.add_argument(
        "--overlap",
        type=float,
        default=0.75,
        help="Requested adjacent-window overlap when --step_size is omitted.",
    )
    parser.add_argument(
        "--step_size",
        "--image_step_size",
        dest="step_size",
        type=int,
        default=None,
        help=(
            "Raw-image step in pixels. Overrides --overlap; the legacy "
            "--image_step_size spelling is retained as an alias."
        ),
    )
    parser.add_argument("--mesh_resolution", type=int, default=192)
    parser.add_argument("--mesh_level", type=float, default=4.5)
    parser.add_argument(
        "--fusion_mode",
        choices=("fractional_feather", "official_box"),
        default="fractional_feather",
        help=(
            "fractional_feather uses floating-point density origins, bilinear "
            "splatting, and raised-cosine blending. official_box retains the "
            "legacy integer placement and hard-crop averaging."
        ),
    )
    parser.add_argument(
        "--feather_width",
        type=int,
        default=None,
        help=(
            "Raised-cosine feather width in density voxels. Defaults to the "
            "official crop-edge ratio (density XY // 8)."
        ),
    )
    parser.add_argument("--model_path", default="qian43/Sat3DGen")
    parser.add_argument("--output_dir", type=Path, required=True)
    return parser.parse_args()


def axis_stops(length: int, window_size: int, step_size: int) -> list[int]:
    if length < window_size:
        raise ValueError(f"Prepared axis {length}px is smaller than {window_size}px")
    stops = list(range(0, length - window_size + 1, step_size))
    final_stop = length - window_size
    if stops[-1] != final_stop:
        stops.append(final_stop)
    return stops


def adjacent_overlap_fractions(stops: list[int], window_size: int) -> list[float]:
    return [
        (window_size - (next_stop - stop)) / window_size
        for stop, next_stop in zip(stops, stops[1:])
    ]


def mapped_stops(
    image_stops: list[int],
    image_length: int,
    image_window: int,
    density_length: int,
) -> tuple[list[int], int]:
    scale = density_length / image_window
    output_length = round(image_length * scale)
    result = []
    for stop in image_stops:
        if stop + image_window == image_length:
            mapped = output_length - density_length
        else:
            mapped = round(stop * scale)
        result.append(mapped)
    if len(set(result)) != len(result):
        raise RuntimeError("Image-window positions collapsed in density coordinates")
    return result, output_length


def fractional_origins(
    image_stops: list[int],
    image_length: int,
    image_window: int,
    density_length: int,
) -> tuple[list[float], int]:
    """Map image stops without quantizing sub-voxel density positions.

    The output axis is large enough to receive the upper bilinear splat from
    the final source sample. Exact integer extents remain exact, while a
    fractional extent receives one additional output cell.
    """
    scale = density_length / image_window
    origins = [float(stop * scale) for stop in image_stops]
    continuous_extent = image_length * scale
    nearest_extent = round(continuous_extent)
    if math.isclose(
        continuous_extent,
        nearest_extent,
        rel_tol=1e-12,
        abs_tol=1e-9,
    ):
        output_length = int(nearest_extent)
    else:
        output_length = math.ceil(continuous_extent)
    output_length = max(output_length, density_length)
    if len(set(origins)) != len(origins):
        raise RuntimeError("Image-window positions collapsed in density coordinates")
    return origins, output_length


def raised_cosine_axis_weights(
    length: int,
    feather_width: int,
    taper_start: bool,
    taper_end: bool,
) -> np.ndarray:
    """Build a one-dimensional raised-cosine window.

    Exterior mosaic boundaries deliberately remain at weight one. Interior
    tile boundaries taper from/to zero so contributors enter and leave the
    normalized blend continuously.
    """
    if length <= 0:
        raise ValueError("weight-axis length must be positive")
    if feather_width < 0:
        raise ValueError("feather_width must be non-negative")
    if feather_width * 2 > length:
        raise ValueError("feather_width cannot exceed half the density width")

    weights = np.ones(length, dtype=np.float32)
    if feather_width == 0:
        return weights
    if feather_width == 1:
        ramp = np.zeros(1, dtype=np.float32)
    else:
        phase = np.linspace(0.0, 1.0, feather_width, dtype=np.float32)
        ramp = 0.5 - 0.5 * np.cos(np.pi * phase)
    if taper_start:
        weights[:feather_width] *= ramp
    if taper_end:
        weights[-feather_width:] *= ramp[::-1]
    return weights


def raised_cosine_patch_weights(
    height: int,
    width: int,
    feather_width: int,
    *,
    touches_top: bool,
    touches_bottom: bool,
    touches_left: bool,
    touches_right: bool,
) -> np.ndarray:
    row_weights = raised_cosine_axis_weights(
        height,
        feather_width,
        taper_start=not touches_top,
        taper_end=not touches_bottom,
    )
    column_weights = raised_cosine_axis_weights(
        width,
        feather_width,
        taper_start=not touches_left,
        taper_end=not touches_right,
    )
    return row_weights[:, None] * column_weights[None, :]


def _add_shifted_patch(
    density_sum: np.ndarray,
    weight_sum: np.ndarray,
    weighted_density: np.ndarray,
    patch_weight: np.ndarray,
    row_start: int,
    column_start: int,
    coefficient: float,
) -> None:
    """Add one integer-aligned component of a bilinear splat."""
    if coefficient <= 0:
        return
    patch_height, patch_width = patch_weight.shape
    output_height, output_width = weight_sum.shape
    output_row_start = max(0, row_start)
    output_column_start = max(0, column_start)
    output_row_end = min(output_height, row_start + patch_height)
    output_column_end = min(output_width, column_start + patch_width)
    if (
        output_row_start >= output_row_end
        or output_column_start >= output_column_end
    ):
        return

    patch_row_start = output_row_start - row_start
    patch_column_start = output_column_start - column_start
    patch_row_end = patch_row_start + (output_row_end - output_row_start)
    patch_column_end = patch_column_start + (
        output_column_end - output_column_start
    )
    output_slice = (
        slice(output_row_start, output_row_end),
        slice(output_column_start, output_column_end),
    )
    patch_slice = (
        slice(patch_row_start, patch_row_end),
        slice(patch_column_start, patch_column_end),
    )
    density_sum[output_slice] += coefficient * weighted_density[patch_slice]
    weight_sum[output_slice] += coefficient * patch_weight[patch_slice]


def splat_density_patch(
    density_sum: np.ndarray,
    weight_sum: np.ndarray,
    density: np.ndarray,
    patch_weight: np.ndarray,
    row_origin: float,
    column_origin: float,
) -> None:
    """Bilinearly splat a density patch with a shared fractional XY origin.

    Because every sample in a tile has the same fractional offset, the full
    splat reduces to four efficient contiguous NumPy slice additions.
    """
    if density.ndim != 3:
        raise ValueError("density must have shape (height, width, depth)")
    if patch_weight.shape != density.shape[:2]:
        raise ValueError("patch_weight must match density's XY shape")
    if not np.isfinite(row_origin) or not np.isfinite(column_origin):
        raise ValueError("density origins must be finite")

    row_floor = math.floor(row_origin)
    column_floor = math.floor(column_origin)
    row_fraction = row_origin - row_floor
    column_fraction = column_origin - column_floor
    weighted_density = density * patch_weight[..., None]
    for row_offset, row_coefficient in (
        (0, 1.0 - row_fraction),
        (1, row_fraction),
    ):
        for column_offset, column_coefficient in (
            (0, 1.0 - column_fraction),
            (1, column_fraction),
        ):
            _add_shifted_patch(
                density_sum,
                weight_sum,
                weighted_density,
                patch_weight,
                row_floor + row_offset,
                column_floor + column_offset,
                row_coefficient * column_coefficient,
            )


def accumulate_official_box(
    density_sum: np.ndarray,
    weight_sum: np.ndarray,
    density: np.ndarray,
    row_origin: int,
    column_origin: int,
    crop_edge: int,
    *,
    touches_top: bool,
    touches_bottom: bool,
    touches_left: bool,
    touches_right: bool,
) -> None:
    """Retain the previous hard-crop, equal-weight fusion exactly."""
    density_height, density_width = density.shape[:2]
    row_start_crop = 0 if touches_top else crop_edge
    row_end_crop = 0 if touches_bottom else crop_edge
    column_start_crop = 0 if touches_left else crop_edge
    column_end_crop = 0 if touches_right else crop_edge
    output_slice = (
        slice(
            row_origin + row_start_crop,
            row_origin + density_height - row_end_crop,
        ),
        slice(
            column_origin + column_start_crop,
            column_origin + density_width - column_end_crop,
        ),
    )
    density_slice = (
        slice(row_start_crop, density_height - row_end_crop),
        slice(column_start_crop, density_width - column_end_crop),
    )
    density_sum[output_slice] += density[density_slice]
    weight_sum[output_slice] += 1.0


def build_transform(model) -> T.Compose:
    network_size = model.unet_model.patch_size * 16
    return T.Compose(
        [
            T.Resize((network_size, network_size), interpolation=T.InterpolationMode.BICUBIC),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )


def main() -> None:
    args = parse_args()
    if args.image_window_size <= 0:
        raise ValueError("image_window_size must be positive")
    if not 0 <= args.overlap < 1:
        raise ValueError("overlap must satisfy 0 <= overlap < 1")
    step_size_was_explicit = args.step_size is not None
    step_size = (
        args.step_size
        if step_size_was_explicit
        else round(args.image_window_size * (1 - args.overlap))
    )
    if step_size <= 0:
        raise ValueError("step_size must be positive")
    if step_size > args.image_window_size:
        raise ValueError("step_size cannot exceed image_window_size")

    sys.path.insert(0, str(args.repo_root.resolve()))
    from source.generator import Sat3DGen

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    Sat3DGen._skip_backbone_weights = True
    model = Sat3DGen.from_pretrained(args.model_path).to(device).eval()
    Sat3DGen._skip_backbone_weights = False
    network_input_size = model.unet_model.patch_size * 16

    source_image = Image.open(args.satellite_img_path).convert("RGB")
    source_size = list(source_image.size)
    if args.preserve_source_pixels:
        prepared_image = source_image
        effective_prepared_resolution = args.input_pixel_resolution
    else:
        resize_factor = args.input_pixel_resolution / args.prepared_pixel_resolution
        prepared_size = (
            max(args.image_window_size, int(source_image.size[0] * resize_factor)),
            max(args.image_window_size, int(source_image.size[1] * resize_factor)),
        )
        prepared_image = source_image.resize(prepared_size, Image.Resampling.BICUBIC)
        effective_prepared_resolution = args.prepared_pixel_resolution
    process_width, process_height = prepared_image.size

    pad = int(
        round(
            (1 - model.position_scale_factor)
            * args.mesh_resolution
            / 2
        )
    )
    density_xy = args.mesh_resolution - 2 * pad
    density_height = args.mesh_resolution - pad
    density_scale = density_xy / args.image_window_size
    fusion_crop_edge = density_xy // 8
    feather_width = (
        fusion_crop_edge if args.feather_width is None else args.feather_width
    )
    if feather_width < 0:
        raise ValueError("feather_width must be non-negative")
    if feather_width * 2 > density_xy:
        raise ValueError("feather_width cannot exceed half the cropped density width")

    image_rows = axis_stops(
        process_height, args.image_window_size, step_size
    )
    image_columns = axis_stops(
        process_width, args.image_window_size, step_size
    )
    if args.fusion_mode == "official_box":
        density_rows, output_height = mapped_stops(
            image_rows, process_height, args.image_window_size, density_xy
        )
        density_columns, output_width = mapped_stops(
            image_columns, process_width, args.image_window_size, density_xy
        )
    else:
        density_rows, output_height = fractional_origins(
            image_rows, process_height, args.image_window_size, density_xy
        )
        density_columns, output_width = fractional_origins(
            image_columns, process_width, args.image_window_size, density_xy
        )

    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    prepared_image.save(output_root / "prepared_input.png")
    transform = build_transform(model)
    output_volume = np.zeros(
        (output_height, output_width, density_height), dtype=np.float32
    )
    output_weight = np.zeros((output_height, output_width), dtype=np.float32)
    window_count = len(image_rows) * len(image_columns)
    feather_weight_cache: dict[tuple[bool, bool, bool, bool], np.ndarray] = {}

    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    started = time.perf_counter()
    completed = 0
    for row_index, image_row in enumerate(image_rows):
        density_row = density_rows[row_index]
        for column_index, image_column in enumerate(image_columns):
            density_column = density_columns[column_index]
            tile = prepared_image.crop(
                (
                    image_column,
                    image_row,
                    image_column + args.image_window_size,
                    image_row + args.image_window_size,
                )
            )
            sat_input = transform(tile).unsqueeze(0).to(device)
            with torch.no_grad():
                density = model.save_shape_from_sat(
                    sat_input,
                    position_scale_factor=1,
                    crop=True,
                    grid_size=args.mesh_resolution,
                )
            density = np.moveaxis(density, 0, 1)
            expected_shape = (density_xy, density_xy, density_height)
            if density.shape != expected_shape:
                raise RuntimeError(
                    f"Expected density {expected_shape}, received {density.shape}"
                )

            touches_top = image_row == 0
            touches_bottom = (
                image_row + args.image_window_size == process_height
            )
            touches_left = image_column == 0
            touches_right = (
                image_column + args.image_window_size == process_width
            )
            if args.fusion_mode == "official_box":
                accumulate_official_box(
                    output_volume,
                    output_weight,
                    density,
                    int(density_row),
                    int(density_column),
                    fusion_crop_edge,
                    touches_top=touches_top,
                    touches_bottom=touches_bottom,
                    touches_left=touches_left,
                    touches_right=touches_right,
                )
            else:
                weight_key = (
                    touches_top,
                    touches_bottom,
                    touches_left,
                    touches_right,
                )
                if weight_key not in feather_weight_cache:
                    feather_weight_cache[weight_key] = raised_cosine_patch_weights(
                        density_xy,
                        density_xy,
                        feather_width,
                        touches_top=touches_top,
                        touches_bottom=touches_bottom,
                        touches_left=touches_left,
                        touches_right=touches_right,
                    )
                splat_density_patch(
                    output_volume,
                    output_weight,
                    density,
                    feather_weight_cache[weight_key],
                    float(density_row),
                    float(density_column),
                )
            completed += 1
            peak_gib = (
                torch.cuda.max_memory_allocated(device) / 2**30
                if device.type == "cuda"
                else 0.0
            )
            print(
                f"window {completed}/{window_count}; "
                f"peak allocated {peak_gib:.2f} GiB",
                flush=True,
            )

    if not np.all(np.isfinite(output_weight)):
        raise RuntimeError("Density fusion produced non-finite weights")
    uncovered = int(np.count_nonzero(output_weight <= 0))
    if uncovered:
        raise RuntimeError(f"Density fusion left {uncovered} zero-weight cells")
    fusion_weight_min = float(output_weight.min())
    fusion_weight_max = float(output_weight.max())
    output_volume /= output_weight[..., None]
    np.savez_compressed(output_root / "density_volume.npz", density=output_volume)

    vertices, faces, _, _ = measure.marching_cubes(
        output_volume, level=args.mesh_level
    )
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.export(output_root / "mesh.ply")
    elapsed = time.perf_counter() - started

    metadata = {
        "source_image": str(args.satellite_img_path.resolve()),
        "source_image_size_px": source_size,
        "input_pixel_resolution_m": args.input_pixel_resolution,
        "prepared_image_size_px": list(prepared_image.size),
        "prepared_pixel_resolution_m": effective_prepared_resolution,
        "preserve_source_pixels": args.preserve_source_pixels,
        "approximate_ground_extent_m": [
            process_width * effective_prepared_resolution,
            process_height * effective_prepared_resolution,
        ],
        "image_window_size_px": args.image_window_size,
        "network_input_size_px": network_input_size,
        "source_to_network_linear_scale": (
            network_input_size / args.image_window_size
        ),
        "source_window_ground_extent_m": (
            args.image_window_size * effective_prepared_resolution
        ),
        "requested_overlap_fraction": args.overlap,
        "overlap_applied_to_derive_step": not step_size_was_explicit,
        "step_size_was_explicit": step_size_was_explicit,
        "image_step_size_px": step_size,
        "nominal_overlap_fraction": 1 - step_size / args.image_window_size,
        "image_column_positions_px": image_columns,
        "image_row_positions_px": image_rows,
        "actual_x_overlap_fractions": adjacent_overlap_fractions(
            image_columns, args.image_window_size
        ),
        "actual_y_overlap_fractions": adjacent_overlap_fractions(
            image_rows, args.image_window_size
        ),
        "mesh_resolution": args.mesh_resolution,
        "model_position_scale_factor": model.position_scale_factor,
        "model_crop_pad_voxels": pad,
        "cropped_density_shape": [density_xy, density_xy, density_height],
        "density_to_image_scale": density_scale,
        "fusion_mode": args.fusion_mode,
        "fusion_crop_edge_voxels": fusion_crop_edge,
        "fusion_feather_profile": (
            "raised_cosine" if args.fusion_mode == "fractional_feather" else None
        ),
        "fusion_feather_width_voxels": (
            feather_width if args.fusion_mode == "fractional_feather" else None
        ),
        "fusion_density_origin_units": "density_voxels",
        "fusion_density_column_origins": [
            float(origin) for origin in density_columns
        ],
        "fusion_density_row_origins": [float(origin) for origin in density_rows],
        "fusion_fractional_splat": (
            "bilinear_xy" if args.fusion_mode == "fractional_feather" else None
        ),
        "fusion_weight_min": fusion_weight_min,
        "fusion_weight_max": fusion_weight_max,
        "fusion_zero_weight_cells": uncovered,
        "window_grid": [len(image_columns), len(image_rows)],
        "window_count": window_count,
        "density_volume_shape": list(output_volume.shape),
        "mesh_level": args.mesh_level,
        "mesh_vertices": int(len(vertices)),
        "mesh_faces": int(len(faces)),
        "elapsed_seconds": elapsed,
        "device": str(device),
        "peak_cuda_allocated_gib": (
            torch.cuda.max_memory_allocated(device) / 2**30
            if device.type == "cuda"
            else 0.0
        ),
        "upstream_forward_grid_modified": False,
        "query_batch_modified": False,
    }
    (output_root / "run_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
