import os

from app import db

DEFAULT_OLLAMA_URL = "http://host.containers.internal:11434"
DEFAULT_OLLAMA_TIMEOUT = 120.0


def resolve_ollama_url(db_path: str) -> str:
    """Resolve the Ollama base URL to use.

    Precedence: value stored in the config table, falling back to the
    OLLAMA_BASE_URL environment variable, falling back to DEFAULT_OLLAMA_URL.
    """
    default = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
    return db.get_config(db_path, "ollama_base_url", default)


def resolve_ollama_timeout(db_path: str) -> float:
    """Resolve the Ollama request timeout (in seconds) to use.

    Precedence: value stored in the config table, falling back to the
    OLLAMA_TIMEOUT environment variable, falling back to
    DEFAULT_OLLAMA_TIMEOUT. An unparseable value at any source falls back to
    DEFAULT_OLLAMA_TIMEOUT rather than crashing the worker.
    """
    env_default = os.environ.get("OLLAMA_TIMEOUT")
    stored = db.get_config(db_path, "ollama_timeout")
    value = stored if stored is not None else env_default
    if value is None:
        return DEFAULT_OLLAMA_TIMEOUT
    try:
        return float(value)
    except ValueError:
        return DEFAULT_OLLAMA_TIMEOUT
