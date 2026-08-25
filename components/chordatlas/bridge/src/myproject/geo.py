"""Dependency-free WGS84 and local-coordinate primitives.

The local frame matches the Sat3DGen mesh convention: X points east, Y is
height, and Z points south.  Conversion uses 111,320 metres per latitude
degree and ``111320 * cos(origin_lat)`` metres per longitude degree.  This is
an intentionally local approximation for city/block-sized areas.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence, Tuple


METERS_PER_DEGREE_LAT = 111_320.0


class CoordinateError(ValueError):
    """Raised when a geographic or local coordinate is unusable."""


def _finite(name: str, value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CoordinateError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(result):
        raise CoordinateError(f"{name} must be finite, got {value!r}")
    return result


def _longitude(name: str, value: float) -> float:
    value = _finite(name, value)
    if not -180.0 <= value <= 180.0:
        raise CoordinateError(f"{name} must be in [-180, 180], got {value}")
    return value


def _latitude(name: str, value: float, *, allow_poles: bool = True) -> float:
    value = _finite(name, value)
    if not -90.0 <= value <= 90.0:
        raise CoordinateError(f"{name} must be in [-90, 90], got {value}")
    if not allow_poles and abs(value) >= 90.0:
        raise CoordinateError(f"{name} must be strictly between -90 and 90")
    return value


@dataclass(frozen=True)
class LocalBBox:
    """Axis-aligned bounding box in local metres."""

    min_x: float
    min_z: float
    max_x: float
    max_z: float

    def __post_init__(self) -> None:
        for name in ("min_x", "min_z", "max_x", "max_z"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))
        if self.min_x > self.max_x:
            raise CoordinateError("min_x must not be greater than max_x")
        if self.min_z > self.max_z:
            raise CoordinateError("min_z must not be greater than max_z")

    @classmethod
    def from_points(cls, points: Iterable[Tuple[float, float]]) -> "LocalBBox":
        materialized = [(_finite("x", x), _finite("z", z)) for x, z in points]
        if not materialized:
            raise CoordinateError("at least one local point is required")
        xs = [point[0] for point in materialized]
        zs = [point[1] for point in materialized]
        return cls(min(xs), min(zs), max(xs), max(zs))

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def depth(self) -> float:
        return self.max_z - self.min_z

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0, (self.min_z + self.max_z) / 2.0)

    def contains(self, x: float, z: float, *, inclusive: bool = True) -> bool:
        x = _finite("x", x)
        z = _finite("z", z)
        if inclusive:
            return self.min_x <= x <= self.max_x and self.min_z <= z <= self.max_z
        return self.min_x < x < self.max_x and self.min_z < z < self.max_z

    def intersects(self, other: "LocalBBox", *, inclusive: bool = True) -> bool:
        if inclusive:
            return not (
                self.max_x < other.min_x
                or other.max_x < self.min_x
                or self.max_z < other.min_z
                or other.max_z < self.min_z
            )
        return not (
            self.max_x <= other.min_x
            or other.max_x <= self.min_x
            or self.max_z <= other.min_z
            or other.max_z <= self.min_z
        )

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.min_x, self.min_z, self.max_x, self.max_z)


@dataclass(frozen=True)
class BBox:
    """Compact WGS84 bounding box in GeoJSON lon/lat order."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_lon", _longitude("min_lon", self.min_lon))
        object.__setattr__(self, "max_lon", _longitude("max_lon", self.max_lon))
        object.__setattr__(self, "min_lat", _latitude("min_lat", self.min_lat))
        object.__setattr__(self, "max_lat", _latitude("max_lat", self.max_lat))
        if self.min_lon >= self.max_lon:
            raise CoordinateError("min_lon must be less than max_lon")
        if self.min_lat >= self.max_lat:
            raise CoordinateError("min_lat must be less than max_lat")

    @classmethod
    def from_sequence(cls, values: Sequence[float]) -> "BBox":
        if len(values) != 4:
            raise CoordinateError("WGS84 bbox requires four values")
        return cls(values[0], values[1], values[2], values[3])

    @property
    def center_lon(self) -> float:
        return (self.min_lon + self.max_lon) / 2.0

    @property
    def center_lat(self) -> float:
        return (self.min_lat + self.max_lat) / 2.0

    @property
    def center(self) -> Tuple[float, float]:
        """Return the center as ``(lon, lat)``."""

        return (self.center_lon, self.center_lat)

    def contains(self, lon: float, lat: float, *, inclusive: bool = True) -> bool:
        lon = _longitude("lon", lon)
        lat = _latitude("lat", lat)
        if inclusive:
            return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat
        return self.min_lon < lon < self.max_lon and self.min_lat < lat < self.max_lat

    def intersects(self, other: "BBox", *, inclusive: bool = True) -> bool:
        if inclusive:
            return not (
                self.max_lon < other.min_lon
                or other.max_lon < self.min_lon
                or self.max_lat < other.min_lat
                or other.max_lat < self.min_lat
            )
        return not (
            self.max_lon <= other.min_lon
            or other.max_lon <= self.min_lon
            or self.max_lat <= other.min_lat
            or other.max_lat <= self.min_lat
        )

    def to_local(self, frame: "LocalFrame") -> LocalBBox:
        return LocalBBox.from_points(
            (
                frame.to_local(self.min_lon, self.min_lat),
                frame.to_local(self.min_lon, self.max_lat),
                frame.to_local(self.max_lon, self.min_lat),
                frame.to_local(self.max_lon, self.max_lat),
            )
        )

    def as_tuple(self) -> Tuple[float, float, float, float]:
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


