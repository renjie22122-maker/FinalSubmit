"""Render the matched eastern 3x3 scene before and after DSM correction.

The source OBJ files are treated as read-only evidence.  The script verifies
that vertex order, X/Z coordinates, RGB attributes and triangle indices are
identical, isolates the larger spatial cluster, renders both states with one
camera/coordinate frame, and writes only inside the thesis workspace.

Run in the existing ``sat3dgen`` Conda environment on Windows.  Open3D's
hidden legacy visualiser is used because its off-screen EGL renderer is not
available on this platform.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import open3d as o3d
from PIL import Image


HEADER_RE = re.compile(r"#\s*(\d+)\s+vertices,\s*(\d+)\s+faces")


@dataclass
class ObjMesh:
    vertices: np.ndarray
    colours: np.ndarray
    faces: np.ndarray


def read_obj(path: Path) -> ObjMesh:
    """Read the repository's triangular XYZ+RGB OBJ format into arrays."""
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        header = handle.readline().strip()
        match = HEADER_RE.fullmatch(header)
        if not match:
            raise RuntimeError(f"Unexpected OBJ header in {path}: {header!r}")
        vertex_count, face_count = (int(value) for value in match.groups())
        vertices = np.empty((vertex_count, 3), dtype=np.float64)
        colours = np.empty((vertex_count, 3), dtype=np.float32)
        faces = np.empty((face_count, 3), dtype=np.int32)
        vertex_index = 0
        face_index = 0
        for line_number, line in enumerate(handle, 2):
            if line.startswith("v "):
                fields = line.split()
                if len(fields) < 7:
                    raise RuntimeError(f"Missing XYZ/RGB at {path}:{line_number}")
                vertices[vertex_index] = [float(value) for value in fields[1:4]]
                colours[vertex_index] = [float(value) for value in fields[4:7]]
                vertex_index += 1
            elif line.startswith("f "):
                fields = line.split()
                if len(fields) != 4:
                    raise RuntimeError(f"Non-triangular face at {path}:{line_number}")
                faces[face_index] = [
                    int(token.split("/", 1)[0]) - 1 for token in fields[1:4]
                ]
                face_index += 1
        if vertex_index != vertex_count or face_index != face_count:
            raise RuntimeError(
                f"OBJ count mismatch for {path}: "
                f"{vertex_index}/{vertex_count} vertices and "
                f"{face_index}/{face_count} faces"
            )
    return ObjMesh(vertices=vertices, colours=colours, faces=faces)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def largest_x_gap(values: np.ndarray) -> tuple[float, float, float]:
    ordered = np.sort(values)
    gaps = np.diff(ordered)
    gap_index = int(np.argmax(gaps))
    lower = float(ordered[gap_index])
    upper = float(ordered[gap_index + 1])
    return lower, upper, (lower + upper) / 2.0


def isolate_larger_cluster(mesh: ObjMesh) -> tuple[np.ndarray, np.ndarray, dict[str, object]]:
    gap_lower, gap_upper, threshold = largest_x_gap(mesh.vertices[:, 0])
    right_mask = mesh.vertices[:, 0] > threshold
    left_mask = ~right_mask
    cluster_mask = right_mask if np.count_nonzero(right_mask) > np.count_nonzero(left_mask) else left_mask

    face_membership = cluster_mask[mesh.faces]
    mixed_faces = np.any(face_membership, axis=1) & ~np.all(face_membership, axis=1)
    if np.any(mixed_faces):
        raise RuntimeError(f"Found {np.count_nonzero(mixed_faces)} faces spanning the cluster gap")
    cluster_face_mask = np.all(face_membership, axis=1)
    vertex_ids = np.flatnonzero(cluster_mask)
    face_ids = np.flatnonzero(cluster_face_mask)

    remap = np.full(len(cluster_mask), -1, dtype=np.int32)
    remap[vertex_ids] = np.arange(len(vertex_ids), dtype=np.int32)
    cluster_faces = remap[mesh.faces[face_ids]]
    cluster_vertices = mesh.vertices[vertex_ids]
    if np.any(cluster_faces < 0):
        raise RuntimeError("Cluster remapping produced an invalid vertex index")

    bounds_min = cluster_vertices.min(axis=0)
    bounds_max = cluster_vertices.max(axis=0)
    details = {
        "x_gap_lower_m": gap_lower,
        "x_gap_upper_m": gap_upper,
        "x_split_threshold_m": threshold,
        "vertices": int(len(vertex_ids)),
        "faces": int(len(face_ids)),
        "bounds_min_xyz_m": bounds_min.tolist(),
        "bounds_max_xyz_m": bounds_max.tolist(),
        "horizontal_span_x_m": float(bounds_max[0] - bounds_min[0]),
        "horizontal_span_z_m": float(bounds_max[2] - bounds_min[2]),
    }
    return vertex_ids, cluster_faces, details


