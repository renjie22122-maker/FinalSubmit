from __future__ import annotations

import argparse
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


SAT_PATTERN = re.compile(r"sat_([0-9.\-]+)_([0-9.\-]+)\.png$")


def latlon_to_world_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    scale = 256.0 * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    lat_rad = math.radians(max(min(lat, 85.05112878), -85.05112878))
    y = (
        (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
        / 2.0
        * scale
    )
    return x, y


def world_px_to_latlon(x: float, y: float, zoom: int) -> tuple[float, float]:
    scale = 256.0 * (2**zoom)
    lon = x / scale * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / scale
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lon


def parse_existing_bounds(sat_dir: Path) -> tuple[float, float, float, float]:
    lats = []
    lons = []
    for p in sat_dir.glob("sat_*.png"):
        m = SAT_PATTERN.match(p.name)
        if not m:
            continue
        lats.append(float(m.group(1)))
        lons.append(float(m.group(2)))
    if not lats or not lons:
        raise RuntimeError(f"No sat_*.png tiles found in {sat_dir}")
    return min(lats), max(lats), min(lons), max(lons)


def build_targets(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    zoom: int,
    stride_px: int,
    name_precision: int,
) -> list[tuple[float, float, str]]:
    min_x, max_y = latlon_to_world_px(min_lat, min_lon, zoom)
    max_x, min_y = latlon_to_world_px(max_lat, max_lon, zoom)

    x0 = min(min_x, max_x)
    x1 = max(min_x, max_x)
    y0 = min(min_y, max_y)
    y1 = max(min_y, max_y)

    xs = []
    cur_x = x0
    while cur_x <= x1 + 1e-6:
        xs.append(cur_x)
        cur_x += stride_px

    ys = []
    cur_y = y0
    while cur_y <= y1 + 1e-6:
        ys.append(cur_y)
        cur_y += stride_px

    targets = []
    seen_names: set[str] = set()
    for y in ys:
        for x in xs:
            lat, lon = world_px_to_latlon(x, y, zoom)
            name = f"sat_{lat:.{name_precision}f}_{lon:.{name_precision}f}.png"
            if name in seen_names:
                raise RuntimeError(
                    f"Filename collision at precision={name_precision}: {name}. "
                    "Increase --name-precision."
                )
            seen_names.add(name)
            targets.append((lat, lon, name))
    return targets


def build_url(lat: float, lon: float, zoom: int, size: int, api_key: str) -> str:
    return (
        "https://maps.googleapis.com/maps/api/staticmap"
        f"?center={lat:.6f},{lon:.6f}"
        f"&zoom={zoom}"
        f"&size={size}x{size}"
        "&maptype=satellite"
        "&format=png"
        f"&key={api_key}"
    )


def download_one(out_path: Path, lat: float, lon: float, zoom: int, size: int, api_key: str, timeout: int) -> tuple[bool, str]:
    try:
        url = build_url(lat, lon, zoom, size, api_key)
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        if "image" not in r.headers.get("content-type", "").lower():
            msg = r.text[:120].replace("\n", " ")
            return False, f"non-image response: {msg}"
        if len(r.content) < 1024:
            return False, "image too small"
        out_path.write_bytes(r.content)
        return True, ""
    except Exception as e:
        return False, str(e)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resample satellite tiles with 50% overlap")
    parser.add_argument("--source-dir", type=Path, default=Path("LondonDataSet/London/satellite"))
    parser.add_argument("--output-dir", type=Path, default=Path("LondonDataSet/London/satellite_overlap50"))
    parser.add_argument("--api-key", type=str, required=True)
    parser.add_argument("--zoom", type=int, default=20)
    parser.add_argument("--tile-size", type=int, default=640)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true", default=False)
    parser.add_argument("--name-precision", type=int, default=6)
    args = parser.parse_args()

    if args.tile_size % 2 != 0:
        raise ValueError("tile-size must be even for exact 50% overlap")

    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    min_lat, max_lat, min_lon, max_lon = parse_existing_bounds(source_dir)
    stride_px = args.tile_size // 2
    targets = build_targets(
        min_lat,
        max_lat,
        min_lon,
        max_lon,
        args.zoom,
        stride_px,
        args.name_precision,
    )

    tasks: list[tuple[Path, float, float]] = []
    skipped = 0
    for lat, lon, name in targets:
        out_path = output_dir / name
        if out_path.exists() and not args.overwrite:
            skipped += 1
            continue
        tasks.append((out_path, lat, lon))

    print(f"source_bbox_lat=[{min_lat:.6f},{max_lat:.6f}] lon=[{min_lon:.6f},{max_lon:.6f}]")
    print(f"tile_size={args.tile_size} stride_px={stride_px} overlap=50%")
    print(f"name_format=sat_<lat>_<lon>.png precision={args.name_precision}")
    print(f"targets={len(targets)} queued={len(tasks)} skipped={skipped}")

    ok = 0
    fail = 0
    failures: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        fut_to_name = {
            ex.submit(
                download_one,
                out_path,
                lat,
                lon,
                args.zoom,
                args.tile_size,
                args.api_key,
                args.timeout,
            ): out_path.name
            for out_path, lat, lon in tasks
        }

        for i, fut in enumerate(as_completed(fut_to_name), start=1):
            success, reason = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
                failures.append((fut_to_name[fut], reason))

            if i % 50 == 0 or i == len(fut_to_name):
                print(f"progress={i}/{len(fut_to_name)} ok={ok} fail={fail}")

    print(f"done ok={ok} fail={fail} skipped={skipped} output={output_dir}")
    if failures:
        fail_log = output_dir / "download_failures.txt"
        with fail_log.open("w", encoding="utf-8") as f:
            for name, reason in failures:
                f.write(f"{name}\t{reason}\n")
        print(f"fail_log={fail_log}")


if __name__ == "__main__":
    main()
