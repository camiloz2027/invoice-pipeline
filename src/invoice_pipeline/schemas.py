"""Data shapes of the grouping and the extraction stages.

Grouping (``group.py``) reads the invoice number of every page:

* ``NumbersReading`` is what the AI returns, one ``PageNumber`` per label.
* ``InvoiceGroups`` is the JSON saved per vendor: the pages of each invoice number.

Extraction (``extract.py``) has two layers:

* ``PageExtraction`` is what the AI returns for ONE page image. Its JSON schema
  is sent to the API as the required output format, so the response always has
  this exact shape. The ``description`` of each field is part of that schema and
  tells the model what the field is.
* ``InvoiceExtraction`` is the final JSON saved per invoice. The code adds what
  the AI must not decide: the invoice number, the extraction date and the page.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class PageNumber(BaseModel):
    """The invoice number read from one number crop."""

    label: str = Field(description="The label given before the image, copied exactly.")
    invoice_number: str | None = Field(
        description=(
            "The invoice number exactly as printed, with no spaces. Null if it cannot be read "
            "with certainty."
        )
    )


class NumbersReading(BaseModel):
    """What the AI returns for a vendor: one entry per label."""

    pages: list[PageNumber] = Field(
        description="One entry for every label, in the order the images were given."
    )


class InvoiceGroups(BaseModel):
    """The JSON saved per vendor by ``group.py``. Paths are relative to the crops folders."""

    model: str
    created_at: datetime.datetime
    input_tokens: int
    output_tokens: int
    # Invoice number -> the pages of that invoice, sorted by PDF folder and page.
    groups: dict[str, list[str]]
    # Pages whose number was unreadable, ambiguous or odd: nobody extracts them.
    review: list[str]
    # Label -> the pages that share that image (exact duplicates are read once).
    labels: dict[str, list[str]]
    # What the AI answered, kept for auditing.
    readings: list[PageNumber]


class InvoiceLine(BaseModel):
    """One row of the item table, as read from the page image."""

    description: str = Field(
        description=(
            "Text of the 'Description' column only, with its lines joined by a single space. "
            "Never include the 'Brand' column, and do not complete text that is cut off at the "
            "edge of the cell."
        )
    )
    upc: str | None = Field(
        description=(
            "Value of the 'UPC / CUP' column (or a PLU code), as text so leading zeros are kept. "
            "Do not use the 'Code' column. Null if the cell is empty."
        )
    )
    qty_ordered: int | None = Field(
        description="'Qty Ord.' column, the quantity ordered. Null if not visible."
    )
    qty_shipped: int | None = Field(
        description=(
            "'Shipped' column only, the quantity shipped now. Never the 'B/O' (back order) "
            "column next to it: a row with Shipped 0 and B/O 1 has qty_shipped 0. "
            "Null if not visible."
        )
    )
    price: float | None = Field(
        description="'Price' column, before any discount. Null if not visible."
    )
    net_price: float | None = Field(
        description="'Net Price' column, the price after the discount. Null if not visible."
    )
    discount_pct: float | None = Field(
        description="'Disc %' column as a percentage (20 means 20%). Null if the cell is empty."
    )
    unit_price: float | None = Field(
        description="'Unit Price' column, the price per single unit. Null if not visible."
    )
    total: float | None = Field(
        description="'Extended Price' column. A value written '.00' is 0. Null if not visible."
    )


class UnfulfilledItem(BaseModel):
    """One row of the 'Unfulfilled supply' list: a product that could not be shipped."""

    upc: str = Field(description="First column of the row, as text so leading zeros are kept.")
    description: str = Field(
        description="The item description, joining its text columns with a single space."
    )
    quantity: int | None = Field(description="The 'Quantity' column. Null if not visible.")
    reason: str | None = Field(
        description="The 'Reason' column, copied exactly as printed. Null if not visible."
    )


class Charge(BaseModel):
    """One line of the totals block between the subtotal and the total (fees, taxes)."""

    name: str = Field(description="The label exactly as printed, for example 'GST 5.00 %'.")
    amount: float


class InvoiceTotals(BaseModel):
    """The totals block at the end of the invoice; a page can show only part of it."""

    subtotal: float | None = Field(
        description=(
            "The amount next to the label 'SUBTOTAL'. Null if that label is not on this page "
            "(some invoices have no subtotal)."
        )
    )
    total: float | None = Field(
        description=(
            "The amount next to the label '** TOTAL **'. Null if that label is not on this "
            "page. Never a fee, a tax or the last amount shown."
        )
    )
    charges: list[Charge] = Field(
        description=(
            "Every fee or tax line shown on this page, in order, without the subtotal and the "
            "total. Empty if there are none."
        )
    )


class PageExtraction(BaseModel):
    """What the AI returns for one page."""

    lines: list[InvoiceLine] = Field(
        description="Every row of the item table on the page, in order from top to bottom."
    )
    continued_description: str | None = Field(
        description=(
            "Text at the very top of the page that continues the description of the previous "
            "page's last row (a line with no quantities, UPC or prices). Null if there is none."
        )
    )
    unfulfilled: list[UnfulfilledItem] = Field(
        description="Rows of the 'Unfulfilled supply' list on this page. Empty if there are none."
    )
    totals: InvoiceTotals | None = Field(
        description=(
            "The invoice totals block as far as this page shows it. Not null as soon as any of "
            "SUBTOTAL, a fee or tax line or '** TOTAL **' is visible, even if it is the only "
            "one. Null only if none of them is on this page."
        )
    )


class ExtractedLine(InvoiceLine):
    """A line in the final JSON: the AI's fields plus the page, set by the code."""

    page: int


class ExtractedUnfulfilled(UnfulfilledItem):
    """An unfulfilled item in the final JSON: the AI's fields plus the page."""

    page: int


class InvoiceExtraction(BaseModel):
    """The final JSON saved for one invoice."""

    # None when the PDF file name has no number.
    invoice_number: str | None
    # The day the invoice was processed, not the date printed on the invoice.
    extracted_at: datetime.date
    lines: list[ExtractedLine]
    unfulfilled: list[ExtractedUnfulfilled]
    # None when no page showed a totals block.
    totals: InvoiceTotals | None
