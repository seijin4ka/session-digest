import asyncio
import logging
import shutil
from pathlib import Path

import aiofiles

logger = logging.getLogger(__name__)

BASE_DIR = Path("/tmp/session-digest")


class FileManager:
    def __init__(self, base_dir: Path = BASE_DIR):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_tasks: set[asyncio.Task] = set()

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

    async def save_upload_stream(self, job_id: str, filename: str, upload_file) -> Path:
        """UploadFileからストリーミングでファイルに保存する。メモリ効率が良い。"""
        job_dir = self.get_job_dir(job_id)
        safe_filename = Path(filename).name
        if not safe_filename:
            safe_filename = "upload"
        file_path = job_dir / safe_filename
        async with aiofiles.open(file_path, "wb") as f:
            while chunk := await upload_file.read(1024 * 1024):
                await f.write(chunk)
        return file_path

    def cleanup_chunks(self, job_id: str) -> None:
        chunks_dir = self.base_dir / job_id / "chunks"
        if chunks_dir.exists():
            shutil.rmtree(chunks_dir)

    def cleanup_job(self, job_id: str) -> None:
        job_dir = self.base_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)

    def schedule_cleanup(self, job_id: str, delay: int = 86400, job_store=None) -> None:
        async def _delayed_cleanup():
            await asyncio.sleep(delay)
            try:
                self.cleanup_job(job_id)
            except Exception:
                logger.exception(f"Failed to cleanup job directory for {job_id}")
            if job_store is not None:
                job_store.remove_job(job_id)

        task = asyncio.create_task(_delayed_cleanup())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
