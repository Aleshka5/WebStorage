from pathlib import Path

from loguru import logger
from PIL import Image

SUPPORTED_INPUT_FORMATS = frozenset({"JPEG", "PNG", "WEBP", "GIF"})
JPEG_QUALITY = 85


class ThumbnailService:
    def generate(self, source_path: Path, output_path: Path, max_px: int) -> None:
        logger.info(
            "Generating thumbnail from {} to {} (max_px={})",
            source_path,
            output_path,
            max_px,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with Image.open(source_path) as image:
            image_format = (image.format or "").upper()
            if image_format not in SUPPORTED_INPUT_FORMATS:
                raise ValueError(f"Unsupported image format: {image_format or 'unknown'}")

            if image_format == "GIF":
                image.seek(0)

            if image.mode in ("RGBA", "P", "LA"):
                image = image.convert("RGB")

            image.thumbnail((max_px, max_px))
            image.save(output_path, format="JPEG", quality=JPEG_QUALITY)

        logger.info("Thumbnail saved to {}", output_path)
