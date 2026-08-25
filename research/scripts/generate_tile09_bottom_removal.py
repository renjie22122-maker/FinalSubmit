"""Render the saved tile_09 mesh before and after bottom-plane removal.

The two source OBJ files are treated as read-only evidence.  The script checks
that the later mesh is exactly the retained coordinate/RGB face set of the
earlier mesh under the active 0.5 m minimum-Y predicate, then renders the two
states with one saved underside camera and one common metric bounding frame.

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
RETAINED_RGB = np.asarray([0.48, 0.64, 0.78], dtype=np.float64)
REMOVED_RGB = np.asarray([0.90, 0.43, 0.13], dtype=np.float64)
FRAME_RGB = np.asarray([0.78, 0.78, 0.78], dtype=np.float64)
CAMERA_FRONT = np.asarray([0.62, -0.48, -0.62], dtype=np.float64)
CAMERA_UP = np.asarray([0.0, 1.0, 0.0], dtype=np.float64)
CAMERA_ZOOM = 0.68


@dataclass
class ObjMesh:
    vertices: np.ndarray
    colours: np.ndarray
    faces: np.ndarray


def read_obj(path: Path) -> ObjMesh:
    """Read the project's triangular XYZ+RGB OBJ format into arrays."""
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        header = handle.readline().strip()
        match = HEADER_RE.fullmatch(header)
        if not match:
            raise RuntimeError(f"Unexpected OBJ header in {path}: {header!r}")
        vertex_count, face_count = (int(value) for value in match.groups())
        vertices = np.empty((vertex_count, 3), dtype=np.float64)
        colours = np.empty((vertex_count, 3), dtype=np.float64)
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
    if not np.isfinite(vertices).all() or not np.isfinite(colours).all():
        raise RuntimeError(f"Non-finite vertex data in {path}")
    if faces.size and (faces.min() < 0 or faces.max() >= len(vertices)):
        raise RuntimeError(f"Out-of-range face index in {path}")
    return ObjMesh(vertices=vertices, colours=colours, faces=faces)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sort_triangle_rows(rows: np.ndarray) -> np.ndarray:
    """Canonicalise vertex order within faces and lexicographically sort faces."""
    ordered_vertices = np.sort(rows, axis=1)
    order = np.lexsort(
        (ordered_vertices[:, 2], ordered_vertices[:, 1], ordered_vertices[:, 0])
    )
    return ordered_vertices[order]


