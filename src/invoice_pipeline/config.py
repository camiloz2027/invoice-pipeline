"""Settings of the pipeline: the only module that reads the environment (.env)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# config.py is in src/invoice_pipeline/, so the project root is two folders up.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    """Raised when a setting is invalid or missing."""


@dataclass(frozen=True)
class Config:
    """The settings, which cannot be changed once loaded, and the data folders."""

    data_dir: Path
    log_level: str
    api_key: str | None = field(default=None, repr=False)  # repr=False keeps it out of the logs

    @property
    def raw_dir(self):
        return self.data_dir / "raw"

    @property
    def vendors_dir(self):
        return self.data_dir / "vendors"

    @property
    def images_dir(self):
        return self.data_dir / "interim" / "images"

    @property
    def body_crops_dir(self):
        return self.data_dir / "interim" / "body_crops"

    @property
    def id_crops_dir(self):
        return self.data_dir / "interim" / "id_crops"

    @property
    def groups_dir(self):
        return self.data_dir / "interim" / "groups"

    @property
    def extracted_dir(self):
        return self.data_dir / "interim" / "extracted"

    @property
    def logs_dir(self):
        return self.data_dir / "logs"

    @property
    def log_file(self):
        return self.logs_dir / "pipeline.log"

    def ensure_dirs(self):
        """Create the data folders that do not exist yet."""
        for folder in (
            self.raw_dir,
            self.vendors_dir,
            self.images_dir,
            self.body_crops_dir,
            self.id_crops_dir,
            self.groups_dir,
            self.extracted_dir,
            self.logs_dir,
        ):
            folder.mkdir(parents=True, exist_ok=True)

    def require_api_key(self):
        """Return the API key. Only the steps that call the AI need it."""
        if not self.api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        return self.api_key


def load_config():
    """Read the settings from the environment and the .env file (the environment wins).

    A relative DATA_DIR is taken from the project root, not from the folder the
    program is run from. An empty value counts as not set.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    data_dir = Path(os.getenv("DATA_DIR") or "data").expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    log_level = (os.getenv("LOG_LEVEL") or "INFO").upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"Invalid LOG_LEVEL {log_level!r}. Must be one of: {', '.join(VALID_LOG_LEVELS)}."
        )

    api_key = os.getenv("ANTHROPIC_API_KEY") or None
    return Config(data_dir=data_dir, log_level=log_level, api_key=api_key)
