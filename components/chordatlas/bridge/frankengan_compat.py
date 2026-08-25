"""Run the existing FrankenGAN watcher with OpenCV 3/4 contour compatibility.

This wrapper changes process-local Python behaviour only.  It does not edit
FrankenGAN, install packages, or mutate the sat3dgen conda environment.
"""

from __future__ import annotations

import argparse
import builtins
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import uuid


READY_HEARTBEAT_SECONDS = 5.0


def install_find_contours_compat(cv2_module) -> None:
    original = cv2_module.findContours
    if getattr(original, "_myproject_compat", False):
        return

    def find_contours(*args, **kwargs):
        result = original(*args, **kwargs)
        if len(result) == 2:  # OpenCV 4: contours, hierarchy
            contours, hierarchy = result
            return None, contours, hierarchy
        return result  # OpenCV 3 already returns image, contours, hierarchy

    find_contours._myproject_compat = True
    cv2_module.findContours = find_contours


def _write_ready_marker(
    ready: Path, *, pid: int, token: str, ready_local_time: str, opencv_version: str
) -> None:
    """Atomically publish a short-lived watcher lease."""
    temporary = ready.with_name(f"{ready.name}.{token}.part")
    payload = {
        "pid": pid,
        "token": token,
        "ready_local_time": ready_local_time,
        "heartbeat_epoch": time.time(),
        "opencv_version": opencv_version,
        "find_contours_compat": True,
    }
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, ready)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _remove_ready_marker_if_owned(ready: Path, token: str) -> None:
    """Never remove a lease which a newer/parallel watcher has replaced."""
    try:
        payload = json.loads(ready.read_text(encoding="utf-8"))
        if payload.get("token") == token:
            ready.unlink()
    except (FileNotFoundError, OSError, ValueError, TypeError):
        pass


class ReadyMarkerHeartbeat:
    def __init__(
        self,
        ready: Path,
        *,
        opencv_version: str = "unknown",
        interval: float = READY_HEARTBEAT_SECONDS,
    ):
        self.ready = ready
        self.interval = interval
        self.pid = os.getpid()
        self.token = uuid.uuid4().hex
        self.ready_local_time = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.opencv_version = opencv_version
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="myproject-frankengan-ready-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _write_ready_marker(
                    self.ready,
                    pid=self.pid,
                    token=self.token,
                    ready_local_time=self.ready_local_time,
                    opencv_version=self.opencv_version,
                )
            except OSError as error:
                print(f"could not refresh FrankenGAN ready marker: {error}")
            if self._stop.wait(self.interval):
                break

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval + 1.0))
        _remove_ready_marker_if_owned(self.ready, self.token)
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="myProject FrankenGAN compatibility watcher")
    parser.add_argument("--root", type=Path, required=True)
    args, watcher_args = parser.parse_known_args(argv)
    root = args.root.resolve()
    script = root / "test_interactive.py"
    if not script.is_file():
        parser.error(f"FrankenGAN watcher not found: {script}")

    os.chdir(root)
    sys.path.insert(0, str(root))
    import cv2

    install_find_contours_compat(cv2)
    ready = root / ".myproject_frankengan_ready.json"
    heartbeat = ReadyMarkerHeartbeat(ready, opencv_version=cv2.__version__)
    original_print = builtins.print

    def tracked_print(*values, **kwargs):
        original_print(*values, **kwargs)
        if "all nets up" in " ".join(str(value) for value in values):
            heartbeat.start()

    builtins.print = tracked_print
    sys.argv = [str(script), *watcher_args]
    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        builtins.print = original_print
        heartbeat.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
