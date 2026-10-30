"""Extract the invoice lines from the body crops with AI vision.

Every page of an invoice is sent to the AI on its own. The code then joins the
pages, adds what the AI must not decide (the invoice number, the date and the
page of each line) and saves one JSON per invoice in extracted/<vendor>/.
"""

import base64
import datetime
import hashlib
import logging
import re
import sys

import anthropic
import pydantic

from invoice_pipeline.config import load_config
from invoice_pipeline.logging_setup import setup_logging
from invoice_pipeline.schemas import (
    ExtractedLine,
    InvoiceExtraction,
    InvoiceGroups,
    InvoiceTotals,
    PageExtraction,
)
from invoice_pipeline.vendors import load_vendor, vendor_folders

logger = logging.getLogger("invoice_pipeline.extract")

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 16000  # a page is a few thousand tokens of JSON, plus the model's reasoning
EFFORT = "high"  # strike-throughs and faint print need careful reading

# What each field means is in the field descriptions of schemas.py, which travel
# inside the response schema. The prompt holds how to behave and the rules that
# involve several fields.
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

# A pack size such as 12/325G, 6/6X38G or 6/1.75L: a number, then one or more
# "/number" or "x number", then a unit.
SIZE_PATTERN = re.compile(r"\d+(?:\.\d+)?(?:\s?[/xX]\s?\d+(?:\.\d+)?)+\s?[A-Za-z]+")

# The ways the invoices print "sold by the case", all saved as CS.
PRICE_PER_ALIASES = {"CASE": "CS", "PER CASE": "CS", "CS": "CS", "MC": "CS"}


def size_in(description):
    """Return the last pack size written in the description, such as 12/325G, or None."""
    matches = SIZE_PATTERN.findall(description)
    if not matches:
        return None
    return matches[-1].replace(" ", "")


def standard_price_per(value):
    """Return the unit of the price, with every way of writing "case" saved as CS."""
    if value is None:
        return None
    text = " ".join(value.split())  # remove extra spaces: " per  case " -> "per case"
    if text == "":
        return None
    return PRICE_PER_ALIASES.get(text.upper(), text)


def is_empty_row(line):
    """True for a row with no codes and no numbers: only a piece of text."""
    if line.upc is not None or line.item_code is not None:
        return False
    for field in NUMBER_FIELDS:
        if getattr(line, field) is not None:
            return False
    return True


def merge_totals(first, extra, name, page_number):
    """Join two parts of a totals block that is split over two pages.

    Each amount is taken from the first part that has it. Fees already read
    (same label and amount) are not added twice.
    """
    amounts = {}
    for field in ("subtotal", "total"):
        old = getattr(first, field)
        new = getattr(extra, field)
        if old is not None and new is not None and old != new:
            logger.warning(
                "%s page %d: the %s differs from the one already read (%s vs %s); keeping the first",
                name,
                page_number,
                field,
                old,
                new,
            )
        if old is not None:
            amounts[field] = old
        else:
            amounts[field] = new

    charges = list(first.charges)
    for charge in extra.charges:
        if charge not in charges:
            charges.append(charge)
    return InvoiceTotals(subtotal=amounts["subtotal"], total=amounts["total"], charges=charges)


def build_prompt(notes):
    """Return the prompt, with the vendor's notes at the end when it has any."""
    if not notes:
        return PROMPT
    return f"{PROMPT}\nNotes about these invoices:\n{notes}\n"


