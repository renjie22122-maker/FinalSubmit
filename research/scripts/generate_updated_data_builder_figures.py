"""Generate thesis-ready data_builder audit figures from derived evidence only.

Inputs are restricted to research/results/update_data_builder JSON/CSV files.
No source imagery or files from the audited data_builder repository are read.
Outputs are vector PDF plus 300 dpi PNG under figures/generated.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
import numpy as np
import pandas as pd


WORKSPACE = Path(__file__).resolve().parents[2]
RESULTS = WORKSPACE / "research" / "results" / "update_data_builder"
OUTPUT = WORKSPACE / "figures" / "generated"

BLUE = "#0072B2"
SKY = "#56B4E9"
GREEN = "#009E73"
ORANGE = "#E69F00"
VERMILION = "#D55E00"
PURPLE = "#CC79A7"
GREY = "#6B7280"
LIGHT_GREY = "#E5E7EB"
DARK = "#1F2937"


mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8.5,
        "axes.titlesize": 10.5,
        "axes.labelsize": 8.5,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "legend.fontsize": 7.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": "#9CA3AF",
        "axes.linewidth": 0.7,
        "grid.color": "#D1D5DB",
        "grid.linewidth": 0.55,
        "grid.alpha": 0.7,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
)


def load_evidence() -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    audit = json.loads((RESULTS / "data_builder_audit.json").read_text(encoding="utf-8"))
    candidates = pd.read_csv(RESULTS / "label_candidates.csv")
    ranks = pd.read_csv(RESULTS / "label_candidate_rank_summary.csv")
    osm = pd.read_csv(RESULTS / "osm_category_summary.csv")
    alias = pd.read_csv(RESULTS / "london_seattle_alias_comparison.csv")
    spatial = pd.read_csv(RESULTS / "spatial_points.csv")
    centres = pd.read_csv(RESULTS / "satellite_centres.csv")
    return audit, candidates, ranks, osm, alias, spatial, centres


def save_figure(fig: plt.Figure, stem: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pdf = OUTPUT / f"{stem}.pdf"
    png = OUTPUT / f"{stem}.png"
    fig.savefig(pdf, bbox_inches="tight", pad_inches=0.06)
    fig.savefig(png, dpi=300, bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)


def add_evidence_note(fig: plt.Figure, text: str) -> None:
    fig.text(0.5, 0.018, text, ha="center", va="bottom", fontsize=6.8, color=GREY)


def draw_box(
    ax: plt.Axes,
    xy: tuple[float, float],
    width: float,
    height: float,
    text: str,
    edge: str,
    face: str,
    fontsize: float = 8.4,
) -> None:
    patch = FancyBboxPatch(
        xy,
        width,
        height,
        boxstyle="round,pad=0.012,rounding_size=0.014",
        linewidth=1.0,
        edgecolor=edge,
        facecolor=face,
    )
    ax.add_patch(patch)
    ax.text(xy[0] + width / 2, xy[1] + height / 2, text, ha="center", va="center", fontsize=fontsize, color=DARK)


def arrow(ax: plt.Axes, start: tuple[float, float], end: tuple[float, float], color: str = GREY) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=9,
            linewidth=1.0,
            color=color,
            shrinkA=2,
            shrinkB=2,
        )
    )


def figure_pipeline(audit: dict) -> None:
    fig, ax = plt.subplots(figsize=(7.25, 4.15))
    fig.subplots_adjust(left=0.02, right=0.98, top=0.88, bottom=0.12)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    fig.suptitle("Data-builder outputs diverge after query matching", fontsize=12, fontweight="semibold", color=DARK)

    ax.text(0.235, 0.965, "Query and split branch", ha="center", va="top", fontsize=9.3, fontweight="semibold", color=BLUE)
    ax.text(0.745, 0.965, "Satellite grid branch", ha="center", va="top", fontsize=9.3, fontweight="semibold", color=ORANGE)
    ax.plot([0.49, 0.49], [0.08, 0.94], color=LIGHT_GREY, linewidth=1.0)

    draw_box(ax, (0.09, 0.72), 0.29, 0.13, "Input highway points\n2,343", BLUE, "#E8F4FA")
    draw_box(ax, (0.105, 0.49), 0.26, 0.13, "Matched panorama queries\n2,333 (99.57%)", GREEN, "#E7F6F1")
    arrow(ax, (0.235, 0.715), (0.235, 0.625))
    ax.text(0.247, 0.665, "10 unmatched", fontsize=7.1, color=VERMILION, va="center")

    split_y = 0.205
    split_w = 0.125
    split_x = [0.025, 0.177, 0.329]
    split_text = ["Train\n1,633", "Validation\n350", "Test\n350"]
    split_colours = [BLUE, ORANGE, GREEN]
    split_faces = ["#E8F4FA", "#FFF4D6", "#E7F6F1"]
    for x, label, colour, face in zip(split_x, split_text, split_colours, split_faces):
        draw_box(ax, (x, split_y), split_w, 0.115, label, colour, face, fontsize=8.0)
        arrow(ax, (0.235, 0.485), (x + split_w / 2, split_y + 0.12))
    ax.text(0.235, 0.105, "Strict four-candidate validation export: 0 / 350", ha="center", fontsize=7.4, color=VERMILION, fontweight="semibold")

    draw_box(ax, (0.61, 0.73), 0.27, 0.12, "Sparse imagery\n127 tiles", ORANGE, "#FFF4D6")
    draw_box(ax, (0.61, 0.49), 0.27, 0.12, "Filled base grid\n272 tiles (16 x 17)", BLUE, "#E8F4FA")
    arrow(ax, (0.745, 0.725), (0.745, 0.615))
    ax.text(0.758, 0.67, "+145 centres", fontsize=7.1, color=BLUE, va="center")

    draw_box(ax, (0.515, 0.21), 0.205, 0.12, "50% overlap\n990 (30 x 33)", PURPLE, "#F8EAF3", fontsize=8.0)
    draw_box(ax, (0.775, 0.21), 0.205, 0.12, "10% overlap\n306 (17 x 18)", GREEN, "#E7F6F1", fontsize=8.0)
    arrow(ax, (0.72, 0.485), (0.62, 0.335))
    arrow(ax, (0.77, 0.485), (0.875, 0.335))
    ax.text(0.745, 0.105, "Real DSM patches: 127   |   OSM masks/category: 127", ha="center", fontsize=7.4, color=VERMILION, fontweight="semibold")

    add_evidence_note(fig, "Stored artifact counts; they do not prove one provenance-locked end-to-end run.")
    save_figure(fig, "data_builder_pipeline_funnel")


def figure_coverage_and_availability(audit: dict) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 4.2), gridspec_kw={"width_ratios": [1.12, 1.22]})
    fig.subplots_adjust(left=0.105, right=0.98, top=0.84, bottom=0.18, wspace=0.42)
    fig.suptitle("Spatial coverage and channel availability are not equivalent", fontsize=12, fontweight="semibold", color=DARK)

    grids = {row["name"]: row for row in audit["satellite_grids"]}
    grid_order = ["sparse_127", "filled_272", "overlap50_990", "overlap10_306"]
    labels = ["Sparse 127", "Filled 272", "50% 990", "10% 306"]
    areas = [grids[key]["union_area_m2_local_scale_estimate"] / 1_000_000 for key in grid_order]
    covered = [grids[key]["panoramas_covered"] for key in grid_order]
    colours = [ORANGE, BLUE, PURPLE, GREEN]
    y = np.arange(len(labels))
    ax = axes[0]
    ax.barh(y, areas, color=colours, height=0.58)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.30)
    ax.set_xlabel("Estimated union area (km2)")
    ax.set_title("Grid footprint union", loc="left", fontweight="semibold")
    ax.grid(axis="x")
    ax.axvline(1.0, color=GREY, linestyle=":", linewidth=0.9)
    for idx, (area, count) in enumerate(zip(areas, covered)):
        ax.text(area - 0.018, idx, f"{area:.3f} km2", ha="right", va="center", color="white", fontsize=7.1, fontweight="semibold")
        ax.text(1.285, idx, f"{count:,}/2,333", ha="right", va="center", fontsize=6.9, color=DARK)
    ax.text(1.285, -0.68, "queries covered", ha="right", fontsize=6.8, color=GREY)

    channels = [
        ("Matched panoramas", 2333, 2343),
        ("Sky masks", 2333, 2333),
        ("Filled satellite", 272, 272),
        ("Model depth", 272, 272),
        ("DSM raster patches", 127, 272),
        ("OSM masks/category", 127, 272),
        ("Strict 4-cand. val", 0, 350),
    ]
    channel_labels = [item[0] for item in channels]
    numerators = np.array([item[1] for item in channels], dtype=float)
    denominators = np.array([item[2] for item in channels], dtype=float)
    percentages = numerators / denominators * 100
    bar_colours = [GREEN if value >= 99 else ORANGE if value > 0 else VERMILION for value in percentages]
    ax = axes[1]
    yy = np.arange(len(channels))
    ax.barh(yy, percentages, color=bar_colours, height=0.58)
    ax.set_yticks(yy, channel_labels)
    ax.invert_yaxis()
    ax.set_xlim(0, 108)
    ax.set_xlabel("Available relative to expected parent (%)")
    ax.set_title("Stored channel availability", loc="left", fontweight="semibold")
    ax.grid(axis="x")
    for idx, (pct, num, den) in enumerate(zip(percentages, numerators.astype(int), denominators.astype(int))):
        ax.text(max(pct + 1.6, 2.0), idx, f"{num:,}/{den:,}", ha="left", va="center", fontsize=7.0, color=DARK)

    add_evidence_note(fig, "Areas are local-scale Web-Mercator estimates. DSM/OSM are compared with 272 filled base centres; availability is not quality.")
    save_figure(fig, "data_builder_coverage_channels")


def figure_candidate_offsets(audit: dict, candidates: pd.DataFrame, ranks: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.35, 4.45), gridspec_kw={"width_ratios": [1.08, 0.92]})
    fig.subplots_adjust(left=0.09, right=0.98, top=0.82, bottom=0.15, wspace=0.34)
    fig.suptitle("Four-nearest labels do not provide four containing tiles", fontsize=12, fontweight="semibold", color=DARK)

    ax = axes[0]
    rank_colours = {1: GREEN, 2: BLUE, 3: ORANGE, 4: PURPLE}
    for rank in [4, 3, 2, 1]:
        subset = candidates[candidates["rank"] == rank]
        ax.scatter(
            subset["dx_px"],
            subset["dy_px"],
            s=5,
            alpha=0.20 if rank > 1 else 0.28,
            color=rank_colours[rank],
            linewidths=0,
            label=f"Rank {rank}",
        )
    ax.add_patch(Rectangle((-320, -320), 640, 640, facecolor="none", edgecolor=DARK, linestyle="--", linewidth=1.15))
    ax.text(-305, 335, "+/-320 px containing boundary", fontsize=6.9, color=DARK, va="bottom")
    ax.axhline(0, color=LIGHT_GREY, linewidth=0.7)
    ax.axvline(0, color=LIGHT_GREY, linewidth=0.7)
    ax.set_xlim(-1000, 1000)
    ax.set_ylim(-1000, 1000)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Recorded dx (px)")
    ax.set_ylabel("Recorded dy (px)")
    ax.set_title("All 9,332 candidate offsets", loc="left", fontweight="semibold")
    handles, labels = ax.get_legend_handles_labels()
    order = [labels.index(f"Rank {rank}") for rank in [1, 2, 3, 4]]
    ax.legend([handles[i] for i in order], [labels[i] for i in order], frameon=False, ncol=2, loc="lower right")

    ax = axes[1]
    ranks = ranks.sort_values("rank")
    inside_pct = ranks["inside_count"] / ranks["candidate_count"] * 100
    outside_pct = ranks["outside_count"] / ranks["candidate_count"] * 100
    yy = np.arange(4)
    ax.barh(yy, inside_pct, color=GREEN, height=0.58, label="Inside")
    ax.barh(yy, outside_pct, left=inside_pct, color=VERMILION, height=0.58, label="Outside")
    ax.set_yticks(yy, [f"Rank {rank}" for rank in ranks["rank"]])
    ax.invert_yaxis()
    ax.set_ylim(3.72, -0.78)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Candidate records (%)")
    ax.set_title("Containment by candidate rank", loc="left", fontweight="semibold")
    ax.grid(axis="x")
    for idx, row in enumerate(ranks.itertuples(index=False)):
        ax.text(98.2, idx, f"{int(row.inside_count):,} in / {int(row.outside_count):,} out", ha="right", va="center", color="white" if row.outside_count > 1500 else DARK, fontsize=6.9, fontweight="semibold")
    ax.text(
        0.5,
        0.985,
        "6,987 / 9,332 outside (74.87%)",
        transform=ax.transAxes,
        ha="center",
        va="top",
        fontsize=7.8,
        fontweight="semibold",
        color=VERMILION,
    )
    ax.text(
        0.02,
        0.012,
        "Queries: 2,321 have one containing candidate; 12 have two;\nnone have three or four.",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=6.7,
        color=GREY,
    )

    add_evidence_note(fig, "Containment uses recorded offsets within +/-320 px on both axes; all referenced files exist.")
    save_figure(fig, "data_builder_vigor_candidate_offsets")


def figure_integrity_semantics(audit: dict, osm: pd.DataFrame, alias: pd.DataFrame) -> None:
    fig = plt.figure(figsize=(7.8, 4.65))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.03, 0.92, 1.05], left=0.055, right=0.985, top=0.79, bottom=0.13, wspace=0.43)
    fig.suptitle("Integrity checks expose leakage, semantic contamination and aliasing", fontsize=12, fontweight="semibold", color=DARK)

    ax = fig.add_subplot(gs[0, 0])
    labels = ["Panorama files", "Unique hashes", "Files in duplicate groups"]
    values = [2333, 1205, 1555]
    colours = [BLUE, GREEN, ORANGE]
    yy = np.arange(3)
    ax.barh(yy, values, color=colours, height=0.55)
    ax.set_yticks(yy, labels)
    ax.invert_yaxis()
    ax.set_ylim(3.72, -0.48)
    ax.set_xlim(0, 2550)
    ax.set_title("Exact-image duplication", loc="left", fontweight="semibold")
    ax.set_xlabel("Equirectangular files / hashes")
    ax.grid(axis="x")
    for idx, value in enumerate(values):
        ax.text(value - 45, idx, f"{value:,}", ha="right", va="center", color="white", fontsize=7.2, fontweight="semibold")
    ax.text(
        0,
        3.08,
        "Leakage by shared hash:\ntrain-val 7 (20/11 rows)\nval-test 3 (4/3 rows)\ntrain-test 0",
        ha="left",
        va="top",
        fontsize=6.8,
        color=DARK,
    )

    ax = fig.add_subplot(gs[0, 1])
    total = int(osm["geojson_features"].sum())
    support = int(osm["geojson_untagged_support_points"].sum())
    other = total - support
    support_pct = support / total * 100
    ax.barh([0], [support_pct], color=VERMILION, height=0.42, label="Untagged support points")
    ax.barh([0], [100 - support_pct], left=[support_pct], color=BLUE, height=0.42, label="Other records")
    ax.set_xlim(0, 100)
    ax.set_ylim(-1.55, 1.45)
    ax.set_yticks([])
    ax.set_xlabel("Category records (%)")
    ax.set_title("OSM semantic\ncontamination", loc="left", fontweight="semibold", fontsize=9.6)
    ax.grid(axis="x")
    ax.text(support_pct / 2, 0, f"{support_pct:.1f}%", ha="center", va="center", color="white", fontsize=8.0, fontweight="semibold")
    ax.text(
        50,
        -0.58,
        f"{support:,} / {total:,}\nuntagged support points",
        ha="center",
        va="center",
        fontsize=7.7,
        fontweight="semibold",
        color=VERMILION,
    )
    ax.text(
        50,
        -1.18,
        "The extractor converts recursive\nsupport nodes into point features,\nthen the renderer draws them in masks.",
        ha="center",
        va="center",
        fontsize=6.5,
        color=DARK,
    )

    ax = fig.add_subplot(gs[0, 2])
    channel_labels = ["Panorama", "Satellite", "Sky mask", "Model depth"]
    exact = alias["byte_identical_common_files"].astype(int).to_numpy()
    totals = alias["common_names"].astype(int).to_numpy()
    y = np.arange(4)
    ax.barh(y, np.ones(4) * 100, color=PURPLE, height=0.55)
    ax.set_yticks(y, channel_labels)
    ax.invert_yaxis()
    ax.set_ylim(4.72, -0.48)
    ax.set_xlim(0, 105)
    ax.set_xlabel("London-Seattle exact match (%)")
    ax.set_title("Directory alias is not\na city test", loc="left", fontweight="semibold", fontsize=9.6)
    ax.grid(axis="x")
    for idx, (num, den) in enumerate(zip(exact, totals)):
        ax.text(97.5, idx, f"{num:,}/{den:,}", ha="right", va="center", color="white", fontsize=7.0, fontweight="semibold")
    ax.text(
        50,
        4.20,
        f"{int(exact.sum()):,} / {int(totals.sum()):,} byte-identical files\nCompatibility alias; not cross-city evidence.",
        ha="center",
        va="center",
        fontsize=6.8,
        color=VERMILION,
        fontweight="semibold",
    )

    add_evidence_note(fig, "Hash equality is exact file-content evidence; it does not recover missing acquisition or model-run provenance.")
    save_figure(fig, "data_builder_integrity_semantics")


def to_local_xy(frame: pd.DataFrame, origin_lat: float, origin_lon: float) -> tuple[np.ndarray, np.ndarray]:
    x = (frame["longitude"].to_numpy() - origin_lon) * 111_320.0 * math.cos(math.radians(origin_lat))
    y = (frame["latitude"].to_numpy() - origin_lat) * 111_320.0
    return x, y


def bbox_to_local_rectangle(bbox: dict, origin_lat: float, origin_lon: float) -> tuple[float, float, float, float]:
    x1 = (bbox["min_lon"] - origin_lon) * 111_320.0 * math.cos(math.radians(origin_lat))
    x2 = (bbox["max_lon"] - origin_lon) * 111_320.0 * math.cos(math.radians(origin_lat))
    y1 = (bbox["min_lat"] - origin_lat) * 111_320.0
    y2 = (bbox["max_lat"] - origin_lat) * 111_320.0
    return x1, y1, x2 - x1, y2 - y1


def figure_spatial_schematic(audit: dict, spatial: pd.DataFrame, centres: pd.DataFrame) -> None:
    origin_lat = float(spatial["latitude"].mean())
    origin_lon = float(spatial["longitude"].mean())
    fig, axes = plt.subplots(1, 2, figsize=(7.5, 4.65))
    fig.subplots_adjust(left=0.08, right=0.98, top=0.84, bottom=0.28, wspace=0.30)
    fig.suptitle("Spatial support differs across query and grid products", fontsize=12, fontweight="semibold", color=DARK)

    ax = axes[0]
    split_colours = {"train": BLUE, "validation": ORANGE, "test": GREEN}
    for split in ["train", "validation", "test"]:
        subset = spatial[(spatial["matched"] == True) & (spatial["split"] == split)]  # noqa: E712
        x, y = to_local_xy(subset, origin_lat, origin_lon)
        ax.scatter(x, y, s=5, alpha=0.55, color=split_colours[split], linewidths=0, label=f"{split.title()} ({len(subset):,})")
    unmatched = spatial[spatial["matched"] == False]  # noqa: E712
    x, y = to_local_xy(unmatched, origin_lat, origin_lon)
    ax.scatter(x, y, s=27, marker="x", linewidths=1.0, color=VERMILION, label="Unmatched (10)", zorder=5)
    ax.set_title("2,343 input points", loc="left", fontweight="semibold")
    ax.set_xlabel("East offset (m)")
    ax.set_ylabel("North offset (m)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.45)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2, fontsize=6.8)
    ax.text(0.02, 0.98, "Envelope: 1,220.8 x 889.7 m", transform=ax.transAxes, ha="left", va="top", fontsize=7.2, color=DARK)

    ax = axes[1]
    filled = centres[centres["grid"] == "filled_272"].copy()
    sparse = centres[centres["grid"] == "sparse_127"].copy()
    filled_keys = set(zip(filled["latitude"].round(6), filled["longitude"].round(6)))
    sparse_keys = set(zip(sparse["latitude"].round(6), sparse["longitude"].round(6)))
    added = filled[
        [
            (round(lat, 6), round(lon, 6)) in (filled_keys - sparse_keys)
            for lat, lon in zip(filled["latitude"], filled["longitude"])
        ]
    ]
    sx, sy = to_local_xy(sparse, origin_lat, origin_lon)
    ax.scatter(sx, sy, s=13, facecolors=BLUE, edgecolors="none", label="Sparse centres (127)", zorder=3)
    axx, ayy = to_local_xy(added, origin_lat, origin_lon)
    ax.scatter(axx, ayy, s=16, marker="x", linewidths=0.85, color=ORANGE, label="Filled-only centres (145)", zorder=2)

    grid_lookup = {row["name"]: row for row in audit["satellite_grids"]}
    for key, colour, linestyle, label in [
        ("filled_272", BLUE, "-", "Filled footprint"),
        ("overlap10_306", GREEN, "--", "10% footprint"),
    ]:
        rect = bbox_to_local_rectangle(grid_lookup[key]["footprint_bbox"], origin_lat, origin_lon)
        ax.add_patch(Rectangle((rect[0], rect[1]), rect[2], rect[3], fill=False, edgecolor=colour, linestyle=linestyle, linewidth=1.1, label=label))

    missed = pd.DataFrame(
        {
            "latitude": [51.510645, 51.510608],
            "longitude": [-0.121092, -0.121190],
        }
    )
    mx, my = to_local_xy(missed, origin_lat, origin_lon)
    ax.scatter(mx, my, s=42, marker="*", color=VERMILION, edgecolors="white", linewidths=0.4, label="Queries missed by 10% grid (2)", zorder=6)
    ax.annotate("2", (float(mx.mean()), float(my.mean())), xytext=(-8, 7), textcoords="offset points", ha="right", va="bottom", fontsize=6.8, color=VERMILION, fontweight="semibold")
    ax.set_title("Base lattice and 10% footprint", loc="left", fontweight="semibold")
    ax.set_xlabel("East offset (m)")
    ax.set_ylabel("North offset (m)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.45)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.13), ncol=2, fontsize=6.4)

    add_evidence_note(fig, "Local equirectangular offsets form a non-imagery schematic; the 10% grid covers 2,331 of 2,333 matched queries.")
    save_figure(fig, "data_builder_spatial_support")


def main() -> None:
    audit, candidates, ranks, osm, alias, spatial, centres = load_evidence()
    figure_pipeline(audit)
    figure_coverage_and_availability(audit)
    figure_candidate_offsets(audit, candidates, ranks)
    figure_integrity_semantics(audit, osm, alias)
    figure_spatial_schematic(audit, spatial, centres)
    for stem in [
        "data_builder_pipeline_funnel",
        "data_builder_coverage_channels",
        "data_builder_vigor_candidate_offsets",
        "data_builder_integrity_semantics",
        "data_builder_spatial_support",
    ]:
        print(OUTPUT / f"{stem}.pdf")
        print(OUTPUT / f"{stem}.png")


if __name__ == "__main__":
    main()
