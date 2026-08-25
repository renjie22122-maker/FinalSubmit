from __future__ import annotations

import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

import requests


SAT_PATTERN = re.compile(r"sat_([0-9.\-]+)_([0-9.\-]+)\.png$")


def parse_existing_grid(sat_dir: Path) -> tuple[list[float], list[float], set[str]]:
    lats = set()
    lons = set()
    names = set()
    for p in sat_dir.glob("sat_*.png"):
        m = SAT_PATTERN.match(p.name)
        if not m:
            continue
        lat = float(m.group(1))
        lon = float(m.group(2))
        lats.add(lat)
        lons.add(lon)
        names.add(p.name)
    return sorted(lats), sorted(lons), names


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


def download_one(name: str, lat: float, lon: float, out_path: Path, api_key: str, zoom: int, size: int, timeout: int) -> tuple[str, bool, str]:
    url = build_url(lat, lon, zoom, size, api_key)
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code != 200:
            return name, False, f"HTTP {r.status_code}"
        ctype = r.headers.get("content-type", "")
        if "image" not in ctype.lower():
            msg = r.text[:120].replace("\n", " ")
            return name, False, f"non-image response: {msg}"
        if len(r.content) < 1024:
            return name, False, "image too small"
        out_path.write_bytes(r.content)
        return name, True, ""
    except Exception as e:
        return name, False, str(e)


def chunks(seq: list[tuple[str, float, float, Path]], n: int) -> Iterable[list[tuple[str, float, float, Path]]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fill missing satellite tiles over the existing lat/lon grid")
    parser.add_argument("--sat-dir", type=Path, default=Path("LondonDataSet/London/satellite"))
    parser.add_argument("--api-key", type=str, required=True)
    parser.add_argument("--zoom", type=int, default=20)
    parser.add_argument("--size", type=int, default=640)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    sat_dir = args.sat_dir.resolve()
    sat_dir.mkdir(parents=True, exist_ok=True)

    lats, lons, existing_names = parse_existing_grid(sat_dir)
    if not lats or not lons:
        raise RuntimeError(f"No existing sat_*.png found in {sat_dir}")

    targets: list[tuple[str, float, float, Path]] = []
    for lat in lats:
        for lon in lons:
            name = f"sat_{lat:.6f}_{lon:.6f}.png"
            if name in existing_names:
                continue
            targets.append((name, lat, lon, sat_dir / name))

    total_full = len(lats) * len(lons)
    print(f"existing={len(existing_names)} full_grid={total_full} missing={len(targets)}")
    if not targets:
        print("No missing tiles. Done.")
        return

    ok = 0
    fail = 0
    failed: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [
            ex.submit(
                download_one,
                name,
                lat,
                lon,
                out_path,
                args.api_key,
                args.zoom,
                args.size,
                args.timeout,
            )
            for name, lat, lon, out_path in targets
        ]

        for i, fut in enumerate(as_completed(futs), start=1):
            name, success, reason = fut.result()
            if success:
                ok += 1
            else:
                fail += 1
                failed.append((name, reason))

            if i % 25 == 0 or i == len(futs):
                print(f"progress={i}/{len(futs)} ok={ok} fail={fail}")

    print(f"done ok={ok} fail={fail} output={sat_dir}")
    if failed:
        fail_log = sat_dir.parent / "satellite_fill_failed.txt"
        with fail_log.open("w", encoding="utf-8") as f:
            for name, reason in failed:
                f.write(f"{name}\t{reason}\n")
        print(f"failed list saved: {fail_log}")


if __name__ == "__main__":
    main()
