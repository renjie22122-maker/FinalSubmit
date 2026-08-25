from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import requests
from PIL import Image, ImageDraw

SAT_RE = re.compile(r"^(?:satellite|sat)_([0-9.\-]+)_([0-9.\-]+)\.(?:png|jpg|jpeg)$", re.IGNORECASE)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# Categories for common 2D constraints.
OVERPASS_QUERIES = {
    "building": '(way["building"]({bbox});relation["building"]({bbox});)',
    "road": '(way["highway"]({bbox});)',
    "water": '((way["natural"="water"]({bbox});relation["natural"="water"]({bbox});way["waterway"]({bbox});way["landuse"="reservoir"]({bbox}););)',
    "green": '((way["leisure"="park"]({bbox});way["landuse"~"grass|forest|meadow"]({bbox});relation["leisure"="park"]({bbox}););)',
}


def latlon_to_world_px(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
    scale = 256.0 * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * scale
    return x, y


def parse_sat_centers(sat_dir: Path) -> List[Tuple[str, float, float]]:
    centers: List[Tuple[str, float, float]] = []
    for p in sorted(sat_dir.glob("*")):
        if not p.is_file():
            continue
        m = SAT_RE.match(p.name)
        if not m:
            continue
        lat = float(m.group(1))
        lon = float(m.group(2))
        centers.append((p.name, lat, lon))
    if not centers:
        raise RuntimeError(f"No satellite files matched expected naming in {sat_dir}")
    return centers


def make_overpass_query(category: str, bbox: str) -> str:
    body = OVERPASS_QUERIES[category].format(bbox=bbox)
    return f"[out:json][timeout:180];{body}out body;>;out skel qt;"


def fetch_overpass(query: str, retries: int = 3) -> Dict:
    last_err = None
    headers = {
        "User-Agent": "data_builder_osm_constraints/1.0",
        "Accept": "application/json,text/plain,*/*",
    }
    for url in OVERPASS_URLS:
        for i in range(retries):
            try:
                r = requests.post(url, data={"data": query}, headers=headers, timeout=240)
                if r.status_code == 200:
                    return r.json()
                last_err = RuntimeError(f"{url} HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(2 + i)
    raise RuntimeError(f"Overpass query failed after retries: {last_err}")


def build_node_map(osm_json: Dict) -> Dict[int, Tuple[float, float]]:
    node_map: Dict[int, Tuple[float, float]] = {}
    for el in osm_json.get("elements", []):
        if el.get("type") == "node" and "lat" in el and "lon" in el:
            node_map[int(el["id"])] = (float(el["lat"]), float(el["lon"]))
    return node_map


def extract_way_geometries(osm_json: Dict, node_map: Dict[int, Tuple[float, float]]) -> List[Dict]:
    geoms: List[Dict] = []
    for el in osm_json.get("elements", []):
        if el.get("type") != "way":
            continue
        node_ids = el.get("nodes", [])
        coords: List[Tuple[float, float]] = []
        for nid in node_ids:
            p = node_map.get(int(nid))
            if p is None:
                continue
            lat, lon = p
            coords.append((lon, lat))
        if len(coords) < 2:
            continue
        closed = coords[0] == coords[-1] and len(coords) >= 4
        geoms.append(
            {
                "id": el.get("id"),
                "tags": el.get("tags", {}),
                "closed": closed,
                "coords": coords,
            }
        )
    return geoms


def save_geojson(path: Path, geoms: Iterable[Dict]) -> None:
    features = []
    for g in geoms:
        geometry_type = "Polygon" if g["closed"] else "LineString"
        if geometry_type == "Polygon":
            geometry = {"type": "Polygon", "coordinates": [g["coords"]]}
        else:
            geometry = {"type": "LineString", "coordinates": g["coords"]}
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": g.get("id"),
                    **g.get("tags", {}),
                },
                "geometry": geometry,
            }
        )
    data = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(data), encoding="utf-8")


