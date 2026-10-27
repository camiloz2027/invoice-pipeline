"""Per-vendor settings.

A vendor is a folder under ``data/raw/`` whose name is exactly its key in
``VENDORS``. The same name is used for the vendor's folders in every later
stage. Vendor names stay local: they are never sent to the AI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# (left, top, right, bottom) as fractions of the page, from 0 to 1.
Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Vendor:
    """What differs from one vendor's invoices to another's."""

    # Region with the invoice lines and totals; the rest of the page is cut away.
    # Fractions do not depend on the DPI used to render the pages. The values were
    # picked by eye on a grid overlay of the vendor's invoice.
    body_box: Box


VENDORS: dict[str, Vendor] = {
    "Purity_Life": Vendor(body_box=(0.025, 0.35, 0.9, 0.9)),
}


def vendor_folders(parent: Path) -> list[Path]:
    """Return the subfolders of ``parent`` that belong to a known vendor, sorted by name.

    A subfolder with no entry in ``VENDORS`` is logged as an error and skipped,
    so its pages are never cropped with another vendor's settings.
    """
    parent = Path(parent)
    if not parent.is_dir():
        logger.warning("Input folder does not exist: %s", parent)
        return []

    folders = []
    for folder in sorted(p for p in parent.iterdir() if p.is_dir()):
        if folder.name in VENDORS:
            folders.append(folder)
        else:
            logger.error(
                "No vendor settings for folder %r in %s: skipped. Add it to VENDORS in vendors.py.",
                folder.name,
                parent,
            )
    return folders
