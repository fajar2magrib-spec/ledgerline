"""Test fixtures: every test gets a brand-new SQLite file and a fresh configuration."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))  # tests must never write into the project folder
    monkeypatch.setenv("LEDGERLINE_ENV", "development")
    monkeypatch.setenv("AUTH_SECRET", "x" * 48)
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    from ledgerline import config, db, security
    monkeypatch.setattr(security, "PBKDF2_ITERATIONS", 1000)  # fast hashing in tests
    config.get_config.cache_clear(); db.get_engine.cache_clear(); db.get_write_engine.cache_clear()
    db.init_schema()
    yield tmp_path
    db.get_engine().dispose()
    config.get_config.cache_clear(); db.get_engine.cache_clear(); db.get_write_engine.cache_clear()
