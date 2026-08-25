import json
import math
from pathlib import Path

import numpy as np


ROOT = (
    Path(__file__).resolve().parents[2]
    / "external"
    / "results"
    / "official_big_image_target_bbox_20260816"
    / "inference_zoom20_app192_raw640_overlap75"
)
NPZ = ROOT / "density_volume.npz"
META = ROOT / "run_metadata.json"
LEVEL = 4.5


def positions(values, scale, mode):
    a = np.asarray(values, dtype=np.float64) * scale
    if mode == "floor":
        return np.floor(a).astype(int)
    if mode == "half_up":
        return np.floor(a + 0.5).astype(int)
    if mode == "bankers":
        return np.rint(a).astype(int)
    raise ValueError(mode)


def boundary_positions(starts, window, length):
    candidates = set(int(v) for v in starts)
    candidates.update(int(v + window) for v in starts)
    return np.asarray(sorted(v for v in candidates if 0 < v < length), dtype=int)


def per_pair_metrics(d, axis):
    n = d.shape[axis] - 1
    mean_abs = np.empty(n, np.float64)
    rms = np.empty(n, np.float64)
    signed = np.empty(n, np.float64)
    crossing_rate = np.empty(n, np.float64)
    near1_rate = np.empty(n, np.float64)
    near1_mean_abs = np.empty(n, np.float64)
    zero_xor_rate = np.empty(n, np.float64)
    p95_abs = np.empty(n, np.float64)

    for i in range(n):
        if axis == 0:
            a = d[i]
            b = d[i + 1]
        else:
            a = d[:, i]
            b = d[:, i + 1]
        delta = b - a
        ad = np.abs(delta)
        mean_abs[i] = ad.mean(dtype=np.float64)
        rms[i] = math.sqrt(np.square(delta, dtype=np.float64).mean(dtype=np.float64))
        signed[i] = delta.mean(dtype=np.float64)
        crossing_rate[i] = np.not_equal(a >= LEVEL, b >= LEVEL).mean()
        near = (np.abs(a - LEVEL) <= 1.0) | (np.abs(b - LEVEL) <= 1.0)
        near1_rate[i] = near.mean()
        near1_mean_abs[i] = ad[near].mean(dtype=np.float64) if near.any() else np.nan
        zero_xor_rate[i] = np.not_equal(a == 0, b == 0).mean()
        p95_abs[i] = np.percentile(ad, 95)
    return {
        "mean_abs": mean_abs,
        "rms": rms,
        "signed": signed,
        "crossing_rate": crossing_rate,
        "near1_rate": near1_rate,
        "near1_mean_abs": near1_mean_abs,
        "zero_xor_rate": zero_xor_rate,
        "p95_abs": p95_abs,
    }


