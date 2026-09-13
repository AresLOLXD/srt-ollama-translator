from fastapi import APIRouter
from pydantic import BaseModel

from app import db
from app.config import resolve_ollama_url


class ConfigUpdate(BaseModel):
    ollama_base_url: str


def get_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/config")
    def get_config():
        return {"ollama_base_url": resolve_ollama_url(db_path)}

    @router.post("/api/config")
    def update_config(payload: ConfigUpdate):
        db.set_config(db_path, "ollama_base_url", payload.ollama_base_url)
        return {"ollama_base_url": payload.ollama_base_url}

    return router
