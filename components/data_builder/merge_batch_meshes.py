"""
Merge batch-generated OBJ meshes into a single continuous mesh.

The input OBJ files are organized in a grid based on their filename lat/lon
coordinates. Each model has 10% overlap with neighbors, and the edge 5% is
discarded before merging to create a seamless continuous surface.

Key features:
  - Correct Y-axis alignment: all models share the same Y origin (Y_min aligned)
  - 5% edge discard on each side to remove overlap artifacts
  - Smooth blending in overlap regions via vertex averaging
  - Preserves vertex colors from original OBJ files
  - Outputs a single merged OBJ file

Coordinate system:
  - Local X: East-West direction (from filename lon)
  - Local Y: North-South direction (from filename lat)
  - Local Z: Elevation (height)
  - All local coordinates are normalized to approximately [-0.81, 0.81]

Usage:
    python merge_batch_meshes.py
    python merge_batch_meshes.py --input-dir external/data/meshes/batch_meshes
    python merge_batch_meshes.py --output merged_mesh.obj
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import trimesh
from scipy.spatial import cKDTree


# ── Constants ─────────────────────────────────────────────────────────
SAT_PATTERN = re.compile(r"sat_([0-9.]+)_([0-9.\-]+)\.obj$")


def parse_filename(path: Path) -> Tuple[float, float]:
    """Extract (lat, lon) from filename like sat_51.503220_-0.121768.obj"""
    m = SAT_PATTERN.match(path.name)
    if not m:
        raise ValueError(f"Cannot parse coordinates from {path.name}")
    return float(m.group(1)), float(m.group(2))


def load_mesh_with_color(path: Path) -> Tuple[trimesh.Trimesh, np.ndarray | None]:
    """Load an OBJ mesh and extract vertex colors if present."""
    mesh = trimesh.load(str(path))
    if mesh.vertices is None or len(mesh.vertices) == 0:
        raise ValueError(f"Empty mesh: {path}")

    colors = None
    if hasattr(mesh, 'visual') and mesh.visual is not None:
        try:
            if hasattr(mesh.visual, 'vertex_colors') and mesh.visual.vertex_colors is not None:
                colors = mesh.visual.vertex_colors.copy()
        except Exception:
            pass

    return mesh, colors


def geo_to_meters(lat: float, lon: float, ref_lat: float, ref_lon: float) -> Tuple[float, float]:
    """Convert lat/lon offset to meters (approximate)."""
    dy = (lat - ref_lat) * 111320.0
    dx = (lon - ref_lon) * 111320.0 * math.cos(math.radians(ref_lat))
    return dx, dy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge batch OBJ meshes into a single continuous mesh"
    )
    parser.add_argument("--input-dir", type=Path,
                        default=Path("external/data/meshes/batch_meshes"),
                        help="Directory containing OBJ files")
    parser.add_argument("--output", type=Path, default=Path("merged_mesh.obj"),
                        help="Output merged OBJ file path")
    parser.add_argument("--discard", type=float, default=0.05,
                        help="Fraction to discard from each edge (default: 0.05 = 5%%)")
    parser.add_argument("--blend-radius", type=float, default=0.02,
                        help="Radius for blending overlap regions in local coords (default: 0.02)")
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    # ═══════════════════════════════════════════════════════════════════
    # 1. Scan and organize OBJ files into a grid
    # ═══════════════════════════════════════════════════════════════════
    obj_files = sorted(input_dir.glob("*.obj"))
    if not obj_files:
        raise FileNotFoundError(f"No .obj files found in {input_dir}")

    print(f"Found {len(obj_files)} OBJ files")

    file_coords: List[Tuple[Path, float, float]] = []
    for f in obj_files:
        try:
            lat, lon = parse_filename(f)
            file_coords.append((f, lat, lon))
        except ValueError:
            print(f"  Skipping {f.name}")
            continue

    all_lats = sorted(set(c[1] for c in file_coords))
    all_lons = sorted(set(c[2] for c in file_coords))
    n_rows = len(all_lats)
    n_cols = len(all_lons)
    print(f"Grid: {n_rows} rows (lat) × {n_cols} cols (lon) = {n_rows * n_cols} meshes")

    # Build grid: {(row, col): (path, lat, lon)}
    grid: Dict[Tuple[int, int], Tuple[Path, float, float]] = {}
    for f, lat, lon in file_coords:
        row = all_lats.index(lat)
        col = all_lons.index(lon)
        grid[(row, col)] = (f, lat, lon)

    # ═══════════════════════════════════════════════════════════════════
    # 2. Determine local coordinate extent from first mesh
    # ═══════════════════════════════════════════════════════════════════
    first_path = file_coords[0][0]
    first_mesh, _ = load_mesh_with_color(first_path)
    verts = first_mesh.vertices

    # The local coordinate system: X and Y range from -half_extent to +half_extent
    x_min, x_max = verts[:, 0].min(), verts[:, 0].max()
    y_min, y_max = verts[:, 1].min(), verts[:, 1].max()
    half_extent_x = (x_max - x_min) / 2.0
    half_extent_y = (y_max - y_min) / 2.0
    half_extent = max(half_extent_x, half_extent_y)

    print(f"\nLocal coordinate system:")
    print(f"  X range: [{x_min:.4f}, {x_max:.4f}] (half-extent: {half_extent_x:.4f})")
    print(f"  Y range: [{y_min:.4f}, {y_max:.4f}] (half-extent: {half_extent_y:.4f})")
    print(f"  Using half-extent: {half_extent:.4f}")

    # With 10% overlap, the spacing between adjacent model centers is:
    # spacing = 2 * half_extent * (1 - overlap_ratio)
    overlap_ratio = 0.10
    spacing = 2.0 * half_extent * (1.0 - overlap_ratio)
    print(f"  Overlap: {overlap_ratio*100:.0f}%")
    print(f"  Spacing between centers: {spacing:.4f}")

    # Edge discard parameters
    discard = args.discard
    keep_start = -half_extent + (2 * half_extent * discard)
    keep_end = half_extent - (2 * half_extent * discard)
    print(f"  Discard {discard*100:.0f}% from each edge")
    print(f"  Keep range: [{keep_start:.4f}, {keep_end:.4f}]")

    # ═══════════════════════════════════════════════════════════════════
    # 3. Load all meshes and transform to world coordinates
    # ═══════════════════════════════════════════════════════════════════
    ref_lat = (all_lats[0] + all_lats[-1]) / 2.0
    ref_lon = (all_lons[0] + all_lons[-1]) / 2.0

    print(f"\nLoading and transforming {len(grid)} meshes...")

    mesh_data = []
    for row in range(n_rows):
        for col in range(n_cols):
            key = (row, col)
            if key not in grid:
                continue

            fpath, lat, lon = grid[key]
            mesh, colors = load_mesh_with_color(fpath)
            local_verts = mesh.vertices.copy()

            # Compute world position offset from reference
            dx, dy = geo_to_meters(lat, lon, ref_lat, ref_lon)

            # Transform to world coordinates:
            #   world_x = local_x + dx  (East-West)
            #   world_y = local_y + dy  (North-South)
            #   world_z = local_z       (elevation, keep as-is)
            world_verts = np.zeros_like(local_verts)
            world_verts[:, 0] = local_verts[:, 0] + dx
            world_verts[:, 1] = local_verts[:, 1] + dy
            world_verts[:, 2] = local_verts[:, 2]

            mesh_data.append({
                'row': row,
                'col': col,
                'lat': lat,
                'lon': lon,
                'local_verts': local_verts,
                'world_verts': world_verts,
                'faces': mesh.faces.copy(),
                'colors': colors,
                'dx': dx,
                'dy': dy,
            })

    # ═══════════════════════════════════════════════════════════════════
    # 4. Apply edge discarding
    # ═══════════════════════════════════════════════════════════════════
    print("Applying edge discarding...")

    for md in mesh_data:
        local_verts = md['local_verts']
        keep_mask = np.ones(len(local_verts), dtype=bool)

        # Keep only vertices within the valid range in local X and Y
        keep_mask &= (local_verts[:, 0] >= keep_start) & (local_verts[:, 0] <= keep_end)
        keep_mask &= (local_verts[:, 1] >= keep_start) & (local_verts[:, 1] <= keep_end)

        md['keep_mask'] = keep_mask

    # ═══════════════════════════════════════════════════════════════════
    # 5. Build the merged mesh
    # ═══════════════════════════════════════════════════════════════════
    print("Building merged mesh...")

    # Collect all kept vertices and build source mapping
    all_kept_verts = []
    all_kept_sources: List[Tuple[int, int]] = []  # (mesh_idx, local_vert_idx)

    for mi, md in enumerate(mesh_data):
        kept_indices = np.where(md['keep_mask'])[0]
        for vi in kept_indices:
            all_kept_verts.append(md['world_verts'][vi])
            all_kept_sources.append((mi, vi))

    all_kept_verts = np.array(all_kept_verts)
    n_kept = len(all_kept_verts)
    print(f"  Kept vertices: {n_kept}")

    # Build mapping: (mesh_idx, local_vert_idx) -> global_vert_idx
    source_to_global: Dict[Tuple[int, int], int] = {}
    for gi, (mi, vi) in enumerate(all_kept_sources):
        source_to_global[(mi, vi)] = gi

    # ═══════════════════════════════════════════════════════════════════
    # 6. Blend overlapping regions
    # ═══════════════════════════════════════════════════════════════════
    print("Blending overlapping regions...")

    blend_radius = args.blend_radius
    tree = cKDTree(all_kept_verts[:, :2])  # query by X, Y only

    # For each vertex, find nearby vertices from OTHER meshes and average
    blend_updates: Dict[int, List[np.ndarray]] = {}

    for gi, (mi, vi) in enumerate(all_kept_sources):
        pos = all_kept_verts[gi]
        indices = tree.query_ball_point(pos[:2], r=blend_radius)

        if len(indices) <= 1:
            continue

        # Find vertices from different meshes
        other_positions = []
        for idx in indices:
            if idx == gi:
                continue
            other_mi, _ = all_kept_sources[idx]
            if other_mi != mi:
                other_positions.append(all_kept_verts[idx])

        if other_positions:
            # Average with nearby vertices from other meshes
            avg_pos = np.mean([pos] + other_positions, axis=0)
            if gi not in blend_updates:
                blend_updates[gi] = []
            blend_updates[gi].append(avg_pos)

    # Apply blending
    blend_count = 0
    for gi, updates in blend_updates.items():
        if updates:
            all_kept_verts[gi] = np.mean(updates, axis=0)
            blend_count += 1

    print(f"  Blended vertices: {blend_count}")

    # ═══════════════════════════════════════════════════════════════════
    # 7. Build faces for the merged mesh
    # ═══════════════════════════════════════════════════════════════════
    print("Building faces...")

    final_faces_list = []
    discarded_faces = 0

    for mi, md in enumerate(mesh_data):
        faces = md['faces']
        kept_indices = set(np.where(md['keep_mask'])[0])

        for face in faces:
            new_face = []
            valid = True
            for fv in face:
                if fv in kept_indices:
                    gi = source_to_global.get((mi, fv))
                    if gi is not None:
                        new_face.append(gi)
                    else:
                        valid = False
                        break
                else:
                    valid = False
                    break

            if valid and len(new_face) == 3:
                final_faces_list.append(new_face)
            else:
                discarded_faces += 1

    final_faces = np.array(final_faces_list, dtype=np.int32)
    print(f"  Faces kept: {len(final_faces)}, discarded: {discarded_faces}")

    # ═══════════════════════════════════════════════════════════════════
    # 8. Collect vertex colors
    # ═══════════════════════════════════════════════════════════════════
    print("Collecting vertex colors...")

    final_colors_list = []
    for gi, (mi, vi) in enumerate(all_kept_sources):
        md = mesh_data[mi]
        if md['colors'] is not None and vi < len(md['colors']):
            final_colors_list.append(md['colors'][vi][:3])
        else:
            final_colors_list.append([128, 128, 128])

    final_colors = np.array(final_colors_list, dtype=np.uint8)

    # ═══════════════════════════════════════════════════════════════════
    # 9. Save merged mesh
    # ═══════════════════════════════════════════════════════════════════
    print(f"\nCreating final mesh...")

    merged_mesh = trimesh.Trimesh(
        vertices=all_kept_verts,
        faces=final_faces,
        vertex_colors=final_colors,
        process=False,
    )

    output_path = args.output.resolve()
    merged_mesh.export(str(output_path), file_type='obj')
    print(f"Merged mesh saved to: {output_path}")

    # ═══════════════════════════════════════════════════════════════════
    # 10. Statistics
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"MERGE STATISTICS")
    print(f"{'='*60}")
    print(f"Input meshes:       {len(mesh_data)}")
    print(f"Output vertices:    {len(all_kept_verts):,}")
    print(f"Output faces:       {len(final_faces):,}")
    print(f"Blended vertices:   {blend_count:,}")
    print(f"Discarded faces:    {discarded_faces:,}")
    print()
    print(f"World bounds:")
    print(f"  X (East-West):  [{all_kept_verts[:, 0].min():.3f}, {all_kept_verts[:, 0].max():.3f}] m")
    print(f"  Y (North-South): [{all_kept_verts[:, 1].min():.3f}, {all_kept_verts[:, 1].max():.3f}] m")
    print(f"  Z (Elevation):  [{all_kept_verts[:, 2].min():.3f}, {all_kept_verts[:, 2].max():.3f}] m")
    print()
    print(f"Size:")
    print(f"  Width (X):  {all_kept_verts[:, 0].max() - all_kept_verts[:, 0].min():.3f} m")
    print(f"  Height (Y): {all_kept_verts[:, 1].max() - all_kept_verts[:, 1].min():.3f} m")
    print(f"  Elevation range (Z): {all_kept_verts[:, 2].max() - all_kept_verts[:, 2].min():.3f} m")
    print(f"{'='*60}")
    print("Done!")


if __name__ == "__main__":
    main()