def verify_pair(
    before: ObjMesh,
    after: ObjMesh,
    tolerance_m: float,
) -> tuple[np.ndarray, dict[str, object]]:
    """Verify the saved pair and return the faces removed by the active rule."""
    before_records = np.column_stack((before.vertices, before.colours))
    after_records = np.column_stack((after.vertices, after.colours))
    combined_records = np.vstack((before_records, after_records))
    _, canonical_ids = np.unique(combined_records, axis=0, return_inverse=True)
    before_ids = canonical_ids[: len(before_records)]
    after_ids = canonical_ids[len(before_records) :]

    before_unique = np.unique(before_ids)
    after_unique = np.unique(after_ids)
    if not np.all(np.isin(after_unique, before_unique)):
        raise RuntimeError("The post-removal vertex/RGB records are not a subset")

    y_min = float(before.vertices[:, 1].min())
    face_y = before.vertices[before.faces, 1]
    removed_mask = np.min(face_y, axis=1) <= y_min + tolerance_m
    all_vertices_in_band = np.max(face_y, axis=1) <= y_min + tolerance_m
    if not np.all(all_vertices_in_band[removed_mask]):
        raise RuntimeError(
            "This saved pair contains a minimum-Y candidate face extending "
            "outside the tolerance band"
        )
    if int(np.count_nonzero(removed_mask)) != len(before.faces) - len(after.faces):
        raise RuntimeError("Face-count change does not match the active predicate")

    retained_signatures = sort_triangle_rows(before_ids[before.faces[~removed_mask]])
    after_signatures = sort_triangle_rows(after_ids[after.faces])
    if not np.array_equal(retained_signatures, after_signatures):
        raise RuntimeError(
            "The post-removal face geometry/RGB set is not the predicate complement"
        )

    retained_vertex_ids = np.unique(before.faces[~removed_mask])
    removed_vertex_ids = np.unique(before.faces[removed_mask])
    shared_vertex_ids = np.intersect1d(
        retained_vertex_ids,
        removed_vertex_ids,
        assume_unique=True,
    )
    if len(shared_vertex_ids):
        raise RuntimeError("Removed and retained face sets unexpectedly share vertices")
    if len(before.vertices) - len(retained_vertex_ids) != len(before.vertices) - len(after.vertices):
        raise RuntimeError("Orphan-vertex removal does not match the saved vertex count")

    removed_triangles = before.vertices[before.faces[removed_mask]]
    edge_a = removed_triangles[:, 1] - removed_triangles[:, 0]
    edge_b = removed_triangles[:, 2] - removed_triangles[:, 0]
    normals = np.cross(edge_a, edge_b)
    normal_lengths = np.linalg.norm(normals, axis=1)
    nondegenerate = normal_lengths > 1.0e-12
    abs_normal_y = np.abs(normals[nondegenerate, 1] / normal_lengths[nondegenerate])
    face_y_spans = np.ptp(removed_triangles[:, :, 1], axis=1)

    removed_vertices = before.vertices[removed_vertex_ids]
    before_min = before.vertices.min(axis=0)
    before_max = before.vertices.max(axis=0)
    after_min = after.vertices.min(axis=0)
    after_max = after.vertices.max(axis=0)
    removed_min = removed_vertices.min(axis=0)
    removed_max = removed_vertices.max(axis=0)
    before_span = before_max - before_min
    removed_span = removed_max - removed_min

    evidence = {
        "predicate": {
            "active_rule": "minimum face-vertex Y <= tile minimum Y + tolerance",
            "tolerance_m": float(tolerance_m),
            "tile_min_y_m": y_min,
            "threshold_y_m": y_min + float(tolerance_m),
            "all_removed_faces_have_all_three_vertices_in_band": True,
        },
        "pair_checks": {
            "after_vertex_xyz_rgb_records_are_subset": True,
            "after_faces_equal_coordinate_rgb_complement": True,
            "removed_and_retained_faces_share_no_vertex_indices": True,
            "same_x_bounds": bool(np.array_equal(before_min[[0]], after_min[[0]]) and np.array_equal(before_max[[0]], after_max[[0]])),
            "same_z_bounds": bool(np.array_equal(before_min[[2]], after_min[[2]]) and np.array_equal(before_max[[2]], after_max[[2]])),
        },
        "counts": {
            "before_vertices": int(len(before.vertices)),
            "before_faces": int(len(before.faces)),
            "after_vertices": int(len(after.vertices)),
            "after_faces": int(len(after.faces)),
            "removed_orphan_vertices": int(len(before.vertices) - len(after.vertices)),
            "removed_faces": int(np.count_nonzero(removed_mask)),
            "removed_vertex_fraction": float((len(before.vertices) - len(after.vertices)) / len(before.vertices)),
            "removed_face_fraction": float(np.count_nonzero(removed_mask) / len(before.faces)),
        },
        "bounds_xyz_m": {
            "before_min": before_min.tolist(),
            "before_max": before_max.tolist(),
            "after_min": after_min.tolist(),
            "after_max": after_max.tolist(),
            "removed_plane_min": removed_min.tolist(),
            "removed_plane_max": removed_max.tolist(),
        },
        "removed_plane_geometry": {
            "span_xyz_m": removed_span.tolist(),
            "x_coverage_of_tile_span": float(removed_span[0] / before_span[0]),
            "z_coverage_of_tile_span": float(removed_span[2] / before_span[2]),
            "face_y_span_m": {
                "median": float(np.median(face_y_spans)),
                "p95": float(np.percentile(face_y_spans, 95)),
                "max": float(np.max(face_y_spans)),
            },
            "absolute_normal_y": {
                "min": float(np.min(abs_normal_y)),
                "p05": float(np.percentile(abs_normal_y, 5)),
                "median": float(np.median(abs_normal_y)),
            },
        },
    }
    return removed_mask, evidence


