"""Render the north-western 2-by-3 scene before and after DSM correction.

The source OBJ files are read-only.  The selected component is the six-tile
cluster west and north of the disconnected eastern 3-by-3 component.  The
script verifies the paired geometry and writes the figure and its numerical
summary only inside the thesis workspace.

Run in the existing ``sat3dgen`` Conda environment on Windows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from generate_dsm_scene_3x3_comparison import (
    ObjMesh,
    displacement_grid,
    largest_x_gap,
    read_obj,
    render_state,
    sha256,
    stats,
)


def common_crop(
    paths: list[Path],
    padding: int = 14,
) -> list[Image.Image]:
    """Apply one union foreground rectangle to every same-camera render."""
    images = [Image.open(path).convert("RGB") for path in paths]
    if len({image.size for image in images}) != 1:
        raise RuntimeError("Same-camera renders do not have identical raster dimensions")
    occupied = np.zeros((images[0].height, images[0].width), dtype=bool)
    for image in images:
        occupied |= np.any(np.asarray(image) < 248, axis=2)
    rows, cols = np.nonzero(occupied)
    if not len(rows):
        return images
    left = max(int(cols.min()) - padding, 0)
    top = max(int(rows.min()) - padding, 0)
    right = min(int(cols.max()) + padding + 1, images[0].width)
    bottom = min(int(rows.max()) + padding + 1, images[0].height)
    rectangle = (left, top, right, bottom)
    return [image.crop(rectangle) for image in images]


def compose_joint_crop_figure(
    before_png: Path,
    after_png: Path,
    output_base: Path,
    before_range: tuple[float, float],
    after_range: tuple[float, float],
    top_vertex_delta: np.ndarray,
    delta_count: np.ndarray,
    x_edges: np.ndarray,
    z_edges: np.ndarray,
) -> None:
    """Compose the established three panels without changing apparent scale."""
    images = common_crop([before_png, after_png])
    fig = plt.figure(figsize=(9.4, 3.4), constrained_layout=True)
    grid = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 0.92])
    axes = [fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])]
    titles = ["(a) Post-stitch, before DSM", "(b) After DSM correction"]
    ranges = [before_range, after_range]
    for ax, image, title, value_range in zip(axes, images, titles, ranges):
        ax.imshow(image)
        ax.set_title(title, fontsize=12.5, pad=5)
        ax.text(
            0.02,
            0.97,
            f"Y range: {value_range[0]:.1f} to {value_range[1]:.1f} m",
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=10.0,
            bbox={
                "boxstyle": "round,pad=0.25",
                "facecolor": "white",
                "alpha": 0.88,
                "edgecolor": "0.7",
            },
        )
        ax.set_axis_off()

    ax_map = fig.add_subplot(grid[0, 2])
    cmap = matplotlib.colormaps["cividis"].copy()
    cmap.set_bad("white")
    x_relative = x_edges - x_edges[0]
    z_relative = z_edges - z_edges[0]
    displacement = np.ma.masked_where(delta_count.T == 0, top_vertex_delta.T)
    colour_mesh = ax_map.pcolormesh(
        x_relative,
        z_relative,
        displacement,
        cmap=cmap,
        vmin=15.0,
        vmax=60.0,
        shading="flat",
        rasterized=True,
    )
    ax_map.set_aspect("equal")
    ax_map.invert_yaxis()
    ax_map.set_title("(c) Highest-vertex $\\Delta Y$", fontsize=12.5, pad=5)
    ax_map.set_xlabel("X east (m)", fontsize=10.0)
    ax_map.set_ylabel("Z south (m)", fontsize=10.0)
    ax_map.tick_params(labelsize=9.5)
    colourbar = fig.colorbar(
        colour_mesh,
        ax=ax_map,
        orientation="horizontal",
        fraction=0.075,
        pad=0.12,
    )
    colourbar.set_label("Applied vertical displacement $\\Delta Y$ (m)", fontsize=9.5)
    colourbar.ax.tick_params(labelsize=9.0)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_base.with_suffix(".pdf"), bbox_inches="tight", dpi=240)
    fig.savefig(output_base.with_suffix(".png"), bbox_inches="tight", dpi=300)
    plt.close(fig)


def isolate_northwestern_cluster(
    mesh: ObjMesh,
) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    """Select and remap the low-X, low-Z six-tile component."""
    gap_lower, gap_upper, threshold = largest_x_gap(mesh.vertices[:, 0])
    west_mask = mesh.vertices[:, 0] < threshold
    east_mask = ~west_mask
    if not np.any(west_mask) or not np.any(east_mask):
        raise RuntimeError("Largest X gap did not divide the scene into two components")
    if float(mesh.vertices[west_mask, 0].mean()) >= float(mesh.vertices[east_mask, 0].mean()):
        raise RuntimeError("Selected component is not west of the comparison component")
    # Local Z increases southwards, so the northern component has the lower Z mean.
    if float(mesh.vertices[west_mask, 2].mean()) >= float(mesh.vertices[east_mask, 2].mean()):
        raise RuntimeError("Selected western component is not also the northern component")

    face_membership = west_mask[mesh.faces]
    mixed_faces = np.any(face_membership, axis=1) & ~np.all(face_membership, axis=1)
    if np.any(mixed_faces):
        raise RuntimeError(
            f"Found {np.count_nonzero(mixed_faces)} faces spanning the component gap"
        )
    cluster_face_mask = np.all(face_membership, axis=1)
    vertex_ids = np.flatnonzero(west_mask)
    face_ids = np.flatnonzero(cluster_face_mask)

    remap = np.full(len(west_mask), -1, dtype=np.int32)
    remap[vertex_ids] = np.arange(len(vertex_ids), dtype=np.int32)
    cluster_faces = remap[mesh.faces[face_ids]]
    if np.any(cluster_faces < 0):
        raise RuntimeError("Cluster remapping produced an invalid vertex index")

    cluster_vertices = mesh.vertices[vertex_ids]
    bounds_min = cluster_vertices.min(axis=0)
    bounds_max = cluster_vertices.max(axis=0)
    details = {
        "selection_rule": "X below the midpoint of the largest empty X gap",
        "x_gap_lower_m": gap_lower,
        "x_gap_upper_m": gap_upper,
        "x_split_threshold_m": threshold,
        "local_axis_convention": "X east, Y height, Z south",
        "layout": "two north-south rows by three east-west columns",
        "vertices": int(len(vertex_ids)),
        "faces": int(len(face_ids)),
        "faces_crossing_component_gap": int(np.count_nonzero(mixed_faces)),
        "bounds_min_xyz_m": bounds_min.tolist(),
        "bounds_max_xyz_m": bounds_max.tolist(),
        "horizontal_span_x_m": float(bounds_max[0] - bounds_min[0]),
        "horizontal_span_z_m": float(bounds_max[2] - bounds_min[2]),
    }
    return vertex_ids, cluster_faces, details


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--before",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "external/data/meshes/test_merge_scene.obj",
    )
    parser.add_argument(
        "--after",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "external/data/meshes/test_merge_scene_corrected.obj",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args()

    before_path = args.before.resolve()
    after_path = args.after.resolve()
    workspace = args.workspace.resolve()
    figure_base = workspace / "figures" / "generated" / "dsm_scene_nw_2x3_comparison"
    result_path = workspace / "research" / "results" / "dsm_scene_nw_2x3_comparison.json"
    temporary_dir = workspace / "tmp" / "dsm_scene_nw_2x3_render"
    temporary_dir.mkdir(parents=True, exist_ok=True)

    before = read_obj(before_path)
    after = read_obj(after_path)
    if not np.array_equal(before.faces, after.faces):
        raise RuntimeError("Before/after face arrays differ")
    if not np.array_equal(before.vertices[:, [0, 2]], after.vertices[:, [0, 2]]):
        raise RuntimeError("Before/after X/Z coordinates differ")
    if not np.array_equal(before.colours, after.colours):
        raise RuntimeError("Before/after RGB attributes differ")

    vertex_ids, cluster_faces, cluster = isolate_northwestern_cluster(before)
    before_vertices = before.vertices[vertex_ids]
    after_vertices = after.vertices[vertex_ids]
    delta_y = after_vertices[:, 1] - before_vertices[:, 1]
    if not np.all(delta_y > 0):
        raise RuntimeError("Expected every saved 2-by-3-cluster displacement to be positive")
    if not np.array_equal(before_vertices[:, [0, 2]], after_vertices[:, [0, 2]]):
        raise RuntimeError("Selected before/after X/Z values differ")
    if not np.array_equal(before.colours[vertex_ids], after.colours[vertex_ids]):
        raise RuntimeError("Selected before/after RGB values differ")

    union_min = np.minimum(before_vertices.min(axis=0), after_vertices.min(axis=0))
    union_max = np.maximum(before_vertices.max(axis=0), after_vertices.max(axis=0))
    before_png = temporary_dir / "before.png"
    after_png = temporary_dir / "after.png"
    before_display_colours = np.repeat(
        np.asarray([[0.60, 0.63, 0.67]], dtype=np.float64),
        len(before_vertices),
        axis=0,
    )
    after_display_colours = np.repeat(
        np.asarray([[0.30, 0.47, 0.66]], dtype=np.float64),
        len(after_vertices),
        axis=0,
    )
    render_state(
        before_png,
        before_vertices,
        before_display_colours,
        cluster_faces,
        union_min,
        union_max,
    )
    render_state(
        after_png,
        after_vertices,
        after_display_colours,
        cluster_faces,
        union_min,
        union_max,
    )

    top_vertex_delta, delta_count, x_edges, z_edges = displacement_grid(
        before_vertices,
        delta_y,
        cell_size_m=1.0,
    )
    compose_joint_crop_figure(
        before_png,
        after_png,
        figure_base,
        (float(before_vertices[:, 1].min()), float(before_vertices[:, 1].max())),
        (float(after_vertices[:, 1].min()), float(after_vertices[:, 1].max())),
        top_vertex_delta,
        delta_count,
        x_edges,
        z_edges,
    )

    report = {
        "source": {
            "before": str(before_path),
            "after": str(after_path),
            "before_sha256": sha256(before_path),
            "after_sha256": sha256(after_path),
            "scene_vertices": int(len(before.vertices)),
            "scene_faces": int(len(before.faces)),
        },
        "pair_checks": {
            "same_vertex_order_and_count": True,
            "same_xz_coordinates": True,
            "same_rgb_attributes": True,
            "same_face_indices": True,
            "only_y_changed": True,
        },
        "northwestern_2x3_cluster": cluster,
        "before_y_m": stats(before_vertices[:, 1]),
        "after_y_m": stats(after_vertices[:, 1]),
        "delta_y_m": stats(delta_y),
        "positive_displacement_vertices": int(np.count_nonzero(delta_y > 0)),
        "spatial_displacement_map": {
            "aggregation": (
                "paired delta_y at the highest pre-correction vertex "
                "in each occupied cell"
            ),
            "cell_size_m": 1.0,
            "grid_shape_xz": [
                int(top_vertex_delta.shape[0]),
                int(top_vertex_delta.shape[1]),
            ],
            "occupied_cells": int(np.count_nonzero(delta_count)),
            "empty_cells": int(np.count_nonzero(delta_count == 0)),
            "interpolation": "none",
            "selection_geometry": "pre-correction mesh",
            "semantic_scope": "display proxy, not roof classification",
            "colour_scale_m": [15.0, 60.0],
        },
        "rendering": {
            "full_cluster_vertices_rendered": int(len(before_vertices)),
            "full_cluster_faces_rendered": int(len(cluster_faces)),
            "same_camera_and_union_xyz_bounds": True,
            "identical_union_crop_rectangle": True,
            "reference_outline_y_m": 0.0,
            "display_axes": "X east, Y height, Z south",
            "stored_vertex_rgb_displayed": False,
            "before_uniform_rgb": [0.60, 0.63, 0.67],
            "after_uniform_rgb": [0.30, 0.47, 0.66],
            "pdf": str(figure_base.with_suffix(".pdf")),
            "png": str(figure_base.with_suffix(".png")),
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(result_path)


if __name__ == "__main__":
    main()
