import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from config import app_config
from pipeline.document_generator import DOCUMENT_TYPES
from pipeline.orchestrator import regenerate_document, run_pipeline
from storage.file_manager import FileManager
from storage.job_store import JobStatus, JobStore

ALLOWED_EXTENSIONS = {".mp3", ".m4a", ".wav", ".webm", ".mp4", ".ogg", ".flac", ".aac"}

load_dotenv()

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Session Digest")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

job_store = JobStore()
file_manager = FileManager()

# Keep references to background tasks to prevent garbage collection
_background_tasks: set[asyncio.Task] = set()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    jobs = sorted(job_store.list_jobs(), key=lambda j: j.created_at, reverse=True)
    return templates.TemplateResponse(
        "jobs.html",
        {"request": request, "jobs": jobs, "doc_types": DOCUMENT_TYPES},
    )


MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    if not app_config.has_any_key:
        return JSONResponse({"error": "OpenAI APIキーが設定されていません"}, status_code=400)
    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return HTMLResponse(
            f"対応していないファイル形式です。対応形式: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
            status_code=400,
        )

    # Check file size via Content-Length header if available
    if file.size and file.size > MAX_UPLOAD_SIZE:
        return HTMLResponse("ファイルサイズが上限(2GB)を超えています", status_code=413)

    job_id = job_store.create_job(filename=filename)
    file_path = await file_manager.save_upload_stream(job_id, filename, file)

    # Verify actual file size after streaming
    actual_size = file_path.stat().st_size
    if actual_size > MAX_UPLOAD_SIZE:
        file_manager.cleanup_job(job_id)
        job_store.remove_job(job_id)
        return HTMLResponse("ファイルサイズが上限(2GB)を超えています", status_code=413)
    task = asyncio.create_task(run_pipeline(job_id, file_path, job_store, file_manager))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return RedirectResponse(url=f"/job/{job_id}", status_code=303)


@app.get("/job/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        return HTMLResponse("<h1>ジョブが見つかりません</h1>", status_code=404)
    return templates.TemplateResponse(
        "job.html",
        {
            "request": request,
            "job": job,
            "doc_types": DOCUMENT_TYPES,
        },
    )


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        return HTMLResponse("Not found", status_code=404)

    async def event_stream():
        queue = job_store.subscribe(job_id)
        try:
            # Re-fetch job state after subscribing to avoid race condition
            current_job = job_store.get_job(job_id)
            init_event = {
                "type": "progress",
                "status": current_job.status.value,
                "progress": current_job.progress,
                "message": current_job.current_step,
            }
            yield f"data: {json.dumps(init_event)}\n\n"

            # If job already finished, stop immediately
            if current_job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                    if event.get("status") in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            job_store.unsubscribe(job_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {
        "id": job.id,
        "status": job.status.value,
        "progress": job.progress,
        "current_step": job.current_step,
        "filename": job.filename,
        "error": job.error,
        "results": {k: (v is not None) for k, v in job.results.items() if not k.endswith("_error")},
        "chunks_total": job.chunks_total,
        "chunks_done": job.chunks_done,
    }


@app.get("/api/jobs/{job_id}/download/{doc_type}")
async def download_document(job_id: str, doc_type: str):
    if doc_type not in DOCUMENT_TYPES:
        return HTMLResponse("Invalid document type", status_code=400)
    if not job_store.get_job(job_id):
        return HTMLResponse("Job not found", status_code=404)
    output_dir = file_manager.get_output_dir(job_id)
    file_path = output_dir / f"{doc_type}.md"
    if not file_path.exists():
        return HTMLResponse("File not found", status_code=404)
    return FileResponse(
        file_path,
        filename=f"{doc_type}.md",
        media_type="text/markdown",
    )


@app.post("/api/jobs/{job_id}/regenerate/{doc_type}")
async def regenerate(job_id: str, doc_type: str):
    if not app_config.has_any_key:
        return JSONResponse({"error": "OpenAI APIキーが設定されていません"}, status_code=400)
    job = job_store.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    if doc_type not in DOCUMENT_TYPES:
        return JSONResponse({"error": "invalid doc_type"}, status_code=400)

    task = asyncio.create_task(regenerate_document(job_id, doc_type, job_store, file_manager))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "regenerating"}


class ApiKeyRequest(BaseModel):
    api_key: str


@app.get("/api/config/status")
async def config_status():
    return {
        "has_key": app_config.has_any_key,
        "source": app_config.source,
    }


@app.post("/api/config/api-key")
async def set_api_key(body: ApiKeyRequest):
    key = body.api_key.strip()
    if not key.startswith("sk-"):
        return JSONResponse({"error": "APIキーは 'sk-' で始まる必要があります"}, status_code=400)

    try:
        from openai import AsyncOpenAI

        test_client = AsyncOpenAI(api_key=key)
        await test_client.models.list()
    except Exception:
        return JSONResponse(
            {"error": "APIキーが無効です。キーを確認してください。"}, status_code=401
        )

    app_config.set_user_key(key)
    return {"status": "ok", "source": "web"}


@app.delete("/api/config/api-key")
async def delete_api_key():
    app_config.clear_user_key()
    return {
        "status": "ok",
        "has_key": app_config.has_any_key,
        "source": app_config.source,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
