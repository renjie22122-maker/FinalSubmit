from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .config import ProjectConfig
from .footprints import export_geojson_footprints
from .geo import BBox, LocalBBox, LocalFrame
from .mesh_pipeline import (
    GeoBBox,
    ObjInspection,
    TopLevelPipelineRequest,
    derive_tile_origin,
    inspect_obj,
    run_top_level_pipeline,
)
from .panoramas import prepare_panoramas


class WorkspaceError(RuntimeError):
    """Raised when a complete ChordAtlas workspace cannot be produced."""


@dataclass(frozen=True)
class ResolvedMesh:
    path: Path
    origin_lat: float
    origin_lon: float
    inspection: ObjInspection
    ground_reference_m: float
    generation: Mapping[str, Any] | None = None


def _config_path(config: ProjectConfig, raw: str | os.PathLike[str]) -> Path:
    value = Path(os.path.expandvars(os.path.expanduser(os.fspath(raw))))
    if not value.is_absolute():
        value = config.source.parent / value
    return value.resolve()


def _optional_config_path(config: ProjectConfig, value: Any) -> Path | None:
    if value in (None, ""):
        return None
    return _config_path(config, os.fspath(value))


def _config_paths(config: ProjectConfig, value: Any) -> tuple[Path, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, (str, os.PathLike)):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise WorkspaceError("pipeline source directories must be a path or a list of paths")
    return tuple(_config_path(config, os.fspath(item)) for item in values)


def _pipeline_request(config: ProjectConfig) -> TopLevelPipelineRequest:
    mesh = config.mesh
    work_dir = _config_path(config, str(mesh.get("work_dir", f"../../projects/{config.project_id}/_mesh_job")))
    raw_bbox = mesh.get("pipeline_bbox_wgs84", config.target_bbox)
    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
        raise WorkspaceError("mesh.pipeline_bbox_wgs84 must be [min_lon, min_lat, max_lon, max_lat]")
    try:
        bbox = GeoBBox(*(float(value) for value in raw_bbox))
    except (TypeError, ValueError) as exc:
        raise WorkspaceError("mesh.pipeline_bbox_wgs84 contains an invalid coordinate") from exc
    chordatlas_root = config.external_paths["chordatlas_root"]
    return TopLevelPipelineRequest(
        bbox=bbox,
        work_dir=work_dir,
        sat3dgen_root=config.external_paths["sat3dgen_root"],
        driver_path=chordatlas_root / "bridge" / "top_level_mesh_driver.py",
        name=str(mesh.get("output_name", config.project_id)),
        conda_environment="sat3dgen",
        conda_executable=str(config.external_paths["conda_executable"]),
        satellite_dirs=_config_paths(config, mesh.get("satellite_dirs")),
        mesh_dirs=_config_paths(config, mesh.get("existing_mesh_dirs")),
        osm_dir=_optional_config_path(config, mesh.get("osm_dir")),
        dsm_dir=_optional_config_path(config, mesh.get("dsm_dir")),
        tile_source=str(mesh.get("tile_source", "discovered")),
        lat_step=float(mesh["lat_step"]) if mesh.get("lat_step") is not None else None,
        lon_step=float(mesh["lon_step"]) if mesh.get("lon_step") is not None else None,
        overlap_ratio=float(mesh["overlap_ratio"]) if mesh.get("overlap_ratio") is not None else None,
        download_missing=bool(mesh.get("download_missing", False)),
        run_inference=bool(mesh.get("run_inference", False)),
        allow_partial=bool(mesh.get("allow_partial", False)),
        osm_prealign=bool(mesh.get("osm_prealign", False)),
        apply_dsm=bool(mesh.get("apply_dsm", False)),
        keep_bottom=bool(mesh.get("keep_bottom", False)),
        no_stitch=bool(mesh.get("no_stitch", False)),
        zoom=int(mesh.get("zoom", 20)),
        tile_size=int(mesh.get("tile_size", 640)),
        crop_ratio=float(mesh["crop_ratio"]) if mesh.get("crop_ratio") is not None else None,
        mesh_resolution=int(mesh.get("mesh_resolution", 256)),
        gradio_url=str(mesh.get("gradio_url", "http://localhost:7860")),
    )


def mesh_plan(config: ProjectConfig) -> dict[str, Any]:
    mode = config.mesh.get("mode", "existing")
    if mode == "on_demand":
        return {
            "mode": "on_demand",
            "initial_minimesh": False,
            "selection_action": "build-selection",
        }
    if mode == "existing":
        return {
            "mode": "existing",
            "source_obj": str(config.mesh_source),
            "exists": bool(config.mesh_source and config.mesh_source.is_file()),
        }
    result = run_top_level_pipeline(_pipeline_request(config), dry_run=True)
    return {"mode": "generate", **result.to_dict()}