def make_reference_outline(bounds_min: np.ndarray, bounds_max: np.ndarray) -> o3d.geometry.LineSet:
    """Create a light rectangular Y=0 reference shared by both renderings."""
    points = np.asarray(
        [
            [bounds_min[0], 0.0, bounds_min[2]],
            [bounds_max[0], 0.0, bounds_min[2]],
            [bounds_max[0], 0.0, bounds_max[2]],
            [bounds_min[0], 0.0, bounds_max[2]],
        ],
        dtype=np.float64,
    )
    lines = np.asarray([[0, 1], [1, 2], [2, 3], [3, 0]], dtype=np.int32)
    outline = o3d.geometry.LineSet(
        points=o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64)),
        lines=o3d.utility.Vector2iVector(np.asarray(lines, dtype=np.int32)),
    )
    outline.colors = o3d.utility.Vector3dVector(
        np.repeat(np.asarray([[0.84, 0.84, 0.84]]), len(lines), axis=0)
    )
    return outline


def render_state(
    output: Path,
    vertices: np.ndarray,
    colours: np.ndarray,
    faces: np.ndarray,
    union_min: np.ndarray,
    union_max: np.ndarray,
    width: int = 1500,
    height: int = 1050,
) -> None:
    mesh = o3d.geometry.TriangleMesh(
        vertices=o3d.utility.Vector3dVector(vertices),
        triangles=o3d.utility.Vector3iVector(faces),
    )
    mesh.vertex_colors = o3d.utility.Vector3dVector(np.clip(colours, 0.0, 1.0))
    mesh.compute_vertex_normals()
    outline = make_reference_outline(union_min, union_max)

    visualiser = o3d.visualization.Visualizer()
    if not visualiser.create_window(
        window_name="DSM scene comparison",
        width=width,
        height=height,
        visible=False,
    ):
        raise RuntimeError("Open3D failed to create its hidden rendering window")
    try:
        visualiser.add_geometry(mesh, reset_bounding_box=True)
        visualiser.add_geometry(outline, reset_bounding_box=False)
        options = visualiser.get_render_option()
        options.background_color = np.asarray([1.0, 1.0, 1.0])
        options.light_on = True
        options.mesh_color_option = o3d.visualization.MeshColorOption.Color
        options.mesh_shade_option = o3d.visualization.MeshShadeOption.Default
        options.mesh_show_back_face = True
        options.line_width = 1.0

        camera = visualiser.get_view_control()
        camera.set_lookat(((union_min + union_max) / 2.0).tolist())
        camera.set_front([0.66, -0.36, -0.66])
        camera.set_up([0.0, 1.0, 0.0])
        camera.set_zoom(0.68)
        visualiser.poll_events()
        visualiser.update_renderer()
        visualiser.capture_screen_image(str(output), do_render=True)
    finally:
        visualiser.destroy_window()


def crop_white_margin(path: Path, padding: int = 14) -> Image.Image:
    image = Image.open(path).convert("RGB")
    array = np.asarray(image)
    occupied = np.any(array < 248, axis=2)
    rows, cols = np.nonzero(occupied)
    if not len(rows):
        return image
    left = max(int(cols.min()) - padding, 0)
    top = max(int(rows.min()) - padding, 0)
    right = min(int(cols.max()) + padding + 1, image.width)
    bottom = min(int(rows.max()) + padding + 1, image.height)
    return image.crop((left, top, right, bottom))


