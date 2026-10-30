"""Check that the extracted invoice data is internally consistent.

The AI can misread a digit and still return a well-formed JSON, so this stage
checks the arithmetic the invoice itself provides on each line. It only reads
the data and never corrects it.

Rules (``E`` = error, the invoice fails; ``W`` = warning; ``I`` = information):

* E2 every line has its quantity; a line with a quantity above 0 also has price and line total.
* E3 quantity x price = line total (a line with quantity 0 must total 0).
* E6 no negative amounts, and never more shipped than ordered (only when the invoice has
  an ordered column).
* E7 the invoice has at least one line (an empty one needs a look, for example when every row
  was a free item with no price and was dropped).

Nothing else is checked about the invoice as a whole yet (totals, subtotal, missing pages)
nor about the description, the size or the UPC.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from invoice_pipeline.schemas import InvoiceExtraction

logger = logging.getLogger(__name__)

# Invoices round each line to the cent, so allow a one-cent difference.
TOLERANCE = Decimal("0.01")


class Severity(enum.StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Issue:
    """One finding. ``line`` is the 1-based position among the invoice lines."""

    severity: Severity
    rule: str
    message: str
    page: int | None = None
    line: int | None = None

    def __str__(self) -> str:
        where = ""
        if self.line is not None:
            where = f" (line {self.line}, page {self.page})"
        return f"{self.rule} {self.message}{where}"


@dataclass
class ValidationReport:
    """Every finding for one invoice."""

    invoice_number: str | None
    issues: list[Issue] = field(default_factory=list)

    def _of(self, severity: Severity) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == severity]

    @property
    def errors(self) -> list[Issue]:
        return self._of(Severity.ERROR)

    @property
    def warnings(self) -> list[Issue]:
        return self._of(Severity.WARNING)

    @property
    def ok(self) -> bool:
        """True when nothing blocks the invoice (warnings and notes do not)."""
        return not self.errors


def _money(value: float | None) -> Decimal | None:
    """Convert through ``str`` so 0.1 stays 0.1 and not 0.1000000000000000055."""
    return None if value is None else Decimal(str(value))


def _differ(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) > TOLERANCE


def validate_invoice(invoice: InvoiceExtraction) -> ValidationReport:
    """Run every rule on one extracted invoice and return all the findings."""
    report = ValidationReport(invoice_number=invoice.invoice_number)

    def add(severity: Severity, rule: str, message: str, page=None, line=None) -> None:
        report.issues.append(Issue(severity, rule, message, page, line))

    if not invoice.lines:
        add(Severity.ERROR, "E7", "the invoice has no lines")

    for number, line in enumerate(invoice.lines, start=1):
        where = {"page": line.page, "line": number}
        quantity = line.quantity
        price = _money(line.price)
        line_total = _money(line.line_total)

        if quantity is None:
            add(Severity.ERROR, "E2", "quantity is missing", **where)
        elif quantity > 0 and (price is None or line_total is None):
            add(
                Severity.ERROR,
                "E2",
                "price or line_total is missing on a line with quantity",
                **where,
            )
        elif quantity > 0:
            if _differ(quantity * price, line_total):
                add(
                    Severity.ERROR,
                    "E3",
                    f"{quantity} x {price} = {quantity * price}, but line_total is {line_total}",
                    **where,
                )
        elif line_total is not None and line_total != 0:
            add(Severity.ERROR, "E3", f"quantity is 0, but line_total is {line_total}", **where)

        for name in ("quantity", "price", "list_price", "unit_price", "line_total"):
            value = getattr(line, name)
            if value is not None and value < 0:
                add(Severity.ERROR, "E6", f"{name} is negative ({value})", **where)
        if line.qty_ordered is not None and quantity is not None and quantity > line.qty_ordered:
            add(
                Severity.ERROR,
                "E6",
                f"quantity {quantity} is above qty_ordered {line.qty_ordered}",
                **where,
            )
    return report


def validate_all(extracted_dir: Path) -> dict[Path, ValidationReport]:
    """Validate every ``*.json`` invoice in ``extracted_dir``.

    Returns a mapping from each JSON file to its report.
    """
    if not extracted_dir.is_dir():
        logger.warning("Extracted folder not found: %s", extracted_dir.name)
        return {}
    reports: dict[Path, ValidationReport] = {}
    for json_path in sorted(extracted_dir.glob("*.json")):
        invoice = InvoiceExtraction.model_validate_json(json_path.read_text(encoding="utf-8"))
        report = validate_invoice(invoice)
        reports[json_path] = report
        for issue in report.issues:
            level = {
                Severity.ERROR: logging.ERROR,
                Severity.WARNING: logging.WARNING,
                Severity.INFO: logging.INFO,
            }[issue.severity]
            logger.log(level, "%s: %s", json_path.name, issue)
        logger.info(
            "Validated %s: %s (%d error(s), %d warning(s))",
            json_path.name,
            "OK" if report.ok else "FAILED",
            len(report.errors),
            len(report.warnings),
        )
    return reports


def main() -> None:
    """Validate every extracted invoice of every known vendor (manual run)."""
    from invoice_pipeline.config import load_config
    from invoice_pipeline.logging_setup import setup_logging
    from invoice_pipeline.vendors import vendor_folders

    config = load_config()
    config.ensure_dirs()
    setup_logging(config)
    reports: dict[Path, ValidationReport] = {}
    for vendor_dir in vendor_folders(config.extracted_dir, config.vendors_dir):
        reports.update(validate_all(vendor_dir))
    failed = sum(1 for report in reports.values() if not report.ok)
    logger.info("Done: %d invoice(s) validated, %d failed.", len(reports), failed)


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.validate import main as _main

    _main()
