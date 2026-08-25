"""Render and audit the six same-building outputs in ``modelsExample2``.

The six-panel appendix figure compares the selected building at three points
in the processing/design space: the two satellite-derived meshes, their two
ChordAtlas outputs, the CityEngine export, and the stored original ChordAtlas
output.  Every panel uses the same orthographic direction and metres-per-pixel
scale.  Each OBJ is translated to its own horizontal centre because the files
do not all retain a common coordinate origin; no per-panel rescaling is used.

Appearance comes from OBJ vertex RGB or the diffuse maps referenced by each
OBJ/MTL package.  Source satellite imagery is not embedded in the figures.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

from generate_models_example_comparison import (
    Mesh,
    build_texture_atlas,
    camera_coordinates,
    common_projection_bounds,
    material_statistics,
    parse_obj,
    rasterise_triangles,
    render_mesh,
    save_panel_figure,
    sha256,
)


FOOTPRINT_ID = "footprint-a688258f4d03"
SOURCE_FEATURE_ID = "way/33719501"


def manifest_record(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    footprint = next(
        item for item in manifest["footprints"] if item["id"] == FOOTPRINT_ID
    )
    metrics = footprint["building_metrics"]
    dsm = manifest["dsm"]
    return {
        "manifest": path.as_posix(),
        "pipeline_contract_version": manifest["pipeline_contract_version"],
        "selection_id": manifest["selection_id"],
        "footprint_id": footprint["id"],
        "source_feature_id": footprint.get("source_feature_id", SOURCE_FEATURE_ID),
        "vertex_count": int(metrics["vertex_count"]),
        "face_count": int(metrics["face_count"]),
        "relief_m": float(metrics["relief_m"]),
        "boundary_edge_count": int(metrics["boundary_edge_count"]),
        "nonmanifold_edge_count": int(metrics["nonmanifold_edge_count"]),
        "watertight": bool(metrics["watertight"]),
        "dsm_status": dsm["status"],
        "dsm_crs": dsm["crs"],
        "dsm_mesh_vertex_coverage_ratio": float(dsm["mesh_vertex_coverage_ratio"]),
        "dsm_topology_changed": dsm.get("topology_changed"),
        "dsm_vertex_colours_preserved": dsm.get("vertex_colors_preserved"),
    }


def mesh_record(mesh: Mesh) -> dict[str, Any]:
    bounds = np.stack((mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)))
    materials = material_statistics(mesh)
    texture_files = materials.pop("texture_files")
    return {
        "obj": mesh.path.as_posix(),
        "sha256": sha256(mesh.path),
        "vertex_records": int(len(mesh.vertices)),
        "vertex_rgb_records": int(mesh.vertex_rgb_count),
        "texture_coordinate_records": (
            int(len(mesh.texcoords)) if np.any(mesh.tex_faces >= 0) else 0
        ),
        "obj_face_records": int(mesh.raw_face_records),
        "triangles_after_obj_triangulation": int(len(mesh.faces)),
        "bounds_xyz_m": bounds.astype(float).tolist(),
        "extent_xyz_m": (bounds[1] - bounds[0]).astype(float).tolist(),
        "appearance_encoding": (
            "complete per-vertex RGB"
            if mesh.vertex_rgb_count == len(mesh.vertices)
            else "OBJ/MTL UV materials"
            if np.any(mesh.tex_faces >= 0)
            else "constant material colour"
        ),
        "materials": {
            **materials,
            "referenced_texture_file_count": len(texture_files),
        },
    }


def assert_manifest_matches_mesh(mesh: Mesh, record: dict[str, Any]) -> None:
    if len(mesh.vertices) != record["vertex_count"]:
        raise ValueError(
            f"Vertex-count mismatch for {mesh.path}: "
            f"OBJ {len(mesh.vertices)} versus manifest {record['vertex_count']}"
        )
    if len(mesh.faces) != record["face_count"]:
        raise ValueError(
            f"Face-count mismatch for {mesh.path}: "
            f"OBJ {len(mesh.faces)} versus manifest {record['face_count']}"
        )
    if mesh.vertex_rgb_count != len(mesh.vertices):
        raise ValueError(f"Expected complete vertex RGB in {mesh.path}")


def add_shared_camera_bounds(
    mesh: Mesh, lower: np.ndarray, upper: np.ndarray
) -> Mesh:
    """Add two unreferenced vertices so both renders use one camera centre.

    The vertices never enter a face and therefore do not alter the rendered
    geometry.  They make ``camera_coordinates`` see identical axis bounds for
    both otherwise matched meshes.
    """

    vertices = np.vstack((mesh.vertices, lower, upper)).astype(np.float32)
    colours = np.vstack(
        (mesh.colours, np.full((2, 3), 0.72, dtype=np.float32))
    ).astype(np.float32)
    return replace(
        mesh,
        vertices=vertices,
        colours=colours,
        vertex_rgb_count=mesh.vertex_rgb_count + 2,
    )


def rotate_y_for_presentation(mesh: Mesh, degrees: float) -> Mesh:
    """Rotate about the mesh's horizontal centre without changing its scale."""

    angle = math.radians(degrees)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    vertices = mesh.vertices.copy()
    centre_x = 0.5 * (vertices[:, 0].min() + vertices[:, 0].max())
    centre_z = 0.5 * (vertices[:, 2].min() + vertices[:, 2].max())
    east = vertices[:, 0] - centre_x
    south = vertices[:, 2] - centre_z
    vertices[:, 0] = centre_x + cosine * east + sine * south
    vertices[:, 2] = centre_z - sine * east + cosine * south
    return replace(mesh, vertices=vertices)


