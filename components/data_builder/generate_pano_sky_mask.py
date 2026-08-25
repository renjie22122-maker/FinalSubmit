from pathlib import Path
import argparse
import numpy as np
from PIL import Image, ImageFile
import torch
import torch.nn.functional as F
from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation


DEFAULT_MODEL = "nvidia/segformer-b0-finetuned-ade-512-512"

# Some downloaded Street View JPEGs can be slightly truncated.
# Allow PIL to load them so batch generation does not crash.
ImageFile.LOAD_TRUNCATED_IMAGES = True


def get_sky_class_ids(model: AutoModelForSemanticSegmentation) -> list[int]:
    id2label = getattr(model.config, "id2label", {})
    sky_ids = []
    for k, v in id2label.items():
        label = str(v).lower()
        if "sky" in label:
            sky_ids.append(int(k))
    if not sky_ids:
        raise ValueError("Could not find sky class id in segmentation model labels.")
    return sky_ids


def mask_from_prediction(pred_map: np.ndarray, sky_ids: list[int]) -> Image.Image:
    mask = np.isin(pred_map, sky_ids).astype(np.uint8) * 255
    return Image.fromarray(mask, mode="L")


def process(input_dir: Path, output_dir: Path, overwrite: bool, model_id: str, batch_size: int) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModelForSemanticSegmentation.from_pretrained(model_id).to(device)
    model.eval()
    sky_ids = get_sky_class_ids(model)

    images_to_process = []
    for img_path in sorted(input_dir.glob("*.jpg")):
        out_path = output_dir / f"{img_path.stem}.png"
        if out_path.exists() and not overwrite:
            continue
        images_to_process.append((img_path, out_path))

    count = 0
    for i in range(0, len(images_to_process), batch_size):
        batch = images_to_process[i : i + batch_size]
        pil_images = []
        original_sizes = []
        valid_batch = []
        for img_path, out_path in batch:
            try:
                with Image.open(img_path) as img:
                    rgb = img.convert("RGB")
                    pil_images.append(rgb)
                    original_sizes.append((rgb.height, rgb.width))
                    valid_batch.append((img_path, out_path))
            except OSError:
                continue

        if not valid_batch:
            continue

        inputs = processor(images=pil_images, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model(**inputs)

        logits = outputs.logits
        for j, (_, out_path) in enumerate(valid_batch):
            upsampled = F.interpolate(
                logits[j : j + 1],
                size=original_sizes[j],
                mode="bilinear",
                align_corners=False,
            )
            pred_map = upsampled.argmax(dim=1).squeeze(0).cpu().numpy()
            mask_img = mask_from_prediction(pred_map, sky_ids)
            mask_img.save(out_path)
            count += 1

    return count


def main() -> None:
    parser = argparse.ArgumentParser(description='Generate model-based pano_sky_mask images from panorama images')
    parser.add_argument('--input-dir', type=Path, default=Path('london_vigor_root/London/panorama'))
    parser.add_argument('--output-dir', type=Path, default=Path('london_vigor_root/London/pano_sky_mask'))
    parser.add_argument('--model-id', type=str, default=DEFAULT_MODEL)
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--overwrite', action='store_true', default=False)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not input_dir.exists():
        raise FileNotFoundError(f'Input directory not found: {input_dir}')

    generated = process(
        input_dir,
        output_dir,
        overwrite=args.overwrite,
        model_id=args.model_id,
        batch_size=max(1, args.batch_size),
    )
    print(f'Generated {generated} pano sky masks')
    print(f'Sky segmentation model: {args.model_id}')
    print(f'Output directory: {output_dir}')


if __name__ == '__main__':
    main()
