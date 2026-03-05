import logging
from pathlib import Path

import aiofiles
from openai import AsyncOpenAI

from config import app_config
from pipeline.audio_splitter import split_audio
from pipeline.document_generator import generate_all
from pipeline.silence_detector import (
    analyze_chunks,
    assess_overall_quality,
    check_hallucination,
)
from pipeline.transcriber import transcribe_all
from pipeline.transcript_merger import format_transcript, merge_transcripts
from storage.file_manager import FileManager
from storage.job_store import JobStatus, JobStore


class SilentAudioError(Exception):
    """全チャンクが無音またはハルシネーションと判定された場合のエラー。"""

    pass


logger = logging.getLogger(__name__)


async def run_pipeline(
    job_id: str,
    file_path: Path,
    job_store: JobStore,
    file_manager: FileManager,
) -> None:
    try:
        api_key = app_config.api_key
        if not api_key:
            raise ValueError("OpenAI APIキーが設定されていません")
        client = AsyncOpenAI(api_key=api_key)

        # Step 1: Split audio (0-5%)
        await _update(job_store, job_id, JobStatus.SPLITTING, 0, "音声ファイルを分割中...")
        chunks_dir = file_manager.get_chunks_dir(job_id)
        chunks = await split_audio(file_path, chunks_dir)

        # Step 1.5: Analyze audio levels
        await _update(job_store, job_id, JobStatus.SPLITTING, 3, "音声レベルを解析中...")
        analyses = await analyze_chunks(chunks)
        silent_indices = {a.index for a in analyses if a.is_silent}

        if silent_indices:
            if len(silent_indices) == len(chunks):
                raise SilentAudioError(
                    "音声が検出されませんでした。無音または作業音のみのファイルは処理できません。"
                )
            logger.info(f"Silent chunks detected: {sorted(silent_indices)}")
            msg = (
                f"{len(silent_indices)}/{len(chunks)} チャンクで"
                "音声が検出されませんでした。有効な部分のみ処理します。"
            )
            await _warn(job_store, job_id, msg)

        msg = f"{len(chunks)}個のチャンクに分割完了"
        await _update(job_store, job_id, JobStatus.SPLITTING, 5, msg)

        # Step 2: Transcribe (5-75%)
        await _update(job_store, job_id, JobStatus.TRANSCRIBING, 5, "文字起こし中...")
        job_store.update_job(job_id, chunks_total=len(chunks), chunks_done=0)

        async def on_transcribe_progress(done: int, total: int):
            progress = 5 + int((done / total) * 70)
            job_store.update_job(job_id, chunks_done=done)
            await _update(
                job_store,
                job_id,
                JobStatus.TRANSCRIBING,
                progress,
                f"文字起こし中... ({done}/{total})",
            )

        results = await transcribe_all(
            client, chunks, on_progress=on_transcribe_progress, skip_indices=silent_indices
        )

        # Step 2.5: Hallucination check
        await _update(
            job_store,
            job_id,
            JobStatus.TRANSCRIBING,
            74,
            "ハルシネーションチェック中...",
        )
        hallucination_results = [check_hallucination(r) for r in results]
        invalid_indices, all_invalid = assess_overall_quality(analyses, hallucination_results)

        # Mark hallucinated results
        for i, hr in enumerate(hallucination_results):
            if hr.is_hallucinated:
                logger.warning(f"Chunk {i} hallucination detected: {hr.reason}")
                results[i]["hallucinated"] = True
                results[i]["hallucination_reason"] = hr.reason

        if all_invalid:
            raise SilentAudioError(
                "有効な音声コンテンツが検出されませんでした。無音、作業音のみ、またはWhisperのハルシネーションが検出されました。"
            )

        hallucinated_count = sum(1 for hr in hallucination_results if hr.is_hallucinated)
        if hallucinated_count > 0:
            await _warn(
                job_store,
                job_id,
                f"{hallucinated_count}個のチャンクでハルシネーション（架空テキスト）を検出しました。該当部分を除外して処理します。",
            )

        # Step 3: Merge transcripts (75-80%)
        await _update(job_store, job_id, JobStatus.MERGING, 75, "トランスクリプトを結合中...")
        segments = merge_transcripts(results)
        transcript = format_transcript(segments)

        # Save raw transcript
        output_dir = file_manager.get_output_dir(job_id)
        raw_path = output_dir / "raw_transcript.md"
        async with aiofiles.open(raw_path, "w", encoding="utf-8") as f:
            await f.write(transcript)

        await _update(job_store, job_id, JobStatus.MERGING, 80, "トランスクリプト結合完了")

        # Step 4: Generate documents (80-98%)
        await _update(job_store, job_id, JobStatus.GENERATING, 80, "ドキュメントを生成中...")
        documents = await generate_all(client, transcript)

        # Save generated documents
        for doc_type, content in documents.items():
            if content is not None and not doc_type.endswith("_error"):
                doc_path = output_dir / f"{doc_type}.md"
                async with aiofiles.open(doc_path, "w", encoding="utf-8") as f:
                    await f.write(content)

        await _update(job_store, job_id, JobStatus.GENERATING, 98, "ドキュメント生成完了")

        # Step 5: Complete
        job_store.update_job(job_id, results=documents)
        await _update(job_store, job_id, JobStatus.COMPLETED, 100, "処理完了")

        # Cleanup chunks
        file_manager.cleanup_chunks(job_id)
        file_manager.schedule_cleanup(job_id, job_store=job_store)

    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id}")
        job_store.update_job(job_id, error=str(e))
        await _update(job_store, job_id, JobStatus.FAILED, -1, "処理中にエラーが発生しました")


