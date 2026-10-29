# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Per-vendor settings: each vendor is a folder in `data/raw/` with a settings file `data/vendors/<vendor>.toml` that holds its crop regions (`body_box`, and `id_box` for the invoice number). A folder with no valid settings is logged and skipped, so a page is never cropped with another vendor's region.
- `pick_boxes.py`: choose a vendor's crop regions by dragging on its pages in the browser and clicking a button. It runs a local-only server protected by a random token and writes the settings file.
- Page orientation (`orientation.py`): scanned pages that arrive sideways or upside down are detected locally with Tesseract and turned upright before they are saved. A page whose orientation is unclear is logged and left as it is. Tesseract is now a system requirement, plus the `pytesseract` dependency.

- `crop.py` also crops each vendor's `id_box` (the invoice number) into `data/interim/id_crops/`, with the same relative path as the body crop.

- Orientation check (`orientation.py`): after turning a page upright, `pdf_to_images.py` reads it in all four orientations and keeps the one that reads clearly better. A page where none does is reported in the log and the command ends with an error, so a sideways page cannot go on to the AI unnoticed.
- `group.py`: reads the invoice number of every page from the `id_crops` with Claude Sonnet 5.5 and groups the pages by it in `data/interim/groups/<vendor>.json`. The AI only sees neutral labels, never file or vendor names; unreadable or odd numbers go to a review list.

- Per-vendor `notes` in the settings file: free text added to the extraction prompt for that vendor only. `vendors.py` accepts the key (`Vendor.notes`, `load_notes`) and `save_box` keeps it.
- `extract.py` accepts an optional vendor name (`extract.py <vendor>`) to extract only that vendor, so a new vendor can be tried without paying again for the others.
- `size` is always filled: when the AI leaves it empty, the code takes it from the description (for example a description split over two pages), and a trailing `*` is removed.

- Tests for `validate.py` (`tests/test_validate.py`), with invented invoices built from the real schema.

### Changed
- **Breaking:** `validate.py` works with the current schema and now checks each line only: E2 (quantity present, and price and total when there is a quantity), E3 (`quantity` x `price` = `line_total`, one cent of tolerance), E6 (no negative amounts, `quantity` not above `qty_ordered`) and E7 (the invoice has at least one line). The checks of the totals block, the subtotal, the UPC, the unfulfilled lists and the other warnings were removed for now.
- Log lines show the name of the file that wrote them (for example `extract.py`) instead of the package path, so it is easy to see which stage is running.
- **Breaking:** the extraction schema and prompt are now vendor-neutral. A line has `item_code`, `upc`, `description`, `size`, `quantity`, `qty_ordered`, `qty_backordered`, `list_price`, `price`, `price_per`, `unit_price` and `line_total`. `price` is the price actually paid after any discount, so `quantity` x `price` = `line_total`.
- The extraction prompt has two invented example rows and rules for the barcode (`upc`: digits only, at least 10), the quantity and the discounted price. The model effort is now `high`, the token limit 16000 and the tokens used by each page are logged.
- **Breaking:** `extract.py` extracts one invoice per invoice number, from the groups file written by `group.py`, instead of one invoice per PDF. Run `group.py` first. Pages that are exact copies of another page of the same invoice are left out, and pages in the review list are not extracted.
- The body crops folder `data/interim/crops` is now `data/interim/body_crops` (`Config.crops_dir` is now `body_crops_dir`).
- **Breaking:** PDFs must now live in `data/raw/<vendor>/`, and every stage reads and writes `data/interim/<stage>/<vendor>/`.
- `crop.py` no longer has a default crop box; it must be given one.
- `pdf_to_images.py` converts every vendor folder, even one with no settings yet, so its crop regions can be chosen on its pages.

### Removed
- **Breaking:** the lists of items that were not supplied (`unfulfilled`), `other_columns`, `discount_pct` and `piece_price` are no longer extracted, and the code that read such lists again was removed. A row with neither a price nor a total is dropped with a warning.
- `grid_overlay.py` and the `data/interim/grids/` folder: `pick_boxes.py` replaces them for choosing crop regions.

### Fixed
- Page orientation is detected at two sizes and the more confident answer is kept. Pages with dense small text, such as a table of many columns, were shrunk too much, so Tesseract answered with little confidence or the wrong way and the page was left sideways or turned the wrong way.
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