def aggregate_exact(d, axis, boundary_pairs):
    n = d.shape[axis] - 1
    is_boundary = np.zeros(n, bool)
    is_boundary[np.asarray(boundary_pairs, int)] = True
    out = {}
    for group, pair_mask in (("boundary", is_boundary), ("baseline", ~is_boundary)):
        sums = {
            "n": 0,
            "sum_abs": 0.0,
            "sum_sq": 0.0,
            "sum_signed": 0.0,
            "cross": 0,
            "near05": 0,
            "near05_cross": 0,
            "near1": 0,
            "near1_cross": 0,
            "abs_gt_05": 0,
            "abs_gt_1": 0,
            "abs_gt_2": 0,
            "abs_gt_45": 0,
            "zero_xor": 0,
        }
        for i in np.flatnonzero(pair_mask):
            if axis == 0:
                a = d[i]
                b = d[i + 1]
            else:
                a = d[:, i]
                b = d[:, i + 1]
            delta = b - a
            ad = np.abs(delta)
            cross = np.not_equal(a >= LEVEL, b >= LEVEL)
            near05 = (np.abs(a - LEVEL) <= 0.5) | (np.abs(b - LEVEL) <= 0.5)
            near1 = (np.abs(a - LEVEL) <= 1.0) | (np.abs(b - LEVEL) <= 1.0)
            sums["n"] += ad.size
            sums["sum_abs"] += ad.sum(dtype=np.float64)
            sums["sum_sq"] += np.square(delta, dtype=np.float64).sum(dtype=np.float64)
            sums["sum_signed"] += delta.sum(dtype=np.float64)
            sums["cross"] += int(cross.sum())
            sums["near05"] += int(near05.sum())
            sums["near05_cross"] += int((near05 & cross).sum())
            sums["near1"] += int(near1.sum())
            sums["near1_cross"] += int((near1 & cross).sum())
            sums["abs_gt_05"] += int((ad > 0.5).sum())
            sums["abs_gt_1"] += int((ad > 1.0).sum())
            sums["abs_gt_2"] += int((ad > 2.0).sum())
            sums["abs_gt_45"] += int((ad > 4.5).sum())
            sums["zero_xor"] += int(np.not_equal(a == 0, b == 0).sum())
        nval = sums["n"]
        out[group] = {
            **sums,
            "mean_abs": sums["sum_abs"] / nval,
            "rms": math.sqrt(sums["sum_sq"] / nval),
            "mean_signed": sums["sum_signed"] / nval,
            "cross_rate": sums["cross"] / nval,
            "near05_rate": sums["near05"] / nval,
            "cross_given_near05": sums["near05_cross"] / sums["near05"],
            "near1_rate": sums["near1"] / nval,
            "cross_given_near1": sums["near1_cross"] / sums["near1"],
            "abs_gt_05_rate": sums["abs_gt_05"] / nval,
            "abs_gt_1_rate": sums["abs_gt_1"] / nval,
            "abs_gt_2_rate": sums["abs_gt_2"] / nval,
            "abs_gt_45_rate": sums["abs_gt_45"] / nval,
            "zero_xor_rate": sums["zero_xor"] / nval,
        }
    return out


def summarize_assignment(metrics, bpos):
    pair_indices = bpos - 1
    n = len(metrics["mean_abs"])
    mask = np.zeros(n, bool)
    mask[pair_indices] = True
    # Local baseline: distances 2..5 away, excluding any other boundary.
    local = set()
    bset = set(pair_indices.tolist())
    for p in pair_indices:
        for offset in (-5, -4, -3, -2, 2, 3, 4, 5):
            q = int(p + offset)
            if 0 <= q < n and q not in bset:
                local.add(q)
    local = np.asarray(sorted(local), int)
    result = {
        "boundary_positions": bpos.tolist(),
        "boundary_pair_indices": pair_indices.tolist(),
        "n_boundaries": int(mask.sum()),
        "n_baseline": int((~mask).sum()),
        "n_local_baseline": int(len(local)),
    }
    for key, vals in metrics.items():
        result[key] = {
            "boundary_mean": float(np.nanmean(vals[mask])),
            "baseline_mean": float(np.nanmean(vals[~mask])),
            "ratio": float(np.nanmean(vals[mask]) / np.nanmean(vals[~mask])),
            "local_baseline_mean": float(np.nanmean(vals[local])),
            "local_ratio": float(np.nanmean(vals[mask]) / np.nanmean(vals[local])),
            "boundary_median": float(np.nanmedian(vals[mask])),
            "baseline_median": float(np.nanmedian(vals[~mask])),
        }
    order = pair_indices[np.argsort(metrics["mean_abs"][pair_indices])[::-1]]
    result["worst_by_mean_abs"] = [
        {
            "boundary_position": int(i + 1),
            "pair_index": int(i),
            "mean_abs": float(metrics["mean_abs"][i]),
            "rms": float(metrics["rms"][i]),
            "crossing_rate": float(metrics["crossing_rate"][i]),
            "signed": float(metrics["signed"][i]),
        }
        for i in order[:10]
    ]
    return result


def coverage(starts, window, length):
    cov = np.zeros(length, np.int16)
    for s in starts:
        cov[s : s + window] += 1
    return cov


