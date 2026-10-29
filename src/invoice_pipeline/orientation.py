"""Detect how a scanned page is rotated, turn it upright and check the result.

Everything runs locally with Tesseract: nothing is sent anywhere. The page is turned
by Tesseract's orientation detection, and then checked with a second, independent
signal: how many words Tesseract reads with confidence in each of the four
orientations. A page that does not read clearly better upright is reported instead of
being passed on silently.
"""

from __future__ import annotations

import logging

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

# Sizes (longest side, in pixels) at which a page is checked. A small image is faster, but dense
# small text is lost when a page is shrunk and Tesseract can then answer with little confidence,
# or the wrong way. So the page is checked at both sizes and the more confident answer is kept.
DETECTION_SIDES = (1600, 2400)
# Detections below this confidence are not trusted and the page is left as it is.
# Every page checked while building this (upright and rotated ones) scored above 2.5.
MIN_CONFIDENCE = 2.0

# Clockwise degrees needed to turn the page upright -> the matching lossless transpose.
_TURNS = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


# Second opinion on the result: the words read with confidence in each of the four orientations.
# On the pages checked, the upright orientation had at least 3.3 times more of them than the
# next one, so requiring 2 times leaves room.
VERIFY_SIDE = 1100
CONFIDENT_WORD = 60
MIN_WORDS = 8
MIN_MARGIN = 2.0


class OrientationError(Exception):
    """Raised when Tesseract is not installed."""


def _detect_at(gray: Image.Image, side: int) -> tuple[int, float] | None:
    """Ask Tesseract for the orientation of ``gray`` shrunk to at most ``side`` pixels."""
    small = gray.copy()
    small.thumbnail((side, side))
    try:
        result = pytesseract.image_to_osd(small, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError as error:
        raise OrientationError(
            "Tesseract is not installed. Install it (for example: brew install tesseract)."
        ) from error
    except pytesseract.TesseractError:
        return None
    return int(result["rotate"]), float(result["orientation_conf"])


def detect_rotation(image: Image.Image) -> tuple[int, float] | None:
    """Return (clockwise degrees to turn the page upright, confidence), or None.

    None means Tesseract could not tell at any size, for example on a page with almost no text.
    """
    gray = image.convert("L")
    answers = [a for a in (_detect_at(gray, side) for side in DETECTION_SIDES) if a is not None]
    return max(answers, key=lambda answer: answer[1]) if answers else None


def _readable_words(gray: Image.Image) -> int:
    """How many words of three or more letters or digits Tesseract reads with confidence."""
    try:
        data = pytesseract.image_to_data(
            gray, output_type=pytesseract.Output.DICT, config="--psm 6"
        )
    except pytesseract.TesseractNotFoundError as error:
        raise OrientationError(
            "Tesseract is not installed. Install it (for example: brew install tesseract)."
        ) from error
    except pytesseract.TesseractError:
        return 0
    words = (text.strip() for text in data["text"])
    return sum(
        1
        for word, conf in zip(words, data["conf"], strict=True)
        if len(word) >= 3 and word.isalnum() and float(conf) >= CONFIDENT_WORD
    )


def verify_upright(image: Image.Image) -> int | None:
    """Return the clockwise degrees still needed to turn ``image`` upright, or None.

    The page is read in its four orientations and the one with clearly the most words
    wins. 0 means the page is upright. None means no orientation reads clearly better,
    for example on a page with almost no text, so the page cannot be verified.
    """
    gray = image.convert("L")
    gray.thumbnail((VERIFY_SIDE, VERIFY_SIDE))
    scores = {turn: _readable_words(gray.rotate(-turn, expand=True)) for turn in (0, 90, 180, 270)}
    best = max(scores, key=scores.__getitem__)
    runner_up = max(score for turn, score in scores.items() if turn != best)
    if scores[best] < MIN_WORDS or scores[best] < MIN_MARGIN * runner_up:
        return None
    return best


def _turn_by_detection(image: Image.Image, label: str) -> Image.Image:
    """Turn ``image`` as Tesseract's orientation detection says; an unclear page is left as it is."""
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


def straighten(image: Image.Image, label: str = "page") -> tuple[Image.Image, bool]:
    """Return ``(upright page, verified)``.

    The page is turned as the detection says and then checked by reading it in all four
    orientations. If another orientation reads clearly better, the page is turned to it
    and a warning is logged. If none reads clearly better, ``verified`` is False and the
    page must be checked by hand. ``label`` names the page in the log messages.
    """
    upright = _turn_by_detection(image, label)
    extra = verify_upright(upright)
    if extra is None:
        logger.error(
            "%s: orientation could not be verified (no orientation reads clearly better); "
            "check it by hand",
            label,
        )
        return upright, False
    if extra != 0:
        logger.warning(
            "%s: the text reads better turned %d more degrees; turned it (the orientation "
            "detection and the reading disagreed)",
            label,
            extra,
        )
        upright = upright.transpose(_TURNS[extra])
    return upright, True
