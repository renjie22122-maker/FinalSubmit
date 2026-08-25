"""Strict GeoJSON building-footprint ingestion and ChordAtlas OBJ export.

Only ``Polygon`` and ``MultiPolygon`` feature geometries are accepted.  Every
polygon contributes its exterior ring as one OBJ face; interior rings cannot
be represented by ChordAtlas' footprint OBJ format and are counted in the
result statistics.  By default a local bounding box selects intersecting
polygons without changing their shape.  Optional rectangular clipping is
available for callers that explicitly request it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

from .geo import LocalBBox, LocalFrame


LonLat = Tuple[float, float]
PointXZ = Tuple[float, float]
GeoJSONSource = Union[str, Path, Mapping]
_EPSILON = 1e-12


class GeoJSONError(ValueError):
    """Raised when the GeoJSON document itself is not a FeatureCollection."""


class _InvalidPolygon(ValueError):
    pass


@dataclass(frozen=True)
class Footprint:
    """One validated exterior ring in local X/Z metres.

    ``points`` is open: the first point is not repeated at the end.  This maps
    directly to one Wavefront OBJ face.
    """

    points: Tuple[PointXZ, ...]
    feature_id: Optional[str] = None
    source_feature_index: int = -1
    polygon_index: int = 0

    def __post_init__(self) -> None:
        if len(self.points) < 3:
            raise ValueError("a footprint requires at least three points")
        for x, z in self.points:
            if not math.isfinite(x) or not math.isfinite(z):
                raise ValueError("footprint coordinates must be finite")
        if abs(_signed_area(self.points)) <= _EPSILON:
            raise ValueError("a footprint must have non-zero area")

    @property
    def bounds(self) -> LocalBBox:
        return LocalBBox.from_points(self.points)


@dataclass
class FootprintStats:
    """Counters collected while filtering and converting GeoJSON.

    ``invalid`` counts malformed features/polygons. ``filtered`` counts valid
    unsupported features and valid polygons rejected by the local bbox.
    ``holes`` counts source interior rings; holes are validated but omitted
    from the OBJ because a single OBJ face cannot encode them.
    """

    features: int = 0
    polygon_features: int = 0
    polygons: int = 0
    exported: int = 0
    holes: int = 0
    invalid: int = 0
    filtered: int = 0
    clipped: int = 0
    partial: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "features": self.features,
            "polygon_features": self.polygon_features,
            "polygons": self.polygons,
            "exported": self.exported,
            "holes": self.holes,
            "invalid": self.invalid,
            "filtered": self.filtered,
            "clipped": self.clipped,
        }


@dataclass(frozen=True)
class FootprintResult:
    footprints: Tuple[Footprint, ...]
    stats: FootprintStats

    @property
    def items(self) -> Tuple[Footprint, ...]:
        """Alias useful to generic pipeline stages."""

        return self.footprints

    def __iter__(self) -> Iterator[Footprint]:
        return iter(self.footprints)

    def __len__(self) -> int:
        return len(self.footprints)

    def __getitem__(self, index: int) -> Footprint:
        return self.footprints[index]


def load_geojson_footprints(
    source: GeoJSONSource,
    frame: LocalFrame,
    local_bbox: Optional[LocalBBox] = None,
    *,
    clip: bool = False,
    selection_policy: str = "intersects",
) -> FootprintResult:
    """Load strict Polygon/MultiPolygon features into the local frame.

    Parameters
    ----------
    source:
        A path or an already-decoded GeoJSON mapping.
    frame:
        WGS84-to-local frame. Local coordinates use X east and Z south.
    local_bbox:
        Optional selection box in local metres. With the default
        ``clip=False``, intersecting polygons are retained whole.
    clip:
        If true, retained exterior rings are clipped to ``local_bbox``.
    selection_policy:
        ``"intersects"`` preserves the historical behaviour.  With
        ``"fully_contained"`` a polygon is exported only when its complete
        exterior ring lies inside ``local_bbox``; it is never truncated.

    Feature-level errors do not abort the collection. They increment
    ``stats.invalid``; an invalid document root raises :class:`GeoJSONError`.
    """

    if not isinstance(frame, LocalFrame):
        raise TypeError("frame must be a LocalFrame")
    if local_bbox is not None and not isinstance(local_bbox, LocalBBox):
        raise TypeError("local_bbox must be a LocalBBox or None")
    if clip and local_bbox is None:
        raise ValueError("clip=True requires local_bbox")
    if selection_policy not in {"intersects", "fully_contained"}:
        raise ValueError("selection_policy must be intersects or fully_contained")
    if clip and selection_policy == "fully_contained":
        raise ValueError("fully_contained footprints cannot also be clipped")

    document = _load_document(source)
    if document.get("type") != "FeatureCollection":
        raise GeoJSONError("GeoJSON root must have type 'FeatureCollection'")
    features = document.get("features")
    if not _is_sequence(features):
        raise GeoJSONError("FeatureCollection.features must be an array")

    stats = FootprintStats()
    output: List[Footprint] = []

    for feature_index, feature in enumerate(features):
        stats.features += 1
        if not isinstance(feature, Mapping) or feature.get("type") != "Feature":
            stats.invalid += 1
            continue

        geometry = feature.get("geometry")
        if geometry is None:
            stats.filtered += 1
            continue
        if not isinstance(geometry, Mapping):
            stats.invalid += 1
            continue

        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "Polygon":
            raw_polygons = (coordinates,)
        elif geometry_type == "MultiPolygon":
            if not _is_sequence(coordinates) or not coordinates:
                stats.invalid += 1
                continue
            raw_polygons = coordinates
        else:
            stats.filtered += 1
            continue

        stats.polygon_features += 1
        feature_id = _feature_id(feature)
        for polygon_index, raw_polygon in enumerate(raw_polygons):
            stats.polygons += 1
            _consume_polygon(
                raw_polygon,
                feature_id,
                feature_index,
                polygon_index,
                frame,
                local_bbox,
                clip,
                selection_policy,
                output,
                stats,
            )

    stats.exported = len(output)
    return FootprintResult(tuple(output), stats)


def write_footprints_obj(
    footprints: Union[FootprintResult, Iterable[Footprint]],
    output_path: Union[str, Path],
    *,
    include_comments: bool = True,
) -> Path:
    """Write one ``v x 0 z`` group and one exterior-ring face per footprint."""

    if isinstance(footprints, FootprintResult):
        items = footprints.footprints
        stats = footprints.stats
    else:
        items = tuple(footprints)
        stats = None

    for item in items:
        if not isinstance(item, Footprint):
            raise TypeError("footprints must contain Footprint instances")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    if include_comments:
        lines.append("# ChordAtlas footprint OBJ: X east, Y up, Z south; units metres")
        if stats is not None:
            lines.append("# stats " + " ".join(f"{key}={value}" for key, value in stats.as_dict().items()))

    next_vertex = 1
    for item_index, footprint in enumerate(items):
        lines.append(f"o footprint_{item_index:06d}")
        if include_comments and footprint.feature_id is not None:
            safe_id = footprint.feature_id.replace("\r", " ").replace("\n", " ")
            lines.append(f"# feature_id {safe_id}")
        face_indices: List[str] = []
        for x, z in footprint.points:
            lines.append(f"v {_format_number(x)} 0 {_format_number(z)}")
            face_indices.append(str(next_vertex))
            next_vertex += 1
        lines.append("f " + " ".join(face_indices))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def export_geojson_footprints(
    source: GeoJSONSource,
    output_path: Union[str, Path],
    frame: LocalFrame,
    local_bbox: Optional[LocalBBox] = None,
    *,
    clip: bool = False,
    selection_policy: str = "intersects",
) -> FootprintResult:
    """Load, validate, select and export footprints in one call."""

    result = load_geojson_footprints(
        source,
        frame,
        local_bbox,
        clip=clip,
        selection_policy=selection_policy,
    )
    write_footprints_obj(result, output_path)
    return result


# Short aliases for orchestration code.
load_footprints = load_geojson_footprints
write_obj = write_footprints_obj


def _load_document(source: GeoJSONSource) -> Mapping:
    if isinstance(source, Mapping):
        return source
    if not isinstance(source, (str, Path)):
        raise TypeError("source must be a path or GeoJSON mapping")
    path = Path(source)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GeoJSONError(f"unable to read GeoJSON {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise GeoJSONError("GeoJSON document must be an object")
    return value


def _consume_polygon(
    raw_polygon: Any,
    feature_id: Optional[str],
    feature_index: int,
    polygon_index: int,
    frame: LocalFrame,
    local_bbox: Optional[LocalBBox],
    clip: bool,
    selection_policy: str,
    output: List[Footprint],
    stats: FootprintStats,
) -> None:
    if not _is_sequence(raw_polygon) or not raw_polygon:
        stats.invalid += 1
        return

    stats.holes += max(0, len(raw_polygon) - 1)
    try:
        rings = tuple(_parse_ring(raw_ring) for raw_ring in raw_polygon)
        _validate_holes(rings[0], rings[1:])
    except _InvalidPolygon:
        stats.invalid += 1
        return

    local_points = tuple(frame.to_local(lon, lat) for lon, lat in rings[0])
    polygon_bounds = LocalBBox.from_points(local_points)
    if local_bbox is not None and not polygon_bounds.intersects(local_bbox):
        stats.filtered += 1
        return

    if (
        local_bbox is not None
        and selection_policy == "fully_contained"
        and not all(local_bbox.contains(x, z) for x, z in local_points)
    ):
        stats.filtered += 1
        stats.partial += 1
        return

    if clip:
        clipped = _clip_ring(local_points, local_bbox)
        if len(clipped) < 3 or abs(_signed_area(clipped)) <= _EPSILON:
            stats.filtered += 1
            return
        if not _rings_equal(local_points, clipped):
            stats.clipped += 1
        local_points = clipped

    try:
        output.append(
            Footprint(
                points=local_points,
                feature_id=feature_id,
                source_feature_index=feature_index,
                polygon_index=polygon_index,
            )
        )
    except ValueError:
        stats.invalid += 1


def _parse_ring(raw_ring: Any) -> Tuple[LonLat, ...]:
    if not _is_sequence(raw_ring) or len(raw_ring) < 4:
        raise _InvalidPolygon("a linear ring requires at least four positions")

    positions = tuple(_parse_position(raw_position) for raw_position in raw_ring)
    if positions[0] != positions[-1]:
        raise _InvalidPolygon("a linear ring must be closed")
    ring = positions[:-1]
    if len(ring) < 3 or len(set(ring)) != len(ring):
        raise _InvalidPolygon("a linear ring requires three distinct, non-repeated vertices")
    if abs(_signed_area(ring)) <= _EPSILON:
        raise _InvalidPolygon("a linear ring must have non-zero area")
    if not _is_simple_ring(ring):
        raise _InvalidPolygon("a linear ring must not self-intersect")
    return ring


def _parse_position(raw_position: Any) -> LonLat:
    if not _is_sequence(raw_position) or len(raw_position) < 2:
        raise _InvalidPolygon("a position requires longitude and latitude")
    lon = _json_number(raw_position[0])
    lat = _json_number(raw_position[1])
    if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
        raise _InvalidPolygon("position is outside WGS84 longitude/latitude bounds")
    return (lon, lat)


def _json_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _InvalidPolygon("coordinates must be JSON numbers")
    value = float(value)
    if not math.isfinite(value):
        raise _InvalidPolygon("coordinates must be finite")
    return value


def _feature_id(feature: Mapping) -> Optional[str]:
    value = feature.get("id")
    properties = feature.get("properties")
    if value is None and isinstance(properties, Mapping):
        for key in ("osm_id", "@id", "id", "name"):
            if properties.get(key) is not None:
                value = properties[key]
                break
    return None if value is None else str(value)


def _validate_holes(outer: Tuple[LonLat, ...], holes: Tuple[Tuple[LonLat, ...], ...]) -> None:
    for hole in holes:
        if any(not _point_in_ring(point, outer, boundary_is_inside=False) for point in hole):
            raise _InvalidPolygon("an interior ring must be strictly inside its exterior ring")
        if _rings_intersect(outer, hole):
            raise _InvalidPolygon("an interior ring must not cross its exterior ring")

    for index, first in enumerate(holes):
        for second in holes[index + 1 :]:
            if _rings_intersect(first, second):
                raise _InvalidPolygon("interior rings must not intersect")
            if _point_in_ring(first[0], second) or _point_in_ring(second[0], first):
                raise _InvalidPolygon("interior rings must not overlap or nest")


def _signed_area(points: Sequence[Tuple[float, float]]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in _edges(points)
    )


def _edges(points: Sequence[Tuple[float, float]]) -> Iterator[Tuple[Tuple[float, float], Tuple[float, float]]]:
    for index, first in enumerate(points):
        yield first, points[(index + 1) % len(points)]


def _is_simple_ring(ring: Tuple[LonLat, ...]) -> bool:
    edges = tuple(_edges(ring))
    count = len(edges)
    for first_index, first in enumerate(edges):
        for second_index in range(first_index + 1, count):
            if second_index == first_index + 1 or (first_index == 0 and second_index == count - 1):
                continue
            if _segments_intersect(first, edges[second_index]):
                return False
    return True


def _rings_intersect(first: Sequence[LonLat], second: Sequence[LonLat]) -> bool:
    return any(_segments_intersect(a, b) for a in _edges(first) for b in _edges(second))


def _segments_intersect(
    first: Tuple[Tuple[float, float], Tuple[float, float]],
    second: Tuple[Tuple[float, float], Tuple[float, float]],
) -> bool:
    a, b = first
    c, d = second
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if o1 * o2 < 0.0 and o3 * o4 < 0.0:
        return True
    return (
        (abs(o1) <= _EPSILON and _on_segment(a, b, c))
        or (abs(o2) <= _EPSILON and _on_segment(a, b, d))
        or (abs(o3) <= _EPSILON and _on_segment(c, d, a))
        or (abs(o4) <= _EPSILON and _on_segment(c, d, b))
    )


def _orientation(a: LonLat, b: LonLat, c: LonLat) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: LonLat, b: LonLat, point: LonLat) -> bool:
    return (
        min(a[0], b[0]) - _EPSILON <= point[0] <= max(a[0], b[0]) + _EPSILON
        and min(a[1], b[1]) - _EPSILON <= point[1] <= max(a[1], b[1]) + _EPSILON
    )


def _point_in_ring(point: LonLat, ring: Sequence[LonLat], boundary_is_inside: bool = True) -> bool:
    inside = False
    x, y = point
    for first, second in _edges(ring):
        if abs(_orientation(first, second, point)) <= _EPSILON and _on_segment(first, second, point):
            return boundary_is_inside
        if (first[1] > y) != (second[1] > y):
            crossing_x = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x < crossing_x:
                inside = not inside
    return inside


def _clip_ring(points: Tuple[PointXZ, ...], bbox: LocalBBox) -> Tuple[PointXZ, ...]:
    clipped: List[PointXZ] = list(points)
    boundaries = (
        (lambda point: point[0] >= bbox.min_x, lambda a, b: _at_x(a, b, bbox.min_x)),
        (lambda point: point[0] <= bbox.max_x, lambda a, b: _at_x(a, b, bbox.max_x)),
        (lambda point: point[1] >= bbox.min_z, lambda a, b: _at_z(a, b, bbox.min_z)),
        (lambda point: point[1] <= bbox.max_z, lambda a, b: _at_z(a, b, bbox.max_z)),
    )
    for inside, intersection in boundaries:
        if not clipped:
            break
        source = clipped
        clipped = []
        previous = source[-1]
        previous_inside = inside(previous)
        for current in source:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    clipped.append(intersection(previous, current))
                clipped.append(current)
            elif previous_inside:
                clipped.append(intersection(previous, current))
            previous = current
            previous_inside = current_inside
        clipped = list(_deduplicate_points(clipped))
    return _deduplicate_points(clipped)


def _at_x(first: PointXZ, second: PointXZ, x: float) -> PointXZ:
    delta = second[0] - first[0]
    if abs(delta) <= _EPSILON:
        return (x, first[1])
    ratio = (x - first[0]) / delta
    return (x, first[1] + ratio * (second[1] - first[1]))


def _at_z(first: PointXZ, second: PointXZ, z: float) -> PointXZ:
    delta = second[1] - first[1]
    if abs(delta) <= _EPSILON:
        return (first[0], z)
    ratio = (z - first[1]) / delta
    return (first[0] + ratio * (second[0] - first[0]), z)


def _deduplicate_points(points: Iterable[PointXZ]) -> Tuple[PointXZ, ...]:
    result: List[PointXZ] = []
    for point in points:
        if not result or not _points_close(result[-1], point):
            result.append(point)
    if len(result) > 1 and _points_close(result[0], result[-1]):
        result.pop()
    return tuple(result)


def _rings_equal(first: Sequence[PointXZ], second: Sequence[PointXZ]) -> bool:
    return len(first) == len(second) and all(_points_close(a, b) for a, b in zip(first, second))


def _points_close(first: PointXZ, second: PointXZ) -> bool:
    return abs(first[0] - second[0]) <= _EPSILON and abs(first[1] - second[1]) <= _EPSILON


def _format_number(value: float) -> str:
    if abs(value) < 0.5e-9:
        value = 0.0
    rendered = f"{value:.9f}".rstrip("0").rstrip(".")
    return rendered if rendered not in ("", "-0") else "0"


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


__all__ = [
    "Footprint",
    "FootprintResult",
    "FootprintStats",
    "GeoJSONError",
    "export_geojson_footprints",
    "load_footprints",
    "load_geojson_footprints",
    "write_footprints_obj",
    "write_obj",
]