def draw_category_mask(
    geoms: List[Dict],
    center_lat: float,
    center_lon: float,
    size: int,
    zoom: int,
    category: str,
) -> Image.Image:
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)

    cx, cy = latlon_to_world_px(center_lat, center_lon, zoom)

    for g in geoms:
        pts: List[Tuple[int, int]] = []
        for lon, lat in g["coords"]:
            x, y = latlon_to_world_px(lat, lon, zoom)
            px = int(round((x - cx) + size / 2.0))
            py = int(round((y - cy) + size / 2.0))
            pts.append((px, py))

        if len(pts) < 2:
            continue

        # Fast reject: skip if completely outside tile bounds.
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < 0 or max(ys) < 0 or min(xs) >= size or min(ys) >= size:
            continue

        if category == "road":
            draw.line(pts, fill=255, width=3)
        else:
            if g["closed"] and len(pts) >= 4:
                draw.polygon(pts, fill=255)
            else:
                draw.line(pts, fill=255, width=2)

    return img


def main() -> None:
    parser = argparse.ArgumentParser(description="Download OSM for dataset extent and build 2D constraint masks")
    parser.add_argument("--sat-dir", type=Path, default=Path("LondonDataSet/London/satellite"))
    parser.add_argument("--out-dir", type=Path, default=Path("LondonDataSet/London/osm_constraints"))
    parser.add_argument("--zoom", type=int, default=20)
    parser.add_argument("--tile-size", type=int, default=640)
    parser.add_argument("--bbox-margin-deg", type=float, default=0.002)
    parser.add_argument("--skip-masks", action="store_true", default=False)
    args = parser.parse_args()

    sat_dir = args.sat_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    centers = parse_sat_centers(sat_dir)
    lats = [x[1] for x in centers]
    lons = [x[2] for x in centers]

    min_lat = min(lats) - args.bbox_margin_deg
    max_lat = max(lats) + args.bbox_margin_deg
    min_lon = min(lons) - args.bbox_margin_deg
    max_lon = max(lons) + args.bbox_margin_deg
    bbox = f"{min_lat:.7f},{min_lon:.7f},{max_lat:.7f},{max_lon:.7f}"

    meta = {
        "satellite_count": len(centers),
        "bbox": {
            "min_lat": min_lat,
            "min_lon": min_lon,
            "max_lat": max_lat,
            "max_lon": max_lon,
        },
        "zoom": args.zoom,
        "tile_size": args.tile_size,
    }

    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    all_geoms: Dict[str, List[Dict]] = {}
    for cat in OVERPASS_QUERIES:
        query = make_overpass_query(cat, bbox)
        raw = fetch_overpass(query)
        raw_path = out_dir / f"osm_raw_{cat}.json"
        raw_path.write_text(json.dumps(raw), encoding="utf-8")

        node_map = build_node_map(raw)
        geoms = extract_way_geometries(raw, node_map)
        all_geoms[cat] = geoms

        gj_path = out_dir / f"{cat}.geojson"
        save_geojson(gj_path, geoms)
        print(f"{cat}: ways={len(geoms)} geojson={gj_path}")

    if args.skip_masks:
        print(f"Done. OSM data saved under: {out_dir}")
        return

    masks_root = out_dir / "masks"
    for cat, geoms in all_geoms.items():
        cat_dir = masks_root / cat
        cat_dir.mkdir(parents=True, exist_ok=True)

        for i, (name, lat, lon) in enumerate(centers, start=1):
            out_name = Path(name).with_suffix(".png").name
            mask = draw_category_mask(geoms, lat, lon, args.tile_size, args.zoom, cat)
            mask.save(cat_dir / out_name)
            if i % 100 == 0 or i == len(centers):
                print(f"{cat}: {i}/{len(centers)}")

    print(f"Done. OSM constraints saved under: {out_dir}")


if __name__ == "__main__":
    main()
