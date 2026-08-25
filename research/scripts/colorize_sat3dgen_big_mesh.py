"""Add blended Sat3DGen vertex colours to an existing large-image mesh.

The first large-image pass fuses per-window density fields and extracts one
global mesh.  It intentionally discards the per-window triplanes required for
colour prediction.  This script performs a memory-bounded second pass:

* reconstruct the exact fractional, raised-cosine spatial contribution of
  every inference window;
* regenerate one triplane at a time from ``prepared_input.png``;
* query only global mesh vertices influenced by that window; and
* blend RGB values in floating point with the same spatial contribution
  weights before exporting a colour PLY.

Geometry and face order are preserved.  The model's ``forward_grid`` method
and query batch are not modified.  A compact zero style vector is passed to
the colour MLP; this is mathematically identical to its ``w_sky=None`` branch
but avoids constructing and transforming a 512-value zero vector per vertex.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import torch
import trimesh
from PIL import Image


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_sat3dgen_big_image_app192 import (  # noqa: E402
    build_transform,
    raised_cosine_axis_weights,
)


@dataclass(frozen=True)
class SpatialBins:
    """Compact row-major uniform-bin index over horizontal mesh positions."""

    order: np.ndarray
    offsets: np.ndarray
    bin_size: float
    row_count: int
    column_count: int


@dataclass(frozen=True)
class WindowSpec:
    image_row: int
    image_column: int
    density_row: float
    density_column: float
    row_weights: np.ndarray
    column_weights: np.ndarray


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Colour an existing fractional-feather Sat3DGen big mesh."
    )
    parser.add_argument("--repo_root", type=Path, required=True)
    parser.add_argument("--result_dir", type=Path, required=True)
    parser.add_argument("--model_path", default="qian43/Sat3DGen")
    parser.add_argument("--mesh_path", type=Path, default=None)
    parser.add_argument("--prepared_image_path", type=Path, default=None)
    parser.add_argument("--output_path", type=Path, default=None)
    parser.add_argument("--color_batch_size", type=int, default=131_072)
    parser.add_argument("--spatial_bin_size", type=float, default=4.0)
    parser.add_argument(
        "--preflight_only",
        action="store_true",
        help="Validate spatial colour coverage without loading the model.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing colour mesh or metadata file.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path, block_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def require_metadata(metadata: dict, keys: Iterable[str]) -> None:
    missing = [key for key in keys if key not in metadata]
    if missing:
        raise KeyError(f"run_metadata.json is missing: {', '.join(missing)}")


def load_geometry(mesh_path: Path) -> tuple[trimesh.Trimesh, np.ndarray]:
    loaded = trimesh.load_mesh(mesh_path, process=False)
    if not isinstance(loaded, trimesh.Trimesh):
        raise TypeError(f"Expected one Trimesh in {mesh_path}, got {type(loaded)}")
    if loaded.vertices.ndim != 2 or loaded.vertices.shape[1] != 3:
        raise ValueError("Mesh vertices must have shape (N, 3)")
    if loaded.faces.ndim != 2 or loaded.faces.shape[1] != 3:
        raise ValueError("Only triangular meshes are supported")
    vertices = np.asarray(loaded.vertices, dtype=np.float32)
    if not np.all(np.isfinite(vertices)):
        raise ValueError("Mesh contains non-finite vertices")
    return loaded, vertices


def build_spatial_bins(
    vertices: np.ndarray,
    output_height: int,
    output_width: int,
    bin_size: float,
) -> SpatialBins:
    if bin_size <= 0 or not np.isfinite(bin_size):
        raise ValueError("spatial_bin_size must be finite and positive")
    if output_height <= 0 or output_width <= 0:
        raise ValueError("Output dimensions must be positive")
    if np.any(vertices[:, 0] < -1e-4) or np.any(vertices[:, 1] < -1e-4):
        raise ValueError("Mesh has negative horizontal coordinates")
    if np.any(vertices[:, 0] > output_height - 1 + 1e-3):
        raise ValueError("Mesh rows exceed the fused density volume")
    if np.any(vertices[:, 1] > output_width - 1 + 1e-3):
        raise ValueError("Mesh columns exceed the fused density volume")

    row_count = int(math.ceil(output_height / bin_size))
    column_count = int(math.ceil(output_width / bin_size))
    bin_rows = np.floor(vertices[:, 0] / bin_size).astype(np.int64)
    bin_columns = np.floor(vertices[:, 1] / bin_size).astype(np.int64)
    np.clip(bin_rows, 0, row_count - 1, out=bin_rows)
    np.clip(bin_columns, 0, column_count - 1, out=bin_columns)
    cell_ids = bin_rows * column_count + bin_columns
    order = np.argsort(cell_ids, kind="stable").astype(np.uint32, copy=False)
    counts = np.bincount(
        cell_ids, minlength=row_count * column_count
    ).astype(np.int64, copy=False)
    offsets = np.empty(counts.size + 1, dtype=np.int64)
    offsets[0] = 0
    np.cumsum(counts, out=offsets[1:])
    return SpatialBins(order, offsets, bin_size, row_count, column_count)


def query_spatial_bins(
    index: SpatialBins,
    row_min: float,
    row_max: float,
    column_min: float,
    column_max: float,
) -> np.ndarray:
    """Return coarse candidates from bins intersecting a rectangle."""
    if row_max < row_min or column_max < column_min:
        return np.empty(0, dtype=np.uint32)
    first_row = max(0, int(math.floor(row_min / index.bin_size)))
    last_row = min(
        index.row_count - 1, int(math.floor(row_max / index.bin_size))
    )
    first_column = max(0, int(math.floor(column_min / index.bin_size)))
    last_column = min(
        index.column_count - 1,
        int(math.floor(column_max / index.bin_size)),
    )
    if first_row > last_row or first_column > last_column:
        return np.empty(0, dtype=np.uint32)

    chunks: list[np.ndarray] = []
    for bin_row in range(first_row, last_row + 1):
        first_cell = bin_row * index.column_count + first_column
        after_cell = bin_row * index.column_count + last_column + 1
        start = int(index.offsets[first_cell])
        stop = int(index.offsets[after_cell])
        if start < stop:
            chunks.append(index.order[start:stop])
    if not chunks:
        return np.empty(0, dtype=np.uint32)
    if len(chunks) == 1:
        return chunks[0]
    return np.concatenate(chunks)


def splatted_axis_weight_at(
    positions: np.ndarray,
    origin: float,
    source_weights: np.ndarray,
) -> np.ndarray:
    """Sample the exact fractional-splat weight at continuous coordinates.

    The density pass first splats the discrete source weights onto integer
    global cells, then Marching Cubes linearly interpolates between those
    cells.  Both operations are reproduced here.
    """
    positions = np.asarray(positions, dtype=np.float64)
    source_weights = np.asarray(source_weights, dtype=np.float32)
    origin_floor = math.floor(origin)
    origin_fraction = float(origin - origin_floor)
    integer_floor = np.floor(positions).astype(np.int64)
    interpolation_fraction = positions - integer_floor

    def integer_splat(integer_positions: np.ndarray) -> np.ndarray:
        local = integer_positions - origin_floor
        result = np.zeros(integer_positions.shape, dtype=np.float32)
        direct = (local >= 0) & (local < source_weights.size)
        if np.any(direct):
            result[direct] += np.float32(1.0 - origin_fraction) * source_weights[
                local[direct]
            ]
        shifted_local = local - 1
        shifted = (shifted_local >= 0) & (
            shifted_local < source_weights.size
        )
        if np.any(shifted):
            result[shifted] += np.float32(origin_fraction) * source_weights[
                shifted_local[shifted]
            ]
        return result

    lower = integer_splat(integer_floor)
    upper = integer_splat(integer_floor + 1)
    return (
        lower * (1.0 - interpolation_fraction)
        + upper * interpolation_fraction
    ).astype(np.float32, copy=False)


def window_vertex_weights(
    vertices: np.ndarray,
    index: SpatialBins,
    window: WindowSpec,
) -> tuple[np.ndarray, np.ndarray]:
    """Return vertex ids and exact positive 2-D contribution weights."""
    length = int(window.row_weights.size)
    if window.column_weights.size != length:
        raise ValueError("Only square density windows are supported")
    row_base = math.floor(window.density_row)
    column_base = math.floor(window.density_column)
    # Linear interpolation of the splatted edge can extend one global cell
    # beyond the discrete patch support.  Exact weights remove false positives.
    candidates = query_spatial_bins(
        index,
        row_base - 1.0,
        row_base + length + 1.0,
        column_base - 1.0,
        column_base + length + 1.0,
    )
    if candidates.size == 0:
        return candidates, np.empty(0, dtype=np.float32)
    candidate_vertices = vertices[candidates]
    row_weight = splatted_axis_weight_at(
        candidate_vertices[:, 0], window.density_row, window.row_weights
    )
    column_weight = splatted_axis_weight_at(
        candidate_vertices[:, 1],
        window.density_column,
        window.column_weights,
    )
    weights = row_weight * column_weight
    selected = weights > 0.0
    return candidates[selected], weights[selected]


def make_windows(metadata: dict) -> list[WindowSpec]:
    image_rows = [int(value) for value in metadata["image_row_positions_px"]]
    image_columns = [
        int(value) for value in metadata["image_column_positions_px"]
    ]
    density_rows = [
        float(value) for value in metadata["fusion_density_row_origins"]
    ]
    density_columns = [
        float(value) for value in metadata["fusion_density_column_origins"]
    ]
    if len(image_rows) != len(density_rows):
        raise ValueError("Image-row and density-row counts differ")
    if len(image_columns) != len(density_columns):
        raise ValueError("Image-column and density-column counts differ")

    image_width, image_height = [
        int(value) for value in metadata["prepared_image_size_px"]
    ]
    image_window = int(metadata["image_window_size_px"])
    density_xy = int(metadata["cropped_density_shape"][0])
    feather_width = int(metadata["fusion_feather_width_voxels"])
    axis_cache: dict[tuple[bool, bool], np.ndarray] = {}

    def axis_weights(touches_start: bool, touches_end: bool) -> np.ndarray:
        key = (touches_start, touches_end)
        if key not in axis_cache:
            axis_cache[key] = raised_cosine_axis_weights(
                density_xy,
                feather_width,
                taper_start=not touches_start,
                taper_end=not touches_end,
            )
        return axis_cache[key]

    windows: list[WindowSpec] = []
    for image_row, density_row in zip(image_rows, density_rows):
        touches_top = image_row == 0
        touches_bottom = image_row + image_window == image_height
        row_weights = axis_weights(touches_top, touches_bottom)
        for image_column, density_column in zip(
            image_columns, density_columns
        ):
            touches_left = image_column == 0
            touches_right = image_column + image_window == image_width
            windows.append(
                WindowSpec(
                    image_row=image_row,
                    image_column=image_column,
                    density_row=density_row,
                    density_column=density_column,
                    row_weights=row_weights,
                    column_weights=axis_weights(touches_left, touches_right),
                )
            )
    return windows


def preflight_coverage(
    vertices: np.ndarray,
    index: SpatialBins,
    windows: list[WindowSpec],
) -> tuple[np.ndarray, np.ndarray, int]:
    weight_sum = np.zeros(vertices.shape[0], dtype=np.float32)
    contributor_count = np.zeros(vertices.shape[0], dtype=np.uint16)
    total_contributions = 0
    for window in windows:
        vertex_ids, weights = window_vertex_weights(vertices, index, window)
        weight_sum[vertex_ids] += weights
        contributor_count[vertex_ids] += 1
        total_contributions += int(vertex_ids.size)
    if not np.all(np.isfinite(weight_sum)):
        raise RuntimeError("Colour preflight produced non-finite weights")
    uncovered = int(np.count_nonzero(weight_sum <= 0.0))
    if uncovered:
        raise RuntimeError(
            f"Colour preflight left {uncovered} of {len(vertices)} vertices uncovered"
        )
    return weight_sum, contributor_count, total_contributions


def model_query_coordinates(
    vertices: np.ndarray,
    window: WindowSpec,
    density_xy: int,
    density_height: int,
    pad: int,
    mesh_resolution: int,
) -> np.ndarray:
    """Map global [row, col, z] vertices to model [x, y, z] coordinates.

    Fractional-splat halo vertices retain their contribution weight, but the
    query itself is clamped to the first/last sampled cropped-density cell.
    This mirrors the edge sample that created the halo rather than
    extrapolating the latent colour field beyond it.
    """
    local_row = np.clip(
        vertices[:, 0] - window.density_row, 0.0, density_xy - 1.0
    )
    local_column = np.clip(
        vertices[:, 1] - window.density_column, 0.0, density_xy - 1.0
    )
    local_height = np.clip(vertices[:, 2], 0.0, density_height - 1.0)
    full_grid = np.stack(
        (
            local_column + pad,
            local_row + pad,
            local_height + pad,
        ),
        axis=1,
    ).astype(np.float32, copy=False)
    coordinates = full_grid * np.float32(2.0 / (mesh_resolution - 1)) - 1.0
    if not np.all(np.isfinite(coordinates)):
        raise RuntimeError("Model query coordinates are non-finite")
    if np.min(coordinates) < -1.0001 or np.max(coordinates) > 1.0001:
        raise RuntimeError("Model query coordinates exceed [-1, 1]")
    return coordinates


def query_rgb(
    model,
    planes,
    coordinates: np.ndarray,
    zero_style: torch.Tensor,
    device: torch.device,
) -> np.ndarray:
    coordinate_tensor = torch.from_numpy(coordinates).to(device).unsqueeze(0)
    with torch.inference_mode():
        rgb = model.density_reg(
            coordinate_tensor,
            planes,
            sample_color=True,
            w_sky=zero_style,
        )
    rgb_array = rgb.squeeze(0).float().cpu().numpy()
    if rgb_array.shape != (coordinates.shape[0], 3):
        raise RuntimeError(
            f"Expected RGB shape {(coordinates.shape[0], 3)}, got {rgb_array.shape}"
        )
    if not np.all(np.isfinite(rgb_array)):
        raise RuntimeError("Model produced non-finite RGB values")
    return rgb_array.astype(np.float32, copy=False)


def export_coloured_mesh(
    source_mesh: trimesh.Trimesh,
    rgb_u8: np.ndarray,
    output_path: Path,
) -> None:
    if rgb_u8.shape != (len(source_mesh.vertices), 3):
        raise ValueError("RGB array does not match mesh vertex count")
    alpha = np.full((rgb_u8.shape[0], 1), 255, dtype=np.uint8)
    rgba = np.concatenate((rgb_u8, alpha), axis=1)
    coloured = trimesh.Trimesh(
        vertices=np.asarray(source_mesh.vertices),
        faces=np.asarray(source_mesh.faces),
        vertex_colors=rgba,
        process=False,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    coloured.export(output_path)


def verify_coloured_mesh(
    source_mesh: trimesh.Trimesh,
    rgb_u8: np.ndarray,
    output_path: Path,
) -> dict:
    loaded = trimesh.load_mesh(output_path, process=False)
    if not isinstance(loaded, trimesh.Trimesh):
        raise RuntimeError("Exported colour PLY did not reload as one mesh")
    if len(loaded.vertices) != len(source_mesh.vertices):
        raise RuntimeError("Colour export changed the vertex count")
    if len(loaded.faces) != len(source_mesh.faces):
        raise RuntimeError("Colour export changed the face count")
    if not np.allclose(
        np.asarray(loaded.vertices),
        np.asarray(source_mesh.vertices),
        rtol=0.0,
        atol=1e-6,
    ):
        raise RuntimeError("Colour export changed vertex positions")
    if not np.array_equal(
        np.asarray(loaded.faces), np.asarray(source_mesh.faces)
    ):
        raise RuntimeError("Colour export changed face indices or order")
    loaded_rgba = np.asarray(loaded.visual.vertex_colors, dtype=np.uint8)
    if loaded_rgba.shape != (len(source_mesh.vertices), 4):
        raise RuntimeError("Exported PLY does not contain per-vertex RGBA")
    if not np.array_equal(loaded_rgba[:, :3], rgb_u8):
        raise RuntimeError("Reloaded PLY colours differ from the exported RGB")
    if not np.all(loaded_rgba[:, 3] == 255):
        raise RuntimeError("Exported PLY alpha is not fully opaque")
    return {
        "reloaded_vertex_count": int(len(loaded.vertices)),
        "reloaded_face_count": int(len(loaded.faces)),
        "geometry_unchanged": True,
        "rgb_roundtrip_exact": True,
        "alpha_opaque": True,
    }


def main() -> None:
    args = parse_args()
    if args.color_batch_size <= 0:
        raise ValueError("color_batch_size must be positive")
    result_dir = args.result_dir.resolve()
    metadata_path = result_dir / "run_metadata.json"
    metadata = read_json(metadata_path)
    require_metadata(
        metadata,
        (
            "prepared_image_size_px",
            "image_window_size_px",
            "image_row_positions_px",
            "image_column_positions_px",
            "mesh_resolution",
            "model_crop_pad_voxels",
            "cropped_density_shape",
            "fusion_mode",
            "fusion_feather_width_voxels",
            "fusion_density_row_origins",
            "fusion_density_column_origins",
            "density_volume_shape",
            "mesh_vertices",
            "mesh_faces",
        ),
    )
    if metadata["fusion_mode"] != "fractional_feather":
        raise ValueError(
            "This colour pass currently requires fractional_feather geometry fusion"
        )
    if metadata.get("fusion_fractional_splat") != "bilinear_xy":
        raise ValueError("Expected bilinear_xy fractional density splatting")

    mesh_path = (
        args.mesh_path.resolve()
        if args.mesh_path is not None
        else result_dir / "mesh.ply"
    )
    prepared_image_path = (
        args.prepared_image_path.resolve()
        if args.prepared_image_path is not None
        else result_dir / "prepared_input.png"
    )
    output_path = (
        args.output_path.resolve()
        if args.output_path is not None
        else result_dir / "mesh_colored.ply"
    )
    color_metadata_path = output_path.with_name("color_metadata.json")
    preflight_path = output_path.with_name("color_preflight.json")
    for path in (mesh_path, prepared_image_path, metadata_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if not args.preflight_only and not args.overwrite:
        existing = [path for path in (output_path, color_metadata_path) if path.exists()]
        if existing:
            raise FileExistsError(
                "Refusing to overwrite existing output: "
                + ", ".join(str(path) for path in existing)
            )

    source_mesh, vertices = load_geometry(mesh_path)
    if len(vertices) != int(metadata["mesh_vertices"]):
        raise ValueError("Mesh vertex count differs from run metadata")
    if len(source_mesh.faces) != int(metadata["mesh_faces"]):
        raise ValueError("Mesh face count differs from run metadata")
    output_height, output_width, density_height = [
        int(value) for value in metadata["density_volume_shape"]
    ]
    density_xy = int(metadata["cropped_density_shape"][0])
    if [density_xy, density_xy, density_height] != [
        int(value) for value in metadata["cropped_density_shape"]
    ]:
        raise ValueError("Unexpected cropped density shape")
    mesh_resolution = int(metadata["mesh_resolution"])
    pad = int(metadata["model_crop_pad_voxels"])
    if density_xy != mesh_resolution - 2 * pad:
        raise ValueError("XY crop size is inconsistent with mesh resolution and pad")
    if density_height != mesh_resolution - pad:
        raise ValueError("Z crop size is inconsistent with mesh resolution and pad")

    image = Image.open(prepared_image_path).convert("RGB")
    if list(image.size) != [
        int(value) for value in metadata["prepared_image_size_px"]
    ]:
        raise ValueError("prepared_input.png size differs from run metadata")
    windows = make_windows(metadata)
    if len(windows) != int(metadata.get("window_count", len(windows))):
        raise ValueError("Window count differs from run metadata")

    started = time.perf_counter()
    spatial_index = build_spatial_bins(
        vertices,
        output_height,
        output_width,
        args.spatial_bin_size,
    )
    weight_sum, contributor_count, total_contributions = preflight_coverage(
        vertices, spatial_index, windows
    )
    preflight = {
        "mesh_path": str(mesh_path),
        "vertex_count": int(len(vertices)),
        "window_count": int(len(windows)),
        "total_positive_contributions": total_contributions,
        "contributors_per_vertex_min": int(contributor_count.min()),
        "contributors_per_vertex_max": int(contributor_count.max()),
        "contributors_per_vertex_mean": float(contributor_count.mean()),
        "weight_sum_min": float(weight_sum.min()),
        "weight_sum_max": float(weight_sum.max()),
        "weight_sum_mean": float(weight_sum.mean()),
        "zero_weight_vertices": int(np.count_nonzero(weight_sum <= 0.0)),
        "spatial_bin_size_density_voxels": args.spatial_bin_size,
        "color_weight_semantics": (
            "raised_cosine_then_fractional_bilinear_splat_then_"
            "continuous_vertex_interpolation"
        ),
        "color_query_halo_policy": "clamp_to_cropped_density_support",
        "preflight_elapsed_seconds": time.perf_counter() - started,
    }
    preflight_path.write_text(json.dumps(preflight, indent=2), encoding="utf-8")
    print(json.dumps(preflight, indent=2), flush=True)
    if args.preflight_only:
        return

    sys.path.insert(0, str(args.repo_root.resolve()))
    from source.generator import Sat3DGen

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    Sat3DGen._skip_backbone_weights = True
    model = Sat3DGen.from_pretrained(args.model_path).to(device).eval()
    Sat3DGen._skip_backbone_weights = False
    if int(round((1.0 - float(model.position_scale_factor)) * mesh_resolution / 2)) != pad:
        raise ValueError("Loaded model crop differs from the geometry pass")
    transform = build_transform(model)
    network_input_size = int(model.unet_model.patch_size * 16)
    if network_input_size != int(metadata.get("network_input_size_px", 256)):
        raise ValueError("Loaded model input size differs from run metadata")
    zero_style = torch.zeros(
        (1, int(model.mlp.style_dim)), dtype=torch.float32, device=device
    )

    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
    rgb_sum = np.zeros((len(vertices), 3), dtype=np.float32)
    processed_contributions = 0
    zero_style_equivalence_max_abs = None
    image_window = int(metadata["image_window_size_px"])
    inference_started = time.perf_counter()
    for window_index, window in enumerate(windows, start=1):
        vertex_ids, weights = window_vertex_weights(
            vertices, spatial_index, window
        )
        if vertex_ids.size:
            tile = image.crop(
                (
                    window.image_column,
                    window.image_row,
                    window.image_column + image_window,
                    window.image_row + image_window,
                )
            )
            sat_input = transform(tile).unsqueeze(0).to(device)
            with torch.inference_mode():
                planes = model.from_sat_to_triplane(sat_input)
            for start in range(0, vertex_ids.size, args.color_batch_size):
                stop = min(start + args.color_batch_size, vertex_ids.size)
                batch_ids = vertex_ids[start:stop]
                batch_weights = weights[start:stop]
                coordinates = model_query_coordinates(
                    vertices[batch_ids],
                    window,
                    density_xy,
                    density_height,
                    pad,
                    mesh_resolution,
                )
                if zero_style_equivalence_max_abs is None:
                    check_coordinates = coordinates[: min(32, len(coordinates))]
                    explicit_rgb = query_rgb(
                        model,
                        planes,
                        check_coordinates,
                        zero_style,
                        device,
                    )
                    check_tensor = (
                        torch.from_numpy(check_coordinates)
                        .to(device)
                        .unsqueeze(0)
                    )
                    with torch.inference_mode():
                        implicit_rgb = model.density_reg(
                            check_tensor,
                            planes,
                            sample_color=True,
                            w_sky=None,
                        ).squeeze(0).float().cpu().numpy()
                    zero_style_equivalence_max_abs = float(
                        np.max(np.abs(explicit_rgb - implicit_rgb))
                    )
                    if zero_style_equivalence_max_abs > 1e-6:
                        raise RuntimeError(
                            "Explicit zero style differs from w_sky=None by "
                            f"{zero_style_equivalence_max_abs}"
                        )
                rgb = query_rgb(
                    model, planes, coordinates, zero_style, device
                )
                rgb_sum[batch_ids] += rgb * batch_weights[:, None]
            processed_contributions += int(vertex_ids.size)
            del planes, sat_input

        peak_gib = (
            torch.cuda.max_memory_allocated(device) / 2**30
            if device.type == "cuda"
            else 0.0
        )
        elapsed = time.perf_counter() - inference_started
        rate = window_index / elapsed if elapsed > 0 else 0.0
        remaining = (len(windows) - window_index) / rate if rate > 0 else 0.0
        print(
            f"colour window {window_index}/{len(windows)}; "
            f"vertices {vertex_ids.size}; peak {peak_gib:.2f} GiB; "
            f"ETA {remaining:.1f}s",
            flush=True,
        )

    if processed_contributions != total_contributions:
        raise RuntimeError(
            "Colour pass contribution count differs from preflight: "
            f"{processed_contributions} != {total_contributions}"
        )
    color_query_elapsed = time.perf_counter() - inference_started
    rgb_float = rgb_sum / weight_sum[:, None]
    if not np.all(np.isfinite(rgb_float)):
        raise RuntimeError("Blended RGB contains non-finite values")
    below_zero = int(np.count_nonzero(rgb_float < 0.0))
    above_one = int(np.count_nonzero(rgb_float > 1.0))
    rgb_clipped = np.clip(rgb_float, 0.0, 1.0)
    rgb_u8 = (rgb_clipped * 255.0).astype(np.uint8)
    export_coloured_mesh(source_mesh, rgb_u8, output_path)
    verification = verify_coloured_mesh(source_mesh, rgb_u8, output_path)
    source_mesh_sha256 = sha256_file(mesh_path)
    output_mesh_sha256 = sha256_file(output_path)
    elapsed_total = time.perf_counter() - started
    color_metadata = {
        "source_run_metadata": str(metadata_path),
        "source_mesh": str(mesh_path),
        "source_mesh_sha256": source_mesh_sha256,
        "prepared_image": str(prepared_image_path),
        "output_mesh": str(output_path),
        "output_mesh_sha256": output_mesh_sha256,
        "model_path": args.model_path,
        "device": str(device),
        "vertex_count": int(len(vertices)),
        "face_count": int(len(source_mesh.faces)),
        "window_count": int(len(windows)),
        "network_input_size_px": network_input_size,
        "mesh_resolution": mesh_resolution,
        "model_crop_pad_voxels": pad,
        "color_batch_size": args.color_batch_size,
        "spatial_bin_size_density_voxels": args.spatial_bin_size,
        "total_positive_contributions": total_contributions,
        "contributors_per_vertex_min": int(contributor_count.min()),
        "contributors_per_vertex_max": int(contributor_count.max()),
        "contributors_per_vertex_mean": float(contributor_count.mean()),
        "weight_sum_min": float(weight_sum.min()),
        "weight_sum_max": float(weight_sum.max()),
        "weight_sum_mean": float(weight_sum.mean()),
        "zero_weight_vertices": int(np.count_nonzero(weight_sum <= 0.0)),
        "color_weight_semantics": preflight["color_weight_semantics"],
        "color_query_halo_policy": preflight["color_query_halo_policy"],
        "color_style_semantics": (
            "one_explicit_zero_style_vector_equivalent_to_w_sky_none"
        ),
        "zero_style_equivalence_max_abs": zero_style_equivalence_max_abs,
        "rgb_float_min": [float(value) for value in rgb_float.min(axis=0)],
        "rgb_float_max": [float(value) for value in rgb_float.max(axis=0)],
        "rgb_float_mean": [float(value) for value in rgb_float.mean(axis=0)],
        "rgb_values_below_zero_before_clip": below_zero,
        "rgb_values_above_one_before_clip": above_one,
        "rgb_uint8_min": [int(value) for value in rgb_u8.min(axis=0)],
        "rgb_uint8_max": [int(value) for value in rgb_u8.max(axis=0)],
        "rgb_uint8_mean": [float(value) for value in rgb_u8.mean(axis=0)],
        "fully_black_vertex_count": int(
            np.count_nonzero(np.all(rgb_u8 == 0, axis=1))
        ),
        "elapsed_seconds": elapsed_total,
        "color_query_elapsed_seconds": color_query_elapsed,
        "peak_cuda_allocated_gib": (
            torch.cuda.max_memory_allocated(device) / 2**30
            if device.type == "cuda"
            else 0.0
        ),
        "geometry_preserved": True,
        "verification": verification,
    }
    color_metadata_path.write_text(
        json.dumps(color_metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(color_metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
