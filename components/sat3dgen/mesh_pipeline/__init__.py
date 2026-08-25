"""
Mesh Pipeline - Modular 3D Building Reconstruction Pipeline
===========================================================
从卫星图 + OSM + DSM 生成建筑的 3D 模型，支持多 tile 拼接、
语义高度修正、网格清理、立面纹理增强等功能。

Usage:
    from mesh_pipeline import Config, Pipeline
    config = Config(google_api_key="...")
    pipeline = Pipeline(config)
    pipeline.run(lat=51.5109, lon=-0.1349)
"""

from .config import Config
from .pipeline import Pipeline

__version__ = "2.0.0"
__all__ = ["Config", "Pipeline"]