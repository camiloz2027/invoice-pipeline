"""Extract the invoice lines from the cropped page images with AI vision."""

from __future__ import annotations

import base64
import datetime
import hashlib
import logging
import re
from pathlib import Path

import anthropic
import pydantic

from invoice_pipeline.schemas import (
    ExtractedLine,
    InvoiceExtraction,
    InvoiceGroups,
    InvoiceLine,
    InvoiceTotals,
    PageExtraction,
)

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
# A page is a few thousand tokens of JSON, and this model's reasoning counts toward the limit.
MAX_TOKENS = 16000
# Strike-throughs and faint print need careful reading, so the effort is high.
EFFORT = "high"

# What each field is lives in the field descriptions of ``schemas.py`` (they
# travel inside the response schema). The prompt holds how to behave and the
# rules that cross several fields.
PROMPT = """\
This image is one cropped page of a vendor invoice. Read the page and fill the
sections of the response. Describe every column by what it means, not by its header.

1. lines: the item table
Every item row, from top to bottom. Copy each value exactly as printed. Do not
calculate, correct or guess. If a value is not printed or cannot be read, use null.
- Codes: upc is a barcode, made only of digits, at least 10 of them. Any other code
  (with letters, or shorter) is the vendor's own item_code. A cell such as "N/A" is null.
- description: join its lines with a single space. Never put text from another
  column into it, and never complete text that is cut off at the edge of the cell.
- size: the pack or size of the item, such as 6/1.75L or 12/170G. Take it from its own
  column; if it is split over two columns (a count and a size), write count/size.
  If there is no size column, take it from the description, which keeps its text.
- Quantities: an invoice can print the quantity ordered, shipped and back-ordered
  ('B/O', 'A Venir': ordered but NOT shipped, it will come later). quantity is the one
  that makes quantity x price = line_total, usually the shipped one: a row that shipped 0
  has quantity 0. A single quantity column is quantity. If the table header has a column
  for the quantity ordered or back-ordered, fill qty_ordered or qty_backordered in EVERY
  row of the page (an empty cell is null), even when the page looks different from
  the others. Leave them null only when the invoice has no such column.
- Prices: price is the price actually paid, after any discount, for one selling unit
  (usually a case): quantity x price = line_total. Choose the column by that
  arithmetic, not by its header. When a row prints a discount percentage, price is
  the column AFTER the discount and list_price the column before it: never put the
  price before the discount in price. list_price is the price before the discount,
  only when the invoice prints it next to a different price. unit_price is the price paid
  for one piece inside the case, only when the invoice prints it in addition to price.
  price_per is the unit that price refers to, as printed (such as CS or ea); null if
  the invoice prints none. Never calculate a price.
- line_total: the amount of the whole row, usually the rightmost number.
  A value written '.00' is 0.
- A row that charges a fee or a deposit instead of an item is not an item: skip it.
  Skip columns that only carry a deposit or a tax.
- Text written by hand (strike-throughs, ticks, notes) is not part of the invoice:
  a row crossed out by hand is still a printed row, so read the printed text under
  the mark and keep its quantity and prices.

Example 1. A row printed with these cells:
  Ord 5 | Shipped 5 | Code 12345 | EXAMPLE OAT DRINK 6/1.75L | UPC 012345678905 |
  UOM CS | Price 32.80 | Disc 20 | Net Price 26.24 | Unit Price 4.37 | Extended 131.20
is read as:
  {"item_code": "12345", "upc": "012345678905", "description": "EXAMPLE OAT DRINK 6/1.75L",
   "size": "6/1.75L", "quantity": 5, "qty_ordered": 5, "qty_backordered": null,
   "list_price": 32.80, "price": 26.24, "price_per": "CS", "unit_price": 4.37,
   "line_total": 131.20}
because 5 x 26.24 = 131.20. The 32.80 is the price before the 20% discount.

Example 2. A row printed with these cells:
  Product 012345678912 | Qty shipped 36 | EXAMPLE BAR 55G | Unit price 3.18 | Per ea |
  Amount 114.48
is read as:
  {"item_code": null, "upc": "012345678912", "description": "EXAMPLE BAR 55G",
   "size": "55G", "quantity": 36, "qty_ordered": null, "qty_backordered": null,
   "list_price": null, "price": 3.18, "price_per": "ea", "unit_price": null,
   "line_total": 114.48}

2. continued_description
If the very top of the page has a line of text with no quantities, code or
prices, it is the end of the description of the previous page's last row.
Put that text here and do not create a row for it. Otherwise use null.

3. totals
The invoice totals block is a few labelled amounts under the item table: an
optional subtotal, fee or tax lines (for example a deposit fee, a fuel surcharge,
GST) and the final total. The block can be split over two pages, and a short
invoice may print only the total.
- Return totals as soon as ANY of those labels is visible on this page, even
  if it is the only one (a page with just the total line still returns totals with
  that total). Use null for totals only if none is visible.
- Fill only what this page shows. subtotal is the amount next to the subtotal label
  and total is the amount next to the final total label; if that label is not on
  this page, use null. Never use a fee, a tax or the last amount shown as the total.
- charges are the fee and tax lines on this page, in order, without the subtotal
  and the total.

Ignore everything else: column headers, section headings (such as a storage area),
footers with counts or weights, payment notes, page numbers, anything handwritten,
and any list of items that were not supplied or are out of stock.
"""

