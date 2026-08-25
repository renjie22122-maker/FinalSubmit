import gc
import json
from pathlib import Path

import numpy as np


BASE = (
    Path(__file__).resolve().parents[2]
    / "external"
    / "results"
    / "official_big_image_target_bbox_20260816"
)
OLD = BASE / "inference_zoom20_app192_raw640_overlap75"
NEW = BASE / "inference_zoom20_app192_raw640_overlap75_fractional_feather"
LEVEL = 4.5


def periodic_positions(length, phase):
    vals = []
    k = 0
    while True:
        p = int(np.rint(phase + k * 38.5))
        if p >= length:
            break
        if p > 0:
            vals.append(p)
        k += 1
    return np.asarray(sorted(set(vals)), dtype=int)


def density_edges(d, axis, positions):
    n_other = int(np.prod([d.shape[i] for i in range(3) if i != axis]))
    s_abs = s_sq = 0.0
    cross = gt45 = 0
    for p in positions:
        a = np.take(d, p - 1, axis=axis)
        b = np.take(d, p, axis=axis)
        delta = b - a
        ad = np.abs(delta)
        s_abs += ad.sum(dtype=np.float64)
        s_sq += np.square(delta, dtype=np.float64).sum(dtype=np.float64)
        cross += int(np.not_equal(a >= LEVEL, b >= LEVEL).sum())
        gt45 += int((ad > LEVEL).sum())
    n = len(positions) * n_other
    return {
        "planes": int(len(positions)),
        "values": int(n),
        "mean_abs": s_abs / n,
        "rms": float(np.sqrt(s_sq / n)),
        "cross_count": cross,
        "cross_rate": cross / n,
        "abs_gt_4p5_rate": gt45 / n,
    }


def top_height(d):
    occ = d >= LEVEL
    any_occ = occ.any(axis=2)
    top_idx = d.shape[2] - 1 - np.argmax(occ[..., ::-1], axis=2)
    yy, xx = np.indices(top_idx.shape)
    z0 = top_idx
    z1 = np.minimum(z0 + 1, d.shape[2] - 1)
    v0 = d[yy, xx, z0]
    v1 = d[yy, xx, z1]
    denom = v0 - v1
    frac = np.divide(v0 - LEVEL, denom, out=np.zeros_like(v0), where=np.abs(denom) > 1e-12)
    h = z0.astype(np.float32) + np.clip(frac, 0, 1)
    h[~any_occ] = np.nan
    return h


def height_edges(h, axis, positions):
    values = []
    for p in positions:
        a = np.take(h, p - 1, axis=axis)
        b = np.take(h, p, axis=axis)
        values.append(np.abs(b - a))
    v = np.concatenate([x.ravel() for x in values])
    v = v[np.isfinite(v)]
    return {
        "values": int(v.size),
        "mean_abs_voxels": float(v.mean(dtype=np.float64)),
        "rms_voxels": float(np.sqrt(np.square(v, dtype=np.float64).mean())),
        "median_voxels": float(np.median(v)),
        "p90_voxels": float(np.percentile(v, 90)),
        "p95_voxels": float(np.percentile(v, 95)),
        "gt1_rate": float((v > 1).mean()),
        "gt2_rate": float((v > 2).mean()),
        "gt5_rate": float((v > 5).mean()),
    }


def analyze(path, families):
    d = np.load(path / "density_volume.npz")["density"]
    h = top_height(d)
    result = {
        "shape": list(d.shape),
        "zero_fraction": float((d == 0).mean()),
        "allzero_y_planes": np.flatnonzero(np.all(d == 0, axis=(1, 2))).tolist(),
        "allzero_x_planes": np.flatnonzero(np.all(d == 0, axis=(0, 2))).tolist(),
        "allzero_z_planes": np.flatnonzero(np.all(d == 0, axis=(0, 1))).tolist(),
        "families": {},
    }
    for name, axes in families.items():
        result["families"][name] = {}
        for axis_name, axis in (("y", 0), ("x", 1)):
            p = axes[axis_name]
            p = p[p < d.shape[axis]]
            result["families"][name][axis_name] = {
                "positions": p.tolist(),
                "density": density_edges(d, axis, p),
                "top_height": height_edges(h, axis, p),
            }
    del d, h
    gc.collect()
    return result


def ratios(old, new):
    out = {}
    for family in old["families"]:
        out[family] = {}
        for axis in ("y", "x"):
            od = old["families"][family][axis]
            nd = new["families"][family][axis]
            out[family][axis] = {
                "density_mean_abs_new_over_old": nd["density"]["mean_abs"] / od["density"]["mean_abs"],
                "density_cross_rate_new_over_old": nd["density"]["cross_rate"] / od["density"]["cross_rate"],
                "density_abs_gt_4p5_new_over_old": nd["density"]["abs_gt_4p5_rate"] / od["density"]["abs_gt_4p5_rate"],
                "top_height_mean_abs_new_over_old": nd["top_height"]["mean_abs_voxels"] / od["top_height"]["mean_abs_voxels"],
                "top_height_p95_new_over_old": nd["top_height"]["p95_voxels"] / od["top_height"]["p95_voxels"],
            }
    return out


def main():
    diag = json.loads(Path(r".agents\density_seam_analysis.json").read_text())
    true_old = {
        "y": np.asarray(diag["actual_fusion"]["y"]["boundary_positions"], int),
        "x": np.asarray(diag["actual_fusion"]["x"]["boundary_positions"], int),
    }
    # Two fixed 38.5-voxel phases: raw window origins and the center phase of
    # the old 19-voxel crop contributor changes.
    families = {
        "old_true_contributor_lines": true_old,
        "fixed_38p5_origin_phase": {
            "y": periodic_positions(1232, 38.5),
            "x": periodic_positions(739, 38.5),
        },
        "fixed_38p5_crop_phase": {
            "y": periodic_positions(1232, 57.75),
            "x": periodic_positions(739, 57.75),
        },
    }
    old = analyze(OLD, families)
    new = analyze(NEW, families)
    old_meta = json.loads((OLD / "run_metadata.json").read_text())
    new_meta = json.loads((NEW / "run_metadata.json").read_text())
    result = {
        "level": LEVEL,
        "family_definitions": {
            "old_true_contributor_lines": "union of old hard-cropped tile contribution starts/ends",
            "fixed_38p5_origin_phase": "round(38.5 + k*38.5), nominal raw-window-origin cycle",
            "fixed_38p5_crop_phase": "round(57.75 + k*38.5), centered phase of old crop-edge changes",
            "top_height": "topmost 4.5 isosurface crossing, linearly interpolated in z",
        },
        "old": old,
        "new": new,
        "new_over_old": ratios(old, new),
        "metadata_check": {
            "old_shape": old_meta["density_volume_shape"],
            "new_shape": new_meta["density_volume_shape"],
            "fusion_mode": new_meta.get("fusion_mode"),
            "feather_profile": new_meta.get("fusion_feather_profile"),
            "feather_width": new_meta.get("fusion_feather_width_voxels"),
            "fractional_splat": new_meta.get("fusion_fractional_splat"),
            "weight_min": new_meta.get("fusion_weight_min"),
            "weight_max": new_meta.get("fusion_weight_max"),
            "zero_weight_cells": new_meta.get("fusion_zero_weight_cells"),
            "fractional_row_origins": new_meta.get("fusion_density_row_origins"),
            "fractional_column_origins": new_meta.get("fusion_density_column_origins"),
        },
    }
    out = Path(r".agents\density_feather_compare.json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"ratios": result["new_over_old"], "metadata": result["metadata_check"]}, indent=2))


if __name__ == "__main__":
    main()
