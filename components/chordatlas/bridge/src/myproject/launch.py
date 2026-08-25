from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .validate import validate_workspace


class LaunchError(RuntimeError):
    pass


def gui_command(config: ProjectConfig, workspace: str | Path | None = None) -> list[str]:
    project = Path(workspace).resolve() if workspace is not None else config.workspace
    jar = config.external_paths["chordatlas_root"] / "target" / "chordatlas-0.0.1.jar"
    java = str(config.chordatlas.get("java_executable", "java"))
    heap_gb = int(config.chordatlas.get("heap_gb", 12))
    return [
        java,
        "-Dsun.java2d.uiScale=1",
        "-Dtweed.skipExampleDownload=true",
        f"-Xmx{heap_gb}g",
        "-jar",
        str(jar),
        "--project",
        str(project),
    ]


def launch_gui(
    config: ProjectConfig,
    workspace: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    project = Path(workspace).resolve() if workspace is not None else config.workspace
    validation = validate_workspace(project)
    command = gui_command(config, project)
    result: dict[str, Any] = {
        "command": command,
        "display": subprocess.list2cmdline(command),
        "cwd": str(config.external_paths["chordatlas_root"]),
        "validation_status": validation["status"],
        "started": False,
    }
    if validation["status"] != "ok":
        raise LaunchError(f"workspace validation failed; see {project / 'validation_report.json'}")
    jar = Path(command[5])
    if not jar.is_file():
        raise LaunchError(f"ChordAtlas JAR not found: {jar}")
    if dry_run:
        return result

    log_dir = project / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / "chordatlas-gui.log").open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(config.external_paths["chordatlas_root"]),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creationflags,
        )
    except OSError as exc:
        log.close()
        raise LaunchError(f"failed to launch ChordAtlas: {exc}") from exc
    log.close()
    result.update({"started": True, "pid": process.pid, "log": str(log_dir / "chordatlas-gui.log")})
    return result


def frankengan_command(config: ProjectConfig) -> list[str]:
    return [
        str(config.external_paths["conda_executable"]),
        "run",
        "--no-capture-output",
        "-n",
        "sat3dgen",
        "python",
        "-B",
        "-u",
        str(config.external_paths["chordatlas_root"] / "bridge" / "frankengan_compat.py"),
        "--root",
        str(config.external_paths["frankengan_root"]),
    ]


def launch_frankengan(
    config: ProjectConfig,
    workspace: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    project = Path(workspace).resolve() if workspace is not None else config.workspace
    root = config.external_paths["frankengan_root"]
    script = root / "test_interactive.py"
    compatibility_wrapper = config.external_paths["chordatlas_root"] / "bridge" / "frankengan_compat.py"
    command = frankengan_command(config)
    result = {
        "command": command,
        "display": subprocess.list2cmdline(command),
        "cwd": str(root),
        "started": False,
        "note": "Only needed for ChordAtlas network texture generation; Select/Profile does not need it.",
    }
    if not script.is_file():
        raise LaunchError(f"FrankenGAN watcher not found: {script}")
    if not compatibility_wrapper.is_file():
        raise LaunchError(f"myProject FrankenGAN compatibility wrapper not found: {compatibility_wrapper}")
    if dry_run:
        return result
    log_dir = project / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "frankengan.log"
    log = log_path.open("a", encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            shell=False,
            creationflags=creationflags,
        )
    except OSError as exc:
        log.close()
        raise LaunchError(f"failed to launch FrankenGAN watcher: {exc}") from exc
    log.close()
    result.update({"started": True, "pid": process.pid, "log": str(log_path)})
    return result


__all__ = [
    "LaunchError",
    "frankengan_command",
    "gui_command",
    "launch_frankengan",
    "launch_gui",
]
