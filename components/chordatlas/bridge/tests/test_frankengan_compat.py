from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path


def _load_wrapper():
    path = Path(__file__).resolve().parents[1] / "frankengan_compat.py"
    spec = importlib.util.spec_from_file_location("myproject_frankengan_compat", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load compatibility wrapper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCV2:
    def __init__(self, result):
        self.result = result

    def findContours(self, *args, **kwargs):
        return self.result


class FrankenGANCompatibilityTests(unittest.TestCase):
    def test_opencv4_two_value_contract_becomes_opencv3_contract(self):
        wrapper = _load_wrapper()
        cv2 = _FakeCV2((["contour"], "hierarchy"))
        wrapper.install_find_contours_compat(cv2)
        self.assertEqual(cv2.findContours("mask", 1, 2), (None, ["contour"], "hierarchy"))

    def test_opencv3_contract_is_preserved_and_install_is_idempotent(self):
        wrapper = _load_wrapper()
        expected = ("image", ["contour"], "hierarchy")
        cv2 = _FakeCV2(expected)
        wrapper.install_find_contours_compat(cv2)
        first = cv2.findContours
        wrapper.install_find_contours_compat(cv2)
        self.assertIs(cv2.findContours, first)
        self.assertEqual(cv2.findContours("mask", 1, 2), expected)

    def test_ready_marker_is_refreshed_and_removed_by_its_owner(self):
        wrapper = _load_wrapper()
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / ".myproject_frankengan_ready.json"
            heartbeat = wrapper.ReadyMarkerHeartbeat(
                ready, opencv_version="4.test", interval=0.02
            )
            heartbeat.start()
            first = self._wait_for_marker(ready)
            first_epoch = first["heartbeat_epoch"]
            deadline = time.monotonic() + 1.0
            refreshed = first
            while refreshed["heartbeat_epoch"] <= first_epoch and time.monotonic() < deadline:
                time.sleep(0.01)
                refreshed = json.loads(ready.read_text(encoding="utf-8"))
            self.assertGreater(refreshed["heartbeat_epoch"], first_epoch)
            self.assertEqual(refreshed["pid"], heartbeat.pid)
            self.assertEqual(refreshed["token"], heartbeat.token)
            self.assertEqual(refreshed["opencv_version"], "4.test")
            self.assertFalse(list(Path(temporary).glob("*.part")))
            heartbeat.stop()
            self.assertFalse(ready.exists())

    def test_cleanup_preserves_a_marker_owned_by_another_watcher(self):
        wrapper = _load_wrapper()
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / ".myproject_frankengan_ready.json"
            heartbeat = wrapper.ReadyMarkerHeartbeat(ready, interval=0.02)
            foreign = {
                "pid": 999,
                "token": "foreign-token",
                "heartbeat_epoch": time.time(),
            }
            ready.write_text(json.dumps(foreign), encoding="utf-8")
            heartbeat.stop()
            self.assertTrue(ready.is_file())
            self.assertEqual(
                json.loads(ready.read_text(encoding="utf-8"))["token"],
                "foreign-token",
            )

    @staticmethod
    def _wait_for_marker(path: Path) -> dict:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
            time.sleep(0.01)
        raise AssertionError("heartbeat did not publish its ready marker")


if __name__ == "__main__":
    unittest.main()
