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

        try:
            srt_files = zip_utils.extract_zip(zip_path, input_dir)
        except ValueError as exc:
            shutil.rmtree(job_dir)
            raise HTTPException(status_code=400, detail=f"Zip inválido: {exc}") from exc

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
