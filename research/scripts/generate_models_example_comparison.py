"""Audit and render the same-building examples in ``modelsExample``.

The generated figures use only derived mesh appearance.  In particular, they
do not embed the source Google satellite mosaic or the roof-reference images.
The renderer supports OBJ vertex colour and diffuse UV maps so that all panels
are produced with one camera, one metric scale, and one rendering procedure.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from numba import njit
from PIL import Image


@dataclass
class Material:
    name: str
    diffuse: tuple[float, float, float] = (0.72, 0.72, 0.72)
    diffuse_map: str | None = None
    normal_map: str | None = None
    specular_map: str | None = None


@dataclass
class Mesh:
    label: str
    path: Path
    vertices: np.ndarray
    colours: np.ndarray
    texcoords: np.ndarray
    faces: np.ndarray
    tex_faces: np.ndarray
    face_materials: np.ndarray
    material_names: list[str]
    materials: dict[str, Material]
    raw_face_records: int
    vertex_rgb_count: int


MODEL_SPECS = (
    (
        "cityengine",
        "CityEngine 2026",
        Path("modelsExample/CityEngineModel/London1_0.obj"),
    ),
    (
        "large_image",
        "Sat3DGen large-image",
        Path("modelsExample/BigImageModel/buildings/footprint-48b374b3f546/cropped.obj"),
    ),
    (
        "independent_tiles",
        "Sat3DGen independent tiles",
        Path("modelsExample/satelliteImageModel/buildings/footprint-48b374b3f546/cropped.obj"),
    ),
    (
        "large_image_chordatlas",
        "ChordAtlas from large-image",
        Path("modelsExample/BigImageModelAfterchordatlas/BigImageModelAfterchordatlas.obj"),
    ),
    (
        "independent_tiles_chordatlas",
        "ChordAtlas from independent tiles",
        Path("modelsExample/satelliteImageModelAfterchordatlas/satelliteImageModelAfterchordatlas.obj"),
    ),
    (
        "standalone_chordatlas",
        "Stored standalone ChordAtlas reference",
        Path("modelsExample/Originalchordatlas/chordatlas.obj"),
    ),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_mtl(path: Path) -> dict[str, Material]:
    materials: dict[str, Material] = {}
    current: Material | None = None
    if not path.exists():
        return materials
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            command, _, value = line.partition(" ")
            value = value.strip()
            if command == "newmtl":
                current = Material(name=value)
                materials[value] = current
            elif current is not None and command == "Kd":
                fields = value.split()
                if len(fields) >= 3:
                    current.diffuse = tuple(float(x) for x in fields[:3])
            elif current is not None and command == "map_Kd":
                current.diffuse_map = value.split()[-1]
            elif current is not None and command in {"map_bump", "bump"}:
                current.normal_map = value.split()[-1]
            elif current is not None and command == "map_Ks":
                current.specular_map = value.split()[-1]
    return materials


def _obj_index(value: str, count: int) -> int:
    index = int(value)
    return index - 1 if index > 0 else count + index


def parse_obj(label: str, path: Path) -> Mesh:
    vertices: list[tuple[float, float, float]] = []
    colours: list[tuple[float, float, float]] = []
    texcoords: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    tex_faces: list[tuple[int, int, int]] = []
    face_materials: list[int] = []
    material_names: list[str] = []
    material_ids: dict[str, int] = {}
    current_material = -1
    mtl_paths: list[Path] = []
    raw_face_records = 0
    vertex_rgb_count = 0

    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            if raw.startswith("v "):
                fields = raw.split()
                vertices.append(tuple(float(x) for x in fields[1:4]))
                if len(fields) >= 7:
                    rgb = tuple(float(x) for x in fields[4:7])
                    colours.append(rgb)
                    vertex_rgb_count += 1
                else:
                    colours.append((0.72, 0.72, 0.72))
            elif raw.startswith("vt "):
                fields = raw.split()
                texcoords.append((float(fields[1]), float(fields[2])))
            elif raw.startswith("mtllib "):
                mtl_paths.append(path.parent / raw.strip().split(maxsplit=1)[1])
            elif raw.startswith("usemtl "):
                name = raw.strip().split(maxsplit=1)[1]
                if name not in material_ids:
                    material_ids[name] = len(material_names)
                    material_names.append(name)
                current_material = material_ids[name]
            elif raw.startswith("f "):
                raw_face_records += 1
                tokens = raw.split()[1:]
                polygon_v: list[int] = []
                polygon_t: list[int] = []
                for token in tokens:
                    parts = token.split("/")
                    polygon_v.append(_obj_index(parts[0], len(vertices)))
                    if len(parts) > 1 and parts[1]:
                        polygon_t.append(_obj_index(parts[1], len(texcoords)))
                    else:
                        polygon_t.append(-1)
                for index in range(1, len(polygon_v) - 1):
                    faces.append((polygon_v[0], polygon_v[index], polygon_v[index + 1]))
                    tex_faces.append((polygon_t[0], polygon_t[index], polygon_t[index + 1]))
                    face_materials.append(current_material)

    materials: dict[str, Material] = {}
    for mtl_path in mtl_paths:
        materials.update(parse_mtl(mtl_path))

    vertex_array = np.asarray(vertices, dtype=np.float32)
    colour_array = np.asarray(colours, dtype=np.float32)
    texcoord_array = np.asarray(texcoords, dtype=np.float32)
    if not texcoords:
        texcoord_array = np.zeros((1, 2), dtype=np.float32)
    face_array = np.asarray(faces, dtype=np.int32)
    tex_face_array = np.asarray(tex_faces, dtype=np.int32)
    material_array = np.asarray(face_materials, dtype=np.int32)
    if not len(vertex_array) or not len(face_array):
        raise ValueError(f"No geometry in {path}")
    if not np.isfinite(vertex_array).all():
        raise ValueError(f"Non-finite vertex in {path}")
    if vertex_rgb_count and vertex_rgb_count != len(vertex_array):
        raise ValueError(f"Partially coloured OBJ is ambiguous: {path}")
    return Mesh(
        label=label,
        path=path,
        vertices=vertex_array,
        colours=colour_array,
        texcoords=texcoord_array,
        faces=face_array,
        tex_faces=tex_face_array,
        face_materials=material_array,
        material_names=material_names,
        materials=materials,
        raw_face_records=raw_face_records,
        vertex_rgb_count=vertex_rgb_count,
    )


def camera_coordinates(
    vertices: np.ndarray, view: str = "oblique"
) -> tuple[np.ndarray, np.ndarray]:
    centre = np.array(
        [
            0.5 * (vertices[:, 0].min() + vertices[:, 0].max()),
            vertices[:, 1].min(),
            0.5 * (vertices[:, 2].min() + vertices[:, 2].max()),
        ],
        dtype=np.float32,
    )
    points = vertices - centre
    if view == "plan":
        projected = np.column_stack((points[:, 0], -points[:, 2])).astype(np.float32)
        return projected, points[:, 1].astype(np.float32)
    if view != "oblique":
        raise ValueError(f"Unknown view: {view}")
    azimuth = math.radians(-50.0)
    elevation = math.radians(24.0)
    east, up, south = points[:, 0], points[:, 1], points[:, 2]
    horizontal = math.cos(azimuth) * east - math.sin(azimuth) * south
    horizontal_depth = math.sin(azimuth) * east + math.cos(azimuth) * south
    projected_vertical = (
        math.cos(elevation) * up - math.sin(elevation) * horizontal_depth
    )
    depth = math.cos(elevation) * horizontal_depth + math.sin(elevation) * up
    return np.column_stack((horizontal, projected_vertical)).astype(np.float32), depth.astype(np.float32)


def common_projection_bounds(
    meshes: list[Mesh], view: str = "oblique"
) -> tuple[float, float, float, float]:
    projections = [camera_coordinates(mesh.vertices, view)[0] for mesh in meshes]
    x_min = min(float(projected[:, 0].min()) for projected in projections)
    x_max = max(float(projected[:, 0].max()) for projected in projections)
    y_min = min(float(projected[:, 1].min()) for projected in projections)
    y_max = max(float(projected[:, 1].max()) for projected in projections)
    width = x_max - x_min
    height = y_max - y_min
    return (
        x_min - 0.08 * width,
        x_max + 0.08 * width,
        y_min - 0.08 * height,
        y_max + 0.08 * height,
    )


def build_texture_atlas(mesh: Mesh, size: int = 256) -> np.ndarray:
    count = max(1, len(mesh.material_names))
    atlas = np.empty((count, size, size, 3), dtype=np.uint8)
    for index in range(count):
        if index < len(mesh.material_names):
            material = mesh.materials.get(mesh.material_names[index], Material("missing"))
        else:
            material = Material("default")
        colour = np.clip(np.asarray(material.diffuse) * 255.0, 0, 255).astype(np.uint8)
        atlas[index, :, :, :] = colour
        if material.diffuse_map:
            texture_path = mesh.path.parent / material.diffuse_map
            if texture_path.exists():
                with Image.open(texture_path) as source:
                    image = source.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
                    atlas[index] = np.asarray(image, dtype=np.uint8)
    return atlas


@njit
def rasterise_triangles(
    pixel_vertices: np.ndarray,
    depth: np.ndarray,
    faces: np.ndarray,
    vertex_colours: np.ndarray,
    has_vertex_colours: bool,
    texcoords: np.ndarray,
    tex_faces: np.ndarray,
    face_materials: np.ndarray,
    atlas: np.ndarray,
    shades: np.ndarray,
    image: np.ndarray,
    z_buffer: np.ndarray,
) -> None:
    height, width, _ = image.shape
    texture_size = atlas.shape[1]
    for face_index in range(faces.shape[0]):
        a = faces[face_index, 0]
        b = faces[face_index, 1]
        c = faces[face_index, 2]
        x0, y0 = pixel_vertices[a, 0], pixel_vertices[a, 1]
        x1, y1 = pixel_vertices[b, 0], pixel_vertices[b, 1]
        x2, y2 = pixel_vertices[c, 0], pixel_vertices[c, 1]
        denominator = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denominator) < 1.0e-8:
            continue
        min_x = max(0, int(math.floor(min(x0, x1, x2))))
        max_x = min(width - 1, int(math.ceil(max(x0, x1, x2))))
        min_y = max(0, int(math.floor(min(y0, y1, y2))))
        max_y = min(height - 1, int(math.ceil(max(y0, y1, y2))))
        if max_x < min_x or max_y < min_y:
            continue
        material_index = face_materials[face_index]
        if material_index < 0 or material_index >= atlas.shape[0]:
            material_index = 0
        ta, tb, tc = (
            tex_faces[face_index, 0],
            tex_faces[face_index, 1],
            tex_faces[face_index, 2],
        )
        for py in range(min_y, max_y + 1):
            sample_y = py + 0.5
            for px in range(min_x, max_x + 1):
                sample_x = px + 0.5
                wa = ((y1 - y2) * (sample_x - x2) + (x2 - x1) * (sample_y - y2)) / denominator
                wb = ((y2 - y0) * (sample_x - x2) + (x0 - x2) * (sample_y - y2)) / denominator
                wc = 1.0 - wa - wb
                if wa < -1.0e-5 or wb < -1.0e-5 or wc < -1.0e-5:
                    continue
                z = wa * depth[a] + wb * depth[b] + wc * depth[c]
                if z <= z_buffer[py, px]:
                    continue
                if has_vertex_colours:
                    red = wa * vertex_colours[a, 0] + wb * vertex_colours[b, 0] + wc * vertex_colours[c, 0]
                    green = wa * vertex_colours[a, 1] + wb * vertex_colours[b, 1] + wc * vertex_colours[c, 1]
                    blue = wa * vertex_colours[a, 2] + wb * vertex_colours[b, 2] + wc * vertex_colours[c, 2]
                    red = min(1.0, max(0.0, red)) * 255.0
                    green = min(1.0, max(0.0, green)) * 255.0
                    blue = min(1.0, max(0.0, blue)) * 255.0
                elif ta >= 0 and tb >= 0 and tc >= 0:
                    u = wa * texcoords[ta, 0] + wb * texcoords[tb, 0] + wc * texcoords[tc, 0]
                    v = wa * texcoords[ta, 1] + wb * texcoords[tb, 1] + wc * texcoords[tc, 1]
                    u = u - math.floor(u)
                    v = v - math.floor(v)
                    tx = min(texture_size - 1, max(0, int(u * (texture_size - 1) + 0.5)))
                    ty = min(texture_size - 1, max(0, int((1.0 - v) * (texture_size - 1) + 0.5)))
                    red = atlas[material_index, ty, tx, 0]
                    green = atlas[material_index, ty, tx, 1]
                    blue = atlas[material_index, ty, tx, 2]
                else:
                    red = atlas[material_index, 0, 0, 0]
                    green = atlas[material_index, 0, 0, 1]
                    blue = atlas[material_index, 0, 0, 2]
                shade = shades[face_index]
                image[py, px, 0] = np.uint8(min(255.0, red * shade))
                image[py, px, 1] = np.uint8(min(255.0, green * shade))
                image[py, px, 2] = np.uint8(min(255.0, blue * shade))
                z_buffer[py, px] = z


def render_mesh(
    mesh: Mesh,
    projection_bounds: tuple[float, float, float, float],
    view: str = "oblique",
    width: int = 760,
    height: int = 620,
) -> np.ndarray:
    projected, depth = camera_coordinates(mesh.vertices, view)
    x_min, x_max, y_min, y_max = projection_bounds
    pixel_vertices = np.empty_like(projected)
    pixel_vertices[:, 0] = (projected[:, 0] - x_min) * (width - 1) / (x_max - x_min)
    pixel_vertices[:, 1] = (y_max - projected[:, 1]) * (height - 1) / (y_max - y_min)

    triangles = mesh.vertices[mesh.faces]
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    lengths[lengths == 0.0] = 1.0
    normals /= lengths[:, None]
    light = np.asarray((0.25, 0.88, -0.40), dtype=np.float32)
    light /= np.linalg.norm(light)
    shades = (0.76 + 0.24 * np.abs(normals @ light)).astype(np.float32)

    image = np.full((height, width, 3), (244, 246, 248), dtype=np.uint8)
    z_buffer = np.full((height, width), -np.inf, dtype=np.float32)
    atlas = build_texture_atlas(mesh)
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


def edge_statistics(mesh: Mesh) -> tuple[int, int, int]:
    faces = mesh.faces.astype(np.int64, copy=False)
    edges = np.concatenate(
        (faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]), axis=0
    )
    edges.sort(axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return int(len(counts)), int(np.count_nonzero(counts == 1)), int(np.count_nonzero(counts > 2))


def colour_statistics(mesh: Mesh) -> dict[str, float | int] | None:
    if mesh.vertex_rgb_count != len(mesh.vertices):
        return None
    colours = np.clip(mesh.colours, 0.0, 1.0)
    luminance = colours @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    edges = np.concatenate(
        (
            mesh.faces[:, [0, 1]],
            mesh.faces[:, [1, 2]],
            mesh.faces[:, [2, 0]],
        ),
        axis=0,
    )
    edges.sort(axis=1)
    edges = np.unique(edges, axis=0)
    differences = np.linalg.norm(colours[edges[:, 0]] - colours[edges[:, 1]], axis=1)
    quantised = np.rint(colours * 255.0).astype(np.uint8)
    return {
        "unique_rgb_triplets": int(len(np.unique(quantised, axis=0))),
        "luminance_standard_deviation": float(luminance.std()),
        "mean_adjacent_edge_rgb_distance": float(differences.mean()),
        "median_adjacent_edge_rgb_distance": float(np.median(differences)),
    }


def material_statistics(mesh: Mesh) -> dict[str, Any]:
    referenced = sorted(
        {
            mesh.material_names[index]
            for index in np.unique(mesh.face_materials)
            if 0 <= index < len(mesh.material_names)
        }
    )
    diffuse = normal = specular = complete = 0
    error_materials: list[str] = []
    texture_files: list[str] = []
    for name in referenced:
        material = mesh.materials.get(name)
        if "error" in name.lower():
            error_materials.append(name)
        if material is None:
            continue
        diffuse += int(bool(material.diffuse_map))
        normal += int(bool(material.normal_map))
        specular += int(bool(material.specular_map))
        complete += int(bool(material.diffuse_map and material.normal_map and material.specular_map))
        for value in (material.diffuse_map, material.normal_map, material.specular_map):
            if value:
                texture_files.append(value)
    return {
        "referenced_material_count": len(referenced),
        "diffuse_map_materials": diffuse,
        "normal_map_materials": normal,
        "specular_map_materials": specular,
        "complete_diffuse_normal_specular_materials": complete,
        "error_material_names": error_materials,
        "texture_files": sorted(set(texture_files)),
    }


def audit_mesh(mesh: Mesh) -> dict[str, Any]:
    unique_edges, boundary_edges, nonmanifold_edges = edge_statistics(mesh)
    bounds = np.stack((mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)))
    return {
        "path": mesh.path.as_posix(),
        "sha256": sha256(mesh.path),
        "vertex_records": int(len(mesh.vertices)),
        "vertex_rgb_records": int(mesh.vertex_rgb_count),
        "texture_coordinate_records": int(len(mesh.texcoords)) if np.any(mesh.tex_faces >= 0) else 0,
        "obj_face_records": int(mesh.raw_face_records),
        "triangles_after_fan_triangulation": int(len(mesh.faces)),
        "bounds_xyz": bounds.astype(float).tolist(),
        "extent_xyz_m": (bounds[1] - bounds[0]).astype(float).tolist(),
        "raw_index_edge_incidence": {
            "unique_edges": unique_edges,
            "boundary_edges": boundary_edges,
            "nonmanifold_edges": nonmanifold_edges,
            "note": "Computed before duplicate-face removal or positional welding; use result manifests for the matched Sat3DGen assets.",
        },
        "colour_statistics": colour_statistics(mesh),
        "materials": material_statistics(mesh),
    }


def load_dsm_evidence(root: Path, relative: str, footprint_id: str) -> dict[str, Any]:
    source = root / relative
    record = json.loads(source.read_text(encoding="utf-8"))
    footprint = next(item for item in record["footprints"] if item["id"] == footprint_id)
    metrics = footprint["building_metrics"]
    dsm = record["dsm"]
    return {
        "result_path": relative,
        "contract": record["pipeline_contract_version"],
        "selection_id": record["selection_id"],
        "footprint_id": footprint_id,
        "source_feature_id": footprint.get("source_feature_id"),
        "vertex_count": metrics["vertex_count"],
        "face_count": metrics["face_count"],
        "relief_m": metrics["relief_m"],
        "boundary_edge_count": metrics["boundary_edge_count"],
        "nonmanifold_edge_count": metrics["nonmanifold_edge_count"],
        "watertight": metrics["watertight"],
        "dsm_status": dsm["status"],
        "dsm_crs": dsm["crs"],
        "dsm_source_coverage_ratio": dsm["source_coverage_ratio"],
        "dsm_mesh_vertex_coverage_ratio": dsm["mesh_vertex_coverage_ratio"],
        "dsm_corrected_vertex_count": dsm["corrected_vertex_count"],
        "dsm_maximum_vertical_delta_m": dsm["maximum_vertical_delta_m"],
        "vertex_colours_preserved": dsm.get("vertex_colors_preserved"),
    }


def texture_hash_overlap(root: Path) -> dict[str, Any]:
    directories = {
        "large_image_chordatlas": root / "modelsExample/BigImageModelAfterchordatlas",
        "independent_tiles_chordatlas": root / "modelsExample/satelliteImageModelAfterchordatlas",
        "standalone_chordatlas": root / "modelsExample/Originalchordatlas",
    }
    hashes = {
        name: {sha256(path) for path in directory.glob("*.png")}
        for name, directory in directories.items()
    }
    result: dict[str, Any] = {
        name: {"png_files": len(list(directories[name].glob("*.png"))), "unique_hashes": len(values)}
        for name, values in hashes.items()
    }
    names = list(directories)
    result["pairwise_shared_hashes"] = {
        f"{names[i]}__{names[j]}": len(hashes[names[i]] & hashes[names[j]])
        for i in range(len(names))
        for j in range(i + 1, len(names))
    }
    return result


def save_panel_figure(
    renders: list[np.ndarray],
    titles: list[str],
    subtitles: list[str],
    output_stem: Path,
    rows: int,
    columns: int,
) -> None:
    fig, axes = plt.subplots(rows, columns, figsize=(10.2, 4.2 * rows), constrained_layout=True)
    axes_array = np.asarray(axes).reshape(-1)
    for index, (axis, render, title, subtitle) in enumerate(
        zip(axes_array, renders, titles, subtitles)
    ):
        axis.imshow(render)
        axis.set_title(f"({chr(97 + index)}) {title}", fontsize=10.2, pad=7)
        axis.text(
            0.5,
            -0.025,
            subtitle,
            transform=axis.transAxes,
            ha="center",
            va="top",
            fontsize=8.2,
            color="#303030",
        )
        axis.axis("off")
    for axis in axes_array[len(renders):]:
        axis.axis("off")
    for suffix in ("pdf", "png"):
        fig.savefig(
            output_stem.with_suffix(f".{suffix}"),
            dpi=300,
            bbox_inches="tight",
            facecolor="white",
        )
    plt.close(fig)


def save_material_map_figure(root: Path, output_stem: Path) -> None:
    rows = [
        (
            "CityEngine facade atlas",
            [
                root / "modelsExample/CityEngineModel/u_f004_t006_Residential_005.jpg",
                None,
                None,
            ],
        ),
        (
            "ChordAtlas from large-image",
            [
                root / "modelsExample/BigImageModelAfterchordatlas/2.png",
                root / "modelsExample/BigImageModelAfterchordatlas/2_norm.png",
                root / "modelsExample/BigImageModelAfterchordatlas/2_spec.png",
            ],
        ),
        (
            "ChordAtlas from independent tiles",
            [
                root / "modelsExample/satelliteImageModelAfterchordatlas/2.png",
                root / "modelsExample/satelliteImageModelAfterchordatlas/2_norm.png",
                root / "modelsExample/satelliteImageModelAfterchordatlas/2_spec.png",
            ],
        ),
    ]
    column_titles = ("Diffuse / facade colour", "Normal map", "Specular mask")
    fig, axes = plt.subplots(3, 3, figsize=(10.2, 8.1), constrained_layout=True)
    for row_index, (row_title, paths) in enumerate(rows):
        for column_index, path in enumerate(paths):
            axis = axes[row_index, column_index]
            if path is None:
                axis.set_facecolor((0.93, 0.93, 0.93))
                axis.text(
                    0.5,
                    0.5,
                    "Not stored for\nthis facade material",
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="#505050",
                    transform=axis.transAxes,
                )
            else:
                with Image.open(path) as source:
                    axis.imshow(source.convert("RGB"))
            if row_index == 0:
                axis.set_title(column_titles[column_index], fontsize=10)
            if column_index == 0:
                axis.set_ylabel(row_title, fontsize=9.2)
            axis.set_xticks([])
            axis.set_yticks([])
            for spine in axis.spines.values():
                spine.set_color("#808080")
                spine.set_linewidth(0.5)
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
    results_dir = (args.results_dir or root / "research/results/models_example").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    meshes_by_key: dict[str, Mesh] = {}
    for key, label, relative in MODEL_SPECS:
        mesh = parse_obj(label, root / relative)
        meshes_by_key[key] = mesh
        print(f"Loaded {key}: {len(mesh.vertices):,} vertices, {len(mesh.faces):,} triangles")

    meshes = list(meshes_by_key.values())
    matched_meshes = [meshes_by_key["large_image"], meshes_by_key["independent_tiles"]]
    matched_oblique_bounds = common_projection_bounds(matched_meshes, "oblique")
    matched_plan_bounds = common_projection_bounds(matched_meshes, "plan")
    matched_renders = [
        render_mesh(meshes_by_key["large_image"], matched_oblique_bounds, "oblique"),
        render_mesh(meshes_by_key["independent_tiles"], matched_oblique_bounds, "oblique"),
        render_mesh(meshes_by_key["large_image"], matched_plan_bounds, "plan"),
        render_mesh(meshes_by_key["independent_tiles"], matched_plan_bounds, "plan"),
    ]
    save_panel_figure(
        matched_renders,
        [
            "Large-image: oblique",
            "Independent tiles: oblique",
            "Large-image: plan",
            "Independent tiles: plan",
        ],
        [
            "Fractional-feather density fusion",
            "Nine meshes followed by mesh-space assembly",
            "Same footprint, camera and metric scale",
            "Same footprint, camera and metric scale",
        ],
        output_dir / "matched_sat3dgen_building_comparison",
        rows=2,
        columns=2,
    )

    representation_keys = [
        "cityengine",
        "large_image_chordatlas",
        "independent_tiles_chordatlas",
        "standalone_chordatlas",
    ]
    representation_meshes = [meshes_by_key[key] for key in representation_keys]
    representation_bounds = common_projection_bounds(representation_meshes, "oblique")
    representation_renders = [
        render_mesh(meshes_by_key[key], representation_bounds, "oblique")
        for key in representation_keys
    ]
    save_panel_figure(
        representation_renders,
        [
            "CityEngine 2026 export",
            "ChordAtlas from large-image",
            "ChordAtlas from independent tiles",
            "Standalone ChordAtlas reference",
        ],
        [
            "One exported initial shape; UV facade atlases",
            "Route-labelled export; UV diffuse maps shown",
            "Route-labelled export; UV diffuse maps shown",
            "No matched location or run manifest retained",
        ],
        output_dir / "procedural_representation_examples",
        rows=2,
        columns=2,
    )
    save_material_map_figure(root, output_dir / "appearance_map_encoding_examples")

    audits = {key: audit_mesh(mesh) for key, mesh in meshes_by_key.items()}
    footprint_id = "footprint-48b374b3f546"
    evidence = {
        "schema_version": 1,
        "study": "modelsExample same-building representation comparison",
        "directly_matched_building": {
            "footprint_id": footprint_id,
            "osm_way": "369245408",
            "scope": "BigImageModel and satelliteImageModel request/result manifests",
        },
        "illustrative_correspondence": {
            "cityengine_name_from_log": "London Pavillion",
            "cityengine_identity_boundary": "No OSM identifier or common-CRS manifest is stored with the export.",
            "route_labelled_chordatlas_boundary": "The filenames and footprint-consistent envelopes support route attribution, but the OBJ files do not embed the footprint identifier.",
            "standalone_chordatlas_boundary": "No matching location or run manifest is retained.",
        },
        "rendering": {
            "camera": "orthographic; oblique views use azimuth -50 degrees and elevation 24 degrees",
            "scale": "common metric projection bounds within each comparison figure",
            "appearance": "OBJ vertex RGB or diffuse UV map; normal/specular maps not applied",
            "source_satellite_imagery_embedded": False,
        },
        "models": audits,
        "matched_sat3dgen_evidence": {
            "large_image": load_dsm_evidence(
                root, "modelsExample/BigImageModel/result.json", footprint_id
            ),
            "independent_tiles": load_dsm_evidence(
                root, "modelsExample/satelliteImageModel/result.json", footprint_id
            ),
        },
        "texture_hash_overlap": texture_hash_overlap(root),
        "claim_boundaries": [
            "Mesh and material counts describe representation, not geometric or appearance fidelity.",
            "DSM APPLIED and full coverage establish vertical conditioning, not height accuracy.",
            "Sat3DGen vertex RGB is predicted while conditioned on satellite imagery; it is not direct pixel projection.",
            "The ChordAtlas maps demonstrate generated procedural appearance, not recovery of true facade texture.",
            "The standalone ChordAtlas reference has no retained manifest tying it to the matched selection.",
            "CityEngine is about 125 m from the matched project footprint and is an illustrative representation example, not a common satellite-reconstruction baseline.",
            "The route-labelled ChordAtlas exports have footprint-consistent geometry but do not embed the OSM identity in their OBJ files.",
        ],
    }
    json_path = results_dir / "models_example_audit.json"
    json_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    csv_path = results_dir / "matched_sat3dgen_building_metrics.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "route",
                "osm_way",
                "vertices",
                "faces",
                "relief_m",
                "boundary_edges",
                "nonmanifold_edges",
                "watertight",
                "dsm_vertex_coverage_ratio",
            ]
        )
        for route, record in evidence["matched_sat3dgen_evidence"].items():
            writer.writerow(
                [
                    route,
                    "369245408",
                    record["vertex_count"],
                    record["face_count"],
                    record["relief_m"],
                    record["boundary_edge_count"],
                    record["nonmanifold_edge_count"],
                    record["watertight"],
                    record["dsm_mesh_vertex_coverage_ratio"],
                ]
            )
    print(f"Wrote {json_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