NUMBER_FIELDS = (
    "quantity",
    "qty_ordered",
    "qty_backordered",
    "list_price",
    "price",
    "unit_price",
    "line_total",
)


# A pack size such as 12/325G, 6/6X38G or 6/1.75L, with no trailing "*".
SIZE_PATTERN = re.compile(r"\d+(?:\.\d+)?(?:\s?[/xX]\s?\d+(?:\.\d+)?)+\s?[A-Za-z]+")


# The ways the invoices print "sold by the case", all written as CS.
PRICE_PER_ALIASES = {"CASE": "CS", "PER CASE": "CS", "CS": "CS", "MC": "CS"}


class ExtractionError(Exception):
    """Raised when a page or an invoice cannot be extracted."""


def _size_in(description: str) -> str | None:
    """The pack size written at the end of a description, such as 12/325G, or None."""
    matches = SIZE_PATTERN.findall(description)
    return matches[-1].replace(" ", "") if matches else None


def _standard_price_per(value: str | None) -> str | None:
    """The unit a price refers to, with the ways of writing "case" turned into CS.

    Any other unit (such as "ea") is kept as printed, only with its spaces tidied.
    """
    text = " ".join((value or "").split())
    return PRICE_PER_ALIASES.get(text.upper(), text) or None


def _is_empty_row(line: InvoiceLine) -> bool:
    """True for a row with no UPC, no item code and no numbers, that is, just a piece of text.

    At the top of a page it is the end of a description that ran over from the
    previous page, not an item.
    """
    return (
        line.upc is None
        and line.item_code is None
        and all(getattr(line, name) is None for name in NUMBER_FIELDS)
    )


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


def _prompt(notes: str | None) -> str:
    """The prompt, with the vendor's notes at the end when it has any."""
    if not notes:
        return PROMPT
    return f"{PROMPT}\nNotes about these invoices:\n{notes}\n"


def extract_page(
    client: anthropic.Anthropic, image_path: Path, notes: str | None = None
) -> PageExtraction:
    """Send one page image to the AI and return what it read.

    The response schema is enforced by the API, and the SDK validates the reply
    against ``PageExtraction``. ``notes`` is the vendor's text for the prompt. Raises
    ExtractionError if the call fails or the reply is unusable (a refusal, or a reply
    cut off at ``MAX_TOKENS``).
    """
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
                        {"type": "text", "text": _prompt(notes)},
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

    logger.info(
        "%s: %d input / %d output tokens",
        Path(image_path).name,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    return page


def extract_invoice(
    client: anthropic.Anthropic,
    page_images: list[Path],
    invoice_number: str | None,
    extracted_dir: Path,
    name: str,
    notes: str | None = None,
) -> Path:
    """Extract the pages of one invoice and save the final JSON.

    ``page_images`` are the body crops of the invoice in reading order; they can
    come from different PDFs. The JSON is saved as ``extracted_dir/<name>.json``
    and ``name`` is also what the log calls the invoice. The invoice number, the
    extraction date, each line's page (its position in ``page_images``) and the
    joining of descriptions that run over two pages are done here, by the code,
    not by the AI. ``notes`` is the vendor's text for the prompt.

    If any page fails, nothing is saved: a JSON with missing pages would look
    like a complete invoice. Running it again overwrites the previous JSON.
    """
    extracted_dir = Path(extracted_dir)
    if not page_images:
        raise ExtractionError(f"No page images given for {name}")

    lines: list[ExtractedLine] = []
    totals: InvoiceTotals | None = None
    for page_number, image_path in enumerate(page_images, start=1):
        page = extract_page(client, image_path, notes)

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
                    name,
                    page_number,
                    fragment,
                )

        # A row with neither a price nor a total is not an item (for example a list of
        # items that were not supplied, which the prompt tells the AI to ignore).
        priced = [
            line for line in page_lines if line.price is not None or line.line_total is not None
        ]
        if len(priced) < len(page_lines):
            logger.warning(
                "%s page %d: dropped %d row(s) with no price and no total",
                name,
                page_number,
                len(page_lines) - len(priced),
            )
        page_lines = priced

        lines.extend(ExtractedLine(**line.model_dump(), page=page_number) for line in page_lines)
        if page.totals is not None:
            if totals is None:
                totals = page.totals
            else:
                logger.info(
                    "%s page %d shows more of the totals block; joining it with the first",
                    name,
                    page_number,
                )
                totals = _merge_totals(totals, page.totals, name, page_number)
        logger.info("Extracted %s page %d: %d line(s)", name, page_number, len(page_lines))

    # A row split across two pages has its size in the joined description, so the
    # AI left it null when it read the first page. Any other row with no size gets
    # the same fallback.
    # A trailing "*" is a mark printed in the description, not part of the size.
    for line in lines:
        line.price_per = _standard_price_per(line.price_per)
        if line.size is None:
            line.size = _size_in(line.description)
        elif line.size.endswith("*"):
            line.size = line.size.rstrip("*").strip() or None

    if totals is None:
        logger.warning("%s: no totals block found on any page", name)

    result = InvoiceExtraction(
        invoice_number=invoice_number,
        extracted_at=datetime.date.today(),
        lines=lines,
        totals=totals,
    )

    extracted_dir.mkdir(parents=True, exist_ok=True)
    output_path = extracted_dir / f"{name}.json"
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Saved %s: %d line(s)", output_path.name, len(lines))
    return output_path


