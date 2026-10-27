"""Extract the invoice lines from the cropped page images with AI vision."""

from __future__ import annotations

import base64
import datetime
import logging
import re
from pathlib import Path

import anthropic
import pydantic

from invoice_pipeline.schemas import (
    ExtractedLine,
    ExtractedUnfulfilled,
    InvoiceExtraction,
    InvoiceLine,
    InvoiceTotals,
    PageExtraction,
    UnfulfilledItem,
)

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
# One page is about 1,100 tokens of JSON, so this leaves plenty of room.
MAX_TOKENS = 10000
# Reading a table does not need deep reasoning; a low effort keeps it cheap and fast.
EFFORT = "low"

# What each field is lives in the field descriptions of ``schemas.py`` (they
# travel inside the response schema). The prompt holds how to behave and the
# rules that cross several fields.
PROMPT = """\
This image is one cropped page of a vendor invoice. Columns are separated by
thin vertical lines. Read the page and fill the sections of the response.

1. lines: the item table
Every item row, from top to bottom. A row has quantities, a UPC and prices.
- Each column has its own cell. Never put text from the Brand column into the
  description, and never complete text that is cut off at the edge of a cell:
  copy what is visible.
- If a description wraps over several lines, join them with a single space.
- Three quantity columns sit side by side: 'Qty Ord.' is the quantity ordered,
  'Shipped' is the quantity shipped now, and 'B/O' ('A Venir', back order) is
  the quantity ordered that was NOT shipped and will come later. They often
  hold the same number. qty_shipped is ONLY the 'Shipped' column: a row with
  Shipped 0 and B/O 1 has qty_shipped 0. Never copy B/O into qty_shipped.
- Copy each value exactly as printed. Do not calculate, correct or guess.
  If a value is not visible, use null.

2. continued_description
If the very top of the page has a line of text with no quantities, UPC or
prices, it is the end of the description of the previous page's last row.
Put that text here and do not create a row for it. Otherwise use null.

3. unfulfilled: the "Unfulfilled supply" list
Rows made of a UPC, an item number, a description, a quantity, a unit and a
reason, ending with a date. This list can continue on the next page without
its title, so any row that starts with a UPC and ends with a date belongs
here and never in lines. If the page has no such rows, return an empty list.

4. totals
The invoice totals block is a few labelled amounts under the item table: an
optional SUBTOTAL, fee or tax lines (for example DEPOSIT FEE, CONTAINER FEE,
GST) and a "** TOTAL **" line. The block can be split over two pages, and a
short invoice may print only the TOTAL line.
- Return totals as soon as ANY of those labels is visible on this page, even
  if it is the only one (a page with just the "** TOTAL **" line still
  returns totals with that total). Use null for totals only if none is visible.
- Fill only what this page shows. subtotal is the amount next to SUBTOTAL and
  total is the amount next to "** TOTAL **"; if that label is not on this page,
  use null. Never use a fee, a tax or the last amount shown as the total.
- charges are the fee and tax lines on this page, in order, without SUBTOTAL
  and TOTAL.

Ignore everything else: column headers, footers and page numbers.
"""


# Added to the prompt when the code knows the page continues the "Unfulfilled
# supply" list: without its title, the AI reads those rows as item lines.
CONTINUATION_NOTE = """
NOTE: this page continues the "Unfulfilled supply" list from the previous page.
The black header row at the top is repeated on every page and does not mean the
rows below are items. Put every row of this page in unfulfilled and return
lines as an empty list.
"""

NUMBER_FIELDS = (
    "qty_ordered",
    "qty_shipped",
    "price",
    "net_price",
    "discount_pct",
    "unit_price",
    "total",
)


class ExtractionError(Exception):
    """Raised when a page or an invoice cannot be extracted."""


def _is_empty_row(line: InvoiceLine) -> bool:
    """True for a row with no UPC and no numbers, that is, just a piece of text.

    At the top of a page it is the end of a description that ran over from the
    previous page, not an item.
    """
    return line.upc is None and all(getattr(line, name) is None for name in NUMBER_FIELDS)


def _looks_like_unfulfilled_rows(page: PageExtraction) -> bool:
    """True if the page has item lines but none of them has a unit price.

    Real item lines always have one. After an "Unfulfilled supply" list, such a
    page is the continuation of that list.
    """
    return bool(page.lines) and all(line.unit_price is None for line in page.lines)


def _lines_to_unfulfilled(
    page_lines: list[InvoiceLine], known_lines: list[ExtractedLine]
) -> list[UnfulfilledItem]:
    """Turn lines the AI read as items into unfulfilled items, using what the code knows.

    The fallback for when the AI cannot read an "Unfulfilled supply" page right.
    The description comes from the item line with the same UPC, because the AI
    only reads its last text column here. The quantity is what that line left
    unshipped. The reason is not in the item columns, so it stays None. A line
    without a UPC cannot be matched and is left out.
    """
    known_by_upc = {line.upc: line for line in known_lines if line.upc}
    items = []
    for line in page_lines:
        if line.upc is None:
            continue
        known = known_by_upc.get(line.upc)
        description = known.description if known else line.description
        quantity = line.qty_shipped if line.qty_shipped is not None else line.qty_ordered
        if known and known.qty_ordered is not None and known.qty_shipped is not None:
            unshipped = known.qty_ordered - known.qty_shipped
            if unshipped > 0:
                quantity = unshipped
        items.append(
            UnfulfilledItem(upc=line.upc, description=description, quantity=quantity, reason=None)
        )
    return items


