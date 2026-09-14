from fastapi import APIRouter
from pydantic import BaseModel

from app import db
from app.config import resolve_ollama_timeout, resolve_ollama_url


class ConfigUpdate(BaseModel):
    ollama_base_url: str
    ollama_timeout: float


def get_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/config")
    def get_config():
        return {
            "ollama_base_url": resolve_ollama_url(db_path),
            "ollama_timeout": resolve_ollama_timeout(db_path),
        }

    @router.post("/api/config")
    def update_config(payload: ConfigUpdate):
        db.set_config(db_path, "ollama_base_url", payload.ollama_base_url)
        db.set_config(db_path, "ollama_timeout", str(payload.ollama_timeout))
        return {
            "ollama_base_url": payload.ollama_base_url,
            "ollama_timeout": payload.ollama_timeout,
        }

    return router