def render_mesh_compact_atlas(
    mesh: Mesh,
    projection_bounds: tuple[float, float, float, float],
    view: str = "oblique",
    width: int = 760,
    height: int = 620,
    texture_size: int = 96,
) -> np.ndarray:
    """Render with the shared software rasteriser and a bounded texture atlas.

    SIMAC references thousands of material images.  The base renderer's
    256-pixel-per-material atlas is unnecessary for an appendix panel and can
    consume hundreds of megabytes.  A 96-pixel atlas retains visible diffuse
    structure at the final panel resolution while bounding memory use.
    """

    projected, depth = camera_coordinates(mesh.vertices, view)
    x_min, x_max, y_min, y_max = projection_bounds
    pixel_vertices = np.empty_like(projected)
    pixel_vertices[:, 0] = (
        (projected[:, 0] - x_min) * (width - 1) / (x_max - x_min)
    )
    pixel_vertices[:, 1] = (
        (y_max - projected[:, 1]) * (height - 1) / (y_max - y_min)
    )

    triangles = mesh.vertices[mesh.faces]
    normals = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0.0] = 1.0
    normals /= lengths[:, None]
    light = np.asarray((0.25, 0.88, -0.40), dtype=np.float32)
    light /= np.linalg.norm(light)
    shades = (0.76 + 0.24 * np.abs(normals @ light)).astype(np.float32)

    image = np.full((height, width, 3), (244, 246, 248), dtype=np.uint8)
    z_buffer = np.full((height, width), -np.inf, dtype=np.float32)
    atlas = build_texture_atlas(mesh, size=texture_size)
    rasterise_triangles(
        pixel_vertices.astype(np.float32),
        depth,
        mesh.faces,
        mesh.colours,
        mesh.vertex_rgb_count == len(mesh.vertices),
        mesh.texcoords,
        mesh.tex_faces,
        mesh.face_materials,
        atlas,
        shades,
        image,
        z_buffer,
    )
    return image


