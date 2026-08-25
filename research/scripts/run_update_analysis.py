"""Audit post-10-August myProject publications and create thesis figures.

The script is deliberately read-only with respect to the audited project.  It
reads publication manifests and OBJ headers, writes a machine-readable summary
inside the thesis workspace, and produces PDF/PNG figures from those records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection
from mpl_toolkits.mplot3d.art3d import Poly3DCollection


WORKSPACE = Path(__file__).resolve().parents[2]
PUBLICATIONS = (
    WORKSPACE
    / "external"
    / "projects"
    / "data_builder_london_on_demand"
    / "generated_blocks"
)
RESULT_JSON = WORKSPACE / "research" / "results" / "update_project_analysis.json"
FIGURE_DIR = WORKSPACE / "figures" / "generated"


@dataclass(frozen=True)
class Publication:
    selection_id: str
    finished: datetime
    tile_count: int
    building_count: int
    corrected_scene_reused: bool | None
    result_path: Path
    buildings: tuple[dict[str, Any], ...]
    completeness_enabled: bool | None


def save_figure(fig: plt.Figure, stem: str) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURE_DIR / f"{stem}.pdf", bbox_inches="tight")
    fig.savefig(FIGURE_DIR / f"{stem}.png", dpi=240, bbox_inches="tight")
    plt.close(fig)


def parse_time(result: dict[str, Any], path: Path) -> datetime:
    raw = result.get("finished_local_time")
    if isinstance(raw, str):
        try:
            return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def load_publications() -> list[Publication]:
    publications: list[Publication] = []
    for path in sorted(PUBLICATIONS.glob("*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("status") != "READY":
            continue
        buildings = tuple(
            item for item in result.get("buildings", []) if isinstance(item, dict)
        )
        pipeline = result.get("pipeline") if isinstance(result.get("pipeline"), dict) else {}
        model_completeness = (
            result.get("model_completeness")
            if isinstance(result.get("model_completeness"), dict)
            else {}
        )
        publications.append(
            Publication(
                selection_id=str(result.get("selection_id", path.parent.name)),
                finished=parse_time(result, path),
                tile_count=int(result.get("tile_count") or pipeline.get("selected_mesh_count") or 0),
                building_count=len(buildings),
                corrected_scene_reused=(
                    bool(pipeline["corrected_scene_reused"])
                    if "corrected_scene_reused" in pipeline
                    else None
                ),
                result_path=path,
                buildings=buildings,
                completeness_enabled=(
                    bool(model_completeness["enabled"])
                    if "enabled" in model_completeness
                    else None
                ),
            )
        )
    return sorted(publications, key=lambda item: item.finished)


def inspect_vertex_colour(path: Path) -> dict[str, Any]:
    vertex_count = 0
    coloured_count = 0
    minimum = np.array([np.inf, np.inf, np.inf], dtype=float)
    maximum = np.array([-np.inf, -np.inf, -np.inf], dtype=float)
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if not line.startswith("v "):
                continue
            vertex_count += 1
            fields = line.split()
            if len(fields) < 7:
                continue
            try:
                colour = np.asarray([float(fields[4]), float(fields[5]), float(fields[6])])
            except ValueError:
                continue
            if not np.all(np.isfinite(colour)):
                continue
            coloured_count += 1
            minimum = np.minimum(minimum, colour)
            maximum = np.maximum(maximum, colour)
    return {
        "path": str(path),
        "vertex_count": vertex_count,
        "coloured_vertex_count": coloured_count,
        "all_vertices_coloured": vertex_count > 0 and coloured_count == vertex_count,
        "rgb_min": minimum.tolist() if coloured_count else None,
        "rgb_max": maximum.tolist() if coloured_count else None,
    }


def read_coloured_obj(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    vertices: list[list[float]] = []
    colours: list[list[float]] = []
    faces: list[list[int]] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if line.startswith("v "):
                fields = line.split()
                if len(fields) < 7:
                    raise ValueError(f"Vertex without RGB in {path}")
                vertices.append([float(value) for value in fields[1:4]])
                colours.append([float(value) for value in fields[4:7]])
            elif line.startswith("f "):
                raw = [int(token.split("/")[0]) - 1 for token in line.split()[1:]]
                for index in range(1, len(raw) - 1):
                    faces.append([raw[0], raw[index], raw[index + 1]])
    return np.asarray(vertices), np.asarray(faces, dtype=int), np.asarray(colours)


def render_coloured_building(path: Path) -> None:
    vertices, faces, colours = read_coloured_obj(path)
    face_colours = np.clip(colours[faces].mean(axis=1), 0.0, 1.0)
    # OBJ axes are X east, Y height and Z south.  Reorder to X/Z/Y for a
    # conventional oblique plot whose vertical plotting axis is height.
    display_vertices = vertices[:, [0, 2, 1]]
    display_polygons = display_vertices[faces]
    extents = np.ptp(display_vertices, axis=0)
    centre = (display_vertices.min(axis=0) + display_vertices.max(axis=0)) / 2.0
    radius_xy = max(float(extents[:2].max()) / 2.0, 1.0)
    radius_height = max(float(extents[2]) / 2.0, 1.0)

    fig = plt.figure(figsize=(10.2, 4.5))
    oblique = fig.add_subplot(1, 2, 1, projection="3d")
    oblique.add_collection3d(
        Poly3DCollection(
            display_polygons,
            facecolors=face_colours,
            edgecolors="none",
            linewidths=0,
        )
    )
    oblique.set_xlim(centre[0] - radius_xy, centre[0] + radius_xy)
    oblique.set_ylim(centre[1] - radius_xy, centre[1] + radius_xy)
    oblique.set_zlim(centre[2] - radius_height, centre[2] + radius_height)
    oblique.set_box_aspect((2 * radius_xy, 2 * radius_xy, 2 * radius_height))
    oblique.view_init(elev=25, azim=-58)
    oblique.set_title("Oblique view")
    oblique.set_xlabel("X east (m)")
    oblique.set_ylabel("Z south (m)")
    oblique.set_zlabel("Height (m)")
    oblique.grid(False)

    plan = fig.add_subplot(1, 2, 2)
    plan.add_collection(
        PolyCollection(
            vertices[faces][:, :, [0, 2]],
            facecolors=face_colours,
            edgecolors="none",
            linewidths=0,
        )
    )
    plan.set_xlim(vertices[:, 0].min(), vertices[:, 0].max())
    plan.set_ylim(vertices[:, 2].min(), vertices[:, 2].max())
    plan.set_aspect("equal", adjustable="box")
    plan.set_title("Plan view")
    plan.set_xlabel("X east (m)")
    plan.set_ylabel("Z south (m)")
    plan.grid(False)
    fig.suptitle(
        f"Real per-footprint vertex-colour OBJ ({len(vertices):,} vertices; {len(faces):,} faces)"
    )
    fig.text(
        0.5,
        0.01,
        "Project output, status COARSE_READY; appearance is not a ground-truth accuracy assessment.",
        ha="center",
        fontsize=8.5,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    save_figure(fig, "on_demand_vertex_colour_building")


def plot_publications(publications: list[Publication]) -> None:
    usable = [item for item in publications if item.tile_count or item.building_count]
    x = np.arange(len(usable))
    tiles = np.asarray([item.tile_count for item in usable])
    buildings = np.asarray([item.building_count for item in usable])

    fig, ax_tiles = plt.subplots(figsize=(10.2, 4.5))
    ax_buildings = ax_tiles.twinx()
    ax_tiles.bar(x - 0.18, tiles, width=0.36, color="#0072B2", label="Required tiles")
    ax_buildings.bar(
        x + 0.18,
        buildings,
        width=0.36,
        color="#E69F00",
        label="Published buildings",
    )
    for index, item in enumerate(usable):
        if item.corrected_scene_reused is not None:
            label = "reuse" if item.corrected_scene_reused else "fresh"
            ax_tiles.text(
                index,
                max(tiles[index] * 0.93, 1.0),
                label,
                ha="center",
                va="top",
                fontsize=8,
                color="white",
                rotation=90,
                fontweight="bold",
            )
    ax_tiles.set_xticks(x)
    ax_tiles.set_xticklabels([f"P{index + 1}" for index in x])
    ax_tiles.set_xlabel("Publication record (chronological order)")
    ax_tiles.set_ylabel("Tiles", color="#0072B2")
    ax_buildings.set_ylabel("Published buildings", color="#E69F00")
    ax_tiles.set_title("Current retained on-demand publications")
    ax_tiles.grid(axis="y", alpha=0.22)
    handles_a, labels_a = ax_tiles.get_legend_handles_labels()
    handles_b, labels_b = ax_buildings.get_legend_handles_labels()
    ax_tiles.legend(handles_a + handles_b, labels_a + labels_b, loc="upper left")
    fig.text(
        0.5,
        0.005,
        "Tile counts report selection size; building counts report modular outputs.",
        ha="center",
        fontsize=8.3,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save_figure(fig, "on_demand_publications_update")


def plot_topology(buildings: list[dict[str, Any]]) -> None:
    metrics = [item["metrics"] for item in buildings]
    faces = np.asarray([int(item.get("face_count", 0)) for item in metrics])
    boundaries = np.asarray([int(item.get("boundary_edge_count", 0)) for item in metrics])
    watertight = np.asarray([bool(item.get("watertight", False)) for item in metrics])
    nonmanifold_metrics = [
        item for item in metrics if item.get("nonmanifold_edge_count") is not None
    ]
    nonmanifold_faces = np.asarray(
        [int(item.get("face_count", 0)) for item in nonmanifold_metrics]
    )
    nonmanifold = np.asarray(
        [int(item["nonmanifold_edge_count"]) for item in nonmanifold_metrics]
    )

    fig, axes = plt.subplots(1, 2, figsize=(10.2, 4.25))
    axes[0].scatter(faces, boundaries, c="#D55E00", s=42, alpha=0.82, edgecolors="white")
    axes[1].scatter(
        nonmanifold_faces,
        nonmanifold + 1,
        c="#7B3294",
        s=42,
        alpha=0.82,
        edgecolors="white",
    )
    for axis, ylabel in zip(axes, ("Boundary edges", "Non-manifold edges + 1")):
        axis.set_xscale("log")
        axis.set_yscale("log")
        axis.set_xlabel("Faces per published building (log scale)")
        axis.set_ylabel(ylabel)
        axis.grid(alpha=0.22, which="both")
    fig.suptitle(
        f"Topology metrics in {len(buildings)} current building publications "
        f"({int(watertight.sum())} watertight)"
    )
    fig.text(
        0.5,
        0.005,
        f"Boundary panel n={len(metrics)}; non-manifold panel n={len(nonmanifold_metrics)} with the metric recorded.",
        ha="center",
        fontsize=8.4,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 0.95))
    save_figure(fig, "on_demand_building_topology_update")


def main() -> None:
    publications = load_publications()
    schema_publications = [item for item in publications if item.buildings]
    buildings: list[dict[str, Any]] = []
    colour_audit: list[dict[str, Any]] = []
    publication_rows: list[dict[str, Any]] = []

    for publication in publications:
        statuses = [str(item.get("status")) for item in publication.buildings]
        publication_rows.append(
            {
                "selection_id": publication.selection_id,
                "finished": publication.finished.isoformat(),
                "tile_count": publication.tile_count,
                "building_count": publication.building_count,
                "building_statuses": statuses,
                "corrected_scene_reused": publication.corrected_scene_reused,
                "model_completeness_enabled": publication.completeness_enabled,
                "result_path": str(publication.result_path),
            }
        )
        for building in publication.buildings:
            enriched = dict(building)
            enriched["selection_id"] = publication.selection_id
            buildings.append(enriched)
            relative = building.get("outputs", {}).get("cropped_obj")
            if relative:
                obj = publication.result_path.parent / str(relative)
                colour_audit.append(inspect_vertex_colour(obj))

    topology_buildings = [
        item
        for item in buildings
        if isinstance(item.get("metrics"), dict)
        and item["metrics"].get("face_count") is not None
        and item["metrics"].get("boundary_edge_count") is not None
    ]
    plot_publications(publications)
    plot_topology(topology_buildings)
    render_source = next(
        (
            Path(item["path"])
            for item in colour_audit
            if "3eb3c1db3f902f02773f" in item["path"]
        ),
        Path(colour_audit[0]["path"]),
    )
    render_coloured_building(render_source)

    metrics = [item["metrics"] for item in topology_buildings]
    summary = {
        "audited_at_local_date": datetime.now().date().isoformat(),
        "ready_publication_roots": len(publications),
        "ready_roots_with_building_schema": len(schema_publications),
        "published_buildings_with_metrics": len(topology_buildings),
        "coarse_ready_buildings": sum(item.get("status") == "COARSE_READY" for item in buildings),
        "watertight_buildings": sum(bool(item.get("watertight")) for item in metrics),
        "buildings_with_boundary_edges": sum(int(item.get("boundary_edge_count", 0)) > 0 for item in metrics),
        "buildings_reporting_nonmanifold_edges": len(
            [item for item in metrics if item.get("nonmanifold_edge_count") is not None]
        ),
        "buildings_with_nonmanifold_edges": sum(
            int(item["nonmanifold_edge_count"]) > 0
            for item in metrics
            if item.get("nonmanifold_edge_count") is not None
        ),
        "known_building_vertices": sum(int(item.get("vertex_count", 0)) for item in metrics),
        "known_building_faces": sum(int(item.get("face_count", 0)) for item in metrics),
        "all_real_obj_vertices_coloured": bool(colour_audit)
        and all(item["all_vertices_coloured"] for item in colour_audit),
        "colour_audited_buildings": len(colour_audit),
        "publications": publication_rows,
        "vertex_colour_audit": colour_audit,
        "figure_sources": {
            "on_demand_publications_update": [
                str(item.result_path) for item in publications
            ],
            "on_demand_building_topology_update": [
                str(item.result_path) for item in schema_publications
            ],
            "on_demand_vertex_colour_building": str(render_source),
        },
    }
    RESULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