def _merge_totals(
    first: InvoiceTotals, extra: InvoiceTotals, invoice_name: str, page_number: int
) -> InvoiceTotals:
    """Join two totals blocks read from different pages.

    The totals block can be split over two pages (the subtotal at the bottom of
    one, the fees and the total at the top of the next). Each amount is taken
    from the first block that has it, and the fees are put together in order. If
    both blocks have a value for the same amount and they differ, the first one
    is kept and a warning is logged. A fee already read (same label and amount)
    is not added twice.
    """
    merged: dict[str, float | None] = {}
    for name in ("subtotal", "total"):
        old, new = getattr(first, name), getattr(extra, name)
        if old is not None and new is not None and old != new:
            logger.warning(
                "%s page %d: the %s differs from the one already read (%s vs %s); keeping the first",
                invoice_name,
                page_number,
                name,
                old,
                new,
            )
        merged[name] = old if old is not None else new
    charges = list(first.charges)
    for charge in extra.charges:
        if charge not in charges:
            charges.append(charge)
    return InvoiceTotals(charges=charges, **merged)


def invoice_number_from_name(name: str) -> str | None:
    """Take the invoice number from a file or folder name ("Invoice_1234567" -> "1234567").

    Uses the longest run of digits, so a short number such as the "2" in "copy 2"
    does not win over the real one. Returns None when the name has no digits.
    """
    numbers = re.findall(r"\d+", name)
    return max(numbers, key=len) if numbers else None


