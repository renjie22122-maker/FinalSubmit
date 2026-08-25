from __future__ import annotations

import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BRIDGE_ROOT / "src"))

from myproject.top_level_mesh_driver import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
