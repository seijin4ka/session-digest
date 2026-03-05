import asyncio
import json
import logging

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pipeline.document_generator import DOCUMENT_TYPES
from pipeline.orchestrator import regenerate_document, run_pipeline
from storage.file_manager import FileManager
from storage.job_store import JobStatus, JobStore

load_dotenv()

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Session Digest")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

job_store = JobStore()
file_manager = FileManager()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    job_id = job_store.create_job(filename=file.filename)
    file_path = await file_manager.save_upload(job_id, file.filename, content)
    asyncio.create_task(run_pipeline(job_id, file_path, job_store, file_manager))
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
            # Send current state immediately
            init_event = {
                "type": "progress",
                "status": job.status.value,
                "progress": job.progress,
                "message": job.current_step,
            }
            yield f"data: {json.dumps(init_event)}\n\n"

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
        return {"error": "not found"}
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
    job = job_store.get_job(job_id)
    if not job:
        return {"error": "not found"}
    if doc_type not in DOCUMENT_TYPES:
        return {"error": "invalid doc_type"}

    asyncio.create_task(regenerate_document(job_id, doc_type, job_store, file_manager))
    return {"status": "regenerating"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
