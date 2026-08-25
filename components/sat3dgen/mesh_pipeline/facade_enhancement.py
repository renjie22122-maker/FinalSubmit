"""
立面纹理增强模块
-------------
通过 FrankenGAN (bikeGAN) 对建筑模型进行立面纹理增强。

工作流：
  1. BigSUR (empty2windows) → 立面语义分割
  2. FrankenGAN (facade_windows) → 立面纹理增强
  3. FrankenGAN (labels2door_5) → 门纹理增强
  4. 将增强纹理映射回 3D 模型
"""

import os
import time
import uuid
import shutil
import numpy as np
from pathlib import Path
from typing import Optional

from PIL import Image

from .config import Config
from .io import parse_obj, write_obj


# ============================================================
# FrankenGAN 网络调用（文件监视模式）
# ============================================================

def _run_frankengan_network(net_name: str, input_img: np.ndarray,
                             config: Config,
                             wait_timeout: int = 60) -> Optional[np.ndarray]:
    """
    通过文件系统与 FrankenGAN 交互（文件监视模式）。

    1. 写入 input/{net_name}/val/{name}.png
    2. 写入 input/{net_name}/val/go 触发文件
    3. 等待 output/{net_name}/{job_name}/{name}.png

    Parameters
    ----------
    net_name : 网络名称（对应 checkpoints/ 下的目录名）
    input_img : (H, W, 3) uint8
    config : 配置
    wait_timeout : 等待超时（秒）

    Returns
    -------
    输出图像 (H, W, 3) uint8，或 None
    """
    bikegan = config.bikegan_root

    if not bikegan.exists():
        print(f"    [WARN] FrankenGAN 根目录不存在: {bikegan}")
        return None

    checkpoint_dir = bikegan / "checkpoints" / net_name
    if not (checkpoint_dir / "latest_net_G.pth").exists():
        print(f"    [WARN] 网络权重不存在: {checkpoint_dir / 'latest_net_G.pth'}")
        return None

    name = str(uuid.uuid4())[:8]
    job_name = f"pipeline_{name}"

    # 主输入
    input_dir = bikegan / "input" / net_name / "val"
    input_dir.mkdir(parents=True, exist_ok=True)
    input_path = input_dir / f"{name}.png"
    Image.fromarray(input_img).save(str(input_path))

    # 空条件（全黑图像）
    empty_dir = bikegan / "input" / f"{net_name}_empty" / "val"
    empty_dir.mkdir(parents=True, exist_ok=True)
    empty_img = np.zeros((256, 256, 3), dtype=np.uint8)
    Image.fromarray(empty_img).save(str(empty_dir / f"{name}.png"))

    # 度量图（红色掩码）
    metrics_dir = bikegan / "input" / f"{net_name}_metrics" / "val"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    metrics_img = np.zeros((256, 256, 3), dtype=np.uint8)
    metrics_img[:, :, 0] = 255
    Image.fromarray(metrics_img).save(str(metrics_dir / f"{name}.png"))

    # 位置图（归一化 x,y 坐标）
    imgpos_dir = bikegan / "input" / f"{net_name}_imgpos" / "val"
    imgpos_dir.mkdir(parents=True, exist_ok=True)
    h, w = 256, 256
    x_coords = np.tile(np.linspace(0, 1, w), (h, 1))
    y_coords = np.tile(np.linspace(0, 1, h)[:, None], (1, w))
    imgpos_img = np.stack([x_coords, y_coords, np.zeros((h, w))], axis=2)
    imgpos_img = (imgpos_img * 255).astype(np.uint8)
    Image.fromarray(imgpos_img).save(str(imgpos_dir / f"{name}.png"))

    # 写入 go 触发文件
    go_path = bikegan / "input" / net_name / "val" / "go"
    with open(str(go_path), 'w') as f:
        f.write(job_name)

    # 等待输出
    output_dir = bikegan / "output" / net_name / job_name
    output_path = output_dir / f"{name}.png"

    print(f"    等待 FrankenGAN [{net_name}] 处理... (超时 {wait_timeout}s)")
    start_time = time.time()
    while time.time() - start_time < wait_timeout:
        if output_path.exists():
            result = np.array(Image.open(str(output_path)).convert("RGB"))
            _cleanup_temp_files(name, input_dir, empty_dir, metrics_dir,
                               imgpos_dir, go_path, output_dir)
            return result
        time.sleep(0.5)

    print(f"    [TIMEOUT] FrankenGAN [{net_name}] 在 {wait_timeout}s 内未返回")
    return None


