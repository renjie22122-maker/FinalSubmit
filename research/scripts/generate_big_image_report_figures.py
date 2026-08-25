"""Generate thesis figures for the large-image Sat3DGen experiments.

The script uses only derived geometry and diagnostic renderings.  It does not
embed or redistribute the source satellite mosaic.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection
from PIL import Image


def read_coloured_obj(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vertices: list[list[float]] = []
    colours: list[list[float]] = []
    faces: list[list[int]] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("v "):
                fields = line.split()
                if len(fields) < 7:
                    raise ValueError(f"Vertex without RGB in {path}: {line.rstrip()}")
                vertices.append([float(fields[1]), float(fields[2]), float(fields[3])])
                colours.append([float(fields[4]), float(fields[5]), float(fields[6])])
            elif line.startswith("f "):
                indices = [int(token.split("/", 1)[0]) - 1 for token in line.split()[1:]]
                if len(indices) == 3:
                    faces.append(indices)
                elif len(indices) > 3:
                    faces.extend([[indices[0], indices[i], indices[i + 1]]
                                  for i in range(1, len(indices) - 1)])
    vertex_array = np.asarray(vertices, dtype=np.float64)
    colour_array = np.asarray(colours, dtype=np.float64)
    face_array = np.asarray(faces, dtype=np.int64)
    if vertex_array.size == 0 or face_array.size == 0:
        raise ValueError(f"No triangular geometry found in {path}")
    if not np.isfinite(vertex_array).all() or not np.isfinite(colour_array).all():
        raise ValueError("OBJ contains non-finite coordinates or colours")
    if colour_array.min() < 0.0 or colour_array.max() > 1.0:
        raise ValueError("Expected normalised OBJ colours in [0, 1]")
    return vertex_array, face_array, colour_array


def add_mesh_panel(ax, vertices, faces, face_colours, view: str) -> None:
    centred = vertices - vertices.mean(axis=0, keepdims=True)
    if view == "plan":
        projected = np.column_stack((centred[:, 0], -centred[:, 2]))
        depth = centred[:, 1]
        xlabel, ylabel = "East--west extent (m)", "North--south extent (m)"
    else:
        azimuth = np.deg2rad(-50.0)
        elevation = np.deg2rad(25.0)
        east = centred[:, 0]
        up = centred[:, 1]
        south = centred[:, 2]
        horizontal = np.cos(azimuth) * east - np.sin(azimuth) * south
        horizontal_depth = np.sin(azimuth) * east + np.cos(azimuth) * south
        projected_vertical = np.cos(elevation) * up - np.sin(elevation) * horizontal_depth
        projected = np.column_stack((horizontal, projected_vertical))
        depth = np.cos(elevation) * horizontal_depth + np.sin(elevation) * up
        xlabel = ylabel = ""

    order = np.argsort(depth[faces].mean(axis=1))
    polygons = projected[faces[order]]
    collection = PolyCollection(
        polygons,
        facecolors=face_colours[order],
        edgecolors=(0.08, 0.08, 0.08, 0.08),
        linewidths=0.08,
        rasterized=True,
    )
    ax.add_collection(collection)
    ax.autoscale_view()
    ax.set_aspect("equal", adjustable="box")
    ax.set_facecolor((0.95, 0.95, 0.95))
    if view == "plan":
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(color="white", linewidth=0.6, alpha=0.7)
    else:
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)


def generate_coloured_mesh_figure(obj_path: Path, output_dir: Path) -> None:
    vertices, faces, colours = read_coloured_obj(obj_path)
    face_colours = colours[faces].mean(axis=1)
    fig, axes = plt.subplots(1, 2, figsize=(10.2, 4.5), constrained_layout=True)
    add_mesh_panel(axes[0], vertices, faces, face_colours, "oblique")
    axes[0].set_title("(a) Oblique view")
    add_mesh_panel(axes[1], vertices, faces, face_colours, "plan")
    axes[1].set_title("(b) Plan view")
    for suffix in ("pdf", "png"):
        fig.savefig(
            output_dir / f"big_image_coloured_publication.{suffix}",
            dpi=300,
            bbox_inches="tight",
            facecolor="white",
        )
    plt.close(fig)


def generate_feather_comparison(source_path: Path, output_dir: Path) -> None:
    image = Image.open(source_path).convert("RGB")
    width, height = image.size
    boxes = [
        (0.076, 0.017, 0.422, 0.485),
        (0.578, 0.017, 0.924, 0.485),
        (0.085, 0.710, 0.412, 0.998),
        (0.588, 0.710, 0.915, 0.998),
    ]
    crops = [
        image.crop((int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)))
        for x0, y0, x1, y1 in boxes
    ]
    titles = [
        "(a) Hard-box fusion: full region",
        "(b) Fractional feathering: full region",
        "(c) Hard-box fusion: roof detail",
        "(d) Fractional feathering: roof detail",
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10.2, 8.4), constrained_layout=True)
    for ax, crop, title in zip(axes.ravel(), crops, titles):
        ax.imshow(crop)
        ax.set_title(title, fontsize=10)
        ax.axis("off")
    for suffix in ("pdf", "png"):
        fig.savefig(
            output_dir / f"big_image_feather_comparison.{suffix}",
            dpi=300,
            bbox_inches="tight",
            facecolor="white",
        )
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coloured-obj", required=True, type=Path)
    parser.add_argument("--comparison-image", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    generate_coloured_mesh_figure(args.coloured_obj, args.output_dir)
    generate_feather_comparison(args.comparison_image, args.output_dir)


if __name__ == "__main__":
    main()
