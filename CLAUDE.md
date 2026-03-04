# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run

```bash
# Docker (recommended)
docker compose up --build        # http://localhost:8000

# Local development (requires ffmpeg installed)
pip install -r requirements.txt
OPENAI_API_KEY=sk-... python main.py
```

Environment: `.env` with `OPENAI_API_KEY` only (loaded via python-dotenv).

## Architecture

Single-process FastAPI app that runs an async audio processing pipeline:

```
Upload → FFmpeg Split → Whisper API → Merge → GPT-4o Generate → Download
```

**Two layers:**
- `pipeline/` - Processing stages, each an async module. `orchestrator.py` chains them and publishes progress events.
- `storage/` - In-memory job state (`dict`) and temp file management under `/tmp/session-digest/{jobId}/`.

**Pipeline flow (`orchestrator.run_pipeline`):**
1. `audio_splitter` - FFmpeg splits into 10min chunks with 30s overlap (mono, 16kHz, 64kbps)
2. `transcriber` - Whisper API with `asyncio.Semaphore(5)`, 3 retries. Failed chunks become placeholders.
3. `transcript_merger` - Deduplicates overlap regions by timestamp offset, produces timestamped text.
4. `document_generator` - GPT-4o generates 3 document types in parallel from prompt templates in `prompts/`.

**Real-time progress:** SSE via `StreamingResponse`. `JobStore` uses pub/sub (`asyncio.Queue`) to push events. Progress is weighted: split 0-5%, transcribe 5-75%, merge 75-80%, generate 80-98%.

**Frontend:** Jinja2 templates + vanilla JS + htmx. `index.html` handles drag-drop upload, `job.html` connects to SSE and renders results in tabs.

## Key Conventions

- All I/O is async (aiofiles, AsyncOpenAI, asyncio.create_subprocess_exec)
- Retry via `tenacity` with exponential backoff on all OpenAI calls
- Pipeline never stops on partial failure: failed transcription chunks insert error placeholders, failed document generation is individually retryable via `/api/jobs/{id}/regenerate/{doc_type}`
- Prompt templates live in `prompts/*.md` with `{transcript}` placeholder
- Temp files: chunks deleted after merge, entire job dir auto-deleted after 24h
