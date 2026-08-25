from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import ConfigError, load_config
from .doctor import run_doctor
from .dsm_footprints import extract_dsm_footprints
from .launch import launch_frankengan, launch_gui
from .mesh_pipeline import inspect_obj
from .panoramas import prepare_panoramas
from .selection import (
    BIG_IMAGE_PIPELINE_CONTRACT_VERSION,
    SelectionBridgeError,
    build_selection,
)
from .validate import validate_workspace
from .workspace import (
    WorkspaceError,
    build_workspace,
    mesh_plan,
    run_configured_mesh,
    scan_configured_mesh,
)


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "data_builder_london_smoke.json"


def _json_default(value: Any):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"cannot encode {type(value).__name__}")


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myproject",
        description="Windows data_builder/Sat3DGen/ChordAtlas integration",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"project JSON (default: {DEFAULT_CONFIG})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("plan", help="show paths, geographic range and mesh command without changing data")
    doctor = sub.add_parser("doctor", help="check Java, conda sat3dgen, top-level mesh_pipeline and external projects")
    doctor.add_argument("--no-probes", action="store_true", help="perform file checks only")

    run_mesh = sub.add_parser("run-mesh", help="plan or execute the Sat3DGen top-level mesh_pipeline adapter")
    run_mode = run_mesh.add_mutually_exclusive_group()
    run_mode.add_argument("--execute", action="store_true", help="actually generate; default is dry-run")
    run_mode.add_argument(
        "--scan-inputs",
        action="store_true",
        help="scan the exact tile plan and write missing-input details without network or inference",
    )
    run_mesh.add_argument("--timeout", type=float, default=None, help="execution timeout in seconds")

    build = sub.add_parser("build", help="create a complete ChordAtlas workspace")
    build.add_argument("--force", action="store_true", help="retain the previous generated workspace as a backup")
    build.add_argument("--run-mesh", action="store_true", help="execute top-level mesh_pipeline first when mesh.mode=generate")
    build.add_argument("--mesh-timeout", type=float, default=None)

    validate = sub.add_parser("validate", help="validate GIS, mini-mesh, panos, tweed.xml and JAR")
    validate.add_argument("--workspace", type=Path, default=None)

    launch = sub.add_parser("launch", help="validate and launch the selected ChordAtlas workspace")
    launch.add_argument("--workspace", type=Path, default=None)
    launch.add_argument("--dry-run", action="store_true")

    franken = sub.add_parser("start-frankengan", help="start the optional network-texture watcher")
    franken.add_argument("--workspace", type=Path, default=None)
    franken.add_argument("--execute", action="store_true", help="actually start; default only prints the command")

    panos = sub.add_parser("prepare-panos", help="import explicitly licensed 2:1 equirectangular JPGs")
    panos.add_argument("--manifest", type=Path, required=True)
    panos.add_argument("--output", type=Path, required=True)
    panos.add_argument("--report", type=Path, default=None)

    streetview = sub.add_parser(
        "import-streetview-panos",
        aliases=["streetview-panos"],
        help="guarded Static Street View import into a ChordAtlas panorama folder",
    )
    streetview.add_argument("--todo", type=Path, required=True, help="ChordAtlas todo.list manifest")
    streetview.add_argument(
        "--output",
        "--output-dir",
        dest="output_dir",
        type=Path,
        default=None,
        help="defaults to <configured workspace>/panos",
    )
    streetview.add_argument("--report", type=Path, default=None)
    streetview.add_argument("--limit", type=int, default=1)
    streetview.add_argument("--all", action="store_true", help="batch after an approved sample")
    streetview.add_argument("--sample-report", type=Path, default=None)
    streetview.add_argument("--sample-approved", action="store_true")
    streetview.add_argument("--dry-run", action="store_true", help="no API calls; no key required")
    streetview.add_argument("--output-width", type=int, default=2560)
    streetview.add_argument("--jpeg-quality", type=int, default=95)
    streetview.add_argument("--radius", type=int, default=50)
    streetview.add_argument("--timeout", type=float, default=30.0)
    streetview.add_argument("--retries", type=int, default=2)
    streetview.add_argument("--nearest", action="store_true")
    streetview.add_argument("--overwrite", action="store_true")
    streetview.add_argument("--keep-panos-cache", action="store_true")
    streetview.add_argument(
        "--coordinate-mode",
        choices=("myproject-local", "original-geographic"),
        default="myproject-local",
        help="default local frame uses heading 180; original geographic mode uses heading 0",
    )

    block_panos = sub.add_parser(
        "prepare-block-panos",
        help="derive a deduplicated Street View todo.list from the current OSM block",
    )
    block_panos.add_argument("--request", type=Path, required=True)
    block_panos.add_argument(
        "--todo", type=Path, required=True, help="selection-scoped todo.list beside the request"
    )
    block_panos.add_argument("--report", type=Path, default=None)
    block_panos.add_argument("--spacing", type=float, default=18.0)
    block_panos.add_argument("--offset", type=float, default=8.0)
    block_panos.add_argument("--max-seeds", type=int, default=24)
    block_panos.add_argument("--radius", type=int, default=50)
    block_panos.add_argument("--timeout", type=float, default=30.0)
    block_panos.add_argument("--retries", type=int, default=2)
    block_panos.add_argument("--dry-run", action="store_true")

    promote_panos = sub.add_parser(
        "promote-block-panos",
        help="promote a scoped panorama plan after its approved batch succeeds",
    )
    promote_panos.add_argument("--request", type=Path, required=True)
    promote_panos.add_argument("--todo", type=Path, required=True)
    promote_panos.add_argument("--plan-report", type=Path, required=True)
    promote_panos.add_argument("--batch-report", type=Path, required=True)
    promote_panos.add_argument("--report", type=Path, required=True)

    dsm = sub.add_parser("extract-dsm", help="extract candidate footprint GeoJSON from DSM/optional DTM")
    dsm.add_argument("--dsm", type=Path, nargs="+", required=True)
    dsm.add_argument("--dtm", type=Path, nargs="*", default=None)
    dsm.add_argument("--bbox", type=float, nargs=4, metavar=("MIN_LON", "MIN_LAT", "MAX_LON", "MAX_LAT"))
    dsm.add_argument("--output", type=Path, required=True)
    dsm.add_argument("--min-height", type=float, default=3.0)
    dsm.add_argument("--min-area", type=float, default=20.0)
    dsm.add_argument("--ground-window", type=float, default=35.0)

    inspect = sub.add_parser("inspect-obj", help="stream-inspect an OBJ without loading it into memory")
    inspect.add_argument("obj", type=Path)
    inspect.add_argument("--percentiles", type=float, nargs="+", default=[0, 2, 50, 98, 100])

    selection = sub.add_parser(
        "build-selection",
        help="plan or build an exact satellite/mesh job for GUI-selected footprints",
    )
    selection.add_argument("--request", type=Path, required=True)
    selection.add_argument(
        "--execute",
        action="store_true",
        help="download, infer, validate and publish; default only freezes the tile plan",
    )

    roof_backfill = sub.add_parser(
        "backfill-roof-references",
        help="offline appearance-only backfill for an existing per-footprint-v2 READY cache",
    )
    roof_backfill.add_argument("--publication", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare-panos":
            report = prepare_panoramas(args.manifest, args.output, args.report)
            _emit(report)
            return 0 if report.get("status") == "ok" else 2
        if args.command in {"import-streetview-panos", "streetview-panos"}:
            from .streetview_panos import import_streetview_panos

            output_dir = args.output_dir
            if output_dir is None:
                output_dir = load_config(args.config).workspace / "panos"
            report = import_streetview_panos(
                todo_path=args.todo,
                output_dir=output_dir,
                report_path=args.report,
                limit=args.limit,
                all_records=args.all,
                sample_report=args.sample_report,
                sample_approved=args.sample_approved,
                dry_run=args.dry_run,
                output_width=args.output_width,
                jpeg_quality=args.jpeg_quality,
                radius=args.radius,
                timeout=args.timeout,
                retries=args.retries,
                nearest=args.nearest,
                overwrite=args.overwrite,
                keep_panos_cache=args.keep_panos_cache,
                coordinate_mode=args.coordinate_mode,
            )
            _emit(report)
            return 0 if not report["summary"]["failed"] else 2
        if args.command == "prepare-block-panos":
            from .block_panos import prepare_block_panos

            report = prepare_block_panos(
                args.request,
                todo_path=args.todo,
                report_path=args.report,
                spacing_m=args.spacing,
                offset_m=args.offset,
                max_seeds=args.max_seeds,
                radius=args.radius,
                timeout=args.timeout,
                retries=args.retries,
                dry_run=args.dry_run,
            )
            _emit(report)
            return 0 if report.get("status") in {"PLANNED", "READY"} else 2
        if args.command == "promote-block-panos":
            from .block_panos import promote_block_panos

            report = promote_block_panos(
                args.request,
                todo_path=args.todo,
                plan_report_path=args.plan_report,
                batch_report_path=args.batch_report,
                report_path=args.report,
            )
            _emit(report)
            return 0 if report.get("status") in {"PROMOTED", "UNCHANGED"} else 2
        if args.command == "inspect-obj":
            _emit(inspect_obj(args.obj, y_percentiles=args.percentiles).to_dict())
            return 0
        if args.command == "build-selection":
            try:
                request_document = json.loads(args.request.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SelectionBridgeError(
                    "invalid_request", f"cannot inspect selection request {args.request}: {exc}"
                ) from exc
            options = request_document.get("options", {}) if isinstance(request_document, dict) else {}
            model_source = str(options.get("model_source", "")).strip().lower() if isinstance(options, dict) else ""
            contract = str(options.get("pipeline_contract_version", "")).strip() if isinstance(options, dict) else ""
            if model_source == "big_image" or contract == BIG_IMAGE_PIPELINE_CONTRACT_VERSION:
                from .big_image_selection import build_big_image_selection

                report = build_big_image_selection(args.request, execute=args.execute)
            else:
                report = build_selection(args.request, execute=args.execute)
            _emit(report)
            return 0 if report.get("status") in {"PLANNED", "READY"} else 2
        if args.command == "backfill-roof-references":
            from .roof_backfill import backfill_cached_roof_references

            report = backfill_cached_roof_references(args.publication)
            _emit(report)
            # Missing imagery is an appearance-level UNAVAILABLE result, not a
            # failed geometry job.  A nonzero exit is reserved for a rejected
            # cache contract or an interrupted metadata transaction.
            return 0

        config = load_config(args.config)
        if args.command == "plan":
            _emit(
                {
                    "project_id": config.project_id,
                    "workspace": str(config.workspace),
                    "target_bbox_wgs84": list(config.target_bbox),
                    "fetch_bbox_wgs84": list(config.fetch_bbox),
                    "paths": config.path_report(),
                    "mesh": mesh_plan(config),
                    "panoramas_enabled": bool(config.panoramas.get("enabled", False)),
                }
            )
            return 0
        if args.command == "doctor":
            report = run_doctor(config, execute_probes=not args.no_probes)
            _emit(report)
            return 0 if report["ok"] else 2
        if args.command == "run-mesh":
            result = (
                scan_configured_mesh(config, timeout=args.timeout)
                if args.scan_inputs
                else run_configured_mesh(config, execute=args.execute, timeout=args.timeout)
            )
            _emit(result.to_dict())
            return 0 if result.ok else 2
        if args.command == "build":
            _emit(
                build_workspace(
                    config,
                    force=args.force,
                    run_generation=args.run_mesh,
                    generation_timeout=args.mesh_timeout,
                )
            )
            return 0
        if args.command == "validate":
            report = validate_workspace(args.workspace or config.workspace)
            _emit(report)
            return 0 if report["status"] == "ok" else 2
        if args.command == "launch":
            _emit(launch_gui(config, args.workspace, dry_run=args.dry_run))
            return 0
        if args.command == "start-frankengan":
            _emit(launch_frankengan(config, args.workspace, dry_run=not args.execute))
            return 0
        if args.command == "extract-dsm":
            bbox = args.bbox if args.bbox is not None else config.target_bbox
            _emit(
                extract_dsm_footprints(
                    args.dsm,
                    bbox,
                    args.output,
                    dtm_paths=args.dtm,
                    minimum_height_m=args.min_height,
                    minimum_area_m2=args.min_area,
                    ground_window_m=args.ground_window,
                )
            )
            return 0
        raise AssertionError(args.command)
    except SelectionBridgeError as exc:
        _emit(exc.to_dict())
        return 2
    except (ConfigError, WorkspaceError, RuntimeError, ValueError, OSError) as exc:
        _emit({"status": "error", "type": type(exc).__name__, "message": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