def unique_pages(body_dir: Path, pages: list[str]) -> tuple[list[Path], int]:
    """The body crops of ``pages`` without exact copies, and how many copies were left out.

    Two crops with the same content are the same page twice (a PDF repeated inside
    another one), so only the first is kept. It is only used on the pages of one
    invoice: identical pages of two different invoices are both real.
    """
    seen: set[str] = set()
    kept: list[Path] = []
    for page in pages:
        path = Path(body_dir) / page
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            kept.append(path)
    return kept, len(pages) - len(kept)


def _file_name(invoice_number: str) -> str:
    """A safe file name for an invoice number, which can hold characters such as '/' or '.'."""
    return re.sub(r"[^\w-]+", "_", invoice_number).strip("_") or "invoice"


def extract_vendor(
    client: anthropic.Anthropic,
    groups_path: Path,
    body_dir: Path,
    extracted_dir: Path,
    notes: str | None = None,
) -> dict[str, Path]:
    """Extract every invoice listed in a vendor's groups file; a failing one is logged and skipped.

    ``groups_path`` is the file written by ``group.py`` and ``body_dir`` the vendor's folder in
    ``body_crops``. ``notes`` is the vendor's text for the prompt. The pages in the review list
    are never extracted. Returns a mapping of each invoice number to its JSON file.
    """
    groups = InvoiceGroups.model_validate_json(Path(groups_path).read_text(encoding="utf-8"))
    if groups.review:
        logger.warning(
            "%d page(s) are in review and will not be extracted: %s",
            len(groups.review),
            ", ".join(groups.review),
        )

    results: dict[str, Path] = {}
    names: dict[str, str] = {}
    for number, pages in groups.groups.items():
        name = _file_name(number)
        if name in names:
            logger.error("Skipping %s: its file name clashes with invoice %s", number, names[name])
            continue
        names[name] = number
        try:
            page_images, repeated = unique_pages(body_dir, pages)
        except OSError:
            logger.exception("Skipping %s: could not read its pages", number)
            continue
        if repeated:
            logger.info("%s: left out %d repeated page(s)", number, repeated)
        try:
            results[number] = extract_invoice(
                client, page_images, number, extracted_dir, name, notes
            )
        except ExtractionError:
            logger.exception("Skipping %s", number)
    return results


def main() -> None:
    """Extract the invoices of every known vendor, or of one, from its groups file (manual run)."""
    import argparse

    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging
    from invoice_pipeline.vendors import load_vendor, vendor_folders

    parser = argparse.ArgumentParser(
        description="Extract the invoices of the vendors' groups files."
    )
    parser.add_argument("vendor", nargs="?", help="only this vendor folder (default: all)")
    args = parser.parse_args()

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    vendor_dirs = vendor_folders(config.body_crops_dir, config.vendors_dir)
    if args.vendor:
        vendor_dirs = [d for d in vendor_dirs if d.name == args.vendor]
        if not vendor_dirs:
            parser.error(
                f"No usable vendor folder named {args.vendor!r} in {config.body_crops_dir}"
            )
    client = anthropic.Anthropic(api_key=config.require_api_key())
    extracted = 0
    for vendor_dir in vendor_dirs:
        groups_path = config.groups_dir / f"{vendor_dir.name}.json"
        if not groups_path.is_file():
            logger.error("No groups file for %s: run group.py first", vendor_dir.name)
            continue
        try:
            extracted += len(
                extract_vendor(
                    client,
                    groups_path,
                    vendor_dir,
                    config.extracted_dir / vendor_dir.name,
                    load_vendor(config.vendors_dir, vendor_dir.name).notes,
                )
            )
        except (OSError, pydantic.ValidationError):
            logger.exception("Skipping %s: the groups file is unusable", vendor_dir.name)
    logger.info("Done: %d invoice(s) extracted. JSON files in %s", extracted, config.extracted_dir)


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.extract import main as _main

    _main()
