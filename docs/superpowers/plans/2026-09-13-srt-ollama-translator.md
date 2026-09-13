# SRT Ollama Translator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted web app that lets a single local user upload a `.zip` of `.srt` subtitle files, queue them for translation to Spanish via a local Ollama model, watch real-time progress, and download the translated `.zip`.

**Architecture:** FastAPI backend (managed with `uv`) serves a small REST API and a static vanilla-JS frontend. A SQLite-backed queue holds jobs; a single asyncio background worker processes jobs one at a time, calling Ollama's HTTP API in per-file translation blocks with index-tagged retries. The whole backend ships as a container image (Docker/Podman compatible) that talks to Ollama running on the host via a configurable URL.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, httpx, `srt` (SRT parsing library), SQLite (stdlib `sqlite3`), pytest + pytest-asyncio, vanilla HTML/CSS/JS, Docker/Podman.

**Spec:** `docs/superpowers/specs/2026-09-13-srt-ollama-translator-design.md`

## Global Constraints

- Single user, no authentication (per spec "Alcance y restricciones").
- Ollama runs on the host, never inside the container; the app talks to it over HTTP using a configurable base URL (env var `OLLAMA_BASE_URL`, default `http://host.containers.internal:11434`, overridable at runtime via `POST /api/config`).
- Job queue must be persisted in SQLite (`jobs`, `job_files`, `config` tables) so it survives a backend restart.
- Jobs are processed strictly sequentially, one at a time, in arrival order.
- A translation block that fails after 3 attempts leaves the original-language lines in place and is counted in `failed_blocks`; it must never abort the whole job.
- Package management is `uv` (`pyproject.toml` + `uv.lock`), not raw pip/requirements.txt.
- Ships as a container image compatible with both Docker and Podman, using volumes `/data/db` and `/data/storage` for persistence.

---

## File Structure

```
pyproject.toml
uv.lock
Dockerfile
docker-compose.yml
.gitignore
README.md
app/
  __init__.py
  main.py            # Task 10
  db.py              # Task 2
  ollama_client.py   # Task 3
  translator.py       # Tasks 4-6
  zip_utils.py        # Task 6
  queue_worker.py     # Task 7
  routes/
    __init__.py
    models.py         # Task 8
    config.py          # Task 8
    jobs.py            # Task 9
static/
  index.html           # Task 11
  app.js               # Task 11
  styles.css           # Task 11
tests/
  __init__.py
  test_db.py
  test_ollama_client.py
  test_translator_blocks.py
  test_translator_translate.py
  test_zip_utils.py
  test_queue_worker.py
  test_routes_models_config.py
  test_routes_jobs.py
```

---

### Task 1: Project scaffolding with uv + FastAPI health check

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `tests/__init__.py`
- Create: `tests/test_main.py`

**Interfaces:**
- Produces: FastAPI app instance `app` in `app/main.py`, importable as `from app.main import app`; a `GET /api/health` endpoint returning `{"status": "ok"}`.

- [ ] **Step 1: Initialize the uv project**

Run:
```bash
uv init --name srt-ollama-translator --python 3.12 --no-readme
```
This creates `pyproject.toml` and `.python-version`. Then add dependencies:
```bash
uv add fastapi "uvicorn[standard]" httpx srt python-multipart
uv add --dev pytest pytest-asyncio
```

- [ ] **Step 2: Add `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
storage/
*.db
*.db-journal
*.db-wal
*.db-shm
.env
```

- [ ] **Step 3: Write the failing test for the health endpoint**

`tests/test_main.py`:
```python
from fastapi.testclient import TestClient
from app.main import app


def test_health_check_returns_ok():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL (import error, `app/main.py` does not exist yet)

- [ ] **Step 5: Create `app/__init__.py` (empty) and `app/main.py`**

`app/__init__.py`: empty file.

`app/main.py`:
```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/api/health")
def health_check():
    return {"status": "ok"}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .python-version app/ tests/
git commit -m "chore: scaffold uv project with FastAPI health check"
```

---

### Task 2: SQLite data access layer

**Files:**
- Create: `app/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces:
  - `init_db(db_path: str) -> None`
  - `create_job(db_path: str, job_id: str, original_zip_name: str, model: str, source_lang: str, total_files: int) -> None`
  - `get_job(db_path: str, job_id: str) -> dict | None`
  - `list_jobs(db_path: str) -> list[dict]`
  - `update_job_status(db_path: str, job_id: str, status: str, error_message: str | None = None) -> None`
  - `increment_job_processed_files(db_path: str, job_id: str) -> None`
  - `delete_job(db_path: str, job_id: str) -> None`
  - `create_job_file(db_path: str, file_id: str, job_id: str, filename: str, total_blocks: int) -> None`
  - `get_job_files(db_path: str, job_id: str) -> list[dict]`
  - `update_job_file_status(db_path: str, file_id: str, status: str) -> None`
  - `update_job_file_progress(db_path: str, file_id: str, translated_blocks: int, failed_blocks: int) -> None`
  - `get_next_pending_job(db_path: str) -> dict | None`
  - `get_config(db_path: str, key: str, default: str | None = None) -> str | None`
  - `set_config(db_path: str, key: str, value: str) -> None`
  - Job status values: `"pending"`, `"processing"`, `"completed"`, `"completed_with_errors"`, `"failed"`.
  - Job file status values: `"pending"`, `"processing"`, `"completed"`, `"completed_with_errors"`.

