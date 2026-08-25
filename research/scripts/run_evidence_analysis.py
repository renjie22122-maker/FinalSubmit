"""Reproduce the quantitative figures and small regression experiments.

The script is deliberately read-only with respect to the audited repositories.
It writes only beneath this thesis workspace.  Run it in the ``sat3dgen``
Conda environment, whose existing installation supplies NumPy, Matplotlib,
SciPy and pyproj.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.patches import Rectangle
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
from pyproj import Transformer


STAGES = [
    ("Raw inputs", 3_640_214, 7_281_608),
    ("Cropped", 2_828_613, 5_615_039),
    ("Bottom removed", 2_252_373, 4_474_289),
    ("Stitched", 1_915_455, 4_097_374),
]


def save_figure(fig: plt.Figure, base: Path) -> None:
    base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(base.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(base.with_suffix(".png"), dpi=240, bbox_inches="tight")
    plt.close(fig)


def parse_tile_coordinates(mesh_dir: Path) -> list[tuple[float, float, Path]]:
    pattern = re.compile(r"sat_(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)\.obj$")
    coordinates = []
    for path in sorted(mesh_dir.rglob("*.obj")):
        match = pattern.fullmatch(path.name)
        if match:
            coordinates.append((float(match.group(1)), float(match.group(2)), path))
    if len(coordinates) != 15:
        raise RuntimeError(f"Expected 15 active OBJ inputs, found {len(coordinates)}")
    return coordinates


def plot_tile_clusters(mesh_dir: Path, figure_dir: Path) -> dict[str, object]:
    coordinates = parse_tile_coordinates(mesh_dir)
    mean_lat = float(np.mean([item[0] for item in coordinates]))
    mean_lon = float(np.mean([item[1] for item in coordinates]))
    metres_lat = 111_320.0
    metres_lon = metres_lat * math.cos(math.radians(mean_lat))

    points = []
    for lat, lon, path in coordinates:
        east = (lon - mean_lon) * metres_lon
        north = (lat - mean_lat) * metres_lat
        group = "east" if lon > -0.13 else "west"
        points.append((east, north, group, path.name, lat, lon))

    east_group = np.array([(x, y) for x, y, g, *_ in points if g == "east"])
    west_group = np.array([(x, y) for x, y, g, *_ in points if g == "west"])
    east_centre = east_group.mean(axis=0)
    west_centre = west_group.mean(axis=0)
    separation = float(np.linalg.norm(east_centre - west_centre))

    fig, ax = plt.subplots(figsize=(7.0, 4.9), constrained_layout=True)
    tile_width = 59.469303
    colours = {"east": "#0072B2", "west": "#D55E00"}
    labels_done: set[str] = set()
    rectangles = []
    rectangle_colours = []
    for east, north, group, *_ in points:
        rectangles.append(
            Rectangle(
                (east - tile_width / 2, north - tile_width / 2),
                tile_width,
                tile_width,
            )
        )
        rectangle_colours.append(colours[group])
        ax.scatter(
            east,
            north,
            s=25,
            color=colours[group],
            label=f"{group.capitalize()} cluster" if group not in labels_done else None,
            zorder=3,
        )
        labels_done.add(group)
    collection = PatchCollection(
        rectangles,
        facecolor=rectangle_colours,
        edgecolor=rectangle_colours,
        alpha=0.13,
        linewidth=0.8,
    )
    ax.add_collection(collection)
    ax.plot(
        [east_centre[0], west_centre[0]],
        [east_centre[1], west_centre[1]],
        color="0.25",
        linestyle="--",
        linewidth=1.1,
    )
    midpoint = (east_centre + west_centre) / 2
    ax.annotate(
        f"cluster-centre separation\n{separation:.1f} m",
        midpoint,
        xytext=(8, 8),
        textcoords="offset points",
        fontsize=9,
    )
    ax.set_xlabel("East offset from all-input mean (m)")
    ax.set_ylabel("North offset from all-input mean (m)")
    ax.set_title("Spatial composition of the 15 meshes selected by the active merge experiment")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, loc="best")
    save_figure(fig, figure_dir / "tile_clusters")
    return {
        "input_count": len(points),
        "east_count": len(east_group),
        "west_count": len(west_group),
        "cluster_centre_separation_m": separation,
        "tile_nominal_width_m": tile_width,
        "coordinates": [
            {"latitude": lat, "longitude": lon, "file": name, "cluster": group}
            for _, _, group, name, lat, lon in points
        ],
    }


def plot_stage_counts(figure_dir: Path) -> dict[str, object]:
    labels = [stage[0] for stage in STAGES]
    vertices = np.array([stage[1] for stage in STAGES]) / 1_000_000
    faces = np.array([stage[2] for stage in STAGES]) / 1_000_000
    x = np.arange(len(labels))
    width = 0.36
    fig, ax = plt.subplots(figsize=(7.2, 4.5), constrained_layout=True)
    vertex_bars = ax.bar(x - width / 2, vertices, width, color="#0072B2", label="Vertices")
    face_bars = ax.bar(x + width / 2, faces, width, color="#E69F00", label="Faces")
    ax.bar_label(vertex_bars, fmt="%.2f", padding=2, fontsize=8)
    ax.bar_label(face_bars, fmt="%.2f", padding=2, fontsize=8)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Count (millions)")
    ax.set_title("Measured geometry retained through the active mesh-processing stages")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, figure_dir / "mesh_stage_counts")
    return {
        "stages": [
            {"stage": label, "vertices": int(vertex), "faces": int(face)}
            for label, vertex, face in STAGES
        ]
    }


def plot_dataset_integrity(figure_dir: Path) -> dict[str, object]:
    fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.8), constrained_layout=True)
    ax = axes[0]
    bars = ax.bar(
        ["Files", "Unique\nhashes"],
        [2333, 1205],
        color=["#0072B2", "#56B4E9"],
        width=0.62,
    )
    ax.bar_label(bars, fmt="%d", padding=2)
    ax.set_ylim(0, 2500)
    ax.set_ylabel("Equirectangular panoramas")
    ax.set_title("Exact-image duplication")
    ax.grid(axis="y", alpha=0.25)

    ax = axes[1]
    inside = 9_332 - 6_987
    bars = ax.bar(
        ["Inside", "Outside"],
        [inside, 6_987],
        color=["#009E73", "#D55E00"],
        width=0.62,
    )
    ax.bar_label(bars, labels=[f"{inside}\n25.13%", "6987\n74.87%"], padding=2)
    ax.set_ylim(0, 7600)
    ax.set_ylabel("Four-candidate label entries")
    ax.set_title("Query position relative to ±320 px tile bounds")
    ax.grid(axis="y", alpha=0.25)
    save_figure(fig, figure_dir / "dataset_integrity")
    return {
        "panorama_files": 2333,
        "unique_panorama_hashes": 1205,
        "candidate_entries": 9332,
        "candidate_inside": inside,
        "candidate_outside": 6987,
        "candidate_outside_percent": 74.87,
    }


def read_vertex_y(path: Path) -> np.ndarray:
    values: list[float] = []
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line in handle:
            if line.startswith("v "):
                fields = line.split()
                values.append(float(fields[2]))
    return np.asarray(values, dtype=np.float64)


def plot_dsm_displacement(before_path: Path, after_path: Path, figure_dir: Path) -> dict[str, object]:
    before = read_vertex_y(before_path)
    after = read_vertex_y(after_path)
    if before.shape != after.shape:
        raise RuntimeError(f"DSM scene vertex counts differ: {before.shape} vs {after.shape}")
    delta = after - before
    fig, axes = plt.subplots(1, 2, figsize=(8.4, 3.8), constrained_layout=True)
    axes[0].hist(before, bins=70, alpha=0.75, color="#0072B2", label="Before DSM")
    axes[0].hist(after, bins=70, alpha=0.62, color="#E69F00", label="After DSM")
    axes[0].set_xlabel("Vertex Y (m)")
    axes[0].set_ylabel("Vertices")
    axes[0].set_title("Absolute vertical-coordinate distributions")
    axes[0].legend(frameon=False)
    axes[0].grid(axis="y", alpha=0.2)
    axes[1].hist(delta, bins=70, color="#CC79A7")
    axes[1].axvline(np.median(delta), color="0.15", linestyle="--", linewidth=1)
    axes[1].set_xlabel("Per-vertex change, ΔY (m)")
    axes[1].set_ylabel("Vertices")
    axes[1].set_title("DSM correction applied to all scene vertices")
    axes[1].grid(axis="y", alpha=0.2)
    save_figure(fig, figure_dir / "dsm_displacement")
    return {
        "vertex_count": int(delta.size),
        "changed_count": int(np.count_nonzero(delta)),
        "positive_count": int(np.count_nonzero(delta > 0)),
        "mean_delta_y_m": float(np.mean(delta)),
        "median_delta_y_m": float(np.median(delta)),
        "min_delta_y_m": float(np.min(delta)),
        "max_delta_y_m": float(np.max(delta)),
        "before_y_min_m": float(np.min(before)),
        "before_y_max_m": float(np.max(before)),
        "after_y_min_m": float(np.min(after)),
        "after_y_max_m": float(np.max(after)),
    }


def read_obj(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vertices: list[list[float]] = []
    colours: list[list[float]] = []
    faces: list[list[int]] = []
    with path.open("r", encoding="utf-8", errors="strict") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.startswith("v "):
                fields = line.split()
                if len(fields) < 4:
                    raise ValueError(f"{path}:{line_number}: malformed vertex")
                vertices.append([float(fields[1]), float(fields[2]), float(fields[3])])
                colours.append(
                    [float(fields[4]), float(fields[5]), float(fields[6])]
                    if len(fields) >= 7
                    else [0.65, 0.68, 0.72]
                )
            elif line.startswith("f "):
                tokens = line.split()[1:]
                if len(tokens) != 3:
                    raise ValueError(f"{path}:{line_number}: expected triangular face")
                face = []
                for token in tokens:
                    index = int(token.split("/", 1)[0])
                    if index <= 0:
                        raise ValueError(f"{path}:{line_number}: non-positive index")
                    face.append(index - 1)
                faces.append(face)
    return (
        np.asarray(vertices, dtype=np.float64),
        np.asarray(faces, dtype=np.int64),
        np.asarray(colours, dtype=np.float64),
    )


def mesh_metrics(path: Path) -> tuple[dict[str, object], tuple[np.ndarray, np.ndarray, np.ndarray]]:
    vertices, faces, colours = read_obj(path)
    invalid_indices = int(np.count_nonzero((faces < 0) | (faces >= len(vertices))))
    finite_vertices = bool(np.isfinite(vertices).all())
    if invalid_indices:
        raise ValueError(f"{path}: {invalid_indices} invalid face indices")
    triangles = vertices[faces]
    double_area = np.linalg.norm(
        np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]),
        axis=1,
    )
    degenerate_faces = int(np.count_nonzero(double_area <= 1e-12))
    canonical_faces = np.sort(faces, axis=1)
    unique_faces = np.unique(canonical_faces, axis=0)
    duplicate_faces = int(len(faces) - len(unique_faces))
    edges = np.vstack((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
    edges = np.sort(edges, axis=1)
    _, edge_counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = int(np.count_nonzero(edge_counts == 1))
    nonmanifold_edges = int(np.count_nonzero(edge_counts > 2))

    parent = np.arange(len(vertices), dtype=np.int64)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    def union(first: int, second: int) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for first, second in edges:
        union(int(first), int(second))
    used = np.unique(faces)
    connected_components = len({find(int(index)) for index in used})
    metrics = {
        "path": str(path),
        "vertices": int(len(vertices)),
        "faces": int(len(faces)),
        "finite_vertices": finite_vertices,
        "invalid_face_indices": invalid_indices,
        "degenerate_faces": degenerate_faces,
        "duplicate_faces_ignoring_winding": duplicate_faces,
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "connected_components": int(connected_components),
        "watertight_by_edge_incidence": boundary_edges == 0 and nonmanifold_edges == 0,
        "bounds_min_xyz": vertices.min(axis=0).tolist(),
        "bounds_max_xyz": vertices.max(axis=0).tolist(),
    }
    return metrics, (vertices, faces, colours)


def render_mesh(ax, mesh: tuple[np.ndarray, np.ndarray, np.ndarray], title: str) -> None:
    vertices, faces, colours = mesh
    triangles = vertices[faces]
    face_colours = np.clip(colours[faces].mean(axis=1), 0, 1)
    collection = Poly3DCollection(
        triangles,
        facecolors=face_colours,
        edgecolors="none",
        linewidths=0,
        alpha=1.0,
    )
    ax.add_collection3d(collection)
    minima = vertices.min(axis=0)
    maxima = vertices.max(axis=0)
    spans = np.maximum(maxima - minima, 1e-6)
    centres = (maxima + minima) / 2
    half = max(spans[0], spans[2], spans[1] * 0.55) / 2
    ax.set_xlim(centres[0] - half, centres[0] + half)
    ax.set_ylim(centres[1] - half, centres[1] + half)
    ax.set_zlim(centres[2] - half, centres[2] + half)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=23, azim=-58)
    ax.set_xlabel("X (m)", labelpad=1)
    ax.set_ylabel("Y (m)", labelpad=1)
    ax.set_zlabel("")
    ax.tick_params(labelsize=7, pad=0)
    ax.set_title(title)


def compare_representative_building(no_dsm: Path, dsm: Path, figure_dir: Path) -> dict[str, object]:
    no_metrics, no_mesh = mesh_metrics(no_dsm)
    dsm_metrics, dsm_mesh = mesh_metrics(dsm)
    if no_mesh[0].shape == dsm_mesh[0].shape:
        delta = dsm_mesh[0][:, 1] - no_mesh[0][:, 1]
        paired = {
            "same_vertex_count": True,
            "mean_delta_y_m": float(np.mean(delta)),
            "min_delta_y_m": float(np.min(delta)),
            "max_delta_y_m": float(np.max(delta)),
        }
    else:
        paired = {"same_vertex_count": False}
    fig = plt.figure(figsize=(8.5, 4.25), constrained_layout=True)
    render_mesh(fig.add_subplot(1, 2, 1, projection="3d"), no_mesh, "Without DSM correction")
    render_mesh(fig.add_subplot(1, 2, 2, projection="3d"), dsm_mesh, "With DSM correction")
    save_figure(fig, figure_dir / "representative_building")
    return {"without_dsm": no_metrics, "with_dsm": dsm_metrics, "paired_vertices": paired}


def coordinate_control() -> dict[str, object]:
    origin_lon, origin_lat = -0.1277, 51.5074
    bbox = (-0.1349, 51.5029, -0.1205, 51.5119)
    points = [
        (origin_lon, origin_lat),
        (bbox[0], bbox[1]),
        (bbox[0], bbox[3]),
        (bbox[2], bbox[1]),
        (bbox[2], bbox[3]),
    ]
    metres_lat = 111_320.0
    metres_lon = metres_lat * math.cos(math.radians(origin_lat))
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
    origin_e, origin_n = transformer.transform(origin_lon, origin_lat)
    records = []
    round_trip_residuals = []
    projected_residuals = []
    for lon, lat in points:
        local_x = (lon - origin_lon) * metres_lon
        local_z = -(lat - origin_lat) * metres_lat
        restored_lon = origin_lon + local_x / metres_lon
        restored_lat = origin_lat - local_z / metres_lat
        round_trip = math.hypot(
            (restored_lon - lon) * metres_lon,
            (restored_lat - lat) * metres_lat,
        )
        easting, northing = transformer.transform(lon, lat)
        bng_x = easting - origin_e
        bng_z = -(northing - origin_n)
        projected_residual = math.hypot(local_x - bng_x, local_z - bng_z)
        round_trip_residuals.append(round_trip)
        projected_residuals.append(projected_residual)
        records.append(
            {
                "longitude": lon,
                "latitude": lat,
                "local_x_m": local_x,
                "local_z_m": local_z,
                "bng_delta_easting_m": bng_x,
                "bng_delta_south_m": bng_z,
                "round_trip_residual_m": round_trip,
                "local_vs_bng_residual_m": projected_residual,
            }
        )
    return {
        "origin_lon": origin_lon,
        "origin_lat": origin_lat,
        "axis_convention": "X east, Y height, Z south",
        "points": records,
        "max_algebraic_round_trip_residual_m": max(round_trip_residuals),
        "max_local_approximation_vs_epsg27700_residual_m": max(projected_residuals),
    }


def synthetic_regressions(sat3dgen_root: Path) -> dict[str, object]:
    sys.path.insert(0, str(sat3dgen_root))
    from mesh_pipeline.mesh_merging import _remove_bottom_faces, stitch_tiles

    colours = np.zeros((6, 3), dtype=np.float64)
    bottom_xyz = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 5.0, 0.0],
            [0.0, 5.0, 1.0],
            [2.0, 5.0, 0.0],
            [3.0, 5.0, 0.0],
            [2.0, 5.0, 1.0],
        ]
    )
    bottom_vertices = np.hstack((bottom_xyz, colours))
    bottom_faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    kept_vertices, kept_faces = _remove_bottom_faces(bottom_vertices, bottom_faces, tol=0.5)

    triangle = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    high_triangle = triangle.copy()
    high_triangle[:, 1] = 10.0
    stitch_vertices = np.hstack((np.vstack((triangle, high_triangle)), colours))
    stitch_faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    stitched_vertices, stitched_faces = stitch_tiles(
        stitch_vertices,
        stitch_faces,
        [(0, 3), (3, 6)],
        stitch_distance=0.01,
    )
    return {
        "bottom_predicate": {
            "input_faces": 2,
            "documented_faces_eligible": 0,
            "output_faces": int(len(kept_faces)),
            "output_vertices": int(len(kept_vertices)),
            "finding": "A triangle with only one bottom-near vertex was removed.",
        },
        "vertical_stitch_threshold": {
            "configured_vertical_separation_m": 10.0,
            "input_vertices": 6,
            "output_vertices": int(len(stitched_vertices)),
            "input_faces": 2,
            "output_faces": int(len(stitched_faces)),
            "finding": "Coincident X/Z vertices ten metres apart in Y were merged.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sat3dgen-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "components" / "sat3dgen",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args()
    sat_root = args.sat3dgen_root.resolve()
    workspace = args.workspace.resolve()
    figure_dir = workspace / "figures" / "generated"
    result_dir = workspace / "research" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)

    final = sat_root / "pipeline_output" / "final_v36"
    report = {
        "tile_clusters": plot_tile_clusters(sat_root / "pipeline_output" / "meshes", figure_dir),
        "mesh_stage_counts": plot_stage_counts(figure_dir),
        "dataset_integrity": plot_dataset_integrity(figure_dir),
        "dsm_displacement": plot_dsm_displacement(
            final / "test_merge_scene.obj",
            final / "test_merge_scene_corrected.obj",
            figure_dir,
        ),
        "representative_building": compare_representative_building(
            final / "buildings_no_dsm" / "building_1140_1141.obj",
            final / "buildings_dsm" / "building_1140_1141.obj",
            figure_dir,
        ),
        "coordinate_control": coordinate_control(),
        "synthetic_regressions": synthetic_regressions(sat_root),
    }
    output = result_dir / "evidence_analysis.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
