"""Download a centred Google Static Maps satellite mosaic without API-key leakage.

Each request is larger than the retained cell.  Only the central cell is used,
so map controls and attribution drawn at the request boundary are not repeated
through the interior of the machine-learning input.  The resulting imagery is
for local experimental use and is not intended for redistribution.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover - fallback for minimal environments
    SSL_CONTEXT = ssl.create_default_context()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--center_lat", type=float, required=True)
    parser.add_argument("--center_lon", type=float, required=True)
    parser.add_argument("--zoom", type=int, required=True)
    parser.add_argument("--columns", type=int, required=True)
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--request_size", type=int, default=640)
    parser.add_argument("--cell_size", type=int, default=512)
    parser.add_argument("--api_key_env", default="GOOGLE_MAPS_API_KEY")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def lonlat_to_world_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    world_size = 256.0 * (2**zoom)
    x = (lon + 180.0) / 360.0 * world_size
    sin_lat = math.sin(math.radians(max(-85.05112878, min(85.05112878, lat))))
    y = (
        0.5
        - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)
    ) * world_size
    return x, y


def world_pixel_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    world_size = 256.0 * (2**zoom)
    lon = x / world_size * 360.0 - 180.0
    mercator_y = math.pi - 2.0 * math.pi * y / world_size
    lat = math.degrees(math.atan(math.sinh(mercator_y)))
    return lon, lat


def download_static_map(api_key: str, lat: float, lon: float, zoom: int, size: int) -> bytes:
    query = urllib.parse.urlencode(
        {
            "center": f"{lat:.12f},{lon:.12f}",
            "zoom": zoom,
            "size": f"{size}x{size}",
            "scale": 1,
            "maptype": "satellite",
            "format": "png",
            "key": api_key,
        }
    )
    request = urllib.request.Request(
        "https://maps.googleapis.com/maps/api/staticmap?" + query,
        headers={"User-Agent": "Sat3DGen-local-research/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=SSL_CONTEXT
            ) as response:
                return response.read()
        except Exception as error:  # pragma: no cover - network retry path
            last_error = error
            time.sleep(1.0 + attempt)
    raise RuntimeError("Static Maps request failed after three attempts") from last_error


def main() -> None:
    args = parse_args()
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {args.api_key_env!r} is not set")
    if args.cell_size > args.request_size:
        raise ValueError("cell_size cannot exceed request_size")
    if (args.request_size - args.cell_size) % 2:
        raise ValueError("request_size - cell_size must be even")

    center_x, center_y = lonlat_to_world_pixel(
        args.center_lon, args.center_lat, args.zoom
    )
    mosaic = Image.new(
        "RGB", (args.columns * args.cell_size, args.rows * args.cell_size)
    )
    crop_margin = (args.request_size - args.cell_size) // 2
    records = []

    for row in range(args.rows):
        for column in range(args.columns):
            x = center_x + (column - (args.columns - 1) / 2.0) * args.cell_size
            y = center_y + (row - (args.rows - 1) / 2.0) * args.cell_size
            lon, lat = world_pixel_to_lonlat(x, y, args.zoom)
            payload = download_static_map(
                api_key, lat, lon, args.zoom, args.request_size
            )
            image = Image.open(io.BytesIO(payload)).convert("RGB")
            if image.size != (args.request_size, args.request_size):
                raise RuntimeError(
                    f"Unexpected response size {image.size} at row={row}, col={column}"
                )
            cell = image.crop(
                (
                    crop_margin,
                    crop_margin,
                    crop_margin + args.cell_size,
                    crop_margin + args.cell_size,
                )
            )
            mosaic.paste(cell, (column * args.cell_size, row * args.cell_size))
            records.append(
                {
                    "row": row,
                    "column": column,
                    "center_lat": lat,
                    "center_lon": lon,
                    "response_sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            print(
                f"downloaded {row * args.columns + column + 1}/"
                f"{args.rows * args.columns}",
                flush=True,
            )

    half_width = args.columns * args.cell_size / 2.0
    half_height = args.rows * args.cell_size / 2.0
    west, north = world_pixel_to_lonlat(
        center_x - half_width, center_y - half_height, args.zoom
    )
    east, south = world_pixel_to_lonlat(
        center_x + half_width, center_y + half_height, args.zoom
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    mosaic.save(args.output)
    manifest = {
        "center": [args.center_lat, args.center_lon],
        "zoom": args.zoom,
        "request_size_px": args.request_size,
        "retained_cell_size_px": args.cell_size,
        "grid": [args.columns, args.rows],
        "mosaic_size_px": list(mosaic.size),
        "bounds_wgs84": {
            "west": west,
            "south": south,
            "east": east,
            "north": north,
        },
        "requests": records,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "requests"}, indent=2))


if __name__ == "__main__":
    main()
