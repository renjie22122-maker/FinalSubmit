"""Memory-bounded Sat3DGen large-image inference with a 192 px fusion window.

This runner preserves the physical footprint seen by the released 256 px model:
the large raster is resampled so that a 192 px source window spans the same
ground width as a 256 px window at 0.28 m/px.  A 240^3 query grid becomes
192 x 192 x 216 after Sat3DGen's configured 0.8 spatial crop.

The upstream repository is imported read-only.  Its ``forward_grid`` method is
replaced only on the loaded model instance so voxel coordinates and density
outputs can be transferred in bounded chunks instead of residing entirely on
the GPU.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import types
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
    parser.add_argument("--model_path", default="qian43/Sat3DGen")
    parser.add_argument("--window_size", type=int, default=192)
    parser.add_argument("--step_size", type=int, default=96)
    parser.add_argument("--grid_size", type=int, default=240)
    parser.add_argument("--max_query_batch", type=int, default=2_000_000)
    parser.add_argument("--model_pixel_resolution", type=float, default=0.28)
    parser.add_argument("--model_input_size", type=int, default=256)
    parser.add_argument(
        "--prepared_pixel_resolution",
        type=float,
        default=None,
        help=(
            "Optional prepared-raster resolution in m/px. By default it is "
            "scaled so a smaller source window retains the 256 px model's "
            "physical footprint."
        ),
    )
    parser.add_argument(
        "--preserve_rectangular",
        action="store_true",
        help="Preserve the prepared raster aspect ratio instead of making it square.",
    )
    parser.add_argument(
        "--max_process_size",
        type=int,
        default=1000,
        help="Maximum prepared square side in pixels; use 0 to disable the cap.",
    )
    parser.add_argument("--mesh_level", type=float, default=4.5)
    parser.add_argument("--output_dir", type=Path, required=True)
    return parser.parse_args()


def axis_stops(process_size: int, window_size: int, step_size: int) -> list[int]:
    if process_size < window_size:
        raise ValueError(
            f"Prepared image ({process_size}px) is smaller than the "
            f"requested window ({window_size}px)."
        )
    stops = list(range(0, process_size - window_size + 1, step_size))
    final_stop = process_size - window_size
    if stops[-1] != final_stop:
        stops.append(final_stop)
    return stops


def iter_tiles(
    process_height: int,
    process_width: int,
    window_size: int,
    step_size: int,
):
    for row in axis_stops(process_height, window_size, step_size):
        for column in axis_stops(process_width, window_size, step_size):
            yield row, column


def prepare_image(
    image: Image.Image,
    input_mpp: float,
    model_mpp: float,
    model_input_size: int,
    window_size: int,
    max_process_size: int,
    prepared_pixel_resolution: float | None,
    preserve_rectangular: bool,
) -> tuple[Image.Image, float]:
    # A smaller source window is enlarged to the fixed model tensor size.  Its
    # pixels must therefore represent proportionally more ground distance.
    prepared_mpp = (
        prepared_pixel_resolution
        if prepared_pixel_resolution is not None
        else model_mpp * model_input_size / window_size
    )
    factor = input_mpp / prepared_mpp
    new_size = (
        max(window_size, int(image.size[0] * factor)),
        max(window_size, int(image.size[1] * factor)),
    )
    image = image.resize(new_size, Image.Resampling.BICUBIC)

    if preserve_rectangular:
        if max_process_size <= 0:
            return image, prepared_mpp
        crop_width = min(image.size[0], max_process_size)
        crop_height = min(image.size[1], max_process_size)
        left = (image.size[0] - crop_width) // 2
        top = (image.size[1] - crop_height) // 2
        image = image.crop(
            (left, top, left + crop_width, top + crop_height)
        )
        return image, prepared_mpp

    if max_process_size > 0 and min(image.size) > max_process_size:
        half_size = max_process_size // 2
        left = image.size[0] // 2 - half_size
        top = image.size[1] // 2 - half_size
        image = image.crop(
            (left, top, left + max_process_size, top + max_process_size)
        )

    process_size = min(image.size)
    left = (image.size[0] - process_size) // 2
    top = (image.size[1] - process_size) // 2
    return image.crop((left, top, left + process_size, top + process_size)), prepared_mpp


def build_transform(model) -> T.Compose:
    network_size = model.unet_model.patch_size * 16
    return T.Compose(
        [
            T.Resize((network_size, network_size), interpolation=T.InterpolationMode.BICUBIC),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )


def bind_memory_bounded_forward_grid(model, create_voxel, max_query_batch: int) -> None:
    @torch.no_grad()
    def forward_grid(self, planes, grid_size=240, position_scale_factor=1, crop=False):
        del position_scale_factor  # Kept for compatibility with the upstream signature.
        device = self._current_device(planes)
        # Keep the complete coordinate grid and accumulated density on CPU.
        voxel_grid = create_voxel(N=grid_size, position_scale_factor=1)["voxel_grid"]
        point_count = voxel_grid.shape[1]
        densities = np.empty(point_count, dtype=np.float32)

        for head in range(0, point_count, max_query_batch):
            tail = min(head + max_query_batch, point_count)
            coordinates = voxel_grid[:, head:tail].to(device, non_blocking=True)
            density = self.density_reg(coordinates=coordinates, triplane_ori=planes)
            densities[head:tail] = density.detach().float().cpu().numpy().reshape(-1)
            del coordinates, density

        densities = densities.reshape((grid_size, grid_size, grid_size))
        if self.position_scale_factor < 1:
            pad = int(round((1 - self.position_scale_factor) * grid_size / 2))
            if crop:
                densities = densities[pad:-pad, pad:-pad, pad:]
            else:
                densities[:pad] = 0
                densities[-pad:] = 0
                densities[:, :pad] = 0
                densities[:, -pad:] = 0
                densities[:, :, :pad] = 0
        return densities

    model.forward_grid = types.MethodType(forward_grid, model)


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    sys.path.insert(0, str(repo_root))

    from source.generator import Sat3DGen
    from source.rendering.utils import create_voxel

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    Sat3DGen._skip_backbone_weights = True
    model = Sat3DGen.from_pretrained(args.model_path).to(device).eval()
    Sat3DGen._skip_backbone_weights = False
    bind_memory_bounded_forward_grid(model, create_voxel, args.max_query_batch)

    pad = int(round((1 - model.position_scale_factor) * args.grid_size / 2))
    density_xy = args.grid_size - 2 * pad
    density_height = args.grid_size - pad
    if density_xy != args.window_size:
        raise ValueError(
            f"grid_size={args.grid_size} and model scale "
            f"{model.position_scale_factor} produce {density_xy}px, not "
            f"window_size={args.window_size}px."
        )

    image = Image.open(args.satellite_img_path).convert("RGB")
    source_image_size = list(image.size)
    image, prepared_mpp = prepare_image(
        image,
        args.input_pixel_resolution,
        args.model_pixel_resolution,
        args.model_input_size,
        args.window_size,
        args.max_process_size,
        args.prepared_pixel_resolution,
        args.preserve_rectangular,
    )

    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    image.save(output_root / "prepared_input.png")
    transform = build_transform(model)
    process_width, process_height = image.size
    crop_edge = args.window_size // 8
    tile_positions = list(
        iter_tiles(
            process_height,
            process_width,
            args.window_size,
            args.step_size,
        )
    )
    output_volume = np.zeros(
        (process_height, process_width, density_height), dtype=np.float32
    )
    output_count = np.zeros((process_height, process_width), dtype=np.float32)

    started = time.perf_counter()
    for tile_number, (row, column) in enumerate(tile_positions, start=1):
        tile = image.crop(
            (column, row, column + args.window_size, row + args.window_size)
        )
        sat_input = transform(tile).unsqueeze(0).to(device)
        with torch.no_grad():
            density = model.save_shape_from_sat(
                sat_input, position_scale_factor=1, crop=True, grid_size=args.grid_size
            )
        density = np.moveaxis(density, 0, 1)
        if density.shape != (args.window_size, args.window_size, density_height):
            raise RuntimeError(f"Unexpected cropped density shape: {density.shape}")

        row_start_crop = 0 if row == 0 else crop_edge
        row_end_crop = 0 if row + args.window_size == process_height else crop_edge
        col_start_crop = 0 if column == 0 else crop_edge
        col_end_crop = 0 if column + args.window_size == process_width else crop_edge

        row_slice = slice(
            row + row_start_crop,
            row + args.window_size - row_end_crop,
        )
        col_slice = slice(
            column + col_start_crop,
            column + args.window_size - col_end_crop,
        )
        density_row_slice = slice(
            row_start_crop,
            density.shape[0] - row_end_crop,
        )
        density_col_slice = slice(
            col_start_crop,
            density.shape[1] - col_end_crop,
        )
        output_volume[row_slice, col_slice] += density[
            density_row_slice, density_col_slice
        ]
        output_count[row_slice, col_slice] += 1

        del sat_input, density
        if device.type == "cuda":
            peak_gib = torch.cuda.max_memory_allocated(device) / 2**30
            torch.cuda.empty_cache()
        else:
            peak_gib = 0.0
        print(
            f"window {tile_number}/{len(tile_positions)} at ({column},{row}); "
            f"peak allocated {peak_gib:.2f} GiB",
            flush=True,
        )

    if np.any(output_count == 0):
        raise RuntimeError("Fusion left uncovered pixels in the prepared image.")
    output_volume /= output_count[..., None]
    np.savez_compressed(output_root / "density_volume.npz", density=output_volume)

    vertices, faces, _, _ = measure.marching_cubes(
        output_volume, level=args.mesh_level
    )
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.export(output_root / "mesh.ply")

    elapsed = time.perf_counter() - started
    metadata = {
        "source_image": str(args.satellite_img_path.resolve()),
        "source_image_size_px": source_image_size,
        "input_pixel_resolution_m": args.input_pixel_resolution,
        "prepared_image_size_px": list(image.size),
        "prepared_pixel_resolution_m": prepared_mpp,
        "approximate_ground_extent_m": [
            image.size[0] * prepared_mpp,
            image.size[1] * prepared_mpp,
        ],
        "window_ground_extent_m": args.window_size * prepared_mpp,
        "reference_model_ground_extent_m": (
            args.model_input_size * args.model_pixel_resolution
        ),
        "apparent_scale_relative_to_reference": (
            args.model_input_size
            * args.model_pixel_resolution
            / (args.window_size * prepared_mpp)
        ),
        "model_input_size_px": args.model_input_size,
        "max_process_size_px": args.max_process_size,
        "preserve_rectangular": args.preserve_rectangular,
        "window_size_px": args.window_size,
        "step_size_px": args.step_size,
        "window_count": len(tile_positions),
        "grid_size": args.grid_size,
        "cropped_density_shape": [
            args.window_size,
            args.window_size,
            density_height,
        ],
        "max_query_batch": args.max_query_batch,
        "mesh_level": args.mesh_level,
        "density_volume_shape": list(output_volume.shape),
        "mesh_vertices": int(len(vertices)),
        "mesh_faces": int(len(faces)),
        "elapsed_seconds": elapsed,
        "device": str(device),
        "peak_cuda_allocated_gib": (
            torch.cuda.max_memory_allocated(device) / 2**30
            if device.type == "cuda"
            else 0.0
        ),
    }
    (output_root / "run_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
