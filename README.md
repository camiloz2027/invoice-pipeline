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
| 4 | Validation (line by line) | `validate.py` | Done |
| 5 | SQLite storage | `db.py` | Planned |
| 6 | Price analytics and notebook | `analytics.py` | Planned |
| 7 | CLI, demo, weekly run script, CI | `cli.py`, `demo.py` | Planned |

Each vendor has its own folder in `data/raw/` and its own settings file,
`data/vendors/<same name>.toml`, which holds the crop regions (see
[vendors.py](src/invoice_pipeline/vendors.py)). A folder with no settings file is skipped,
so a page is never cropped with another vendor's region. The file can also hold `notes` for the
AI (see below). Settings stay on your machine: `data/` is ignored by Git.

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
python src/invoice_pipeline/group.py           # data/interim/id_crops -> data/interim/groups (calls the API)
python src/invoice_pipeline/extract.py         # data/interim/body_crops + groups -> data/interim/extracted
python src/invoice_pipeline/validate.py        # checks data/interim/extracted, changes nothing
```

`pdf_to_images.py` turns rotated pages upright and then checks each page by reading it in all four
orientations. If another orientation reads clearly better, the page is turned and a warning is
logged. If none does (for example a nearly blank page), the page is listed in the log and the command
ends with an error, so check that page by hand. It takes a few seconds per page.

Running a stage again overwrites its previous output, so you can tune and repeat it freely.
The exception to "freely" is `extract.py`, which calls the API and costs a little every time.
To pay only for one vendor, run `python src/invoice_pipeline/extract.py <vendor>`.

### Choosing a vendor's crop regions

Run `pdf_to_images.py` first, then open the picker for that vendor:

```bash
python src/invoice_pipeline/pick_boxes.py <vendor>
```

It opens a window (matplotlib) with the vendor's pages. Drag on a page to draw a zone; drag its
handles to resize it or drag inside it to move it. Then press **b** to save it as the body zone
(the invoice lines and totals) or **i** to save it as the invoice-number zone. The left and right
arrow keys change the page and **q** closes the window. The zone is written to
`data/vendors/<vendor>.toml`, and saved zones are drawn on every page (blue = body, green =
invoice number) so you can check that they fit all of them.

A settings file is plain text, one zone per line, as fractions of the page
(left, top, right, bottom):

```toml
body_box = [0.025, 0.35, 0.9, 0.9]
id_box = [0.59, 0.15, 0.72, 0.17]
```

### Vendor notes

A settings file may also hold `notes`, a text written by hand that is added to the extraction
prompt for that vendor only. Use it when the AI keeps mixing up a vendor's columns, for example to
say which printed column is which field. Do not name the vendor in it: vendor names are never
sent to the AI. `pick_boxes.py` keeps the notes when it saves a zone.

```toml
notes = """
The Price column is the price before the discount and the Net Price column is the price after it.
price is the Net Price column. The size is at the end of the description.
"""
```

Logs go to the console and to `data/logs/pipeline.log`. Each line shows the time, the level and the
file that wrote it, so you can tell which stage is running, for example
`2026-10-28 22:24:39,069 | INFO     | crop.py           | Done: 16 invoice(s) cropped.`

## Grouping pages by invoice number

An invoice can span several PDFs, and a PDF can hold several invoices, so pages are grouped
by the invoice number printed on them. `group.py` sends the small number crops of a vendor
(`data/interim/id_crops/<vendor>/`) to Claude Sonnet 5.5 (`claude-sonnet-5-5`) in one request.
Each crop comes after a neutral label (`P01`, `P02`, ...): the AI never sees a file name or a
vendor name, and the code keeps the table from label to page. Pages whose crop is identical
are read once.

The result is `data/interim/groups/<vendor>.json`:

- `groups`: invoice number → its pages (paths relative to the crops folders).
- `review`: pages whose number was unreadable (`null`), had spaces or odd characters, was
  answered twice with different numbers, or was left out. They are never guessed or extracted.
- `labels`, `readings`, `model` and token counts, kept for auditing.

Running it again calls the API again and overwrites the file.

## Extraction output

`extract.py` reads the groups file written by `group.py` and extracts one invoice per invoice
number, even when its pages come from different PDFs. Inside an invoice, a page that is an
exact copy of another one (the same content) is left out. Pages in the review list are never
extracted. It sends each cropped page to Claude Haiku 5.5 (`claude-haiku-5-5`) with a prompt
and a response schema ([schemas.py](src/invoice_pipeline/schemas.py)), so the reply always has
the same shape. It saves one JSON per invoice in `data/interim/extracted/`. An example with
made-up values:

```json
{
  "invoice_number": "1234567",
  "extracted_at": "2026-01-15",
  "lines": [
    {
      "item_code": "10001",
      "upc": "012345678905",
      "description": "EXAMPLE TEA 6/18BAGS",
      "size": "6/18BAGS",
      "quantity": 5,
      "qty_ordered": 5,
      "qty_backordered": null,
      "list_price": 40.0,
      "price": 32.0,
      "price_per": "CS",
      "unit_price": 5.33,
      "line_total": 160.0,
      "page": 1
    }
  ],
  "totals": {
    "subtotal": 160.0,
    "total": 168.0,
    "charges": [{ "name": "GST 5.00 %", "amount": 8.0 }]
  }
}
```

- `lines` are the rows of the item table and `totals` holds the subtotal, the total and the
  fee or tax lines between them.
- The fields describe what a column means, not the header of one vendor, so the same shape
  fits every vendor. `price` is always the price actually paid, after any discount, for one
  selling unit (usually a case), so `quantity` x `price` = `line_total`. `quantity` is the
  quantity that makes that true (usually the shipped one); `qty_ordered` and
  `qty_backordered` are filled only when the invoice has those columns. `list_price` is the
  price before the discount and `unit_price` the price of one piece inside the case, both only
  when the invoice prints them. `price_per` is the unit `price` refers to: the
  ways of writing "case" (`CASE`, `PER CASE`, `CS`, `MC`) are all saved as `CS`, and any other
  unit (`ea`) as printed.
- `upc` is a barcode (digits only, at least 10) and `item_code` is the vendor's own code; both
  are text so leading zeros are kept. `size` is always filled, from its own column or from the
  description. If the AI leaves it empty, the code looks for it in the description.
- Values are copied as printed. An empty or unreadable cell is `null`, and a field the vendor
  does not print is `null`.
- Not extracted: lists of items that were not supplied, fee or deposit rows inside the item
  table, brand, tax and any column not listed above. A row with neither a price nor a total is
  dropped with a warning.
- A vendor's `notes` (see above) are added to the prompt when its invoices are extracted.
- The code, not the AI, sets the invoice number (the one read by `group.py`), `extracted_at`
  and `page` (the position of the page within its invoice). It also joins descriptions that run
  over two pages.
- If any page of an invoice fails, no JSON is saved for that invoice.
- The totals block can be split over two pages (the subtotal at the bottom of one, the fees and
  the total at the top of the next), and a page can show only part of it, even just the
  final total line. The AI fills only what the page shows and leaves the rest `null`.
  The code joins the blocks: each amount comes from the first
  block that has it, and the fees are put together in order. If two blocks disagree on an
  amount, the first is kept and a warning is logged.
- In a test with a 10-page invoice it took about a minute and cost about one US cent.

## Validation

The AI can misread a digit and still return a well-formed JSON, so `validate.py` checks that
the numbers of each line agree with each other, using the arithmetic the invoice itself
provides. It only reads the JSON files: it never changes them and makes no API calls. Money is
compared as `Decimal`, allowing a difference of one cent.

Every line is checked the same way, including lines that were not shipped, because all of
them are kept. For the example above:

| Rule | What it checks | Example |
|------|----------------|---------|
| E2 | Each line has its `quantity`; a line with a quantity above 0 also has `price` and `line_total` | `quantity` is not `null` |
| E3 | `quantity` × `price` = `line_total` (a line with quantity 0 must total 0) | 5 × 32.0 = 160.0 |
| E6 | No negative amounts, and `quantity` never above `qty_ordered` (only when the invoice has an ordered column) | 0 ≤ 5 ≤ 5 |
| E7 | The invoice has at least one line | 1 line |

- Every rule is an error: an invoice **fails** when it has at least one.
- All the errors are reported, not only the first one, each as a message with its line and
  page, for example `E3 3 x 24.5 = 73.5, but line_total is 99.0 (line 3, page 2)`.
- E7 flags an invoice that came out empty so someone looks at it. For example, when every row
  was a free item with no price, `extract.py` drops them and nothing is left.
- The rule numbers are kept from the full list of rules designed for the check, so the ones
  that are not active yet (totals, subtotal, fees) can come back with their own numbers.
- Not checked yet: the totals block (subtotal + fees = total), the sum of the lines against the
  subtotal, missing pages, and the description, size and UPC digits. Some vendors print a
  subtotal that already includes the deposit rows of the table, which are not extracted, so
  those checks need a rule that allows for it.
- For now a failed invoice is only reported in the log; nothing is moved or corrected. What to do with it comes with the CLI.

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
