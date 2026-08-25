"""
模型导出模块
----------
将网格数据导出为 OBJ / PLY 格式。
"""

import os
import numpy as np
from pathlib import Path
from typing import Tuple, Optional

from .io import write_obj, write_ply
from .types import MeshData


def export_model(vertices: np.ndarray, faces: np.ndarray,
                 output_dir: Path, name: str = "building",
                 export_ply: bool = True) -> Tuple[Path, Optional[Path]]:
    """
    导出网格为 OBJ（和可选的 PLY）。

    Returns
    -------
    obj_path : Path
    ply_path : Path | None
    """
    print(f"\n  导出最终模型: {name}")
    output_dir.mkdir(parents=True, exist_ok=True)

    obj_path = write_obj(vertices, faces, output_dir / f"{name}.obj")

    ply_path = None
    if export_ply:
        ply_path = write_ply(vertices, faces, output_dir / f"{name}.ply")

    return obj_path, ply_path


def export_mesh_data(mesh: MeshData, output_dir: Path, name: str = "building",
                     export_ply: bool = True) -> Tuple[Path, Optional[Path]]:
    """从 MeshData 导出"""
    return export_model(mesh.vertices, mesh.faces, output_dir, name, export_ply)