async def regenerate_document(
    job_id: str,
    doc_type: str,
    job_store: JobStore,
    file_manager: FileManager,
) -> None:
    from pipeline.document_generator import generate_document

    output_dir = file_manager.get_output_dir(job_id)
    raw_path = output_dir / "raw_transcript.md"

    if not raw_path.exists():
        logger.error(f"Raw transcript not found for job {job_id}")
        return

    api_key = app_config.api_key
    if not api_key:
        logger.error(f"API key not configured for regeneration of {doc_type} in job {job_id}")
        job = job_store.get_job(job_id)
        if job:
            job.results[f"{doc_type}_error"] = "OpenAI APIキーが設定されていません"
        return
    client = AsyncOpenAI(api_key=api_key)

    async with aiofiles.open(raw_path, "r", encoding="utf-8") as f:
        transcript = await f.read()

    try:
        content = await generate_document(client, transcript, doc_type)
        doc_path = output_dir / f"{doc_type}.md"
        async with aiofiles.open(doc_path, "w", encoding="utf-8") as f:
            await f.write(content)

        job = job_store.get_job(job_id)
        if job:
            job.results[doc_type] = content
            job.results.pop(f"{doc_type}_error", None)
            await job_store.notify(
                job_id,
                {
                    "type": "regenerated",
                    "doc_type": doc_type,
                },
            )
    except Exception as e:
        logger.exception(f"Regeneration failed for {doc_type} in job {job_id}")
        job = job_store.get_job(job_id)
        if job:
            job.results[f"{doc_type}_error"] = str(e)


async def _update(
    job_store: JobStore,
    job_id: str,
    status: JobStatus,
    progress: int,
    message: str,
) -> None:
    job_store.update_job(job_id, status=status, progress=progress, current_step=message)
    await job_store.notify(
        job_id,
        {
            "type": "progress",
            "status": status.value,
            "progress": progress,
            "message": message,
        },
    )


async def _warn(job_store: JobStore, job_id: str, message: str) -> None:
    await job_store.notify(
        job_id,
        {
            "type": "warning",
            "message": message,
        },
    )
