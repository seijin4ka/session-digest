import asyncio
import logging
from pathlib import Path
from typing import Any, Callable

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

Callback = Callable[[int, int], Any]


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=30))
async def transcribe_chunk(client: AsyncOpenAI, chunk_path: Path) -> dict:
    logger.info(f"Transcribing {chunk_path.name}")
    with open(chunk_path, "rb") as audio_file:
        # languageを指定しない: Whisperが自動検出する
        # 英語スピーカー+日本語通訳などの多言語音声に対応
        response = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
    return response.model_dump()


async def transcribe_all(
    client: AsyncOpenAI,
    chunks: list[Path],
    on_progress: Callback | None = None,
    skip_indices: set[int] | None = None,
) -> list[dict]:
    semaphore = asyncio.Semaphore(5)
    results: list[dict | None] = [None] * len(chunks)

    async def _process(index: int, chunk_path: Path):
        if skip_indices and index in skip_indices:
            logger.info(f"Skipping silent chunk {chunk_path.name}")
            results[index] = {"segments": [], "text": "", "skipped": True}
            done = sum(1 for r in results if r is not None)
            if on_progress:
                await on_progress(done, len(chunks))
            return

        async with semaphore:
            try:
                result = await transcribe_chunk(client, chunk_path)
                results[index] = result
            except Exception as e:
                logger.error(f"Failed to transcribe {chunk_path.name} after retries: {e}")
                results[index] = {
                    "segments": [],
                    "text": "",
                    "error": f"[文字起こし失敗: {chunk_path.name}]",
                }
            finally:
                done = sum(1 for r in results if r is not None)
                if on_progress:
                    await on_progress(done, len(chunks))

    tasks = [_process(i, chunk) for i, chunk in enumerate(chunks)]
    await asyncio.gather(*tasks)

    return results
