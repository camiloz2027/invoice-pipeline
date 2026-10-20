"""Data shapes of the extraction stage.

Two layers:

* ``PageExtraction`` is what the AI returns for ONE page image. Its JSON schema
  is sent to the API as the required output format, so the response always has
  this exact shape. The ``description`` of each field is part of that schema and
  tells the model which column of the invoice to read.
* ``InvoiceExtraction`` is the final JSON saved per invoice. The code adds what
  the AI must not decide: the invoice number, the extraction date and the page.
"""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class InvoiceLine(BaseModel):
    """One row of the item table, as read from the page image."""

    description: str = Field(
        description="Text of the 'Description' column, on every line of the row."
    )
    upc: str | None = Field(
        description=(
            "Value of the 'UPC / CUP' column (or a PLU code), as text so leading zeros are kept. "
            "Do not use the 'Code' column. Null if the cell is empty."
        )
    )
    qty_ordered: int = Field(description="'Qty Ord.' column.")
    qty_shipped: int = Field(description="'Shipped' column.")
    price: float = Field(description="'Price' column, before any discount.")
    net_price: float = Field(description="'Net Price' column, the price after the discount.")
    discount_pct: float | None = Field(
        description="'Disc %' column as a percentage (20 means 20%). Null if the cell is empty."
    )
    unit_price: float = Field(description="'Unit Price' column, the price per single unit.")
    total: float = Field(description="'Extended Price' column. A value written '.00' is 0.")


class PageExtraction(BaseModel):
    """What the AI returns for one page."""

    lines: list[InvoiceLine] = Field(
        description="Every row of the item table on the page, in order from top to bottom."
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
