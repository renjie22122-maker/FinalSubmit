from __future__ import annotations

import json
import math
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.etree import ElementTree

from .mesh_pipeline import inspect_obj
from .panoramas import JpegFormatError, read_jpeg_dimensions


_PANO_NAME = re.compile(
    r"^[^_]+_[^_]+_[^_]+_[^_]+_[^_]+_[^_]+_.+\.jpg$", re.IGNORECASE
)


def _issue(report: dict[str, Any], level: str, code: str, message: str) -> None:
    report[level].append({"code": code, "message": message})


def _matrix(element) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(float(element.findtext(f"m{row}{column}", "0")) for column in range(4))
        for row in range(4)
    )


def _transform(matrix, point):
    vector = (point[0], point[1], point[2], 1.0)
    output = tuple(sum(matrix[row][column] * vector[column] for column in range(4)) for row in range(4))
    scale = output[3] if output[3] else 1.0
    return output[0] / scale, output[1] / scale, output[2] / scale


def _minimesh_world_bounds(index_root, model_files):
    matrices = {}
    for entry in index_root.findall("./index/entry"):
        identifier = int(entry.findtext("int"))
        matrix_element = entry.find("javax.vecmath.Matrix4d")
        if matrix_element is None:
            raise ValueError(f"index entry {identifier} has no Matrix4d")
        matrices[identifier] = _matrix(matrix_element)
    offset_element = index_root.find("offset")
    offset = _matrix(offset_element) if offset_element is not None else (
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    vertex_count = 0
    for model in model_files:
        try:
            identifier = int(model.parent.name)
            matrix = matrices[identifier]
        except (ValueError, KeyError) as exc:
            raise ValueError(f"model tile has no matching numeric index entry: {model}") from exc
        with model.open("r", encoding="utf-8-sig", errors="strict") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.startswith("v "):
                    continue
                values = line.split()
                if len(values) < 4:
                    raise ValueError(f"{model}:{line_number}: malformed vertex")
                local = tuple(float(values[index]) for index in range(1, 4))
                world = _transform(offset, _transform(matrix, local))
                vertex_count += 1
                for axis, value in enumerate(world):
                    minimum[axis] = min(minimum[axis], value)
                    maximum[axis] = max(maximum[axis], value)
    if not vertex_count:
        raise ValueError("mini-mesh contains no vertices")
    return {"minimum": minimum, "maximum": maximum, "vertex_count": vertex_count}


def validate_workspace(workspace: str | Path, *, write_report: bool = True) -> dict[str, Any]:
    root = Path(workspace).resolve()
    report: dict[str, Any] = {
        "workspace": str(root),
        "status": "invalid",
        "checks": {},
        "errors": [],
        "warnings": [],
    }
    if not root.is_dir():
        _issue(report, "errors", "workspace_missing", f"workspace directory not found: {root}")
        return report

    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("generated_by") != "myProject.bridge":
            raise ValueError("unknown generated_by marker")
        report["checks"]["manifest"] = "ok"
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        manifest = {}
        _issue(report, "errors", "manifest_invalid", f"cannot read a myProject manifest: {exc}")

    on_demand = manifest.get("mesh", {}).get("mode") == "on_demand"

    if on_demand:
        try:
            config_path = Path(manifest["config"]).expanduser().resolve()
            config_document = json.loads(config_path.read_text(encoding="utf-8"))
            mesh_config = config_document.get("mesh")
            if not isinstance(mesh_config, dict) or mesh_config.get("apply_dsm") is not True:
                raise ValueError("on-demand workspace requires mesh.apply_dsm=true")

            def configured_path(name: str) -> Path:
                raw = mesh_config.get(name)
                if not isinstance(raw, str) or not raw.strip():
                    raise ValueError(f"mesh.{name} is required")
                path = Path(raw).expanduser()
                if not path.is_absolute():
                    path = config_path.parent / path
                return path.resolve()

            dsm_dir = configured_path("dsm_dir")
            osm_dir = configured_path("osm_dir")
            dsm_files = mesh_config.get("dsm_files")
            if not isinstance(dsm_files, list) or not dsm_files:
                raise ValueError("mesh.dsm_files must be a non-empty array")
            if not (osm_dir / "building.geojson").is_file():
                raise ValueError(f"DSM semantic OSM file is missing: {osm_dir / 'building.geojson'}")
            from .top_level_mesh_driver import _validate_required_dsm

            target_bbox = tuple(float(value) for value in config_document["area"]["target_bbox_wgs84"])
            dsm_check = _validate_required_dsm(
                SimpleNamespace(dsm_dir=dsm_dir, dsm_files=list(dsm_files)),
                target_bbox,
                str(mesh_config.get("dsm_crs", "")),
            )
            dsm_check["osm_dir"] = str(osm_dir)
            report["checks"]["dsm"] = dsm_check
        except Exception as exc:
            _issue(report, "errors", "mandatory_dsm_invalid", str(exc))

    footprint_path = root / "footprints.obj"
    try:
        footprint = inspect_obj(footprint_path, y_percentiles=(0.0, 50.0, 100.0))
        if footprint.vertex_count == 0 or footprint.face_count == 0 or footprint.bounds is None:
            raise ValueError("OBJ has no vertices/faces")
        if max(abs(footprint.bounds.minimum[1]), abs(footprint.bounds.maximum[1])) > 1e-8:
            raise ValueError("footprint Y coordinates are not zero")
        report["checks"]["footprints"] = {
            "vertices": footprint.vertex_count,
            "faces": footprint.face_count,
            "bounds": footprint.bounds.to_dict(),
        }
    except Exception as exc:
        _issue(report, "errors", "footprints_invalid", str(exc))

    mini_root = root / "minimesh"
    index_path = mini_root / "index.xml"
    model_files = sorted(mini_root.glob("*/model.obj")) if mini_root.is_dir() else []
    try:
        if on_demand and not index_path.is_file() and not model_files:
            report["checks"]["minimesh"] = {
                "status": "on_demand",
                "message": "no initial MiniMesh; a verified selection generates one from the GUI",
            }
            raise StopIteration
        index_tree = ElementTree.parse(index_path)
        index_root = index_tree.getroot()
        entries = index_root.findall(".//entry")
        if not model_files:
            raise ValueError("no numbered model.obj tiles")
        missing_mtl = []
        empty_models = []
        for model in model_files:
            if model.stat().st_size == 0:
                empty_models.append(str(model))
            with model.open("r", encoding="utf-8", errors="ignore") as stream:
                first_kb = stream.read(4096)
            if "mtllib " in first_kb and not (model.parent / "model.mtl").is_file():
                missing_mtl.append(str(model.parent / "model.mtl"))
        if empty_models:
            raise ValueError(f"{len(empty_models)} model tiles are empty")
        if missing_mtl:
            _issue(
                report,
                "warnings",
                "minimesh_materials_missing",
                f"{len(missing_mtl)} model tiles reference a missing model.mtl",
            )
        world_bounds = _minimesh_world_bounds(index_root, model_files)
        source_bounds = manifest.get("mesh", {}).get("inspection", {}).get("bounds", {})
        expected_minimum = source_bounds.get("minimum")
        expected_maximum = source_bounds.get("maximum")
        vertical_offset = manifest.get("frame", {}).get("vertical_offset_m", 0.0)
        if expected_minimum and expected_maximum:
            expected_minimum = list(expected_minimum)
            expected_maximum = list(expected_maximum)
            expected_minimum[1] += vertical_offset
            expected_maximum[1] += vertical_offset
            errors = [
                abs(actual - expected)
                for actual, expected in zip(
                    world_bounds["minimum"] + world_bounds["maximum"],
                    expected_minimum + expected_maximum,
                )
            ]
            if max(errors) > 1e-3:
                raise ValueError(f"world bounds do not preserve source frame; max error={max(errors):.6g}m")
        report["checks"]["minimesh"] = {
            "model_tiles": len(model_files),
            "index_entries": len(entries),
            "index_root": index_root.tag,
            "world_bounds": world_bounds,
        }
    except StopIteration:
        pass
    except (OSError, ElementTree.ParseError, ValueError) as exc:
        _issue(report, "errors", "minimesh_invalid", f"{index_path}: {exc}")

    tweed_path = root / "tweed.xml"
    try:
        tweed = ElementTree.parse(tweed_path).getroot()
        tags = {element.tag for element in tweed.iter()}
        required = {
            "org.twak.tweed.gen.GISGen",
            "condaEnvironment",
            "facadePytorchRoot",
            "bikeGanRoot",
        }
        if not on_demand:
            required.add("org.twak.tweed.gen.MiniGen")
        missing = sorted(required - tags)
        if missing:
            raise ValueError("missing fields/layers: " + ", ".join(missing))
        conda_env = tweed.findtext("condaEnvironment")
        if conda_env != "sat3dgen":
            raise ValueError(f"condaEnvironment is {conda_env!r}, expected 'sat3dgen'")
        report["checks"]["tweed_xml"] = "ok"
    except (OSError, ElementTree.ParseError, ValueError) as exc:
        _issue(report, "errors", "tweed_xml_invalid", f"{tweed_path}: {exc}")

    jar_text = manifest.get("chordatlas", {}).get("jar") if isinstance(manifest, dict) else None
    jar_path = Path(jar_text) if jar_text else root.parent.parent / "target" / "chordatlas-0.0.1.jar"
    required_jar_entries = {
        "META-INF/services/org.twak.tweed.plugins.TweedPlugin",
        "org/twak/tweed/plugins/HousesPlugin.class",
        "org/twak/readTrace/MiniTransformCLI.class",
        "org/twak/tweed/gen/WorkspaceCLI.class",
        "org/twak/tweed/gen/SelectedBlockMeshService.class",
    }
    try:
        with zipfile.ZipFile(jar_path) as archive:
            names = set(archive.namelist())
        missing = sorted(required_jar_entries - names)
        if missing:
            raise ValueError("missing JAR entries: " + ", ".join(missing))
        report["checks"]["chordatlas_jar"] = str(jar_path)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        _issue(report, "errors", "chordatlas_jar_invalid", f"{jar_path}: {exc}")

    pano_dir = root / "panos"
    if pano_dir.is_dir():
        pano_errors = 0
        jpgs = sorted(pano_dir.glob("*.jpg"))
        for image in jpgs:
            if not _PANO_NAME.fullmatch(image.name):
                pano_errors += 1
                _issue(report, "errors", "panorama_name_invalid", image.name)
                continue
            try:
                width, height = read_jpeg_dimensions(image)
                if width != 2 * height:
                    raise JpegFormatError(f"dimensions are {width}x{height}, expected 2:1")
            except (OSError, JpegFormatError) as exc:
                pano_errors += 1
                _issue(report, "errors", "panorama_image_invalid", f"{image}: {exc}")
        report["checks"]["panoramas"] = {"count": len(jpgs), "invalid": pano_errors}
    else:
        report["checks"]["panoramas"] = {"status": "disabled"}

    coverage = manifest.get("area", {}).get("coverage", {}) if isinstance(manifest, dict) else {}
    ratio = coverage.get("mesh_aabb_target_ratio") if isinstance(coverage, dict) else None
    if (
        not on_demand
        and isinstance(ratio, (int, float))
        and math.isfinite(float(ratio))
        and ratio < 0.999
    ):
        _issue(
            report,
            "warnings",
            "partial_geographic_coverage",
            f"mesh AABB covers approximately {ratio:.2%} of the configured target bbox",
        )

    report["checks"]["select_block_profile"] = {
        "status": "ready_for_gui_smoke_test" if not report["errors"] else "blocked",
        "manual_steps": [
            *( [] if on_demand else ["select the minimesh layer and click load all (visual check)"] ),
            "choose Tools > select and right-click a complete orange OSM footprint",
            *( ["wait for the verified per-selection MiniMesh to appear"] if on_demand else [] ),
            "select the new block layer and click find profiles",
            "select the profiles layer and click optimize",
        ],
    }
    report["status"] = "ok" if not report["errors"] else "invalid"
    if write_report:
        target = root / "validation_report.json"
        temporary = root / "validation_report.json.part"
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
    return report


__all__ = ["validate_workspace"]
