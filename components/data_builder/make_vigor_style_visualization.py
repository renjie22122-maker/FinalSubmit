from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def draw_star(draw: ImageDraw.ImageDraw, x: int, y: int, r: int = 10, color: tuple[int, int, int] = (220, 0, 0)) -> None:
    # Simple 5-point star polygon.
    pts = [
        (x, y - r),
        (x + int(0.35 * r), y - int(0.35 * r)),
        (x + r, y - int(0.3 * r)),
        (x + int(0.5 * r), y + int(0.15 * r)),
        (x + int(0.62 * r), y + r),
        (x, y + int(0.45 * r)),
        (x - int(0.62 * r), y + r),
        (x - int(0.5 * r), y + int(0.15 * r)),
        (x - r, y - int(0.3 * r)),
        (x - int(0.35 * r), y - int(0.35 * r)),
    ]
    draw.polygon(pts, fill=color)


def fit_within(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(src_w * scale))
    new_h = max(1, int(src_h * scale))
    return img.resize((new_w, new_h), Image.Resampling.BILINEAR)


def parse_label_line(line: str) -> tuple[str, list[tuple[str, float, float]]]:
    parts = line.split()
    if len(parts) < 13:
        raise ValueError("Expected 4-candidate VIGOR format line")

    pano_name = parts[0]
    cands: list[tuple[str, float, float]] = []
    for i in (1, 4, 7, 10):
        sat = parts[i]
        dy = float(parts[i + 1])
        dx = float(parts[i + 2])
        cands.append((sat, dy, dx))

    return pano_name, cands


def make_panel(sat_img: Image.Image, dx: float, dy: float, out_size: int = 260) -> Image.Image:
    panel = sat_img.convert("RGB").resize((out_size, out_size), Image.Resampling.BILINEAR)
    draw = ImageDraw.Draw(panel)

    cx = out_size // 2
    cy = out_size // 2

    sx = sat_img.size[0] / out_size
    sy = sat_img.size[1] / out_size

    # Label dy/dx are from panorama -> satellite center.
    # To plot panorama position on the satellite image, invert the vector.
    x = int(round(cx - dx / sx))
    y = int(round(cy - dy / sy))
    x = clamp(x, 8, out_size - 8)
    y = clamp(y, 8, out_size - 8)

    draw_star(draw, x, y, r=10)
    return panel


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a VIGOR-style visualization from a label line")
    parser.add_argument("--city-dir", type=Path, default=Path("LondonDataSet/London"))
    parser.add_argument("--label-file", type=Path, default=Path("LondonDataSet/London/pano_label_balanced.txt"))
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("LondonDataSet/London/vigor_visualization_sample.png"))
    args = parser.parse_args()

    city_dir = args.city_dir.resolve()
    pano_dir = city_dir / "panorama"
    sat_dir = city_dir / "satellite"

    lines = [ln.strip() for ln in args.label_file.resolve().read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError("Label file is empty")

    idx = args.index % len(lines)
    pano_name, cands = parse_label_line(lines[idx])

    pano_path = pano_dir / pano_name
    if not pano_path.exists():
        raise FileNotFoundError(f"Panorama not found: {pano_path}")

    sat_pos, dy_pos, dx_pos = cands[0]
    sat_semi, dy_semi, dx_semi = cands[1]

    sat_pos_path = sat_dir / sat_pos
    sat_semi_path = sat_dir / sat_semi
    if not sat_pos_path.exists() or not sat_semi_path.exists():
        raise FileNotFoundError("Satellite candidate image not found")

    with Image.open(pano_path) as pano:
        pano = pano.convert("RGB")
        pano_view = fit_within(pano, 700, 230)

    with Image.open(sat_pos_path) as s1, Image.open(sat_semi_path) as s2:
        panel_pos = make_panel(s1, dx_pos, dy_pos, out_size=260)
        panel_semi = make_panel(s2, dx_semi, dy_semi, out_size=260)

    W, H = 780, 760
    canvas = Image.new("RGB", (W, H), (235, 235, 235))
    draw = ImageDraw.Draw(canvas)
    font_title = ImageFont.load_default()
    font_big = ImageFont.load_default()

    # Top title
    draw.text((W // 2 - 70, 20), "Street-view Query", fill=(0, 0, 0), font=font_title)

    # Panorama
    px = (W - pano_view.size[0]) // 2
    py = 60
    canvas.paste(pano_view, (px, py))

    # Bottom satellite panels
    left_x, panel_y = 80, 350
    right_x = W - 80 - 260
    canvas.paste(panel_pos, (left_x, panel_y))
    canvas.paste(panel_semi, (right_x, panel_y))

    draw.text((left_x + 60, panel_y + 275), "Positive", fill=(210, 0, 0), font=font_big)
    draw.text((right_x + 28, panel_y + 275), "Semi-positive", fill=(210, 0, 0), font=font_big)
    draw.text((W // 2 - 10, panel_y + 145), "or", fill=(0, 0, 0), font=font_big)
    draw.text((W // 2 - 90, 680), "Aerial-view Reference", fill=(0, 0, 0), font=font_title)

    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    canvas.save(args.output.resolve())

    print(f"index={idx}")
    print(f"pano={pano_name}")
    print(f"positive={sat_pos} dy={dy_pos:.1f} dx={dx_pos:.1f}")
    print(f"semi_positive={sat_semi} dy={dy_semi:.1f} dx={dx_semi:.1f}")
    print(f"output={args.output.resolve()}")


if __name__ == "__main__":
    main()
