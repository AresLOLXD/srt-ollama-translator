import os

from app import db

DEFAULT_OLLAMA_URL = "http://host.containers.internal:11434"


def resolve_ollama_url(db_path: str) -> str:
    """Resolve the Ollama base URL to use.

    Precedence: value stored in the config table, falling back to the
    OLLAMA_BASE_URL environment variable, falling back to DEFAULT_OLLAMA_URL.
    """
    default = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
    return db.get_config(db_path, "ollama_base_url", default)
