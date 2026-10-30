"""Check that every extracted invoice is consistent. It only reads the data.

The AI can misread a digit and still return a valid JSON, so each line is checked
with its own arithmetic. Every error makes the invoice fail:

    E2  every line has a quantity; a line with quantity above 0 also has price and line_total
    E3  quantity x price = line_total, with one cent of tolerance (quantity 0 must total 0)
    E6  no negative amounts, and quantity never above qty_ordered
    E7  the invoice has at least one line

The totals, the subtotal, the description, the size and the UPC are not checked yet.
"""

import logging
from decimal import Decimal

from invoice_pipeline.config import load_config
from invoice_pipeline.logging_setup import setup_logging
from invoice_pipeline.schemas import InvoiceExtraction
from invoice_pipeline.vendors import vendor_folders

logger = logging.getLogger("invoice_pipeline.validate")

TOLERANCE = Decimal("0.01")  # invoices round each line to the cent
AMOUNT_FIELDS = ("quantity", "price", "list_price", "unit_price", "line_total")


def money(value):
    """Turn a float into a Decimal through str, so 0.1 stays exactly 0.1."""
    if value is None:
        return None
    return Decimal(str(value))


def check_line(line):
    """Return the errors of one line as messages, such as "E2 quantity is missing"."""
    errors = []
    quantity = line.quantity
    price = money(line.price)
    line_total = money(line.line_total)

    if quantity is None:
        errors.append("E2 quantity is missing")
    elif quantity > 0:
        if price is None or line_total is None:
            errors.append("E2 price or line_total is missing on a line with quantity")
        elif abs(quantity * price - line_total) > TOLERANCE:
            errors.append(
                f"E3 {quantity} x {price} = {quantity * price}, but line_total is {line_total}"
            )
    elif line_total is not None and line_total != 0:
        errors.append(f"E3 quantity is 0, but line_total is {line_total}")

    for field in AMOUNT_FIELDS:
        value = getattr(line, field)
        if value is not None and value < 0:
            errors.append(f"E6 {field} is negative ({value})")
    if line.qty_ordered is not None and quantity is not None and quantity > line.qty_ordered:
        errors.append(f"E6 quantity {quantity} is above qty_ordered {line.qty_ordered}")
    return errors


def validate_invoice(invoice):
    """Return every error of one invoice as a message. An empty list means it is OK."""
    if not invoice.lines:
        return ["E7 the invoice has no lines"]

    errors = []
    for number, line in enumerate(invoice.lines, start=1):
        for error in check_line(line):
            errors.append(f"{error} (line {number}, page {line.page})")
    return errors


def validate_all(extracted_dir):
    """Validate every JSON invoice in extracted_dir. Returns {json path: errors}."""
    results = {}
    if not extracted_dir.is_dir():
        logger.warning("Extracted folder not found: %s", extracted_dir.name)
        return results

    for json_path in sorted(extracted_dir.glob("*.json")):
        invoice = InvoiceExtraction.model_validate_json(json_path.read_text(encoding="utf-8"))
        errors = validate_invoice(invoice)
        results[json_path] = errors

        for error in errors:
            logger.error("%s: %s", json_path.name, error)
        if errors:
            logger.info("Validated %s: FAILED (%d error(s))", json_path.name, len(errors))
        else:
            logger.info("Validated %s: OK", json_path.name)
    return results


def main():
    config = load_config()
    config.ensure_dirs()
    setup_logging(config)

    vendor_dirs = vendor_folders(config.extracted_dir, config.vendors_dir)
    if not vendor_dirs:
        logger.warning(
            "No extracted invoices in %s: run extract.py first", config.extracted_dir.name
        )
        return

    results = {}
    for vendor_dir in vendor_dirs:
        results.update(validate_all(vendor_dir))

    failed = 0
    for errors in results.values():
        if errors:
            failed += 1
    logger.info("Done: %d invoice(s) validated, %d failed", len(results), failed)


if __name__ == "__main__":
    main()
