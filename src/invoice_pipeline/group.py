"""Read the invoice number of every page and group the pages by it.

The AI only sees the small crops of the invoice number, each one after a neutral
label (P01, P02, ...), and says which number each label shows. It never sees a
file name or a vendor name. The code keeps the table label -> page, so it can turn
the answer into groups of pages and save them in ``groups/<vendor>.json``.

Pages whose number is unreadable, ambiguous or odd go to ``review`` and are never
guessed. ``extract.py`` then reads the body crop of every page of a group.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import io
import logging
import re
from pathlib import Path

import anthropic
import pydantic
from PIL import Image

from invoice_pipeline.schemas import InvoiceGroups, NumbersReading, PageNumber

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"
# The reply is a short list, but this model thinks before it answers.
MAX_TOKENS = 8000
# A crop resized to this width was read right 57 times out of 57 in a test.
IMAGE_WIDTH = 600
# The API accepts at most 100 images in one request.
MAX_IMAGES = 100
# Only a soft check that works for any vendor: no spaces and ordinary characters.
NUMBER_PATTERN = re.compile(r"[A-Za-z0-9./#_-]+")

PROMPT = """\
Each image is a small crop of a printed invoice that shows its invoice number.
The label before each image names it. For every label, read the invoice number
exactly as printed, with no spaces, and return it with that label. If you cannot
read it with certainty, use null: never guess a digit. Return every label once.
"""


class GroupingError(Exception):
    """Raised when the pages of a vendor cannot be grouped."""


def list_pages(vendor_dir: Path) -> list[Path]:
    """The number crops of a vendor as paths relative to ``vendor_dir``, sorted."""
    vendor_dir = Path(vendor_dir)
    return sorted(p.relative_to(vendor_dir) for p in vendor_dir.glob("*/page_*.png"))


def label_pages(vendor_dir: Path, pages: list[Path]) -> dict[str, list[Path]]:
    """Give a label to every distinct image; pages with identical pixels share one.

    A page repeated in two PDFs is read once, but every copy still gets the number.
    Labels run through the whole vendor (P01, P02, ...), not per PDF.
    """
    by_content: dict[str, list[Path]] = {}
    for page in pages:
        digest = hashlib.sha256((Path(vendor_dir) / page).read_bytes()).hexdigest()
        by_content.setdefault(digest, []).append(page)
    return {f"P{number:02d}": paths for number, paths in enumerate(by_content.values(), start=1)}


def _clean(number: str | None) -> str | None:
    """The number, or None when it is empty or has spaces or odd characters."""
    if number is None:
        return None
    number = number.strip()
    return number if NUMBER_PATTERN.fullmatch(number) else None


def build_groups(
    labels: dict[str, list[Path]], readings: list[PageNumber]
) -> tuple[dict[str, list[str]], list[str]]:
    """Turn the AI's answer into ``(groups, review)``.

    A label the AI left out, read as null, answered twice with different numbers or
    read as an odd number sends its pages to ``review``. An answer for a label that
    was never given is ignored with a warning.
    """
    answers: dict[str, list[str | None]] = {}
    for reading in readings:
        if reading.label not in labels:
            logger.warning("The AI answered for an unknown label: %r", reading.label)
            continue
        answers.setdefault(reading.label, []).append(_clean(reading.invoice_number))

    groups: dict[str, list[str]] = {}
    review: list[str] = []
    for label, paths in labels.items():
        numbers = set(answers.get(label) or [None])
        number = numbers.pop() if len(numbers) == 1 else None
        if number is None:
            logger.warning("%s has no usable number; its page(s) go to review", label)
            review.extend(path.as_posix() for path in paths)
        else:
            groups.setdefault(number, []).extend(path.as_posix() for path in paths)
    return {number: sorted(paths) for number, paths in groups.items()}, sorted(review)


def _image_block(path: Path) -> dict:
    """One crop, resized to ``IMAGE_WIDTH``, as an image block for the API."""
    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.width != IMAGE_WIDTH:
            height = max(1, round(image.height * IMAGE_WIDTH / image.width))
            image = image.resize((IMAGE_WIDTH, height), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
    data = base64.standard_b64encode(buffer.getvalue()).decode("utf-8")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def read_numbers(
    client: anthropic.Anthropic, vendor_dir: Path, labels: dict[str, list[Path]]
) -> tuple[NumbersReading, int, int]:
    """Send one number crop per label to the AI; return its answer and the tokens used."""
    if len(labels) > MAX_IMAGES:
        raise GroupingError(f"{len(labels)} images are too many for one request ({MAX_IMAGES})")

    content: list[dict] = []
    try:
        for label, paths in labels.items():
            content.append({"type": "text", "text": label})
            content.append(_image_block(Path(vendor_dir) / paths[0]))
        content.append({"type": "text", "text": PROMPT})
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_format=NumbersReading,
            messages=[{"role": "user", "content": content}],
        )
    except OSError as error:
        raise GroupingError(f"Could not read a number crop: {error}") from error
    except anthropic.APIError as error:
        raise GroupingError(f"API call failed: {error}") from error
    except pydantic.ValidationError as error:
        raise GroupingError(f"The reply did not match the schema: {error}") from error

    if response.parsed_output is None:
        raise GroupingError(f"No result (stop reason: {response.stop_reason})")
    usage = response.usage
    return response.parsed_output, usage.input_tokens, usage.output_tokens


def group_vendor(client: anthropic.Anthropic, vendor_dir: Path, output_path: Path) -> Path:
    """Read the numbers of one vendor's pages and save the groups as JSON.

    ``vendor_dir`` is the vendor's folder in ``id_crops``. Running it again calls
    the AI again and overwrites the previous file.
    """
    vendor_dir, output_path = Path(vendor_dir), Path(output_path)
    pages = list_pages(vendor_dir)
    if not pages:
        raise GroupingError(f"No number crops found in {vendor_dir}")

    labels = label_pages(vendor_dir, pages)
    logger.info(
        "%s: %d page(s), %d distinct image(s) to read",
        vendor_dir.name,
        len(pages),
        len(labels),
    )
    reading, tokens_in, tokens_out = read_numbers(client, vendor_dir, labels)
    groups, review = build_groups(labels, reading.pages)

    result = InvoiceGroups(
        model=MODEL,
        created_at=datetime.datetime.now(datetime.UTC),
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        groups=groups,
        review=review,
        labels={label: [p.as_posix() for p in paths] for label, paths in labels.items()},
        readings=reading.pages,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "Saved %s: %d invoice(s), %d page(s) to review, %d input / %d output tokens",
        output_path.name,
        len(groups),
        len(review),
        tokens_in,
        tokens_out,
    )
    return output_path


def main() -> None:
    """Group the pages of every known vendor by invoice number (manual run)."""
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging
    from invoice_pipeline.vendors import vendor_folders

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    client = anthropic.Anthropic(api_key=config.require_api_key())
    grouped = 0
    for vendor_dir in vendor_folders(config.id_crops_dir, config.vendors_dir):
        try:
            group_vendor(client, vendor_dir, config.groups_dir / f"{vendor_dir.name}.json")
            grouped += 1
        except GroupingError:
            logger.exception("Skipping %s", vendor_dir.name)
    logger.info("Done: %d vendor(s) grouped. JSON files in %s", grouped, config.groups_dir)


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.group import main as _main

    _main()