def extract_page(client, image_path, notes):
    """Send one page image to the AI and return what it read (a PageExtraction).

    The API makes the reply follow the PageExtraction schema.
    """
    image_data = base64.standard_b64encode(image_path.read_bytes()).decode()
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": image_data},
    }
    text = {"type": "text", "text": build_prompt(notes)}

    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        output_config={"effort": EFFORT},
        output_format=PageExtraction,
        # The image goes before the text, as the API docs recommend.
        messages=[{"role": "user", "content": [image, text]}],
    )
    if response.parsed_output is None:
        raise ValueError(f"No result for {image_path.name} (stop reason: {response.stop_reason})")

    logger.info(
        "%s: %d input / %d output tokens",
        image_path.name,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    return response.parsed_output


def join_continued_text(lines, page, name, page_number):
    """Add the text that ends the previous page's last row to that row.

    That text is at the top of the page, in continued_description or as a row
    with no codes and no numbers. Returns the page's rows without it.
    """
    page_lines = list(page.lines)
    pieces = []
    if page.continued_description is not None:
        text = page.continued_description.strip()
        if text:
            pieces.append(text)
    if page_lines and is_empty_row(page_lines[0]):
        text = page_lines.pop(0).description.strip()
        if text and text not in pieces:
            pieces.append(text)

    if pieces:
        piece = " ".join(pieces)
        if lines:
            lines[-1].description = f"{lines[-1].description} {piece}"
        else:
            logger.warning(
                "%s page %d starts with %r but there is no previous line to join it to",
                name,
                page_number,
                piece,
            )
    return page_lines


def drop_unpriced(page_lines, name, page_number):
    """Remove the rows with no price and no total: they are not items."""
    priced = []
    for line in page_lines:
        if line.price is not None or line.line_total is not None:
            priced.append(line)

    dropped = len(page_lines) - len(priced)
    if dropped:
        logger.warning(
            "%s page %d: dropped %d row(s) with no price and no total", name, page_number, dropped
        )
    return priced


def clean_line(line):
    """Save price_per in the standard way and fill a missing size from the description."""
    line.price_per = standard_price_per(line.price_per)
    if line.size is None:
        # For example a row split over two pages: its size is in the joined description.
        line.size = size_in(line.description)
    elif line.size.endswith("*"):
        # A trailing "*" is a mark printed in the description, not part of the size.
        size = line.size.rstrip("*").strip()
        if size == "":
            size = None
        line.size = size


def extract_invoice(client, page_images, invoice_number, output_path, notes):
    """Extract the pages of one invoice in order and save the final JSON.

    If any page fails, nothing is saved, because a JSON with missing pages would
    look like a complete invoice.
    """
    name = output_path.stem
    lines = []
    totals = None

    for page_number, image_path in enumerate(page_images, start=1):
        page = extract_page(client, image_path, notes)
        page_lines = join_continued_text(lines, page, name, page_number)
        page_lines = drop_unpriced(page_lines, name, page_number)
        for line in page_lines:
            # Copy the fields the AI read and add the page number.
            lines.append(ExtractedLine(**line.model_dump(), page=page_number))

        if page.totals is not None:
            if totals is None:
                totals = page.totals
            else:
                logger.info(
                    "%s page %d shows more of the totals block; joining it with the first",
                    name,
                    page_number,
                )
                totals = merge_totals(totals, page.totals, name, page_number)
        logger.info("Extracted %s page %d: %d line(s)", name, page_number, len(page_lines))

    for line in lines:
        clean_line(line)
    if totals is None:
        logger.warning("%s: no totals block found on any page", name)

    result = InvoiceExtraction(
        invoice_number=invoice_number,
        extracted_at=datetime.date.today(),
        lines=lines,
        totals=totals,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Saved %s: %d line(s)", output_path.name, len(lines))


def unique_pages(body_dir, pages):
    """Return the paths of the invoice's pages, leaving out exact copies.

    Two crops with the same content are the same page twice (a PDF repeated inside
    another one). Only used inside one invoice: identical pages of two invoices are real.
    """
    seen = set()
    kept = []
    for page in pages:
        path = body_dir / page
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            kept.append(path)
    return kept


def file_name(invoice_number):
    """Safe file name for an invoice number, which can hold characters such as '/'."""
    name = re.sub(r"[^\w-]+", "_", invoice_number).strip("_")
    if name == "":
        name = "invoice"
    return name


def extract_vendor(client, groups_path, body_dir, extracted_dir, notes):
    """Extract every invoice of a vendor's groups file. Returns how many were saved.

    A failing invoice is logged and skipped. Pages in review are never extracted.
    """
    groups = InvoiceGroups.model_validate_json(groups_path.read_text(encoding="utf-8"))
    if groups.review:
        logger.warning(
            "%d page(s) are in review and will not be extracted: %s",
            len(groups.review),
            ", ".join(groups.review),
        )

    extracted = 0
    used_names = {}
    for number, pages in groups.groups.items():
        name = file_name(number)
        if name in used_names:
            logger.error(
                "Skipping %s: its file name clashes with invoice %s", number, used_names[name]
            )
            continue
        used_names[name] = number

        try:
            page_images = unique_pages(body_dir, pages)
            repeated = len(pages) - len(page_images)
            if repeated:
                logger.info("%s: left out %d repeated page(s)", number, repeated)
            output_path = extracted_dir / f"{name}.json"
            extract_invoice(client, page_images, number, output_path, notes)
            extracted += 1
        except (OSError, anthropic.APIError, pydantic.ValidationError, ValueError):
            logger.exception("Skipping %s", number)
    return extracted


def main():
    """Extract every vendor, or only the one given: python extract.py <vendor>."""
    only_vendor = None
    if len(sys.argv) > 1:
        only_vendor = sys.argv[1]

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)

    vendor_dirs = vendor_folders(config.body_crops_dir, config.vendors_dir)
    if only_vendor:
        vendor_dirs = [d for d in vendor_dirs if d.name == only_vendor]
        if not vendor_dirs:
            raise SystemExit(f"No usable vendor folder named {only_vendor!r} in body_crops")
    if not vendor_dirs:
        logger.warning("No body crops in %s: run crop.py first", config.body_crops_dir.name)
        return

    client = anthropic.Anthropic(api_key=config.require_api_key())
    extracted = 0
    for vendor_dir in vendor_dirs:
        groups_path = config.groups_dir / f"{vendor_dir.name}.json"
        if not groups_path.is_file():
            logger.error("No groups file for %s: run group.py first", vendor_dir.name)
            continue

        notes = load_vendor(config.vendors_dir, vendor_dir.name).notes
        output_dir = config.extracted_dir / vendor_dir.name
        try:
            extracted += extract_vendor(client, groups_path, vendor_dir, output_dir, notes)
        except (OSError, pydantic.ValidationError):
            logger.exception("Skipping %s: the groups file is unusable", vendor_dir.name)

    logger.info(
        "Done: %d invoice(s) extracted. JSON files in %s", extracted, config.extracted_dir.name
    )


if __name__ == "__main__":
    main()