def triangle_mesh(
    vertices: np.ndarray,
    faces: np.ndarray,
    colour: np.ndarray,
) -> o3d.geometry.TriangleMesh:
    mesh = o3d.geometry.TriangleMesh(
        vertices=o3d.utility.Vector3dVector(vertices),
        triangles=o3d.utility.Vector3iVector(faces),
    )
    mesh.paint_uniform_color(colour.tolist())
    mesh.compute_vertex_normals()
    return mesh


def metric_frame(bounds_min: np.ndarray, bounds_max: np.ndarray) -> o3d.geometry.LineSet:
    bounds = o3d.geometry.AxisAlignedBoundingBox(bounds_min, bounds_max)
    frame = o3d.geometry.LineSet.create_from_axis_aligned_bounding_box(bounds)
    frame.paint_uniform_color(FRAME_RGB.tolist())
    return frame


def render_state(
    output: Path,
    meshes: list[o3d.geometry.TriangleMesh],
    bounds_min: np.ndarray,
    bounds_max: np.ndarray,
    camera_parameters: o3d.camera.PinholeCameraParameters | None = None,
    width: int = 1600,
    height: int = 1120,
) -> o3d.camera.PinholeCameraParameters:
    """Render one state and return/reuse the exact pinhole camera parameters."""
    visualiser = o3d.visualization.Visualizer()
    if not visualiser.create_window(
        window_name="Tile 09 bottom removal",
        width=width,
        height=height,
        visible=False,
    ):
        raise RuntimeError("Open3D failed to create its hidden rendering window")
    try:
        visualiser.add_geometry(metric_frame(bounds_min, bounds_max), reset_bounding_box=True)
        for mesh in meshes:
            visualiser.add_geometry(mesh, reset_bounding_box=False)
        options = visualiser.get_render_option()
        options.background_color = np.asarray([1.0, 1.0, 1.0])
        options.light_on = True
        options.mesh_color_option = o3d.visualization.MeshColorOption.Color
        options.mesh_shade_option = o3d.visualization.MeshShadeOption.Default
        options.mesh_show_back_face = True
        options.line_width = 1.0

        camera = visualiser.get_view_control()
        if camera_parameters is None:
            camera.set_lookat(((bounds_min + bounds_max) / 2.0).tolist())
            camera.set_front(CAMERA_FRONT.tolist())
            camera.set_up(CAMERA_UP.tolist())
            camera.set_zoom(CAMERA_ZOOM)
            camera_parameters = camera.convert_to_pinhole_camera_parameters()
        else:
            if not camera.convert_from_pinhole_camera_parameters(
                camera_parameters,
                allow_arbitrary=True,
            ):
                raise RuntimeError("Open3D rejected the shared camera parameters")

        visualiser.poll_events()
        visualiser.update_renderer()
        visualiser.capture_screen_image(str(output), do_render=True)
        return camera_parameters
    finally:
        visualiser.destroy_window()


def common_crop(paths: list[Path], padding: int = 16) -> list[Image.Image]:
    images = [Image.open(path).convert("RGB") for path in paths]
    sizes = {image.size for image in images}
    if len(sizes) != 1:
        raise RuntimeError(f"Rendered image sizes differ: {sizes}")
    occupied_union = np.zeros((images[0].height, images[0].width), dtype=bool)
    for image in images:
        occupied_union |= np.any(np.asarray(image) < 248, axis=2)
    rows, columns = np.nonzero(occupied_union)
    if not len(rows):
        raise RuntimeError("Open3D produced blank renders")
    left = max(int(columns.min()) - padding, 0)
    top = max(int(rows.min()) - padding, 0)
    right = min(int(columns.max()) + padding + 1, images[0].width)
    bottom = min(int(rows.max()) + padding + 1, images[0].height)
    return [image.crop((left, top, right, bottom)) for image in images]


