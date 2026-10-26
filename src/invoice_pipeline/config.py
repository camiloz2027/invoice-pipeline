"""Central configuration: the only module that reads the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# parents[0] = invoice_pipeline/, parents[1] = src/, parents[2] = the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_DATA_DIR = "data"
DEFAULT_LOG_LEVEL = "INFO"


class ConfigError(Exception):
    """Raised when the configuration is invalid or incomplete."""


@dataclass(frozen=True)
class Config:
    """Immutable application settings plus the paths derived from them."""

    data_dir: Path
    log_level: str
    # repr=False keeps the secret out of logs and tracebacks.
    api_key: str | None = field(default=None, repr=False)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "interim" / "images"

    @property
    def crops_dir(self) -> Path:
        return self.data_dir / "interim" / "crops"

    @property
    def grids_dir(self) -> Path:
        return self.data_dir / "interim" / "grids"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "interim" / "extracted"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def db_path(self) -> Path:
        return self.processed_dir / "invoices.db"

    @property
    def failed_dir(self) -> Path:
        return self.data_dir / "failed"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "pipeline.log"

    def ensure_dirs(self) -> None:
        """Create every data directory that does not exist yet."""
        for directory in (
            self.raw_dir,
            self.images_dir,
            self.crops_dir,
            self.grids_dir,
            self.extracted_dir,
            self.processed_dir,
            self.failed_dir,
            self.logs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def require_api_key(self) -> str:
        """Return the API key, or fail with a clear message if it is missing.

        The key is optional when loading the config, so validation, the
        database and the tests work without it; only extraction needs it.
        """
        if not self.api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        return self.api_key


def load_config() -> Config:
    """Build a Config from the .env file and the process environment.

    Variables already set in the environment take precedence over .env.
    A relative DATA_DIR is resolved against the project root, so the result
    does not depend on the directory the program is launched from.
    """
    load_dotenv(PROJECT_ROOT / ".env")

    data_dir = Path(os.getenv("DATA_DIR") or DEFAULT_DATA_DIR).expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    log_level = (os.getenv("LOG_LEVEL") or DEFAULT_LOG_LEVEL).upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"Invalid LOG_LEVEL {log_level!r}. Must be one of: {', '.join(VALID_LOG_LEVELS)}."
        )

    # An empty value (ANTHROPIC_API_KEY=) counts as "not set".
    api_key = os.getenv("ANTHROPIC_API_KEY") or None

    return Config(data_dir=data_dir, log_level=log_level, api_key=api_key)
