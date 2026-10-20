# invoice-pipeline

Python pipeline that turns PDF invoices into structured data:
PDF → images → crop → AI extraction (vision) → validation → SQLite → price analytics.

> **Status:** work in progress, built step by step. See the [CHANGELOG](CHANGELOG.md).

## Pipeline

| # | Stage | Module | Status |
|---|-------|--------|--------|
| 1 | PDF → one PNG per page | `pdf_to_images.py` | Done |
| 2 | Crop each page to the region with the invoice data | `crop.py` | Done |
| 3 | AI extraction (vision) | `extract.py` | Planned |
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
| `ANTHROPIC_API_KEY` | none | API key. Only needed for the AI extraction stage. |
| `DATA_DIR` | `data` | Where all local data lives. A relative path is resolved from the project root. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |

## Usage

Put your PDF invoices in `data/raw/`, then run each stage (with the virtual environment
active):

```bash
python src/invoice_pipeline/pdf_to_images.py   # data/raw -> data/interim/images
python src/invoice_pipeline/crop.py            # data/interim/images -> data/interim/crops
```

Running a stage again overwrites its previous output, so you can tune and repeat it freely.

To choose the crop region for a new layout, draw a percentage grid over a page image and
read the (left, top, right, bottom) fractions off it:

```bash
python src/invoice_pipeline/grid_overlay.py data/interim/images/<invoice>/page_001.png
```

The result is saved in `data/interim/grids/`.

Logs go to the console and to `data/logs/pipeline.log`.

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
