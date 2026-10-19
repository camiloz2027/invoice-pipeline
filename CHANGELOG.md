# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Initial project skeleton.
- Configuration module that reads `.env` and derives the `data/` paths.
- Logging setup with console and rotating file output.
- PDF to image conversion (one PNG per page).
- Fixed-region cropping of page images, using fractions of the page so it does not depend on the DPI.
- Grid overlay helper to choose the crop fractions by eye.
