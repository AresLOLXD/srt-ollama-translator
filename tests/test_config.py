import pytest

from app import db
from app.config import DEFAULT_OLLAMA_TIMEOUT, resolve_ollama_timeout


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "jobs.db")
    db.init_db(path)
    return path


def test_resolve_ollama_timeout_returns_default_when_unset(db_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)
    assert resolve_ollama_timeout(db_path) == DEFAULT_OLLAMA_TIMEOUT


def test_resolve_ollama_timeout_uses_env_var_when_config_unset(db_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT", "300")
    assert resolve_ollama_timeout(db_path) == 300.0


def test_resolve_ollama_timeout_prefers_db_value_over_env_var(db_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT", "300")
    db.set_config(db_path, "ollama_timeout", "600")
    assert resolve_ollama_timeout(db_path) == 600.0


def test_resolve_ollama_timeout_falls_back_to_default_on_invalid_value(db_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_TIMEOUT", raising=False)
    db.set_config(db_path, "ollama_timeout", "not-a-number")
    assert resolve_ollama_timeout(db_path) == DEFAULT_OLLAMA_TIMEOUT