def run_configured_mesh(config: ProjectConfig, *, execute: bool, timeout: float | None = None):
    if config.mesh.get("mode") != "generate":
        raise WorkspaceError("run-mesh is only valid when mesh.mode is generate")
    return run_top_level_pipeline(
        _pipeline_request(config),
        dry_run=not execute,
        timeout=timeout,
        check=execute,
    )


def scan_configured_mesh(config: ProjectConfig, *, timeout: float | None = None):
    """Execute the driver's plan-only input scan without network or inference."""

    if config.mesh.get("mode") != "generate":
        raise WorkspaceError("run-mesh --scan-inputs is only valid when mesh.mode is generate")
    request = replace(_pipeline_request(config), plan_only=True)
    return run_top_level_pipeline(request, dry_run=False, timeout=timeout, check=True)


def _generated_scene(config: ProjectConfig, generation_result=None) -> Path:
    request = _pipeline_request(config)
    if generation_result is not None:
        candidates = [
            path for key, path in generation_result.outputs if path.name.lower().endswith("_scene.obj")
        ]
        if candidates:
            return candidates[0].resolve()
    expected = request.work_dir / "final" / f"{request.name}_scene.obj"
    if expected.is_file():
        return expected.resolve()
    scenes = sorted((request.work_dir / "final").glob("*_scene.obj")) if (request.work_dir / "final").is_dir() else []
    if len(scenes) == 1:
        return scenes[0].resolve()
    raise WorkspaceError(
        "generated scene OBJ was not found; run `myproject run-mesh --execute` first "
        f"(expected {expected})"
    )


