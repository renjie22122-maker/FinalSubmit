"""
CLI 入口
-------
命令行界面，封装 argparser 和管线调用。
"""

import argparse
from pathlib import Path
import sys

from .config import Config
from .pipeline import Pipeline


def main():
    """CLI 主入口"""
    parser = argparse.ArgumentParser(
        description="Interactive Building-to-3D Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 命令行模式（指定建筑坐标）
  python -m mesh_pipeline --api-key YOUR_KEY --lat 51.5109 --lon -0.1349

  # 仅下载数据
  python -m mesh_pipeline --api-key YOUR_KEY --lat 51.5109 --lon -0.1349 --skip-inference

  # 仅合并已有模型
  python -m mesh_pipeline --lat 51.5109 --lon -0.1349 --skip-download --skip-inference

  # 交互模式（需要 Jupyter Notebook）
  python -m mesh_pipeline --api-key YOUR_KEY --interactive
        """,
    )

    parser.add_argument("--api-key", type=str, default="",
                        help="Google API Key")
    parser.add_argument("--lat", type=float, default=None,
                        help="建筑纬度")
    parser.add_argument("--lon", type=float, default=None,
                        help="建筑经度")
    parser.add_argument("--name", type=str, default="building",
                        help="建筑名称")
    parser.add_argument("--work-dir", type=str, default="pipeline_output",
                        help="工作目录")
    parser.add_argument("--skip-download", action="store_true",
                        help="跳过数据下载")
    parser.add_argument("--skip-inference", action="store_true",
                        help="跳过 Sat3DGen 推理")

    args = parser.parse_args()

    # 配置
    config = Config(
        google_api_key=args.api_key,
        work_dir=Path(args.work_dir),
    )

    # 命令行模式
    if args.lat is None or args.lon is None:
        parser.print_help()
        print("\n请提供 --lat 和 --lon 参数。")
        return

    pipeline = Pipeline(config)
    pipeline.run(
        lat=args.lat,
        lon=args.lon,
        building_name=args.name,
        skip_download=args.skip_download,
        skip_inference=args.skip_inference,
    )


if __name__ == "__main__":
    main()