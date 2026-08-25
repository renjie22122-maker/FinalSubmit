from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from myproject.dsm_footprints import DsmFootprintError, extract_dsm_footprints


class DsmFootprintTests(unittest.TestCase):
    def test_rejects_missing_dsm_before_importing_optional_dependencies(self):
        missing = Path(tempfile.gettempdir()) / "myproject-does-not-exist-dsm.tif"
        with self.assertRaisesRegex(DsmFootprintError, "missing DSM"):
            extract_dsm_footprints(
                [missing], [-0.13, 51.50, -0.12, 51.51], Path(tempfile.gettempdir()) / "out.geojson"
            )

    def test_rejects_reversed_bbox(self):
        with self.assertRaisesRegex(DsmFootprintError, "bbox interval"):
            extract_dsm_footprints([], [-0.12, 51.50, -0.13, 51.51], "out.geojson")


if __name__ == "__main__":
    unittest.main()
