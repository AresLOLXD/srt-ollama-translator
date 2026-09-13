import os

from fastapi import APIRouter, HTTPException

from app import db
from app.ollama_client import OllamaClient

DEFAULT_OLLAMA_URL = "http://host.containers.internal:11434"


def get_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/models")
    async def list_models():
        default = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
        base_url = db.get_config(db_path, "ollama_base_url", default)
        client = OllamaClient(base_url)
        try:
            models = await client.list_models()
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"No se pudo conectar a Ollama: {exc}"
            ) from exc
        return {"models": models}

    return router
