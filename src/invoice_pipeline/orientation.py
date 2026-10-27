"""Detect how a scanned page is rotated and turn it upright.

Detection runs locally with Tesseract (orientation and script detection only):
nothing is sent anywhere and no invoice data is read.
"""

from __future__ import annotations

import logging

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

# Tesseract only needs a small image to tell which way the text runs.
DETECTION_MAX_SIDE = 1600
# Detections below this confidence are not trusted and the page is left as it is.
# Every page checked while building this (upright and rotated ones) scored above 2.5.
MIN_CONFIDENCE = 2.0

# Clockwise degrees needed to turn the page upright -> the matching lossless transpose.
_TURNS = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


class OrientationError(Exception):
    """Raised when Tesseract is not installed."""


def detect_rotation(image: Image.Image) -> tuple[int, float] | None:
    """Return (clockwise degrees to turn the page upright, confidence), or None.

    None means Tesseract could not tell, for example on a page with almost no text.
    """
    small = image.convert("L")
    small.thumbnail((DETECTION_MAX_SIDE, DETECTION_MAX_SIDE))
    try:
        result = pytesseract.image_to_osd(small, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as error:
        raise OrientationError(
            "Tesseract is not installed. Install it (for example: brew install tesseract)."
        ) from error
    except pytesseract.TesseractError:
        return None
    return int(result["rotate"]), float(result["orientation_conf"])


def straighten(image: Image.Image, label: str = "page") -> Image.Image:
    """Return ``image`` turned upright; an upright or unclear page is returned unchanged.

    ``label`` names the page in the log messages.
    """
    detected = detect_rotation(image)
    if detected is None:
        logger.warning("%s: could not detect the orientation; left as it is", label)
        return image

    degrees, confidence = detected
    if confidence < MIN_CONFIDENCE:
        logger.warning(
            "%s: orientation unclear (%d degrees, confidence %.2f); left as it is",
            label,
            degrees,
            confidence,
        )
        return image
    if degrees == 0:
        return image

    logger.info("%s: rotated %d degrees (confidence %.2f)", label, degrees, confidence)
    return image.transpose(_TURNS[degrees])