def _cleanup_temp_files(name, input_dir, empty_dir, metrics_dir,
                         imgpos_dir, go_path, output_dir):
    """清理 FrankenGAN 临时文件"""
    try:
        shutil.rmtree(str(output_dir), ignore_errors=True)
        for d in [input_dir, empty_dir, metrics_dir, imgpos_dir]:
            p = d / f"{name}.png"
            if p.exists():
                p.unlink()
        if go_path.exists():
            go_path.unlink()
    except Exception:
        pass


def _start_bikegan_if_needed(config: Config) -> bool:
    """检查 FrankenGAN 是否在运行"""
    bikegan = config.bikegan_root
    if not bikegan.exists():
        print(f"  [WARN] FrankenGAN 根目录不存在: {bikegan}")
        return False

    all_networks = [config.bigsur_checkpoint] + config.frankengan_checkpoints
    all_ok = True
    for net_name in all_networks:
        ckpt = bikegan / "checkpoints" / net_name / "latest_net_G.pth"
        if not ckpt.exists():
            all_ok = False
            break

    if not all_ok:
        return False

    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('test_interactive.py' in c for c in cmdline):
                    print(f"  FrankenGAN 已在运行 (PID={proc.info['pid']})")
                    return True
            except Exception:
                pass
    except ImportError:
        pass

    print(f"\n  {'=' * 50}")
    print(f"  FrankenGAN (bikeGAN) 未运行!")
    print(f"  请在另一个终端中手动启动:")
    print(f"    cd {bikegan}")
    print(f"    python test_interactive.py")
    print(f"  {'=' * 50}")
    return False


# ============================================================
# 纹理映射
# ============================================================

def _apply_texture_to_obj(vertices: np.ndarray, faces: np.ndarray,
                           texture_img: np.ndarray) -> np.ndarray:
    """
    将 2D 纹理图像映射到 3D OBJ 顶点颜色（正交投影）。

    对全景图（宽 > 高），使用 (x, z) 水平投影；
    对单张街景，使用 (x, y) 正面投影。
    """
    tex_h, tex_w = texture_img.shape[:2]
    is_panorama = tex_w > tex_h * 1.5

    if is_panorama:
        xs = vertices[:, 0]
        zs = vertices[:, 2]
        x_min, x_max = xs.min(), xs.max()
        z_min, z_max = zs.min(), zs.max()

        for i in range(len(vertices)):
            px = int((vertices[i, 0] - x_min) / (x_max - x_min + 1e-6) * (tex_w - 1))
            py = int((vertices[i, 2] - z_min) / (z_max - z_min + 1e-6) * (tex_h - 1))
            px = np.clip(px, 0, tex_w - 1)
            py = np.clip(py, 0, tex_h - 1)
            vertices[i, 3:6] = texture_img[py, px].astype(np.float64) / 255.0
    else:
        xs = vertices[:, 0]
        ys = vertices[:, 1]
        x_min, x_max = xs.min(), xs.max()
        y_min, y_max = ys.min(), ys.max()

        for i in range(len(vertices)):
            px = int((vertices[i, 0] - x_min) / (x_max - x_min + 1e-6) * (tex_w - 1))
            py = int((vertices[i, 1] - y_min) / (y_max - y_min + 1e-6) * (tex_h - 1))
            px = np.clip(px, 0, tex_w - 1)
            py = np.clip(py, 0, tex_h - 1)
            vertices[i, 3:6] = texture_img[py, px].astype(np.float64) / 255.0

    return vertices


# ============================================================
# 立面增强主入口
# ============================================================

