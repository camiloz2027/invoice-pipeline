"""Per-vendor settings, one TOML file per vendor: data/vendors/<vendor>.toml.

    body_box = [0.025, 0.35, 0.9, 0.9]
    id_box = [0.59, 0.15, 0.72, 0.17]
    notes = \"\"\"Text about how this vendor's invoices are laid out.\"\"\"

Each box is (left, top, right, bottom) as fractions of the page, from 0 to 1.
pick_boxes.py writes the boxes. The notes are optional, written by hand and added
to the extraction prompt, so they must never name the vendor.
"""

import logging
import tomllib
from dataclasses import dataclass

logger = logging.getLogger("invoice_pipeline.vendors")

BOX_KEYS = ("body_box", "id_box")


class VendorError(Exception):
    """Raised when a vendor's settings are missing or invalid."""


@dataclass
class Vendor:
    body_box: tuple  # the invoice lines and totals
    id_box: tuple | None = None  # only the invoice number
    notes: str | None = None  # text for the extraction prompt


def settings_path(vendors_dir, name):
    """Path of the vendor's settings file. The name must be a plain folder name."""
    if name == "" or name.startswith(".") or "/" in name:
        raise VendorError(f"Invalid vendor name: {name!r}")
    return vendors_dir / f"{name}.toml"


def check_box(box, file_name, key):
    """Return the box as 4 floats, or raise VendorError if it is not inside the page."""
    if not isinstance(box, list | tuple) or len(box) != 4:
        raise VendorError(f"{file_name}: {key} must be a list of 4 numbers")
    for value in box:
        # In Python True and False also count as numbers, so they are refused first.
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise VendorError(f"{file_name}: {key} must be a list of 4 numbers")

    left, top, right, bottom = box
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise VendorError(
            f"{file_name}: {key} {list(box)} must have 0 <= left < right <= 1 "
            "and 0 <= top < bottom <= 1"
        )
    return (float(left), float(top), float(right), float(bottom))


def read_settings(vendors_dir, name):
    """Read and check the vendor's settings file. Returns {} if there is no file yet."""
    path = settings_path(vendors_dir, name)
    if not path.is_file():
        return {}

    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise VendorError(f"Could not read {path.name}: {error}") from error

    for key in data:
        if key not in BOX_KEYS and key != "notes":
            raise VendorError(f"{path.name}: unknown key {key}")
    for key in BOX_KEYS:
        if key in data:
            data[key] = check_box(data[key], path.name, key)

    notes = data.get("notes", "")
    if not isinstance(notes, str):
        raise VendorError(f"{path.name}: notes must be text")
    notes = notes.strip()
    if notes == "":
        notes = None
    data["notes"] = notes
    return data


def load_boxes(vendors_dir, name):
    """Return the saved boxes of the vendor, for example {"body_box": (...)}."""
    data = read_settings(vendors_dir, name)
    boxes = {}
    for key in BOX_KEYS:
        if key in data:
            boxes[key] = data[key]
    return boxes


def load_vendor(vendors_dir, name):
    """Load the vendor's settings; fail if there is no file or no body_box."""
    path = settings_path(vendors_dir, name)
    if not path.is_file():
        raise VendorError(f"No settings file {path.name}. Create it with pick_boxes.py.")

    data = read_settings(vendors_dir, name)
    if "body_box" not in data:
        raise VendorError(f"{path.name}: body_box is missing. Set it with pick_boxes.py.")
    return Vendor(body_box=data["body_box"], id_box=data.get("id_box"), notes=data["notes"])


def save_box(vendors_dir, name, key, box):
    """Save one box of the vendor, keeping the other box and the notes. Returns the boxes."""
    if key not in BOX_KEYS:
        raise VendorError(f"Unknown box {key!r}: expected body_box or id_box")
    path = settings_path(vendors_dir, name)

    data = read_settings(vendors_dir, name)
    box = check_box(box, path.name, key)
    data[key] = tuple(round(value, 4) for value in box)

    lines = []
    for box_key in BOX_KEYS:
        if box_key in data:
            lines.append(f"{box_key} = {list(data[box_key])}")
    if data.get("notes"):
        # In a TOML multi-line string only backslashes and triple quotes need escaping.
        notes = data["notes"].replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        lines.append(f'notes = """\n{notes}\n"""')

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return load_boxes(vendors_dir, name)


def vendor_folders(parent, vendors_dir):
    """Return the subfolders of parent that have usable settings, sorted by name.

    A folder with no valid settings is logged and skipped, so its pages are never
    cropped with another vendor's settings.
    """
    if not parent.is_dir():
        logger.warning("Input folder does not exist: %s", parent.name)
        return []

    folders = []
    for folder in sorted(parent.iterdir()):
        if not folder.is_dir():
            continue
        try:
            load_vendor(vendors_dir, folder.name)
        except VendorError as error:
            logger.error("Skipping %s: %s", folder.name, error)
        else:
            folders.append(folder)
    return folders
