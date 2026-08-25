from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ConfigError(ValueError):
    """Raised when a project configuration is incomplete or contradictory."""


_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _expand_path(value: str, base: Path) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _bbox(value: Any, label: str) -> tuple[float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ConfigError(f"{label} must be [min_lon, min_lat, max_lon, max_lat]")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label} contains a non-numeric coordinate") from exc
    if not (-180 <= min_lon < max_lon <= 180):
        raise ConfigError(f"{label} longitude interval is invalid")
    if not (-90 <= min_lat < max_lat <= 90):
        raise ConfigError(f"{label} latitude interval is invalid")
    return min_lon, min_lat, max_lon, max_lat


@dataclass(frozen=True)
class ProjectConfig:
    source: Path
    raw: Mapping[str, Any]
    project_id: str
    output_root: Path
    target_bbox: tuple[float, float, float, float]
    fetch_bbox: tuple[float, float, float, float]
    external_paths: Mapping[str, Path]
    footprint_source: Path
    mesh_source: Path | None
    panorama_manifest: Path | None

    @property
    def workspace(self) -> Path:
        return (self.output_root / self.project_id).resolve()

    @property
    def mesh(self) -> Mapping[str, Any]:
        return self.raw["mesh"]

    @property
    def footprints(self) -> Mapping[str, Any]:
        return self.raw["footprints"]

    @property
    def panoramas(self) -> Mapping[str, Any]:
        return self.raw.get("panoramas", {})

    @property
    def chordatlas(self) -> Mapping[str, Any]:
        return self.raw.get("chordatlas", {})

    def path_report(self) -> dict[str, dict[str, Any]]:
        values: dict[str, Path | None] = dict(self.external_paths)
        values["footprint_source"] = self.footprint_source
        values["mesh_source"] = self.mesh_source
        values["panorama_manifest"] = self.panorama_manifest
        return {
            name: {
                "path": str(path) if path is not None else None,
                "exists": path.exists() if path is not None else None,
            }
            for name, path in values.items()
        }


