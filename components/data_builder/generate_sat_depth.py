from pathlib import Path
import argparse
import numpy as np
from PIL import Image
import torch
from transformers import pipeline


DEFAULT_MODEL = "Intel/dpt-hybrid-midas"


def to_depth_image(prediction: dict, target_size: tuple[int, int]) -> Image.Image:
    if "depth" in prediction and isinstance(prediction["depth"], Image.Image):
        return prediction["depth"].convert("L").resize(target_size, Image.BILINEAR)

    pred = prediction.get("predicted_depth")
    if pred is None:
        raise ValueError("Depth model output does not contain 'depth' or 'predicted_depth'.")

    if hasattr(pred, "detach"):
        arr = pred.detach().cpu().numpy()
    else:
        arr = np.array(pred)

    if arr.ndim == 3:
        arr = arr[0]
    arr = arr.astype(np.float32)
    arr -= arr.min()
    denom = max(arr.max(), 1e-6)
    arr = arr / denom
    arr = (arr * 255.0).astype(np.uint8)
    depth_img = Image.fromarray(arr, mode="L")
    return depth_img.resize(target_size, Image.BILINEAR)


def process_images(input_dir: Path, output_dir: Path, overwrite: bool, model_id: str) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    device_index = 0 if torch.cuda.is_available() else -1
    depth_pipe = pipeline("depth-estimation", model=model_id, device=device_index)
    count = 0

    for image_path in sorted(input_dir.glob("*.png")):
        output_path = output_dir / image_path.name
        if output_path.exists() and not overwrite:
            continue

        with Image.open(image_path) as img:
            rgb = img.convert("RGB")
            prediction = depth_pipe(rgb)
            depth_img = to_depth_image(prediction, target_size=rgb.size)
            depth_img.save(output_path)
        count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate model-based sat_depth images from satellite images")
    parser.add_argument("--input-dir", type=Path, default=Path("london_vigor_root/London/satellite"))
    parser.add_argument("--output-dir", type=Path, default=Path("london_vigor_root/London/sat_depth"))
    parser.add_argument("--model-id", type=str, default=DEFAULT_MODEL)
    parser.add_argument("--overwrite", action="store_true", default=False)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")

    count = process_images(input_dir, output_dir, overwrite=args.overwrite, model_id=args.model_id)
    print(f"Generated {count} sat_depth images")
    print(f"Depth model: {args.model_id}")
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
