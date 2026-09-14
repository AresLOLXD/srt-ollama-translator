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
