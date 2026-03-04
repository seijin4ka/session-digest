import asyncio
import shutil
from pathlib import Path

import aiofiles

BASE_DIR = Path("/tmp/session-digest")


class FileManager:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_job_dir(self, job_id: str) -> Path:
        path = self.base_dir / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_chunks_dir(self, job_id: str) -> Path:
        path = self.get_job_dir(job_id) / "chunks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_output_dir(self, job_id: str) -> Path:
        path = self.get_job_dir(job_id) / "output"
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def save_upload(self, job_id: str, filename: str, content: bytes) -> Path:
        job_dir = self.get_job_dir(job_id)
        file_path = job_dir / filename
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)
        return file_path

    def cleanup_chunks(self, job_id: str) -> None:
        chunks_dir = self.base_dir / job_id / "chunks"
        if chunks_dir.exists():
            shutil.rmtree(chunks_dir)

    def cleanup_job(self, job_id: str) -> None:
        job_dir = self.base_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)

    def schedule_cleanup(self, job_id: str, delay: int = 86400) -> None:
        async def _delayed_cleanup():
            await asyncio.sleep(delay)
            self.cleanup_job(job_id)

        asyncio.create_task(_delayed_cleanup())
