from fastapi import APIRouter, HTTPException

from app.config import resolve_ollama_url
from app.ollama_client import OllamaClient


def get_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/models")
    async def list_models():
        base_url = resolve_ollama_url(db_path)
        client = OllamaClient(base_url)
        try:
            models = await client.list_models()
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"No se pudo conectar a Ollama: {exc}"
            ) from exc
        return {"models": models}

    return router
