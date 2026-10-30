"""Convert every PDF invoice into one PNG image per page."""

import logging
import re

import pypdfium2 as pdfium

from invoice_pipeline.config import load_config
from invoice_pipeline.logging_setup import setup_logging
from invoice_pipeline.orientation import straighten
from invoice_pipeline.vendors import settings_path

logger = logging.getLogger("invoice_pipeline.pdf_to_images")

DPI = 200


def folder_name(pdf_path):
    """Safe folder name: "Invoice # 31.pdf" -> "Invoice_31"."""
    name = re.sub(r"[^\w.-]+", "_", pdf_path.stem)
    name = name.strip("_")
    if name == "":
        name = "invoice"
    return name


def convert_pdf(pdf_path, images_dir, unverified, dpi=DPI):
    """Save every page of the PDF as images_dir/<name>/page_001.png, ...

    Each page is straightened first (see orientation.py). Pages whose orientation
    could not be verified are added to the list ``unverified``.
    """
    output_dir = images_dir / folder_name(pdf_path)
    image_paths = []

    with pdfium.PdfDocument(pdf_path) as document:
        output_dir.mkdir(parents=True, exist_ok=True)

        # Delete the images of an earlier run.
        for old_image in output_dir.glob("page_*.png"):
            old_image.unlink()

        for number, page in enumerate(document, start=1):
            image = page.render(scale=dpi / 72).to_pil()
            label = f"{pdf_path.name} page {number}"
            image, verified = straighten(image, label)
            if not verified:
                unverified.append(label)
            image_path = output_dir / f"page_{number:03d}.png"
            image.save(image_path, dpi=(dpi, dpi))
            image_paths.append(image_path)

    logger.info("Converted %s: %d page(s)", pdf_path.name, len(image_paths))
    return image_paths


def convert_all(raw_dir, images_dir, unverified, dpi=DPI):
    """Convert every PDF in raw_dir. If one fails, skip it and go on."""
    results = {}

    if not raw_dir.is_dir():
        logger.warning("Input folder does not exist: %s", raw_dir)
        return results

    for pdf_path in sorted(raw_dir.iterdir()):
        if pdf_path.suffix.lower() != ".pdf":
            continue
        try:
            results[pdf_path] = convert_pdf(pdf_path, images_dir, unverified, dpi)
        except (pdfium.PdfiumError, OSError):
            logger.exception("Skipping %s", pdf_path.name)

    return results


def main():
    config = load_config()
    config.ensure_dirs()
    setup_logging(config)

    converted = 0
    unverified = []
    for vendor_dir in sorted(config.raw_dir.iterdir()):
        if not vendor_dir.is_dir():
            continue
        if not settings_path(config.vendors_dir, vendor_dir.name).is_file():
            logger.info("%s has no crop settings yet: run pick_boxes.py", vendor_dir.name)
        results = convert_all(vendor_dir, config.images_dir / vendor_dir.name, unverified)
        converted += len(results)

    logger.info("Done: %d PDF(s) converted", converted)
    if unverified:
        logger.error("Orientation not verified, check by hand: %s", "; ".join(unverified))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
