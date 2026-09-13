import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.routes import config as config_routes
from app.routes import models as models_routes


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "jobs.db")
    db.init_db(path)
    return path


def test_get_config_returns_default_when_unset(db_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    app = FastAPI()
    app.include_router(config_routes.get_router(db_path))
    client = TestClient(app)

    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {"ollama_base_url": "http://host.containers.internal:11434"}


def test_post_config_persists_value(db_path):
    app = FastAPI()
    app.include_router(config_routes.get_router(db_path))
    client = TestClient(app)

    response = client.post("/api/config", json={"ollama_base_url": "http://custom:11434"})
    assert response.status_code == 200
    assert db.get_config(db_path, "ollama_base_url") == "http://custom:11434"

    response = client.get("/api/config")
    assert response.json() == {"ollama_base_url": "http://custom:11434"}


def test_list_models_proxies_ollama(db_path, monkeypatch):
    db.set_config(db_path, "ollama_base_url", "http://fake-ollama:11434")

    def fake_init(self, base_url, timeout=120.0, transport=None):
        self.base_url = base_url

    async def fake_list_models(self):
        return ["llama3.1", "mistral"]

    monkeypatch.setattr("app.ollama_client.OllamaClient.__init__", fake_init)
    monkeypatch.setattr("app.ollama_client.OllamaClient.list_models", fake_list_models)

    app = FastAPI()
    app.include_router(models_routes.get_router(db_path))
    client = TestClient(app)

    response = client.get("/api/models")
    assert response.status_code == 200
    assert response.json() == {"models": ["llama3.1", "mistral"]}


def test_list_models_returns_502_when_ollama_unreachable(db_path, monkeypatch):
    async def failing_list_models(self):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("app.ollama_client.OllamaClient.list_models", failing_list_models)

    app = FastAPI()
    app.include_router(models_routes.get_router(db_path))
    client = TestClient(app)

    response = client.get("/api/models")
    assert response.status_code == 502
