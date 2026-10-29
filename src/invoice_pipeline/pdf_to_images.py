"""Convert PDF invoices into one PNG image per page."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pypdfium2 as pdfium

from invoice_pipeline.orientation import straighten

logger = logging.getLogger(__name__)

DEFAULT_DPI = 200
PDF_POINTS_PER_INCH = 72


class PdfConversionError(Exception):
    """Raised when a PDF cannot be converted to images."""


def _folder_name(pdf_path: Path) -> str:
    """Filesystem-safe folder name for a PDF ("Invoice # 31.pdf" -> "Invoice_31")."""
    return re.sub(r"[^\w.-]+", "_", pdf_path.stem).strip("_") or "invoice"


def convert_pdf(
    pdf_path: Path,
    images_dir: Path,
    dpi: int = DEFAULT_DPI,
    unverified: list[str] | None = None,
) -> list[Path]:
    """Render every page of a PDF to ``images_dir/<pdf name>/page_001.png``, ...

    Each page is turned upright first if the scan is rotated and its orientation is
    checked (see orientation.py). The name of every page whose orientation could not be
    verified is added to ``unverified`` when it is given. Running it again overwrites the
    previous result: leftover pages from an earlier run are removed first, so the folder
    always mirrors the PDF.

    Returns the image paths in page order.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(images_dir) / _folder_name(pdf_path)

    if not pdf_path.is_file():
        raise PdfConversionError(f"{pdf_path.name} does not exist")

    try:
        with pdfium.PdfDocument(pdf_path) as document:
            if len(document) == 0:
                raise PdfConversionError(f"{pdf_path.name} has no pages")

            output_dir.mkdir(parents=True, exist_ok=True)
            for stale in output_dir.glob("page_*.png"):
                stale.unlink()

            image_paths = []
            for number, page in enumerate(document, start=1):
                image = page.render(scale=dpi / PDF_POINTS_PER_INCH).to_pil()
                label = f"{pdf_path.name} page {number}"
                image, verified = straighten(image, label)
                if not verified and unverified is not None:
                    unverified.append(label)
                image_path = output_dir / f"page_{number:03d}.png"
                image.save(image_path, dpi=(dpi, dpi))
                image_paths.append(image_path)
    except PdfConversionError:
        raise
    except (pdfium.PdfiumError, OSError) as error:
        # PdfiumError covers corrupt or password-protected files; OSError covers
        # missing files and write failures.
        raise PdfConversionError(f"Could not convert {pdf_path.name}: {error}") from error

    logger.info("Converted %s: %d page(s) at %d dpi", pdf_path.name, len(image_paths), dpi)
    return image_paths


def convert_all(
    raw_dir: Path,
    images_dir: Path,
    dpi: int = DEFAULT_DPI,
    unverified: list[str] | None = None,
) -> dict[Path, list[Path]]:
    """Convert every PDF in ``raw_dir``; a failing PDF is logged and skipped.

    A missing ``raw_dir`` is logged as a warning and converts nothing. The pages whose
    orientation could not be verified are added to ``unverified`` when it is given.

    Returns a mapping of each converted PDF to its image paths.
    """
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        logger.warning("Input folder does not exist: %s", raw_dir)
        return {}

    pdfs = sorted(p for p in raw_dir.iterdir() if p.suffix.lower() == ".pdf")
    if not pdfs:
        logger.warning("No PDF files found in %s", raw_dir)

    results: dict[Path, list[Path]] = {}
    for pdf in pdfs:
        try:
            results[pdf] = convert_pdf(pdf, images_dir, dpi, unverified)
        except PdfConversionError:
            logger.exception("Skipping %s", pdf.name)
    return results


def main() -> None:
    """Convert every PDF in every vendor folder of the raw folder (manual run).

    A vendor needs no settings yet: its pages must exist first so that its
    crop boxes can be chosen with pick_boxes.py.
    """
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging
    from invoice_pipeline.vendors import settings_path

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    converted = 0
    unverified: list[str] = []
    for vendor_dir in sorted(p for p in config.raw_dir.iterdir() if p.is_dir()):
        if not settings_path(config.vendors_dir, vendor_dir.name).is_file():
            logger.info(
                "%s has no crop settings yet: run pick_boxes.py %s",
                vendor_dir.name,
                vendor_dir.name,
            )
        converted += len(
            convert_all(vendor_dir, config.images_dir / vendor_dir.name, unverified=unverified)
        )
    logger.info("Done: %d PDF(s) converted. Images in %s", converted, config.images_dir)
    if unverified:
        logger.error(
            "%d page(s) have an orientation that could not be verified; check them by hand: %s",
            len(unverified),
            "; ".join(unverified),
        )
        raise SystemExit(1)


if __name__ == "__main__":
    # Re-import under the real module name: when run directly, __name__ is
    # "__main__", so this module's logger would fall outside the configured
    # "invoice_pipeline" logger and its messages would be lost.
    from invoice_pipeline.pdf_to_images import main as _main

    _main()
