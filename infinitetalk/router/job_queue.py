"""Router-side upload queue and background worker submitter."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request
from streaming_form_data import StreamingFormDataParser
from streaming_form_data.targets import BaseTarget, ValueTarget

from common import protocol as P
from common.settings import RouterSettings
from router.database import RouterDatabase, decode_json_object, encode_json, seconds_to_ms, unix_ms
from router.prompt_routes import PromptRouteStore
from router.worker_pool import WorkerConnection, WorkerError, WorkerOffline, WorkerPool

log = logging.getLogger(__name__)


RECEIVING = "receiving"
QUEUED = "queued"
SUBMITTING = "submitting"
PROCESSING = "processing"
FETCHING_RESULT = "fetching_result"
SUCCESS = "success"
ERROR = "error"

ACTIVE_STATUSES = {RECEIVING, QUEUED, SUBMITTING, PROCESSING, FETCHING_RESULT}
TERMINAL_STATUSES = {SUCCESS, ERROR}


@dataclass
class QueuedFile:
    file_id: str
    path: Path
    filename: str
    content_type: str
    size: int
    sha256: str


@dataclass
class StoredResult:
    path: Path
    filename: str
    content_type: str
    size: int


@dataclass
class QueuedJob:
    task_id: str
    dir: Path
    created_at: int
    updated_at: int
    status: str = RECEIVING
    params: dict[str, Any] = field(default_factory=dict)
    files: dict[str, QueuedFile] = field(default_factory=dict)
    attempts: int = 0
    next_attempt_at: int = 0
    last_error: Optional[str] = None
    worker_id: Optional[str] = None
    prompt_id: Optional[str] = None
    result: Optional[StoredResult] = None
    finished_at: Optional[int] = None


class _FileTarget(BaseTarget):
    def __init__(self, file_id: str, job_dir: Path):
        super().__init__()
        self.file_id = file_id
        self.job_dir = job_dir
        self.entry: QueuedFile | None = None
        self._fh = None
        self._size = 0
        self._hash = hashlib.sha256()
        self._path: Path | None = None
        self._filename = f"{file_id}.bin"
        self._content_type = "application/octet-stream"

    def on_start(self) -> None:
        raw_name = getattr(self, "multipart_filename", None) or f"{self.file_id}.bin"
        self._filename = _safe_filename(raw_name, fallback=f"{self.file_id}.bin")
        self._content_type = (
            getattr(self, "multipart_content_type", None) or "application/octet-stream"
        )
        self._path = self.job_dir / f"{self.file_id}_{self._filename}"
        self._fh = open(self._path, "wb")

    def on_data_received(self, chunk: bytes) -> None:
        if self._fh is None:
            return
        self._fh.write(chunk)
        self._size += len(chunk)
        self._hash.update(chunk)

    def on_finish(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        if self._path is not None:
            self.entry = QueuedFile(
                file_id=self.file_id,
                path=self._path,
                filename=self._filename,
                content_type=self._content_type,
                size=self._size,
                sha256=self._hash.hexdigest(),
            )

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class _ParamTarget(ValueTarget):
    def as_value(self) -> Any:
        try:
            value = bytes(self.value).decode("utf-8")
        except UnicodeDecodeError:
            return ""
        try:
            return int(value)
        except (TypeError, ValueError):
            return value


class RouterJobStore:
    def __init__(
        self,
        queue_dir: str,
        result_dir: str,
        max_jobs: int,
        finished_ttl_seconds: float,
        database: RouterDatabase | None = None,
    ) -> None:
        self._queue_dir = Path(queue_dir)
        self._queue_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir = Path(result_dir)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        self._max_jobs = max_jobs
        self._finished_ttl_ms = seconds_to_ms(finished_ttl_seconds)
        self._database = database
        self._jobs: dict[str, QueuedJob] = {}
        self._prompt_to_task: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def prepare(self) -> None:
        if self._database is None:
            return
        await self._recover_db_jobs()
        await self._gc_db(unix_ms())

    async def enqueue_from_request(self, request: Request, max_upload_bytes: int) -> QueuedJob:
        content_type = request.headers.get("content-type", "")
        if "multipart/form-data" not in content_type.lower():
            raise HTTPException(status_code=400, detail="expected multipart/form-data")

        async with self._lock:
            if self._database is not None:
                job = await self._new_job_db()
            else:
                if self._active_count_locked() >= self._max_jobs:
                    raise HTTPException(status_code=503, detail="router queue is full")
                job = self._new_job_locked()

        image_target = _FileTarget(P.FILE_ID_IMAGE, job.dir)
        audio_target = _FileTarget(P.FILE_ID_AUDIO, job.dir)
        width_target = _ParamTarget()
        height_target = _ParamTarget()
        parser = StreamingFormDataParser(headers={"Content-Type": content_type})
        parser.register("image", image_target)
        parser.register("audio", audio_target)
        parser.register("width", width_target)
        parser.register("height", height_target)

        total_bytes = 0
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > max_upload_bytes:
                    raise HTTPException(status_code=413, detail="upload too large")
                parser.data_received(chunk)

            files: dict[str, QueuedFile] = {}
            if image_target.entry is not None:
                files[P.FILE_ID_IMAGE] = image_target.entry
            if audio_target.entry is not None:
                files[P.FILE_ID_AUDIO] = audio_target.entry
            if P.FILE_ID_IMAGE not in files or P.FILE_ID_AUDIO not in files:
                raise HTTPException(status_code=400, detail="missing image or audio file")

            params: dict[str, Any] = {}
            if width_target.value:
                params["width"] = width_target.as_value()
            if height_target.value:
                params["height"] = height_target.as_value()
            await self.mark_received(job.task_id, params, files)
            return job
        except HTTPException:
            await self.discard(job.task_id)
            raise
        except Exception as exc:
            log.exception("failed to receive queued upload %s", job.task_id)
            await self.discard(job.task_id)
            raise HTTPException(status_code=400, detail=f"upload failed: {exc}") from exc
        finally:
            image_target.close()
            audio_target.close()

    async def mark_received(
        self,
        task_id: str,
        params: dict[str, Any],
        files: dict[str, QueuedFile],
    ) -> None:
        if self._database is not None:
            await self._mark_received_db(task_id, params, files)
            return
        async with self._lock:
            job = self._jobs[task_id]
            job.params = params
            job.files = files
            job.status = QUEUED
            job.updated_at = unix_ms()
            job.next_attempt_at = 0

    async def acquire_ready(self) -> QueuedJob | None:
        now = unix_ms()
        if self._database is not None:
            await self._gc_db(now)
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                async with connection.transaction():
                    record = await connection.fetchrow(
                        f"""
                        UPDATE {jobs_table}
                        SET status = $1, updated_at = $2
                        WHERE task_id = (
                            SELECT task_id
                            FROM {jobs_table}
                            WHERE status = $3 AND next_attempt_at <= $2
                            ORDER BY created_at
                            FOR UPDATE SKIP LOCKED
                            LIMIT 1
                        )
                        RETURNING *
                        """,
                        SUBMITTING,
                        now,
                        QUEUED,
                    )
                    if record is None:
                        return None
                    return await self._job_from_record(connection, record)
        async with self._lock:
            self._gc_locked(now)
            for job in self._jobs.values():
                if job.status == QUEUED and job.next_attempt_at <= now:
                    job.status = SUBMITTING
                    job.updated_at = now
                    return job
        return None

    async def defer(self, task_id: str, delay_seconds: float, reason: str | None = None) -> None:
        if self._database is not None:
            await self._defer_db(task_id, delay_seconds, reason)
            return
        async with self._lock:
            job = self._jobs.get(task_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return
            if job.prompt_id:
                self._prompt_to_task.pop(job.prompt_id, None)
                job.prompt_id = None
            job.status = QUEUED
            job.worker_id = None
            job.updated_at = unix_ms()
            job.next_attempt_at = job.updated_at + seconds_to_ms(delay_seconds)
            if reason:
                job.last_error = reason

    async def mark_attempt(self, task_id: str, worker_id: str) -> None:
        if self._database is not None:
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                await connection.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET status = $2,
                        worker_id = $3,
                        attempts = attempts + 1,
                        updated_at = $4
                    WHERE task_id = $1
                    """,
                    task_id,
                    SUBMITTING,
                    worker_id,
                    unix_ms(),
                )
            return
        async with self._lock:
            job = self._jobs.get(task_id)
            if job is None:
                return
            job.status = SUBMITTING
            job.worker_id = worker_id
            job.attempts += 1
            job.updated_at = unix_ms()

    async def mark_processing(self, task_id: str, prompt_id: str, worker_id: str) -> None:
        if self._database is not None:
            await self._mark_processing_db(task_id, prompt_id, worker_id)
            return
        async with self._lock:
            job = self._jobs.get(task_id)
            if job is None:
                return
            if job.prompt_id and job.prompt_id != prompt_id:
                self._prompt_to_task.pop(job.prompt_id, None)
            job.status = PROCESSING
            job.prompt_id = prompt_id
            job.worker_id = worker_id
            job.last_error = None
            job.updated_at = unix_ms()
            self._prompt_to_task[prompt_id] = task_id

    async def mark_fetching_result(self, task_id: str) -> None:
        if self._database is not None:
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                await connection.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET status = $2, updated_at = $3
                    WHERE task_id = $1 AND status <> ALL($4::text[])
                    """,
                    task_id,
                    FETCHING_RESULT,
                    unix_ms(),
                    list(TERMINAL_STATUSES),
                )
            return
        async with self._lock:
            job = self._jobs.get(task_id)
            if job is None or job.status in TERMINAL_STATUSES:
                return
            job.status = FETCHING_RESULT
            job.updated_at = unix_ms()

    async def mark_success(self, task_id: str, result: StoredResult) -> None:
        if self._database is not None:
            await self._mark_success_db(task_id, result)
            await self._remove_files(task_id)
            return
        async with self._lock:
            job = self._jobs.get(task_id)
            if job is None:
                return
            job.status = SUCCESS
            job.result = result
            job.last_error = None
            job.updated_at = unix_ms()
            job.finished_at = job.updated_at
        await self._remove_files(task_id)

    async def mark_error(self, task_id: str, message: str) -> None:
        if self._database is not None:
            jobs_table = self._database.table("router_jobs")
            now = unix_ms()
            async with self._database.pool.acquire() as connection:
                await connection.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET status = $2,
                        last_error = $3,
                        worker_id = NULL,
                        updated_at = $4,
                        finished_at = $4
                    WHERE task_id = $1
                    """,
                    task_id,
                    ERROR,
                    message,
                    now,
                )
            await self._remove_files(task_id)
            return
        async with self._lock:
            job = self._jobs.get(task_id)
            if job is None:
                return
            job.status = ERROR
            job.last_error = message
            job.worker_id = None
            job.updated_at = unix_ms()
            job.finished_at = job.updated_at
        await self._remove_files(task_id)

    async def get(self, task_id: str) -> QueuedJob | None:
        if self._database is not None:
            await self._gc_db(unix_ms())
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                record = await connection.fetchrow(f"SELECT * FROM {jobs_table} WHERE task_id = $1", task_id)
                if record is None:
                    return None
                return await self._job_from_record(connection, record)
        async with self._lock:
            self._gc_locked(unix_ms())
            return self._jobs.get(task_id)

    async def get_by_prompt(self, prompt_id: str) -> QueuedJob | None:
        if self._database is not None:
            await self._gc_db(unix_ms())
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                record = await connection.fetchrow(f"SELECT * FROM {jobs_table} WHERE prompt_id = $1", prompt_id)
                if record is None:
                    return None
                return await self._job_from_record(connection, record)
        async with self._lock:
            self._gc_locked(unix_ms())
            task_id = self._prompt_to_task.get(prompt_id)
            if task_id is None:
                return None
            return self._jobs.get(task_id)

    async def discard(self, task_id: str) -> None:
        if self._database is not None:
            job = await self.get(task_id)
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                await connection.execute(f"DELETE FROM {jobs_table} WHERE task_id = $1", task_id)
            await self._remove_files(task_id)
            if job and job.result:
                await self._remove_result_file(job.result)
            return
        async with self._lock:
            job = self._jobs.pop(task_id, None)
            if job and job.prompt_id:
                self._prompt_to_task.pop(job.prompt_id, None)
        await self._remove_files(task_id)
        if job and job.result:
            await self._remove_result_file(job.result)

    async def snapshot(self) -> list[dict[str, Any]]:
        if self._database is not None:
            await self._gc_db(unix_ms())
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                records = await connection.fetch(f"SELECT * FROM {jobs_table} ORDER BY created_at")
            return [job_to_payload(self._job_from_record_sync(record)) for record in records]
        async with self._lock:
            self._gc_locked(unix_ms())
            return [job_to_payload(job) for job in self._jobs.values()]

    async def summary(self) -> dict[str, Any]:
        if self._database is not None:
            await self._gc_db(unix_ms())
            jobs_table = self._database.table("router_jobs")
            async with self._database.pool.acquire() as connection:
                records = await connection.fetch(
                    f"SELECT status, COUNT(*) AS count FROM {jobs_table} GROUP BY status"
                )
            return {
                "counts": {record["status"]: record["count"] for record in records},
                "max_jobs": self._max_jobs,
            }
        async with self._lock:
            self._gc_locked(unix_ms())
            counts: dict[str, int] = {}
            for job in self._jobs.values():
                counts[job.status] = counts.get(job.status, 0) + 1
            return {"counts": counts, "max_jobs": self._max_jobs}

    def _new_job_locked(self) -> QueuedJob:
        now = unix_ms()
        task_id = "task_" + uuid.uuid4().hex[:16]
        job_dir = self._queue_dir / task_id
        job_dir.mkdir(parents=True, exist_ok=False)
        job = QueuedJob(task_id=task_id, dir=job_dir, created_at=now, updated_at=now)
        self._jobs[task_id] = job
        return job

    async def _new_job_db(self) -> QueuedJob:
        database = self._database
        if database is None:
            raise RuntimeError("router database is not configured")
        now = unix_ms()
        task_id = "task_" + uuid.uuid4().hex[:16]
        job_dir = self._queue_dir / task_id
        jobs_table = database.table("router_jobs")
        job_dir_created = False
        try:
            async with database.pool.acquire() as connection:
                async with connection.transaction():
                    active_count = await connection.fetchval(
                        f"SELECT COUNT(*) FROM {jobs_table} WHERE status = ANY($1::text[])",
                        list(ACTIVE_STATUSES),
                    )
                    if int(active_count or 0) >= self._max_jobs:
                        raise HTTPException(status_code=503, detail="router queue is full")
                    job_dir.mkdir(parents=True, exist_ok=False)
                    job_dir_created = True
                    await connection.execute(
                        f"""
                        INSERT INTO {jobs_table}
                            (task_id, dir_path, status, params, attempts, next_attempt_at, created_at, updated_at)
                        VALUES ($1, $2, $3, $4::jsonb, 0, 0, $5, $5)
                        """,
                        task_id,
                        str(job_dir),
                        RECEIVING,
                        encode_json({}),
                        now,
                    )
        except Exception:
            if job_dir_created:
                await asyncio.to_thread(shutil.rmtree, job_dir, True)
            raise
        return QueuedJob(task_id=task_id, dir=job_dir, created_at=now, updated_at=now)

    async def _mark_received_db(
        self,
        task_id: str,
        params: dict[str, Any],
        files: dict[str, QueuedFile],
    ) -> None:
        database = self._database
        if database is None:
            raise RuntimeError("router database is not configured")
        now = unix_ms()
        jobs_table = database.table("router_jobs")
        job_files_table = database.table("router_job_files")
        file_rows = [
            (
                task_id,
                queued_file.file_id,
                str(queued_file.path),
                queued_file.filename,
                queued_file.content_type,
                queued_file.size,
                queued_file.sha256,
            )
            for queued_file in files.values()
        ]
        async with database.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET params = $2::jsonb,
                        status = $3,
                        updated_at = $4,
                        next_attempt_at = 0
                    WHERE task_id = $1
                    """,
                    task_id,
                    encode_json(params),
                    QUEUED,
                    now,
                )
                await connection.executemany(
                    f"""
                    INSERT INTO {job_files_table}
                        (task_id, file_id, path, filename, content_type, size, sha256)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT (task_id, file_id) DO UPDATE SET
                        path = EXCLUDED.path,
                        filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type,
                        size = EXCLUDED.size,
                        sha256 = EXCLUDED.sha256
                    """,
                    file_rows,
                )

    async def _defer_db(self, task_id: str, delay_seconds: float, reason: str | None) -> None:
        database = self._database
        if database is None:
            raise RuntimeError("router database is not configured")
        now = unix_ms()
        jobs_table = database.table("router_jobs")
        routes_table = database.table("prompt_routes")
        async with database.pool.acquire() as connection:
            async with connection.transaction():
                record = await connection.fetchrow(
                    f"SELECT status, prompt_id FROM {jobs_table} WHERE task_id = $1 FOR UPDATE",
                    task_id,
                )
                if record is None or record["status"] in TERMINAL_STATUSES:
                    return
                if record["prompt_id"]:
                    await connection.execute(
                        f"DELETE FROM {routes_table} WHERE prompt_id = $1",
                        record["prompt_id"],
                    )
                await connection.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET status = $2,
                        prompt_id = NULL,
                        worker_id = NULL,
                        updated_at = $3,
                        next_attempt_at = $4,
                        last_error = CASE WHEN $5::text IS NULL THEN last_error ELSE $5 END
                    WHERE task_id = $1
                    """,
                    task_id,
                    QUEUED,
                    now,
                    now + seconds_to_ms(delay_seconds),
                    reason,
                )

    async def _mark_processing_db(self, task_id: str, prompt_id: str, worker_id: str) -> None:
        database = self._database
        if database is None:
            raise RuntimeError("router database is not configured")
        now = unix_ms()
        jobs_table = database.table("router_jobs")
        routes_table = database.table("prompt_routes")
        async with database.pool.acquire() as connection:
            async with connection.transaction():
                old_prompt_id = await connection.fetchval(
                    f"SELECT prompt_id FROM {jobs_table} WHERE task_id = $1 FOR UPDATE",
                    task_id,
                )
                if old_prompt_id and old_prompt_id != prompt_id:
                    await connection.execute(
                        f"DELETE FROM {routes_table} WHERE prompt_id = $1",
                        old_prompt_id,
                    )
                await connection.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET status = $2,
                        prompt_id = $3,
                        worker_id = $4,
                        last_error = NULL,
                        updated_at = $5
                    WHERE task_id = $1
                    """,
                    task_id,
                    PROCESSING,
                    prompt_id,
                    worker_id,
                    now,
                )

    async def _mark_success_db(self, task_id: str, result: StoredResult) -> None:
        database = self._database
        if database is None:
            raise RuntimeError("router database is not configured")
        now = unix_ms()
        jobs_table = database.table("router_jobs")
        async with database.pool.acquire() as connection:
            await connection.execute(
                f"""
                UPDATE {jobs_table}
                SET status = $2,
                    result_path = $3,
                    result_filename = $4,
                    result_content_type = $5,
                    result_size = $6,
                    last_error = NULL,
                    updated_at = $7,
                    finished_at = $7
                WHERE task_id = $1
                """,
                task_id,
                SUCCESS,
                str(result.path),
                result.filename,
                result.content_type,
                result.size,
                now,
            )

    async def _recover_db_jobs(self) -> None:
        database = self._database
        if database is None:
            return
        now = unix_ms()
        jobs_table = database.table("router_jobs")
        routes_table = database.table("prompt_routes")
        interrupted_statuses = [SUBMITTING, PROCESSING, FETCHING_RESULT]
        async with database.pool.acquire() as connection:
            async with connection.transaction():
                receiving_records = await connection.fetch(
                    f"SELECT task_id FROM {jobs_table} WHERE status = $1",
                    RECEIVING,
                )
                interrupted_records = await connection.fetch(
                    f"SELECT task_id FROM {jobs_table} WHERE status = ANY($1::text[])",
                    interrupted_statuses,
                )
                await connection.execute(
                    f"""
                    UPDATE {jobs_table}
                    SET status = $2,
                        last_error = $3,
                        updated_at = $4,
                        finished_at = $4
                    WHERE status = $1
                    """,
                    RECEIVING,
                    ERROR,
                    "Router restarted while receiving upload",
                    now,
                )
                if interrupted_records:
                    interrupted_task_ids = [record["task_id"] for record in interrupted_records]
                    await connection.execute(
                        f"DELETE FROM {routes_table} WHERE task_id = ANY($1::text[])",
                        interrupted_task_ids,
                    )
                    await connection.execute(
                        f"""
                        UPDATE {jobs_table}
                        SET status = $2,
                            prompt_id = NULL,
                            worker_id = NULL,
                            next_attempt_at = 0,
                            updated_at = $3,
                            last_error = $4
                        WHERE task_id = ANY($1::text[])
                        """,
                        interrupted_task_ids,
                        QUEUED,
                        now,
                        "Router restarted; task was requeued",
                    )
        for record in receiving_records:
            await self._remove_files(record["task_id"])

    async def _gc_db(self, now: int) -> None:
        database = self._database
        if database is None:
            return
        jobs_table = database.table("router_jobs")
        async with database.pool.acquire() as connection:
            records = await connection.fetch(
                f"""
                SELECT * FROM {jobs_table}
                WHERE status = ANY($1::text[])
                  AND finished_at IS NOT NULL
                  AND finished_at < $2
                """,
                list(TERMINAL_STATUSES),
                now - self._finished_ttl_ms,
            )
            if not records:
                return
            task_ids = [record["task_id"] for record in records]
            await connection.execute(
                f"DELETE FROM {jobs_table} WHERE task_id = ANY($1::text[])",
                task_ids,
            )
        for record in records:
            await self._remove_files(record["task_id"])
            result = self._result_from_record(record)
            if result is not None:
                await self._remove_result_file(result)

    async def _job_from_record(self, connection, record) -> QueuedJob:
        job = self._job_from_record_sync(record)
        if self._database is None:
            return job
        job_files_table = self._database.table("router_job_files")
        file_records = await connection.fetch(
            f"""
            SELECT file_id, path, filename, content_type, size, sha256
            FROM {job_files_table}
            WHERE task_id = $1
            """,
            job.task_id,
        )
        job.files = {
            file_record["file_id"]: QueuedFile(
                file_id=file_record["file_id"],
                path=Path(file_record["path"]),
                filename=file_record["filename"],
                content_type=file_record["content_type"],
                size=file_record["size"],
                sha256=file_record["sha256"],
            )
            for file_record in file_records
        }
        return job

    def _job_from_record_sync(self, record) -> QueuedJob:
        return QueuedJob(
            task_id=record["task_id"],
            dir=Path(record["dir_path"]),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            status=record["status"],
            params=decode_json_object(record["params"]),
            attempts=record["attempts"],
            next_attempt_at=record["next_attempt_at"],
            last_error=record["last_error"],
            worker_id=record["worker_id"],
            prompt_id=record["prompt_id"],
            result=self._result_from_record(record),
            finished_at=record["finished_at"],
        )

    @staticmethod
    def _result_from_record(record) -> StoredResult | None:
        if not record["result_path"] or not record["result_filename"]:
            return None
        return StoredResult(
            path=Path(record["result_path"]),
            filename=record["result_filename"],
            content_type=record["result_content_type"] or "application/octet-stream",
            size=record["result_size"] or 0,
        )

    def _active_count_locked(self) -> int:
        return sum(1 for job in self._jobs.values() if job.status in ACTIVE_STATUSES)

    def _gc_locked(self, now: int) -> None:
        for task_id, job in list(self._jobs.items()):
            if job.status not in TERMINAL_STATUSES or job.finished_at is None:
                continue
            if now - job.finished_at > self._finished_ttl_ms:
                if job.prompt_id:
                    self._prompt_to_task.pop(job.prompt_id, None)
                if job.result:
                    self._remove_result_file_sync(job.result)
                self._jobs.pop(task_id, None)

    async def _remove_files(self, task_id: str) -> None:
        path = self._queue_dir / task_id
        await asyncio.to_thread(shutil.rmtree, path, True)

    async def _remove_result_file(self, result: StoredResult) -> None:
        await asyncio.to_thread(self._remove_result_file_sync, result)

    @staticmethod
    def _remove_result_file_sync(result: StoredResult) -> None:
        with contextlib.suppress(FileNotFoundError):
            result.path.unlink()


class RouterJobDispatcher:
    def __init__(
        self,
        jobs: RouterJobStore,
        pool: WorkerPool,
        routes: PromptRouteStore,
        settings: RouterSettings,
    ) -> None:
        self._jobs = jobs
        self._pool = pool
        self._routes = routes
        self._settings = settings
        self._task: asyncio.Task | None = None
        self._active_tasks: set[asyncio.Task] = set()
        self._stopping = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run(), name="router-job-dispatcher")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        for task in list(self._active_tasks):
            task.cancel()
        for task in list(self._active_tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while not self._stopping:
            job = await self._jobs.acquire_ready()
            if job is None:
                await self._sleep_retry_interval()
                continue

            worker = await self._pool.reserve_idle()
            if worker is None:
                await self._jobs.defer(
                    job.task_id,
                    self._settings.router_submit_retry_seconds,
                    "Waiting for an available worker",
                )
                await self._sleep_retry_interval()
                continue

            task = asyncio.create_task(
                self._submit(job, worker),
                name=f"router-job-{job.task_id}",
            )
            self._active_tasks.add(task)
            task.add_done_callback(self._active_tasks.discard)
            await asyncio.sleep(0)

    async def _submit(self, job: QueuedJob, worker: WorkerConnection) -> None:
        await self._jobs.mark_attempt(job.task_id, worker.worker_id)
        try:
            msg = await submit_job_to_worker(
                job,
                worker,
                chunk_size=self._settings.upload_stream_chunk_bytes,
                timeout=self._settings.worker_rpc_timeout_seconds,
            )
            prompt_id = msg.get("prompt_id")
            if not prompt_id:
                await self._jobs.defer(
                    job.task_id,
                    self._settings.router_submit_retry_seconds,
                    "Worker did not return prompt_id; retrying",
                )
                return
            await self._routes.put(prompt_id, worker.worker_id, task_id=job.task_id)
            await self._jobs.mark_processing(job.task_id, prompt_id, worker.worker_id)
            log.info("queued task %s submitted as prompt %s", job.task_id, prompt_id)
            await self._wait_for_result(job.task_id, prompt_id, worker)
        except (WorkerOffline, asyncio.TimeoutError, ConnectionError) as exc:
            log.warning("queued task %s will retry after worker disconnect/timeout: %s", job.task_id, exc)
            await self._jobs.defer(
                job.task_id,
                self._settings.router_submit_retry_seconds,
                str(exc) or "Worker connection interrupted; retrying",
            )
        except WorkerError as exc:
            log.warning("queued task %s will retry after worker error: %s", job.task_id, exc)
            await self._jobs.defer(
                job.task_id,
                self._settings.router_submit_retry_seconds,
                str(exc) or "Worker processing failed; retrying",
            )
        except Exception as exc:
            log.exception("queued task %s submit crashed", job.task_id)
            worker.mark_offline()
            await self._jobs.defer(
                job.task_id,
                self._settings.router_submit_retry_seconds,
                str(exc) or "Worker submit failed; retrying",
            )
        finally:
            await self._pool.release(worker.worker_id)

    async def _wait_for_result(
        self,
        task_id: str,
        prompt_id: str,
        worker: WorkerConnection,
    ) -> None:
        while not self._stopping:
            payload = await query_worker_status(
                worker,
                prompt_id,
                timeout=self._settings.worker_rpc_timeout_seconds,
            )
            status = payload.get("status")
            if status == "processing":
                await asyncio.sleep(self._settings.router_status_poll_seconds)
                continue
            if status == "success":
                await self._jobs.mark_fetching_result(task_id)
                result = await fetch_result_to_router(
                    task_id=task_id,
                    prompt_id=prompt_id,
                    worker=worker,
                    result_dir=self._jobs.result_dir,
                    timeout=self._settings.worker_rpc_timeout_seconds,
                )
                await self._jobs.mark_success(task_id, result)
                log.info("queued task %s result saved to %s", task_id, result.path)
                return
            message = payload.get("message") or f"ComfyUI task status: {status or 'unknown'}"
            log.warning("queued task %s will retry after status %s: %s", task_id, status, message)
            await self._jobs.defer(
                task_id,
                self._settings.router_submit_retry_seconds,
                message,
            )
            return

    async def _sleep_retry_interval(self) -> None:
        await asyncio.sleep(self._settings.router_submit_retry_seconds)


async def submit_job_to_worker(
    job: QueuedJob,
    worker: WorkerConnection,
    chunk_size: int,
    timeout: float,
) -> dict:
    submit_session = worker.begin_submit()
    try:
        await worker.send_json({"type": P.SUBMIT_START, "task_id": job.task_id})
        await asyncio.wait_for(submit_session.accepted, timeout=timeout)
        await worker.send_json({"type": P.SUBMIT_PARAMS, "params": job.params})
        for file_id in (P.FILE_ID_IMAGE, P.FILE_ID_AUDIO):
            queued_file = job.files[file_id]
            await worker.send_json({
                "type": P.FILE_START,
                "file_id": file_id,
                "filename": queued_file.filename,
                "content_type": queued_file.content_type,
            })
            with queued_file.path.open("rb") as fh:
                while True:
                    chunk = await asyncio.to_thread(fh.read, chunk_size)
                    if not chunk:
                        break
                    await worker.send_chunk(
                        {
                            "type": P.FILE_CHUNK,
                            "file_id": file_id,
                            "size": len(chunk),
                        },
                        chunk,
                    )
            await worker.send_json({
                "type": P.FILE_END,
                "file_id": file_id,
                "size": queued_file.size,
                "sha256": queued_file.sha256,
            })
        await worker.send_json({"type": P.SUBMIT_COMPLETE, "task_id": job.task_id})
        return await asyncio.wait_for(submit_session.result, timeout=timeout)
    except Exception:
        with contextlib.suppress(Exception):
            await worker.send_json({"type": P.SUBMIT_CANCEL, "reason": "router_submit_failed"})
        raise
    finally:
        worker.end_submit()


async def query_worker_status(
    worker: WorkerConnection,
    prompt_id: str,
    timeout: float,
) -> dict:
    rpc_id, fut = worker.new_rpc_future()
    try:
        await worker.send_json({
            "type": P.STATUS_QUERY,
            "rpc_id": rpc_id,
            "prompt_id": prompt_id,
        })
        msg = await asyncio.wait_for(fut, timeout=timeout)
        return {k: v for k, v in msg.items() if k not in ("type", "rpc_id")}
    finally:
        worker.drop_rpc(rpc_id)


async def fetch_result_to_router(
    task_id: str,
    prompt_id: str,
    worker: WorkerConnection,
    result_dir: Path,
    timeout: float,
) -> StoredResult:
    rpc_id, stream = worker.new_result_stream()
    tmp_path: Path | None = None
    try:
        await worker.send_json({
            "type": P.RESULT_OPEN,
            "rpc_id": rpc_id,
            "prompt_id": prompt_id,
        })
        meta = await asyncio.wait_for(stream.meta_future, timeout=timeout)
        filename = _safe_filename(
            meta.get("filename") or f"{prompt_id}.mp4",
            fallback=f"{prompt_id}.mp4",
        )
        content_type = meta.get("content_type") or "application/octet-stream"
        result_path = result_dir / f"{task_id}_{filename}"
        tmp_path = result_path.with_name(f"{result_path.name}.part")
        size = 0
        with tmp_path.open("wb") as fh:
            while True:
                kind, data = await asyncio.wait_for(stream.queue.get(), timeout=timeout)
                if kind == "chunk":
                    await asyncio.to_thread(fh.write, data)
                    size += len(data)
                    continue
                if kind == "end":
                    break
                if kind == "error":
                    message = data.get("message") if isinstance(data, dict) else str(data)
                    raise WorkerError(message or "result stream error")
        await asyncio.to_thread(os.replace, tmp_path, result_path)
        return StoredResult(
            path=result_path,
            filename=filename,
            content_type=content_type,
            size=size,
        )
    except Exception:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
        raise
    finally:
        worker.drop_result_stream(rpc_id)


def job_to_payload(job: QueuedJob) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": job.status,
        "task_id": job.task_id,
        "attempts": job.attempts,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if job.prompt_id:
        payload["prompt_id"] = job.prompt_id
    if job.worker_id:
        payload["worker_id"] = job.worker_id
    if job.last_error:
        payload["message"] = job.last_error
    if job.result:
        payload["result"] = {
            "filename": job.result.filename,
            "content_type": job.result.content_type,
            "size": job.result.size,
        }
    return payload


def _safe_filename(name: str, fallback: str) -> str:
    base = os.path.basename(name or "")
    base = base.replace("\\", "").replace("/", "").strip()
    if not base or base in (".", ".."):
        return fallback
    cleaned = "".join(c for c in base if c.isalnum() or c in "._-")
    return cleaned or fallback