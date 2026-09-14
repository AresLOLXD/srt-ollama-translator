import asyncio
import os
import zipfile
import pytest
from app import db
from app.queue_worker import process_job, worker_loop


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
async def test_process_job_marks_pending_and_increments_retry_count_on_unexpected_error(
    tmp_path, db_path
):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    # No .srt file written -> reading it will raise FileNotFoundError

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "missing.srt", total_blocks=1)

    job = db.get_job(db_path, "job-1")
    await process_job(db_path, storage_dir, FakeOllamaClient(), job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "pending"
    assert updated_job["retry_count"] == 1
    assert updated_job["error_message"] is not None


@pytest.mark.asyncio
async def test_process_job_marks_failed_after_retry_limit_exhausted(tmp_path, db_path):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    # No .srt file written -> every attempt raises FileNotFoundError

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "missing.srt", total_blocks=1)

    for _ in range(4):
        job = db.get_job(db_path, "job-1")
        await process_job(db_path, storage_dir, FakeOllamaClient(), job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "failed"
    assert updated_job["retry_count"] == 3


@pytest.mark.asyncio
async def test_process_job_catches_initial_status_update_failure(tmp_path, db_path, monkeypatch):
    """Verify that exceptions during initial status update (before the try block) are caught."""
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_srt(os.path.join(job_dir, "episode1.srt"))

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=1)

    job = db.get_job(db_path, "job-1")

    # Monkeypatch: make the first call to update_job_status raise an exception
    original_update = db.update_job_status
    call_count = [0]

    def failing_update_job_status(db_path, job_id, status, error_message=None):
        call_count[0] += 1
        if call_count[0] == 1 and status == "processing":
            raise RuntimeError("Database connection lost")
        return original_update(db_path, job_id, status, error_message)

    monkeypatch.setattr(db, "update_job_status", failing_update_job_status)

    # Call process_job - it should NOT raise an exception, even though
    # the initial status update failed
    await process_job(db_path, storage_dir, FakeOllamaClient(), job)

    # The job should be marked as pending for auto-retry with the error message
    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "pending"
    assert updated_job["retry_count"] == 1
    assert "Database connection lost" in updated_job["error_message"]


@pytest.mark.asyncio
async def test_worker_loop_uses_ollama_base_url_env_var_when_config_unset(
    tmp_path, db_path, monkeypatch
):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-configured:11434")
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_srt(os.path.join(job_dir, "episode1.srt"))

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=1)

    seen_urls = []

    def factory(base_url, timeout):
        seen_urls.append(base_url)
        return FakeOllamaClient()

    task = asyncio.create_task(
        worker_loop(db_path, storage_dir, factory, poll_interval=0.01)
    )
    for _ in range(100):
        if seen_urls:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert seen_urls == ["http://env-configured:11434"]


@pytest.mark.asyncio
async def test_worker_loop_uses_ollama_timeout_env_var_when_config_unset(
    tmp_path, db_path, monkeypatch
):
    monkeypatch.setenv("OLLAMA_TIMEOUT", "300")
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_srt(os.path.join(job_dir, "episode1.srt"))

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=1)

    seen_timeouts = []

    def factory(base_url, timeout):
        seen_timeouts.append(timeout)
        return FakeOllamaClient()

    task = asyncio.create_task(
        worker_loop(db_path, storage_dir, factory, poll_interval=0.01)
    )
    for _ in range(100):
        if seen_timeouts:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert seen_timeouts == [300.0]


@pytest.mark.asyncio
async def test_worker_loop_does_not_raise_when_get_next_pending_job_fails(
    db_path, monkeypatch
):
    call_count = [0]

    def failing_get_next_pending_job(db_path):
        call_count[0] += 1
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "get_next_pending_job", failing_get_next_pending_job)

    task = asyncio.create_task(
        worker_loop(
            db_path,
            "/tmp/storage",
            lambda base_url, timeout: FakeOllamaClient(),
            poll_interval=0.01,
        )
    )
    await asyncio.sleep(0.05)
    assert not task.done()
    assert call_count[0] > 0
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
