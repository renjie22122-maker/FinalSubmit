from __future__ import annotations

import argparse
from pathlib import Path


def parse_pano_latlon(line: str) -> tuple[str, str, str] | None:
    line = line.strip()
    if not line:
        return None
    first = line.split()[0]
    parts = first.split(",")
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2]


def rewrite_line(line: str, pano_map: dict[str, str], sat_map: dict[str, str]) -> str:
    out = line
    for old_p, new_p in pano_map.items():
        if old_p in out:
            out = out.replace(old_p, new_p)
    for old_s, new_s in sat_map.items():
        if old_s in out:
            out = out.replace(old_s, new_s)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Align London dataset naming/layout to VIGOR-style format")
    parser.add_argument("--root", type=Path, default=Path("LondonDataSet"))
    parser.add_argument("--city", type=str, default="London")
    parser.add_argument("--pano-label", type=Path, default=Path("pano_label_balanced.txt"))
    parser.add_argument("--train", type=Path, default=Path("london_train.txt"))
    parser.add_argument("--test", type=Path, default=Path("london_test.txt"))
    args = parser.parse_args()

    root = args.root.resolve()
    city_dir = root / args.city
    pano_dir = city_dir / "panorama"
    sky_dir = city_dir / "pano_sky_mask"
    sat_dir = city_dir / "satellite"
    depth_dir = city_dir / "sat_depth"

    for d in [pano_dir, sky_dir, sat_dir, depth_dir]:
        if not d.exists():
            raise FileNotFoundError(f"Missing required directory: {d}")

    pano_label_path = args.pano_label.resolve()
    train_path = args.train.resolve()
    test_path = args.test.resolve()
    for p in [pano_label_path, train_path, test_path]:
        if not p.exists():
            raise FileNotFoundError(f"Missing required file: {p}")

    pano_lines = [ln for ln in pano_label_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    pano_map: dict[str, str] = {}
    for ln in pano_lines:
        parsed = parse_pano_latlon(ln)
        if parsed is None:
            continue
        old_pano, lat, lon = parsed
        stem = Path(old_pano).stem
        new_pano = f"{stem},{lat},{lon},.jpg"
        pano_map[old_pano] = new_pano

    sat_map: dict[str, str] = {}
    for sat_path in sat_dir.glob("sat_*.png"):
        sat_map[sat_path.name] = sat_path.name.replace("sat_", "satellite_", 1)

    depth_map: dict[str, str] = {}
    for dep_path in depth_dir.glob("sat_*.png"):
        depth_map[dep_path.name] = dep_path.name.replace("sat_", "satellite_", 1)

    # Rename panorama and sky mask by paired stems.
    pano_renamed = 0
    sky_renamed = 0
    for old_pano, new_pano in pano_map.items():
        old_p = pano_dir / old_pano
        new_p = pano_dir / new_pano
        if old_p.exists() and old_p != new_p:
            if new_p.exists() and old_p.name != new_p.name:
                raise RuntimeError(f"Panorama target exists: {new_p.name}")
            old_p.rename(new_p)
            pano_renamed += 1

        old_sky = sky_dir / (Path(old_pano).stem + ".png")
        new_sky = sky_dir / (Path(new_pano).stem + ".png")
        if old_sky.exists() and old_sky != new_sky:
            if new_sky.exists() and old_sky.name != new_sky.name:
                raise RuntimeError(f"Sky mask target exists: {new_sky.name}")
            old_sky.rename(new_sky)
            sky_renamed += 1

    sat_renamed = 0
    for old_sat, new_sat in sat_map.items():
        old_p = sat_dir / old_sat
        new_p = sat_dir / new_sat
        if old_p.exists() and old_p != new_p:
            if new_p.exists() and old_p.name != new_p.name:
                raise RuntimeError(f"Satellite target exists: {new_p.name}")
            old_p.rename(new_p)
            sat_renamed += 1

    depth_renamed = 0
    for old_dep, new_dep in depth_map.items():
        old_p = depth_dir / old_dep
        new_p = depth_dir / new_dep
        if old_p.exists() and old_p != new_p:
            if new_p.exists() and old_p.name != new_p.name:
                raise RuntimeError(f"Sat depth target exists: {new_p.name}")
            old_p.rename(new_p)
            depth_renamed += 1

    # Rewrite txt files in VIGOR-style names and place in city folder.
    train_lines = [ln for ln in train_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    test_lines = [ln for ln in test_path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    rewritten_pano = [rewrite_line(ln, pano_map, sat_map) for ln in pano_lines]
    rewritten_train = [rewrite_line(ln, pano_map, sat_map) for ln in train_lines]
    rewritten_test = [rewrite_line(ln, pano_map, sat_map) for ln in test_lines]

    (city_dir / "pano_label_balanced.txt").write_text("\n".join(rewritten_pano) + "\n", encoding="utf-8")
    (city_dir / "same_area_balanced_train.txt").write_text("\n".join(rewritten_train) + "\n", encoding="utf-8")
    (city_dir / "same_area_balanced_test.txt").write_text("\n".join(rewritten_test) + "\n", encoding="utf-8")

    sat_list = sorted([p.name for p in sat_dir.glob("satellite_*.png")])
    (city_dir / "satellite_list.txt").write_text("\n".join(sat_list) + "\n", encoding="utf-8")

    print(f"panorama_renamed={pano_renamed}")
    print(f"pano_sky_mask_renamed={sky_renamed}")
    print(f"satellite_renamed={sat_renamed}")
    print(f"sat_depth_renamed={depth_renamed}")
    print(f"city_txt_written={city_dir}")
    print(f"satellite_list_count={len(sat_list)}")


if __name__ == "__main__":
    main()