def compose_figure(
    before_png: Path,
    after_png: Path,
    output_base: Path,
    horizontal_span: tuple[float, float],
) -> None:
    images = common_crop([before_png, after_png])
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 4.05))
    titles = [
        "(a) Before bottom removal",
        "(b) After bottom removal",
    ]
    subtitles = [
        "Removed lower sheet highlighted in orange",
        "Retained geometry in the identical frame",
    ]
    for axis, image, title, subtitle in zip(axes, images, titles, subtitles):
        axis.imshow(image)
        axis.set_title(title, fontsize=12.5, pad=5)
        axis.text(
            0.5,
            -0.015,
            subtitle,
            transform=axis.transAxes,
            ha="center",
            va="top",
            fontsize=9.4,
            color="#303030",
        )
        axis.set_axis_off()

    fig.text(
        0.5,
        0.055,
        (
            "Shared underside camera and metric frame; "
            f"X/Z extent {horizontal_span[0]:.1f} x {horizontal_span[1]:.1f} m; "
            "stored vertex RGB omitted"
        ),
        ha="center",
        va="center",
        fontsize=9.2,
        color="#303030",
    )
    fig.subplots_adjust(left=0.01, right=0.99, top=0.92, bottom=0.14, wspace=0.025)
    output_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_base.with_suffix(".pdf"), bbox_inches="tight", dpi=240)
    fig.savefig(output_base.with_suffix(".png"), bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--before",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "external/data/meshes/final_v36/debug_per_tile_crop/tile_09.obj",
    )
    parser.add_argument(
        "--after",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "external/data/meshes/final_v36/debug_per_tile_no_bottom/tile_09.obj",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--tolerance-m", type=float, default=0.5)
    args = parser.parse_args()

    before_path = args.before.resolve()
    after_path = args.after.resolve()
    workspace = args.workspace.resolve()
    output_base = workspace / "figures" / "generated" / "tile09_bottom_removal"
    result_path = workspace / "research" / "results" / "tile09_bottom_removal.json"
    temporary_dir = workspace / "tmp" / "tile09_bottom_removal_render"
    temporary_dir.mkdir(parents=True, exist_ok=True)

    before = read_obj(before_path)
    after = read_obj(after_path)
    removed_mask, evidence = verify_pair(before, after, args.tolerance_m)

    union_min = np.minimum(before.vertices.min(axis=0), after.vertices.min(axis=0))
    union_max = np.maximum(before.vertices.max(axis=0), after.vertices.max(axis=0))
    before_png = temporary_dir / "before.png"
    after_png = temporary_dir / "after.png"

    retained_before = triangle_mesh(
        before.vertices,
        before.faces[~removed_mask],
        RETAINED_RGB,
    )
    removed_plane = triangle_mesh(
        before.vertices,
        before.faces[removed_mask],
        REMOVED_RGB,
    )
    retained_after = triangle_mesh(after.vertices, after.faces, RETAINED_RGB)
    camera_parameters = render_state(
        before_png,
        [retained_before, removed_plane],
        union_min,
        union_max,
    )
    render_state(
        after_png,
        [retained_after],
        union_min,
        union_max,
        camera_parameters=camera_parameters,
    )

    union_span = union_max - union_min
    compose_figure(
        before_png,
        after_png,
        output_base,
        (float(union_span[0]), float(union_span[2])),
    )

    report = {
        "source": {
            "before": str(before_path),
            "after": str(after_path),
            "before_sha256": sha256(before_path),
            "after_sha256": sha256(after_path),
            "stage_before": "cropped, placed and semantically pre-aligned tile; before bottom removal and stitching",
            "stage_after": "same tile after bottom removal; before stitching",
        },
        **evidence,
        "rendering": {
            "full_before_faces_rendered": int(len(before.faces)),
            "full_after_faces_rendered": int(len(after.faces)),
            "same_camera_parameters": True,
            "common_union_bounds_xyz_m": [union_min.tolist(), union_max.tolist()],
            "camera_front": CAMERA_FRONT.tolist(),
            "camera_up": CAMERA_UP.tolist(),
            "camera_zoom": CAMERA_ZOOM,
            "view": "underside oblique",
            "display_axes": "X east, Y height, Z south",
            "stored_vertex_rgb_displayed": False,
            "retained_uniform_rgb": RETAINED_RGB.tolist(),
            "removed_plane_uniform_rgb": REMOVED_RGB.tolist(),
            "pdf": str(output_base.with_suffix(".pdf")),
            "png": str(output_base.with_suffix(".png")),
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output_base.with_suffix(".pdf"))
    print(output_base.with_suffix(".png"))
    print(result_path)


if __name__ == "__main__":
    main()
