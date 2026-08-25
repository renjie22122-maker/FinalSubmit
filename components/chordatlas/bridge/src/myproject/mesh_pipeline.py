"""Safe adapter for inspecting OBJ files and invoking Sat3DGen's top-level pipeline.

The module intentionally uses only the Python standard library.  It does not
import Sat3DGen, activate or modify a conda environment, or contact a network.
Commands are represented as argument tuples and execution defaults to a dry
run, making the API suitable for a GUI process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import random
import re
import shlex
import subprocess
import sys
import time
from typing import Iterable, Mapping, Sequence


DEFAULT_Y_PERCENTILES = (0.0, 1.0, 5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 95.0, 99.0, 100.0)
DEFAULT_Y_SAMPLE_SIZE = 50_000


class MeshPipelineAdapterError(RuntimeError):
    """Base class for errors raised by this adapter."""


class ObjInspectionError(MeshPipelineAdapterError):
    """The OBJ cannot be read or contains an invalid relevant record."""


class TileOriginError(MeshPipelineAdapterError):
    """Sat3DGen tile stems cannot be converted into a geographic origin."""


class CommandBuildError(MeshPipelineAdapterError):
    """A top-level pipeline request is incomplete or unsafe."""


class CommandTimeoutError(MeshPipelineAdapterError):
    """The explicitly requested subprocess exceeded its timeout."""

    def __init__(self, message: str, *, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


class CommandExecutionError(MeshPipelineAdapterError):
    """A command could not start or returned a failure when ``check=True``."""

    def __init__(self, message: str, *, result: "PipelineCommandResult | None" = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class Bounds3D:
    """Exact axis-aligned bounds collected while streaming an OBJ."""

    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(hi - lo for lo, hi in zip(self.minimum, self.maximum))

    def to_dict(self) -> dict[str, list[float]]:
        return {"minimum": list(self.minimum), "maximum": list(self.maximum), "size": list(self.size)}


@dataclass(frozen=True)
class PercentileValue:
    percentile: float
    value: float

    def to_dict(self) -> dict[str, float]:
        return {"percentile": self.percentile, "value": self.value}


@dataclass(frozen=True)
class ObjInspection:
    """Structured summary of one OBJ file.

    ``face_count`` counts OBJ ``f`` records. ``triangulated_face_count`` is the
    number of triangles obtained by fan-triangulating each polygon. Bounds are
    exact; Y percentiles are exact only when all vertices fit in the reservoir.
    """

    path: Path
    vertex_count: int
    face_count: int
    triangulated_face_count: int
    material_libraries: tuple[str, ...]
    materials: tuple[str, ...]
    bounds: Bounds3D | None
    y_sample_size: int
    y_percentiles: tuple[PercentileValue, ...]

    @property
    def material_count(self) -> int:
        return len(self.materials)

    def percentile(self, percentile: float) -> float:
        wanted = float(percentile)
        for item in self.y_percentiles:
            if math.isclose(item.percentile, wanted, rel_tol=0.0, abs_tol=1e-12):
                return item.value
        raise KeyError(f"Y percentile {percentile!r} was not requested")

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "vertex_count": self.vertex_count,
            "face_count": self.face_count,
            "triangulated_face_count": self.triangulated_face_count,
            "material_count": self.material_count,
            "material_libraries": list(self.material_libraries),
            "materials": list(self.materials),
            "bounds": self.bounds.to_dict() if self.bounds else None,
            "y_sample_size": self.y_sample_size,
            "y_percentiles": [item.to_dict() for item in self.y_percentiles],
        }


def _normalise_percentiles(values: Sequence[float]) -> tuple[float, ...]:
    normalised: set[float] = set()
    for raw in values:
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid percentile {raw!r}") from exc
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ValueError(f"percentile must be finite and in [0, 100], got {raw!r}")
        normalised.add(value)
    if not normalised:
        raise ValueError("at least one Y percentile is required")
    return tuple(sorted(normalised))


def _percentile(sorted_values: Sequence[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("cannot calculate a percentile of an empty sample")
    rank = (percentile / 100.0) * (len(sorted_values) - 1)
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return float(sorted_values[lower])
    weight = rank - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def _obj_words(text: str) -> list[str]:
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    lexer.escape = ""  # Preserve Windows paths such as textures\brick.mtl.
    return list(lexer)


def _record_error(path: Path, line_number: int, message: str) -> ObjInspectionError:
    return ObjInspectionError(f"{path}:{line_number}: {message}")


def inspect_obj(
    obj_path: str | os.PathLike[str],
    *,
    y_percentiles: Sequence[float] = DEFAULT_Y_PERCENTILES,
    y_sample_size: int = DEFAULT_Y_SAMPLE_SIZE,
    random_seed: int = 0,
) -> ObjInspection:
    """Inspect an OBJ in one pass without loading its mesh into memory.

    A deterministic reservoir is used for Y percentiles. Vertex/face counts,
    bounds, ``mtllib`` records and ``usemtl`` names are always collected exactly.
    Malformed vertex and face records fail with a path and line number.
    """

    path = Path(obj_path).expanduser().resolve(strict=False)
    if not path.is_file():
        raise ObjInspectionError(f"OBJ file does not exist: {path}")
    if isinstance(y_sample_size, bool) or not isinstance(y_sample_size, int) or y_sample_size <= 0:
        raise ValueError("y_sample_size must be a positive integer")
    requested_percentiles = _normalise_percentiles(y_percentiles)

    vertex_count = 0
    face_count = 0
    triangulated_face_count = 0
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    y_sample: list[float] = []
    rng = random.Random(random_seed)
    libraries: list[str] = []
    library_seen: set[str] = set()
    materials: list[str] = []
    material_seen: set[str] = set()

    try:
        with path.open("r", encoding="utf-8-sig", errors="strict") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                stripped = raw_line.lstrip()
                if not stripped or stripped.startswith("#"):
                    continue
                head = stripped.split(maxsplit=1)
                record = head[0]
                rest = head[1] if len(head) == 2 else ""

                if record == "v":
                    values = rest.split("#", 1)[0].split()
                    if len(values) < 3:
                        raise _record_error(path, line_number, "vertex has fewer than three coordinates")
                    try:
                        x, y, z = (float(values[index]) for index in range(3))
                    except (ValueError, OverflowError) as exc:
                        raise _record_error(path, line_number, "vertex coordinates are not valid numbers") from exc
                    if not all(math.isfinite(value) for value in (x, y, z)):
                        raise _record_error(path, line_number, "vertex coordinates must be finite")

                    vertex_count += 1
                    for index, value in enumerate((x, y, z)):
                        minimum[index] = min(minimum[index], value)
                        maximum[index] = max(maximum[index], value)
                    if len(y_sample) < y_sample_size:
                        y_sample.append(y)
                    else:
                        replacement = rng.randrange(vertex_count)
                        if replacement < y_sample_size:
                            y_sample[replacement] = y

                elif record == "f":
                    tokens = rest.split("#", 1)[0].split()
                    if len(tokens) < 3:
                        raise _record_error(path, line_number, "face has fewer than three vertices")
                    for token in tokens:
                        index_text = token.split("/", 1)[0]
                        try:
                            index = int(index_text)
                        except ValueError as exc:
                            raise _record_error(path, line_number, f"invalid face index {index_text!r}") from exc
                        if index == 0:
                            raise _record_error(path, line_number, "OBJ face indices are one-based; zero is invalid")
                        if index > vertex_count or (index < 0 and -index > vertex_count):
                            raise _record_error(
                                path,
                                line_number,
                                f"face index {index} refers outside the {vertex_count} vertices read so far",
                            )
                    face_count += 1
                    triangulated_face_count += len(tokens) - 2

                elif record == "mtllib":
                    try:
                        names = _obj_words(rest)
                    except ValueError as exc:
                        raise _record_error(path, line_number, f"invalid mtllib record: {exc}") from exc
                    if not names:
                        raise _record_error(path, line_number, "mtllib has no filename")
                    for name in names:
                        if name not in library_seen:
                            library_seen.add(name)
                            libraries.append(name)

                elif record == "usemtl":
                    try:
                        words = _obj_words(rest)
                    except ValueError as exc:
                        raise _record_error(path, line_number, f"invalid usemtl record: {exc}") from exc
                    if not words:
                        raise _record_error(path, line_number, "usemtl has no material name")
                    name = " ".join(words)
                    if name not in material_seen:
                        material_seen.add(name)
                        materials.append(name)
    except ObjInspectionError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ObjInspectionError(f"failed to read OBJ {path}: {exc}") from exc

    bounds = None
    percentile_values: tuple[PercentileValue, ...] = ()
    if vertex_count:
        bounds = Bounds3D(tuple(minimum), tuple(maximum))
        ordered_sample = sorted(y_sample)
        percentile_values = tuple(
            PercentileValue(value, _percentile(ordered_sample, value))
            for value in requested_percentiles
        )

    return ObjInspection(
        path=path,
        vertex_count=vertex_count,
        face_count=face_count,
        triangulated_face_count=triangulated_face_count,
        material_libraries=tuple(libraries),
        materials=tuple(materials),
        bounds=bounds,
        y_sample_size=len(y_sample),
        y_percentiles=percentile_values,
    )


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_TILE_RE = re.compile(rf"^sat_({_NUMBER})_({_NUMBER})$", re.IGNORECASE)
_KNOWN_TILE_SUFFIXES = (
    ".obj.json",
    ".png.json",
    ".jpg.json",
    ".jpeg.json",
    ".obj",
    ".png",
    ".jpg",
    ".jpeg",
)


@dataclass(frozen=True)
class TileCoordinate:
    stem: str
    latitude: float
    longitude: float

    def to_dict(self) -> dict[str, object]:
        return {"stem": self.stem, "latitude": self.latitude, "longitude": self.longitude}


@dataclass(frozen=True)
class TileOrigin:
    """The horizontal origin convention used by top-level mesh_merging.py."""

    origin_latitude: float
    origin_longitude: float
    tiles: tuple[TileCoordinate, ...]

    @property
    def tile_count(self) -> int:
        return len(self.tiles)

    def to_dict(self) -> dict[str, object]:
        return {
            "origin_latitude": self.origin_latitude,
            "origin_longitude": self.origin_longitude,
            "tile_count": self.tile_count,
            "tiles": [tile.to_dict() for tile in self.tiles],
        }


def _without_known_tile_suffix(value: str | os.PathLike[str]) -> str:
    name = os.fspath(value).replace("\\", "/").rsplit("/", 1)[-1]
    lower = name.lower()
    for suffix in _KNOWN_TILE_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name


def derive_tile_origin(
    tile_stems: Iterable[str | os.PathLike[str]] | str | os.PathLike[str],
) -> TileOrigin:
    """Derive ``min(latitude), min(longitude)`` from Sat3DGen tile names.

    Accepted inputs include stems and common cache filenames, for example
    ``sat_51.507180_-0.125568`` and ``sat_51.507180_-0.125568.obj``.
    The minimum values are independent, matching top-level ``load_and_merge_tiles``.
    """

    if isinstance(tile_stems, (str, os.PathLike)):
        candidates = [tile_stems]
    else:
        candidates = list(tile_stems)
    if not candidates:
        raise TileOriginError("at least one Sat3DGen tile stem is required")

    tiles: list[TileCoordinate] = []
    for raw in candidates:
        stem = _without_known_tile_suffix(raw)
        match = _TILE_RE.fullmatch(stem)
        if match is None:
            raise TileOriginError(
                f"invalid Sat3DGen tile stem {os.fspath(raw)!r}; "
                "expected sat_<latitude>_<longitude>"
            )
        latitude, longitude = (float(match.group(index)) for index in (1, 2))
        if not math.isfinite(latitude) or not -90.0 <= latitude <= 90.0:
            raise TileOriginError(f"tile latitude is outside [-90, 90]: {latitude!r}")
        if not math.isfinite(longitude) or not -180.0 <= longitude <= 180.0:
            raise TileOriginError(f"tile longitude is outside [-180, 180]: {longitude!r}")
        tiles.append(TileCoordinate(stem, latitude, longitude))

    return TileOrigin(
        origin_latitude=min(tile.latitude for tile in tiles),
        origin_longitude=min(tile.longitude for tile in tiles),
        tiles=tuple(tiles),
    )


@dataclass(frozen=True)
class GeoBBox:
    """WGS84 bounds in CLI order: min lon, min lat, max lon, max lat."""

    min_longitude: float
    min_latitude: float
    max_longitude: float
    max_latitude: float

    def __post_init__(self) -> None:
        values = tuple(float(value) for value in (
            self.min_longitude,
            self.min_latitude,
            self.max_longitude,
            self.max_latitude,
        ))
        for name, value in zip(
            ("min_longitude", "min_latitude", "max_longitude", "max_latitude"), values
        ):
            object.__setattr__(self, name, value)
            if not math.isfinite(value):
                raise CommandBuildError(f"bbox {name} must be finite")
        if not -180.0 <= self.min_longitude < self.max_longitude <= 180.0:
            raise CommandBuildError("bbox longitudes must satisfy -180 <= min < max <= 180")
        if not -85.05112878 <= self.min_latitude < self.max_latitude <= 85.05112878:
            raise CommandBuildError(
                "bbox latitudes must satisfy -85.05112878 <= min < max <= 85.05112878"
            )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_longitude, self.min_latitude, self.max_longitude, self.max_latitude)


@dataclass(frozen=True)
class TopLevelPipelineRequest:
    bbox: GeoBBox
    work_dir: Path
    sat3dgen_root: Path
    driver_path: Path
    name: str = "region"
    conda_environment: str = "sat3dgen"
    conda_executable: str = "conda"
    satellite_dirs: tuple[Path, ...] = ()
    mesh_dirs: tuple[Path, ...] = ()
    osm_dir: Path | None = None
    dsm_dir: Path | None = None
    dsm_files: tuple[str, ...] = ()
    dsm_crs: str = "EPSG:27700"
    tile_source: str = "discovered"
    lat_step: float | None = None
    lon_step: float | None = None
    overlap_ratio: float | None = None
    download_missing: bool = False
    run_inference: bool = False
    allow_partial: bool = False
    osm_prealign: bool = False
    apply_dsm: bool = False
    keep_bottom: bool = False
    no_stitch: bool = False
    zoom: int = 20
    tile_size: int = 640
    crop_ratio: float | None = None
    mesh_resolution: int = 256
    gradio_url: str = "http://localhost:7860"
    plan_only: bool = False
    exact_tile_manifest: Path | None = None
    use_current_python: bool = False
    pipeline_contract_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.bbox, GeoBBox):
            raise CommandBuildError("bbox must be a GeoBBox")
        object.__setattr__(self, "work_dir", Path(self.work_dir).expanduser().resolve(strict=False))
        object.__setattr__(
            self, "sat3dgen_root", Path(self.sat3dgen_root).expanduser().resolve(strict=False)
        )
        object.__setattr__(self, "driver_path", Path(self.driver_path).expanduser().resolve(strict=False))
        if self.exact_tile_manifest is not None:
            object.__setattr__(
                self,
                "exact_tile_manifest",
                Path(self.exact_tile_manifest).expanduser().resolve(strict=False),
            )
        object.__setattr__(
            self,
            "satellite_dirs",
            tuple(Path(path).expanduser().resolve(strict=False) for path in self.satellite_dirs),
        )
        object.__setattr__(
            self,
            "mesh_dirs",
            tuple(Path(path).expanduser().resolve(strict=False) for path in self.mesh_dirs),
        )
        if self.osm_dir is not None:
            object.__setattr__(self, "osm_dir", Path(self.osm_dir).expanduser().resolve(strict=False))
        if self.dsm_dir is not None:
            object.__setattr__(self, "dsm_dir", Path(self.dsm_dir).expanduser().resolve(strict=False))
        dsm_files = tuple(str(name) for name in self.dsm_files)
        if any(
            not name
            or Path(name).name != name
            or Path(name).suffix.lower() not in {".tif", ".tiff"}
            for name in dsm_files
        ):
            raise CommandBuildError("dsm_files must contain GeoTIFF basenames only")
        object.__setattr__(self, "dsm_files", dsm_files)
        object.__setattr__(self, "dsm_crs", str(self.dsm_crs).upper())
        if self.apply_dsm:
            if self.dsm_dir is None or not self.dsm_files:
                raise CommandBuildError("apply_dsm requires dsm_dir and explicit dsm_files")
            if self.osm_dir is None:
                raise CommandBuildError("apply_dsm requires osm_dir for semantic correction")
            if self.dsm_crs != "EPSG:27700":
                raise CommandBuildError("apply_dsm currently requires dsm_crs EPSG:27700")
        name = str(self.name).strip()
        if not name or any(character in name for character in ("\0", "\r", "\n")):
            raise CommandBuildError("name must be non-empty and contain no control characters")
        object.__setattr__(self, "name", name)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.conda_environment):
            raise CommandBuildError("conda_environment contains unsupported characters")
        if not str(self.conda_executable).strip():
            raise CommandBuildError("conda_executable must not be empty")
        if self.tile_source not in {"discovered", "data_builder_grid", "top_grid", "exact_manifest"}:
            raise CommandBuildError(
                "tile_source must be discovered, data_builder_grid, top_grid or exact_manifest"
            )
        if self.tile_source == "exact_manifest" and self.exact_tile_manifest is None:
            raise CommandBuildError("tile_source exact_manifest requires exact_tile_manifest")
        if self.exact_tile_manifest is not None and self.tile_source != "exact_manifest":
            raise CommandBuildError("exact_tile_manifest requires tile_source exact_manifest")
        if not isinstance(self.use_current_python, bool):
            raise CommandBuildError("use_current_python must be boolean")
        if self.pipeline_contract_version is not None and not re.fullmatch(
            r"[A-Za-z0-9_.-]+", self.pipeline_contract_version
        ):
            raise CommandBuildError("pipeline_contract_version contains unsupported characters")
        for label, value in (("lat_step", self.lat_step), ("lon_step", self.lon_step)):
            if value is not None and (not math.isfinite(float(value)) or float(value) <= 0):
                raise CommandBuildError(f"{label} must be a positive finite value")
        if self.overlap_ratio is not None and (
            not math.isfinite(float(self.overlap_ratio)) or not 0 <= float(self.overlap_ratio) < 1
        ):
            raise CommandBuildError("overlap_ratio must be finite and in [0, 1)")
        for label, value in (("zoom", self.zoom), ("tile_size", self.tile_size), ("mesh_resolution", self.mesh_resolution)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise CommandBuildError(f"{label} must be a positive integer")
        if self.crop_ratio is not None and (
            not math.isfinite(float(self.crop_ratio)) or not 0 <= float(self.crop_ratio) < 0.5
        ):
            raise CommandBuildError("crop_ratio must be finite and in [0, 0.5)")


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path

    @property
    def display(self) -> str:
        """Return a Windows-safe display string; execution still uses ``argv``."""

        return subprocess.list2cmdline(list(self.argv))

    def to_dict(self) -> dict[str, object]:
        return {"argv": list(self.argv), "cwd": str(self.cwd), "display": self.display}


def _format_cli_float(value: float) -> str:
    return format(0.0 if value == 0.0 else value, ".15g")


def build_top_level_conda_command(request: TopLevelPipelineRequest) -> CommandSpec:
    """Build the myProject driver invocation for top-level mesh_pipeline modules.

    API keys are deliberately not accepted here. The child process can inherit
    ``GOOGLE_MAPS_API_KEY`` when the caller explicitly executes the command.
    """

    if not isinstance(request, TopLevelPipelineRequest):
        raise CommandBuildError("request must be a TopLevelPipelineRequest")
    if not request.sat3dgen_root.is_dir():
        raise CommandBuildError(f"Sat3DGen root directory does not exist: {request.sat3dgen_root}")
    if not (request.sat3dgen_root / "mesh_pipeline" / "pipeline.py").is_file():
        raise CommandBuildError("Sat3DGen top-level mesh_pipeline/pipeline.py was not found")
    if not request.driver_path.is_file():
        raise CommandBuildError(f"myProject top-level driver does not exist: {request.driver_path}")
    if request.exact_tile_manifest is not None and not request.exact_tile_manifest.is_file():
        raise CommandBuildError(
            f"exact tile manifest does not exist: {request.exact_tile_manifest}"
        )

    argv = (
        [sys.executable, "-B", str(request.driver_path)]
        if request.use_current_python
        else [
            str(request.conda_executable),
            "run",
            "--no-capture-output",
            "-n",
            request.conda_environment,
            "python",
            "-B",
            str(request.driver_path),
        ]
    )
    argv.extend([
        "--sat3dgen-root",
        str(request.sat3dgen_root),
        "--bbox",
        *(_format_cli_float(value) for value in request.bbox.as_tuple()),
        "--name",
        request.name,
        "--work-dir",
        str(request.work_dir),
        "--tile-source",
        request.tile_source,
        "--zoom",
        str(request.zoom),
        "--tile-size",
        str(request.tile_size),
        "--mesh-resolution",
        str(request.mesh_resolution),
        "--gradio-url",
        request.gradio_url,
    ])
    for directory in request.satellite_dirs:
        argv.extend(("--satellite-dir", str(directory)))
    for directory in request.mesh_dirs:
        argv.extend(("--mesh-dir", str(directory)))
    if request.osm_dir is not None:
        argv.extend(("--osm-dir", str(request.osm_dir)))
    if request.dsm_dir is not None:
        argv.extend(("--dsm-dir", str(request.dsm_dir)))
    for name in request.dsm_files:
        argv.extend(("--dsm-file", name))
    if request.dsm_files:
        argv.extend(("--dsm-crs", request.dsm_crs))
    if request.lat_step is not None:
        argv.extend(("--lat-step", _format_cli_float(request.lat_step)))
    if request.lon_step is not None:
        argv.extend(("--lon-step", _format_cli_float(request.lon_step)))
    if request.overlap_ratio is not None:
        argv.extend(("--overlap-ratio", _format_cli_float(request.overlap_ratio)))
    if request.crop_ratio is not None:
        argv.extend(("--crop-ratio", _format_cli_float(request.crop_ratio)))
    if request.exact_tile_manifest is not None:
        argv.extend(("--exact-tile-manifest", str(request.exact_tile_manifest)))
    if request.pipeline_contract_version is not None:
        argv.extend(("--pipeline-contract-version", request.pipeline_contract_version))
    flags = (
        (request.download_missing, "--download-missing"),
        (request.run_inference, "--run-inference"),
        (request.allow_partial, "--allow-partial"),
        (request.osm_prealign, "--osm-prealign"),
        (request.apply_dsm, "--apply-dsm"),
        (request.keep_bottom, "--keep-bottom"),
        (request.no_stitch, "--no-stitch"),
        (request.plan_only, "--plan-only"),
    )
    argv.extend(flag for enabled, flag in flags if enabled)
    return CommandSpec(tuple(argv), request.sat3dgen_root)


_OUTPUT_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$")


def _parse_pipeline_stdout(stdout: str, cwd: Path) -> tuple[tuple[tuple[str, Path], ...], tuple[str, ...]]:
    outputs: list[tuple[str, Path]] = []
    warnings: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.lower().startswith("warning:"):
            warnings.append(line.split(":", 1)[1].strip())
            continue
        match = _OUTPUT_LINE_RE.fullmatch(line)
        if match is None:
            continue
        key, raw_path = match.groups()
        path = Path(raw_path.strip().strip('"'))
        if not path.is_absolute():
            path = (cwd / path).resolve(strict=False)
        outputs.append((key, path))
    return tuple(outputs), tuple(warnings)


@dataclass(frozen=True)
class PipelineCommandResult:
    command: CommandSpec
    executed: bool
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    outputs: tuple[tuple[str, Path], ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.executed or self.returncode == 0

    @property
    def output_files(self) -> dict[str, Path]:
        return dict(self.outputs)

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command.to_dict(),
            "executed": self.executed,
            "returncode": self.returncode,
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "outputs": {key: str(path) for key, path in self.outputs},
            "warnings": list(self.warnings),
        }


def execute_pipeline_command(
    command: CommandSpec,
    *,
    dry_run: bool = True,
    timeout: float | None = None,
    environment: Mapping[str, str] | None = None,
    check: bool = False,
) -> PipelineCommandResult:
    """Optionally execute a command; by default return a dry-run result.

    Execution uses ``shell=False``. A non-zero exit is returned structurally
    unless ``check=True``. Missing executables and timeouts always raise a clear
    adapter exception because no completed process result exists.
    """

    if not isinstance(command, CommandSpec) or not command.argv:
        raise CommandBuildError("command must be a non-empty CommandSpec")
    if dry_run:
        return PipelineCommandResult(command=command, executed=False, returncode=None)
    if not command.cwd.is_dir():
        raise CommandExecutionError(f"command working directory does not exist: {command.cwd}")
    if timeout is not None and (not math.isfinite(float(timeout)) or float(timeout) <= 0):
        raise ValueError("timeout must be a positive finite number")

    child_environment = os.environ.copy()
    if environment:
        for raw_key, raw_value in environment.items():
            key, value = str(raw_key), str(raw_value)
            if not key or "=" in key or "\0" in key or "\0" in value:
                raise ValueError(f"invalid environment entry {raw_key!r}")
            child_environment[key] = value

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(command.argv),
            cwd=str(command.cwd),
            env=child_environment,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CommandExecutionError(
            f"could not start {command.argv[0]!r}; ensure conda is installed and on PATH"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        raise CommandTimeoutError(
            f"top-level pipeline command exceeded timeout {timeout}s", stdout=stdout, stderr=stderr
        ) from exc
    except OSError as exc:
        raise CommandExecutionError(f"could not start top-level pipeline command: {exc}") from exc

    duration = time.perf_counter() - started
    outputs, warnings = _parse_pipeline_stdout(completed.stdout or "", command.cwd)
    result = PipelineCommandResult(
        command=command,
        executed=True,
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        duration_seconds=duration,
        outputs=outputs,
        warnings=warnings,
    )
    if check and not result.ok:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise CommandExecutionError(
            f"top-level pipeline failed with exit code {result.returncode}: {detail}", result=result
        )
    return result


def run_top_level_pipeline(
    request: TopLevelPipelineRequest,
    *,
    dry_run: bool = True,
    timeout: float | None = None,
    environment: Mapping[str, str] | None = None,
    check: bool = False,
) -> PipelineCommandResult:
    """Build and optionally execute the top-level compatibility driver."""

    return execute_pipeline_command(
        build_top_level_conda_command(request),
        dry_run=dry_run,
        timeout=timeout,
        environment=environment,
        check=check,
    )


__all__ = [
    "Bounds3D",
    "CommandBuildError",
    "CommandExecutionError",
    "CommandSpec",
    "CommandTimeoutError",
    "GeoBBox",
    "MeshPipelineAdapterError",
    "ObjInspection",
    "ObjInspectionError",
    "PercentileValue",
    "PipelineCommandResult",
    "TileCoordinate",
    "TileOrigin",
    "TileOriginError",
    "TopLevelPipelineRequest",
    "build_top_level_conda_command",
    "derive_tile_origin",
    "execute_pipeline_command",
    "inspect_obj",
    "run_top_level_pipeline",
]
