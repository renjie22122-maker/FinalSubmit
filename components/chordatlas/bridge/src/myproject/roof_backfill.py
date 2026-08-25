"""Offline, roof-directory-only backfill for a READY selection cache."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

from .roof_reference import generate_roof_references
from .selection import (
    BUILDING_PUBLICATION_VERSION,
    PIPELINE_CONTRACT_VERSION,
    SelectionBridgeError,
    _read_json_object,
    load_selection_request,
)


BUILDINGS_INDEX_KIND = "myProject.selection.buildings"


def _fail(code: str, message: str) -> SelectionBridgeError:
    return SelectionBridgeError(code, message)


def _require(value: bool, message: str) -> None:
    if not value:
        raise _fail("invalid_cached_publication", message)


def _contained(root: Path, candidate: Path, label: str) -> Path:
    root = root.resolve(strict=True)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise _fail("invalid_cached_publication", f"{label} escapes {root}") from exc
    if candidate.is_symlink():
        raise _fail("invalid_cached_publication", f"{label} must not be a symbolic link")
    return resolved


def _validate_publication(publication: Path):
    root = publication.expanduser().resolve(strict=True)
    _require(root.is_dir(), f"cached publication is not a directory: {root}")
    result = _read_json_object(_contained(root, root / "result.json", "result.json"),
                               "cached selection result")
    index = _read_json_object(
        _contained(root, root / "buildings" / "index.json", "buildings/index.json"),
        "cached buildings index",
    )
    request = load_selection_request(_contained(root, root / "request.json", "request.json"))
    _require(request.output_dir.resolve(strict=False) == root, "request does not identify this publication")

    _require(result.get("schema_version") == 1, "selection result schema is not version 1")
    _require(result.get("kind") == "myProject.selection.result", "selection result kind is unsupported")
    _require(result.get("status") == "READY", "selection result is not READY")
    _require(result.get("building_publication_version") == BUILDING_PUBLICATION_VERSION,
             "selection result is not per-footprint-v2")
    _require(result.get("pipeline_contract_version") == PIPELINE_CONTRACT_VERSION,
             "selection pipeline contract is unsupported")
    _require(result.get("osm_prealign") is True, "selection did not use OSM prealignment")
    _require(result.get("selection_id") == request.selection_id
             and result.get("stable_id") == request.stable_id,
             "selection result identity disagrees with request")

    _require(index.get("schema_version") == 1, "buildings index schema is not version 1")
    _require(index.get("kind") == BUILDINGS_INDEX_KIND, "buildings index kind is unsupported")
    _require(index.get("status") == "READY", "buildings index is not READY")
    _require(index.get("building_publication_version") == BUILDING_PUBLICATION_VERSION,
             "buildings index is not per-footprint-v2")
    _require(index.get("pipeline_contract_version") == PIPELINE_CONTRACT_VERSION,
             "buildings index pipeline contract is unsupported")
    _require(index.get("selection_id") == request.selection_id
             and index.get("stable_id") == request.stable_id,
             "buildings index identity disagrees with request")
    result_entries = result.get("buildings")
    index_entries = index.get("buildings")
    _require(isinstance(result_entries, list) and result_entries == index_entries,
             "result.json and buildings/index.json disagree")
    _require(result.get("buildings_summary") == index.get("summary"),
             "result and index building summaries disagree")

    requested = {footprint.identifier: footprint for footprint in request.footprints}
    indexed_ids: set[str] = set()
    publishable = []
    for raw in index_entries:
        _require(isinstance(raw, dict), "buildings index contains a non-object entry")
        identifier = raw.get("id")
        _require(isinstance(identifier, str) and identifier == raw.get("footprint_id"),
                 "building id and footprint_id must match")
        _require(identifier in requested and identifier not in indexed_ids,
                 "building footprint identity is missing or duplicated")
        indexed_ids.add(identifier)
        expected_relative = f"buildings/{identifier}"
        _require(raw.get("relative_dir") == expected_relative,
                 f"building {identifier} has an unexpected relative_dir")
        if raw.get("publishable") is True:
            _require(raw.get("status") in {"READY", "COARSE_READY"},
                     f"building {identifier} has an invalid publishable status")
            building_root = _contained(root, root / expected_relative, f"building {identifier}")
            metadata = _read_json_object(
                _contained(building_root, building_root / "building.json",
                           f"building {identifier} metadata"),
                f"building {identifier} metadata",
            )
            _require(metadata == raw, f"building {identifier} metadata disagrees with the index")
            publishable.append(requested[identifier])
        else:
            _require(raw.get("status") in {"REJECTED", "EMPTY"},
                     f"building {identifier} has an invalid non-publishable status")
    _require(indexed_ids == set(requested), "request and buildings index footprint sets disagree")
    _require(bool(publishable), "cached publication contains no publishable building")

    job_root = request.workspace / "_selection_jobs" / request.stable_id
    tile_manifest_path = _contained(request.workspace, job_root / "tile_manifest.json", "tile manifest")
    tile_manifest = _read_json_object(tile_manifest_path, "exact cached tile manifest")
    _require(tile_manifest.get("schema_version") == 1, "tile manifest schema is not version 1")
    _require(tile_manifest.get("kind") == "myProject.selection.exact_tiles",
             "tile manifest kind is unsupported")
    _require(tile_manifest.get("status") == "READY", "tile manifest is not READY")
    _require(tile_manifest.get("selection_id") == request.selection_id
             and tile_manifest.get("stable_id") == request.stable_id,
             "tile manifest identity disagrees with request")
    _require(tile_manifest.get("pipeline_contract_version") == PIPELINE_CONTRACT_VERSION,
             "tile manifest pipeline contract is unsupported")
    _require(tile_manifest.get("osm_prealign") is True, "tile manifest did not use OSM prealignment")
    manifest_workspace = tile_manifest.get("workspace")
    _require(isinstance(manifest_workspace, str)
             and Path(manifest_workspace).expanduser().resolve(strict=False) == request.workspace,
             "tile manifest workspace disagrees with request")

    raw_tiles = tile_manifest.get("tiles")
    _require(isinstance(raw_tiles, list) and bool(raw_tiles), "tile manifest has no cached tiles")
    satellite_root = (job_root / "satellite").resolve(strict=False)
    for position, tile in enumerate(raw_tiles):
        _require(isinstance(tile, Mapping), f"tile manifest entry {position} is not an object")
        source = tile.get("satellite_path")
        _require(isinstance(source, str) and bool(source),
                 f"tile manifest entry {position} has no satellite_path")
        source_path = Path(source).expanduser()
        if not source_path.is_absolute():
            source_path = job_root / source_path
        try:
            source_path.resolve(strict=False).relative_to(satellite_root)
        except ValueError as exc:
            raise _fail("invalid_cached_publication",
                        f"tile manifest entry {position} escapes the exact satellite cache") from exc
        if source_path.exists() and source_path.is_symlink():
            raise _fail("invalid_cached_publication",
                        f"tile manifest entry {position} must not be a symbolic link")
    return root, request, publishable, tile_manifest_path, job_root


def _try_lock(handle) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _selection_lock(path: Path, timeout: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + max(0.0, float(timeout))
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise _fail("roof_backfill_locked", "another process is backfilling this selection")
            time.sleep(0.1)
        try:
            yield
        finally:
            _unlock(handle)


def backfill_cached_roof_references(
    publication_dir: str | os.PathLike[str],
    *,
    padding_m: float = 2.0,
    erosion_m: float = 0.5,
    minimum_source_coverage: float = 0.95,
    minimum_mask_pixels: int = 16,
    max_dimension_px: int = 2048,
    lock_timeout: float = 120.0,
) -> dict[str, Any]:
    """Build only per-building ``references/roof`` directories, with no I/O elsewhere."""

    root, request, footprints, tile_manifest, job_root = _validate_publication(Path(publication_dir))
    metadata_paths = [root / "result.json", root / "buildings" / "index.json"] + [
        root / "buildings" / footprint.identifier / "building.json" for footprint in footprints
    ]
    metadata_before = {path: path.read_bytes() for path in metadata_paths}
    with _selection_lock(job_root / ".roof-reference-backfill.lock", lock_timeout):
        # Revalidate after acquiring the cross-process lock, so a waiting
        # process cannot act on a publication changed by the prior owner.
        root, request, footprints, tile_manifest, _ = _validate_publication(root)
        report = generate_roof_references(
            tile_manifest_path=tile_manifest,
            buildings_root=root / "buildings",
            footprints=footprints,
            frame=request.frame,
            cache_buildings_root=root / "buildings",
            padding_m=padding_m,
            erosion_m=erosion_m,
            minimum_source_coverage=minimum_source_coverage,
            minimum_mask_pixels=minimum_mask_pixels,
            max_dimension_px=max_dimension_px,
        )
    if any(path.read_bytes() != payload for path, payload in metadata_before.items()):
        raise _fail("publication_metadata_changed", "roof-only backfill modified geometry metadata")
    return {
        "schema_version": 1,
        "kind": "myProject.selection.roof_reference_backfill",
        "status": report["status"],
        "selection_id": request.selection_id,
        "publication": str(root),
        "source": "exact_cached_satellite_tiles",
        "network_used": False,
        "gpu_used": False,
        "geometry_modified": False,
        "publication_metadata_modified": False,
        "requested": report["requested"],
        "ready": report["ready"],
        "unavailable": report["unavailable"],
        "buildings": report["buildings"],
    }


__all__ = ["backfill_cached_roof_references"]
