from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image


SAT_PATTERN = re.compile(r"sat_([0-9.\-]+)_([0-9.\-]+)\.png$")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export satellite mosaic from sat_*.png tiles")
    parser.add_argument("--sat-dir", type=Path, default=Path("LondonDataSet/London/satellite"))
    parser.add_argument("--out", type=Path, default=Path("LondonDataSet/London/satellite_mosaic_no_holes.png"))
    args = parser.parse_args()

    sat_dir = args.sat_dir.resolve()
    out_path = args.out.resolve()

    tile_map: dict[tuple[float, float], Path] = {}
    lats: set[float] = set()
    lons: set[float] = set()

    for p in sat_dir.glob("sat_*.png"):
        m = SAT_PATTERN.match(p.name)
        if not m:
            continue
        lat = float(m.group(1))
        lon = float(m.group(2))
        tile_map[(lat, lon)] = p
        lats.add(lat)
        lons.add(lon)

    if not tile_map:
        raise RuntimeError(f"No sat_*.png tiles found in {sat_dir}")

    lats_sorted = sorted(lats, reverse=True)  # north at top
    lons_sorted = sorted(lons)  # west at left

    first = Image.open(next(iter(tile_map.values())))
    tile_w, tile_h = first.size
    first.close()

    missing: list[tuple[float, float]] = []
    for lat in lats_sorted:
        for lon in lons_sorted:
            if (lat, lon) not in tile_map:
                missing.append((lat, lon))

    if missing:
        msg = ", ".join([f"({lat:.6f},{lon:.6f})" for lat, lon in missing[:12]])
        raise RuntimeError(f"Grid has {len(missing)} missing tiles, e.g. {msg}")

    mosaic = Image.new("RGB", (len(lons_sorted) * tile_w, len(lats_sorted) * tile_h))

    for row, lat in enumerate(lats_sorted):
        for col, lon in enumerate(lons_sorted):
            with Image.open(tile_map[(lat, lon)]) as tile:
                if tile.size != (tile_w, tile_h):
                    raise RuntimeError(
                        f"Inconsistent tile size at {tile_map[(lat, lon)].name}: {tile.size} vs {(tile_w, tile_h)}"
                    )
                mosaic.paste(tile.convert("RGB"), (col * tile_w, row * tile_h))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    mosaic.save(out_path)

    print(f"tiles={len(tile_map)}")
    print(f"grid={len(lats_sorted)}x{len(lons_sorted)}")
    print(f"tile_size={tile_w}x{tile_h}")
    print(f"mosaic_size={mosaic.size[0]}x{mosaic.size[1]}")
    print(f"output={out_path}")


if __name__ == "__main__":
    main()
