"""Analyze OBJ mesh coordinate system for proper merging."""
import os
import trimesh
import numpy as np
from pathlib import Path

p = Path(os.environ.get("MESH_BATCH_DIR", "external/data/meshes/batch_meshes"))
files = sorted(p.glob('*.obj'))

# Analyze first column (lon=-0.121768)
col_files = [f for f in files if f.name.endswith('-0.121768.obj')]

print('=== First column all models (lon=-0.121768) ===')
print(f'{"File":<30} {"Y_min":<10} {"Y_max":<10} {"Z_min":<10} {"Z_max":<10} {"lat":<12}')
print('-'*75)
for f in sorted(col_files):
    m = trimesh.load(str(f))
    lat = float(f.name.split('_')[1])
    print(f'{f.name:<30} {m.vertices[:,1].min():<10.6f} {m.vertices[:,1].max():<10.6f} {m.vertices[:,2].min():<10.6f} {m.vertices[:,2].max():<10.6f} {lat:<12.6f}')

# Check Z median vs lat
print()
print('=== Z median vs lat ===')
for f in sorted(col_files):
    m = trimesh.load(str(f))
    lat = float(f.name.split('_')[1])
    z_median = np.median(m.vertices[:,2])
    print(f'lat={lat:.6f}  Z_median={z_median:.6f}')

# Check Y overlap between adjacent models
print()
print('=== Adjacent model Y overlap ===')
for i in range(len(col_files)-1):
    f1 = sorted(col_files)[i]
    f2 = sorted(col_files)[i+1]
    m1 = trimesh.load(str(f1))
    m2 = trimesh.load(str(f2))
    lat1 = float(f1.name.split('_')[1])
    lat2 = float(f2.name.split('_')[1])
    y1_min, y1_max = m1.vertices[:,1].min(), m1.vertices[:,1].max()
    y2_min, y2_max = m2.vertices[:,1].min(), m2.vertices[:,1].max()
    overlap = y1_max - y2_min
    gap = y2_min - y1_max
    print(f'{lat1:.6f} Y=[{y1_min:.4f},{y1_max:.4f}] -> {lat2:.6f} Y=[{y2_min:.4f},{y2_max:.4f}]  overlap={overlap:.4f}  gap={gap:.4f}')

# Check if Y_min is consistent across all models
print()
print('=== Y_min consistency ===')
y_mins = []
for f in files:
    m = trimesh.load(str(f))
    y_mins.append(m.vertices[:,1].min())
print(f'Y_min: min={min(y_mins):.6f}, max={max(y_mins):.6f}, mean={np.mean(y_mins):.6f}, std={np.std(y_mins):.6f}')

# Check if Y_max is consistent
y_maxs = []
for f in files:
    m = trimesh.load(str(f))
    y_maxs.append(m.vertices[:,1].max())
print(f'Y_max: min={min(y_maxs):.6f}, max={max(y_maxs):.6f}, mean={np.mean(y_maxs):.6f}, std={np.std(y_maxs):.6f}')

# Check X range consistency
print()
print('=== X range consistency ===')
x_mins, x_maxs = [], []
for f in files:
    m = trimesh.load(str(f))
    x_mins.append(m.vertices[:,0].min())
    x_maxs.append(m.vertices[:,0].max())
print(f'X_min: min={min(x_mins):.6f}, max={max(x_mins):.6f}')
print(f'X_max: min={min(x_maxs):.6f}, max={max(x_maxs):.6f}')

# Check if Z values are actual elevation or normalized
print()
print('=== Z range per model ===')
z_ranges = []
for f in files:
    m = trimesh.load(str(f))
    z_ranges.append(m.vertices[:,2].max() - m.vertices[:,2].min())
print(f'Z range: min={min(z_ranges):.6f}, max={max(z_ranges):.6f}, mean={np.mean(z_ranges):.6f}')

# Check first row vs last row Z values
print()
print('=== First row (south) vs last row (north) Z ===')
first_row = [f for f in files if f.name.startswith('sat_51.503220')]
last_row = [f for f in files if f.name.startswith('sat_51.510913')]
for f in first_row[:3]:
    m = trimesh.load(str(f))
    print(f'{f.name}: Z=[{m.vertices[:,2].min():.4f},{m.vertices[:,2].max():.4f}]')
for f in last_row[:3]:
    m = trimesh.load(str(f))
    print(f'{f.name}: Z=[{m.vertices[:,2].min():.4f},{m.vertices[:,2].max():.4f}]')
