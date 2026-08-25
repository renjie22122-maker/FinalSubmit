from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


_TILE_RE = re.compile(
    r"^(?:sat|satellite)_([+-]?(?:\d+(?:\.\d*)?|\.\d+))_([+-]?(?:\d+(?:\.\d*)?|\.\d+))$",
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
MERGE_STAGE_ORDER = [
    "coordinate_transform",
    "osm_semantic_prealign",
    "remove_bottom_faces",
    "stitch_tiles",
    "dsm_height_correction",
    "export_scene",
]


class TopLevelPipelineError(RuntimeError):
    pass


def _path_is_within(path: Path, root: Path) -> bool:
    """Containment check that tolerates Windows 8.3/long-path aliases."""

    path = path.resolve()
    root = root.resolve()
    try:
        path.relative_to(root)
        return True
    except ValueError:
        pass
    if root.exists():
        for ancestor in (path, *path.parents):
            if not ancestor.exists():
                continue
            try:
                if os.path.samefile(ancestor, root):
                    return True
            except OSError:
                continue
    return False


def _tile(path: Path):
    match = _TILE_RE.fullmatch(path.stem)
    if match is None:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    return f"sat_{lat:.6f}_{lon:.6f}", lat, lon


def _inside(lat: float, lon: float, bbox) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def _missing_mesh_stems(desired, meshes: Mapping[str, Path]) -> list[str]:
    return [stem for stem, _, _ in desired if stem not in meshes]


def _needs_inference_connection(run_inference: bool, desired, meshes) -> bool:
    """A fully cached exact job must not require a live Gradio service."""

    return bool(run_inference and _missing_mesh_stems(desired, meshes))


def _discover(directories: Iterable[Path], suffixes: set[str], bbox) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            parsed = _tile(path)
            if parsed is None or not _inside(parsed[1], parsed[2], bbox):
                continue
            found.setdefault(parsed[0], path.resolve())
    return found


def _grid_from_min(bbox, lat_step: float, lon_step: float):
    centres = []
    latitude = bbox[1]
    while latitude < bbox[3] - 1e-12:
        longitude = bbox[0]
        while longitude < bbox[2] - 1e-12:
            lat, lon = round(latitude, 6), round(longitude, 6)
            centres.append((f"sat_{lat:.6f}_{lon:.6f}", lat, lon))
            longitude += lon_step
        latitude += lat_step
    return centres


def _load_exact_tile_manifest(path: Path, work_dir: Path):
    """Load a frozen tile allowlist without scanning historical caches.

    Existing mesh files are admitted only when their individual manifest row
    explicitly sets ``reuse_existing_mesh``.  Merely finding a same-named OBJ
    anywhere under a supplied mesh directory is never a fallback in this mode.
    """

    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TopLevelPipelineError(f"cannot read exact tile manifest {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise TopLevelPipelineError("exact tile manifest must be a JSON object")
    rows = document.get("tiles")
    if not isinstance(rows, list) or not rows:
        raise TopLevelPipelineError("exact tile manifest must contain a non-empty tiles array")
    desired = []
    satellites: dict[str, Path] = {}
    meshes: dict[str, Path] = {}
    mesh_destinations: dict[str, Path] = {}
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise TopLevelPipelineError(f"exact tile row {index} must be an object")
        stem = str(row.get("stem", "")).strip()
        # Path(stem).stem would treat the longitude's decimal tail as a file
        # suffix, so append a known suffix before using the shared parser.
        parsed = _tile(Path(stem + ".obj"))
        if parsed is None or parsed[0] != stem:
            raise TopLevelPipelineError(
                f"exact tile row {index} has invalid canonical stem {stem!r}"
            )
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TopLevelPipelineError(
                f"exact tile row {index} requires numeric lat and lon"
            ) from exc
        if not (math.isclose(lat, parsed[1], abs_tol=5e-7) and math.isclose(lon, parsed[2], abs_tol=5e-7)):
            raise TopLevelPipelineError(
                f"exact tile row {index} coordinates disagree with stem {stem}"
            )
        if stem in seen:
            raise TopLevelPipelineError(f"duplicate exact tile stem: {stem}")
        seen.add(stem)
        satellite_raw = row.get("satellite_path")
        mesh_raw = row.get("mesh_path")
        if not isinstance(satellite_raw, str) or not isinstance(mesh_raw, str):
            raise TopLevelPipelineError(
                f"exact tile row {index} requires satellite_path and mesh_path"
            )
        satellite = Path(satellite_raw).expanduser().resolve()
        mesh = Path(mesh_raw).expanduser().resolve()
        if not _path_is_within(mesh, work_dir):
            raise TopLevelPipelineError(
                f"exact tile mesh path must stay inside work_dir: {mesh}"
            )
        desired.append((stem, lat, lon))
        mesh_destinations[stem] = mesh
        if satellite.is_file():
            satellites[stem] = satellite
        if row.get("reuse_existing_mesh") is True and _valid_obj(mesh):
            meshes[stem] = mesh
    return desired, satellites, meshes, mesh_destinations


def _top_grid(bbox, config):
    from mesh_pipeline.tile_grid import compute_satellite_grid
    from mesh_pipeline.types import GeoBBox

    bounds = GeoBBox(*bbox)
    return [
        (Path(tile.filename).stem, tile.lat, tile.lon)
        for tile in compute_satellite_grid(
            bounds, config.lat_step, config.lon_step, config.overlap_ratio
        )
    ]


def _flatten_paths(value: Any):
    if isinstance(value, (str, os.PathLike)):
        yield Path(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten_paths(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _flatten_paths(item)


def _valid_obj(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1000:
        return False
    has_vertex = has_face = False
    with path.open("r", encoding="utf-8", errors="ignore") as stream:
        for line in stream:
            has_vertex |= line.startswith("v ")
            has_face |= line.startswith("f ")
            if has_vertex and has_face:
                return True
    return False


def _convert_glb_to_obj(source: Path, destination: Path) -> Path:
    """Convert one unique Gradio GLB result into the top-level OBJ contract."""

    try:
        import numpy as np
        import trimesh
    except Exception as exc:
        raise TopLevelPipelineError(f"GLB conversion dependencies are unavailable: {exc}") from exc
    try:
        loaded = trimesh.load(str(source), force="scene", process=False)
        if isinstance(loaded, trimesh.Scene):
            mesh = loaded.to_geometry() if hasattr(loaded, "to_geometry") else loaded.dump(concatenate=True)
        else:
            mesh = loaded
        vertices = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)
        if (
            vertices.ndim != 2
            or vertices.shape[1] != 3
            or faces.ndim != 2
            or faces.shape[1] != 3
            or len(vertices) < 3
            or len(faces) < 1
            or not np.isfinite(vertices).all()
            or np.any(faces < 0)
            or np.any(faces >= len(vertices))
        ):
            raise TopLevelPipelineError(f"returned GLB has invalid triangle geometry: {source}")
        colors = None
        visual = getattr(mesh, "visual", None)
        if visual is not None:
            candidate = np.asarray(getattr(visual, "vertex_colors", []))
            if candidate.ndim == 2 and len(candidate) == len(vertices) and candidate.shape[1] >= 3:
                colors = candidate[:, :3].astype(np.float64)
                if colors.size and float(np.max(colors)) > 1.0:
                    colors /= 255.0
                colors = np.clip(colors, 0.0, 1.0)
        if colors is None:
            colors = np.full((len(vertices), 3), 0.5, dtype=np.float64)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("# Converted from the unique Sat3DGen Gradio GLB result\n")
            for vertex, color in zip(vertices, colors):
                stream.write(
                    "v {:.9g} {:.9g} {:.9g} {:.6g} {:.6g} {:.6g}\n".format(
                        vertex[0], vertex[1], vertex[2], color[0], color[1], color[2]
                    )
                )
            for face in faces:
                stream.write(f"f {face[0] + 1} {face[1] + 1} {face[2] + 1}\n")
    except TopLevelPipelineError:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise TopLevelPipelineError(f"cannot convert returned GLB {source}: {exc}") from exc
    if not _valid_obj(destination):
        destination.unlink(missing_ok=True)
        raise TopLevelPipelineError(f"GLB-converted OBJ failed validation: {source}")
    return destination


def _safe_remove_bottom_faces(vertices, faces, tile_ranges, tolerance: float = 0.5):
    """Remove only triangles whose three vertices are in a tile's bottom band.

    The top-level helper uses each face's minimum Y, which also deletes side
    walls when just one vertex touches the bottom. This wrapper keeps the same
    tile-range contract while applying the intended all-three-vertices test.
    """

    import numpy as np

    all_vertices = []
    all_faces = []
    new_ranges = []
    vertex_offset = 0
    for start, end in tile_ranges:
        mask = np.all((faces >= start) & (faces < end), axis=1)
        tile_faces = faces[mask] - start
        tile_vertices = vertices[start:end].copy()
        if len(tile_faces) == 0 or len(tile_vertices) == 0:
            continue
        bottom = float(np.min(tile_vertices[:, 1])) + tolerance
        remove = np.all(tile_vertices[tile_faces, 1] <= bottom, axis=1)
        kept = tile_faces[~remove]
        if len(kept) == 0:
            continue
        used = np.unique(kept.reshape(-1))
        remap = np.full(len(tile_vertices), -1, dtype=np.int64)
        remap[used] = np.arange(len(used), dtype=np.int64)
        compact_vertices = tile_vertices[used]
        compact_faces = remap[kept].astype(np.int32)
        all_vertices.append(compact_vertices)
        all_faces.append(compact_faces + vertex_offset)
        new_ranges.append((vertex_offset, vertex_offset + len(compact_vertices)))
        vertex_offset += len(compact_vertices)
    if not all_vertices:
        raise TopLevelPipelineError("safe bottom removal left no mesh faces")
    return np.vstack(all_vertices), np.vstack(all_faces), new_ranges


def _infer(
    client,
    handle_file,
    satellite: Path,
    destination: Path,
    resolution: int,
    sat_root: Path,
    *,
    allow_legacy_fallback: bool = True,
):
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = client.predict(
        sat_image_pil=handle_file(str(satellite.resolve())),
        mesh_resolution=resolution,
        api_name="/generate_mesh",
    )
    returned_paths = list(_flatten_paths(result))
    candidates = [path for path in returned_paths if path.suffix.lower() == ".obj"]
    if allow_legacy_fallback:
        candidates.append(sat_root / "Sat3DGen" / "results" / "gradio_demo" / "mesh.obj")
    source = next((path for path in candidates if _valid_obj(path)), None)
    temporary = destination.with_suffix(".obj.part")
    if source is None:
        glb = next(
            (
                path
                for path in returned_paths
                if path.suffix.lower() == ".glb" and path.is_file() and path.stat().st_size > 1000
            ),
            None,
        )
        if glb is not None:
            _convert_glb_to_obj(glb, temporary)
            os.replace(temporary, destination)
            return destination
        returned = [str(path) for path in returned_paths]
        raise TopLevelPipelineError(
            "/generate_mesh returned no readable OBJ or GLB; returned paths=" + repr(returned)
        )
    shutil.copy2(source, temporary)
    if not _valid_obj(temporary):
        temporary.unlink(missing_ok=True)
        raise TopLevelPipelineError(f"generated OBJ failed validation: {source}")
    os.replace(temporary, destination)
    return destination


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _validate_required_dsm(config, bbox, expected_crs: str) -> dict[str, Any]:
    """Validate the explicit DSM allowlist and coverage before GPU inference."""

    try:
        import rasterio
        from pyproj import Transformer
        from shapely.geometry import box
        from shapely.ops import unary_union
    except Exception as exc:
        raise TopLevelPipelineError(f"mandatory DSM dependencies are unavailable: {exc}") from exc

    if expected_crs.upper() != "EPSG:27700":
        raise TopLevelPipelineError("mandatory DSM currently requires EPSG:27700")
    if not config.dsm_files:
        raise TopLevelPipelineError("mandatory DSM file allowlist is empty")

    sources: list[dict[str, Any]] = []
    coverage_parts = []
    for name in config.dsm_files:
        path = (config.dsm_dir / name).resolve()
        if not path.is_file():
            raise TopLevelPipelineError(f"mandatory DSM file is missing: {path}")
        try:
            with rasterio.open(str(path)) as dataset:
                epsg = dataset.crs.to_epsg() if dataset.crs is not None else None
                if epsg != 27700:
                    raise TopLevelPipelineError(
                        f"mandatory DSM must use EPSG:27700, got {dataset.crs}: {path}"
                    )
                if dataset.count < 1 or dataset.width < 1 or dataset.height < 1:
                    raise TopLevelPipelineError(f"mandatory DSM raster is empty: {path}")
                bounds = dataset.bounds
                coverage_parts.append(box(bounds.left, bounds.bottom, bounds.right, bounds.top))
                sources.append(
                    {
                        "name": name,
                        "path": str(path),
                        "crs": "EPSG:27700",
                        "width": int(dataset.width),
                        "height": int(dataset.height),
                        "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                        "nodata": dataset.nodata,
                    }
                )
        except TopLevelPipelineError:
            raise
        except Exception as exc:
            raise TopLevelPipelineError(f"cannot read mandatory DSM {path}: {exc}") from exc

    transformer = Transformer.from_crs("EPSG:4326", expected_crs, always_xy=True)
    longitudes = [bbox[0], bbox[2], bbox[2], bbox[0]]
    latitudes = [bbox[1], bbox[1], bbox[3], bbox[3]]
    eastings, northings = transformer.transform(longitudes, latitudes)
    target = box(min(eastings), min(northings), max(eastings), max(northings))
    coverage = unary_union(coverage_parts)
    if not coverage.covers(target):
        missing_area = float(target.difference(coverage).area)
        raise TopLevelPipelineError(
            "mandatory DSM does not completely cover the selected footprint context; "
            f"missing projected area={missing_area:.3f} m2"
        )
    return {
        "required": True,
        "status": "SOURCE_COVERAGE_READY",
        "crs": expected_crs.upper(),
        "files": sources,
        "selection_bounds_epsg27700": list(target.bounds),
        "source_coverage_ratio": 1.0,
    }


def _parser():
    parser = argparse.ArgumentParser(
        description="myProject compatibility driver for Sat3DGen/mesh_pipeline top-level modules"
    )
    parser.add_argument("--sat3dgen-root", type=Path, required=True)
    parser.add_argument("--bbox", type=float, nargs=4, required=True,
                        metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--name", default="region")
    parser.add_argument("--satellite-dir", action="append", type=Path, default=[])
    parser.add_argument("--mesh-dir", action="append", type=Path, default=[])
    parser.add_argument("--osm-dir", type=Path, default=None)
    parser.add_argument("--dsm-dir", type=Path, default=None)
    parser.add_argument("--dsm-file", action="append", default=[])
    parser.add_argument("--dsm-crs", default="EPSG:27700")
    parser.add_argument("--tile-source", choices=("discovered", "data_builder_grid", "top_grid", "exact_manifest"),
                        default="discovered")
    parser.add_argument("--exact-tile-manifest", type=Path, default=None)
    parser.add_argument("--pipeline-contract-version", default=None)
    parser.add_argument("--lat-step", type=float, default=None)
    parser.add_argument("--lon-step", type=float, default=None)
    parser.add_argument("--overlap-ratio", type=float, default=None)
    parser.add_argument("--crop-ratio", type=float, default=None)
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--run-inference", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--osm-prealign", action="store_true")
    parser.add_argument("--apply-dsm", action="store_true")
    parser.add_argument("--keep-bottom", action="store_true")
    parser.add_argument("--no-stitch", action="store_true")
    parser.add_argument("--mesh-resolution", type=int, default=256)
    parser.add_argument("--zoom", type=int, default=20)
    parser.add_argument("--tile-size", type=int, default=640)
    parser.add_argument("--gradio-url", default="http://localhost:7860")
    parser.add_argument("--plan-only", action="store_true")
    return parser


def run(args) -> dict[str, Any]:
    sat_root = args.sat3dgen_root.resolve()
    if args.pipeline_contract_version is not None:
        if args.pipeline_contract_version != "osm-prealign-v1":
            raise TopLevelPipelineError(
                f"unsupported pipeline contract: {args.pipeline_contract_version}"
            )
        if not args.osm_prealign or not args.apply_dsm or args.osm_dir is None:
            raise TopLevelPipelineError(
                "osm-prealign-v1 requires --osm-prealign, --osm-dir and --apply-dsm"
            )
    if not (sat_root / "mesh_pipeline" / "pipeline.py").is_file():
        raise TopLevelPipelineError(f"top-level mesh_pipeline not found under {sat_root}")
    forbidden = sat_root / "mesh_pipeline" / "mesh_generate_merge_pipeline"
    sys.path.insert(0, str(sat_root))

    # These imports intentionally target only Sat3DGen/mesh_pipeline/*.py.
    from mesh_pipeline.config import Config
    from mesh_pipeline.downloader import download_satellite_tile
    from mesh_pipeline.export import export_model
    from mesh_pipeline.mesh_merging import (
        load_and_merge_tiles,
        stitch_tiles,
    )

    top_level = (sat_root / "mesh_pipeline").resolve()
    checked_modules = (
        "mesh_pipeline",
        "mesh_pipeline.config",
        "mesh_pipeline.downloader",
        "mesh_pipeline.export",
        "mesh_pipeline.mesh_merging",
    )
    for module_name in checked_modules:
        loaded = Path(sys.modules[module_name].__file__).resolve()
        try:
            loaded.relative_to(top_level)
        except ValueError as exc:
            raise TopLevelPipelineError(
                f"refusing mesh pipeline import outside the configured top-level directory: {loaded}"
            ) from exc
        if forbidden == loaded or forbidden in loaded.parents:
            raise TopLevelPipelineError(f"refusing nested mesh pipeline import: {loaded}")

    bbox = tuple(float(value) for value in args.bbox)
    if not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
        raise TopLevelPipelineError("invalid WGS84 bbox")
    work_dir = args.work_dir.resolve()
    config = Config(work_dir=work_dir)
    if args.lat_step is not None:
        config.lat_step = args.lat_step
    if args.lon_step is not None:
        config.lon_step = args.lon_step
    if args.overlap_ratio is not None:
        config.overlap_ratio = args.overlap_ratio
    if args.crop_ratio is not None:
        if not 0 <= args.crop_ratio < 0.5:
            raise TopLevelPipelineError("crop_ratio must be in [0, 0.5)")
        config.crop_ratio = args.crop_ratio
    config.zoom = args.zoom
    config.img_size = args.tile_size
    config.mesh_resolution = args.mesh_resolution
    config.gradio_api_url = args.gradio_url
    if args.osm_dir is not None:
        config.osm_data_dir = args.osm_dir.resolve()
    if args.dsm_dir is not None:
        config.dsm_dir = args.dsm_dir.resolve()
    if args.dsm_file:
        config.dsm_files = list(args.dsm_file)

    satellite_dirs = [path.resolve() for path in args.satellite_dir]
    mesh_dirs = [path.resolve() for path in args.mesh_dir] + [config.mesh_dir.resolve()]
    mesh_destinations: dict[str, Path] = {}
    ignored_extra_meshes: list[str] = []
    if args.tile_source == "exact_manifest":
        if args.exact_tile_manifest is None:
            raise TopLevelPipelineError("exact_manifest tile source requires --exact-tile-manifest")
        if args.download_missing:
            raise TopLevelPipelineError(
                "exact_manifest does not download; the caller must validate every declared PNG first"
            )
        desired, satellites, meshes, mesh_destinations = _load_exact_tile_manifest(
            args.exact_tile_manifest.resolve(), work_dir
        )
        allowlist = {stem for stem, _, _ in desired}
        explicitly_reused = {path.resolve() for path in meshes.values()}
        if config.mesh_dir.is_dir():
            for extra in config.mesh_dir.rglob("*.obj"):
                parsed = _tile(extra)
                if (
                    parsed is None
                    or parsed[0] not in allowlist
                    or extra.resolve() not in explicitly_reused
                ):
                    ignored_extra_meshes.append(str(extra.resolve()))
    else:
        if args.exact_tile_manifest is not None:
            raise TopLevelPipelineError(
                "--exact-tile-manifest requires --tile-source exact_manifest"
            )
        satellites = _discover(satellite_dirs + [config.sat_dir], _IMAGE_SUFFIXES, bbox)
        meshes = _discover(mesh_dirs, {".obj"}, bbox)

    if args.tile_source == "data_builder_grid":
        desired = _grid_from_min(bbox, config.lat_step, config.lon_step)
    elif args.tile_source == "top_grid":
        desired = _top_grid(bbox, config)
    elif args.tile_source == "discovered":
        source_stems = sorted(set(satellites) | set(meshes))
        desired = []
        for stem in source_stems:
            match = _TILE_RE.fullmatch(stem)
            desired.append((stem, float(match.group(1)), float(match.group(2))))
    if not desired:
        raise TopLevelPipelineError("no tiles were discovered or planned for the bbox")

    dsm_report = None
    if args.apply_dsm:
        if args.dsm_dir is None or not args.dsm_file:
            raise TopLevelPipelineError(
                "mandatory DSM requires --dsm-dir and an explicit --dsm-file allowlist"
            )
        if args.osm_dir is None or not (args.osm_dir / "building.geojson").is_file():
            raise TopLevelPipelineError(
                "mandatory DSM semantic correction requires --osm-dir/building.geojson"
            )
        dsm_report = _validate_required_dsm(config, bbox, args.dsm_crs)

    report_path = work_dir / "top_level_pipeline_manifest.json"
    report: dict[str, Any] = {
        "schema_version": 1,
        "pipeline": "Sat3DGen/mesh_pipeline top-level",
        "forbidden_nested_directory": str(forbidden),
        "bbox_wgs84": list(bbox),
        "work_dir": str(work_dir),
        "tile_source": args.tile_source,
        "pipeline_contract_version": args.pipeline_contract_version,
        "osm_prealign": bool(args.osm_prealign),
        "apply_dsm": bool(args.apply_dsm),
        "merge_stage_order": list(MERGE_STAGE_ORDER),
        "coordinate_parameters": {
            "lat_step": config.lat_step,
            "lon_step": config.lon_step,
            "overlap_ratio": config.overlap_ratio,
            "axes": {"x": "east", "y": "up", "z": "south"},
            "units": "m",
        },
        "mesh_resolution": int(config.mesh_resolution),
        "desired_tile_count": len(desired),
        "satellite_sources": {key: str(value) for key, value in satellites.items()},
        "mesh_sources_before_run": {key: str(value) for key, value in meshes.items()},
        "exact_tile_manifest": str(args.exact_tile_manifest.resolve()) if args.exact_tile_manifest else None,
        "ignored_extra_meshes": ignored_extra_meshes,
        "download_failures": [],
        "inference_failures": [],
        "dsm": dsm_report,
        "status": "planned" if args.plan_only else "running",
    }
    work_dir.mkdir(parents=True, exist_ok=True)
    _write_report(report_path, report)
    if args.plan_only:
        report["missing_satellite"] = [stem for stem, _, _ in desired if stem not in satellites]
        report["missing_mesh"] = [stem for stem, _, _ in desired if stem not in meshes]
        _write_report(report_path, report)
        return report

    if args.download_missing:
        api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        if not api_key:
            raise TopLevelPipelineError(
                "download_missing requires GOOGLE_MAPS_API_KEY in the process environment"
            )
        config.sat_dir.mkdir(parents=True, exist_ok=True)
        for stem, lat, lon in desired:
            if stem in satellites:
                continue
            ok = download_satellite_tile(
                lat, lon, api_key, config.sat_dir, config.zoom, config.img_size
            )
            path = config.sat_dir / f"{stem}.png"
            if ok and path.is_file():
                satellites[stem] = path
            else:
                report["download_failures"].append(
                    {"tile": stem, "message": "top-level download_satellite_tile returned false"}
                )
                _write_report(report_path, report)

    missing_before_inference = _missing_mesh_stems(desired, meshes)
    report["missing_mesh_before_inference"] = missing_before_inference
    if _needs_inference_connection(args.run_inference, desired, meshes):
        try:
            from gradio_client import Client, handle_file
            try:
                client = Client(config.gradio_api_url, verbose=False)
            except TypeError:
                client = Client(config.gradio_api_url)
        except Exception as exc:
            raise TopLevelPipelineError(
                f"cannot connect/import Gradio client for {config.gradio_api_url}: {exc}"
            ) from exc
        for stem, _, _ in desired:
            if stem in meshes:
                continue
            satellite = satellites.get(stem)
            if satellite is None:
                report["inference_failures"].append(
                    {"tile": stem, "message": "satellite image missing"}
                )
                continue
            destination = mesh_destinations.get(stem, config.mesh_dir / stem / f"{stem}.obj")
            try:
                meshes[stem] = _infer(
                    client, handle_file, satellite, destination,
                    config.mesh_resolution, sat_root,
                    allow_legacy_fallback=args.tile_source != "exact_manifest",
                )
            except Exception as exc:
                report["inference_failures"].append(
                    {"tile": stem, "type": type(exc).__name__, "message": str(exc)}
                )
            _write_report(report_path, report)
    elif args.run_inference:
        report["inference_skipped_reason"] = "all exact tile meshes already validated"
        _write_report(report_path, report)

    selected = [meshes[stem] for stem, _, _ in desired if stem in meshes]
    missing_mesh = [stem for stem, _, _ in desired if stem not in meshes]
    report["missing_satellite"] = [stem for stem, _, _ in desired if stem not in satellites]
    report["missing_mesh"] = missing_mesh
    if missing_mesh and not args.allow_partial:
        report["status"] = "incomplete"
        _write_report(report_path, report)
        raise TopLevelPipelineError(
            f"{len(missing_mesh)}/{len(desired)} tile meshes are missing; "
            f"see {report_path} or explicitly use allow_partial"
        )
    if not selected:
        raise TopLevelPipelineError("no mesh tiles are available to merge")

    osm_loader = None
    if args.osm_prealign:
        from mesh_pipeline.osm_loader import OSMLoader
        osm_loader = OSMLoader(config)
    vertices, faces, origin_lat, origin_lon, tile_ranges = load_and_merge_tiles(
        selected, config, osm_loader=osm_loader, remove_bottom=False
    )
    if not args.keep_bottom:
        vertices, faces, tile_ranges = _safe_remove_bottom_faces(
            vertices, faces, tile_ranges
        )
    if len(selected) > 1 and not args.no_stitch:
        vertices, faces = stitch_tiles(vertices, faces, tile_ranges, config.stitch_distance)
    if args.apply_dsm:
        import numpy as np
        from pyproj import Transformer
        from mesh_pipeline.dsm_loader import DSMLoader
        from mesh_pipeline.height_correction import semantic_height_correction
        from mesh_pipeline.utils import world_to_latlon_batch
        if osm_loader is None:
            from mesh_pipeline.osm_loader import OSMLoader
            osm_loader = OSMLoader(config)
        dsm_loader = DSMLoader(config, apply_gaussian_filter=True, sigma=config.dsm_gaussian_sigma)
        if dsm_loader.is_empty:
            raise TopLevelPipelineError(f"apply_dsm requested but no DSM loaded from {config.dsm_dir}")
        lats, lons = world_to_latlon_batch(
            vertices[:, 0], vertices[:, 2], origin_lat, origin_lon
        )
        transformer = Transformer.from_crs("EPSG:4326", args.dsm_crs, always_xy=True)
        eastings, northings = transformer.transform(lons, lats)
        sampled_heights = dsm_loader.query_heights_batch(eastings, northings)
        valid_dsm = np.isfinite(sampled_heights)
        valid_count = int(valid_dsm.sum())
        total_count = int(len(sampled_heights))
        vertex_coverage = valid_count / total_count if total_count else 0.0
        report["dsm"].update(
            {
                "mesh_vertex_count": total_count,
                "valid_height_count": valid_count,
                "mesh_vertex_coverage_ratio": vertex_coverage,
            }
        )
        _write_report(report_path, report)
        if total_count < 100 or valid_count != total_count:
            raise TopLevelPipelineError(
                "mandatory DSM has invalid/NoData coverage for merged mesh vertices: "
                f"{valid_count}/{total_count}"
            )
        before_y = vertices[:, 1].copy()
        vertices, faces = semantic_height_correction(
            vertices, faces, osm_loader, dsm_loader, origin_lat, origin_lon, config
        )
        if not np.isfinite(vertices[:, :3]).all():
            raise TopLevelPipelineError("mandatory DSM correction produced non-finite vertices")
        report["dsm"].update(
            {
                "status": "APPLIED",
                "corrected_vertex_count": int(np.count_nonzero(np.abs(vertices[:, 1] - before_y) > 1e-9)),
                "maximum_vertical_delta_m": float(np.max(np.abs(vertices[:, 1] - before_y))),
            }
        )
        _write_report(report_path, report)
    output_obj, _ = export_model(
        vertices, faces, config.output_dir, f"{args.name}_scene", export_ply=False
    )
    report.update(
        {
            "status": "ok" if not missing_mesh else "partial",
            "origin_lat": origin_lat,
            "origin_lon": origin_lon,
            "selected_meshes": [str(path) for path in selected],
            "selected_mesh_count": len(selected),
            "output_scene_obj": str(output_obj.resolve()),
            "vertex_count": int(len(vertices)),
            "face_count": int(len(faces)),
            "finished_local_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    _write_report(report_path, report)
    print(f"scene_obj: {output_obj.resolve()}")
    print(f"manifest: {report_path.resolve()}")
    return report


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    report_path = args.work_dir.resolve() / "top_level_pipeline_manifest.json"
    try:
        report = run(args)
        if args.plan_only:
            print(f"manifest: {report_path}")
        print(json.dumps({
            "status": report["status"],
            "desired_tile_count": report["desired_tile_count"],
            "selected_mesh_count": report.get("selected_mesh_count", 0),
            "report": str(report_path),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        failure = {
            "status": "error",
            "type": type(exc).__name__,
            "message": str(exc),
            "report": str(report_path),
        }
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
            existing.update(failure)
            _write_report(report_path, existing)
        except Exception:
            pass
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
