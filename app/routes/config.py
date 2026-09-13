import os

from fastapi import APIRouter
from pydantic import BaseModel

from app import db

DEFAULT_OLLAMA_URL = "http://host.containers.internal:11434"


class ConfigUpdate(BaseModel):
    ollama_base_url: str


def get_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/config")
    def get_config():
        default = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
        return {"ollama_base_url": db.get_config(db_path, "ollama_base_url", default)}

    @router.post("/api/config")
    def update_config(payload: ConfigUpdate):
        db.set_config(db_path, "ollama_base_url", payload.ollama_base_url)
        return {"ollama_base_url": payload.ollama_base_url}

    return router
