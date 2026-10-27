"""Crop the page images down to the region that holds the invoice data."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from invoice_pipeline.vendors import Box, validate_box

logger = logging.getLogger(__name__)

# Each vendor's boxes live in data/vendors/ (see vendors.py); there is deliberately
# no default box, so a page is never cropped with another vendor's region.


def crop_image(image_path: Path, output_path: Path, box: Box) -> Path:
    """Crop one image to ``box`` (fractions of its size) and save it as ``output_path``."""
    validate_box(box)
    left, top, right, bottom = box

    with Image.open(image_path) as image:
        width, height = image.size
        pixel_box = (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.crop(pixel_box).save(output_path)

    return output_path


def crop_all(images_dir: Path, crops_dir: Path, box: Box) -> dict[Path, list[Path]]:
    """Crop every page image under ``images_dir`` into ``crops_dir``, keeping the layout.

    ``images_dir/<invoice>/page_001.png`` becomes ``crops_dir/<invoice>/page_001.png``.
    Running it again overwrites the previous result: leftover crops from an
    earlier run are removed first. A page that fails is logged and skipped.
    A missing ``images_dir`` is logged as a warning and crops nothing.

    Returns a mapping of each invoice folder to its cropped image paths.
    """
    validate_box(box)
    images_dir, crops_dir = Path(images_dir), Path(crops_dir)
    if not images_dir.is_dir():
        logger.warning("Input folder does not exist: %s", images_dir)
        return {}

    invoice_dirs = sorted(p for p in images_dir.iterdir() if p.is_dir())
    if not invoice_dirs:
        logger.warning("No image folders found in %s", images_dir)

    results: dict[Path, list[Path]] = {}
    for invoice_dir in invoice_dirs:
        output_dir = crops_dir / invoice_dir.name
        output_dir.mkdir(parents=True, exist_ok=True)
        for stale in output_dir.glob("page_*.png"):
            stale.unlink()

        cropped = []
        for page_image in sorted(invoice_dir.glob("page_*.png")):
            try:
                cropped.append(crop_image(page_image, output_dir / page_image.name, box))
            except OSError:
                logger.exception("Skipping %s", page_image)
        results[invoice_dir] = cropped
        logger.info("Cropped %s: %d page(s)", invoice_dir.name, len(cropped))
    return results


def main() -> None:
    """Crop every converted page image of every known vendor (manual run).

    Two crops per page, with the same relative path: the body (lines and totals)
    and, when the vendor has an ``id_box``, the invoice number.
    """
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging
    from invoice_pipeline.vendors import load_vendor, vendor_folders

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    cropped = 0
    for vendor_dir in vendor_folders(config.images_dir, config.vendors_dir):
        vendor = load_vendor(config.vendors_dir, vendor_dir.name)
        cropped += len(
            crop_all(vendor_dir, config.body_crops_dir / vendor_dir.name, vendor.body_box)
        )
        if vendor.id_box is None:
            logger.warning("%s has no id_box: no invoice-number crops", vendor_dir.name)
        else:
            crop_all(vendor_dir, config.id_crops_dir / vendor_dir.name, vendor.id_box)
    logger.info(
        "Done: %d invoice(s) cropped. Crops in %s and %s",
        cropped,
        config.body_crops_dir,
        config.id_crops_dir,
    )


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.crop import main as _main

    _main()
