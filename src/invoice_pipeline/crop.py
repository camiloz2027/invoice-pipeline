"""Crop every page image to the region of the invoice that is needed."""

import logging

from PIL import Image

from invoice_pipeline.config import load_config
from invoice_pipeline.logging_setup import setup_logging
from invoice_pipeline.vendors import load_vendor, vendor_folders

logger = logging.getLogger("invoice_pipeline.crop")


def crop_image(image_path, output_path, box):
    """Crop one image to box and save it as output_path.

    box is (left, top, right, bottom) as fractions of the page, from 0 to 1.
    """
    left, top, right, bottom = box

    with Image.open(image_path) as image:
        width, height = image.size
        pixel_box = (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )
        image.crop(pixel_box).save(output_path)


def crop_all(images_dir, crops_dir, box):
    """Crop every page under images_dir into crops_dir, with the same folders.

    images_dir/<invoice>/page_001.png becomes crops_dir/<invoice>/page_001.png.
    If a page fails, it is skipped and the rest go on.
    """
    results = {}

    if not images_dir.is_dir():
        logger.warning("Input folder does not exist: %s", images_dir.name)
        return results

    for invoice_dir in sorted(images_dir.iterdir()):
        if not invoice_dir.is_dir():
            continue
        output_dir = crops_dir / invoice_dir.name
        output_dir.mkdir(parents=True, exist_ok=True)

        # Delete the crops of an earlier run.
        for old_crop in output_dir.glob("page_*.png"):
            old_crop.unlink()

        cropped = []
        for page_path in sorted(invoice_dir.glob("page_*.png")):
            output_path = output_dir / page_path.name
            try:
                crop_image(page_path, output_path, box)
            except OSError:
                logger.exception("Skipping %s/%s", invoice_dir.name, page_path.name)
            else:
                cropped.append(output_path)

        results[invoice_dir] = cropped
        logger.info("Cropped %s: %d page(s)", invoice_dir.name, len(cropped))

    return results


def main():
    """Make two crops of every page: the body (lines and totals) and the invoice number."""
    config = load_config()
    config.ensure_dirs()
    setup_logging(config)

    cropped = 0
    for vendor_dir in vendor_folders(config.images_dir, config.vendors_dir):
        vendor = load_vendor(config.vendors_dir, vendor_dir.name)
        body_dir = config.body_crops_dir / vendor_dir.name
        cropped += len(crop_all(vendor_dir, body_dir, vendor.body_box))

        if vendor.id_box is None:
            logger.warning("%s has no id_box: no invoice-number crops", vendor_dir.name)
        else:
            id_dir = config.id_crops_dir / vendor_dir.name
            crop_all(vendor_dir, id_dir, vendor.id_box)

    logger.info("Done: %d invoice(s) cropped", cropped)


if __name__ == "__main__":
    main()
