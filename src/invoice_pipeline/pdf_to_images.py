"""Convert PDF invoices into one PNG image per page."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

DEFAULT_DPI = 200


class PdfConversionError(Exception):
    """Raised when a PDF cannot be converted to images."""


def _folder_name(pdf_path: Path) -> str:
    """Filesystem-safe folder name for a PDF ("Invoice # 31.pdf" -> "Invoice_31")."""
    return re.sub(r"[^\w.-]+", "_", pdf_path.stem).strip("_") or "invoice"


def convert_pdf(pdf_path: Path, images_dir: Path, dpi: int = DEFAULT_DPI) -> list[Path]:
    """Render every page of a PDF to ``images_dir/<pdf name>/page_001.png``, ...

    Running it again overwrites the previous result: leftover pages from an
    earlier run are removed first, so the folder always mirrors the PDF.

    Returns the image paths in page order.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(images_dir) / _folder_name(pdf_path)

    try:
        with pymupdf.open(pdf_path) as document:
            if document.needs_pass:
                raise PdfConversionError(f"{pdf_path.name} is password protected")
            if document.page_count == 0:
                raise PdfConversionError(f"{pdf_path.name} has no pages")

            output_dir.mkdir(parents=True, exist_ok=True)
            for stale in output_dir.glob("page_*.png"):
                stale.unlink()

            image_paths = []
            for number, page in enumerate(document, start=1):
                image_path = output_dir / f"page_{number:03d}.png"
                page.get_pixmap(dpi=dpi).save(image_path)
                image_paths.append(image_path)
    except PdfConversionError:
        raise
    except Exception as error:  # pymupdf raises several unrelated error types
        raise PdfConversionError(f"Could not convert {pdf_path.name}: {error}") from error

    logger.info("Converted %s: %d page(s) at %d dpi", pdf_path.name, len(image_paths), dpi)
    return image_paths


def convert_all(raw_dir: Path, images_dir: Path, dpi: int = DEFAULT_DPI) -> dict[Path, list[Path]]:
    """Convert every PDF in ``raw_dir``; a failing PDF is logged and skipped.

    Returns a mapping of each converted PDF to its image paths.
    """
    pdfs = sorted(p for p in Path(raw_dir).iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        logger.warning("No PDF files found in %s", raw_dir)

    results: dict[Path, list[Path]] = {}
    for pdf in pdfs:
        try:
            results[pdf] = convert_pdf(pdf, images_dir, dpi)
        except PdfConversionError:
            logger.exception("Skipping %s", pdf.name)
    return results


def main() -> None:
    """Convert every PDF in the configured raw folder (manual run)."""
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    results = convert_all(config.raw_dir, config.images_dir)
    logger.info("Done: %d PDF(s) converted. Images in %s", len(results), config.images_dir)


if __name__ == "__main__":
    # Re-import under the real module name: when run directly, __name__ is
    # "__main__", so this module's logger would fall outside the configured
    # "invoice_pipeline" logger and its messages would be lost.
    from invoice_pipeline.pdf_to_images import main as _main

    _main()
