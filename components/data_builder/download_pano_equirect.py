from __future__ import annotations

from pathlib import Path
import argparse
import math
from typing import Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

import numpy as np
from PIL import Image
import requests


FACE_PARAMS = {
    "front": (0, 0),
    "right": (90, 0),
    "back": (180, 0),
    "left": (270, 0),
    "up": (0, 90),
    "down": (0, -90),
}


def _img_from_bytes(data: bytes) -> Image.Image:
    from io import BytesIO

    return Image.open(BytesIO(data)).convert("RGB")


def download_faces(session: requests.Session, api_key: str, lat: str, lon: str, size: int) -> Dict[str, np.ndarray] | None:
    faces: Dict[str, np.ndarray] = {}
    base_url = "https://maps.googleapis.com/maps/api/streetview"
    for name, (heading, pitch) in FACE_PARAMS.items():
        params = {
            "size": f"{size}x{size}",
            "location": f"{lat},{lon}",
            "fov": "90",
            "heading": str(heading),
            "pitch": str(pitch),
            "source": "outdoor",
            "key": api_key,
        }
        r = session.get(base_url, params=params, timeout=30)
        if r.status_code != 200 or len(r.content) < 5000:
            return None
        cl = r.content.lower()
        if b"sorry" in cl or b"maperror" in cl:
            return None
        img = _img_from_bytes(r.content)
        faces[name] = np.array(img, dtype=np.uint8)
    return faces


def cubemap_to_equirect(faces: Dict[str, np.ndarray], width: int, height: int) -> np.ndarray:
    h = height
    w = width

    xs = np.linspace(-math.pi, math.pi, w, endpoint=False)
    ys = np.linspace(math.pi / 2, -math.pi / 2, h)
    theta, phi = np.meshgrid(xs, ys)

    x = np.cos(phi) * np.sin(theta)
    y = np.sin(phi)
    z = np.cos(phi) * np.cos(theta)

    ax = np.abs(x)
    ay = np.abs(y)
    az = np.abs(z)

    out = np.zeros((h, w, 3), dtype=np.uint8)

    def sample(face: str, uc: np.ndarray, vc: np.ndarray, mask: np.ndarray) -> None:
        if not np.any(mask):
            return
        img = faces[face]
        n = img.shape[0]
        u = np.nan_to_num(uc[mask], nan=0.0, posinf=0.0, neginf=0.0)
        v = np.nan_to_num(vc[mask], nan=0.0, posinf=0.0, neginf=0.0)
        px = np.clip(((u + 1.0) * 0.5 * (n - 1)).round().astype(np.int32), 0, n - 1)
        py = np.clip(((v + 1.0) * 0.5 * (n - 1)).round().astype(np.int32), 0, n - 1)
        out[mask] = img[py, px]

    # right (+X)
    mask = (ax >= ay) & (ax >= az) & (x > 0)
    sample("right", -z / np.maximum(ax, 1e-9), -y / np.maximum(ax, 1e-9), mask)

    # left (-X)
    mask = (ax >= ay) & (ax >= az) & (x <= 0)
    sample("left", z / np.maximum(ax, 1e-9), -y / np.maximum(ax, 1e-9), mask)

    # up (+Y)
    mask = (ay > ax) & (ay >= az) & (y > 0)
    sample("up", x / np.maximum(ay, 1e-9), z / np.maximum(ay, 1e-9), mask)

    # down (-Y)
    mask = (ay > ax) & (ay >= az) & (y <= 0)
    sample("down", x / np.maximum(ay, 1e-9), -z / np.maximum(ay, 1e-9), mask)

    # front (+Z)
    mask = (az > ax) & (az > ay) & (z > 0)
    sample("front", x / np.maximum(az, 1e-9), -y / np.maximum(az, 1e-9), mask)

    # back (-Z)
    mask = (az > ax) & (az > ay) & (z <= 0)
    sample("back", -x / np.maximum(az, 1e-9), -y / np.maximum(az, 1e-9), mask)

    return out


def run(
    label_file: Path,
    output_dir: Path,
    api_key: str,
    max_samples: int,
    face_size: int,
    out_w: int,
    out_h: int,
    workers: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    lines = [ln.strip() for ln in label_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if max_samples > 0:
        lines = lines[:max_samples]

    lock = threading.Lock()
    stats = {"generated": 0, "skipped": 0, "failed": 0, "already_ok": 0, "processed": 0}

    def process_line(line: str) -> None:
        base = line.split(" ")[0]
        parts = base.split(",")
        pano_name, lat, lon = parts[0], parts[1], parts[2]
        out_path = output_dir / pano_name

        # Resume support: skip images already in desired equirect size.
        if out_path.exists():
            try:
                with Image.open(out_path) as im:
                    if im.size == (out_w, out_h):
                        with lock:
                            stats["already_ok"] += 1
                            stats["processed"] += 1
                        return
            except Exception:
                pass

        session = requests.Session()
        faces = None
        for _ in range(3):
            faces = download_faces(session, api_key, lat, lon, face_size)
            if faces is not None:
                break
            time.sleep(0.3)

        if faces is None:
            with lock:
                stats["failed"] += 1
                stats["processed"] += 1
            return

        eq = cubemap_to_equirect(faces, width=out_w, height=out_h)
        Image.fromarray(eq, mode="RGB").save(out_path, quality=92)
        with lock:
            stats["generated"] += 1
            stats["processed"] += 1

    total = len(lines)
    max_workers = min(max(1, workers), max(1, total))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(process_line, ln) for ln in lines]
        for i, fut in enumerate(as_completed(futures), start=1):
            fut.result()
            if i % 50 == 0 or i == total:
                print(
                    f"progress={i}/{total} generated={stats['generated']} already_ok={stats['already_ok']} failed={stats['failed']}"
                )

    print(
        f"generated={stats['generated']} already_ok={stats['already_ok']} failed={stats['failed']} output={output_dir}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download equirectangular-like full panoramas using 6 Street View faces")
    parser.add_argument("--label-file", type=Path, default=Path("pano_label_balanced.txt"))
    parser.add_argument("--output-dir", type=Path, default=Path("panorama_equirect"))
    parser.add_argument("--api-key", type=str, required=True)
    parser.add_argument("--max-samples", type=int, default=0)
    parser.add_argument("--face-size", type=int, default=640)
    parser.add_argument("--out-width", type=int, default=1536)
    parser.add_argument("--out-height", type=int, default=768)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    run(
        label_file=args.label_file.resolve(),
        output_dir=args.output_dir.resolve(),
        api_key=args.api_key,
        max_samples=args.max_samples,
        face_size=args.face_size,
        out_w=args.out_width,
        out_h=args.out_height,
        workers=args.workers,
    )


if __name__ == "__main__":
    main()