def contribution_intervals(starts, window, crop):
    starts = np.asarray(starts, int)
    out = []
    last_i = len(starts) - 1
    for i, s in enumerate(starts):
        lo = int(s + (0 if i == 0 else crop))
        hi = int(s + window - (0 if i == last_i else crop))
        out.append((lo, hi))
    return out


def coverage_intervals(intervals, length):
    cov = np.zeros(length, np.int16)
    for lo, hi in intervals:
        cov[lo:hi] += 1
    return cov


def interval_change_positions(intervals, length):
    return np.asarray(
        sorted({v for lo, hi in intervals for v in (lo, hi) if 0 < v < length}),
        dtype=int,
    )


def change_events(intervals, length):
    starts = {}
    ends = {}
    for i, (lo, hi) in enumerate(intervals):
        if 0 < lo < length:
            starts.setdefault(lo, []).append(i)
        if 0 < hi < length:
            ends.setdefault(hi, []).append(i)
    return {
        str(p): {
            "starts": starts.get(p, []),
            "ends": ends.get(p, []),
            "n_start": len(starts.get(p, [])),
            "n_end": len(ends.get(p, [])),
        }
        for p in sorted(set(starts) | set(ends))
    }


def run_lengths(a):
    changes = np.r_[0, np.flatnonzero(a[1:] != a[:-1]) + 1, len(a)]
    return [
        {"start": int(s), "end_exclusive": int(e), "length": int(e - s), "value": int(a[s])}
        for s, e in zip(changes[:-1], changes[1:])
    ]


def autocorrelation(x, lags):
    z = np.asarray(x, np.float64)
    z = z - z.mean()
    denom = np.dot(z, z)
    return {str(k): float(np.dot(z[:-k], z[k:]) / denom) for k in lags}


