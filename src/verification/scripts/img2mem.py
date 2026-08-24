from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


IMAGE_SIZE = (128, 128)
INPUT_DIR = Path(__file__).resolve().parent / "cnn_dataset"
OUTPUT_DIR = Path(__file__).resolve().parent / "mem_out"


def list_image_files(folder: Path) -> list[Path]:
    files: list[Path] = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp"):
        files.extend(folder.rglob(ext))
    return sorted(files)


def image_to_int8(path: Path) -> np.ndarray:
    img = Image.open(path).convert("L")
    if img.size != IMAGE_SIZE:
        raise ValueError(f"Expected 128x128 image, got {img.size} at: {path}")

    arr_u8 = np.asarray(img, dtype=np.uint8)
    return (arr_u8.astype(np.int16) - 128).astype(np.int8)


def write_mem(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii") as f:
        for v in values.flatten():
            f.write(f"{int(v)}\n")


def export_class(class_name: str, stem_prefix: str) -> int:
    src_dir = INPUT_DIR / class_name
    dst_dir = OUTPUT_DIR / class_name
    files = list_image_files(src_dir)

    for idx, src in enumerate(files):
        dst = dst_dir / f"{stem_prefix}{idx}.mem"
        write_mem(dst, image_to_int8(src))

    return len(files)


def main() -> None:
    if not INPUT_DIR.exists():
        raise FileNotFoundError(f"Dataset folder not found: {INPUT_DIR}")

    person_count = export_class("person", "person")
    nonperson_count = export_class("non_person", "non_person")

    print(f"person mem files: {person_count}")
    print(f"non_person mem files: {nonperson_count}")
    print(f"output dir: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
