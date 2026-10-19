# invoice-pipeline

Python pipeline that turns PDF invoices into structured data:
PDF → images → AI extraction (vision) → validation → SQLite → price analytics.

> **Status:** work in progress, built step by step. See the [CHANGELOG](CHANGELOG.md).

## Requirements

- Python 3.11+

## Installation (development)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in your values
```

## Privacy

Real invoices live only in `data/` (ignored by Git). Files in `examples/` are fake invoices.

## License

MIT
