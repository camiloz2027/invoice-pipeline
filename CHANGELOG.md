# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed
- Extraction reads a totals block that a page shows only in part, such as a page with just the `** TOTAL **` line. Before, the AI returned no totals for those pages, and in one case copied a fee into the total.
- Extraction no longer reads the `B/O` (back order) column as the shipped quantity. The prompt and the field descriptions now explain the three quantity columns; before, rows that shipped nothing could come out as shipped, which validation caught.

## [0.1.0] - 2026-10-26

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
- Validation accepts invoices that print only a total and no subtotal, with a note when that happens.

### Fixed
- Extraction joins a totals block that is split over two pages instead of keeping only the first half, which left the invoice without a total.

[Unreleased]: https://github.com/camiloz2027/invoice-pipeline/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/camiloz2027/invoice-pipeline/releases/tag/v0.1.0