def load_config(path: str | Path) -> ProjectConfig:
    source = Path(path).resolve()
    if not source.is_file():
        raise ConfigError(f"configuration file not found: {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read configuration {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("configuration root must be a JSON object")
    if raw.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")

    project_id = str(raw.get("project_id", ""))
    if not _PROJECT_ID.fullmatch(project_id):
        raise ConfigError("project_id may contain letters, digits, dot, underscore and dash")

    area = raw.get("area")
    if not isinstance(area, dict):
        raise ConfigError("area must be an object")
    target_bbox = _bbox(area.get("target_bbox_wgs84"), "area.target_bbox_wgs84")
    fetch_bbox = _bbox(area.get("fetch_bbox_wgs84", list(target_bbox)), "area.fetch_bbox_wgs84")
    if not (
        fetch_bbox[0] <= target_bbox[0]
        and fetch_bbox[1] <= target_bbox[1]
        and fetch_bbox[2] >= target_bbox[2]
        and fetch_bbox[3] >= target_bbox[3]
    ):
        raise ConfigError("fetch_bbox_wgs84 must contain target_bbox_wgs84")

    base = source.parent
    output_root = _expand_path(str(raw.get("output_root", "../../projects")), base)
    paths_raw = raw.get("paths")
    if not isinstance(paths_raw, dict):
        raise ConfigError("paths must be an object")
    required_paths = (
        "chordatlas_root",
        "sat3dgen_root",
        "data_builder_root",
        "facade_pytorch_root",
        "frankengan_root",
        "conda_executable",
    )
    missing = [name for name in required_paths if not paths_raw.get(name)]
    if missing:
        raise ConfigError("missing external paths: " + ", ".join(missing))
    external_paths = {}
    for name in required_paths:
        value = str(paths_raw[name])
        if name == "conda_executable" and Path(value).name == value:
            # Preserve command names such as ``conda`` so subprocess can resolve
            # them from PATH instead of treating them as config-relative files.
            external_paths[name] = Path(value)
        else:
            external_paths[name] = _expand_path(value, base)

    footprints = raw.get("footprints")
    if not isinstance(footprints, dict) or not footprints.get("source_geojson"):
        raise ConfigError("footprints.source_geojson is required")
    if footprints.get("mode", "osm") not in {"osm", "hybrid", "dsm"}:
        raise ConfigError("footprints.mode must be osm, hybrid or dsm")
    selection_policy = footprints.get("selection_policy", "intersects")
    if selection_policy not in {"intersects", "fully_contained"}:
        raise ConfigError("footprints.selection_policy must be intersects or fully_contained")
    if selection_policy == "fully_contained" and footprints.get("clip_to_mesh_bounds", False):
        raise ConfigError(
            "fully_contained footprints cannot be clipped; set clip_to_mesh_bounds to false"
        )
    footprint_source = _expand_path(str(footprints["source_geojson"]), base)

    mesh = raw.get("mesh")
    if not isinstance(mesh, dict):
        raise ConfigError("mesh must be an object")
    mesh_mode = mesh.get("mode", "existing")
    if mesh_mode not in {"existing", "generate", "on_demand"}:
        raise ConfigError("mesh.mode must be existing, generate or on_demand")
    if mesh_mode == "on_demand":
        mesh_resolution = mesh.get("mesh_resolution", 192)
        if (
            isinstance(mesh_resolution, bool)
            or not isinstance(mesh_resolution, int)
            or not 32 <= mesh_resolution <= 512
        ):
            raise ConfigError("mesh.mesh_resolution must be an integer in [32, 512]")
        if mesh.get("apply_dsm") is not True:
            raise ConfigError("mesh.apply_dsm must be true in on_demand mode")
        if not mesh.get("dsm_dir"):
            raise ConfigError("mesh.dsm_dir is required in on_demand mode")
        dsm_files = mesh.get("dsm_files")
        if not isinstance(dsm_files, list) or not dsm_files:
            raise ConfigError("mesh.dsm_files must be a non-empty array in on_demand mode")
        if any(
            not isinstance(name, str)
            or not name.strip()
            or Path(name).name != name
            or Path(name).suffix.lower() not in {".tif", ".tiff"}
            for name in dsm_files
        ):
            raise ConfigError("mesh.dsm_files must contain GeoTIFF basenames only")
        if str(mesh.get("dsm_crs", "")).upper() != "EPSG:27700":
            raise ConfigError("mesh.dsm_crs must be EPSG:27700 in on_demand mode")
        if not mesh.get("osm_dir"):
            raise ConfigError("mesh.osm_dir is required for DSM semantic correction")
    mesh_source = None
    if mesh_mode == "existing":
        if not mesh.get("source_obj"):
            raise ConfigError("mesh.source_obj is required in existing mode")
        mesh_source = _expand_path(str(mesh["source_obj"]), base)

    panos = raw.get("panoramas", {})
    if not isinstance(panos, dict):
        raise ConfigError("panoramas must be an object")
    panorama_manifest = None
    if panos.get("enabled", False):
        if not panos.get("manifest_csv"):
            raise ConfigError("panoramas.manifest_csv is required when panoramas are enabled")
        panorama_manifest = _expand_path(str(panos["manifest_csv"]), base)

    conda_environment = raw.get("conda_environment", "sat3dgen")
    if conda_environment != "sat3dgen":
        raise ConfigError("this project intentionally uses the existing sat3dgen environment")

    return ProjectConfig(
        source=source,
        raw=raw,
        project_id=project_id,
        output_root=output_root,
        target_bbox=target_bbox,
        fetch_bbox=fetch_bbox,
        external_paths=external_paths,
        footprint_source=footprint_source,
        mesh_source=mesh_source,
        panorama_manifest=panorama_manifest,
    )
