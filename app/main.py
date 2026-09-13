import asyncio
import logging
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

logging.basicConfig(level=logging.INFO)

DB_PATH = os.environ.get("DB_PATH", "/data/db/jobs.db")
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/data/storage")


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(STORAGE_DIR, exist_ok=True)
    db.init_db(DB_PATH)
    db.reset_stale_processing_jobs(DB_PATH)

    worker_task = asyncio.create_task(
        worker_loop(DB_PATH, STORAGE_DIR, ollama_client_factory=OllamaClient)
    )
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)

app.include_router(jobs_routes.get_router(DB_PATH, STORAGE_DIR))
app.include_router(models_routes.get_router(DB_PATH))
app.include_router(config_routes.get_router(DB_PATH))


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


if os.path.isdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
