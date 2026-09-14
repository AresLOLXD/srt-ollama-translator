# Resiliencia de Jobs (retry, resume, stop, descarga parcial) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A job that fails mid-translation because of a transient network error (e.g. Ollama disconnecting during a scheduled restart) recovers automatically without repeating already-translated blocks, and the user can manually stop or resume a job and download whatever `.srt` files already finished.

**Architecture:** Persist per-block translation results in a new `job_file_blocks` table as they happen, so a job that gets interrupted (crash, exception, or user-requested stop) can be resumed from exactly where it left off instead of from scratch. A job-level `retry_count` lets the worker auto-retry transient failures a bounded number of times before giving up (still resumable manually after that). A cooperative `cancel_requested` flag lets the worker stop cleanly between blocks. The download endpoint stops requiring the whole job to be finished — it serves whichever `.srt` files inside the job already completed.

**Tech Stack:** Python 3.12, FastAPI, sqlite3 (stdlib, no ORM), httpx, pytest + pytest-asyncio, vanilla JS frontend.

**Spec:** `docs/superpowers/specs/2026-09-14-job-resilience-design.md`

## Global Constraints

- No ORM, no connection pooling — every DB function opens and closes its own `sqlite3` connection (existing pattern in `app/db.py`).
- Route modules stay factories: `get_router(db_path, ...)` (existing pattern, do not change).
- Tests that touch `OllamaClient` use the `transport` constructor param with `httpx.MockTransport`/a fake transport — never a real Ollama server.
- Only files that already existed in `docs/superpowers/specs/2026-09-14-job-resilience-design.md` scope are touched: `app/db.py`, `app/ollama_client.py`, `app/translator.py`, `app/queue_worker.py`, `app/routes/jobs.py`, `app/zip_utils.py`, `static/app.js`, plus their test files.
- Descarga parcial only ever includes fully-completed `.srt` files — never partial content of the file currently mid-translation (explicit non-goal in the spec).
- Auto-retry limit is 3 (`retry_count < 3` allows a retry; the 4th failure is terminal).
- Job-level retry backoff: none beyond the worker's normal 2s poll interval plus the OllamaClient-level backoff (see Task 2) — do not add extra sleep/backoff at the job level, that's YAGNI for a local single-user app.

---

### Task 1: DB layer — schema, migration, and CRUD for job resilience

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces (used by Tasks 4 and 5):
  - `db.get_job(db_path, job_id)` — unchanged signature, but returned dict now also has `retry_count: int` and `cancel_requested: int` (0/1) keys.
  - `db.mark_job_for_retry(db_path: str, job_id: str, error_message: str) -> None`
  - `db.reset_job_for_resume(db_path: str, job_id: str) -> None`
  - `db.set_job_cancel_requested(db_path: str, job_id: str, value: bool) -> None`
  - `db.get_job_cancel_requested(db_path: str, job_id: str) -> bool`
  - `db.upsert_job_file_block(db_path: str, job_file_id: str, position: int, translations: dict[int, str], success: bool) -> None`
  - `db.get_completed_job_file_blocks(db_path: str, job_file_id: str) -> dict[int, dict[int, str]]` — keyed by block position, only rows with `success=1`.
  - `db.delete_job_file_blocks(db_path: str, job_file_id: str) -> None`
  - `db.reset_stale_processing_jobs(db_path)` — behavior changes: no longer zeroes `processed_files`, `translated_blocks`, or `failed_blocks`.

