import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


async def get_audio_duration(input_path: Path) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(input_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode()}")
    return float(stdout.decode().strip())


async def split_audio(
    input_path: Path,
    output_dir: Path,
    chunk_duration: int = 600,
    overlap: int = 30,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    duration = await get_audio_duration(input_path)
    logger.info(f"Audio duration: {duration:.1f}s, splitting into {chunk_duration}s chunks with {overlap}s overlap")

    chunks: list[Path] = []
    start = 0
    index = 0

    while start < duration:
        output_path = output_dir / f"chunk_{index:03d}.mp3"
        end = min(start + chunk_duration + overlap, duration)
        segment_duration = end - start

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-t", str(segment_duration),
            "-i", str(input_path),
            "-ac", "1",
            "-ar", "16000",
            "-b:a", "64k",
            "-f", "mp3",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed on chunk {index}: {stderr.decode()}")

        chunks.append(output_path)
        logger.info(f"Created chunk {index}: {start}s - {end}s")

        start += chunk_duration
        index += 1

    return chunks
