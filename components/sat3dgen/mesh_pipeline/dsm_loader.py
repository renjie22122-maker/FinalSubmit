"""
DSM 数据加载模块
--------------
加载 GeoTIFF 格式的 DSM 数据，支持高斯滤波去噪和批量高度查询。
"""

import numpy as np
from pathlib import Path
from typing import List

import rasterio
from scipy.ndimage import gaussian_filter

from .config import Config


class DSMLoader:
    """
    DSM 数据加载器。

    一次性将所有 DSM tile 加载到内存，提供批量高度查询。
    支持高斯滤波去除树冠、汽车等高频噪声。
    """

    def __init__(self, config: Config,
                 apply_gaussian_filter: bool = True,
                 sigma: float = None):
        """
        Parameters
        ----------
        config : Config
        apply_gaussian_filter : 是否对 DSM 做高斯滤波
        sigma : 高斯滤波标准差（像素单位）。
                如果为 None，使用 config.dsm_gaussian_sigma
        """
        if sigma is None:
            sigma = config.dsm_gaussian_sigma

        self.sources: List[dict] = []
        self._load(config, apply_gaussian_filter, sigma)

    def _load(self, config: Config, apply_gaussian_filter: bool, sigma: float):
        """加载所有 DSM tile"""
        print("\n[DSM] 加载 DSM 数据...")

        for fname in config.dsm_files:
            path = config.dsm_dir / fname
            if not path.exists():
                print(f"  [WARN] 文件不存在: {path}")
                continue

            with rasterio.open(str(path)) as src:
                data = src.read(1).astype(np.float64)

                if apply_gaussian_filter:
                    data_orig = data.copy()
                    data = gaussian_filter(data, sigma=sigma)
                    valid_mask = data_orig > -100
                    data[~valid_mask] = data_orig[~valid_mask]

                self.sources.append({
                    'bounds': src.bounds,
                    'transform': src.transform,
                    'data': data,
                    'shape': data.shape,
                })
                print(f"  {fname}: shape={data.shape}")

    def query_heights_batch(self, eastings: np.ndarray,
                            northings: np.ndarray) -> np.ndarray:
        """
        批量查询 DSM 高度。

        Parameters
        ----------
        eastings : (N,)  EPSG:27700 东坐标
        northings : (N,)  EPSG:27700 北坐标

        Returns
        -------
        heights : (N,)  DSM 高度（米），无效区域为 NaN
        """
        n = len(eastings)
        heights = np.full(n, np.nan)

        for src in self.sources:
            b = src['bounds']
            mask = ((eastings >= b.left) & (eastings <= b.right) &
                    (northings >= b.bottom) & (northings <= b.top))
            if not mask.any():
                continue

            idxs = np.where(mask)[0]
            transform = src['transform']
            cols = ((eastings[idxs] - transform.c) / transform.a).astype(np.int64)
            rows = ((northings[idxs] - transform.f) / transform.e).astype(np.int64)

            h, w = src['shape']
            valid = (rows >= 0) & (rows < h) & (cols >= 0) & (cols < w)
            valid_idxs = idxs[valid]
            vals = src['data'][rows[valid], cols[valid]]
            good = vals > -100
            heights[valid_idxs[good]] = vals[good]

        return heights

    def query_single_point(self, easting: float, northing: float) -> float:
        """查询单点高度"""
        result = self.query_heights_batch(
            np.array([easting]), np.array([northing])
        )
        return result[0]

    @property
    def is_empty(self) -> bool:
        return len(self.sources) == 0