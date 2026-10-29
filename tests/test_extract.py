import pytest

from invoice_pipeline.extract import _standard_price_per


@pytest.mark.parametrize(
    "printed",
    ["CS", "cs", "CASE", "Case", "PER CASE", "per  case", " PER CASE ", "MC"],
)
def test_ways_of_writing_case_become_cs(printed):
    assert _standard_price_per(printed) == "CS"


@pytest.mark.parametrize("printed", [None, "", "   "])
def test_empty_stays_none(printed):
    assert _standard_price_per(printed) is None


@pytest.mark.parametrize(
    ("printed", "expected"), [("ea", "ea"), (" EA ", "EA"), ("per  kg", "per kg")]
)
def test_other_units_are_kept_as_printed(printed, expected):
    assert _standard_price_per(printed) == expected
