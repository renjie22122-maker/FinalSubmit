from __future__ import annotations

import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any, Sequence

from .config import ProjectConfig
from .workspace import mesh_plan


def _probe(argv: Sequence[str], cwd: Path, timeout: float = 60.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "duration_seconds": time.perf_counter() - started,
            "stdout": (completed.stdout or "")[-4000:],
            "stderr": (completed.stderr or "")[-4000:],
            "command": subprocess.list2cmdline(list(argv)),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "duration_seconds": time.perf_counter() - started,
            "error": str(exc),
            "command": subprocess.list2cmdline(list(argv)),
        }


def run_doctor(config: ProjectConfig, *, execute_probes: bool = True) -> dict[str, Any]:
    root = config.external_paths["chordatlas_root"]
    jar = root / "target" / "chordatlas-0.0.1.jar"
    required_entries = {
        "META-INF/services/org.twak.tweed.plugins.TweedPlugin",
        "org/twak/tweed/plugins/HousesPlugin.class",
        "org/twak/readTrace/MiniTransformCLI.class",
        "org/twak/tweed/gen/WorkspaceCLI.class",
        "com/fasterxml/jackson/core/exc/InputCoercionException.class",
    }
    jar_check: dict[str, Any] = {"path": str(jar), "exists": jar.is_file(), "ok": False}
    if jar.is_file():
        try:
            with zipfile.ZipFile(jar) as archive:
                missing = sorted(required_entries - set(archive.namelist()))
            jar_check.update({"ok": not missing, "missing_entries": missing})
        except (OSError, zipfile.BadZipFile) as exc:
            jar_check["error"] = str(exc)

    conda = str(config.external_paths["conda_executable"])
    sat_root = config.external_paths["sat3dgen_root"]
    probes: dict[str, Any] = {}
    if execute_probes:
        probes["java"] = _probe([str(config.chordatlas.get("java_executable", "java")), "-version"], root)
        probes["sat3dgen_python"] = _probe(
            [
                conda,
                "run",
                "--no-capture-output",
                "-n",
                "sat3dgen",
                "python",
                "-B",
                "-c",
                "import sys; print(sys.executable); import torch; print('torch='+torch.__version__)",
            ],
            sat_root,
        )
        probes["mesh_pipeline_top_level_cli"] = _probe(
            [
                conda,
                "run",
                "--no-capture-output",
                "-n",
                "sat3dgen",
                "python",
                "-B",
                "-m",
                "mesh_pipeline.cli",
                "--help",
            ],
            sat_root,
        )
        probes["mesh_pipeline_top_level_import"] = _probe(
            [
                conda,
                "run",
                "--no-capture-output",
                "-n",
                "sat3dgen",
                "python",
                "-B",
                "-c",
                (
                    "import pathlib,mesh_pipeline; "
                    "p=pathlib.Path(mesh_pipeline.__file__).resolve(); print(p); "
                    "assert p.parent.name=='mesh_pipeline'; "
                    "assert p.parent.parent.name=='Sat3DGen'; "
                    "assert 'mesh_generate_merge_pipeline' not in p.parts"
                ),
            ],
            sat_root,
        )
        probes["dsm_dependencies"] = _probe(
            [
                conda,
                "run",
                "--no-capture-output",
                "-n",
                "sat3dgen",
                "python",
                "-B",
                "-c",
                "import numpy,rasterio,scipy,shapely,pyproj; print('DSM dependencies OK')",
            ],
            sat_root,
        )

    facade_root = config.external_paths["facade_pytorch_root"]
    franken_root = config.external_paths["frankengan_root"]
    static_checks = {
        "facade_pytorch_module": (facade_root / "__main__.py").is_file(),
        "frankengan_watcher": (franken_root / "test_interactive.py").is_file(),
        "frankengan_compatibility_wrapper": (root / "bridge" / "frankengan_compat.py").is_file(),
        "maven_project": (root / "pom.xml").is_file(),
    }
    report = {
        "config": str(config.source),
        "paths": config.path_report(),
        "jar": jar_check,
        "static_checks": static_checks,
        "mesh_plan": mesh_plan(config),
        "probes": probes,
    }
    report["ok"] = (
        all(value["exists"] in {True, None} for value in report["paths"].values())
        and jar_check["ok"]
        and all(static_checks.values())
        and all(probe.get("ok", False) for probe in probes.values())
    )
    return report


__all__ = ["run_doctor"]