- [ ] **Step 1: Write failing tests for job + job_file lifecycle**

`tests/test_db.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL (`app.db` does not exist)

- [ ] **Step 3: Implement `app/db.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat: add SQLite data access layer for jobs, job files and config"
```

---

### Task 3: Ollama HTTP client

**Files:**
- Create: `app/ollama_client.py`
- Test: `tests/test_ollama_client.py`

**Interfaces:**
- Produces:
  - `class OllamaClient:`
    - `__init__(self, base_url: str, timeout: float = 120.0, transport: httpx.AsyncBaseTransport | None = None)`
    - `async def list_models(self) -> list[str]`
    - `async def chat(self, model: str, prompt: str) -> str`

- [ ] **Step 1: Write failing tests using `httpx.MockTransport`**

`tests/test_ollama_client.py`:
```python
import httpx
import pytest
from app.ollama_client import OllamaClient


@pytest.mark.asyncio
async def test_list_models_returns_model_names():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "llama3.1"}, {"name": "mistral"}]})

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    models = await client.list_models()
    assert models == ["llama3.1", "mistral"]


@pytest.mark.asyncio
async def test_chat_sends_model_and_prompt_and_returns_content():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json={"message": {"content": "hola mundo"}})

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    result = await client.chat("llama3.1", "translate: hello world")

    assert result == "hola mundo"
    assert b"llama3.1" in captured["body"]
    assert b"translate: hello world" in captured["body"]


@pytest.mark.asyncio
async def test_list_models_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = OllamaClient("http://fake-ollama:11434", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        await client.list_models()
```

- [ ] **Step 2: Add `pytest-asyncio` config and run tests to verify they fail**

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Run: `uv run pytest tests/test_ollama_client.py -v`
Expected: FAIL (`app.ollama_client` does not exist)

- [ ] **Step 3: Implement `app/ollama_client.py`**

```python
import httpx


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.timeout, transport=self._transport)

    async def list_models(self) -> list[str]:
        async with self._client() as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]

    async def chat(self, model: str, prompt: str) -> str:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ollama_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ollama_client.py tests/test_ollama_client.py pyproject.toml uv.lock
git commit -m "feat: add Ollama HTTP client wrapper"
```

---

### Task 4: SRT block splitting and prompt building

**Files:**
- Create: `app/translator.py`
- Test: `tests/test_translator_blocks.py`

**Interfaces:**
- Produces:
  - `@dataclass class SubtitleBlock: subs: list[srt.Subtitle]`
  - `def split_into_blocks(subs: list[srt.Subtitle], block_size: int = 25) -> list[SubtitleBlock]`
  - `def build_prompt(block: SubtitleBlock, source_lang: str) -> str`
  - `def parse_translated_response(response: str) -> dict[int, str]`

- [ ] **Step 1: Write failing tests for block splitting, prompt building and response parsing**

`tests/test_translator_blocks.py`:
```python
import srt
from datetime import timedelta
from app.translator import SubtitleBlock, split_into_blocks, build_prompt, parse_translated_response


def make_subs(count: int) -> list[srt.Subtitle]:
    return [
        srt.Subtitle(
            index=i + 1,
            start=timedelta(seconds=i),
            end=timedelta(seconds=i + 1),
            content=f"Line {i + 1}",
        )
        for i in range(count)
    ]


def test_split_into_blocks_respects_block_size():
    subs = make_subs(55)
    blocks = split_into_blocks(subs, block_size=25)
    assert len(blocks) == 3
    assert [len(b.subs) for b in blocks] == [25, 25, 5]


def test_split_into_blocks_empty_list_returns_no_blocks():
    assert split_into_blocks([], block_size=25) == []


def test_build_prompt_includes_indices_and_content():
    block = SubtitleBlock(subs=make_subs(2))
    prompt = build_prompt(block, source_lang="en")
    assert "[1] Line 1" in prompt
    assert "[2] Line 2" in prompt
    assert "en" in prompt


def test_build_prompt_describes_auto_detect_language():
    block = SubtitleBlock(subs=make_subs(1))
    prompt = build_prompt(block, source_lang="auto")
    assert "detectado autom" in prompt.lower()


def test_parse_translated_response_extracts_indexed_lines():
    response = "[1] Hola\n[2] Mundo\n"
    result = parse_translated_response(response)
    assert result == {1: "Hola", 2: "Mundo"}


def test_parse_translated_response_ignores_malformed_lines():
    response = "[1] Hola\nesto no tiene indice\n[3] Adios\n"
    result = parse_translated_response(response)
    assert result == {1: "Hola", 3: "Adios"}


def test_parse_translated_response_empty_string_returns_empty_dict():
    assert parse_translated_response("") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_blocks.py -v`
Expected: FAIL (`app.translator` does not exist)

- [ ] **Step 3: Implement the block/prompt/parsing pieces in `app/translator.py`**

```python
import re
from dataclasses import dataclass

