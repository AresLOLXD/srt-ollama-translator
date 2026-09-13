import io
import os
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.routes.jobs import get_router


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "jobs.db")
    storage_dir = str(tmp_path / "storage")
    db.init_db(db_path)
    app = FastAPI()
    app.include_router(get_router(db_path, storage_dir))
    return TestClient(app), db_path, storage_dir


def _make_zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_create_job_uploads_zip_and_creates_job_files(client):
    test_client, db_path, _storage_dir = client
    srt_content = (
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n"
    )
    zip_bytes = _make_zip_bytes({"episode1.srt": srt_content})

    response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )

    assert response.status_code == 200
    job_id = response.json()["id"]
    job = db.get_job(db_path, job_id)
    assert job["original_zip_name"] == "movie.zip"
    assert job["total_files"] == 1
    files = db.get_job_files(db_path, job_id)
    assert files[0]["filename"] == "episode1.srt"
    assert files[0]["total_blocks"] == 1


def test_create_job_rejects_zip_without_srt_files(client):
    test_client, _db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"readme.txt": "no subtitles"})

    response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )

    assert response.status_code == 400


def test_create_job_rejects_zip_slip_attack(client):
    test_client, _db_path, _storage_dir = client
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../../evil.srt", "1\n00:00:00,000 --> 00:00:01,000\nHi\n")
    zip_bytes = buffer.getvalue()

    response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )

    assert response.status_code == 400


def test_list_and_get_job(client):
    test_client, _db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes(
        {"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"}
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    list_response = test_client.get("/api/jobs")
    assert any(j["id"] == job_id for j in list_response.json()["jobs"])

    detail_response = test_client.get(f"/api/jobs/{job_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == job_id
    assert len(detail_response.json()["files"]) == 1


def test_get_unknown_job_returns_404(client):
    test_client, _db_path, _storage_dir = client
    response = test_client.get("/api/jobs/does-not-exist")
    assert response.status_code == 404


def test_download_before_completion_returns_409(client):
    test_client, _db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes(
        {"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"}
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    response = test_client.get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 409


def test_download_after_completion_returns_zip(client):
    test_client, db_path, storage_dir = client
    zip_bytes = _make_zip_bytes(
        {"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"}
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    db.update_job_status(db_path, job_id, "completed")
    output_dir = os.path.join(storage_dir, job_id)
    os.makedirs(output_dir, exist_ok=True)
    with zipfile.ZipFile(os.path.join(output_dir, "output.zip"), "w") as zf:
        zf.writestr("episode1.srt", "translated")

    response = test_client.get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] in ("application/zip", "application/x-zip-compressed")


def test_create_job_rejects_malformed_srt_and_leaves_no_trace(client):
    test_client, db_path, storage_dir = client
    zip_bytes = _make_zip_bytes(
        {
            "episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n",
            "episode2.srt": "this is not a valid srt file at all\njust plain text\n",
        }
    )

    response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )

    assert response.status_code == 400
    assert "episode2.srt" in response.json()["detail"]
    assert db.list_jobs(db_path) == []
    if os.path.isdir(storage_dir):
        assert os.listdir(storage_dir) == []


def test_download_returns_404_when_output_zip_missing_on_disk(client):
    test_client, db_path, storage_dir = client
    zip_bytes = _make_zip_bytes(
        {"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"}
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    db.update_job_status(db_path, job_id, "completed")
    # No output.zip is written to disk, simulating storage/DB divergence.

    response = test_client.get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 404


def test_list_jobs_includes_current_file_progress_for_processing_file(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes(
        {
            "episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n",
            "episode2.srt": "1\n00:00:00,000 --> 00:00:01,000\nWorld\n\n",
        }
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    job_files = db.get_job_files(db_path, job_id)
    processing_file = next(f for f in job_files if f["filename"] == "episode2.srt")
    db.update_job_file_status(db_path, processing_file["id"], "processing")
    db.update_job_file_progress(db_path, processing_file["id"], translated_blocks=1, failed_blocks=0)

    response = test_client.get("/api/jobs")
    job = next(j for j in response.json()["jobs"] if j["id"] == job_id)

    assert job["current_file"] == {
        "filename": "episode2.srt",
        "translated_blocks": 1,
        "total_blocks": 1,
    }


def test_list_jobs_current_file_is_null_when_no_file_processing(client):
    test_client, _db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes(
        {"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"}
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    response = test_client.get("/api/jobs")
    job = next(j for j in response.json()["jobs"] if j["id"] == job_id)

    assert job["current_file"] is None


def test_delete_job_removes_record_and_files(client):
    test_client, db_path, storage_dir = client
    zip_bytes = _make_zip_bytes(
        {"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"}
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    response = test_client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    assert db.get_job(db_path, job_id) is None
    assert not os.path.exists(os.path.join(storage_dir, job_id))


def test_delete_job_returns_409_when_processing(client):
    test_client, db_path, storage_dir = client
    zip_bytes = _make_zip_bytes(
        {"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"}
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    db.update_job_status(db_path, job_id, "processing")

    response = test_client.delete(f"/api/jobs/{job_id}")
    assert response.status_code == 409
    assert db.get_job(db_path, job_id) is not None
    assert os.path.exists(os.path.join(storage_dir, job_id))
