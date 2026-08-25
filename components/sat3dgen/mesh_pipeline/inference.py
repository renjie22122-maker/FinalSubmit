"""
Sat3DGen 推理模块
---------------
通过 Gradio API 调用 Sat3DGen 生成 3D 模型。
"""

import shutil
import numpy as np
from pathlib import Path
from typing import List, Optional, Tuple

from .config import Config
from .types import GridTile
from .io import parse_obj


def get_satellite_paths_for_tiles(tiles: List[GridTile],
                                   config: Config) -> List[Path]:
    """
    根据 GridTile 列表获取本地已有的卫星图路径。

    Returns
    -------
    List[Path] : 存在的卫星图路径列表
    """
    sat_files = []
    for tile in tiles:
        path = config.sat_dir / tile.filename
        if path.exists():
            sat_files.append(path)
        else:
            print(f"  [WARN] 卫星图不存在: {tile.filename}")
    return sorted(sat_files)


def run_sat3dgen_inference(sat_path: Path, config: Config) -> Optional[Path]:
    """
    通过 Gradio API 调用 Sat3DGen 对单张卫星图推理。

    与 app.py 的 /generate_mesh 端点交互：
      1. 上传卫星图
      2. 指定 mesh_resolution
      3. 下载生成的 OBJ

    Returns
    -------
    输出的 OBJ 文件路径，或 None
    """
    stem = sat_path.stem
    mesh_dir = config.mesh_dir / stem
    mesh_dir.mkdir(parents=True, exist_ok=True)

    obj_path = mesh_dir / f"{stem}.obj"
    if obj_path.exists():
        print(f"    模型已存在: {obj_path}")
        return obj_path

    api_url = config.gradio_api_url
    print(f"    调用 Sat3DGen API ({api_url}): {sat_path.name}")

    try:
        from gradio_client import Client, handle_file
    except ImportError:
        print(f"    [ERROR] 需要安装 gradio_client: pip install gradio_client")
        return None

    try:
        client = Client(api_url)

        # Step 1: 上传卫星图并生成 mesh
        result = client.predict(
            sat_image_pil=handle_file(str(sat_path.absolute())),
            mesh_resolution=config.mesh_resolution,
            api_name="/generate_mesh",
        )

        if result is None:
            print(f"    [ERROR] Sat3DGen /generate_mesh 返回空")
            return None

        generated_mesh_path = Path(result)
        if not generated_mesh_path.exists():
            print(f"    [ERROR] 生成的 mesh 文件不存在: {generated_mesh_path}")
            return None

        # Step 2: 下载 mesh 文件
        download_result = client.predict(api_name="/download_mesh")

        if download_result is not None:
            downloaded_path = Path(download_result)
            if downloaded_path.exists():
                shutil.copy2(str(downloaded_path), str(obj_path))
                print(f"    -> {obj_path}")
                return obj_path

        # 回退：尝试直接使用 generate_mesh 的结果
        if generated_mesh_path.suffix == '.obj':
            shutil.copy2(str(generated_mesh_path), str(obj_path))
            print(f"    -> {obj_path}")
            return obj_path

        # 在 output_dir 中搜索
        for p in mesh_dir.rglob("*.obj"):
            if p.stat().st_size > 1000:
                shutil.copy2(str(p), str(obj_path))
                print(f"    -> {obj_path}")
                return obj_path

        return None

    except Exception as e:
        print(f"    [ERROR] Sat3DGen API 调用失败: {e}")
        return None


class Sat3DGenRunner:
    """Sat3DGen 推理管理器"""

    def __init__(self, config: Config):
        self.config = config

    def run_batch(self, tiles: List[GridTile]) -> List[Path]:
        """
        批量运行 Sat3DGen 推理。

        Parameters
        ----------
        tiles : 当前建筑需要的 tile 列表

        Returns
        -------
        List[Path] : 生成的 OBJ 文件路径列表
        """
        config = self.config

        print(f"\n{'=' * 60}")
        print("Sat3DGen 批量推理")
        print(f"{'=' * 60}")

        sat_files = get_satellite_paths_for_tiles(tiles, config)

        if not sat_files:
            print("  没有找到卫星图，跳过推理")
            return []

        print(f"  共 {len(sat_files)} 个 tile")

        obj_files = []
        for i, sat_path in enumerate(sat_files):
            print(f"\n  [{i + 1}/{len(sat_files)}] {sat_path.name}")
            obj = run_sat3dgen_inference(sat_path, config)
            if obj:
                obj_files.append(obj)
                print(f"    -> {obj}")
            else:
                print(f"    [FAILED]")

        print(f"\n  成功: {len(obj_files)}/{len(sat_files)}")
        return obj_files

    def load_mesh_for_tile(self, obj_path: Path,
                           origin_lat: float, origin_lon: float,
                           crop_ratio: float = 0.05) -> Tuple[np.ndarray, np.ndarray, float, float]:
        """
        加载单个 tile 的 OBJ，裁切边缘并转为世界坐标。

        Returns
        -------
        world_vertices : (N, 6)
        faces : (M, 3)
        lat : tile 纬度
        lon : tile 经度
        """
        from .utils import extract_lat_lon_from_filename, local_to_world
        import numpy as np

        lat, lon = extract_lat_lon_from_filename(obj_path.name)
        vertices, faces = parse_obj(obj_path)
        vertices, faces = _crop_boundary(vertices, faces, crop_ratio)

        world_verts = local_to_world(
            vertices, lat, lon, origin_lat, origin_lon,
            self.config.lon_step, self.config.lat_step,
            self.config.overlap_ratio,
        )

        return world_verts, faces, lat, lon


def _crop_boundary(vertices: np.ndarray, faces: np.ndarray,
                   crop_ratio: float = 0.05) -> Tuple[np.ndarray, np.ndarray]:
    """
    裁切边界：移除 OBJ 空间边缘的重叠区域。
    """
    import numpy as np

    threshold = 0.81 * (1 - crop_ratio)
    obj_x, obj_z = vertices[:, 0], vertices[:, 2]
    keep_mask = (np.abs(obj_x) <= threshold) & (np.abs(obj_z) <= threshold)
    keep_indices = np.where(keep_mask)[0]

    old_to_new = {old: new for new, old in enumerate(keep_indices)}
    cropped_vertices = vertices[keep_mask]
    cropped_faces = []
    for face in faces:
        if all(v in old_to_new for v in face):
            cropped_faces.append([old_to_new[v] for v in face])

    result_faces = (np.array(cropped_faces, dtype=np.int32)
                    if cropped_faces
                    else np.zeros((0, 3), dtype=np.int32))
    return cropped_vertices, result_faces