"""Turn a scanned page upright and check the result.

Everything runs locally with Tesseract. First Tesseract's orientation detection turns
the page. Then the result is checked by counting the words Tesseract reads clearly in
each of the four orientations: the upright one must read clearly better.
"""

import logging

import pytesseract
from PIL import Image

logger = logging.getLogger("invoice_pipeline.orientation")

# The page is detected at two sizes (longest side in pixels) and the most confident
# answer is kept: small text can be lost when the page is shrunk.
DETECTION_SIDES = (1600, 2400)
# Below this confidence the detection is not trusted (real pages scored above 2.5).
MIN_CONFIDENCE = 2.0

# Check: the best orientation needs at least MIN_WORDS clear words and twice as many
# as the next one (on real pages the upright one had at least 3.3 times more).
VERIFY_SIDE = 1100
CONFIDENT_WORD = 60
MIN_WORDS = 8
MIN_MARGIN = 2.0

# Clockwise degrees needed to turn the page upright -> the matching transpose.
TURNS = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def check_tesseract():
    """Stop with a clear message if Tesseract is not installed."""
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        raise SystemExit("Tesseract is not installed (for example: brew install tesseract).")


def detect_rotation(image):
    """Return (clockwise degrees to turn the page upright, confidence), or None if unclear."""
    gray = image.convert("L")
    best = None
    for side in DETECTION_SIDES:
        small = gray.copy()
        small.thumbnail((side, side))
        try:
            result = pytesseract.image_to_osd(small, output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractError:
            continue
        answer = (int(result["rotate"]), float(result["orientation_conf"]))
        if best is None or answer[1] > best[1]:
            best = answer
    return best


def count_words(gray):
    """Count the words of 3 or more letters or digits that Tesseract reads with confidence."""
    try:
        data = pytesseract.image_to_data(
            gray, output_type=pytesseract.Output.DICT, config="--psm 6"
        )
    except pytesseract.TesseractError:
        return 0

    count = 0
    for text, conf in zip(data["text"], data["conf"]):
        word = text.strip()
        if len(word) >= 3 and word.isalnum() and float(conf) >= CONFIDENT_WORD:
            count += 1
    return count


def verify_upright(image):
    """Return the clockwise degrees still needed to turn the page upright (0 = upright).

    Returns None if no orientation reads clearly better, for example on a page with
    almost no text.
    """
    gray = image.convert("L")
    gray.thumbnail((VERIFY_SIDE, VERIFY_SIDE))

    scores = {}
    for turn in (0, 90, 180, 270):
        scores[turn] = count_words(gray.rotate(-turn, expand=True))

    # The orientation with the most words, and the score of the next best one.
    best = 0
    for turn, score in scores.items():
        if score > scores[best]:
            best = turn
    runner_up = 0
    for turn, score in scores.items():
        if turn != best and score > runner_up:
            runner_up = score

    if scores[best] < MIN_WORDS or scores[best] < MIN_MARGIN * runner_up:
        return None
    return best


def straighten(image, label="page"):
    """Return (upright page, verified). If verified is False, check the page by hand."""
    detected = detect_rotation(image)
    if detected is None or detected[1] < MIN_CONFIDENCE:
        logger.warning("%s: orientation unclear; left as it is", label)
    elif detected[0] != 0:
        degrees, confidence = detected
        logger.info("%s: rotated %d degrees (confidence %.2f)", label, degrees, confidence)
        image = image.transpose(TURNS[degrees])

    extra = verify_upright(image)
    if extra is None:
        logger.error("%s: orientation could not be verified; check it by hand", label)
        return image, False
    if extra != 0:
        logger.warning("%s: reads better turned %d more degrees; turned it", label, extra)
        image = image.transpose(TURNS[extra])
    return image, True
