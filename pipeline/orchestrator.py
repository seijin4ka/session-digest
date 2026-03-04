import asyncio
import logging
from pathlib import Path

from openai import AsyncOpenAI

from pipeline.audio_splitter import split_audio
from pipeline.document_generator import generate_all
from pipeline.transcriber import transcribe_all
from pipeline.transcript_merger import format_transcript, merge_transcripts
from storage.file_manager import FileManager
from storage.job_store import JobStatus, JobStore

logger = logging.getLogger(__name__)


async def run_pipeline(
    job_id: str,
    file_path: Path,
    job_store: JobStore,
    file_manager: FileManager,
) -> None:
    client = AsyncOpenAI()

    try:
        # Step 1: Split audio (0-5%)
        await _update(job_store, job_id, JobStatus.SPLITTING, 0, "音声ファイルを分割中...")
        chunks_dir = file_manager.get_chunks_dir(job_id)
        chunks = await split_audio(file_path, chunks_dir)
        await _update(job_store, job_id, JobStatus.SPLITTING, 5, f"{len(chunks)}個のチャンクに分割完了")

        # Step 2: Transcribe (5-75%)
        await _update(job_store, job_id, JobStatus.TRANSCRIBING, 5, "文字起こし中...")
        job_store.update_job(job_id, chunks_total=len(chunks), chunks_done=0)

        async def on_transcribe_progress(done: int, total: int):
            progress = 5 + int((done / total) * 70)
            job_store.update_job(job_id, chunks_done=done)
            await _update(
                job_store, job_id, JobStatus.TRANSCRIBING, progress,
                f"文字起こし中... ({done}/{total})"
            )

        results = await transcribe_all(client, chunks, on_progress=on_transcribe_progress)

        # Step 3: Merge transcripts (75-80%)
        await _update(job_store, job_id, JobStatus.MERGING, 75, "トランスクリプトを結合中...")
        segments = merge_transcripts(results)
        transcript = format_transcript(segments)

        # Save raw transcript
        output_dir = file_manager.get_output_dir(job_id)
        raw_path = output_dir / "raw_transcript.md"
        raw_path.write_text(transcript, encoding="utf-8")

        await _update(job_store, job_id, JobStatus.MERGING, 80, "トランスクリプト結合完了")

        # Step 4: Generate documents (80-98%)
        await _update(job_store, job_id, JobStatus.GENERATING, 80, "ドキュメントを生成中...")
        documents = await generate_all(client, transcript)

        # Save generated documents
        for doc_type, content in documents.items():
            if content is not None and not doc_type.endswith("_error"):
                doc_path = output_dir / f"{doc_type}.md"
                doc_path.write_text(content, encoding="utf-8")

        await _update(job_store, job_id, JobStatus.GENERATING, 98, "ドキュメント生成完了")

        # Step 5: Complete
        job_store.update_job(job_id, results=documents)
        await _update(job_store, job_id, JobStatus.COMPLETED, 100, "処理完了")

        # Cleanup chunks
        file_manager.cleanup_chunks(job_id)
        file_manager.schedule_cleanup(job_id)

    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id}")
        job_store.update_job(job_id, error=str(e))
        await _update(job_store, job_id, JobStatus.FAILED, -1, f"エラー: {e}")


async def regenerate_document(
    job_id: str,
    doc_type: str,
    job_store: JobStore,
    file_manager: FileManager,
) -> None:
    from pipeline.document_generator import generate_document

    client = AsyncOpenAI()
    output_dir = file_manager.get_output_dir(job_id)
    raw_path = output_dir / "raw_transcript.md"

    if not raw_path.exists():
        raise FileNotFoundError("Raw transcript not found")

    transcript = raw_path.read_text(encoding="utf-8")

    try:
        content = await generate_document(client, transcript, doc_type)
        doc_path = output_dir / f"{doc_type}.md"
        doc_path.write_text(content, encoding="utf-8")

        job = job_store.get_job(job_id)
        if job:
            job.results[doc_type] = content
            job.results.pop(f"{doc_type}_error", None)
            await job_store.notify(job_id, {
                "type": "regenerated",
                "doc_type": doc_type,
            })
    except Exception as e:
        logger.error(f"Regeneration failed for {doc_type}: {e}")
        raise


async def _update(
    job_store: JobStore,
    job_id: str,
    status: JobStatus,
    progress: int,
    message: str,
) -> None:
    job_store.update_job(job_id, status=status, progress=progress, current_step=message)
    await job_store.notify(job_id, {
        "type": "progress",
        "status": status.value,
        "progress": progress,
        "message": message,
    })
