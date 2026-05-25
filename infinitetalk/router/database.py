"""PostgreSQL helpers for Router state persistence."""
from __future__ import annotations

import json
import re
import time
from typing import Any


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_database_url(database_url: str) -> str:
    raw = database_url.strip()
    if raw.startswith("postgresql+asyncpg://"):
        return "postgresql://" + raw[len("postgresql+asyncpg://"):]
    if raw.startswith("postgres+asyncpg://"):
        return "postgres://" + raw[len("postgres+asyncpg://"):]
    return raw


def encode_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def decode_json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    return {}


def unix_ms() -> int:
    return time.time_ns() // 1_000_000


def seconds_to_ms(seconds: float) -> int:
    return int(seconds * 1000)


class RouterDatabase:
    def __init__(
        self,
        database_url: str,
        table_prefix: str = "itd_",
        min_pool_size: int = 1,
        max_pool_size: int = 10,
    ) -> None:
        self.database_url = normalize_database_url(database_url)
        self.table_prefix = table_prefix
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self._pool: Any | None = None
        self._validate_table_name("router_jobs")

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("router database is not connected")
        return self._pool

    def table(self, base_name: str) -> str:
        return self._validate_table_name(base_name)

    async def connect(self) -> None:
        if self._pool is not None:
            return
        import asyncpg

        self._pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
        )

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    async def ensure_schema(self) -> None:
        async with self.pool.acquire() as connection:
            for statement in self.schema_statements():
                await connection.execute(statement)

    def schema_statements(self) -> list[str]:
        jobs = self.table("router_jobs")
        job_files = self.table("router_job_files")
        prompt_routes = self.table("prompt_routes")
        workers = self.table("router_workers")
        return [
            f"""
            CREATE TABLE IF NOT EXISTS {jobs} (
                task_id TEXT PRIMARY KEY,
                dir_path TEXT NOT NULL,
                status TEXT NOT NULL,
                params JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at BIGINT NOT NULL DEFAULT 0,
                last_error TEXT,
                worker_id TEXT,
                prompt_id TEXT UNIQUE,
                result_path TEXT,
                result_filename TEXT,
                result_content_type TEXT,
                result_size BIGINT,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                finished_at BIGINT
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS {self.table("router_jobs_status_next_attempt_idx")}
            ON {jobs} (status, next_attempt_at, created_at)
            """,
            f"""
            CREATE INDEX IF NOT EXISTS {self.table("router_jobs_prompt_id_idx")}
            ON {jobs} (prompt_id)
            WHERE prompt_id IS NOT NULL
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {job_files} (
                task_id TEXT NOT NULL REFERENCES {jobs}(task_id) ON DELETE CASCADE,
                file_id TEXT NOT NULL,
                path TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size BIGINT NOT NULL,
                sha256 TEXT NOT NULL,
                PRIMARY KEY (task_id, file_id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {prompt_routes} (
                prompt_id TEXT PRIMARY KEY,
                task_id TEXT REFERENCES {jobs}(task_id) ON DELETE CASCADE,
                worker_id TEXT NOT NULL,
                created_at BIGINT NOT NULL,
                last_accessed_at BIGINT NOT NULL
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS {self.table("prompt_routes_worker_id_idx")}
            ON {prompt_routes} (worker_id)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {workers} (
                worker_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                capabilities JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                connected_at BIGINT,
                updated_at BIGINT NOT NULL,
                disconnected_at BIGINT
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS {self.table("router_workers_status_idx")}
            ON {workers} (status)
            """,
        ]

    def _validate_table_name(self, base_name: str) -> str:
        full_name = f"{self.table_prefix}{base_name}"
        if not _IDENTIFIER_RE.match(full_name):
            raise ValueError(f"invalid database table name: {full_name!r}")
        return full_name