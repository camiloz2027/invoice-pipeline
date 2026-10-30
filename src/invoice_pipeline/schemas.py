"""The shapes of the data: what the AI returns and the JSON files the pipeline saves.

group.py:   the AI returns NumbersReading; the code saves InvoiceGroups.
extract.py: the AI returns PageExtraction for each page; the code saves InvoiceExtraction.

The models the AI returns are sent to the API as the required response format,
so their docstrings and field descriptions are instructions for the AI: change
them only on purpose.
"""

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
    """One row of the item table, as read from the page image.

    Fields are described by what a column means, not by the header of one vendor.
    """

    item_code: str | None = Field(
        description=(
            "The vendor's own code for the item (a column such as 'Code', 'Item #' or "
            "'Item No'), as text. It has letters or fewer than 10 digits; a barcode is the "
            "upc, never this. Null if the invoice has none."
        )
    )
    upc: str | None = Field(
        description=(
            "The barcode: only digits, at least 10 of them (a column such as 'UPC', 'CUP' or "
            "'Product Number'), as text so leading zeros are kept. Null if the cell is empty, "
            "reads 'N/A' or the invoice has no barcode."
        )
    )
    description: str = Field(
        description=(
            "The item description, with its lines joined by a single space. Do not complete "
            "text that is cut off at the edge of the cell, and do not add other columns to it."
        )
    )
    size: str | None = Field(
        description=(
            "The pack or size of the item (for example '6/1.75L' or '12/170G'). Take it from "
            "its own column; if it is split over two columns, a count and a size, write "
            "count/size. If there is no such column, take it from the description. Null if it "
            "is printed nowhere."
        )
    )
    quantity: int | None = Field(
        description=(
            "The quantity that makes quantity x price = line_total, usually the shipped one. "
            "A row that shipped 0 has quantity 0. A single quantity column is this one. "
            "Null if not visible."
        )
    )
    qty_ordered: int | None = Field(
        description="The quantity ordered, only if the invoice has a column for it. Otherwise null."
    )
    qty_backordered: int | None = Field(
        description=(
            "The quantity ordered that was NOT shipped and will come later (a column such as "
            "'B/O' or 'A Venir'). Null if the invoice has no such column or it is empty."
        )
    )
    list_price: float | None = Field(
        description=(
            "The price before any discount, only when the invoice prints it next to a "
            "different price. Null otherwise."
        )
    )
    price: float | None = Field(
        description=(
            "The price actually paid, after any discount, for one selling unit (usually a "
            "case): quantity x price = line_total. Decide the column by that arithmetic, not "
            "by its header. Null if not visible."
        )
    )
    price_per: str | None = Field(
        description=(
            "The unit that price refers to, as the invoice prints it (for example 'CS' or "
            "'ea'). Null if the invoice prints none."
        )
    )
    unit_price: float | None = Field(
        description=(
            "The price actually paid for one piece inside the case, only when the invoice "
            "prints it in addition to price. Null otherwise."
        )
    )
    line_total: float | None = Field(
        description=(
            "The amount of the whole row, usually the rightmost number. A value written "
            "'.00' is 0. Null if not visible."
        )
    )


class Charge(BaseModel):
    """One line of the totals block between the subtotal and the total (fees, taxes)."""

    name: str = Field(description="The label exactly as printed, for example 'GST 5.00 %'.")
    amount: float


class InvoiceTotals(BaseModel):
    """The totals block at the end of the invoice; a page can show only part of it."""

    subtotal: float | None = Field(
        description=(
            "The amount next to the subtotal label. Null if that label is not on this page "
            "(some invoices have no subtotal)."
        )
    )
    total: float | None = Field(
        description=(
            "The amount next to the final total label. Null if that label is not on this "
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
    totals: InvoiceTotals | None = Field(
        description=(
            "The invoice totals block as far as this page shows it. Not null as soon as any of "
            "the subtotal, a fee or tax line or the final total is visible, even if it is the only "
            "one. Null only if none of them is on this page."
        )
    )


class ExtractedLine(InvoiceLine):
    """A line in the final JSON: the AI's fields plus the page, set by the code."""

    page: int


class InvoiceExtraction(BaseModel):
    """The final JSON saved for one invoice."""

    # None when the PDF file name has no number.
    invoice_number: str | None
    # The day the invoice was processed, not the date printed on the invoice.
    extracted_at: datetime.date
    lines: list[ExtractedLine]
    # None when no page showed a totals block.
    totals: InvoiceTotals | None
