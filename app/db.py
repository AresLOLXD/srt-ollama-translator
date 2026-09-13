import sqlite3
from datetime import datetime, timezone

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
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
);

CREATE TABLE IF NOT EXISTS job_files (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    total_blocks INTEGER NOT NULL DEFAULT 0,
    translated_blocks INTEGER NOT NULL DEFAULT 0,
    failed_blocks INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def create_job(
    db_path: str,
    job_id: str,
    original_zip_name: str,
    model: str,
    source_lang: str,
    total_files: int,
) -> None:
    conn = _connect(db_path)
    try:
        now = _now()
        conn.execute(
            """
            INSERT INTO jobs
                (id, original_zip_name, model, source_lang, status,
                 total_files, processed_files, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, 0, NULL, ?, ?)
            """,
            (job_id, original_zip_name, model, source_lang, total_files, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_job(db_path: str, job_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_jobs(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_job_status(
    db_path: str, job_id: str, status: str, error_message: str | None = None
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (status, error_message, _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_job_processed_files(db_path: str, job_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET processed_files = processed_files + 1, updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_job(db_path: str, job_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM job_files WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def create_job_file(
    db_path: str, file_id: str, job_id: str, filename: str, total_blocks: int
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO job_files
                (id, job_id, filename, status, total_blocks, translated_blocks, failed_blocks)
            VALUES (?, ?, ?, 'pending', ?, 0, 0)
            """,
            (file_id, job_id, filename, total_blocks),
        )
        conn.commit()
    finally:
        conn.close()


def get_job_files(db_path: str, job_id: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM job_files WHERE job_id = ? ORDER BY filename", (job_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_job_file_status(db_path: str, file_id: str, status: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE job_files SET status = ? WHERE id = ?", (status, file_id))
        conn.commit()
    finally:
        conn.close()


def update_job_file_progress(
    db_path: str, file_id: str, translated_blocks: int, failed_blocks: int
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE job_files SET translated_blocks = ?, failed_blocks = ? WHERE id = ?",
            (translated_blocks, failed_blocks, file_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_next_pending_job(db_path: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def reset_stale_processing_jobs(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status = 'pending', processed_files = 0, updated_at = ? "
            "WHERE status = 'processing'",
            (_now(),),
        )
        conn.commit()
    finally:
        conn.close()


def get_config(db_path: str, key: str, default: str | None = None) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_config(db_path: str, key: str, value: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
