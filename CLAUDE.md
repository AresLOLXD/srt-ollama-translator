# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Local web app that translates `.srt` subtitle files to Spanish using a locally
running [Ollama](https://ollama.com) model. FastAPI backend, vanilla JS/HTML/CSS
frontend served as static files, SQLite for job persistence.

The `src/srt_ollama_translator/` package is a leftover `uv init` stub (unused
"Hello world"); all real code lives under `app/`.

## Commands

```bash
# Install deps
uv sync

# Run locally (no container)
export DB_PATH=./dev-data/jobs.db
export STORAGE_DIR=./dev-data/storage
uv run uvicorn app.main:app --reload

# Run all tests
uv run pytest -v

# Run a single test file / test
uv run pytest tests/test_translator_translate.py -v
uv run pytest tests/test_translator_translate.py::test_name -v

# Run via container (Docker or Podman)
docker compose up --build
podman-compose up --build
```

There is no lint/format command configured in this repo.

## Architecture

**Request flow:** `POST /api/jobs` (app/routes/jobs.py) accepts a zip of `.srt`
files, extracts it (`app/zip_utils.py`, zip-slip protected), pre-counts
translation blocks per file, and writes a `jobs` row plus one `job_files` row
per subtitle file — all with status `pending`. The actual translation happens
out-of-band in a background asyncio task.

**Background worker** (`app/queue_worker.py`): `worker_loop`, started in
`app/main.py`'s lifespan handler, polls the DB every 2s for the oldest pending
job, processes it fully (one `.srt` file at a time, writing outputs to
`storage/<job_id>/output/`), zips the output directory, and marks the job
`completed` / `completed_with_errors` / `failed`. A new `OllamaClient` is
created per job so config changes (base URL) take effect on the next job
without restarting. On process crash, `reset_stale_processing_jobs` (called at
startup) resets any job/file stuck in `processing` back to `pending` so it's
retried from scratch.

**Translation unit is the block, not the file** (`app/translator.py`): each
`.srt` file's subtitles are chunked into blocks of 25 (`split_into_blocks`).
Each block is sent to Ollama as one prompt (`build_prompt`, includes the
filename for context) and the model is expected to return one `[N] text` line
per subtitle. `translate_block` retries up to 3 times if the response is
missing indices; any indices still missing after retries are left untranslated
in the output and counted as a "failed block" for that file. This per-block
retry/failure model — not per-file — is why job progress is tracked as
`translated_blocks` / `failed_blocks` on `job_files` and surfaced live via
`current_file` in `GET /api/jobs`.

**Ollama base URL resolution** (`app/config.py`): precedence is
DB `config` table value → `OLLAMA_BASE_URL` env var → hardcoded default
(`http://host.containers.internal:11434`, for reaching host-installed Ollama
from inside a container). The DB value is set via `POST /api/config` from the
web UI's "Configuración" section and persists across restarts.

**DB access pattern** (`app/db.py`): no ORM, no connection pooling — every
function opens a new sqlite3 connection (WAL mode), executes, and closes. This
is intentional given SQLite + single-worker usage; don't introduce a
connection pool or ORM without reason.

**Route modules are factories**: each module under `app/routes/` exposes
`get_router(db_path, ...)` returning an `APIRouter`, rather than importing a
global `db_path`. `app/main.py` wires `DB_PATH`/`STORAGE_DIR` (env vars,
defaulting to `/data/db/jobs.db` and `/data/storage` for the container) into
each router at startup.

**Tests** construct `OllamaClient` with a fake `httpx.AsyncBaseTransport`
(the `transport` constructor param) instead of hitting a real Ollama server —
follow this pattern for new tests touching the client.
