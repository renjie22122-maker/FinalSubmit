from __future__ import annotations

from pathlib import Path
import argparse
import json
from typing import Dict, Any

import numpy as np
from PIL import Image


def image_stats(path: Path) -> Dict[str, Any]:
    arr = np.array(Image.open(path))
    return {
        "shape": list(arr.shape),
        "mode": Image.open(path).mode,
        "min": int(arr.min()),
        "max": int(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "unique_count": int(np.unique(arr).size),
    }


def check_sky_masks(pano_dir: Path, sky_dir: Path) -> Dict[str, Any]:
    panos = sorted(pano_dir.glob("*.jpg"))
    masks = sorted(sky_dir.glob("*.png"))
    missing = [f"{p.stem}.png" for p in panos if not (sky_dir / f"{p.stem}.png").exists()]

    binary_ok = 0
    sky_ratio = []
    bad_binary = []
    for m in masks:
        arr = np.array(Image.open(m))
        uniq = np.unique(arr)
        if set(uniq.tolist()).issubset({0, 255}):
            binary_ok += 1
        else:
            bad_binary.append(m.name)
        sky_ratio.append(float((arr == 255).mean()))

    ratio_arr = np.array(sky_ratio, dtype=np.float32) if sky_ratio else np.array([], dtype=np.float32)
    return {
        "panorama_count": len(panos),
        "mask_count": len(masks),
        "missing_mask_count": len(missing),
        "binary_mask_count": binary_ok,
        "non_binary_examples": bad_binary[:10],
        "sky_ratio_min": float(ratio_arr.min()) if ratio_arr.size else None,
        "sky_ratio_max": float(ratio_arr.max()) if ratio_arr.size else None,
        "sky_ratio_mean": float(ratio_arr.mean()) if ratio_arr.size else None,
        "low_sky_ratio_count(<1%)": int((ratio_arr < 0.01).sum()) if ratio_arr.size else 0,
        "high_sky_ratio_count(>95%)": int((ratio_arr > 0.95).sum()) if ratio_arr.size else 0,
        "missing_examples": missing[:10],
    }


def check_depth(sat_dir: Path, depth_dir: Path) -> Dict[str, Any]:
    sats = sorted(sat_dir.glob("*.png"))
    depths = sorted(depth_dir.glob("*.png"))
    missing = [s.name for s in sats if not (depth_dir / s.name).exists()]

    stats = []
    flat = 0
    for d in depths:
        arr = np.array(Image.open(d))
        s = float(arr.std())
        if s < 3.0:
            flat += 1
        stats.append((float(arr.min()), float(arr.max()), float(arr.mean()), s))

    if stats:
        mat = np.array(stats, dtype=np.float32)
        min_mean = float(mat[:, 0].mean())
        max_mean = float(mat[:, 1].mean())
        mean_mean = float(mat[:, 2].mean())
        std_mean = float(mat[:, 3].mean())
    else:
        min_mean = max_mean = mean_mean = std_mean = None

    return {
        "satellite_count": len(sats),
        "depth_count": len(depths),
        "missing_depth_count": len(missing),
        "flat_depth_count(std<3)": flat,
        "pixel_min_mean": min_mean,
        "pixel_max_mean": max_mean,
        "pixel_mean_mean": mean_mean,
        "pixel_std_mean": std_mean,
        "missing_examples": missing[:10],
    }


def run(root: Path, dsm_depth_dir: Path, dsm_raster_depth_dir: Path, report_path: Path) -> None:
    city = root / "London"
    pano_dir = city / "panorama"
    sat_dir = city / "satellite"
    sky_dir = city / "pano_sky_mask"
    depth_dir = city / "sat_depth"

    report: Dict[str, Any] = {
        "root": str(root),
        "sky_mask": check_sky_masks(pano_dir, sky_dir),
        "sat_depth_model": check_depth(sat_dir, depth_dir),
    }

    if dsm_depth_dir.exists():
        report["sat_depth_dsm"] = check_depth(sat_dir, dsm_depth_dir)

    if dsm_raster_depth_dir.exists():
        report["sat_depth_dsm_raster"] = check_depth(sat_dir, dsm_raster_depth_dir)

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote quality report: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Quality check for pano_sky_mask and sat_depth outputs")
    parser.add_argument("--root", type=Path, default=Path("london_vigor_root"))
    parser.add_argument("--dsm-depth-dir", type=Path, default=Path("london_vigor_root/London/sat_depth_dsm"))
    parser.add_argument(
        "--dsm-raster-depth-dir",
        type=Path,
        default=Path("london_vigor_root/London/sat_depth_dsm_raster"),
    )
    parser.add_argument("--report", type=Path, default=Path("london_vigor_root/aux_quality_report.json"))
    args = parser.parse_args()

    run(
        args.root.resolve(),
        args.dsm_depth_dir.resolve(),
        args.dsm_raster_depth_dir.resolve(),
        args.report.resolve(),
    )


if __name__ == "__main__":
    main()
