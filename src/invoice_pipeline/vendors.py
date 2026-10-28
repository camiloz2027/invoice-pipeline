"""Per-vendor settings, stored as one TOML file per vendor.

A vendor is a folder under ``data/raw/``. Its settings live in
``data/vendors/<folder name>.toml``. ``pick_boxes.py`` writes the two boxes:

    body_box = [0.025, 0.35, 0.9, 0.9]
    id_box = [0.59, 0.15, 0.72, 0.17]

Each box is (left, top, right, bottom) as fractions of the page, from 0 to 1.
The file may also hold ``notes``, a text written by hand (a TOML multi-line string)
about how that vendor's invoices are laid out. It is added to the extraction prompt,
so it must not name the vendor: vendor names stay local, never sent to the AI.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# (left, top, right, bottom) as fractions of the page, from 0 to 1.
# Fractions do not depend on the DPI used to render the pages.
Box = tuple[float, float, float, float]

# The boxes a settings file may hold, in the order they are written.
BOX_KEYS = ("body_box", "id_box")
# The only other key: free text that is added to the extraction prompt.
NOTES_KEY = "notes"


class VendorError(Exception):
    """Raised when a vendor's settings are missing or invalid."""


@dataclass(frozen=True)
class Vendor:
    """What differs from one vendor's invoices to another's."""

    # Region with the invoice lines and totals; the rest of the page is cut away.
    body_box: Box
    # Small region with only the invoice number (none until it is chosen).
    id_box: Box | None = None
    # Text about this vendor's invoices for the extraction prompt (none if not written).
    notes: str | None = None


def validate_box(box: Box) -> None:
    """Raise ValueError unless ``box`` is (left, top, right, bottom) inside the page."""
    left, top, right, bottom = box
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError(
            f"Invalid crop box {box}: expected 0 <= left < right <= 1 and 0 <= top < bottom <= 1"
        )


def settings_path(vendors_dir: Path, name: str) -> Path:
    """Path of the settings file of vendor ``name`` (a plain folder name, nothing else)."""
    if not name or name != Path(name).name or name.startswith("."):
        raise VendorError(f"Invalid vendor name: {name!r}")
    return Path(vendors_dir) / f"{name}.toml"


def _to_box(file_name: str, key: str, value: object) -> Box:
    numbers = isinstance(value, list) and all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in value
    )
    if not (numbers and len(value) == 4):
        raise VendorError(f"{file_name}: {key} must be a list of 4 numbers")
    box = tuple(float(v) for v in value)
    try:
        validate_box(box)
    except ValueError as error:
        raise VendorError(f"{file_name}: {key}: {error}") from error
    return box


def _read_settings(vendors_dir: Path, name: str) -> dict:
    """The raw content of the settings file of ``name``; no file yet means no settings."""
    path = settings_path(vendors_dir, name)
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise VendorError(f"Could not read {path.name}: {error}") from error

    unknown = sorted(set(data) - set(BOX_KEYS) - {NOTES_KEY})
    if unknown:
        raise VendorError(f"{path.name}: unknown key(s) {', '.join(unknown)}")
    if NOTES_KEY in data and not isinstance(data[NOTES_KEY], str):
        raise VendorError(f"{path.name}: {NOTES_KEY} must be text")
    return data


def load_boxes(vendors_dir: Path, name: str) -> dict[str, Box]:
    """Read the boxes saved for ``name``; no file yet means no boxes."""
    path = settings_path(vendors_dir, name)
    data = _read_settings(vendors_dir, name)
    return {key: _to_box(path.name, key, data[key]) for key in BOX_KEYS if key in data}


def load_notes(vendors_dir: Path, name: str) -> str | None:
    """Read the notes saved for ``name``; None when there are none or they are blank."""
    notes = _read_settings(vendors_dir, name).get(NOTES_KEY, "").strip()
    return notes or None


def load_vendor(vendors_dir: Path, name: str) -> Vendor:
    """Load the settings of vendor ``name``; fail if there is no usable body_box."""
    path = settings_path(vendors_dir, name)
    if not path.is_file():
        raise VendorError(
            f"No settings for vendor {name!r}: expected {path}. Create them with pick_boxes.py."
        )
    boxes = load_boxes(vendors_dir, name)
    if "body_box" not in boxes:
        raise VendorError(f"{path.name}: body_box is missing. Set it with pick_boxes.py.")
    return Vendor(
        body_box=boxes["body_box"],
        id_box=boxes.get("id_box"),
        notes=load_notes(vendors_dir, name),
    )


def save_box(vendors_dir: Path, name: str, key: str, box: Box) -> dict[str, Box]:
    """Save one box of vendor ``name``, keeping the other box and the notes, and return all its boxes."""
    if key not in BOX_KEYS:
        raise VendorError(f"Unknown box {key!r}: expected one of {', '.join(BOX_KEYS)}")
    try:
        validate_box(box)
    except ValueError as error:
        raise VendorError(str(error)) from error

    boxes = load_boxes(vendors_dir, name)
    boxes[key] = tuple(round(float(v), 4) for v in box)
    notes = load_notes(vendors_dir, name)

    path = settings_path(vendors_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k} = {list(boxes[k])}" for k in BOX_KEYS if k in boxes]
    if notes:
        # A TOML multi-line string: only backslashes and triple quotes need escaping.
        escaped = notes.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        lines.append(f'{NOTES_KEY} = """\n{escaped}\n"""')
    # Write to a temporary file first so a failure never leaves half a settings file.
    temporary = path.with_suffix(".toml.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return boxes


def vendor_folders(parent: Path, vendors_dir: Path) -> list[Path]:
    """Return the subfolders of ``parent`` that have usable settings, sorted by name.

    A subfolder with no valid settings is logged as an error and skipped, so its
    pages are never cropped with another vendor's settings.
    """
    parent = Path(parent)
    if not parent.is_dir():
        logger.warning("Input folder does not exist: %s", parent)
        return []

    folders = []
    for folder in sorted(p for p in parent.iterdir() if p.is_dir()):
        try:
            load_vendor(vendors_dir, folder.name)
        except VendorError as error:
            logger.error("Skipping folder %r in %s: %s", folder.name, parent, error)
        else:
            folders.append(folder)
    return folders