GeoBBox = BBox


@dataclass(frozen=True)
class LocalFrame:
    """WGS84 origin and reversible local-metre approximation."""

    origin_lat: float
    origin_lon: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "origin_lat",
            _latitude("origin_lat", self.origin_lat, allow_poles=False),
        )
        object.__setattr__(self, "origin_lon", _longitude("origin_lon", self.origin_lon))

    @classmethod
    def from_bbox(cls, bbox: BBox) -> "LocalFrame":
        return cls(origin_lat=bbox.center_lat, origin_lon=bbox.center_lon)

    @property
    def meters_per_degree_lon(self) -> float:
        return METERS_PER_DEGREE_LAT * math.cos(math.radians(self.origin_lat))

    def to_local(self, lon: float, lat: float) -> Tuple[float, float]:
        """Convert GeoJSON-order ``(lon, lat)`` to local ``(x, z)`` metres."""

        lon = _longitude("lon", lon)
        lat = _latitude("lat", lat)
        x = (lon - self.origin_lon) * self.meters_per_degree_lon
        z = -(lat - self.origin_lat) * METERS_PER_DEGREE_LAT
        return (x, z)

    def to_wgs84(self, x: float, z: float) -> Tuple[float, float]:
        """Convert local ``(x, z)`` to GeoJSON-order ``(lon, lat)``."""

        x = _finite("x", x)
        z = _finite("z", z)
        lon = self.origin_lon + x / self.meters_per_degree_lon
        lat = self.origin_lat - z / METERS_PER_DEGREE_LAT
        return (_longitude("lon", lon), _latitude("lat", lat))

    def latlon_to_local(self, lat: float, lon: float) -> Tuple[float, float]:
        """Convert explicit ``(lat, lon)`` order to local ``(x, z)``."""

        return self.to_local(lon, lat)

    def local_to_latlon(self, x: float, z: float) -> Tuple[float, float]:
        """Convert local coordinates to explicit ``(lat, lon)`` order."""

        lon, lat = self.to_wgs84(x, z)
        return (lat, lon)

    def bbox_to_local(self, bbox: BBox) -> LocalBBox:
        return bbox.to_local(self)


def wgs84_to_local(lon: float, lat: float, frame: LocalFrame) -> Tuple[float, float]:
    return frame.to_local(lon, lat)


def local_to_wgs84(x: float, z: float, frame: LocalFrame) -> Tuple[float, float]:
    return frame.to_wgs84(x, z)


__all__ = [
    "BBox",
    "CoordinateError",
    "GeoBBox",
    "LocalBBox",
    "LocalFrame",
    "METERS_PER_DEGREE_LAT",
    "local_to_wgs84",
    "wgs84_to_local",
]
