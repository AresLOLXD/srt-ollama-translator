import pytest
from app import db


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "jobs.db")
    db.init_db(path)
    return path


def test_create_and_get_job(db_path):
    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=2)
    job = db.get_job(db_path, "job-1")
    assert job["id"] == "job-1"
    assert job["original_zip_name"] == "movie.zip"
    assert job["status"] == "pending"
    assert job["total_files"] == 2
    assert job["processed_files"] == 0


def test_list_jobs_returns_all_jobs_newest_first(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.create_job(db_path, "job-2", "b.zip", "llama3.1", "auto", total_files=1)
    jobs = db.list_jobs(db_path)
    assert [j["id"] for j in jobs] == ["job-2", "job-1"]


def test_update_job_status_and_error_message(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.update_job_status(db_path, "job-1", "failed", error_message="ollama unreachable")
    job = db.get_job(db_path, "job-1")
    assert job["status"] == "failed"
    assert job["error_message"] == "ollama unreachable"


def test_increment_job_processed_files(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=2)
    db.increment_job_processed_files(db_path, "job-1")
    job = db.get_job(db_path, "job-1")
    assert job["processed_files"] == 1


def test_create_and_get_job_files(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=5)
    files = db.get_job_files(db_path, "job-1")
    assert len(files) == 1
    assert files[0]["filename"] == "episode1.srt"
    assert files[0]["total_blocks"] == 5
    assert files[0]["status"] == "pending"
    assert files[0]["translated_blocks"] == 0
    assert files[0]["failed_blocks"] == 0


def test_update_job_file_status_and_progress(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=5)
    db.update_job_file_status(db_path, "file-1", "processing")
    db.update_job_file_progress(db_path, "file-1", translated_blocks=3, failed_blocks=1)
    files = db.get_job_files(db_path, "job-1")
    assert files[0]["status"] == "processing"
    assert files[0]["translated_blocks"] == 3
    assert files[0]["failed_blocks"] == 1


def test_get_next_pending_job_returns_oldest_pending(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.create_job(db_path, "job-2", "b.zip", "llama3.1", "auto", total_files=1)
    next_job = db.get_next_pending_job(db_path)
    assert next_job["id"] == "job-1"


def test_get_next_pending_job_returns_none_when_no_pending(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.update_job_status(db_path, "job-1", "completed")
    assert db.get_next_pending_job(db_path) is None


def test_delete_job_removes_job_and_its_files(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=5)
    db.delete_job(db_path, "job-1")
    assert db.get_job(db_path, "job-1") is None
    assert db.get_job_files(db_path, "job-1") == []


def test_config_roundtrip_and_default(db_path):
    assert db.get_config(db_path, "ollama_base_url", "http://default:11434") == "http://default:11434"
    db.set_config(db_path, "ollama_base_url", "http://custom:11434")
    assert db.get_config(db_path, "ollama_base_url", "http://default:11434") == "http://custom:11434"