def _origin(config: ProjectConfig, mesh_path: Path) -> tuple[float, float]:
    origin = config.mesh.get("origin", {})
    mode = origin.get("mode", "explicit") if isinstance(origin, dict) else "explicit"
    if mode == "explicit":
        try:
            latitude = float(origin["lat"])
            longitude = float(origin["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkspaceError("mesh.origin explicit mode needs numeric lat and lon") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise WorkspaceError("mesh origin must be finite")
        return latitude, longitude
    if mode == "top_level_manifest":
        report_path = _pipeline_request(config).work_dir / "top_level_pipeline_manifest.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            latitude = float(report["origin_lat"])
            longitude = float(report["origin_lon"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise WorkspaceError(f"cannot read top-level mesh origin from {report_path}: {exc}") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            raise WorkspaceError(f"top-level mesh manifest has a non-finite origin: {report_path}")
        declared_scene = report.get("output_scene_obj")
        if declared_scene and Path(declared_scene).resolve() != mesh_path.resolve():
            raise WorkspaceError(
                "generated mesh and top-level manifest do not match: "
                f"{mesh_path} != {declared_scene}"
            )
        return latitude, longitude
    if mode != "tile_centres":
        raise WorkspaceError("mesh.origin.mode must be explicit, top_level_manifest or tile_centres")

    request = _pipeline_request(config)
    mesh_root = request.work_dir / "meshes"
    stems = []
    if mesh_root.is_dir():
        stems.extend(path.name for path in mesh_root.iterdir() if path.is_dir() and path.name.startswith("sat_"))
        if not stems:
            stems.extend(path.name for path in mesh_root.rglob("sat_*.obj"))
    if not stems:
        raise WorkspaceError(f"cannot derive mesh origin: no sat_<lat>_<lon> tiles under {mesh_root}")
    tile_origin = derive_tile_origin(stems)
    return tile_origin.origin_latitude, tile_origin.origin_longitude


def _ground_reference(config: ProjectConfig, inspection: ObjInspection) -> float:
    setting = config.mesh.get("ground_reference", {})
    mode = setting.get("mode", "percentile") if isinstance(setting, dict) else "percentile"
    if mode == "value":
        try:
            value = float(setting["value_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkspaceError("ground_reference value mode needs value_m") from exc
        if not math.isfinite(value):
            raise WorkspaceError("ground_reference.value_m must be finite")
        return value
    if mode == "none":
        return 0.0
    if mode != "percentile":
        raise WorkspaceError("ground_reference.mode must be percentile, value or none")
    percentile = float(setting.get("percentile", 2.0))
    try:
        return inspection.percentile(percentile)
    except KeyError as exc:
        raise WorkspaceError(f"Y percentile {percentile} was not inspected") from exc


def resolve_mesh(
    config: ProjectConfig,
    *,
    run_generation: bool = False,
    generation_timeout: float | None = None,
) -> ResolvedMesh:
    generation_result = None
    if config.mesh.get("mode", "existing") == "generate":
        if run_generation:
            generation_result = run_configured_mesh(config, execute=True, timeout=generation_timeout)
        mesh_path = _generated_scene(config, generation_result)
    else:
        if config.mesh_source is None or not config.mesh_source.is_file():
            raise WorkspaceError(f"mesh source OBJ not found: {config.mesh_source}")
        mesh_path = config.mesh_source

    ground_setting = config.mesh.get("ground_reference", {})
    requested_percentile = float(ground_setting.get("percentile", 2.0)) if isinstance(ground_setting, dict) else 2.0
    inspection = inspect_obj(mesh_path, y_percentiles=(requested_percentile, 50.0, 98.0))
    if inspection.vertex_count == 0 or inspection.face_count == 0 or inspection.bounds is None:
        raise WorkspaceError(f"mesh OBJ has no usable geometry: {mesh_path}")
    origin_lat, origin_lon = _origin(config, mesh_path)
    ground = _ground_reference(config, inspection)
    return ResolvedMesh(
        path=mesh_path,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        inspection=inspection,
        ground_reference_m=ground,
        generation=generation_result.to_dict() if generation_result is not None else None,
    )


def _intersection(first: LocalBBox, second: LocalBBox) -> LocalBBox | None:
    min_x, min_z = max(first.min_x, second.min_x), max(first.min_z, second.min_z)
    max_x, max_z = min(first.max_x, second.max_x), min(first.max_z, second.max_z)
    if min_x >= max_x or min_z >= max_z:
        return None
    return LocalBBox(min_x, min_z, max_x, max_z)


def _coverage(mesh_bounds: LocalBBox, target_bounds: LocalBBox) -> dict[str, float]:
    overlap = _intersection(mesh_bounds, target_bounds)
    target_area = target_bounds.width * target_bounds.depth
    overlap_area = 0.0 if overlap is None else overlap.width * overlap.depth
    return {
        "target_area_m2": target_area,
        "mesh_aabb_overlap_m2": overlap_area,
        "mesh_aabb_target_ratio": overlap_area / target_area if target_area else 0.0,
    }


def _run_logged(command: list[str], cwd: Path, log_path: Path, timeout: float) -> None:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_path.write_text(f"command: {subprocess.list2cmdline(command)}\nerror: {exc}\n", encoding="utf-8")
        raise WorkspaceError(f"command failed to start or timed out; see {log_path}: {exc}") from exc
    log_path.write_text(
        "command: " + subprocess.list2cmdline(command) + "\n"
        + f"duration_seconds: {time.perf_counter() - started:.3f}\n"
        + f"exit_code: {completed.returncode}\n\n[stdout]\n{completed.stdout}\n[stderr]\n{completed.stderr}",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no diagnostic output"
        raise WorkspaceError(f"command exited {completed.returncode}; see {log_path}: {detail}")


def _copy_example_assets(chordatlas_root: Path, workspace: Path) -> list[str]:
    copied: list[str] = []
    for name in (
        "brick.jpg",
        "brick_norm.jpg",
        "tile.jpg",
        "tile_norm.jpg",
        "chordatlas_example_inputs_1.zip",
    ):
        source = chordatlas_root / name
        if source.is_file():
            shutil.copy2(source, workspace / name)
            copied.append(name)
    source_inputs = chordatlas_root / "network_inputs"
    if source_inputs.is_dir():
        shutil.copytree(source_inputs, workspace / "network_inputs", dirs_exist_ok=True)
        copied.append("network_inputs")
    return copied


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _promote_workspace(staging: Path, target: Path, *, force: bool) -> Path | None:
    backup = None
    if target.exists():
        marker = target / "manifest.json"
        if not force:
            raise WorkspaceError(f"workspace already exists: {target}; use --force to retain it as a backup")
        try:
            old_manifest = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceError(
                f"refusing to replace an unrecognised directory without a myProject manifest: {target}"
            ) from exc
        if old_manifest.get("generated_by") != "myProject.bridge":
            raise WorkspaceError(f"refusing to replace a non-myProject workspace: {target}")
        backup = target.with_name(target.name + ".backup-" + time.strftime("%Y%m%d-%H%M%S"))
        target.rename(backup)
    try:
        staging.rename(target)
    except BaseException:
        if backup is not None and not target.exists():
            backup.rename(target)
        raise
    return backup


def build_workspace(
    config: ProjectConfig,
    *,
    force: bool = False,
    run_generation: bool = False,
    generation_timeout: float | None = None,
) -> dict[str, Any]:
    on_demand = config.mesh.get("mode") == "on_demand"
    target_geo = BBox.from_sequence(config.target_bbox)
    mesh: ResolvedMesh | None
    mesh_local: LocalBBox | None
    if on_demand:
        if run_generation:
            raise WorkspaceError("--run-mesh is not valid when mesh.mode is on_demand")
        mesh = None
        mesh_local = None
        origin = config.mesh.get("origin", {})
        if isinstance(origin, dict) and origin.get("mode") == "explicit":
            try:
                frame = LocalFrame(float(origin["lat"]), float(origin["lon"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise WorkspaceError("on_demand explicit origin needs numeric lat and lon") from exc
        else:
            frame = LocalFrame.from_bbox(target_geo)
        target_local = target_geo.to_local(frame)
        selection_bounds = target_local
    else:
        mesh = resolve_mesh(
            config, run_generation=run_generation, generation_timeout=generation_timeout
        )
        bounds = mesh.inspection.bounds
        assert bounds is not None
        mesh_local = LocalBBox(bounds.minimum[0], bounds.minimum[2], bounds.maximum[0], bounds.maximum[2])
        frame = LocalFrame(mesh.origin_lat, mesh.origin_lon)
        target_local = target_geo.to_local(frame)
        selection_bounds = _intersection(mesh_local, target_local)
        if selection_bounds is None:
            raise WorkspaceError("mesh X/Z bounds do not overlap the configured target geographic range")

    output_root = config.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".{config.project_id}.building-{uuid.uuid4().hex[:8]}"
    staging.mkdir()
    (staging / "logs").mkdir()
    (staging / "features").mkdir()
    (staging / "exports").mkdir()

    # The GIS layer is a selection catalogue, not a declaration that a mesh
    # already exists. Keep complete intersecting OSM rings and never constrain
    # them to the currently available mesh extent. Generated 3D blocks apply a
    # separate, stricter completeness gate before publication.
    footprint_policy = str(config.footprints.get("selection_policy", "intersects"))
    footprint_result = export_geojson_footprints(
        config.footprint_source,
        staging / "footprints.obj",
        frame,
        target_local,
        clip=bool(config.footprints.get("clip_to_mesh_bounds", False)),
        selection_policy=footprint_policy,
    )
    if not footprint_result.footprints:
        raise WorkspaceError("no valid Polygon/MultiPolygon building footprints overlap the mesh")

    chordatlas_root = config.external_paths["chordatlas_root"]
    jar = chordatlas_root / "target" / "chordatlas-0.0.1.jar"
    if not jar.is_file():
        raise WorkspaceError(f"ChordAtlas JAR not found: {jar}; run bridge/scripts/build_chordatlas.ps1")
    java = str(config.chordatlas.get("java_executable", "java"))
    heap_gb = int(config.chordatlas.get("heap_gb", 12))
    mini_arg = "-"
    model_files: list[Path] = []
    if mesh is not None:
        mini_dir = staging / "minimesh"
        _run_logged(
            [
                java,
                f"-Xmx{heap_gb}g",
                "-cp",
                str(jar),
                "org.twak.readTrace.MiniTransformCLI",
                str(mesh.path),
                str(mini_dir),
                "Y_UP",
                "0",
                format(-mesh.ground_reference_m, ".15g"),
                "0",
            ],
            chordatlas_root,
            staging / "logs" / "minimesh-conversion.log",
            float(config.mesh.get("minimesh_timeout_seconds", 7200)),
        )
        index = mini_dir / "index.xml"
        model_files = list(mini_dir.glob("*/model.obj"))
        if not index.is_file() or not model_files:
            raise WorkspaceError("mini-mesh conversion did not produce index.xml and model tiles")
        mini_arg = "minimesh"

    panorama_report = None
    pano_arg = "-"
    if config.panoramas.get("enabled", False):
        assert config.panorama_manifest is not None
        panorama_report = prepare_panoramas(config.panorama_manifest, staging / "panos")
        if panorama_report.get("status") != "ok" or panorama_report.get("errors"):
            raise WorkspaceError(
                "panorama manifest was rejected; see "
                + str(staging / "panos" / "panorama_import_report.json")
            )
        pano_arg = "panos"

    copied_assets = []
    if bool(config.chordatlas.get("copy_example_assets", True)):
        copied_assets = _copy_example_assets(chordatlas_root, staging)

    conda = config.external_paths["conda_executable"]
    _run_logged(
        [
            java,
            "-cp",
            str(jar),
            "org.twak.tweed.gen.WorkspaceCLI",
            str(staging),
            "footprints.obj",
            mini_arg,
            pano_arg,
            format(frame.origin_lat, ".15g"),
            format(frame.origin_lon, ".15g"),
            str(config.external_paths["frankengan_root"]),
            str(config.external_paths["facade_pytorch_root"]),
            str(conda),
            "sat3dgen",
        ],
        chordatlas_root,
        staging / "logs" / "workspace-descriptor.log",
        120.0,
    )
    if not (staging / "tweed.xml").is_file():
        raise WorkspaceError("WorkspaceCLI did not create tweed.xml")

    material_status = "on-demand-not-generated" if mesh is None else "vertex-colours-or-untextured"
    material_warnings = []
    if mesh is None:
        material_warnings.append(
            "No initial MiniMesh was requested. OSM footprints remain selectable; "
            "a verified per-selection mesh is generated from the GUI on demand."
        )
    elif mesh.inspection.material_libraries:
        missing = [name for name in mesh.inspection.material_libraries if not (mesh.path.parent / name).is_file()]
        material_status = "source-mtl-present" if not missing else "source-mtl-incomplete"
        if missing:
            material_warnings.append("missing source material libraries: " + ", ".join(missing))
    else:
        material_warnings.append(
            "Sat3DGen top-level mesh_pipeline scene has no UV/MTL texture; geometry remains usable and ChordAtlas can texture the reconstructed model later."
        )

    coverage: dict[str, Any]
    if mesh_local is None:
        coverage = {
            "mode": "on_demand",
            "target_area_m2": target_local.width * target_local.depth,
            "mesh_aabb_overlap_m2": 0.0,
            "mesh_aabb_target_ratio": 0.0,
        }
    else:
        coverage = _coverage(mesh_local, target_local)
    warnings = material_warnings[:]
    if mesh is not None and coverage["mesh_aabb_target_ratio"] < 0.999:
        warnings.append(
            f"mesh covers only {coverage['mesh_aabb_target_ratio']:.2%} of the target bbox by AABB; this workspace is partial"
        )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generated_by": "myProject.bridge",
        "project_id": config.project_id,
        "config": str(config.source),
        "created_local_time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "area": {
            "target_bbox_wgs84": list(config.target_bbox),
            "fetch_bbox_wgs84": list(config.fetch_bbox),
            "coverage": coverage,
        },
        "frame": {
            "origin_lat": frame.origin_lat,
            "origin_lon": frame.origin_lon,
            "units": "m",
            "axes": {"x": "east", "y": "up", "z": "south"},
            "vertical_offset_m": -mesh.ground_reference_m if mesh is not None else 0.0,
            "ground_reference_m": mesh.ground_reference_m if mesh is not None else 0.0,
        },
        "footprints": {
            "source": str(config.footprint_source),
            "mode": config.footprints.get("mode", "osm"),
            "selection_policy": footprint_policy,
            "stats": footprint_result.stats.as_dict(),
            "bounds_local": list(target_local.as_tuple()),
            "output": "footprints.obj",
        },
        "mesh": {
            "mode": config.mesh.get("mode", "existing"),
            "source": str(mesh.path) if mesh is not None else None,
            "inspection": mesh.inspection.to_dict() if mesh is not None else None,
            "minimesh_tiles": len(model_files),
            "material_status": material_status,
            "generation": mesh.generation if mesh is not None else None,
            "on_demand_options": dict(config.mesh) if mesh is None else None,
        },
        "panoramas": panorama_report or {"status": "disabled", "output_files": []},
        "chordatlas": {
            "jar": str(jar),
            "tweed_xml": "tweed.xml",
            "example_assets": copied_assets,
            "conda_environment": "sat3dgen",
        },
        "warnings": warnings,
    }
    _write_json(staging / "manifest.json", manifest)
    backup = _promote_workspace(staging, config.workspace, force=force)
    manifest["workspace"] = str(config.workspace)
    manifest["previous_workspace_backup"] = str(backup) if backup else None
    _write_json(config.workspace / "manifest.json", manifest)
    return manifest


__all__ = [
    "ResolvedMesh",
    "WorkspaceError",
    "build_workspace",
    "mesh_plan",
    "resolve_mesh",
    "run_configured_mesh",
    "scan_configured_mesh",
]
