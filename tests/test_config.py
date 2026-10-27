import dataclasses
from pathlib import Path

import pytest

from invoice_pipeline import config as config_module
from invoice_pipeline.config import PROJECT_ROOT, ConfigError, load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Isolate every test from the real .env file and the user's environment."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: None)
    for name in ("DATA_DIR", "LOG_LEVEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_defaults():
    config = load_config()

    assert config.data_dir == PROJECT_ROOT / "data"
    assert config.log_level == "INFO"
    assert config.api_key is None


def test_absolute_data_dir_drives_derived_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    config = load_config()

    assert config.raw_dir == tmp_path / "raw"
    assert config.images_dir == tmp_path / "interim" / "images"
    assert config.body_crops_dir == tmp_path / "interim" / "body_crops"
    assert config.id_crops_dir == tmp_path / "interim" / "id_crops"
    assert config.processed_dir == tmp_path / "processed"
    assert config.db_path == tmp_path / "processed" / "invoices.db"
    assert config.failed_dir == tmp_path / "failed"
    assert config.logs_dir == tmp_path / "logs"
    assert config.log_file == tmp_path / "logs" / "pipeline.log"


def test_relative_data_dir_resolves_against_project_root(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "somewhere/else")

    assert load_config().data_dir == PROJECT_ROOT / "somewhere" / "else"


def test_relative_data_dir_ignores_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", "data")

    assert load_config().data_dir == PROJECT_ROOT / "data"


def test_log_level_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")

    assert load_config().log_level == "DEBUG"


def test_invalid_log_level_raises(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "banana")

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_config()


def test_empty_values_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("DATA_DIR", "")
    monkeypatch.setenv("LOG_LEVEL", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    config = load_config()

    assert config.data_dir == PROJECT_ROOT / "data"
    assert config.log_level == "INFO"
    assert config.api_key is None


def test_missing_api_key_is_allowed_until_required():
    config = load_config()

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        config.require_api_key()


def test_require_api_key_returns_the_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")

    assert load_config().require_api_key() == "sk-test-123"


def test_api_key_is_hidden_from_repr(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")

    assert "sk-test-123" not in repr(load_config())


def test_ensure_dirs_creates_the_data_tree(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    config = load_config()

    config.ensure_dirs()
    config.ensure_dirs()  # calling it twice must not fail

    for directory in (
        config.raw_dir,
        config.images_dir,
        config.body_crops_dir,
        config.id_crops_dir,
        config.processed_dir,
        config.failed_dir,
        config.logs_dir,
    ):
        assert Path(directory).is_dir()


def test_config_is_immutable():
    config = load_config()

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.log_level = "DEBUG"