def displacement_grid(
    vertices: np.ndarray,
    delta_y: np.ndarray,
    cell_size_m: float = 1.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Select the highest pre-correction vertex in each metric X/Z cell."""
    x = vertices[:, 0]
    z = vertices[:, 2]
    x_edges = np.arange(x.min(), x.max() + cell_size_m, cell_size_m)
    z_edges = np.arange(z.min(), z.max() + cell_size_m, cell_size_m)
    if x_edges[-1] <= x.max():
        x_edges = np.append(x_edges, x_edges[-1] + cell_size_m)
    if z_edges[-1] <= z.max():
        z_edges = np.append(z_edges, z_edges[-1] + cell_size_m)
    x_index = np.clip(np.searchsorted(x_edges, x, side="right") - 1, 0, len(x_edges) - 2)
    z_index = np.clip(np.searchsorted(z_edges, z, side="right") - 1, 0, len(z_edges) - 2)
    z_cells = len(z_edges) - 1
    linear_index = x_index * z_cells + z_index

    # Sort by cell, then by pre-correction Y; the last record per cell is the
    # highest saved vertex.  This avoids weighting vertical sheets by their
    # vertex density and does not use the corrected geometry to select what is
    # displayed.
    order = np.lexsort((vertices[:, 1], linear_index))
    sorted_cells = linear_index[order]
    last_in_cell = np.r_[sorted_cells[1:] != sorted_cells[:-1], True]
    selected = order[last_in_cell]

    x_cells = len(x_edges) - 1
    count = np.bincount(linear_index, minlength=x_cells * z_cells).reshape(x_cells, z_cells)
    top_vertex_delta = np.full(x_cells * z_cells, np.nan, dtype=np.float64)
    top_vertex_delta[linear_index[selected]] = delta_y[selected]
    return top_vertex_delta.reshape(x_cells, z_cells), count.astype(np.int32), x_edges, z_edges


def compose_figure(
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
    images = [crop_white_margin(before_png), crop_white_margin(after_png)]
    fig = plt.figure(figsize=(9.4, 3.4), constrained_layout=True)
    grid = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 0.92])
    axes = [fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1])]
    titles = ["(a) Before DSM stage", "(b) After DSM stage"]
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
            bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "alpha": 0.88, "edgecolor": "0.7"},
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
    colourbar = fig.colorbar(colour_mesh, ax=ax_map, orientation="horizontal", fraction=0.075, pad=0.12)
    colourbar.set_label("Applied vertical displacement $\\Delta Y$ (m)", fontsize=9.5)
    colourbar.ax.tick_params(labelsize=9.0)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_base.with_suffix(".pdf"), bbox_inches="tight", dpi=240)
    fig.savefig(output_base.with_suffix(".png"), bbox_inches="tight", dpi=300)
    plt.close(fig)


def stats(values: np.ndarray) -> dict[str, float]:
    return {
        "min": float(np.min(values)),
        "p05": float(np.percentile(values, 5)),
        "median": float(np.median(values)),
        "mean": float(np.mean(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(np.max(values)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--before",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "external/data/meshes/test_merge_scene.obj",
    )
    parser.add_argument(
        "--after",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "external/data/meshes/test_merge_scene_corrected.obj",
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
    figure_base = workspace / "figures" / "generated" / "dsm_scene_3x3_comparison"
    result_path = workspace / "research" / "results" / "dsm_scene_3x3_comparison.json"
    temporary_dir = workspace / "tmp" / "dsm_scene_3x3_render"
    temporary_dir.mkdir(parents=True, exist_ok=True)

    before = read_obj(before_path)
    after = read_obj(after_path)
    if not np.array_equal(before.faces, after.faces):
        raise RuntimeError("Before/after face arrays differ")
    if not np.array_equal(before.vertices[:, [0, 2]], after.vertices[:, [0, 2]]):
        raise RuntimeError("Before/after X/Z coordinates differ")
    if not np.array_equal(before.colours, after.colours):
        raise RuntimeError("Before/after RGB attributes differ")

    vertex_ids, cluster_faces, cluster = isolate_larger_cluster(before)
    before_vertices = before.vertices[vertex_ids]
    after_vertices = after.vertices[vertex_ids]
    delta_y = after_vertices[:, 1] - before_vertices[:, 1]
    if not np.all(delta_y > 0):
        raise RuntimeError("Expected every saved 3x3-cluster displacement to be positive")

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
    delta_summary = stats(delta_y)
    compose_figure(
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
        "larger_eastern_3x3_cluster": cluster,
        "before_y_m": stats(before_vertices[:, 1]),
        "after_y_m": stats(after_vertices[:, 1]),
        "delta_y_m": delta_summary,
        "positive_displacement_vertices": int(np.count_nonzero(delta_y > 0)),
        "spatial_displacement_map": {
            "aggregation": "paired delta_y at the highest pre-correction vertex in each occupied cell",
            "cell_size_m": 1.0,
            "grid_shape_xz": [int(top_vertex_delta.shape[0]), int(top_vertex_delta.shape[1])],
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