def save_six_panel_figure(
    renders: list[np.ndarray],
    titles: list[str],
    subtitles: list[str],
    output_stem: Path,
) -> None:
    """Save the same-building 2x3 comparison at report and QA resolution."""

    fig, axes = plt.subplots(2, 3, figsize=(7.0, 5.45), constrained_layout=True)
    for index, (axis, render, title, subtitle) in enumerate(
        zip(axes.reshape(-1), renders, titles, subtitles)
    ):
        axis.imshow(render)
        axis.set_title(f"({chr(97 + index)}) {title}", fontsize=8.8, pad=5)
        axis.text(
            0.5,
            -0.018,
            subtitle,
            transform=axis.transAxes,
            ha="center",
            va="top",
            fontsize=7.2,
            color="#303030",
        )
        axis.axis("off")
    for suffix in ("pdf", "png"):
        fig.savefig(
            output_stem.with_suffix(f".{suffix}"),
            dpi=300,
            bbox_inches="tight",
            facecolor="white",
        )
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--results-dir", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = (args.output_dir or root / "figures/generated").resolve()
    results_dir = (
        args.results_dir or root / "research/results/models_example2"
    ).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    sources: dict[str, dict[str, Any]] = {
        "large_image": {
            "label": "Large-image Sat3DGen",
            "title": "Large-image Sat3DGen",
            "obj": root
            / "modelsExample2/BigImageModel/buildings"
            / FOOTPRINT_ID
            / "cropped.obj",
            "manifest": root / "modelsExample2/BigImageModel/result.json",
        },
        "large_image_chordatlas": {
            "label": "Large-image after ChordAtlas",
            "title": "Large-image after ChordAtlas",
            "obj": root / "modelsExample2/BigImageModelAfterchordatlas/BIMAC.obj",
        },
        "independent_tiles": {
            "label": "Independent-tile Sat3DGen",
            "title": "Independent-tile Sat3DGen",
            "obj": root
            / "modelsExample2/satelliteImageModel/buildings"
            / FOOTPRINT_ID
            / "cropped.obj",
            "manifest": root / "modelsExample2/satelliteImageModel/result.json",
        },
        "independent_tiles_chordatlas": {
            "label": "Independent tiles after ChordAtlas",
            "title": "Independent tiles after ChordAtlas",
            "obj": root
            / "modelsExample2/satelliteImageModelAfterchordatlas/SIMAC.obj",
        },
        "cityengine": {
            "label": "CityEngine",
            "title": "CityEngine",
            "obj": root / "modelsExample2/CityEngineModel/London1_0.obj",
        },
        "original_chordatlas": {
            "label": "Original ChordAtlas",
            "title": "Original ChordAtlas",
            "obj": root / "modelsExample2/Originalchordatlas/OriginalChordatlas.obj",
        },
    }

    meshes: dict[str, Mesh] = {}
    manifests: dict[str, dict[str, Any]] = {}
    for key, source in sources.items():
        if not source["obj"].is_file():
            raise FileNotFoundError(source["obj"])
        mesh = parse_obj(source["label"], source["obj"])
        if "manifest" in source:
            record = manifest_record(source["manifest"])
            assert_manifest_matches_mesh(mesh, record)
            manifests[key] = record
        meshes[key] = mesh
        print(
            f"Loaded {key}: {len(mesh.vertices):,} vertices, "
            f"{len(mesh.faces):,} triangles"
        )

    matched_order = [meshes["large_image"], meshes["independent_tiles"]]
    stacked_vertices = np.vstack([mesh.vertices for mesh in matched_order])
    shared_lower = stacked_vertices.min(axis=0)
    shared_upper = stacked_vertices.max(axis=0)
    framed = {
        key: add_shared_camera_bounds(mesh, shared_lower, shared_upper)
        for key, mesh in (
            ("large_image", meshes["large_image"]),
            ("independent_tiles", meshes["independent_tiles"]),
        )
    }
    framed_ordered = [framed["large_image"], framed["independent_tiles"]]
    oblique_bounds = common_projection_bounds(framed_ordered, "oblique")
    plan_bounds = common_projection_bounds(framed_ordered, "plan")
    matched_renders = [
        render_mesh(framed["large_image"], oblique_bounds, "oblique"),
        render_mesh(framed["independent_tiles"], oblique_bounds, "oblique"),
        render_mesh(framed["large_image"], plan_bounds, "plan"),
        render_mesh(framed["independent_tiles"], plan_bounds, "plan"),
    ]
    figure_stem = output_dir / "models_example2_matched_sat3dgen"
    save_panel_figure(
        matched_renders,
        [
            "Large-image route: oblique",
            "Independent-tile route: oblique",
            "Large-image route: plan",
            "Independent-tile route: plan",
        ],
        [
            f"{len(meshes['large_image'].vertices):,} vertices, "
            f"{len(meshes['large_image'].faces):,} faces",
            f"{len(meshes['independent_tiles'].vertices):,} vertices, "
            f"{len(meshes['independent_tiles'].faces):,} faces",
            "Same footprint, orthographic camera and metric scale",
            "Same footprint, orthographic camera and metric scale",
        ],
        figure_stem,
        rows=2,
        columns=2,
    )

    with Image.open(figure_stem.with_suffix(".png")) as image:
        matched_figure_pixels = [int(image.width), int(image.height)]

    six_panel_order = (
        "large_image",
        "large_image_chordatlas",
        "cityengine",
        "independent_tiles",
        "independent_tiles_chordatlas",
        "original_chordatlas",
    )
    presentation_meshes = dict(meshes)
    presentation_meshes["original_chordatlas"] = rotate_y_for_presentation(
        meshes["original_chordatlas"], 180.0
    )
    six_panel_meshes = [presentation_meshes[key] for key in six_panel_order]
    six_panel_bounds = common_projection_bounds(six_panel_meshes, "oblique")
    six_panel_renders = [
        render_mesh_compact_atlas(mesh, six_panel_bounds, "oblique")
        for mesh in six_panel_meshes
    ]

    def subtitle(mesh: Mesh) -> str:
        appearance = (
            "vertex RGB"
            if mesh.vertex_rgb_count == len(mesh.vertices)
            else "UV diffuse materials"
            if np.any(mesh.tex_faces >= 0)
            else "material colour"
        )
        return (
            f"{appearance}\n"
            f"{len(mesh.vertices):,} vertices\n"
            f"{len(mesh.faces):,} triangles"
        )

    six_panel_stem = output_dir / "models_example2_six_method_comparison"
    save_six_panel_figure(
        six_panel_renders,
        [
            {
                "large_image": "Large-image\nSat3DGen",
                "large_image_chordatlas": "Large-image to\nChordAtlas",
                "cityengine": "CityEngine",
                "independent_tiles": "Independent-tile\nSat3DGen",
                "independent_tiles_chordatlas": "Independent tiles to\nChordAtlas",
                "original_chordatlas": "Original\nChordAtlas",
            }[key]
            for key in six_panel_order
        ],
        [subtitle(meshes[key]) for key in six_panel_order],
        six_panel_stem,
    )
    with Image.open(six_panel_stem.with_suffix(".png")) as image:
        six_panel_pixels = [int(image.width), int(image.height)]

    audit = {
        "schema_version": 2,
        "study": "modelsExample2 six-output same-building representation comparison",
        "matched_identity": {
            "footprint_id": FOOTPRINT_ID,
            "source_feature_id": SOURCE_FEATURE_ID,
            "scope": (
                "The BigImageModel and satelliteImageModel result manifests "
                "name the same footprint and OSM way. The six directories are "
                "the supplied same-building case; the four other OBJ files do "
                "not embed that OSM identifier."
            ),
        },
        "rendering": {
            "camera": (
                "shared orthographic direction; oblique azimuth -50 degrees "
                "and elevation 24 degrees"
            ),
            "centring": (
                "each OBJ translated to its own horizontal AABB centre and "
                "minimum Y; coordinate origins are not compared"
            ),
            "presentation_transforms": {
                "original_chordatlas": (
                    "180 degree rotation about the mesh-centred Y axis to "
                    "present the same side as the other stored outputs; no "
                    "rescaling and no source-file modification"
                )
            },
            "scale": (
                "one common metric projection bound for all six panels; no "
                "per-panel rescaling"
            ),
            "common_oblique_projection_bounds_m": [
                float(value) for value in six_panel_bounds
            ],
            "appearance": (
                "complete OBJ vertex RGB where present; otherwise OBJ/MTL "
                "diffuse UV materials rendered by the same rasteriser"
            ),
            "uv_texture_atlas_resolution_per_material": 96,
            "source_satellite_imagery_embedded": False,
            "figures": {
                "matched_sat3dgen": {
                    "pdf": figure_stem.with_suffix(".pdf").as_posix(),
                    "png": figure_stem.with_suffix(".png").as_posix(),
                    "png_pixels": matched_figure_pixels,
                },
                "six_method_comparison": {
                    "pdf": six_panel_stem.with_suffix(".pdf").as_posix(),
                    "png": six_panel_stem.with_suffix(".png").as_posix(),
                    "png_pixels": six_panel_pixels,
                    "panel_order": list(six_panel_order),
                },
            },
        },
        "routes": {
            key: {
                "mesh": mesh_record(meshes[key]),
                **({"manifest": manifests[key]} if key in manifests else {}),
            }
            for key in six_panel_order
        },
        "comparison_boundary": [
            "All panels depict the supplied same-building case.",
            "Only the two satellite-derived result manifests directly retain the common footprint and OSM identity.",
            "Translation centring removes source-origin offsets; a shared orientation and metres-per-pixel scale preserve relative extent.",
            "The figure compares stored representations and appearance encodings, not accuracy against surveyed ground truth.",
            "Vertex RGB is model output conditioned by satellite imagery, not direct satellite-pixel projection.",
        ],
    }
    audit_path = results_dir / "models_example2_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(f"Wrote {figure_stem.with_suffix('.pdf')}")
    print(f"Wrote {figure_stem.with_suffix('.png')}")
    print(f"Wrote {six_panel_stem.with_suffix('.pdf')}")
    print(f"Wrote {six_panel_stem.with_suffix('.png')}")
    print(f"Wrote {audit_path}")


if __name__ == "__main__":
    main()
