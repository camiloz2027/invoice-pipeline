"""Choose a vendor's crop boxes by drawing them on its pages.

    python src/invoice_pipeline/pick_boxes.py <vendor>

Drag on the page to draw a box, then press:
    b             save it as body_box (the invoice lines and totals)
    i             save it as id_box (only the invoice number)
    left / right  previous / next page
    q             quit

The boxes are saved in data/vendors/<vendor>.toml.
"""

import logging
import sys

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from matplotlib.widgets import RectangleSelector
from PIL import Image

from invoice_pipeline.config import load_config
from invoice_pipeline.logging_setup import setup_logging
from invoice_pipeline.vendors import VendorError, load_boxes, save_box

logger = logging.getLogger("invoice_pipeline.pick_boxes")

KEYS = {"b": "body_box", "i": "id_box"}
COLORS = {"body_box": "tab:blue", "id_box": "tab:green"}
HELP = "drag = draw box   b = save body box   i = save id box   left/right = page   q = quit"


def to_box(extents, width, height):
    """Turn the pixel extents of the selector into (left, top, right, bottom) fractions."""
    xmin, xmax, ymin, ymax = extents
    left = min(max(xmin / width, 0), 1)
    top = min(max(ymin / height, 0), 1)
    right = min(max(xmax / width, 0), 1)
    bottom = min(max(ymax / height, 0), 1)
    return (left, top, right, bottom)


class BoxPicker:
    """A window that shows the pages of one vendor and saves the boxes drawn on them."""

    def __init__(self, vendor, vendors_dir, pages):
        self.vendor = vendor
        self.vendors_dir = vendors_dir
        self.pages = pages
        self.index = 0
        self.boxes = load_boxes(vendors_dir, vendor)

        self.figure, self.axes = plt.subplots(figsize=(9, 11))
        self.figure.canvas.mpl_connect("key_press_event", self.on_key)
        self.show_page()

    def show_page(self):
        """Draw the current page, the saved boxes and a new empty selector."""
        page_path = self.pages[self.index]
        image = Image.open(page_path)
        self.width, self.height = image.size

        self.axes.clear()
        self.axes.imshow(image)
        self.axes.set_axis_off()
        self.axes.set_title(
            f"{self.vendor}  page {self.index + 1}/{len(self.pages)}: "
            f"{page_path.parent.name}/{page_path.name}\n{HELP}",
            fontsize=9,
        )

        for key, box in self.boxes.items():
            left, top, right, bottom = box
            rectangle = Rectangle(
                (left * self.width, top * self.height),
                (right - left) * self.width,
                (bottom - top) * self.height,
                fill=False,
                edgecolor=COLORS[key],
                linewidth=2,
            )
            self.axes.add_patch(rectangle)

        # Clearing the axes removes the old selector, so a new one is made for every page.
        self.has_selection = False
        self.selector = RectangleSelector(self.axes, self.on_select, interactive=True)
        self.figure.canvas.draw_idle()

    def on_select(self, press, release):
        self.has_selection = True

    def on_key(self, event):
        if event.key == "right":
            self.index = (self.index + 1) % len(self.pages)
            self.show_page()
        elif event.key == "left":
            self.index = (self.index - 1) % len(self.pages)
            self.show_page()
        elif event.key in KEYS:
            self.save(KEYS[event.key])

    def save(self, key):
        if not self.has_selection:
            logger.warning("Draw a box first")
            return

        box = to_box(self.selector.extents, self.width, self.height)
        try:
            self.boxes = save_box(self.vendors_dir, self.vendor, key, box)
        except VendorError as error:
            logger.error("Not saved: %s", error)
            return
        logger.info("Saved %s of %s: %s", key, self.vendor, list(self.boxes[key]))
        self.show_page()


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python src/invoice_pipeline/pick_boxes.py <vendor>")
    vendor = sys.argv[1]

    config = load_config()
    setup_logging(config)

    pages = sorted((config.images_dir / vendor).rglob("page_*.png"))
    if not pages:
        raise SystemExit(f"No page images for {vendor}: run pdf_to_images.py first.")

    try:
        BoxPicker(vendor, config.vendors_dir, pages)
    except VendorError as error:
        raise SystemExit(str(error))
    plt.show()


if __name__ == "__main__":
    main()