- [ ] **Step 1: Write failing tests for the new columns and migration**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k "retry_count_and_cancel_requested_defaults or migrates_existing_db" -v`
Expected: FAIL — `KeyError: 'retry_count'` (column doesn't exist yet).

- [ ] **Step 3: Add the new columns and migration to `app/db.py`**

Replace the `jobs` table definition inside `SCHEMA_SQL` (currently `app/db.py:5-16`) with:

```python
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
    retry_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS job_file_blocks (
    job_file_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    translations_json TEXT NOT NULL,
    success INTEGER NOT NULL,
    PRIMARY KEY (job_file_id, position)
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
```

Then update `init_db` (currently `app/db.py:46-53`) to migrate pre-existing databases that were created before these columns existed:

```python
def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        _ensure_column(conn, "jobs", "retry_count", "retry_count INTEGER NOT NULL DEFAULT 0")
        _ensure_column(
            conn, "jobs", "cancel_requested", "cancel_requested INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -k "retry_count_and_cancel_requested_defaults or migrates_existing_db" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add retry_count/cancel_requested columns with migration for existing dbs"
```

- [ ] **Step 6: Write failing tests for the new job/job_file mutation helpers**

Append to `tests/test_db.py`:

```python
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
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -k "mark_job_for_retry or reset_job_for_resume or cancel_requested or job_file_blocks" -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'mark_job_for_retry'`

- [ ] **Step 8: Implement the new functions in `app/db.py`**

Add after `update_job_status` (currently ends at `app/db.py:110`):

```python
def mark_job_for_retry(db_path: str, job_id: str, error_message: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status = 'pending', retry_count = retry_count + 1, "
            "error_message = ?, updated_at = ? WHERE id = ?",
            (error_message, _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def reset_job_for_resume(db_path: str, job_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status = 'pending', retry_count = 0, cancel_requested = 0, "
            "error_message = NULL, updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_job_cancel_requested(db_path: str, job_id: str, value: bool) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET cancel_requested = ?, updated_at = ? WHERE id = ?",
            (int(value), _now(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_job_cancel_requested(db_path: str, job_id: str) -> bool:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return bool(row["cancel_requested"]) if row else False
    finally:
        conn.close()
```

Add after `update_job_file_progress` (currently ends at `app/db.py:184`), before `get_next_pending_job`:

```python
def upsert_job_file_block(
    db_path: str, job_file_id: str, position: int, translations: dict[int, str], success: bool
) -> None:
    conn = _connect(db_path)
    try:
        translations_json = json.dumps({str(k): v for k, v in translations.items()})
        conn.execute(
            """
            INSERT INTO job_file_blocks (job_file_id, position, translations_json, success)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_file_id, position) DO UPDATE SET
                translations_json = excluded.translations_json,
                success = excluded.success
            """,
            (job_file_id, position, translations_json, int(success)),
        )
        conn.commit()
    finally:
        conn.close()


def get_completed_job_file_blocks(db_path: str, job_file_id: str) -> dict[int, dict[int, str]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT position, translations_json FROM job_file_blocks "
            "WHERE job_file_id = ? AND success = 1",
            (job_file_id,),
        ).fetchall()
        return {
            row["position"]: {int(k): v for k, v in json.loads(row["translations_json"]).items()}
            for row in rows
        }
    finally:
        conn.close()


def delete_job_file_blocks(db_path: str, job_file_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM job_file_blocks WHERE job_file_id = ?", (job_file_id,))
        conn.commit()
    finally:
        conn.close()
```

Add `import json` at the top of `app/db.py` (`app/db.py:1-2`):

```python
import json
import sqlite3
from datetime import datetime, timezone
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -k "mark_job_for_retry or reset_job_for_resume or cancel_requested or job_file_blocks" -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add job retry/cancel state and per-block persistence to db layer"
```

- [ ] **Step 11: Fix `reset_stale_processing_jobs` to preserve progress, and update its existing tests**

Read `tests/test_db.py:88-129` — the two existing tests
`test_reset_stale_processing_jobs_resets_processing_but_not_completed` and
`test_reset_stale_processing_jobs_resets_job_files_status` currently assert
that `processed_files`, `translated_blocks`, and `failed_blocks` get zeroed
on reset. That's the bug from the spec's root-cause analysis: zeroing these
throws away real progress that `job_file_blocks` (and the `completed`
job_files) still make resumable. Update both tests in place:

```python
def test_reset_stale_processing_jobs_resets_processing_but_not_completed(db_path):
    db.create_job(db_path, "job-1", "a.zip", "llama3.1", "auto", total_files=2)
    db.update_job_status(db_path, "job-1", "processing")
    db.increment_job_processed_files(db_path, "job-1")

    db.create_job(db_path, "job-2", "b.zip", "llama3.1", "auto", total_files=1)
    db.update_job_status(db_path, "job-2", "completed")

    db.reset_stale_processing_jobs(db_path)

    job1 = db.get_job(db_path, "job-1")
    assert job1["status"] == "pending"
    assert job1["processed_files"] == 1

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
    assert file1["translated_blocks"] == 2
    assert file1["failed_blocks"] == 1

    assert file2["status"] == "completed"
    assert file2["translated_blocks"] == 3
    assert file2["failed_blocks"] == 0
```

- [ ] **Step 12: Run tests to verify they fail against current implementation**

Run: `uv run pytest tests/test_db.py -k reset_stale_processing_jobs -v`
Expected: FAIL — `assert 0 == 1` (processed_files/translated_blocks still being zeroed).

- [ ] **Step 13: Update `reset_stale_processing_jobs`**

Replace the function body (currently `app/db.py:197-210`... offsets shift after
Step 3/8 edits, locate by function name) with:

```python
def reset_stale_processing_jobs(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status = 'pending', updated_at = ? WHERE status = 'processing'",
            (_now(),),
        )
        conn.execute("UPDATE job_files SET status = 'pending' WHERE status = 'processing'")
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 15: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "fix: reset_stale_processing_jobs preserves progress instead of zeroing it"
```

---

### Task 2: OllamaClient retries transient network errors

**Files:**
- Modify: `app/ollama_client.py`
- Test: `tests/test_ollama_client.py`

**Interfaces:**
- Produces (used by Task 4 indirectly, no signature change for callers): `OllamaClient.__init__(base_url, timeout=120.0, transport=None, retry_backoff_seconds=1.0)`. `chat()` keeps its existing signature and return type.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_ollama_client.py`:

```python
@pytest.mark.asyncio
async def test_chat_retries_on_transport_error_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"message": {"content": "hola"}})

    client = OllamaClient(
        "http://fake-ollama:11434",
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    result = await client.chat("llama3.1", "hello")

    assert result == "hola"
    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_chat_raises_after_exhausting_retries():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    client = OllamaClient(
        "http://fake-ollama:11434",
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    with pytest.raises(httpx.RemoteProtocolError):
        await client.chat("llama3.1", "hello")

    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_chat_does_not_retry_http_status_errors():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500)

    client = OllamaClient(
        "http://fake-ollama:11434",
        transport=httpx.MockTransport(handler),
        retry_backoff_seconds=0,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.chat("llama3.1", "hello")

    assert calls["count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ollama_client.py -k "retries_on_transport_error or exhausting_retries or does_not_retry_http_status" -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'retry_backoff_seconds'`

- [ ] **Step 3: Implement the retry in `app/ollama_client.py`**

Replace the whole file content with:

```python
import asyncio

import httpx


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_backoff_seconds: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport
        self.retry_backoff_seconds = retry_backoff_seconds

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def list_models(self) -> list[str]:
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]

    async def chat(self, model: str, prompt: str, max_retries: int = 3) -> str:
        last_error: httpx.TransportError | None = None
        for attempt in range(max_retries):
            try:
                async with self._client() as client:
                    response = await client.post(
                        f"{self.base_url}/api/chat",
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "stream": False,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    return data["message"]["content"]
            except httpx.TransportError as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    await asyncio.sleep(self.retry_backoff_seconds * (2**attempt))
        raise last_error
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ollama_client.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add app/ollama_client.py tests/test_ollama_client.py
git commit -m "feat: retry transient network errors in OllamaClient.chat"
```

---

### Task 3: translator.py — resume from persisted blocks

**Files:**
- Modify: `app/translator.py`
- Test: `tests/test_translator_translate.py`

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces (used by Task 4): `translate_srt_file(..., on_block_result: Callable[[int, dict[int, str], bool], None] | None = None, resume_blocks: dict[int, dict[int, str]] | None = None)`. Return type unchanged: `tuple[int, int]` (total_blocks, failed_blocks).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_translator_translate.py`:

```python
@pytest.mark.asyncio
async def test_translate_srt_file_reports_block_results(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola\n[2] Mundo"])
    results = []

    await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        on_block_result=lambda position, translations, success: results.append(
            (position, translations, success)
        ),
        block_size=25,
    )

    assert results == [(1, {1: "Hola", 2: "Mundo"}, True)]


@pytest.mark.asyncio
async def test_translate_srt_file_reports_failed_block_result(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola"])
    results = []

    await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        on_block_result=lambda position, translations, success: results.append(
            (position, translations, success)
        ),
    )

    assert results == [(1, {1: "Hola"}, False)]


@pytest.mark.asyncio
async def test_translate_srt_file_skips_ollama_for_resumed_blocks(tmp_path):
    input_path = tmp_path / "input.srt"
    lines = []
    for i in range(1, 51):
        start = f"00:00:{i:02d},000"
        end = f"00:00:{i + 1:02d},000"
        lines.append(f"{i}\n{start} --> {end}\nLine {i}\n")
    input_path.write_text("\n".join(lines), encoding="utf-8")
    output_path = tmp_path / "output.srt"

    # Only one scripted response: for block 2 (positions 26-50). Block 1 is
    # "resumed" and must never reach the client.
    block_2_response = "\n".join(f"[{i}] Traducido {i}" for i in range(26, 51))
    client = FakeClient([block_2_response])
    resume_blocks = {1: {i: f"Ya traducido {i}" for i in range(1, 26)}}

    total_blocks, failed_blocks = await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        resume_blocks=resume_blocks,
        block_size=25,
    )

    assert total_blocks == 2
    assert failed_blocks == 0
    assert client.calls == 1
    output_text = output_path.read_text(encoding="utf-8")
    assert "Ya traducido 1" in output_text
    assert "Traducido 26" in output_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_translate.py -k "reports_block_results or reports_failed_block_result or skips_ollama_for_resumed_blocks" -v`
Expected: FAIL — `TypeError: translate_srt_file() got an unexpected keyword argument 'on_block_result'`

- [ ] **Step 3: Implement in `app/translator.py`**

Replace `translate_srt_file` (currently `app/translator.py:87-119`) with:

```python
async def translate_srt_file(
    client,
    model: str,
    source_lang: str,
    input_path: str,
    output_path: str,
    on_block_translated: Callable[[int, int], None] | None = None,
    on_block_result: Callable[[int, dict[int, str], bool], None] | None = None,
    resume_blocks: dict[int, dict[int, str]] | None = None,
    block_size: int = 25,
    filename: str | None = None,
) -> tuple[int, int]:
    subs = list(srt.parse(read_srt_text(input_path)))

    blocks = split_into_blocks(subs, block_size=block_size)
    resume_blocks = resume_blocks or {}
    translated_by_index: dict[int, str] = {}
    failed_blocks = 0

    for position, block in enumerate(blocks, start=1):
        expected_indices = {sub.index for sub in block.subs}

        if position in resume_blocks:
            translations = resume_blocks[position]
        else:
            translations = await translate_block(client, model, block, source_lang, filename=filename)
            success = expected_indices.issubset(translations.keys())
            if on_block_result:
                on_block_result(position, translations, success)

        if not expected_indices.issubset(translations.keys()):
            failed_blocks += 1
        translated_by_index.update(translations)
        if on_block_translated:
            on_block_translated(position, len(blocks))

    for sub in subs:
        if sub.index in translated_by_index:
            sub.content = translated_by_index[sub.index]

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt.compose(subs))

    return len(blocks), failed_blocks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_translate.py -v`
Expected: PASS (all tests in the file, including pre-existing ones — signature is backward compatible since the new params default to `None`)

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_translate.py
git commit -m "feat: let translate_srt_file resume from already-completed blocks"
```

---

### Task 4: queue_worker.py — skip completed files, persist blocks, auto-retry, cooperative stop

**Files:**
- Modify: `app/queue_worker.py`
- Test: `tests/test_queue_worker.py`

**Interfaces:**
- Consumes: `db.mark_job_for_retry`, `db.get_completed_job_file_blocks`, `db.upsert_job_file_block`, `db.delete_job_file_blocks`, `db.get_job_cancel_requested`, `db.set_job_cancel_requested` (Task 1); `translate_srt_file(..., on_block_result=..., resume_blocks=...)` (Task 3).
- Produces: `queue_worker.JobCancelled` exception class (internal, not imported elsewhere). `process_job` signature unchanged.

- [ ] **Step 1: Update existing tests for the new failure-handling behavior**

The existing tests `test_process_job_marks_failed_on_unexpected_error` and
`test_process_job_catches_initial_status_update_failure` in
`tests/test_queue_worker.py` assert that a single unexpected exception marks
the job `failed` immediately. That's no longer true — a single failure now
consumes one of 3 auto-retries and goes back to `pending`. Replace
`test_process_job_marks_failed_on_unexpected_error` with the two tests
below, and update the assertions in
`test_process_job_catches_initial_status_update_failure` (same function,
new expected status):

```python
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

    original_update = db.update_job_status
    call_count = [0]

    def failing_update_job_status(db_path, job_id, status, error_message=None):
        call_count[0] += 1
        if call_count[0] == 1 and status == "processing":
            raise RuntimeError("Database connection lost")
        return original_update(db_path, job_id, status, error_message)

    monkeypatch.setattr(db, "update_job_status", failing_update_job_status)

    await process_job(db_path, storage_dir, FakeOllamaClient(), job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "pending"
    assert updated_job["retry_count"] == 1
    assert "Database connection lost" in updated_job["error_message"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queue_worker.py -k "unexpected_error or retry_limit_exhausted or initial_status_update_failure" -v`
Expected: FAIL — asserts `status == "pending"` but current code produces `"failed"`.

- [ ] **Step 3: Rewrite `process_job` in `app/queue_worker.py`**

Replace the whole file content with:

```python
import asyncio
import logging
import os

from app import db, zip_utils
from app.config import resolve_ollama_timeout, resolve_ollama_url
from app.translator import translate_srt_file

logger = logging.getLogger(__name__)

MAX_JOB_RETRIES = 3


class JobCancelled(Exception):
    pass


async def process_job(db_path: str, storage_dir: str, ollama_client, job: dict) -> None:
    job_id = job["id"]
    try:
        db.update_job_status(db_path, job_id, "processing")
        job_dir = os.path.join(storage_dir, job_id)
        input_dir = os.path.join(job_dir, "input")
        output_dir = os.path.join(job_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        any_failed = False
        for job_file in db.get_job_files(db_path, job_id):
            if job_file["status"] in ("completed", "completed_with_errors"):
                if job_file["failed_blocks"] > 0:
                    any_failed = True
                continue

            db.update_job_file_status(db_path, job_file["id"], "processing")
            input_path = os.path.join(input_dir, job_file["filename"])
            output_path = os.path.join(output_dir, job_file["filename"])
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            def on_progress(done: int, _total: int, file_id: str = job_file["id"]) -> None:
                db.update_job_file_progress(db_path, file_id, done, 0)
                if db.get_job_cancel_requested(db_path, job_id):
                    raise JobCancelled()

            def on_result(
                position: int,
                translations: dict[int, str],
                success: bool,
                file_id: str = job_file["id"],
            ) -> None:
                db.upsert_job_file_block(db_path, file_id, position, translations, success)

            resume_blocks = db.get_completed_job_file_blocks(db_path, job_file["id"])

            total_blocks, failed_blocks = await translate_srt_file(
                ollama_client,
                job["model"],
                job["source_lang"],
                input_path,
                output_path,
                on_block_translated=on_progress,
                on_block_result=on_result,
                resume_blocks=resume_blocks,
                filename=job_file["filename"],
            )

            file_status = "completed_with_errors" if failed_blocks > 0 else "completed"
            if failed_blocks > 0:
                any_failed = True
            db.update_job_file_status(db_path, job_file["id"], file_status)
            db.update_job_file_progress(db_path, job_file["id"], total_blocks, failed_blocks)
            db.delete_job_file_blocks(db_path, job_file["id"])
            db.increment_job_processed_files(db_path, job_id)

        zip_path = os.path.join(job_dir, "output.zip")
        zip_utils.create_zip(output_dir, zip_path)
        db.update_job_status(
            db_path, job_id, "completed_with_errors" if any_failed else "completed"
        )
    except JobCancelled:
        db.set_job_cancel_requested(db_path, job_id, False)
        db.update_job_status(db_path, job_id, "stopped")
    except Exception as exc:  # noqa: BLE001 - job failures must never crash the worker loop
        logger.exception("Job %s failed", job_id)
        if job["retry_count"] < MAX_JOB_RETRIES:
            db.mark_job_for_retry(db_path, job_id, str(exc))
        else:
            db.update_job_status(db_path, job_id, "failed", error_message=str(exc))


async def worker_loop(
    db_path: str, storage_dir: str, ollama_client_factory, poll_interval: float = 2.0
) -> None:
    while True:
        try:
            job = db.get_next_pending_job(db_path)
            if job is None:
                await asyncio.sleep(poll_interval)
                continue
            base_url = resolve_ollama_url(db_path)
            timeout = resolve_ollama_timeout(db_path)
            ollama_client = ollama_client_factory(base_url, timeout)
            await process_job(db_path, storage_dir, ollama_client, job)
        except Exception:  # noqa: BLE001 - the worker loop must never die silently
            logger.exception("Unexpected error in worker loop")
            await asyncio.sleep(poll_interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queue_worker.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: auto-retry transient job failures with a bounded retry count"
```

- [ ] **Step 6: Write failing tests for skipping completed files and resuming a mid-file block**

Append to `tests/test_queue_worker.py`:

```python
class ScriptedOllamaClient:
    """Returns scripted responses in order; records every prompt it receives."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.prompts = []

    async def chat(self, model: str, prompt: str) -> str:
        self.prompts.append(prompt)
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _write_multi_block_srt(path: str, count: int) -> None:
    lines = []
    for i in range(1, count + 1):
        start_s, end_s = i, i + 1
        lines.append(f"{i}\n00:00:{start_s:02d},000 --> 00:00:{end_s:02d},000\nLine {i}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


@pytest.mark.asyncio
async def test_process_job_skips_already_completed_files(tmp_path, db_path):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_srt(os.path.join(job_dir, "episode1.srt"))
    _write_srt(os.path.join(job_dir, "episode2.srt"))

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=2)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=1)
    db.create_job_file(db_path, "file-2", "job-1", "episode2.srt", total_blocks=1)

    # Simulate file-1 having already completed in a previous, interrupted run.
    db.update_job_file_status(db_path, "file-1", "completed")
    db.update_job_file_progress(db_path, "file-1", translated_blocks=1, failed_blocks=0)
    db.increment_job_processed_files(db_path, "job-1")

    job = db.get_job(db_path, "job-1")
    client = ScriptedOllamaClient(["[1] Hola\n[2] Mundo"])
    await process_job(db_path, storage_dir, client, job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "completed"
    assert updated_job["processed_files"] == 2
    assert client.calls == 1  # only file-2 was translated

    files = {f["filename"]: f for f in db.get_job_files(db_path, "job-1")}
    assert files["episode1.srt"]["status"] == "completed"
    assert files["episode2.srt"]["status"] == "completed"


@pytest.mark.asyncio
async def test_process_job_resumes_mid_file_without_recalling_ollama_for_done_blocks(
    tmp_path, db_path
):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_multi_block_srt(os.path.join(job_dir, "episode1.srt"), 50)

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=2)

    # Simulate block 1 (subs 1-25) already translated and persisted before a crash.
    block_1_translations = {i: f"Ya traducido {i}" for i in range(1, 26)}
    db.upsert_job_file_block(db_path, "file-1", 1, block_1_translations, True)
    db.update_job_file_status(db_path, "file-1", "processing")
    db.update_job_file_progress(db_path, "file-1", translated_blocks=1, failed_blocks=0)

    block_2_response = "\n".join(f"[{i}] Traducido {i}" for i in range(26, 51))
    job = db.get_job(db_path, "job-1")
    client = ScriptedOllamaClient([block_2_response])
    await process_job(db_path, storage_dir, client, job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "completed"
    assert client.calls == 1  # block 1 was reused, only block 2 hit the client

    output_path = os.path.join(storage_dir, "job-1", "output", "episode1.srt")
    output_text = open(output_path, encoding="utf-8").read()
    assert "Ya traducido 1" in output_text
    assert "Traducido 26" in output_text

    # job_file_blocks cleaned up once the file completed
    assert db.get_completed_job_file_blocks(db_path, "file-1") == {}
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest tests/test_queue_worker.py -k "skips_already_completed_files or resumes_mid_file" -v`
Expected: FAIL — `client.calls == 2` (both files/blocks get processed, nothing skipped yet, since `process_job` doesn't check `job_file["status"]` before this step... wait, Step 3 already added the skip logic). Actually expected to already PASS after Step 3's implementation — this step exists to lock in the behavior with a dedicated test. If it fails, re-check the `continue` branch and `resume_blocks` wiring added in Step 3.

- [ ] **Step 8: Fix or confirm implementation**

If Step 7 failed, re-check `app/queue_worker.py` against the code in Step 3 — the skip-if-completed `continue` and the `resume_blocks = db.get_completed_job_file_blocks(...)` line are what make these tests pass. No new production code should be needed here if Step 3 was applied correctly; this step exists to verify it.

Run: `uv run pytest tests/test_queue_worker.py -k "skips_already_completed_files or resumes_mid_file" -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add tests/test_queue_worker.py
git commit -m "test: cover resume-by-block and skip-completed-file behavior in process_job"
```

- [ ] **Step 10: Write a failing test for cooperative cancellation**

Append to `tests/test_queue_worker.py`:

```python
@pytest.mark.asyncio
async def test_process_job_stops_cleanly_when_cancel_requested_mid_file(tmp_path, db_path):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_multi_block_srt(os.path.join(job_dir, "episode1.srt"), 50)

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=2)

    class CancellingClient:
        def __init__(self):
            self.calls = 0

        async def chat(self, model: str, prompt: str) -> str:
            self.calls += 1
            # Simulate the user clicking "Detener" while block 1 is in flight.
            db.set_job_cancel_requested(db_path, "job-1", True)
            return "\n".join(f"[{i}] Traducido {i}" for i in range(1, 26))

    job = db.get_job(db_path, "job-1")
    client = CancellingClient()
    await process_job(db_path, storage_dir, client, job)

    updated_job = db.get_job(db_path, "job-1")
    assert updated_job["status"] == "stopped"
    assert updated_job["cancel_requested"] == 0
    assert client.calls == 1  # block 2 was never attempted

    files = db.get_job_files(db_path, "job-1")
    assert files[0]["status"] == "processing"  # left mid-file, resumable later


@pytest.mark.asyncio
async def test_process_job_stopped_file_keeps_resumable_block_progress(tmp_path, db_path):
    storage_dir = str(tmp_path / "storage")
    job_dir = os.path.join(storage_dir, "job-1", "input")
    os.makedirs(job_dir)
    _write_multi_block_srt(os.path.join(job_dir, "episode1.srt"), 50)

    db.create_job(db_path, "job-1", "movie.zip", "llama3.1", "auto", total_files=1)
    db.create_job_file(db_path, "file-1", "job-1", "episode1.srt", total_blocks=2)

    class CancellingClient:
        async def chat(self, model: str, prompt: str) -> str:
            db.set_job_cancel_requested(db_path, "job-1", True)
            return "\n".join(f"[{i}] Traducido {i}" for i in range(1, 26))

    job = db.get_job(db_path, "job-1")
    await process_job(db_path, storage_dir, CancellingClient(), job)

    resumed = db.get_completed_job_file_blocks(db_path, "file-1")
    assert resumed == {1: {i: f"Traducido {i}" for i in range(1, 26)}}
```

- [ ] **Step 11: Run tests to verify they fail**

Run: `uv run pytest tests/test_queue_worker.py -k "stops_cleanly_when_cancel_requested or stopped_file_keeps_resumable" -v`
Expected: PASS already if Step 3's `JobCancelled` handling and `on_progress` cancellation check were applied correctly — this step exists to verify. If it fails, re-check that `on_progress` calls `db.get_job_cancel_requested` and raises `JobCancelled`, and that `process_job` catches `JobCancelled` before the generic `except Exception`.

- [ ] **Step 12: Confirm pass**

Run: `uv run pytest tests/test_queue_worker.py -v`
Expected: PASS (entire file)

- [ ] **Step 13: Commit**

```bash
git add tests/test_queue_worker.py
git commit -m "test: cover cooperative job cancellation leaving resumable block progress"
```

---

### Task 5: routes/jobs.py — stop, resume, and partial download endpoints

**Files:**
- Modify: `app/routes/jobs.py`
- Modify: `app/zip_utils.py`
- Test: `tests/test_routes_jobs.py`
- Test: `tests/test_zip_utils.py`

**Interfaces:**
- Consumes: `db.update_job_status`, `db.set_job_cancel_requested`, `db.reset_job_for_resume`, `db.get_job_files` (existing/Task 1).
- Produces: `zip_utils.create_zip_subset(src_dir: str, filenames: list[str], zip_path: str) -> None`. New routes `POST /api/jobs/{job_id}/stop`, `POST /api/jobs/{job_id}/resume`.

- [ ] **Step 1: Write a failing test for `zip_utils.create_zip_subset`**

Append to `tests/test_zip_utils.py` (check the file first for its existing fixture style — follow the same pattern used there for `create_zip`):

```python
def test_create_zip_subset_includes_only_named_files(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.srt").write_text("a", encoding="utf-8")
    (src_dir / "b.srt").write_text("b", encoding="utf-8")
    (src_dir / "c.srt").write_text("c", encoding="utf-8")

    zip_path = tmp_path / "out.zip"
    zip_utils.create_zip_subset(str(src_dir), ["a.srt", "c.srt"], str(zip_path))

    with zipfile.ZipFile(str(zip_path)) as zf:
        assert sorted(zf.namelist()) == ["a.srt", "c.srt"]


def test_create_zip_subset_skips_missing_files(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "a.srt").write_text("a", encoding="utf-8")

    zip_path = tmp_path / "out.zip"
    zip_utils.create_zip_subset(str(src_dir), ["a.srt", "does-not-exist.srt"], str(zip_path))

    with zipfile.ZipFile(str(zip_path)) as zf:
        assert zf.namelist() == ["a.srt"]
```

(Add `import zipfile` and `from app import zip_utils` at the top of the test
file if not already imported — check first, `test_zip_utils.py` likely
already imports `zip_utils`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_zip_utils.py -k create_zip_subset -v`
Expected: FAIL — `AttributeError: module 'app.zip_utils' has no attribute 'create_zip_subset'`

- [ ] **Step 3: Implement `create_zip_subset` in `app/zip_utils.py`**

Add after `create_zip` (currently `app/zip_utils.py:28-33`, end of file):

```python
def create_zip_subset(src_dir: str, filenames: list[str], zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in filenames:
            full_path = os.path.join(src_dir, name)
            if os.path.isfile(full_path):
                zf.write(full_path, name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_zip_utils.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add app/zip_utils.py tests/test_zip_utils.py
git commit -m "feat: add create_zip_subset for zipping a named subset of files"
```

- [ ] **Step 6: Write failing tests for `/stop` and `/resume`**

Append to `tests/test_routes_jobs.py`:

```python
def test_stop_pending_job_marks_it_stopped_immediately(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"})
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    response = test_client.post(f"/api/jobs/{job_id}/stop")
    assert response.status_code == 200
    assert db.get_job(db_path, job_id)["status"] == "stopped"


def test_stop_processing_job_sets_cancel_requested(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"})
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]
    db.update_job_status(db_path, job_id, "processing")

    response = test_client.post(f"/api/jobs/{job_id}/stop")
    assert response.status_code == 200
    job = db.get_job(db_path, job_id)
    assert job["status"] == "processing"
    assert job["cancel_requested"] == 1


def test_stop_completed_job_returns_409(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"})
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]
    db.update_job_status(db_path, job_id, "completed")

    response = test_client.post(f"/api/jobs/{job_id}/stop")
    assert response.status_code == 409


def test_stop_unknown_job_returns_404(client):
    test_client, _db_path, _storage_dir = client
    response = test_client.post("/api/jobs/does-not-exist/stop")
    assert response.status_code == 404


def test_resume_failed_job_sets_pending_and_clears_retry_state(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"})
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]
    db.mark_job_for_retry(db_path, job_id, "boom")
    db.mark_job_for_retry(db_path, job_id, "boom")
    db.update_job_status(db_path, job_id, "failed", error_message="boom")

    response = test_client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 200
    job = db.get_job(db_path, job_id)
    assert job["status"] == "pending"
    assert job["retry_count"] == 0


def test_resume_stopped_job_sets_pending(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"})
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]
    db.update_job_status(db_path, job_id, "stopped")

    response = test_client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 200
    assert db.get_job(db_path, job_id)["status"] == "pending"


def test_resume_processing_job_returns_409(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"})
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]
    db.update_job_status(db_path, job_id, "processing")

    response = test_client.post(f"/api/jobs/{job_id}/resume")
    assert response.status_code == 409
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "stop_ or resume_" -v`
Expected: FAIL — `404 Not Found` (routes don't exist yet — `TestClient` returns 404 for undefined POST routes).

- [ ] **Step 8: Add `/stop` and `/resume` routes to `app/routes/jobs.py`**

Add after the `download_job` route (currently `app/routes/jobs.py:86-99`), before `delete_job`:

```python
    @router.post("/api/jobs/{job_id}/stop")
    def stop_job(job_id: str):
        job = db.get_job(db_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        if job["status"] == "pending":
            db.update_job_status(db_path, job_id, "stopped")
        elif job["status"] == "processing":
            db.set_job_cancel_requested(db_path, job_id, True)
        else:
            raise HTTPException(
                status_code=409, detail="El job no se puede detener en su estado actual"
            )
        return {"stopped": True}

    @router.post("/api/jobs/{job_id}/resume")
    def resume_job(job_id: str):
        job = db.get_job(db_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        if job["status"] not in ("failed", "stopped"):
            raise HTTPException(
                status_code=409, detail="El job no se puede reanudar en su estado actual"
            )
        db.reset_job_for_resume(db_path, job_id)
        return {"resumed": True}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 10: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add /stop and /resume endpoints for jobs"
```

- [ ] **Step 11: Write failing tests for partial download**

Append to `tests/test_routes_jobs.py`:

```python
def test_download_partial_includes_only_completed_files(client):
    test_client, db_path, storage_dir = client
    zip_bytes = _make_zip_bytes(
        {
            "episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n",
            "episode2.srt": "1\n00:00:00,000 --> 00:00:01,000\nHi\n\n",
        }
    )
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]

    files = db.get_job_files(db_path, job_id)
    file1 = next(f for f in files if f["filename"] == "episode1.srt")
    db.update_job_file_status(db_path, file1["id"], "completed")
    db.update_job_status(db_path, job_id, "failed", error_message="boom")

    output_dir = os.path.join(storage_dir, job_id, "output")
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "episode1.srt"), "w", encoding="utf-8") as f:
        f.write("translated content")

    response = test_client.get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.namelist() == ["episode1.srt"]


def test_download_returns_409_when_no_file_completed_yet(client):
    test_client, db_path, _storage_dir = client
    zip_bytes = _make_zip_bytes({"episode1.srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"})
    create_response = test_client.post(
        "/api/jobs",
        files={"file": ("movie.zip", zip_bytes, "application/zip")},
        data={"model": "llama3.1", "source_lang": "en"},
    )
    job_id = create_response.json()["id"]
    db.update_job_status(db_path, job_id, "failed", error_message="boom")

    response = test_client.get(f"/api/jobs/{job_id}/download")
    assert response.status_code == 409
```

- [ ] **Step 12: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -k "download_partial or no_file_completed_yet" -v`
Expected: FAIL — `test_download_partial_includes_only_completed_files` gets 409 (current code requires `completed`/`completed_with_errors` job status). `test_download_returns_409_when_no_file_completed_yet` currently already passes (same 409), which is fine — it's here to lock in behavior after the change.

- [ ] **Step 13: Update `download_job` in `app/routes/jobs.py`**

Replace the `download_job` route (currently `app/routes/jobs.py:86-99`) with:

```python
    @router.get("/api/jobs/{job_id}/download")
    def download_job(job_id: str):
        job = db.get_job(db_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")

        if job["status"] in ("completed", "completed_with_errors"):
            zip_path = os.path.join(storage_dir, job_id, "output.zip")
            if not os.path.isfile(zip_path):
                raise HTTPException(status_code=404, detail="El archivo de salida no existe")
            return FileResponse(
                zip_path,
                media_type="application/zip",
                filename=f"traducido_{job['original_zip_name']}",
            )

        completed_filenames = [
            f["filename"]
            for f in db.get_job_files(db_path, job_id)
            if f["status"] in ("completed", "completed_with_errors")
        ]
        if not completed_filenames:
            raise HTTPException(status_code=409, detail="El job aún no ha terminado")

        output_dir = os.path.join(storage_dir, job_id, "output")
        partial_zip_path = os.path.join(storage_dir, job_id, "partial.zip")
        zip_utils.create_zip_subset(output_dir, completed_filenames, partial_zip_path)

        return FileResponse(
            partial_zip_path,
            media_type="application/zip",
            filename=f"parcial_{job['original_zip_name']}",
        )
```

- [ ] **Step 14: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS (all tests in the file, including the two pre-existing download tests — they use job status `completed`, which still goes through the unchanged first branch)

- [ ] **Step 15: Run the full test suite**

Run: `uv run pytest -v`
Expected: PASS (every test in the project)

- [ ] **Step 16: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: serve completed files from an unfinished job as a partial download"
```

---

### Task 6: Frontend — Detener/Reanudar buttons and partial download link

**Files:**
- Modify: `static/app.js`

**Interfaces:**
- Consumes: `GET /api/jobs` (`job.status`, `job.processed_files`), `POST /api/jobs/{id}/stop`, `POST /api/jobs/{id}/resume`, `GET /api/jobs/{id}/download` (all from Task 5).
- No exports — this is the leaf of the dependency chain.

There's no JS test harness in this repo (`CLAUDE.md` lists only `pytest`
for the test suite), so this task is verified manually against the running
app instead of an automated test, per this project's testing setup.

- [ ] **Step 1: Update `renderJobRow`'s action cell in `static/app.js`**

Replace the action-cell block (currently `static/app.js:112-118`, the `if
(job.status === "completed" ...)` download link) with:

```javascript
  const actionCell = document.createElement("td");

  if (job.status === "completed" || job.status === "completed_with_errors") {
    const link = document.createElement("a");
    link.href = `/api/jobs/${job.id}/download`;
    link.textContent = "Descargar";
    actionCell.appendChild(link);
  } else if (
    job.processed_files > 0 &&
    (job.status === "processing" || job.status === "failed" || job.status === "stopped")
  ) {
    const link = document.createElement("a");
    link.href = `/api/jobs/${job.id}/download`;
    link.textContent = "Descargar parcial";
    actionCell.appendChild(link);
  }

  if (job.status === "pending" || job.status === "processing") {
    const stopButton = document.createElement("button");
    stopButton.textContent = "Detener";
    stopButton.addEventListener("click", async () => {
      try {
        const response = await fetch(`/api/jobs/${job.id}/stop`, { method: "POST" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await refreshJobs();
      } catch (err) {
        alert("Error al detener el trabajo");
      }
    });
    actionCell.appendChild(stopButton);
  }

  if (job.status === "failed" || job.status === "stopped") {
    const resumeButton = document.createElement("button");
    resumeButton.textContent = "Reanudar";
    resumeButton.addEventListener("click", async () => {
      try {
        const response = await fetch(`/api/jobs/${job.id}/resume`, { method: "POST" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await refreshJobs();
      } catch (err) {
        alert("Error al reanudar el trabajo");
      }
    });
    actionCell.appendChild(resumeButton);
  }
```

This keeps the existing "Borrar" button code right after it (currently
`static/app.js:120-132`) untouched.

- [ ] **Step 2: Manually verify in the running app**

Run: `export DB_PATH=./dev-data/jobs.db && export STORAGE_DIR=./dev-data/storage && uv run uvicorn app.main:app --reload`

With a real or fake Ollama reachable at the configured URL:
1. Upload a job with at least 2 `.srt` files, confirm "Detener" appears
   while `pending`/`processing` and works (job ends up `stopped`, "Reanudar"
   appears).
2. Click "Reanudar" on the stopped job, confirm it goes back to
   `processing` and finishes normally.
3. Stop Ollama mid-job (or point `OLLAMA_BASE_URL` at an unreachable host)
   to force a failure, confirm the job cycles `pending` → `processing` up
   to 3 times (visible via `retry_count` growing if you inspect
   `dev-data/jobs.db`) before landing on `failed`, and that "Reanudar"
   appears and works from there.
4. On a job with at least one fully-translated file but not yet finished
   (or `failed`/`stopped`), confirm "Descargar parcial" appears and
   downloads a zip containing only the finished `.srt` file(s).

If any step doesn't work as described, fix `static/app.js` and re-verify
before moving on — do not claim this task complete without having run
these steps.

- [ ] **Step 3: Commit**

```bash
git add static/app.js
git commit -m "feat: add stop/resume controls and partial download link to job list"
```

---

## Post-plan note

This plan does not retroactively recover the specific job mentioned in the
original bug report — its `job_file_blocks` never existed (the table is new)
and its interrupted file has no persisted per-block progress. Once Task 5 is
deployed, its already-`completed` files become downloadable via the new
partial-download branch, and "Reanudar" will re-run only the file that was
mid-translation from scratch (skipping the rest, per Task 4's skip-if-completed
logic).
