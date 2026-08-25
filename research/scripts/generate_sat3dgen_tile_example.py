"""Render a retained Sat3DGen tile as vertex-coloured and neutral geometry.

The source OBJ is a project inference artefact.  Its coordinates are in the
model's normalised local frame, so the figure deliberately omits metric axes.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import open3d as o3d
from PIL import Image


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_mesh(mesh: o3d.geometry.TriangleMesh, output: Path) -> None:
    mesh.compute_vertex_normals()
    bounds = mesh.get_axis_aligned_bounding_box()
    visualiser = o3d.visualization.Visualizer()
    if not visualiser.create_window(
        window_name="Sat3DGen tile example",
        width=1500,
        height=1050,
        visible=False,
    ):
        raise RuntimeError("Open3D failed to create its hidden rendering window")
    try:
        visualiser.add_geometry(mesh, reset_bounding_box=True)
        options = visualiser.get_render_option()
        options.background_color = np.asarray([1.0, 1.0, 1.0])
        options.light_on = True
        options.mesh_color_option = o3d.visualization.MeshColorOption.Color
        options.mesh_shade_option = o3d.visualization.MeshShadeOption.Default
        options.mesh_show_back_face = True

        camera = visualiser.get_view_control()
        camera.set_lookat(bounds.get_center().tolist())
        # Open3D's front vector points from the camera towards the look-at
        # point.  A positive Y component therefore places this camera above
        # the local ground plane for the Sat3DGen coordinate convention.
        camera.set_front([0.66, 0.46, -0.66])
        camera.set_up([0.0, 1.0, 0.0])
        camera.set_zoom(0.70)
        visualiser.poll_events()
        visualiser.update_renderer()
        visualiser.capture_screen_image(str(output), do_render=True)
    finally:
        visualiser.destroy_window()


def crop_white_margin(path: Path, padding: int = 18) -> Image.Image:
    image = Image.open(path).convert("RGB")
    array = np.asarray(image)
    occupied = np.any(array < 248, axis=2)
    rows, columns = np.nonzero(occupied)
    if not len(rows):
        return image
    left = max(int(columns.min()) - padding, 0)
    top = max(int(rows.min()) - padding, 0)
    right = min(int(columns.max()) + padding + 1, image.width)
    bottom = min(int(rows.max()) + padding + 1, image.height)
    return image.crop((left, top, right, bottom))


def compose_figure(coloured_path: Path, neutral_path: Path, output_stem: Path) -> None:
    images = [crop_white_margin(coloured_path), crop_white_margin(neutral_path)]
    titles = ["(a) Predicted vertex colour", "(b) Uniform material"]
    subtitles = [
        "Stored RGB attributes on the generated surface",
        "Identical vertices, triangles, and camera",
    ]
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.8), constrained_layout=True)
    for axis, image, title, subtitle in zip(axes, images, titles, subtitles):
        axis.imshow(image)
        axis.set_title(title, fontsize=12.0, pad=5)
        axis.text(
            0.5,
            -0.025,
            subtitle,
            transform=axis.transAxes,
            ha="center",
            va="top",
            fontsize=9.2,
            color="#303030",
        )
        axis.set_axis_off()
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_stem.with_suffix(".pdf"), bbox_inches="tight", dpi=240)
    fig.savefig(output_stem.with_suffix(".png"), bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parents[2]
        / "external/data/meshes/sat_51.510303_-0.133978/sat_51.510303_-0.133978.obj",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args()

    source = args.input.resolve()
    workspace = args.workspace.resolve()
    mesh = o3d.io.read_triangle_mesh(str(source), enable_post_processing=False)
    if mesh.is_empty():
        raise ValueError(f"No geometry in {source}")
    if not mesh.has_vertex_colors():
        raise ValueError(f"Expected per-vertex RGB in {source}")

    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.triangles)
    colours = np.asarray(mesh.vertex_colors)
    if len(colours) != len(vertices) or not np.isfinite(colours).all():
        raise ValueError("Vertex-colour records are incomplete or non-finite")

    temporary = workspace / "tmp" / "sat3dgen_tile_example"
    temporary.mkdir(parents=True, exist_ok=True)
    coloured_png = temporary / "coloured.png"
    neutral_png = temporary / "neutral.png"
    render_mesh(mesh, coloured_png)

    neutral_mesh = copy.deepcopy(mesh)
    neutral_mesh.paint_uniform_color([0.48, 0.64, 0.78])
    render_mesh(neutral_mesh, neutral_png)

    output_stem = workspace / "figures" / "generated" / "sat3dgen_tile_example"
    compose_figure(coloured_png, neutral_png, output_stem)

    bounds = np.stack((vertices.min(axis=0), vertices.max(axis=0)))
    evidence = {
        "source_obj": str(source),
        "source_sha256": sha256(source),
        "vertices": int(len(vertices)),
        "triangles": int(len(faces)),
        "vertex_rgb_records": int(len(colours)),
        "rgb_min": colours.min(axis=0).astype(float).tolist(),
        "rgb_max": colours.max(axis=0).astype(float).tolist(),
        "bounds_normalised_xyz": bounds.astype(float).tolist(),
        "rendering": {
            "camera": "same orthographic Open3D view in both panels",
            "coloured_panel": "stored per-vertex RGB",
            "neutral_panel": "uniform display colour; geometry unchanged",
            "metric_axes_shown": False,
            "source_satellite_image_embedded": False,
        },
    }
    results = workspace / "research" / "results" / "sat3dgen_tile_example.json"
    results.parent.mkdir(parents=True, exist_ok=True)
    results.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Wrote {output_stem.with_suffix('.pdf')}")
    print(f"Wrote {output_stem.with_suffix('.png')}")
    print(f"Wrote {results}")


if __name__ == "__main__":
    main()