def main():
    meta = json.loads(META.read_text(encoding="utf-8"))
    d = np.load(NPZ)["density"]
    scale = float(meta["density_to_image_scale"])
    window = int(meta["cropped_density_shape"][0])

    result = {
        "shape": list(d.shape),
        "dtype": str(d.dtype),
        "min": float(d.min()),
        "max": float(d.max()),
        "mean": float(d.mean(dtype=np.float64)),
        "std": float(d.std(dtype=np.float64)),
        "level": LEVEL,
        "global_zero_count": int((d == 0).sum()),
        "global_zero_fraction": float((d == 0).mean()),
        "global_above_level_count": int((d >= LEVEL).sum()),
        "global_above_level_fraction": float((d >= LEVEL).mean()),
    }

    # Zero/uncovered-like diagnostics.
    allzero_z = np.all(d == 0, axis=(0, 1))
    allzero_y = np.all(d == 0, axis=(1, 2))
    allzero_x = np.all(d == 0, axis=(0, 2))
    allzero_xy_columns = np.all(d == 0, axis=2)
    zero_fraction_z = np.mean(d == 0, axis=(0, 1))
    zero_fraction_y = np.mean(d == 0, axis=(1, 2))
    zero_fraction_x = np.mean(d == 0, axis=(0, 2))
    result["zeros"] = {
        "allzero_z_planes": np.flatnonzero(allzero_z).tolist(),
        "allzero_y_planes": np.flatnonzero(allzero_y).tolist(),
        "allzero_x_planes": np.flatnonzero(allzero_x).tolist(),
        "allzero_xy_columns_count": int(allzero_xy_columns.sum()),
        "allzero_xy_columns_fraction": float(allzero_xy_columns.mean()),
        "z_zero_fraction_min": float(zero_fraction_z.min()),
        "z_zero_fraction_max": float(zero_fraction_z.max()),
        "z_zero_fraction_argmax": int(zero_fraction_z.argmax()),
        "y_zero_fraction_min": float(zero_fraction_y.min()),
        "y_zero_fraction_max": float(zero_fraction_y.max()),
        "y_zero_fraction_argmax": int(zero_fraction_y.argmax()),
        "x_zero_fraction_min": float(zero_fraction_x.min()),
        "x_zero_fraction_max": float(zero_fraction_x.max()),
        "x_zero_fraction_argmax": int(zero_fraction_x.argmax()),
        "z_zero_fraction": zero_fraction_z.tolist(),
    }

    print("Computing adjacent-plane metrics for Y...", flush=True)
    my = per_pair_metrics(d, 0)
    print("Computing adjacent-plane metrics for X...", flush=True)
    mx = per_pair_metrics(d, 1)

    result["mapping_candidates"] = {}
    candidate_scores = []
    for mode in ("floor", "half_up", "bankers"):
        sy = positions(meta["image_row_positions_px"], scale, mode)
        sx = positions(meta["image_column_positions_px"], scale, mode)
        by = boundary_positions(sy, window, d.shape[0])
        bx = boundary_positions(sx, window, d.shape[1])
        ay = summarize_assignment(my, by)
        ax = summarize_assignment(mx, bx)
        result["mapping_candidates"][mode] = {
            "starts_y": sy.tolist(),
            "starts_x": sx.tolist(),
            "y": ay,
            "x": ax,
        }
        candidate_scores.append(
            (
                mode,
                ay["mean_abs"]["local_ratio"] * ax["mean_abs"]["local_ratio"],
            )
        )

    inferred = max(candidate_scores, key=lambda t: t[1])[0]
    result["inferred_mapping_by_jump_alignment"] = inferred
    sy = np.asarray(result["mapping_candidates"][inferred]["starts_y"], int)
    sx = np.asarray(result["mapping_candidates"][inferred]["starts_x"], int)
    by = np.asarray(result["mapping_candidates"][inferred]["y"]["boundary_positions"], int)
    bx = np.asarray(result["mapping_candidates"][inferred]["x"]["boundary_positions"], int)

    print("Computing exact boundary vs baseline aggregates...", flush=True)
    result["exact_aggregates"] = {
        "y": aggregate_exact(d, 0, by - 1),
        "x": aggregate_exact(d, 1, bx - 1),
    }


    # The implementation uses Python round() (banker's rounding), and hard-crops
    # 19 voxels at every non-exterior tile edge before box averaging.  These are
    # the actual contributor-change lines in the saved volume.
    actual_mode = "bankers"
    sy_actual = positions(meta["image_row_positions_px"], scale, actual_mode)
    sx_actual = positions(meta["image_column_positions_px"], scale, actual_mode)
    # mapped_stops special-cases a window touching the far edge.
    sy_actual[-1] = d.shape[0] - window
    sx_actual[-1] = d.shape[1] - window
    fusion_crop = int(meta["fusion_crop_edge_voxels"])
    iy = contribution_intervals(sy_actual, window, fusion_crop)
    ix = contribution_intervals(sx_actual, window, fusion_crop)
    cby = interval_change_positions(iy, d.shape[0])
    cbx = interval_change_positions(ix, d.shape[1])
    result["actual_fusion"] = {
        "mapping_mode": actual_mode,
        "crop": fusion_crop,
        "starts_y": sy_actual.tolist(),
        "starts_x": sx_actual.tolist(),
        "intervals_y": iy,
        "intervals_x": ix,
        "change_events_y": change_events(iy, d.shape[0]),
        "change_events_x": change_events(ix, d.shape[1]),
        "y": summarize_assignment(my, cby),
        "x": summarize_assignment(mx, cbx),
        "exact_aggregates": {
            "y": aggregate_exact(d, 0, cby - 1),
            "x": aggregate_exact(d, 1, cbx - 1),
        },
    }

    cy = coverage(sy, window, d.shape[0])
    cx = coverage(sx, window, d.shape[1])
    cov2_counts = {}
    vals, counts = np.unique(cy[:, None] * cx[None, :], return_counts=True)
    for v, c in zip(vals, counts):
        cov2_counts[str(int(v))] = int(c)
    result["coverage"] = {
        "y_min": int(cy.min()),
        "y_max": int(cy.max()),
        "x_min": int(cx.min()),
        "x_max": int(cx.max()),
        "y_unique_counts": {str(int(v)): int((cy == v).sum()) for v in np.unique(cy)},
        "x_unique_counts": {str(int(v)): int((cx == v).sum()) for v in np.unique(cx)},
        "coverage2d_unique_pixel_counts": cov2_counts,
        "uncovered_y_count": int((cy == 0).sum()),
        "uncovered_x_count": int((cx == 0).sum()),
        "uncovered_xy_count": int(((cy[:, None] * cx[None, :]) == 0).sum()),
        "x_runs": run_lengths(cx),
        "y_runs_first": run_lengths(cy)[:12],
        "y_runs_last": run_lengths(cy)[-12:],
    }


    cy_actual = coverage_intervals(iy, d.shape[0])
    cx_actual = coverage_intervals(ix, d.shape[1])
    vals, counts = np.unique(cy_actual[:, None] * cx_actual[None, :], return_counts=True)
    result["actual_fusion"]["coverage"] = {
        "y_min": int(cy_actual.min()),
        "y_max": int(cy_actual.max()),
        "x_min": int(cx_actual.min()),
        "x_max": int(cx_actual.max()),
        "y_unique_counts": {
            str(int(v)): int((cy_actual == v).sum()) for v in np.unique(cy_actual)
        },
        "x_unique_counts": {
            str(int(v)): int((cx_actual == v).sum()) for v in np.unique(cx_actual)
        },
        "coverage2d_unique_pixel_counts": {
            str(int(v)): int(c) for v, c in zip(vals, counts)
        },
        "uncovered_xy_count": int(
            ((cy_actual[:, None] * cx_actual[None, :]) == 0).sum()
        ),
        "x_runs": run_lengths(cx_actual),
        "y_runs_first": run_lengths(cy_actual)[:16],
        "y_runs_last": run_lengths(cy_actual)[-16:],
    }

    # Quantify periodicity of the observed slice-jump signal.
    result["jump_autocorrelation"] = {
        "y_mean_abs": autocorrelation(my["mean_abs"], [1, 2, 3, 7, 8, 38, 39, 77, 115, 116, 154]),
        "x_mean_abs": autocorrelation(mx["mean_abs"], [1, 2, 3, 7, 8, 38, 39, 77, 115, 116, 154]),
        "y_crossing": autocorrelation(my["crossing_rate"], [1, 2, 3, 7, 8, 38, 39, 77, 115, 116, 154]),
        "x_crossing": autocorrelation(mx["crossing_rate"], [1, 2, 3, 7, 8, 38, 39, 77, 115, 116, 154]),
    }

    # Boundary-zero fractions compared with non-boundaries.
    for axis_name, zero_fracs, b in (
        ("y", zero_fraction_y, by),
        ("x", zero_fraction_x, bx),
    ):
        plane_mask = np.zeros(len(zero_fracs), bool)
        plane_mask[b] = True
        result.setdefault("boundary_plane_zero", {})[axis_name] = {
            "boundary_mean": float(zero_fracs[plane_mask].mean()),
            "nonboundary_mean": float(zero_fracs[~plane_mask].mean()),
            "ratio": float(zero_fracs[plane_mask].mean() / zero_fracs[~plane_mask].mean()),
            "boundary_max": float(zero_fracs[plane_mask].max()),
        }

    # Preserve compact per-pair series for independent checking without density reload.
    result["series"] = {
        "y_mean_abs": my["mean_abs"].tolist(),
        "x_mean_abs": mx["mean_abs"].tolist(),
        "y_crossing_rate": my["crossing_rate"].tolist(),
        "x_crossing_rate": mx["crossing_rate"].tolist(),
    }

    out = Path(__file__).with_suffix(".json")
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {out}", flush=True)
    print(json.dumps({
        "inferred": inferred,
        "exact": result["exact_aggregates"],
        "coverage": result["coverage"],
        "zeros": result["zeros"],
        "autocorrelation": result["jump_autocorrelation"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