import srt

LINE_PATTERN = re.compile(r"^\[(\d+)\]\s*(.*)$")


@dataclass
class SubtitleBlock:
    subs: list[srt.Subtitle]


def split_into_blocks(subs: list[srt.Subtitle], block_size: int = 25) -> list[SubtitleBlock]:
    return [
        SubtitleBlock(subs=subs[i : i + block_size])
        for i in range(0, len(subs), block_size)
    ]


def build_prompt(block: SubtitleBlock, source_lang: str) -> str:
    source_desc = "el idioma detectado automáticamente" if source_lang == "auto" else source_lang
    lines = "\n".join(f"[{sub.index}] {sub.content}" for sub in block.subs)
    return (
        "Traduce al español los siguientes subtítulos de una película o serie. "
        f"El idioma de origen es {source_desc}. "
        "Devuelve EXACTAMENTE una línea por cada subtítulo recibido, en el formato "
        '"[N] texto traducido", preservando el número N tal cual. '
        "No agregues explicaciones, encabezados ni texto adicional fuera de esas líneas.\n\n"
        f"{lines}"
    )


def parse_translated_response(response: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for line in response.splitlines():
        match = LINE_PATTERN.match(line.strip())
        if match:
            result[int(match.group(1))] = match.group(2)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_blocks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_blocks.py
git commit -m "feat: add SRT block splitting, prompt building and response parsing"
```

---

### Task 5: Block translation with retries

**Files:**
- Modify: `app/translator.py`
- Test: `tests/test_translator_translate.py`

**Interfaces:**
- Consumes: `SubtitleBlock`, `build_prompt`, `parse_translated_response` from Task 4; any object exposing `async def chat(self, model: str, prompt: str) -> str` (duck-typed `OllamaClient`).
- Produces: `async def translate_block(client, model: str, block: SubtitleBlock, source_lang: str, max_retries: int = 3) -> dict[int, str]`

- [ ] **Step 1: Write failing tests using a fake client**

`tests/test_translator_translate.py`:
```python
import pytest
import srt
from datetime import timedelta
from app.translator import SubtitleBlock, translate_block


def make_block(count: int) -> SubtitleBlock:
    subs = [
        srt.Subtitle(
            index=i + 1,
            start=timedelta(seconds=i),
            end=timedelta(seconds=i + 1),
            content=f"Line {i + 1}",
        )
        for i in range(count)
    ]
    return SubtitleBlock(subs=subs)


class FakeClient:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def chat(self, model: str, prompt: str) -> str:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@pytest.mark.asyncio
async def test_translate_block_succeeds_on_first_try():
    client = FakeClient(["[1] Hola\n[2] Mundo"])
    block = make_block(2)
    result = await translate_block(client, "llama3.1", block, "en")
    assert result == {1: "Hola", 2: "Mundo"}
    assert client.calls == 1


@pytest.mark.asyncio
async def test_translate_block_retries_when_lines_missing():
    client = FakeClient(["[1] Hola", "[1] Hola\n[2] Mundo"])
    block = make_block(2)
    result = await translate_block(client, "llama3.1", block, "en")
    assert result == {1: "Hola", 2: "Mundo"}
    assert client.calls == 2


@pytest.mark.asyncio
async def test_translate_block_gives_up_after_max_retries():
    client = FakeClient(["[1] Hola"])
    block = make_block(2)
    result = await translate_block(client, "llama3.1", block, "en", max_retries=3)
    assert result == {1: "Hola"}
    assert client.calls == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_translate.py -v`
Expected: FAIL (`translate_block` not defined)

- [ ] **Step 3: Implement `translate_block` in `app/translator.py`**

Append to `app/translator.py`:
```python
async def translate_block(
    client, model: str, block: SubtitleBlock, source_lang: str, max_retries: int = 3
) -> dict[int, str]:
    expected_indices = {sub.index for sub in block.subs}
    translations: dict[int, str] = {}

    for _attempt in range(max_retries):
        prompt = build_prompt(block, source_lang)
        response = await client.chat(model, prompt)
        translations.update(parse_translated_response(response))
        if expected_indices.issubset(translations.keys()):
            break

    return {index: text for index, text in translations.items() if index in expected_indices}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_translate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/translator.py tests/test_translator_translate.py
git commit -m "feat: add per-block translation with retry on missing lines"
```

---

### Task 6: Full-file translation and zip utilities

**Files:**
- Modify: `app/translator.py`
- Create: `app/zip_utils.py`
- Test: `tests/test_translator_translate.py` (append)
- Test: `tests/test_zip_utils.py`

**Interfaces:**
- Consumes: `split_into_blocks`, `translate_block` from Tasks 4-5.
- Produces:
  - `async def translate_srt_file(client, model: str, source_lang: str, input_path: str, output_path: str, on_block_translated=None, block_size: int = 25) -> tuple[int, int]` — returns `(total_blocks, failed_blocks)`.
  - `def extract_zip(zip_path: str, dest_dir: str) -> list[str]` — returns sorted relative paths of `.srt` files found recursively.
  - `def create_zip(src_dir: str, zip_path: str) -> None`

- [ ] **Step 1: Write failing test for `translate_srt_file`**

Append to `tests/test_translator_translate.py`:
```python
from app.translator import translate_srt_file


@pytest.mark.asyncio
async def test_translate_srt_file_writes_translated_output(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola\n[2] Mundo"])
    progress_calls = []

    total_blocks, failed_blocks = await translate_srt_file(
        client,
        "llama3.1",
        "en",
        str(input_path),
        str(output_path),
        on_block_translated=lambda done, total: progress_calls.append((done, total)),
        block_size=25,
    )

    assert total_blocks == 1
    assert failed_blocks == 0
    assert progress_calls == [(1, 1)]
    output_text = output_path.read_text(encoding="utf-8")
    assert "Hola" in output_text
    assert "Mundo" in output_text


@pytest.mark.asyncio
async def test_translate_srt_file_keeps_original_text_for_failed_lines(tmp_path):
    input_path = tmp_path / "input.srt"
    input_path.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nWorld\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "output.srt"
    client = FakeClient(["[1] Hola"])

    total_blocks, failed_blocks = await translate_srt_file(
        client, "llama3.1", "en", str(input_path), str(output_path)
    )

    assert total_blocks == 1
    assert failed_blocks == 1
    output_text = output_path.read_text(encoding="utf-8")
    assert "Hola" in output_text
    assert "World" in output_text
```

- [ ] **Step 2: Write failing tests for zip utilities**

`tests/test_zip_utils.py`:
```python
import os
import zipfile
from app.zip_utils import extract_zip, create_zip


def test_extract_zip_finds_srt_files_recursively(tmp_path):
    zip_path = tmp_path / "input.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("episode1.srt", "content1")
        zf.writestr("subdir/episode2.srt", "content2")
        zf.writestr("readme.txt", "not a subtitle")

    dest_dir = tmp_path / "extracted"
    srt_files = extract_zip(str(zip_path), str(dest_dir))

    assert srt_files == ["episode1.srt", os.path.join("subdir", "episode2.srt")]
    assert (dest_dir / "episode1.srt").read_text() == "content1"


def test_extract_zip_returns_empty_list_when_no_srt_files(tmp_path):
    zip_path = tmp_path / "input.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", "no subtitles here")

    dest_dir = tmp_path / "extracted"
    assert extract_zip(str(zip_path), str(dest_dir)) == []


def test_create_zip_packages_all_files_preserving_structure(tmp_path):
    src_dir = tmp_path / "output"
    os.makedirs(src_dir / "subdir")
    (src_dir / "episode1.srt").write_text("translated1")
    (src_dir / "subdir" / "episode2.srt").write_text("translated2")

    zip_path = tmp_path / "result.zip"
    create_zip(str(src_dir), str(zip_path))

    with zipfile.ZipFile(zip_path) as zf:
        names = sorted(zf.namelist())
        assert names == ["episode1.srt", os.path.join("subdir", "episode2.srt")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_translator_translate.py tests/test_zip_utils.py -v`
Expected: FAIL (`translate_srt_file` and `app.zip_utils` not defined)

- [ ] **Step 4: Implement `translate_srt_file` in `app/translator.py`**

Append to `app/translator.py`:
```python
from typing import Callable


async def translate_srt_file(
    client,
    model: str,
    source_lang: str,
    input_path: str,
    output_path: str,
    on_block_translated: Callable[[int, int], None] | None = None,
    block_size: int = 25,
) -> tuple[int, int]:
    with open(input_path, encoding="utf-8") as f:
        subs = list(srt.parse(f.read()))

    blocks = split_into_blocks(subs, block_size=block_size)
    translated_by_index: dict[int, str] = {}
    failed_blocks = 0

    for position, block in enumerate(blocks, start=1):
        translations = await translate_block(client, model, block, source_lang)
        expected_indices = {sub.index for sub in block.subs}
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

- [ ] **Step 5: Implement `app/zip_utils.py`**

```python
import os
import zipfile


def extract_zip(zip_path: str, dest_dir: str) -> list[str]:
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)

    srt_files = []
    for root, _dirs, files in os.walk(dest_dir):
        for name in files:
            if name.lower().endswith(".srt"):
                full_path = os.path.join(root, name)
                srt_files.append(os.path.relpath(full_path, dest_dir))
    return sorted(srt_files)


def create_zip(src_dir: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_dir):
            for name in files:
                full_path = os.path.join(root, name)
                zf.write(full_path, os.path.relpath(full_path, src_dir))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_translator_translate.py tests/test_zip_utils.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/translator.py app/zip_utils.py tests/test_translator_translate.py tests/test_zip_utils.py
git commit -m "feat: add full SRT file translation and zip extraction/creation utilities"
```

---

### Task 7: Background queue worker

**Files:**
- Create: `app/queue_worker.py`
- Test: `tests/test_queue_worker.py`

**Interfaces:**
- Consumes: `db.*` from Task 2, `translator.translate_srt_file` from Task 6, `zip_utils.create_zip` from Task 6.
- Produces:
  - `async def process_job(db_path: str, storage_dir: str, ollama_client, job: dict) -> None`
  - `async def worker_loop(db_path: str, storage_dir: str, ollama_client_factory, poll_interval: float = 0.05) -> None` (runs forever; callers cancel the asyncio task to stop it)

- [ ] **Step 1: Write failing tests for `process_job`**

`tests/test_queue_worker.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_queue_worker.py -v`
Expected: FAIL (`app.queue_worker` does not exist)

- [ ] **Step 3: Implement `app/queue_worker.py`**

```python
import asyncio
import os

from app import db, zip_utils
from app.translator import translate_srt_file


async def process_job(db_path: str, storage_dir: str, ollama_client, job: dict) -> None:
    job_id = job["id"]
    db.update_job_status(db_path, job_id, "processing")
    job_dir = os.path.join(storage_dir, job_id)
    input_dir = os.path.join(job_dir, "input")
    output_dir = os.path.join(job_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    try:
        any_failed = False
        for job_file in db.get_job_files(db_path, job_id):
            db.update_job_file_status(db_path, job_file["id"], "processing")
            input_path = os.path.join(input_dir, job_file["filename"])
            output_path = os.path.join(output_dir, job_file["filename"])
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            def on_progress(done: int, _total: int, file_id: str = job_file["id"]) -> None:
                db.update_job_file_progress(db_path, file_id, done, 0)

            total_blocks, failed_blocks = await translate_srt_file(
                ollama_client,
                job["model"],
                job["source_lang"],
                input_path,
                output_path,
                on_block_translated=on_progress,
            )

            file_status = "completed_with_errors" if failed_blocks > 0 else "completed"
            if failed_blocks > 0:
                any_failed = True
            db.update_job_file_status(db_path, job_file["id"], file_status)
            db.update_job_file_progress(db_path, job_file["id"], total_blocks, failed_blocks)
            db.increment_job_processed_files(db_path, job_id)

        zip_path = os.path.join(job_dir, "output.zip")
        zip_utils.create_zip(output_dir, zip_path)
        db.update_job_status(
            db_path, job_id, "completed_with_errors" if any_failed else "completed"
        )
    except Exception as exc:  # noqa: BLE001 - job failures must never crash the worker loop
        db.update_job_status(db_path, job_id, "failed", error_message=str(exc))


async def worker_loop(
    db_path: str, storage_dir: str, ollama_client_factory, poll_interval: float = 2.0
) -> None:
    while True:
        job = db.get_next_pending_job(db_path)
        if job is None:
            await asyncio.sleep(poll_interval)
            continue
        base_url = db.get_config(
            db_path, "ollama_base_url", "http://host.containers.internal:11434"
        )
        ollama_client = ollama_client_factory(base_url)
        await process_job(db_path, storage_dir, ollama_client, job)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_queue_worker.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/queue_worker.py tests/test_queue_worker.py
git commit -m "feat: add sequential background queue worker"
```

---

### Task 8: API routes for models and config

**Files:**
- Create: `app/routes/__init__.py`
- Create: `app/routes/models.py`
- Create: `app/routes/config.py`
- Test: `tests/test_routes_models_config.py`

**Interfaces:**
- Consumes: `db.get_config`, `db.set_config` from Task 2; `OllamaClient` from Task 3.
- Produces:
  - `models.get_router(db_path: str) -> APIRouter` exposing `GET /api/models`
  - `config.get_router(db_path: str) -> APIRouter` exposing `GET /api/config` and `POST /api/config`

- [ ] **Step 1: Write failing tests**

`tests/test_routes_models_config.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_models_config.py -v`
Expected: FAIL (`app.routes` does not exist)

- [ ] **Step 3: Implement `app/routes/__init__.py`, `app/routes/config.py`, `app/routes/models.py`**

`app/routes/__init__.py`: empty file.

`app/routes/config.py`:
```python
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
```

`app/routes/models.py`:
```python
import os

from fastapi import APIRouter, HTTPException

from app import db
from app.ollama_client import OllamaClient

DEFAULT_OLLAMA_URL = "http://host.containers.internal:11434"


def get_router(db_path: str) -> APIRouter:
    router = APIRouter()

    @router.get("/api/models")
    async def list_models():
        default = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
        base_url = db.get_config(db_path, "ollama_base_url", default)
        client = OllamaClient(base_url)
        try:
            models = await client.list_models()
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"No se pudo conectar a Ollama: {exc}"
            ) from exc
        return {"models": models}

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_models_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/__init__.py app/routes/config.py app/routes/models.py tests/test_routes_models_config.py
git commit -m "feat: add config and models API routes"
```

---

### Task 9: API routes for jobs (upload, list, detail, download, delete)

**Files:**
- Create: `app/routes/jobs.py`
- Test: `tests/test_routes_jobs.py`

**Interfaces:**
- Consumes: `db.*` from Task 2, `zip_utils.extract_zip` from Task 6, `translator.split_into_blocks` from Task 4.
- Produces: `jobs.get_router(db_path: str, storage_dir: str) -> APIRouter` exposing:
  - `POST /api/jobs` (multipart: `file`, `model`, `source_lang`) → `{"id": job_id}`
  - `GET /api/jobs` → `{"jobs": [...]}`
  - `GET /api/jobs/{job_id}` → job dict with `"files"` key
  - `GET /api/jobs/{job_id}/download` → file response
  - `DELETE /api/jobs/{job_id}` → `{"deleted": true}`

- [ ] **Step 1: Write failing tests**

`tests/test_routes_jobs.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: FAIL (`app.routes.jobs` does not exist)

- [ ] **Step 3: Implement `app/routes/jobs.py`**

```python
import os
import shutil
import uuid

import srt as srt_lib
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import db, zip_utils
from app.translator import split_into_blocks


def get_router(db_path: str, storage_dir: str) -> APIRouter:
    router = APIRouter()

    @router.post("/api/jobs")
    def create_job(
        file: UploadFile = File(...),
        model: str = Form(...),
        source_lang: str = Form("auto"),
    ):
        job_id = str(uuid.uuid4())
        job_dir = os.path.join(storage_dir, job_id)
        input_dir = os.path.join(job_dir, "input")
        os.makedirs(input_dir, exist_ok=True)

        zip_path = os.path.join(job_dir, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(file.file.read())

        srt_files = zip_utils.extract_zip(zip_path, input_dir)
        if not srt_files:
            shutil.rmtree(job_dir)
            raise HTTPException(status_code=400, detail="El zip no contiene archivos .srt")

        db.create_job(db_path, job_id, file.filename, model, source_lang, len(srt_files))
        for filename in srt_files:
            with open(
                os.path.join(input_dir, filename), encoding="utf-8", errors="ignore"
            ) as f:
                subs = list(srt_lib.parse(f.read()))
            total_blocks = len(split_into_blocks(subs))
            db.create_job_file(db_path, str(uuid.uuid4()), job_id, filename, total_blocks)

        return {"id": job_id}

    @router.get("/api/jobs")
    def list_jobs():
        return {"jobs": db.list_jobs(db_path)}

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = db.get_job(db_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        job["files"] = db.get_job_files(db_path, job_id)
        return job

    @router.get("/api/jobs/{job_id}/download")
    def download_job(job_id: str):
        job = db.get_job(db_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        if job["status"] not in ("completed", "completed_with_errors"):
            raise HTTPException(status_code=409, detail="El job aún no ha terminado")
        zip_path = os.path.join(storage_dir, job_id, "output.zip")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"traducido_{job['original_zip_name']}",
        )

    @router.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str):
        job = db.get_job(db_path, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job no encontrado")
        db.delete_job(db_path, job_id)
        job_dir = os.path.join(storage_dir, job_id)
        if os.path.isdir(job_dir):
            shutil.rmtree(job_dir)
        return {"deleted": True}

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/jobs.py tests/test_routes_jobs.py
git commit -m "feat: add jobs API routes for upload, listing, detail, download and delete"
```

---

### Task 10: Wire the FastAPI app together with the background worker

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_main.py` (append)

**Interfaces:**
- Consumes: `db.init_db`, `queue_worker.worker_loop` (Task 7), `routes.jobs.get_router`, `routes.models.get_router`, `routes.config.get_router` (Tasks 8-9), `ollama_client.OllamaClient` (Task 3).
- Produces: `app` in `app/main.py` fully wired, reading `DB_PATH` and `STORAGE_DIR` env vars (defaults `/data/db/jobs.db` and `/data/storage`), starting/cancelling the worker via FastAPI lifespan.

- [ ] **Step 1: Write failing test asserting all routers are mounted**

Append to `tests/test_main.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main.py -v`
Expected: FAIL (routers not wired, env vars not read)

- [ ] **Step 3: Rewrite `app/main.py`**

```python
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.ollama_client import OllamaClient
from app.queue_worker import worker_loop
from app.routes import config as config_routes
from app.routes import jobs as jobs_routes
from app.routes import models as models_routes

DB_PATH = os.environ.get("DB_PATH", "/data/db/jobs.db")
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/data/storage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    db.init_db(DB_PATH)

    worker_task = asyncio.create_task(
        worker_loop(DB_PATH, STORAGE_DIR, ollama_client_factory=OllamaClient)
    )
    yield
    worker_task.cancel()


app = FastAPI(lifespan=lifespan)

app.include_router(jobs_routes.get_router(DB_PATH, STORAGE_DIR))
app.include_router(models_routes.get_router(DB_PATH))
app.include_router(config_routes.get_router(DB_PATH))


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


if os.path.isdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_main.py
git commit -m "feat: wire FastAPI app with routers and background worker lifespan"
```

---

### Task 11: Frontend (upload form, job queue table, config panel)

**Files:**
- Create: `static/index.html`
- Create: `static/app.js`
- Create: `static/styles.css`

**Interfaces:**
- Consumes: `GET /api/config`, `POST /api/config`, `GET /api/models`, `POST /api/jobs`, `GET /api/jobs`, `GET /api/jobs/{id}/download` from Tasks 8-9.
- No new backend interfaces are produced; this is a pure static frontend, verified manually (no JS test runner is introduced, per YAGNI — this is glue code over an already-tested API).

- [ ] **Step 1: Create `static/index.html`**

```html
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <title>Traductor de subtítulos</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <h1>Traductor de subtítulos SRT</h1>

  <section id="config-section">
    <h2>Configuración</h2>
    <label>
      URL de Ollama:
      <input type="text" id="ollama-url-input" />
    </label>
    <button id="save-config-btn">Guardar</button>
    <span id="config-status"></span>
  </section>

  <section id="upload-section">
    <h2>Nuevo trabajo</h2>
    <form id="upload-form">
      <label>
        Archivo .zip:
        <input type="file" id="zip-input" accept=".zip" required />
      </label>
      <label>
        Modelo:
        <select id="model-select"></select>
        <button type="button" id="refresh-models-btn">Refrescar modelos</button>
      </label>
      <label>
        Idioma de origen:
        <select id="source-lang-select">
          <option value="auto">Detectar automáticamente</option>
          <option value="en">Inglés</option>
          <option value="fr">Francés</option>
          <option value="de">Alemán</option>
          <option value="pt">Portugués</option>
          <option value="it">Italiano</option>
        </select>
      </label>
      <button type="submit">Encolar trabajo</button>
    </form>
    <span id="upload-status"></span>
  </section>

  <section id="jobs-section">
    <h2>Trabajos</h2>
    <table id="jobs-table">
      <thead>
        <tr>
          <th>Archivo</th>
          <th>Estado</th>
          <th>Progreso</th>
          <th>Acción</th>
        </tr>
      </thead>
      <tbody id="jobs-table-body"></tbody>
    </table>
  </section>

  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `static/styles.css`**

```css
body {
  font-family: system-ui, sans-serif;
  max-width: 800px;
  margin: 2rem auto;
  padding: 0 1rem;
}

section {
  margin-bottom: 2rem;
  padding: 1rem;
  border: 1px solid #ccc;
  border-radius: 8px;
}

label {
  display: block;
  margin-bottom: 0.75rem;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th, td {
  text-align: left;
  padding: 0.5rem;
  border-bottom: 1px solid #ddd;
}
```

- [ ] **Step 3: Create `static/app.js`**

```js
const configStatus = document.getElementById("config-status");
const uploadStatus = document.getElementById("upload-status");
const jobsTableBody = document.getElementById("jobs-table-body");
const modelSelect = document.getElementById("model-select");
const ollamaUrlInput = document.getElementById("ollama-url-input");

async function loadConfig() {
  const response = await fetch("/api/config");
  const data = await response.json();
  ollamaUrlInput.value = data.ollama_base_url;
}

async function saveConfig() {
  configStatus.textContent = "Guardando...";
  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ollama_base_url: ollamaUrlInput.value }),
  });
  configStatus.textContent = response.ok ? "Guardado" : "Error al guardar";
}

async function loadModels() {
  modelSelect.innerHTML = "";
  try {
    const response = await fetch("/api/models");
    if (!response.ok) throw new Error("No se pudo conectar a Ollama");
    const data = await response.json();
    for (const model of data.models) {
      const option = document.createElement("option");
      option.value = model;
      option.textContent = model;
      modelSelect.appendChild(option);
    }
  } catch (err) {
    const option = document.createElement("option");
    option.textContent = "No se pudieron cargar los modelos";
    modelSelect.appendChild(option);
  }
}

async function uploadJob(event) {
  event.preventDefault();
  const zipInput = document.getElementById("zip-input");
  const sourceLangSelect = document.getElementById("source-lang-select");

  const formData = new FormData();
  formData.append("file", zipInput.files[0]);
  formData.append("model", modelSelect.value);
  formData.append("source_lang", sourceLangSelect.value);

  uploadStatus.textContent = "Subiendo...";
  const response = await fetch("/api/jobs", { method: "POST", body: formData });
  if (response.ok) {
    uploadStatus.textContent = "Trabajo encolado";
    zipInput.value = "";
    await refreshJobs();
  } else {
    const error = await response.json();
    uploadStatus.textContent = `Error: ${error.detail}`;
  }
}

function renderJobRow(job) {
  const row = document.createElement("tr");

  const nameCell = document.createElement("td");
  nameCell.textContent = job.original_zip_name;
  row.appendChild(nameCell);

  const statusCell = document.createElement("td");
  statusCell.textContent = job.status;
  row.appendChild(statusCell);

  const progressCell = document.createElement("td");
  progressCell.textContent = `${job.processed_files} / ${job.total_files} archivos`;
  row.appendChild(progressCell);

  const actionCell = document.createElement("td");
  if (job.status === "completed" || job.status === "completed_with_errors") {
    const link = document.createElement("a");
    link.href = `/api/jobs/${job.id}/download`;
    link.textContent = "Descargar";
    actionCell.appendChild(link);
  }
  row.appendChild(actionCell);

  return row;
}

async function refreshJobs() {
  const response = await fetch("/api/jobs");
  const data = await response.json();
  jobsTableBody.innerHTML = "";
  for (const job of data.jobs) {
    jobsTableBody.appendChild(renderJobRow(job));
  }
}

document.getElementById("save-config-btn").addEventListener("click", saveConfig);
document.getElementById("refresh-models-btn").addEventListener("click", loadModels);
document.getElementById("upload-form").addEventListener("submit", uploadJob);

loadConfig();
loadModels();
refreshJobs();
setInterval(refreshJobs, 3000);
```

- [ ] **Step 4: Manually verify the frontend**

Run: `uv run uvicorn app.main:app --reload` (with `DB_PATH`/`STORAGE_DIR` pointing at local writable paths, e.g. `export DB_PATH=./dev-data/jobs.db STORAGE_DIR=./dev-data/storage`), then open `http://localhost:8000` in a browser. Confirm:
- The config panel loads and can save a new Ollama URL.
- "Refrescar modelos" populates the select (or shows the fallback message if Ollama is unreachable).
- Uploading a zip with `.srt` files creates a row in the jobs table.
- The jobs table updates its status/progress every 3 seconds via polling.

- [ ] **Step 5: Commit**

```bash
git add static/
git commit -m "feat: add frontend for config, upload and job queue monitoring"
```

---

### Task 12: Containerization and README

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `README.md`

**Interfaces:**
- Consumes: the full app built in Tasks 1-11.
- Produces: a runnable container image exposing port `8000`, with `/data/db` and `/data/storage` as volumes and `OLLAMA_BASE_URL` as a configurable env var.

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /srv/app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
COPY static/ ./static/

ENV DB_PATH=/data/db/jobs.db
ENV STORAGE_DIR=/data/storage
ENV OLLAMA_BASE_URL=http://host.containers.internal:11434
ENV PORT=8000

VOLUME ["/data/db", "/data/storage"]
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  srt-translator:
    build: .
    ports:
      - "8000:8000"
    environment:
      - OLLAMA_BASE_URL=http://host.containers.internal:11434
    volumes:
      - ./data/db:/data/db
      - ./data/storage:/data/storage
    extra_hosts:
      - "host.containers.internal:host-gateway"
```

- [ ] **Step 3: Create `README.md`**

```markdown
# srt-ollama-translator

Aplicación web local para traducir archivos `.srt` (subtítulos) al español
usando un modelo de [Ollama](https://ollama.com) corriendo en tu máquina.

## Requisitos

- Ollama corriendo localmente con al menos un modelo descargado
  (`ollama pull llama3.1`, por ejemplo).
- Docker o Podman (con `docker-compose`/`podman-compose`).

## Uso con Podman

```bash
podman-compose up --build
```

## Uso con Docker

```bash
docker compose up --build
```

Luego abre `http://localhost:8000`.

## Configurar la URL de Ollama

Por defecto la app espera a Ollama en `http://host.containers.internal:11434`
(la forma estándar de referirse al host desde un contenedor en Podman/Docker
Desktop). Si tu configuración es distinta:

- Cambia la variable de entorno `OLLAMA_BASE_URL` en `docker-compose.yml`, o
- Ajusta la URL directamente desde la sección "Configuración" de la interfaz
  web una vez la app está corriendo (se guarda en la base de datos y persiste
  entre reinicios).

## Desarrollo local (sin contenedor)

```bash
uv sync
export DB_PATH=./dev-data/jobs.db
export STORAGE_DIR=./dev-data/storage
uv run uvicorn app.main:app --reload
```

## Tests

```bash
uv run pytest -v
```
```

- [ ] **Step 4: Build and smoke-test the image**

Run:
```bash
podman build -t srt-ollama-translator .
podman run --rm -p 8000:8000 \
  -v ./data/db:/data/db \
  -v ./data/storage:/data/storage \
  srt-ollama-translator
```
Expected: container starts, `curl http://localhost:8000/api/health` returns `{"status":"ok"}`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml README.md
git commit -m "chore: add Dockerfile, compose file and README for containerized deployment"
```

- [ ] **Step 6: Create the GitHub repository and push**

Run:
```bash
gh repo create srt-ollama-translator --private --source=. --remote=origin
git push -u origin main
```
Expected: repository `srt-ollama-translator` created privately under the user's GitHub account, `main` branch pushed.

---

## Self-Review Notes

- **Spec coverage:** upload+extract (Task 9), model selection via live Ollama query (Tasks 3, 8), block-based translation with index tagging and retries (Tasks 4-5), sequential SQLite-backed queue with resume-on-restart via `get_next_pending_job` (Tasks 2, 7), progress detail per file/block (Tasks 2, 7, 11), download of result zip (Tasks 6, 9), configurable Ollama URL via env + UI (Tasks 8, 11), containerization with Ollama on host (Task 12), `uv` package management (Task 1), GitHub repo creation (Task 12) — all covered.
- **Placeholder scan:** no TBD/TODO markers; every step has runnable code or an explicit manual-verification procedure (Task 11 frontend, Task 12 container smoke test) since those layers have no automated test harness introduced in this plan.
- **Type/signature consistency:** `db_path: str` is threaded consistently through `db.py`, `queue_worker.py`, and both route factories; `translate_srt_file`'s `on_block_translated(done, total)` signature matches its usage in `queue_worker.process_job`; job/job_file status string literals (`pending`, `processing`, `completed`, `completed_with_errors`, `failed`) are consistent across `db.py`, `queue_worker.py`, `routes/jobs.py`, and `static/app.js`.
