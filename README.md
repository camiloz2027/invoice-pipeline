# invoice-pipeline

Python pipeline that turns PDF invoices into structured data:
PDF → images → crop → AI extraction (vision) → validation → SQLite → price analytics.

> **Status:** work in progress, built step by step. See the [CHANGELOG](CHANGELOG.md).

## Pipeline

| # | Stage | Module | Status |
|---|-------|--------|--------|
| 1 | PDF → one PNG per page | `pdf_to_images.py` | Done |
| 2 | Crop each page to the region with the invoice data | `crop.py` | Done |
| 3 | AI extraction (vision) | `extract.py` | Done |
| 4 | Validation | `validate.py` | Planned |
| 5 | SQLite storage | `db.py` | Planned |
| 6 | Price analytics and notebook | `analytics.py` | Planned |
| 7 | CLI, demo, weekly run script, CI | `cli.py`, `demo.py` | Planned |

The first version targets the invoice layout of a single vendor, so the crop region
is fixed (`CROP_BOX` in [crop.py](src/invoice_pipeline/crop.py)).

The invoice date is not read from the invoice. Each extraction result carries an
`extracted_at` date, the day it was processed, which is set by the code and not by the AI.
This assumes invoices are processed the same day they arrive. If the real invoice date is
needed, the pipeline can be changed to send the full page image to the AI; do that according
to the processing requirements and security standards that apply to your data.

## Requirements

- Python 3.11+

## Installation (development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in your values
```

The virtual environment (`.venv`) holds the project's libraries. Activate it
(`source .venv/bin/activate`) in every new terminal, or call `.venv/bin/python` directly.
Running with another Python fails with `ModuleNotFoundError`.

## Configuration

Settings are read from `.env` (or from the environment, which takes precedence) by
[config.py](src/invoice_pipeline/config.py):

| Variable | Default | Meaning |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | none | API key, needed by the AI extraction stage (`extract.py`). The other stages work without it. |
| `DATA_DIR` | `data` | Where all local data lives. A relative path is resolved from the project root. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |

## Usage

Put your PDF invoices in `data/raw/`, then run each stage (with the virtual environment
active):

```bash
python src/invoice_pipeline/pdf_to_images.py   # data/raw -> data/interim/images
python src/invoice_pipeline/crop.py            # data/interim/images -> data/interim/crops
python src/invoice_pipeline/extract.py         # data/interim/crops -> data/interim/extracted
```

Running a stage again overwrites its previous output, so you can tune and repeat it freely.
The exception to "freely" is `extract.py`, which calls the API and costs a little every time.

To choose the crop region for a new layout, draw a percentage grid over a page image and
read the (left, top, right, bottom) fractions off it:

```bash
python src/invoice_pipeline/grid_overlay.py data/interim/images/<invoice>/page_001.png
```

The result is saved in `data/interim/grids/`.

Logs go to the console and to `data/logs/pipeline.log`.

## Extraction output

`extract.py` sends each cropped page to Claude Haiku 5.5 (`claude-haiku-5-5`) with a prompt
and a response schema ([schemas.py](src/invoice_pipeline/schemas.py)), so the reply always has
the same shape. It saves one JSON per invoice in `data/interim/extracted/`. An example with
made-up values:

```json
{
  "invoice_number": "1234567",
  "extracted_at": "2026-01-15",
  "lines": [
    {
      "description": "EXAMPLE TEA 6/18BAGS",
      "upc": "012345678905",
      "qty_ordered": 1,
      "qty_shipped": 1,
      "price": 40.0,
      "net_price": 32.0,
      "discount_pct": 20.0,
      "unit_price": 5.33,
      "total": 32.0,
      "page": 1
    }
  ],
  "unfulfilled": [
    {
      "upc": "012345678912",
      "description": "EXAMPLE COOKIES 12/200G",
      "quantity": 1,
      "reason": "Facility Out of Stock",
      "page": 9
    }
  ],
  "totals": {
    "subtotal": 32.0,
    "total": 33.6,
    "charges": [{ "name": "GST 5.00 %", "amount": 1.6 }]
  }
}
```

- `lines` are the rows of the item table, `unfulfilled` is the "Unfulfilled supply" list
  (products that could not be shipped, with the reason; empty when the invoice has none) and
  `totals` holds the subtotal, the total and the fee or tax lines between them.
- Values are copied as printed. An empty or unreadable cell is `null`, and `upc` is text so
  leading zeros are kept.
- The code, not the AI, sets the invoice number (the longest run of digits in the PDF file
  name), `extracted_at` and `page`. It also joins descriptions that run over two pages and
  reads again a page that continues the "Unfulfilled supply" list.
- If any page of an invoice fails, no JSON is saved for that invoice.
- In a test with a 10-page invoice it took about a minute and cost about one US cent.

## Development

```bash
pytest            # run the tests
ruff check .      # lint
ruff format .     # format
```

## Privacy

Real invoices live only in `data/` (ignored by Git), and `.env` is never committed.
Files in `examples/` are fake invoices.

## License

MIT
