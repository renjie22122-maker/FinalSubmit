from __future__ import annotations

import argparse
import math
import re
from pathlib import Path


PANO_RE = re.compile(r"^(pano_\d+),([0-9.\-]+),([0-9.\-]+),\.jpg$")
SAT_RE = re.compile(r"^satellite_([0-9.\-]+)_([0-9.\-]+)\.png$")
ID_RE = re.compile(r"(pano_\d+)")


def meters_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)


def parse_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ID_RE.search(line)
        if m:
            ids.add(m.group(1))
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate strict VIGOR 4-candidate label files for London")
    parser.add_argument("--city-dir", type=Path, default=Path("LondonDataSet/London"))
    parser.add_argument("--train-src", type=Path, default=Path("london_train.txt"))
    parser.add_argument("--test-src", type=Path, default=Path("london_test.txt"))
    parser.add_argument("--zoom", type=int, default=20)
    args = parser.parse_args()

    city_dir = args.city_dir.resolve()
    pano_dir = city_dir / "panorama"
    sat_dir = city_dir / "satellite"

    if not pano_dir.exists() or not sat_dir.exists():
        raise FileNotFoundError("Missing panorama/ satellite directory under city dir")

    # Load panorama items.
    panos: list[tuple[str, str, float, float]] = []
    id_to_name: dict[str, str] = {}
    for p in sorted(pano_dir.glob("*.jpg")):
        m = PANO_RE.match(p.name)
        if not m:
            continue
        pano_id = m.group(1)
        lat = float(m.group(2))
        lon = float(m.group(3))
        panos.append((p.name, pano_id, lat, lon))
        id_to_name[pano_id] = p.name

    if not panos:
        raise RuntimeError("No panorama files matched VIGOR naming format")

    # Load satellite items.
    sats: list[tuple[str, float, float]] = []
    for p in sorted(sat_dir.glob("*.png")):
        m = SAT_RE.match(p.name)
        if not m:
            continue
        lat = float(m.group(1))
        lon = float(m.group(2))
        sats.append((p.name, lat, lon))

    if len(sats) < 4:
        raise RuntimeError("Need at least 4 satellite tiles for VIGOR 4-candidate format")

    # Build full label lines.
    full_lines: list[str] = []
    line_by_id: dict[str, str] = {}

    for pano_name, pano_id, p_lat, p_lon in panos:
        mpp = meters_per_pixel(p_lat, args.zoom)
        candidates: list[tuple[float, str, float, float]] = []
        for sat_name, s_lat, s_lon in sats:
            dy_m = (s_lat - p_lat) * 111000.0
            dx_m = (s_lon - p_lon) * 111000.0 * math.cos(math.radians(p_lat))
            dist = math.hypot(dx_m, dy_m)
            dy_px = dy_m / mpp
            dx_px = dx_m / mpp
            candidates.append((dist, sat_name, dy_px, dx_px))

        candidates.sort(key=lambda x: x[0])
        top4 = candidates[:4]

        parts = [pano_name]
        for _, sat_name, dy_px, dx_px in top4:
            parts.append(sat_name)
            parts.append(f"{dy_px:.1f}")
            parts.append(f"{dx_px:.1f}")

        line = " ".join(parts)
        full_lines.append(line)
        line_by_id[pano_id] = line

    # Build train/test from source ids.
    train_ids = parse_ids(args.train_src.resolve())
    test_ids = parse_ids(args.test_src.resolve())

    train_lines = [line_by_id[i] for i in sorted(train_ids, key=lambda x: int(x.split("_")[1])) if i in line_by_id]
    test_lines = [line_by_id[i] for i in sorted(test_ids, key=lambda x: int(x.split("_")[1])) if i in line_by_id]

    (city_dir / "pano_label_balanced.txt").write_text("\n".join(full_lines) + "\n", encoding="utf-8")
    (city_dir / "same_area_balanced_train.txt").write_text("\n".join(train_lines) + "\n", encoding="utf-8")
    (city_dir / "same_area_balanced_test.txt").write_text("\n".join(test_lines) + "\n", encoding="utf-8")

    print(f"panorama_count={len(panos)}")
    print(f"satellite_count={len(sats)}")
    print(f"pano_label_balanced_count={len(full_lines)}")
    print(f"train_count={len(train_lines)}")
    print(f"test_count={len(test_lines)}")
    print(f"output_dir={city_dir}")


if __name__ == "__main__":
    main()