def extract_page(
    client: anthropic.Anthropic, image_path: Path, continues_unfulfilled: bool = False
) -> PageExtraction:
    """Send one page image to the AI and return what it read.

    The response schema is enforced by the API, and the SDK validates the reply
    against ``PageExtraction``. ``continues_unfulfilled`` tells the AI that the
    page continues the "Unfulfilled supply" list. Raises ExtractionError if the
    call fails or the reply is unusable (a refusal, or a reply cut off at
    ``MAX_TOKENS``).
    """
    prompt = PROMPT + CONTINUATION_NOTE if continues_unfulfilled else PROMPT
    try:
        image_data = base64.standard_b64encode(Path(image_path).read_bytes()).decode("utf-8")
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            output_format=PageExtraction,
            messages=[
                {
                    "role": "user",
                    "content": [
                        # The image goes before the text, as the API docs recommend.
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_data,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
    except OSError as error:
        raise ExtractionError(f"Could not read {Path(image_path).name}: {error}") from error
    except anthropic.APIError as error:
        raise ExtractionError(f"API call failed for {Path(image_path).name}: {error}") from error
    except pydantic.ValidationError as error:
        # The SDK could not parse the reply: a refusal or a reply cut at max_tokens.
        raise ExtractionError(
            f"The reply for {Path(image_path).name} did not match the schema: {error}"
        ) from error

    page = response.parsed_output
    if page is None:
        raise ExtractionError(
            f"No result for {Path(image_path).name} (stop reason: {response.stop_reason})"
        )

    logger.debug(
        "%s: %d input / %d output tokens",
        Path(image_path).name,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    return page


def extract_invoice(client: anthropic.Anthropic, invoice_dir: Path, extracted_dir: Path) -> Path:
    """Extract every page of one invoice and save the final JSON.

    ``invoice_dir`` holds ``page_001.png``, ... and the JSON is saved as
    ``extracted_dir/<invoice folder name>.json``. The invoice number, the
    extraction date, each line's page and the joining of descriptions that run
    over two pages are done here, by the code, not by the AI.

    If any page fails, nothing is saved: a JSON with missing pages would look
    like a complete invoice. Running it again overwrites the previous JSON.
    """
    invoice_dir, extracted_dir = Path(invoice_dir), Path(extracted_dir)
    page_images = sorted(invoice_dir.glob("page_*.png"))
    if not page_images:
        raise ExtractionError(f"No page images found in {invoice_dir}")

    lines: list[ExtractedLine] = []
    unfulfilled: list[ExtractedUnfulfilled] = []
    totals: InvoiceTotals | None = None
    # True while the pages being read belong to the "Unfulfilled supply" list.
    in_unfulfilled = False
    for image_path in page_images:
        # The page number comes from the file name, so a page missing from the
        # folder cannot shift the numbering of the ones after it.
        page_number = int(image_path.stem.removeprefix("page_"))
        page = extract_page(client, image_path)

        # The list continues on the next page without its title, so the AI reads
        # its rows as item lines. They give themselves away by having no unit
        # price: read the page again, telling the AI what it is.
        continues_list = in_unfulfilled and _looks_like_unfulfilled_rows(page)
        if continues_list:
            logger.info(
                "%s page %d continues the unfulfilled list; reading it again",
                invoice_dir.name,
                page_number,
            )
            try:
                page = extract_page(client, image_path, continues_unfulfilled=True)
            except ExtractionError as error:
                # The first reading is still there to fall back on.
                logger.warning(
                    "%s page %d: reading it again failed (%s)", invoice_dir.name, page_number, error
                )
            if _looks_like_unfulfilled_rows(page):
                # Still read as items. The code knows what they are, so it moves them.
                moved = _lines_to_unfulfilled(page.lines, lines)
                logger.warning(
                    "%s page %d: moved %d of %d line(s) to unfulfilled by the code "
                    "(no reason available; lines without a UPC are dropped)",
                    invoice_dir.name,
                    page_number,
                    len(moved),
                    len(page.lines),
                )
                page = page.model_copy(
                    update={"lines": [], "unfulfilled": [*page.unfulfilled, *moved]}
                )
        # Stays True through a continuation page even if nothing was read from it,
        # so a further page of the list is still recognised.
        in_unfulfilled = bool(page.unfulfilled) or continues_list

        # A row split across two pages: its last description line is at the top
        # of this page, either in ``continued_description`` or as a row with no
        # UPC and no numbers. It belongs to the last line read so far, so it is
        # joined before this page's own lines are added.
        page_lines = list(page.lines)
        fragments = [(page.continued_description or "").strip()]
        if page_lines and _is_empty_row(page_lines[0]):
            fragments.append(page_lines.pop(0).description.strip())
        fragment = " ".join(dict.fromkeys(text for text in fragments if text))
        if fragment:
            if lines:
                lines[-1].description = f"{lines[-1].description} {fragment}"
            else:
                logger.warning(
                    "%s page %d starts with %r but there is no previous line to join it to",
                    invoice_dir.name,
                    page_number,
                    fragment,
                )

        lines.extend(ExtractedLine(**line.model_dump(), page=page_number) for line in page_lines)
        unfulfilled.extend(
            ExtractedUnfulfilled(**item.model_dump(), page=page_number) for item in page.unfulfilled
        )
        if page.totals is not None:
            if totals is None:
                totals = page.totals
            else:
                logger.info(
                    "%s page %d shows more of the totals block; joining it with the first",
                    invoice_dir.name,
                    page_number,
                )
                totals = _merge_totals(totals, page.totals, invoice_dir.name, page_number)
        logger.info(
            "Extracted %s page %d: %d line(s), %d unfulfilled",
            invoice_dir.name,
            page_number,
            len(page_lines),
            len(page.unfulfilled),
        )

    if totals is None:
        logger.warning("%s: no totals block found on any page", invoice_dir.name)

    result = InvoiceExtraction(
        invoice_number=invoice_number_from_name(invoice_dir.name),
        extracted_at=datetime.date.today(),
        lines=lines,
        unfulfilled=unfulfilled,
        totals=totals,
    )

    extracted_dir.mkdir(parents=True, exist_ok=True)
    output_path = extracted_dir / f"{invoice_dir.name}.json"
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Saved %s: %d line(s)", output_path.name, len(lines))
    return output_path


def extract_all(
    client: anthropic.Anthropic, crops_dir: Path, extracted_dir: Path
) -> dict[Path, Path]:
    """Extract every invoice folder under ``crops_dir``; a failing invoice is logged and skipped.

    A missing ``crops_dir`` is logged as a warning and extracts nothing.

    Returns a mapping of each invoice folder to its JSON file.
    """
    crops_dir = Path(crops_dir)
    if not crops_dir.is_dir():
        logger.warning("Input folder does not exist: %s", crops_dir)
        return {}

    invoice_dirs = sorted(p for p in crops_dir.iterdir() if p.is_dir())
    if not invoice_dirs:
        logger.warning("No crop folders found in %s", crops_dir)

    results: dict[Path, Path] = {}
    for invoice_dir in invoice_dirs:
        try:
            results[invoice_dir] = extract_invoice(client, invoice_dir, extracted_dir)
        except ExtractionError:
            logger.exception("Skipping %s", invoice_dir.name)
    return results


def main() -> None:
    """Extract every cropped invoice of every known vendor (manual run)."""
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging
    from invoice_pipeline.vendors import vendor_folders

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    client = anthropic.Anthropic(api_key=config.require_api_key())
    extracted = 0
    for vendor_dir in vendor_folders(config.crops_dir, config.vendors_dir):
        extracted_dir = config.extracted_dir / vendor_dir.name
        extracted += len(extract_all(client, vendor_dir, extracted_dir))
    logger.info("Done: %d invoice(s) extracted. JSON files in %s", extracted, config.extracted_dir)


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.extract import main as _main

    _main()
