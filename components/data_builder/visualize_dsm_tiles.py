from __future__ import annotations

from pathlib import Path
import math

import numpy as np
from PIL import Image, ImageDraw
import rasterio


def normalize_tile(arr: np.ndarray) -> np.ndarray:
    valid = ~np.isnan(arr)
    out = np.zeros_like(arr, dtype=np.uint8)
    if not np.any(valid):
        return out

    vals = arr[valid]
    p2 = float(np.percentile(vals, 2))
    p98 = float(np.percentile(vals, 98))
    denom = max(p98 - p2, 1e-6)

    norm = np.clip((arr - p2) / denom, 0.0, 1.0)
    norm = np.nan_to_num(norm, nan=0.0)
    out = (norm * 255.0).astype(np.uint8)
    return out


def make_grid(preview_paths: list[Path], out_path: Path, tile_size: int = 320) -> None:
    if not preview_paths:
        return

    cols = min(3, len(preview_paths))
    rows = math.ceil(len(preview_paths) / cols)

    card_w = tile_size
    card_h = tile_size + 44
    canvas = Image.new("RGB", (cols * card_w, rows * card_h), color=(18, 18, 18))
    draw = ImageDraw.Draw(canvas)

    for i, p in enumerate(preview_paths):
        r = i // cols
        c = i % cols
        x0 = c * card_w
        y0 = r * card_h

        img = Image.open(p).convert("L").resize((tile_size, tile_size), Image.BILINEAR)
        rgb = Image.merge("RGB", (img, img, img))
        canvas.paste(rgb, (x0, y0))

        label = p.stem
        draw.text((x0 + 6, y0 + tile_size + 8), label, fill=(235, 235, 235))

    canvas.save(out_path)


def run() -> None:
    root = Path("LondonDataSet/London_DSM")
    out_dir = Path("LondonDataSet/London_DSM_preview")
    out_dir.mkdir(parents=True, exist_ok=True)

    preview_paths: list[Path] = []

    for tif in sorted(root.glob("*.tif")):
        with rasterio.open(tif) as ds:
            arr = ds.read(1).astype(np.float32)
            nodata = ds.nodata
            if nodata is not None:
                arr[arr == nodata] = np.nan

            img_u8 = normalize_tile(arr)

        out_path = out_dir / f"{tif.stem}.png"
        Image.fromarray(img_u8, mode="L").save(out_path)
        preview_paths.append(out_path)

    make_grid(preview_paths, out_dir / "DSM_tiles_overview.png", tile_size=320)

    print(f"Preview folder: {out_dir.resolve()}")
    print(f"Tile previews: {len(preview_paths)}")
    print(f"Overview image: {(out_dir / 'DSM_tiles_overview.png').resolve()}")


if __name__ == "__main__":
    run()
