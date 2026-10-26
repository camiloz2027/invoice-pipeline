# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Initial project skeleton.
- Configuration module that reads `.env` and derives the `data/` paths.
- Logging setup with console and rotating file output.
- PDF to image conversion (one PNG per page), rendered with `pypdfium2`.
- Fixed-region cropping of page images, using fractions of the page so it does not depend on the DPI.
- Grid overlay helper to choose the crop fractions by eye.
- AI extraction of the invoice data from the cropped pages with Claude Haiku 5.5, one call per page, saved as one JSON per invoice in `data/interim/extracted/`.
- Output schemas (Pydantic) for the AI reply and for the final JSON: item lines, the "Unfulfilled supply" list and the invoice totals.
- Code-side handling of what the AI must not decide: invoice number from the file name, extraction date (`extracted_at`), page numbers, joining descriptions that run over two pages, and reading again a page that continues the "Unfulfilled supply" list.
- `anthropic` and `pydantic` dependencies.
- Validation of the extracted data (`validate.py`): per-line arithmetic, line totals against the subtotal, subtotal plus fees against the total, and sanity checks on quantities and amounts. It reports every finding as an error, a warning or a note and never changes the data.
