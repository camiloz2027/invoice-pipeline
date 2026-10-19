"""Draw a labeled percentage grid over a page image.

A helper for choosing the crop fractions in ``crop.py``: open the result and
read the (left, top, right, bottom) positions straight off the grid, from 0 to 1.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

MAJOR_COLOR = (217, 26, 26)  # every 10%, labeled
MINOR_COLOR = (115, 153, 242)  # every 5%, thin
MAJOR_STEP = 0.10
MINOR_STEP = 0.05


def draw_grid(image_path: Path, output_path: Path) -> Path:
    """Save a copy of ``image_path`` with a grid drawn on top.

    Line width and label size scale with the image, so the result is equally
    readable at any resolution. The original image is not modified.
    """
    with Image.open(image_path) as source:
        image = source.convert("RGB")  # also handles grayscale / transparent inputs

    width, height = image.size
    longest = max(width, height)
    line_width = max(1, round(longest / 1000))
    font = ImageFont.load_default(size=max(10, longest // 55))
    draw = ImageDraw.Draw(image)

    steps = round(1 / MINOR_STEP)
    for step in range(1, steps):
        fraction = step * MINOR_STEP
        major = round(fraction / MAJOR_STEP, 6).is_integer()
        color = MAJOR_COLOR if major else MINOR_COLOR
        thickness = line_width * 2 if major else line_width
        x, y = round(fraction * width), round(fraction * height)
        draw.line([(x, 0), (x, height)], fill=color, width=thickness)
        draw.line([(0, y), (width, y)], fill=color, width=thickness)

        if major:
            label = f"{fraction:.1f}"
            # Labels on all four edges so they are readable wherever you look.
            draw.text((x + 4, 2), label, fill=MAJOR_COLOR, font=font)
            draw.text((x + 4, height - 4), label, fill=MAJOR_COLOR, font=font, anchor="ld")
            draw.text((4, y + 2), label, fill=MAJOR_COLOR, font=font)
            draw.text((width - 4, y + 2), label, fill=MAJOR_COLOR, font=font, anchor="ra")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    logger.info("Grid saved to %s", output_path)
    return output_path


def main() -> None:
    """Draw a grid over one page image (manual run)."""
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging

    parser = argparse.ArgumentParser(description="Draw a percentage grid over a page image.")
    parser.add_argument(
        "image", type=Path, help="page image, e.g. data/interim/images/<invoice>/page_001.png"
    )
    parser.add_argument("-o", "--output", type=Path, help="where to save the result")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)
    output = (
        args.output or config.grids_dir / f"{args.image.parent.name}_{args.image.stem}_grid.png"
    )
    draw_grid(args.image, output)


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.grid_overlay import main as _main

    _main()
