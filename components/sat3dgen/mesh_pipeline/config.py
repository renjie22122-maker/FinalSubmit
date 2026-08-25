"""
全局配置模块
----------
所有可调参数集中管理，支持从 dict / CLI / 代码灵活初始化。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Config:
    """Pipeline 全局配置"""

    # ==================== Google API ====================
    google_api_key: str = ""

    # ==================== 路径 ====================
    work_dir: Path = Path("pipeline_output")
    sat_dir: Path = field(init=False)
    pano_dir: Path = field(init=False)
    mesh_dir: Path = field(init=False)
    output_dir: Path = field(init=False)
    osm_dir: Path = field(init=False)

    # ==================== 卫星图参数 ====================
    zoom: int = 20
    img_size: int = 640
    lon_step: float = 0.000772
    lat_step: float = 0.000481
    overlap_ratio: float = 0.10
    crop_ratio: float = 0.05

    # ==================== Sat3DGen 参数 ====================
    sat3dgen_dir: Path = Path("Sat3DGen")
    model_path: str = "qian43/Sat3DGen"
    mesh_resolution: int = 256
    gradio_api_url: str = "http://localhost:7860"

    # ==================== DSM 参数 ====================
    dsm_dir: Path = Path("LondonDataSet/London_DSM")
    dsm_files: List[str] = field(default_factory=lambda: [
        "TQ27ne_FZ_DSM_1m.tif", "TQ28se_FZ_DSM_1m.tif",
        "TQ37nw_FZ_DSM_1m.tif", "TQ38sw_FZ_DSM_1m.tif",
    ])
    dsm_gaussian_sigma: float = 3.0

    # ==================== OSM 参数 ====================
    osm_data_dir: Path = Path("LondonDataSet/osm_features")
    osm_search_radius_m: float = 50.0

    # ==================== 拼接参数 ====================
    stitch_distance: float = 0.5
    max_y_diff_for_merge: float = 2.0

    # ==================== 街景参数 ====================
    pano_fov: int = 90
    pano_size: int = 640
    pano_headings: List[int] = field(default_factory=lambda: [0, 90, 180, 270])
    pano_offset_m: float = 15.0
    pano_spacing_m: float = 35.0

    # ==================== FrankenGAN 参数 ====================
    bikegan_root: Path = field(
        default_factory=lambda: Path(os.environ.get("BIKEGAN_ROOT", "external/bikegan"))
    )
    bikegan_resolution: int = 256
    bigsur_checkpoint: str = "empty2windows_f009v2_400"
    frankengan_checkpoints: List[str] = field(default_factory=lambda: [
        "facade_windows_f013v2_150",
        "labels2door_5",
    ])
    frankengan_timeout: int = 60

    # ==================== 线程 ====================
    download_workers: int = 8
    inference_workers: int = 1

    # ==================== 建筑物提取参数 ====================
    building_padding_m: float = 30.0
    max_hole_edges: int = 50

    def __post_init__(self):
        """自动推导子目录路径"""
        wd = Path(self.work_dir)
        self.sat_dir = wd / "satellite"
        self.pano_dir = wd / "panorama"
        self.mesh_dir = wd / "meshes"
        self.output_dir = wd / "final"
        self.osm_dir = wd / "osm"

    @classmethod
    def from_args(cls, args) -> "Config":
        """从 argparse Namespace 构建配置"""
        return cls(
            google_api_key=args.api_key or "",
            work_dir=Path(args.work_dir or "pipeline_output"),
        )
