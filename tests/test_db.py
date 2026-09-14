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


def test_reset_stale_processing_jobs_resets_processing_but_not_completed(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=2)
    db.update_job_status(db_path, "job-1", "processing")
    db.increment_job_processed_files(db_path, "job-1")

    db.create_job(db_path, "job-2", "b.zip", "llama3.1", "auto", total_files=1)
    db.update_job_status(db_path, "job-2", "completed")

    db.reset_stale_processing_jobs(db_path)

    job1 = db.get_job(db_path, "job-1")
    assert job1["status"] == "pending"
    assert job1["processed_files"] == 0

    job2 = db.get_job(db_path, "job-2")
    assert job2["status"] == "completed"


def test_reset_stale_processing_jobs_resets_job_files_status(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=2)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=5)
    db.create_job_file(db_path, "file-2", "job-1", "episode2.srt", total_blocks=3)

    db.update_job_file_status(db_path, "file-1", "processing")
    db.update_job_file_progress(db_path, "file-1", translated_blocks=2, failed_blocks=1)

    db.update_job_file_status(db_path, "file-2", "completed")
    db.update_job_file_progress(db_path, "file-2", translated_blocks=3, failed_blocks=0)

    db.reset_stale_processing_jobs(db_path)

    files = db.get_job_files(db_path, "job-1")
    file1 = next(f for f in files if f["id"] == "file-1")
    file2 = next(f for f in files if f["id"] == "file-2")

    assert file1["status"] == "pending"
    assert file1["translated_blocks"] == 0
    assert file1["failed_blocks"] == 0

    assert file2["status"] == "completed"
    assert file2["translated_blocks"] == 3
    assert file2["failed_blocks"] == 0


def test_config_roundtrip_and_default(db_path):
    assert db.get_config(db_path, "ollama_base_url", "http://default:11434") == "http://default:11434"
    db.set_config(db_path, "ollama_base_url", "http://custom:11434")
    assert db.get_config(db_path, "ollama_base_url", "http://default:11434") == "http://custom:11434"


def test_new_job_has_retry_count_and_cancel_requested_defaults(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    job = db.get_job(db_path, "job-1")
    assert job["retry_count"] == 0
    assert job["cancel_requested"] == 0


def test_init_db_migrates_existing_db_missing_new_columns(tmp_path):
    import sqlite3

    old_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(old_path)
    conn.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY,
            original_zip_name TEXT NOT NULL,
            model TEXT NOT NULL,
            source_lang TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            total_files INTEGER NOT NULL,
            processed_files INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO jobs VALUES ('job-1', 'a.zip', 'llama3.1', 'auto', 'pending', 1, 0, NULL, 't', 't')"
    )
    conn.commit()
    conn.close()

    db.init_db(old_path)

    job = db.get_job(old_path, "job-1")
    assert job["retry_count"] == 0
    assert job["cancel_requested"] == 0


def test_mark_job_for_retry_increments_count_sets_pending_and_error(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.update_job_status(db_path, "job-1", "processing")

    db.mark_job_for_retry(db_path, "job-1", "connection reset")

    job = db.get_job(db_path, "job-1")
    assert job["status"] == "pending"
    assert job["retry_count"] == 1
    assert job["error_message"] == "connection reset"

    db.mark_job_for_retry(db_path, "job-1", "connection reset again")
    job = db.get_job(db_path, "job-1")
    assert job["retry_count"] == 2


def test_reset_job_for_resume_clears_retry_state(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.mark_job_for_retry(db_path, "job-1", "boom")
    db.mark_job_for_retry(db_path, "job-1", "boom again")
    db.update_job_status(db_path, "job-1", "failed", error_message="boom again")
    db.set_job_cancel_requested(db_path, "job-1", True)

    db.reset_job_for_resume(db_path, "job-1")

    job = db.get_job(db_path, "job-1")
    assert job["status"] == "pending"
    assert job["retry_count"] == 0
    assert job["cancel_requested"] == 0


def test_set_and_get_job_cancel_requested(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    assert db.get_job_cancel_requested(db_path, "job-1") is False

    db.set_job_cancel_requested(db_path, "job-1", True)
    assert db.get_job_cancel_requested(db_path, "job-1") is True

    db.set_job_cancel_requested(db_path, "job-1", False)
    assert db.get_job_cancel_requested(db_path, "job-1") is False


def test_get_job_cancel_requested_returns_false_for_unknown_job(db_path):
    assert db.get_job_cancel_requested(db_path, "does-not-exist") is False


def test_job_file_blocks_upsert_get_and_delete_roundtrip(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=2)

    db.upsert_job_file_block(db_path, "file-1", 1, {1: "Hola", 2: "Mundo"}, True)
    db.upsert_job_file_block(db_path, "file-1", 2, {3: "Chau"}, False)

    completed = db.get_completed_job_file_blocks(db_path, "file-1")
    assert completed == {1: {1: "Hola", 2: "Mundo"}}

    # Upsert on the same position overwrites, doesn't duplicate
    db.upsert_job_file_block(db_path, "file-1", 2, {3: "Chau", 4: "Adios"}, True)
    completed = db.get_completed_job_file_blocks(db_path, "file-1")
    assert completed == {1: {1: "Hola", 2: "Mundo"}, 2: {3: "Chau", 4: "Adios"}}

    db.delete_job_file_blocks(db_path, "file-1")
    assert db.get_completed_job_file_blocks(db_path, "file-1") == {}
