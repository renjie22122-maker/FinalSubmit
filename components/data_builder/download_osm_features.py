"""
Download OSM features (buildings, roads, landuse, etc.) for the satellite
coverage area and save as GeoJSON files with full metadata.

This script queries the Overpass API for detailed geographic data within
the bounding box defined by satellite tile centers.

Usage:
    python download_osm_features.py --sat-dir satellite --out-dir osm_features
    python download_osm_features.py --sat-dir LondonDataSet/London/satellite --out-dir LondonDataSet/London/osm_features
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont


SAT_RE = re.compile(r"^(?:satellite|sat)_([0-9.\-]+)_([0-9.\-]+)\.(?:png|jpg|jpeg)$", re.IGNORECASE)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# ── Overpass queries ──────────────────────────────────────────────────
# Each query returns ways/relations with full node geometry via "out geom;"

QUERIES = {
    "building": """
        (
          way["building"]({bbox});
          relation["building"]({bbox});
        );
        out body; >; out skel qt;
    """,
    "building_with_height": """
        (
          way["building"]["height"]({bbox});
          way["building"]["building:levels"]({bbox});
        );
        out body; >; out skel qt;
    """,
    "road": """
        (
          way["highway"]({bbox});
        );
        out body; >; out skel qt;
    """,
    "water": """
        (
          way["natural"="water"]({bbox});
          relation["natural"="water"]({bbox});
          way["waterway"]({bbox});
          way["landuse"="reservoir"]({bbox});
          way["landuse"="basin"]({bbox});
        );
        out body; >; out skel qt;
    """,
    "landuse": """
        (
          way["landuse"]({bbox});
          relation["landuse"]({bbox});
        );
        out body; >; out skel qt;
    """,
    "green": """
        (
          way["leisure"="park"]({bbox});
          way["leisure"="garden"]({bbox});
          way["leisure"="golf_course"]({bbox});
          way["landuse"~"grass|forest|meadow|orchard|vineyard|allotments"]({bbox});
          relation["leisure"="park"]({bbox});
        );
        out body; >; out skel qt;
    """,
    "railway": """
        (
          way["railway"]({bbox});
        );
        out body; >; out skel qt;
    """,
    "barrier": """
        (
          way["barrier"]({bbox});
        );
        out body; >; out skel qt;
    """,
}


# ── Colour scheme for visualisation ───────────────────────────────────
CATEGORY_COLORS = {
    "building": (255, 0, 0, 180),           # red
    "building_with_height": (255, 0, 0, 200),
    "road": (255, 255, 0, 200),              # yellow
    "water": (0, 150, 255, 180),             # blue
    "landuse": (100, 200, 100, 120),         # green
    "green": (0, 200, 0, 150),               # bright green
    "railway": (150, 75, 0, 200),            # brown
    "barrier": (128, 128, 128, 200),         # grey
}


# ── Helpers ───────────────────────────────────────────────────────────

def parse_sat_centers(sat_dir: Path) -> List[Tuple[str, float, float]]:
    """Parse satellite tile filenames to extract centre coordinates."""
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


def get_sat_bounds(sat_dir: Path, margin_deg: float = 0.002
                   ) -> Tuple[float, float, float, float]:
    """Return (min_lat, min_lon, max_lat, max_lon) for the satellite area."""
    centers = parse_sat_centers(sat_dir)
    lats = [c[1] for c in centers]
    lons = [c[2] for c in centers]
    return (min(lats) - margin_deg, min(lons) - margin_deg,
            max(lats) + margin_deg, max(lons) + margin_deg)


def latlon_to_world_px(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
    """Convert lat/lon to Web Mercator pixel coordinates at given zoom."""
    scale = 256.0 * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * scale
    return x, y


def meters_per_pixel(lat: float, zoom: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2**zoom)


def bbox_str(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> str:
    return f"{min_lat:.7f},{min_lon:.7f},{max_lat:.7f},{max_lon:.7f}"


# ── Overpass API ──────────────────────────────────────────────────────

def fetch_overpass(query: str, retries: int = 3) -> Dict:
    """Send a query to the Overpass API with retries."""
    last_err = None
    headers = {
        "User-Agent": "data_builder_osm_features/1.0",
        "Accept": "application/json,text/plain,*/*",
    }
    for url in OVERPASS_URLS:
        for i in range(retries):
            try:
                r = requests.post(url, data={"data": query}, headers=headers, timeout=300)
                if r.status_code == 200:
                    return r.json()
                last_err = RuntimeError(f"{url} HTTP {r.status_code}: {r.text[:300]}")
            except Exception as e:
                last_err = e
            time.sleep(2 + i * 2)
    raise RuntimeError(f"Overpass query failed after retries: {last_err}")


# ── Geometry extraction ───────────────────────────────────────────────

def build_node_map(osm_json: Dict) -> Dict[int, Tuple[float, float]]:
    """Build a {node_id: (lat, lon)} map from OSM JSON."""
    node_map: Dict[int, Tuple[float, float]] = {}
    for el in osm_json.get("elements", []):
        if el.get("type") == "node" and "lat" in el and "lon" in el:
            node_map[int(el["id"])] = (float(el["lat"]), float(el["lon"]))
    return node_map


def extract_geometries(osm_json: Dict, node_map: Dict[int, Tuple[float, float]]
                       ) -> List[Dict[str, Any]]:
    """Extract GeoJSON-like features from OSM JSON."""
    features: List[Dict[str, Any]] = []

    for el in osm_json.get("elements", []):
        el_type = el.get("type")
        el_id = el.get("id")
        tags = el.get("tags", {})

        if el_type == "node" and "lat" in el and "lon" in el:
            # Point feature (e.g. a POI or a standalone node)
            features.append({
                "type": "Feature",
                "id": f"node/{el_id}",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(el["lon"]), float(el["lat"])],
                },
                "properties": {"osm_id": el_id, "osm_type": "node", **tags},
            })

        elif el_type == "way":
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

            closed = (coords[0] == coords[-1] and len(coords) >= 4)
            geom_type = "Polygon" if closed else "LineString"
            geom_coords = [coords] if closed else coords

            features.append({
                "type": "Feature",
                "id": f"way/{el_id}",
                "geometry": {"type": geom_type, "coordinates": geom_coords},
                "properties": {"osm_id": el_id, "osm_type": "way", **tags},
            })

        elif el_type == "relation":
            # For relations, we try to build a multipolygon from members
            members = el.get("members", [])
            outer_rings: List[List[Tuple[float, float]]] = []
            inner_rings: List[List[Tuple[float, float]]] = []

            for member in members:
                ref = member.get("ref")
                role = member.get("role", "")
                if member.get("type") == "way":
                    # We need to look up the way in the elements
                    for el2 in osm_json.get("elements", []):
                        if el2.get("id") == ref and el2.get("type") == "way":
                            node_ids2 = el2.get("nodes", [])
                            coords2 = []
                            for nid in node_ids2:
                                p = node_map.get(int(nid))
                                if p:
                                    coords2.append((float(p[1]), float(p[0])))
                            if len(coords2) >= 2:
                                if role == "inner":
                                    inner_rings.append(coords2)
                                else:
                                    outer_rings.append(coords2)
                            break

            if outer_rings:
                # Build a multipolygon
                polygons = []
                for ring in outer_rings:
                    if ring[0] != ring[-1]:
                        ring.append(ring[0])
                    polygons.append([ring])
                for ring in inner_rings:
                    if ring[0] != ring[-1]:
                        ring.append(ring[0])
                    if polygons:
                        polygons[0].append(ring)

                features.append({
                    "type": "Feature",
                    "id": f"relation/{el_id}",
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": polygons,
                    },
                    "properties": {"osm_id": el_id, "osm_type": "relation", **tags},
                })

    return features


def save_geojson(path: Path, features: List[Dict]) -> None:
    """Save features as a GeoJSON FeatureCollection."""
    data = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── Visualisation ─────────────────────────────────────────────────────

def draw_feature_on_tile(
    draw: ImageDraw,
    feature: Dict,
    center_lat: float,
    center_lon: float,
    tile_size: int,
    zoom: int,
    color: Tuple[int, int, int, int],
    line_width: int = 2,
) -> None:
    """Draw a single OSM feature onto a PIL ImageDraw surface."""
    geom = feature.get("geometry", {})
    geom_type = geom.get("type")
    coords = geom.get("coordinates", [])

    if geom_type == "Point":
        lon, lat = coords
        x, y = latlon_to_world_px(lat, lon, zoom)
        px = int(round((x - center_lon_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
        py = int(round((y - center_lat_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
        if 0 <= px < tile_size and 0 <= py < tile_size:
            draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=color[:3])

    elif geom_type == "LineString":
        pts = []
        for lon, lat in coords:
            x, y = latlon_to_world_px(lat, lon, zoom)
            px = int(round((x - center_lon_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
            py = int(round((y - center_lat_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
            pts.append((px, py))
        if len(pts) >= 2:
            draw.line(pts, fill=color[:3], width=line_width)

    elif geom_type == "Polygon":
        for ring in coords:
            pts = []
            for lon, lat in ring:
                x, y = latlon_to_world_px(lat, lon, zoom)
                px = int(round((x - center_lon_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
                py = int(round((y - center_lat_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
                pts.append((px, py))
            if len(pts) >= 3:
                draw.polygon(pts, fill=color, outline=tuple(color[:3]) + (255,))

    elif geom_type == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                pts = []
                for lon, lat in ring:
                    x, y = latlon_to_world_px(lat, lon, zoom)
                    px = int(round((x - center_lon_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
                    py = int(round((y - center_lat_px(center_lat, center_lon, zoom)) + tile_size / 2.0))
                    pts.append((px, py))
                if len(pts) >= 3:
                    draw.polygon(pts, fill=color, outline=tuple(color[:3]) + (255,))


def center_lon_px(center_lat: float, center_lon: float, zoom: int) -> float:
    """Get the world pixel x of the tile centre."""
    return latlon_to_world_px(center_lat, center_lon, zoom)[0]


def center_lat_px(center_lat: float, center_lon: float, zoom: int) -> float:
    """Get the world pixel y of the tile centre."""
    return latlon_to_world_px(center_lat, center_lon, zoom)[1]


def create_overview_visualization(
    all_features: Dict[str, List[Dict]],
    sat_dir: Path,
    out_path: Path,
    zoom: int = 20,
    tile_size: int = 640,
) -> None:
    """
    Create a composite overview image showing all OSM features overlaid
    on a blank background, with a legend.
    """
    centers = parse_sat_centers(sat_dir)
    if not centers:
        return

    # Determine overall bounds
    lats = [c[1] for c in centers]
    lons = [c[2] for c in centers]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    center_lat = (min_lat + max_lat) / 2
    center_lon = (min_lon + max_lon) / 2

    # Calculate image size to cover the whole area
    cx, cy = latlon_to_world_px(center_lat, center_lon, zoom)
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for _, lat, lon in centers:
        x, y = latlon_to_world_px(lat, lon, zoom)
        min_x = min(min_x, x)
        max_x = max(max_x, x)
        min_y = min(min_y, y)
        max_y = max(max_y, y)

    # Add tile extent margin
    mpp = meters_per_pixel(center_lat, zoom)
    half_tile_px = (tile_size * mpp / 2) / mpp  # half tile in pixels
    min_x -= half_tile_px
    max_x += half_tile_px
    min_y -= half_tile_px
    max_y += half_tile_px

    img_w = int(max_x - min_x) + 100
    img_h = int(max_y - min_y) + 100
    img_w = max(img_w, 800)
    img_h = max(img_h, 800)

    # Cap size to avoid huge images
    max_dim = 4000
    if img_w > max_dim or img_h > max_dim:
        scale = min(max_dim / img_w, max_dim / img_h)
        img_w = int(img_w * scale)
        img_h = int(img_h * scale)

    img = Image.new("RGBA", (img_w, img_h), (30, 30, 30, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    # Draw features
    for cat, features in all_features.items():
        color = CATEGORY_COLORS.get(cat, (200, 200, 200, 150))
        for feature in features:
            geom = feature.get("geometry", {})
            geom_type = geom.get("type")
            coords = geom.get("coordinates", [])

            try:
                if geom_type == "Point":
                    lon, lat = coords
                    x, y = latlon_to_world_px(lat, lon, zoom)
                    px = int(round(x - min_x))
                    py = int(round(y - min_y))
                    if 0 <= px < img_w and 0 <= py < img_h:
                        draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=color[:3])

                elif geom_type == "LineString":
                    pts = []
                    for lon, lat in coords:
                        x, y = latlon_to_world_px(lat, lon, zoom)
                        pts.append((int(round(x - min_x)), int(round(y - min_y))))
                    if len(pts) >= 2:
                        draw.line(pts, fill=color[:3], width=2)

                elif geom_type == "Polygon":
                    for ring in coords:
                        pts = []
                        for lon, lat in ring:
                            x, y = latlon_to_world_px(lat, lon, zoom)
                            pts.append((int(round(x - min_x)), int(round(y - min_y))))
                        if len(pts) >= 3:
                            draw.polygon(pts, fill=color, outline=tuple(color[:3]) + (255,))

                elif geom_type == "MultiPolygon":
                    for polygon in coords:
                        for ring in polygon:
                            pts = []
                            for lon, lat in ring:
                                x, y = latlon_to_world_px(lat, lon, zoom)
                                pts.append((int(round(x - min_x)), int(round(y - min_y))))
                            if len(pts) >= 3:
                                draw.polygon(pts, fill=color, outline=tuple(color[:3]) + (255,))
            except Exception:
                continue

    # Draw legend
    legend_x = 15
    legend_y = 15
    line_h = 22
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    # Legend background
    legend_h = len(CATEGORY_COLORS) * line_h + 20
    draw.rectangle([legend_x, legend_y, legend_x + 200, legend_y + legend_h],
                   fill=(0, 0, 0, 180), outline=(255, 255, 255, 100))

    for i, (cat, color) in enumerate(CATEGORY_COLORS.items()):
        if cat not in all_features or not all_features[cat]:
            continue
        y_pos = legend_y + 10 + i * line_h
        draw.rectangle([legend_x + 5, y_pos, legend_x + 20, y_pos + 14],
                       fill=color[:3] + (255,))
        count = len(all_features[cat])
        draw.text((legend_x + 25, y_pos - 1), f"{cat}: {count}", fill=(255, 255, 255), font=font)

    img = img.convert("RGB")
    img.save(out_path, quality=90)
    print(f"Overview visualization saved to: {out_path}")


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download OSM features (buildings, roads, landuse, etc.) "
                    "for the satellite coverage area."
    )
    parser.add_argument("--sat-dir", type=Path, default=Path("satellite"),
                        help="Directory containing satellite tiles")
    parser.add_argument("--out-dir", type=Path, default=Path("osm_features"),
                        help="Output directory for GeoJSON files and visualizations")
    parser.add_argument("--zoom", type=int, default=20,
                        help="Web Mercator zoom level for mask generation")
    parser.add_argument("--tile-size", type=int, default=640,
                        help="Satellite tile size in pixels")
    parser.add_argument("--bbox-margin", type=float, default=0.002,
                        help="Margin (degrees) to add around satellite bounds")
    parser.add_argument("--categories", type=str, nargs="+",
                        default=list(QUERIES.keys()),
                        help=f"Categories to download: {list(QUERIES.keys())}")
    parser.add_argument("--skip-masks", action="store_true", default=False,
                        help="Skip per-tile mask generation")
    parser.add_argument("--skip-overview", action="store_true", default=False,
                        help="Skip overview visualization")
    args = parser.parse_args()

    sat_dir = args.sat_dir.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Determine bounding box
    min_lat, min_lon, max_lat, max_lon = get_sat_bounds(sat_dir, args.bbox_margin)
    bbox = bbox_str(min_lat, min_lon, max_lat, max_lon)
    print(f"Satellite area bbox: lat=[{min_lat:.6f}, {max_lat:.6f}] lon=[{min_lon:.6f}, {max_lon:.6f}]")

    # Save metadata
    centers = parse_sat_centers(sat_dir)
    meta = {
        "satellite_count": len(centers),
        "bbox": {"min_lat": min_lat, "min_lon": min_lon,
                 "max_lat": max_lat, "max_lon": max_lon},
        "zoom": args.zoom,
        "tile_size": args.tile_size,
        "categories": args.categories,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # 2. Download each category
    all_features: Dict[str, List[Dict]] = {}
    for cat in args.categories:
        if cat not in QUERIES:
            print(f"Warning: Unknown category '{cat}', skipping.")
            continue

        print(f"\n{'='*60}")
        print(f"Downloading: {cat} ...")
        query_body = QUERIES[cat].format(bbox=bbox)
        query = f"[out:json][timeout:300];{query_body}"

        try:
            raw = fetch_overpass(query)
        except RuntimeError as e:
            print(f"  FAILED: {e}")
            continue

        # Save raw response
        raw_path = out_dir / f"osm_raw_{cat}.json"
        raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        # Extract geometries
        node_map = build_node_map(raw)
        features = extract_geometries(raw, node_map)
        all_features[cat] = features

        # Save GeoJSON
        gj_path = out_dir / f"{cat}.geojson"
        save_geojson(gj_path, features)
        print(f"  {cat}: {len(features)} features -> {gj_path.name}")

        # Print summary stats
        if features:
            tags_summary: Dict[str, int] = {}
            for f in features:
                for key in f.get("properties", {}):
                    if key not in ("osm_id", "osm_type"):
                        tags_summary[key] = tags_summary.get(key, 0) + 1
            top_tags = sorted(tags_summary.items(), key=lambda x: -x[1])[:10]
            if top_tags:
                print(f"  Top tags: {dict(top_tags)}")

    # 3. Generate per-tile mask images (if requested)
    if not args.skip_masks and all_features:
        masks_root = out_dir / "masks"
        print(f"\nGenerating per-tile masks in {masks_root} ...")

        for cat, features in all_features.items():
            if not features:
                continue
            cat_dir = masks_root / cat
            cat_dir.mkdir(parents=True, exist_ok=True)
            color = CATEGORY_COLORS.get(cat, (200, 200, 200, 150))

            for i, (name, lat, lon) in enumerate(centers, start=1):
                img = Image.new("RGBA", (args.tile_size, args.tile_size), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img, "RGBA")

                for feature in features:
                    draw_feature_on_tile(draw, feature, lat, lon,
                                         args.tile_size, args.zoom, color)

                out_name = Path(name).with_suffix(".png").name
                img.save(cat_dir / out_name)

                if i % 100 == 0 or i == len(centers):
                    print(f"  {cat}: {i}/{len(centers)} masks")

    # 4. Create overview visualization
    if not args.skip_overview and all_features:
        overview_path = out_dir / "overview.png"
        create_overview_visualization(
            all_features, sat_dir, overview_path,
            zoom=args.zoom, tile_size=args.tile_size,
        )

    # 5. Final summary
    print(f"\n{'='*60}")
    print(f"All OSM data saved to: {out_dir}")
    total = sum(len(v) for v in all_features.values())
    print(f"Total features downloaded: {total}")
    for cat, features in all_features.items():
        print(f"  {cat}: {len(features)} features")
    print("Done!")


if __name__ == "__main__":
    main()
