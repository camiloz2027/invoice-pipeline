"""Read the invoice number of every page and group the pages by it.

The AI only sees the small crops of the invoice number, each one after a neutral
label (P01, P02, ...). It never sees a file name or a vendor name. The code keeps
which label is which page, turns the answer into groups of pages and saves them in
groups/<vendor>.json. Pages whose number is unreadable or odd go to "review".
"""

import base64
import datetime
import hashlib
import io
import logging
import re

import anthropic
import pydantic
from PIL import Image

from invoice_pipeline.config import load_config
from invoice_pipeline.logging_setup import setup_logging
from invoice_pipeline.schemas import InvoiceGroups, NumbersReading
from invoice_pipeline.vendors import vendor_folders

logger = logging.getLogger("invoice_pipeline.group")

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 8000  # the reply is short, but the model thinks before it answers
IMAGE_WIDTH = 600  # at this width the crops were read right 57 times out of 57
MAX_IMAGES = 100  # the API accepts at most 100 images in one request
# Soft check that works for any vendor: no spaces and ordinary characters.
NUMBER_PATTERN = re.compile(r"[A-Za-z0-9./#_-]+")

PROMPT = """\
Each image is a small crop of a printed invoice that shows its invoice number.
The label before each image names it. For every label, read the invoice number
exactly as printed, with no spaces, and return it with that label. If you cannot
read it with certainty, use null: never guess a digit. Return every label once.
"""


def label_pages(vendor_dir):
    """Give a label (P01, P02, ...) to every different image of the vendor.

    Pages with identical pixels (the same page in two PDFs) share one label, so
    they are read once. Returns {label: ["<invoice folder>/page_001.png", ...]}.
    """
    by_content = {}
    for path in sorted(vendor_dir.glob("*/page_*.png")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in by_content:
            by_content[digest] = []
        by_content[digest].append(f"{path.parent.name}/{path.name}")

    labels = {}
    for number, pages in enumerate(by_content.values(), start=1):
        labels[f"P{number:02d}"] = pages
    return labels


def clean_number(number):
    """Return the number without spaces around it, or None if it is empty or odd."""
    if number is None:
        return None
    number = number.strip()
    if NUMBER_PATTERN.fullmatch(number):
        return number
    return None


def build_groups(labels, readings):
    """Turn the AI's answer into (groups, review).

    A label that has no answer, a null or odd number, or two different answers
    sends its pages to review.
    """
    answers = {}
    for reading in readings:
        if reading.label not in labels:
            logger.warning("The AI answered for an unknown label: %r", reading.label)
            continue
        if reading.label not in answers:
            answers[reading.label] = []
        answers[reading.label].append(clean_number(reading.invoice_number))

    groups = {}
    review = []
    for label, pages in labels.items():
        numbers = answers.get(label, [])
        # Exactly one number (it may be repeated, but never two different ones).
        number = None
        if len(set(numbers)) == 1:
            number = numbers[0]

        if number is None:
            logger.warning("%s has no usable number; its page(s) go to review", label)
            review.extend(pages)
        else:
            if number not in groups:
                groups[number] = []
            groups[number].extend(pages)

    for pages in groups.values():
        pages.sort()
    review.sort()
    return groups, review


def image_block(path):
    """Resize one crop to IMAGE_WIDTH and return it as an image block for the API."""
    with Image.open(path) as image:
        height = round(image.height * IMAGE_WIDTH / image.width)
        image = image.resize((IMAGE_WIDTH, height), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")

    data = base64.standard_b64encode(buffer.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def read_numbers(client, vendor_dir, labels):
    """Send one crop per label to the AI and return its response."""
    content = []
    for label, pages in labels.items():
        content.append({"type": "text", "text": label})
        content.append(image_block(vendor_dir / pages[0]))
    content.append({"type": "text", "text": PROMPT})

    return client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_format=NumbersReading,
        messages=[{"role": "user", "content": content}],
    )


def group_vendor(client, vendor_dir, output_path):
    """Read the numbers of one vendor's pages and save the groups as JSON.

    vendor_dir is the vendor's folder in id_crops. Running it again calls the AI
    again and overwrites the file. Returns True if the file was saved.
    """
    labels = label_pages(vendor_dir)
    if not labels:
        logger.warning("No number crops found for %s", vendor_dir.name)
        return False
    if len(labels) > MAX_IMAGES:
        logger.error("%s: %d images are too many for one request", vendor_dir.name, len(labels))
        return False

    logger.info("%s: %d image(s) to read", vendor_dir.name, len(labels))
    response = read_numbers(client, vendor_dir, labels)
    if response.parsed_output is None:
        logger.error("%s: no result (stop reason: %s)", vendor_dir.name, response.stop_reason)
        return False

    readings = response.parsed_output.pages
    groups, review = build_groups(labels, readings)

    result = InvoiceGroups(
        model=MODEL,
        created_at=datetime.datetime.now(datetime.UTC),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        groups=groups,
        review=review,
        labels=labels,
        readings=readings,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "Saved %s: %d invoice(s), %d page(s) to review, %d input / %d output tokens",
        output_path.name,
        len(groups),
        len(review),
        result.input_tokens,
        result.output_tokens,
    )
    return True


def main():
    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    client = anthropic.Anthropic(api_key=config.require_api_key())

    vendor_dirs = vendor_folders(config.id_crops_dir, config.vendors_dir)
    if not vendor_dirs:
        logger.warning("No number crops in %s: run crop.py first", config.id_crops_dir.name)
        return

    grouped = 0
    for vendor_dir in vendor_dirs:
        output_path = config.groups_dir / f"{vendor_dir.name}.json"
        try:
            if group_vendor(client, vendor_dir, output_path):
                grouped += 1
        except (OSError, anthropic.APIError, pydantic.ValidationError):
            logger.exception("Skipping %s", vendor_dir.name)

    logger.info("Done: %d vendor(s) grouped. Groups in %s", grouped, config.groups_dir.name)


if __name__ == "__main__":
    main()
