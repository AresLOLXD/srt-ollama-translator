from fastapi.testclient import TestClient
from app.main import app


def test_health_check_returns_ok():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


import importlib
import os


def test_app_mounts_all_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))

    import app.main as main_module

    importlib.reload(main_module)

    with TestClient(main_module.app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/config").status_code == 200
        assert client.get("/api/jobs").json() == {"jobs": []}

    assert os.path.exists(os.environ["DB_PATH"])
