import datetime

import pytest

from invoice_pipeline.schemas import ExtractedLine, InvoiceExtraction
from invoice_pipeline.validate import validate_all, validate_invoice


def make_line(**changes) -> ExtractedLine:
    """A valid invented line (3 x 24.50 = 73.50); each test changes only what it needs."""
    values = {
        "page": 1,
        "item_code": "ABC1",
        "upc": "0123456789012",
        "description": "TEST ITEM",
        "size": "6/300ML",
        "quantity": 3,
        "qty_ordered": None,
        "qty_backordered": None,
        "list_price": None,
        "price": 24.50,
        "price_per": "CS",
        "unit_price": None,
        "line_total": 73.50,
    }
    values.update(changes)
    return ExtractedLine(**values)


def make_invoice(*lines: ExtractedLine) -> InvoiceExtraction:
    return InvoiceExtraction(
        invoice_number="TEST001",
        extracted_at=datetime.date(2026, 1, 1),
        lines=list(lines),
        totals=None,
    )


def rules(report) -> list[str]:
    return [issue.rule for issue in report.issues]


def test_correct_line_passes():
    report = validate_invoice(make_invoice(make_line()))

    assert report.ok
    assert report.issues == []


@pytest.mark.parametrize(
    ("line_total", "should_pass"),
    [
        (73.50, True),  # exact
        (73.51, True),  # one cent, inside the tolerance
        (73.49, True),
        (73.52, False),  # two cents, outside
        (73.80, False),
    ],
)
def test_line_total_tolerance(line_total, should_pass):
    report = validate_invoice(make_invoice(make_line(line_total=line_total)))

    assert report.ok == should_pass
    if not should_pass:
        assert rules(report) == ["E3"]


def test_quantity_zero_with_zero_total_passes():
    report = validate_invoice(make_invoice(make_line(quantity=0, line_total=0.0)))

    assert report.ok


def test_quantity_zero_with_nonzero_total_fails():
    report = validate_invoice(make_invoice(make_line(quantity=0, line_total=5.0)))

    assert rules(report) == ["E3"]


def test_quantity_zero_without_price_or_total_passes():
    report = validate_invoice(make_invoice(make_line(quantity=0, price=None, line_total=None)))

    assert report.ok


def test_missing_quantity_fails():
    report = validate_invoice(make_invoice(make_line(quantity=None)))

    assert rules(report) == ["E2"]


@pytest.mark.parametrize("missing", ["price", "line_total"])
def test_shipped_line_without_price_or_total_fails(missing):
    report = validate_invoice(make_invoice(make_line(**{missing: None})))

    assert rules(report) == ["E2"]


@pytest.mark.parametrize("name", ["quantity", "price", "list_price", "unit_price", "line_total"])
def test_negative_amount_fails(name):
    report = validate_invoice(make_invoice(make_line(**{name: -1})))

    assert "E6" in rules(report)
    assert not report.ok


def test_quantity_above_ordered_fails():
    report = validate_invoice(make_invoice(make_line(quantity=3, qty_ordered=2)))

    assert rules(report) == ["E6"]


def test_quantity_not_above_ordered_passes():
    report = validate_invoice(make_invoice(make_line(quantity=3, qty_ordered=3)))

    assert report.ok


def test_error_points_to_the_right_line_and_page():
    good = make_line()
    bad = make_line(page=2, line_total=99.00)

    report = validate_invoice(make_invoice(good, good, bad))

    assert len(report.errors) == 1
    assert report.errors[0].line == 3
    assert report.errors[0].page == 2


def test_every_bad_line_is_reported():
    report = validate_invoice(
        make_invoice(make_line(line_total=1.0), make_line(), make_line(quantity=None))
    )

    assert rules(report) == ["E3", "E2"]


def test_invoice_without_lines_fails():
    report = validate_invoice(make_invoice())

    assert rules(report) == ["E7"]
    assert not report.ok


def test_validate_all_reads_every_json(tmp_path):
    (tmp_path / "good.json").write_text(make_invoice(make_line()).model_dump_json())
    (tmp_path / "bad.json").write_text(make_invoice(make_line(line_total=99.0)).model_dump_json())

    reports = validate_all(tmp_path)

    assert {path.name: report.ok for path, report in reports.items()} == {
        "bad.json": False,
        "good.json": True,
    }


def test_validate_all_with_missing_folder_returns_nothing(tmp_path):
    assert validate_all(tmp_path / "does-not-exist") == {}
