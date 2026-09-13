import os
import zipfile
import pytest
from app import db
from app.queue_worker import process_job


class FakeOllamaClient:
    def __init__(self, response: str = "[1] Hola\n[2] Mundo"):
        self.response = response

    async def chat(self, model: str, prompt: str) -> str:
        return self.response


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "jobs.db")
    db.init_db(path)
    return path


def _write_srt(path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nWorld\n"
        )


@pytest.mark.asyncio
async def test_process_job_translates_files_and_marks_completed(tmp_path, db_path):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_srt(os.path.join(job_dir, "episode1.srt"))

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=1)

    job = db.get_job(db_path, "job-1")
    await process_job(db_path, storage_dir, FakeOllamaClient(), job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "completed"
    assert updated_job["processed_files"] == 1

    files = db.get_job_files(db_path, "job-1")
    assert files[0]["status"] == "completed"
    assert files[0]["failed_blocks"] == 0

    zip_path = os.path.join(storage_dir, "job-1", "output.zip")
    assert os.path.exists(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        assert "episode1.srt" in zf.namelist()


@pytest.mark.asyncio
async def test_process_job_marks_completed_with_errors_when_a_block_fails(tmp_path, db_path):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_srt(os.path.join(job_dir, "episode1.srt"))

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=1)

    job = db.get_job(db_path, "job-1")
    await process_job(db_path, storage_dir, FakeOllamaClient(response="[1] Hola"), job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "completed_with_errors"


@pytest.mark.asyncio
async def test_process_job_marks_failed_on_unexpected_error(tmp_path, db_path):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    # No .srt file written -> reading it will raise FileNotFoundError

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "missing.srt", total_blocks=1)

    job = db.get_job(db_path, "job-1")
    await process_job(db_path, storage_dir, FakeOllamaClient(), job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "failed"
    assert updated_job["error_message"] is not None
