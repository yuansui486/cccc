CREATE TABLE IF NOT EXISTS itd_router_jobs (
    task_id TEXT PRIMARY KEY,
    dir_path TEXT NOT NULL,
    status TEXT NOT NULL,
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
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
);

CREATE INDEX IF NOT EXISTS itd_router_jobs_status_next_attempt_idx
ON itd_router_jobs (status, next_attempt_at, created_at);

CREATE INDEX IF NOT EXISTS itd_router_jobs_prompt_id_idx
ON itd_router_jobs (prompt_id)
WHERE prompt_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS itd_router_job_files (
    task_id TEXT NOT NULL REFERENCES itd_router_jobs(task_id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    path TEXT NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size BIGINT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (task_id, file_id)
);

CREATE TABLE IF NOT EXISTS itd_prompt_routes (
    prompt_id TEXT PRIMARY KEY,
    task_id TEXT REFERENCES itd_router_jobs(task_id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    last_accessed_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS itd_prompt_routes_worker_id_idx
ON itd_prompt_routes (worker_id);

CREATE TABLE IF NOT EXISTS itd_router_workers (
    worker_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
    connected_at BIGINT,
    updated_at BIGINT NOT NULL,
    disconnected_at BIGINT
);

CREATE INDEX IF NOT EXISTS itd_router_workers_status_idx
ON itd_router_workers (status);