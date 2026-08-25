"""
网格 IO 模块
----------
OBJ / PLY 的读取和写入。
"""

import os
import numpy as np
from pathlib import Path
from typing import Tuple, Optional


def parse_obj(obj_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    """
    解析 Wavefront OBJ 文件。

    Returns
    -------
    vertices : (N, 6+)  float64  [x, y, z, r, g, b]
    faces : (M, 3)  int32  三角形面索引
    """
    vertices = []
    faces = []
    with open(str(obj_path), "r") as f:
        for line in f:
            if line.startswith("v "):
                parts = line.strip().split()
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                if len(parts) >= 7:
                    r, g, b = float(parts[4]), float(parts[5]), float(parts[6])
                else:
                    r, g, b = 0.5, 0.5, 0.5
                vertices.append([x, y, z, r, g, b])
            elif line.startswith("f "):
                parts = line.strip().split()[1:]
                face = [int(p.split("/")[0]) - 1 for p in parts]
                if len(face) >= 3:
                    faces.append(face[:3])

    if not vertices:
        return np.empty((0, 6), dtype=np.float64), np.empty((0, 3), dtype=np.int32)

    return np.array(vertices, dtype=np.float64), np.array(faces, dtype=np.int32)


def write_obj(vertices: np.ndarray, faces: np.ndarray,
              output_path: Path, header: str = "") -> Path:
    """
    将网格写入 OBJ 文件。

    Parameters
    ----------
    vertices : (N, 6)  [x, y, z, r, g, b]
    faces : (M, 3)
    output_path : 输出路径
    header : 文件头注释
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(output_path), "w") as f:
        if header:
            for line in header.strip().split("\n"):
                f.write(f"# {line}\n")
        f.write(f"# {len(vertices)} vertices, {len(faces)} faces\n")
        for v in vertices:
            if len(v) >= 6:
                f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f} "
                        f"{v[3]:.6f} {v[4]:.6f} {v[5]:.6f}\n")
            else:
                f.write(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f} 0.5 0.5 0.5\n")
        for face in faces:
            f.write(f"f {face[0] + 1} {face[1] + 1} {face[2] + 1}\n")

    print(f"  OBJ: {output_path} ({os.path.getsize(output_path) / 1024 / 1024:.1f} MB)")
    return output_path


def write_ply(vertices: np.ndarray, faces: np.ndarray,
              output_path: Path) -> Optional[Path]:
    """
    将网格写入 PLY 文件（ASCII）。

    Returns
    -------
    输出路径，失败返回 None
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(str(output_path), "w") as f:
            f.write("ply\nformat ascii 1.0\n")
            f.write(f"element vertex {len(vertices)}\n")
            f.write("property float x\nproperty float y\nproperty float z\n")
            f.write("property uchar red\nproperty uchar green\nproperty uchar blue\n")
            f.write(f"element face {len(faces)}\n")
            f.write("property list uchar int vertex_indices\n")
            f.write("end_header\n")
            for v in vertices:
                r = int(np.clip(v[3] * 255, 0, 255))
                g = int(np.clip(v[4] * 255, 0, 255))
                b = int(np.clip(v[5] * 255, 0, 255))
                f.write(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f} {r} {g} {b}\n")
            for face in faces:
                f.write(f"3 {face[0]} {face[1]} {face[2]}\n")
        print(f"  PLY: {output_path} ({os.path.getsize(output_path) / 1024 / 1024:.1f} MB)")
        return output_path
    except Exception as e:
        print(f"  PLY 导出失败: {e}")
        return None