import json
from pathlib import Path
from typing import List, Tuple, Dict, Any

ROOT = Path(__file__).resolve().parent


def load_geojson(path: Path) -> Dict[str, Any]:
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def iter_point_features(geojson: Dict[str, Any]) -> List[Tuple[Dict[str, Any], Tuple[float, float]]]:
    points = []
    for feature in geojson.get('features', []):
        geom = feature.get('geometry', {})
        gtype = geom.get('type')
        if gtype == 'Point':
            coords = geom.get('coordinates', [])
            if len(coords) >= 2:
                points.append((feature, (float(coords[0]), float(coords[1]))))
        elif gtype == 'MultiPoint':
            for coords in geom.get('coordinates', []):
                if len(coords) >= 2:
                    points.append((feature, (float(coords[0]), float(coords[1]))))
    return points


def iter_polygons(geojson: Dict[str, Any]) -> List[List[List[Tuple[float, float]]]]:
    polygons = []
    for feature in geojson.get('features', []):
        geom = feature.get('geometry', {})
        gtype = geom.get('type')
        if gtype == 'Polygon':
            rings = geom.get('coordinates', [])
            if rings:
                polygons.append([[(float(x[0]), float(x[1])) for x in ring] for ring in rings])
        elif gtype == 'MultiPolygon':
            for poly in geom.get('coordinates', []):
                if poly:
                    polygons.append([[(float(x[0]), float(x[1])) for x in ring] for ring in poly])
    return polygons


def point_in_polygon(point: Tuple[float, float], polygon: List[List[Tuple[float, float]]]) -> bool:
    x, y = point
    inside = False
    for ring in polygon:
        if ring and point_in_ring(x, y, ring):
            inside = not inside
    return inside


def point_in_ring(x: float, y: float, ring: List[Tuple[float, float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def build_alignment() -> None:
    dsm_path = ROOT / 'LIDAR_Composite_1m_First_Return_DSM_2022_extents.json'
    london_paths = [
        ROOT / 'london_highways.geojson',
        ROOT / 'london_highways_500x500.geojson',
    ]

    dsm = load_geojson(dsm_path)
    polygons = iter_polygons(dsm)
    if not polygons:
        raise ValueError('No polygon geometry found in DSM JSON')

    summary = {
        'dsm_file': dsm_path.name,
        'dsm_feature_count': len(dsm.get('features', [])),
        'dsm_polygon_count': len(polygons),
        'datasets': [],
    }

    for london_path in london_paths:
        data = load_geojson(london_path)
        points = iter_point_features(data)
        matched = []
        for feature, point in points:
            if any(point_in_polygon(point, polygon) for polygon in polygons):
                matched.append({
                    'id': feature.get('id'),
                    'lon': point[0],
                    'lat': point[1],
                    'properties': feature.get('properties', {}),
                })

        summary['datasets'].append({
            'source_file': london_path.name,
            'total_points': len(points),
            'matched_points': len(matched),
            'matched_ids': [item['id'] for item in matched[:10]],
        })

        out_path = ROOT / f'{london_path.stem}_dsm_aligned.geojson'
        out_geojson = {
            'type': 'FeatureCollection',
            'source': london_path.name,
            'dsm_source': dsm_path.name,
            'features': [
                {
                    'type': 'Feature',
                    'id': item['id'],
                    'properties': item['properties'],
                    'geometry': {
                        'type': 'Point',
                        'coordinates': [item['lon'], item['lat']],
                    },
                }
                for item in matched
            ],
        }
        with out_path.open('w', encoding='utf-8') as f:
            json.dump(out_geojson, f, ensure_ascii=False, indent=2)

    summary_path = ROOT / 'london_dsm_alignment_summary.json'
    with summary_path.open('w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f'\nWrote: {summary_path}')
    for london_path in london_paths:
        print(f'Wrote: {ROOT / f"{london_path.stem}_dsm_aligned.geojson"}')


if __name__ == '__main__':
    build_alignment()
