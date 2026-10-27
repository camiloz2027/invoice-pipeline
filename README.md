# invoice-pipeline

Python pipeline that turns PDF invoices into structured data:
PDF → images → crop → AI extraction (vision) → validation → SQLite → price analytics.

> **Status:** work in progress, built step by step. See the [CHANGELOG](CHANGELOG.md).

## Pipeline

| # | Stage | Module | Status |
|---|-------|--------|--------|
| 1 | PDF → one PNG per page, rotated pages turned upright | `pdf_to_images.py`, `orientation.py` | Done |
| 2 | Crop each page to the region with the invoice data | `crop.py` | Done |
| 3 | AI extraction (vision) | `extract.py` | Done |
| 4 | Validation | `validate.py` | Done |
| 5 | SQLite storage | `db.py` | Planned |
| 6 | Price analytics and notebook | `analytics.py` | Planned |
| 7 | CLI, demo, weekly run script, CI | `cli.py`, `demo.py` | Planned |

Each vendor has its own folder in `data/raw/` and its own settings file,
`data/vendors/<same name>.toml`, which holds the crop regions (see
[vendors.py](src/invoice_pipeline/vendors.py)). A folder with no settings file is skipped,
so a page is never cropped with another vendor's region. Settings stay on your machine:
`data/` is ignored by Git.

The invoice date is not read from the invoice. Each extraction result carries an
`extracted_at` date, the day it was processed, which is set by the code and not by the AI.
This assumes invoices are processed the same day they arrive. If the real invoice date is
needed, the pipeline can be changed to send the full page image to the AI; do that according
to the processing requirements and security standards that apply to your data.

## Requirements

- Python 3.11+
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) installed on the system (for example
  `brew install tesseract` on macOS). It runs locally and is used only to detect page orientation.

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

Put your PDF invoices in `data/raw/<vendor>/`, then run each stage (with the virtual environment
active):

```bash
python src/invoice_pipeline/pdf_to_images.py   # data/raw -> data/interim/images
python src/invoice_pipeline/crop.py            # data/interim/images -> data/interim/body_crops and id_crops
python src/invoice_pipeline/extract.py         # data/interim/body_crops -> data/interim/extracted
python src/invoice_pipeline/validate.py        # checks data/interim/extracted, changes nothing
```

Running a stage again overwrites its previous output, so you can tune and repeat it freely.
The exception to "freely" is `extract.py`, which calls the API and costs a little every time.

### Choosing a vendor's crop regions

Run `pdf_to_images.py` first, then open the picker for that vendor:

```bash
python src/invoice_pipeline/pick_boxes.py <vendor>
```

It opens a page in your browser with the vendor's pages. Drag on a page to select a zone; drag
its handles to resize it or drag inside it to move it. Then click **Save as body zone** (the
invoice lines and totals) or **Save as invoice-number zone**. A saved zone can be loaded back
with the **Edit saved ...** buttons, and **Select whole page** starts from the full page.
The zone is written to `data/vendors/<vendor>.toml`, and saved zones are drawn on every page so
you can check that they fit all of them. Click **Finish** to stop the picker (or press Ctrl+C in
the terminal).

The picker runs a small server that only your own computer can reach, protected by a random
access token, and loads nothing from the internet. The pages shown are full pages, so keep an
eye on browser extensions that can read page contents.

A settings file is plain text, one zone per line, as fractions of the page
(left, top, right, bottom):

```toml
body_box = [0.025, 0.35, 0.9, 0.9]
id_box = [0.59, 0.15, 0.72, 0.17]
```

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
- The totals block can be split over two pages (the subtotal at the bottom of one, the fees and
  the total at the top of the next), and a page can show only part of it, even just the
  `** TOTAL **` line. The AI fills only what the page shows and leaves the rest `null`.
  The code joins the blocks: each amount comes from the first
  block that has it, and the fees are put together in order. If two blocks disagree on an
  amount, the first is kept and a warning is logged.
- In a test with a 10-page invoice it took about a minute and cost about one US cent.

## Validation

The AI can misread a digit and still return a well-formed JSON, so `validate.py` checks that
the numbers agree with each other, using the arithmetic the invoice itself provides. It only
reads the JSON files: it never changes them and makes no API calls. Money is compared as
`Decimal`, allowing a difference of one cent.

Every line is checked the same way, including lines that were not shipped, because all of
them are kept. For the example above:

| Rule | Severity | What it checks | Example |
|------|----------|----------------|---------|
| E1 | error | The totals block has a total (the subtotal is optional: short invoices print only a total) | `total` is present |
| E2 | error | Each line has its shipped quantity; a line that shipped also has `net_price` and `total` | `qty_shipped` is not `null` |
| E3 | error | Shipped quantity × net price = line total (a line that did not ship must total 0) | 1 × 32.0 = 32.0 |
| E4 | error | The line totals add up to the printed subtotal, or to the printed total minus the fees and taxes when the invoice prints no subtotal | 32.0 = 32.0 |
| E5 | error | Subtotal + fees and taxes = printed total (only when a subtotal is printed) | 32.0 + 1.6 = 33.6 |
| E6 | error | No negative amounts, and never more shipped than ordered | 0 ≤ 1 ≤ 1 |
| W1 | warning | A line without a UPC | `upc` is not empty |
| W2 | warning | The same UPC on more than one line (a duplicated page, maybe) | each UPC appears once |
| W3 | warning | A net price above the list price | 32.0 ≤ 40.0 |
| I1 | info | Lines not shipped compared with the "Unfulfilled supply" list; shown only, never judged | 0 lines, 1 row |
| I2 | info | The invoice prints no subtotal; shown only | the example has one |

- An invoice **fails** when it has at least one error. Warnings and notes do not fail it.
- All the findings are reported, not only the first one. A single misread value often breaks
  more than one rule (a wrong subtotal breaks E4 and E5), and the combination points to the
  cause. The E4 message includes the difference.
- The unfulfilled list is shown but its detail is not checked: the priority is the data that
  was billed.
- Not checked: the description, the discount percentage (the invoice prints it rounded to a
  whole number) and each fee or tax on its own.
- For now a failed invoice is only reported in the log and in the returned report; nothing is
  moved or corrected. What to do with it comes with the CLI.

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