class FacadeEnhancer:
    """立面纹理增强器"""

    def __init__(self, config: Config):
        self.config = config

    def enhance(self, obj_path: Path, streetview_path: Optional[Path],
                output_dir: Path) -> Path:
        """
        对建筑模型进行立面纹理增强。

        工作流：
          7a. 加载全景街景图
          7b. BigSUR 立面语义分割
          7c. FrankenGAN facade_windows 纹理增强
          7d. FrankenGAN labels2door_5 门纹理增强
          7e. 将增强纹理映射回模型

        Returns
        -------
        enhanced_path : 增强后的 OBJ 文件路径
        """
        config = self.config
        output_dir.mkdir(parents=True, exist_ok=True)
        enhanced_path = output_dir / f"{obj_path.stem}_enhanced.obj"

        print(f"\n{'=' * 60}")
        print("立面纹理增强")
        print(f"{'=' * 60}")

        if streetview_path is None or not streetview_path.exists():
            print("  没有街景图，跳过立面增强")
            shutil.copy2(obj_path, enhanced_path)
            return enhanced_path

        if not _start_bikegan_if_needed(config):
            print("  FrankenGAN 不可用，跳过立面增强")
            shutil.copy2(obj_path, enhanced_path)
            return enhanced_path

        # ---- 7a: 加载全景街景图 ----
        print("\n  [7a] 加载全景街景参考图...")
        streetview_img = np.array(Image.open(str(streetview_path)).convert("RGB"))
        streetview_resized = np.array(
            Image.fromarray(streetview_img).resize((256, 256))
        )
        print(f"  街景图: {streetview_img.shape} -> 256x256")

        # ---- 7b: BigSUR 立面语义分割 ----
        print(f"\n  [7b] BigSUR 立面语义分割...")
        facade_labels = _run_frankengan_network(
            config.bigsur_checkpoint, streetview_resized, config,
        )

        if facade_labels is None:
            print("  BigSUR 分割失败，回退到仅 labels2door_5")
            return self._fallback_labels2door(
                obj_path, streetview_resized, enhanced_path
            )

        label_path = output_dir / "bigsur_labels.png"
        Image.fromarray(facade_labels).save(str(label_path))

        # ---- 7c: FrankenGAN facade_windows 立面纹理增强 ----
        print(f"\n  [7c] FrankenGAN 立面纹理增强...")
        facade_input = np.concatenate([streetview_resized, facade_labels], axis=1)
        facade_enhanced = _run_frankengan_network(
            "facade_windows_f013v2_150", facade_input, config,
        )
        if facade_enhanced is None:
            facade_enhanced = facade_labels

        # ---- 7d: FrankenGAN labels2door_5 门纹理增强 ----
        print(f"\n  [7d] FrankenGAN 门纹理增强...")
        door_enhanced = _run_frankengan_network(
            "labels2door_5", facade_enhanced, config,
        )
        if door_enhanced is None:
            door_enhanced = facade_enhanced

        final_tex_path = output_dir / "frankengan_final_texture.png"
        Image.fromarray(door_enhanced).save(str(final_tex_path))

        # ---- 7e: 将纹理映射回模型 ----
        print(f"\n  [7e] 纹理映射...")
        return self._apply_and_save(obj_path, door_enhanced, enhanced_path)

    def _fallback_labels2door(self, obj_path, streetview_resized, enhanced_path):
        """回退方案：仅使用 labels2door_5"""
        door_enhanced = _run_frankengan_network(
            "labels2door_5", streetview_resized, self.config,
        )
        if door_enhanced is not None:
            return self._apply_and_save(obj_path, door_enhanced, enhanced_path,
                                        header="FrankenGAN Enhanced (labels2door_5 only)")
        shutil.copy2(obj_path, enhanced_path)
        return enhanced_path

    def _apply_and_save(self, obj_path, texture, enhanced_path,
                         header=""):
        """应用纹理并保存 OBJ"""
        try:
            vertices, faces = parse_obj(obj_path)
            vertices = _apply_texture_to_obj(vertices, faces, texture)
            default_header = ("FrankenGAN Enhanced Building Model\n"
                              "Pipeline: BigSUR(empty2windows) -> "
                              "facade_windows -> labels2door_5")
            write_obj(vertices, faces, enhanced_path,
                      header=header or default_header)
            print(f"  立面增强完成: {enhanced_path}")
            return enhanced_path
        except Exception as e:
            print(f"  纹理映射失败: {e}")
            shutil.copy2(obj_path, enhanced_path)
            return enhanced_path