"""Check that the extracted invoice data is internally consistent.

The AI can misread a digit and still return a well-formed JSON, so this stage
checks the arithmetic the invoice itself provides: each line, the sum of the
lines against the printed subtotal, and the subtotal plus fees against the
printed total. It only reads the data and never corrects it.

Rules (``E`` = error, the invoice fails; ``W`` = warning; ``I`` = information):

* E1 totals block present, with the total (the subtotal is optional).
* E2 every line has its shipped quantity; a line that shipped also has net price and total.
* E3 shipped x net price = line total (a line that did not ship must total 0).
* E4 the sum of the line totals = the printed subtotal (or, when the invoice prints no
  subtotal, the printed total minus the fees and taxes).
* E5 subtotal + fees and taxes = the printed total (only when a subtotal is printed).
* E6 no negative amounts and never more shipped than ordered.
* W1 a line without UPC.
* W2 a UPC repeated across lines (a duplicated page, maybe).
* W3 a net price above the list price.
* I1 lines not shipped against the "Unfulfilled supply" list (shown only, never judged).
* I2 the invoice prints no subtotal (shown only).

The description, the discount percentage and each fee on its own are not checked.
"""

from __future__ import annotations

import enum
import logging
from collections import Counter
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

    totals = invoice.totals
    if totals is None or totals.total is None:
        add(Severity.ERROR, "E1", "totals block or total is missing")

    lines_sum = Decimal(0)
    for number, line in enumerate(invoice.lines, start=1):
        where = {"page": line.page, "line": number}
        shipped = line.qty_shipped
        net = _money(line.net_price)
        total = _money(line.total)

        if shipped is None:
            add(Severity.ERROR, "E2", "qty_shipped is missing", **where)
        elif shipped > 0 and (net is None or total is None):
            add(Severity.ERROR, "E2", "net_price or total is missing on a shipped line", **where)
        elif shipped > 0:
            if _differ(shipped * net, total):
                add(
                    Severity.ERROR,
                    "E3",
                    f"{shipped} x {net} = {shipped * net}, but total is {total}",
                    **where,
                )
        elif total is not None and total != 0:
            add(Severity.ERROR, "E3", f"nothing shipped, but total is {total}", **where)

        lines_sum += total or Decimal(0)

        for name in ("price", "net_price", "total", "unit_price"):
            value = _money(getattr(line, name))
            if value is not None and value < 0:
                add(Severity.ERROR, "E6", f"{name} is negative ({value})", **where)
        if (
            line.qty_ordered is not None
            and shipped is not None
            and not 0 <= shipped <= line.qty_ordered
        ):
            add(
                Severity.ERROR,
                "E6",
                f"shipped {shipped} is outside 0..ordered {line.qty_ordered}",
                **where,
            )

        if not line.upc:
            add(Severity.WARNING, "W1", "line has no UPC", **where)
        price = _money(line.price)
        if net is not None and price is not None and net > price + TOLERANCE:
            add(Severity.WARNING, "W3", f"net_price {net} is above price {price}", **where)

    for upc, count in Counter(line.upc for line in invoice.lines if line.upc).items():
        if count > 1:
            add(Severity.WARNING, "W2", f"UPC {upc} appears on {count} lines")

    if totals is not None and totals.total is not None:
        total = _money(totals.total)
        fees = sum((_money(charge.amount) for charge in totals.charges), Decimal(0))
        if totals.subtotal is not None:
            subtotal = _money(totals.subtotal)
            if _differ(subtotal + fees, total):
                add(
                    Severity.ERROR,
                    "E5",
                    f"subtotal {subtotal} + fees {fees} = {subtotal + fees}, total is {total}",
                )
        else:
            # Short invoices print only the total, with no subtotal line.
            subtotal = total - fees
            add(
                Severity.INFO,
                "I2",
                "no subtotal printed; the lines are compared with the total minus the fees",
            )
        if _differ(lines_sum, subtotal):
            add(
                Severity.ERROR,
                "E4",
                f"lines add up to {lines_sum}, subtotal is {subtotal} "
                f"(difference {lines_sum - subtotal})",
            )

    not_shipped = {line.upc for line in invoice.lines if line.qty_shipped == 0 and line.upc}
    listed = {item.upc for item in invoice.unfulfilled}
    count = sum(1 for line in invoice.lines if line.qty_shipped == 0)
    add(
        Severity.INFO,
        "I1",
        f"{count} line(s) not shipped, {len(invoice.unfulfilled)} unfulfilled row(s), "
        f"{len(not_shipped ^ listed)} UPC(s) in only one of the two",
    )
    return report


def validate_all(extracted_dir: Path) -> dict[Path, ValidationReport]:
    """Validate every ``*.json`` invoice in ``extracted_dir``.

    Returns a mapping from each JSON file to its report.
    """
    if not extracted_dir.is_dir():
        logger.warning("Extracted folder not found: %s", extracted_dir)
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
    for vendor_dir in vendor_folders(config.extracted_dir):
        reports.update(validate_all(vendor_dir))
    failed = sum(1 for report in reports.values() if not report.ok)
    logger.info("Done: %d invoice(s) validated, %d failed.", len(reports), failed)


if __name__ == "__main__":
    # Re-import under the real module name so this module's logger falls under
    # the configured "invoice_pipeline" logger (when run directly, __name__ is
    # "__main__" and its messages would be lost).
    from invoice_pipeline.validate import main as _main

    _main()
