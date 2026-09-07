from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fastapi import FastAPI
from cccc_desensitization import service
from cccc_desensitization.routes import create_routers


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    group_roots = {"group-a": tmp_path / "a", "group-b": tmp_path / "b"}
    for root in group_roots.values():
        root.mkdir()
    monkeypatch.setattr(
        service,
        "load_group",
        lambda group_id: SimpleNamespace(path=group_roots[group_id], doc={}) if group_id in group_roots else None,
    )
    monkeypatch.setattr(service, "resolve_scope_root", lambda group_id: group_roots[group_id] / "scope")
    monkeypatch.setenv("ONECOLLEAGUE_DESENSITIZATION_MIN_FREE_DISK_BYTES", "0")
    monkeypatch.setenv("CCCC_DESENSITIZATION_TOKEN", "test-token")
    app = FastAPI()
    for router in create_routers():
        app.include_router(router)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def test_multipart_analyze_streams_file_and_preserves_name(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "model_status", lambda: {"enabled": True, "status": "ready"})
    observed: dict[str, object] = {}

    def analyze(group_id: str, path: Path, filename: str) -> dict[str, object]:
        observed.update(group_id=group_id, filename=filename, content=path.read_bytes())
        return {"analysis_id": "a" * 32, "filename": filename, "kind": "txt", "entities": []}

    monkeypatch.setattr(service, "analyze_path", analyze)
    response = client.post(
        "/api/v1/groups/group-a/desensitization/files/analyze",
        files={"file": ("客户.txt", b"13800138000", "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["result"]["filename"] == "客户.txt"
    assert observed == {"group_id": "group-a", "filename": "客户.txt", "content": b"13800138000"}


def test_model_gate_and_batch_traversal_are_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "model_status", lambda: {"enabled": True, "status": "starting"})
    blocked = client.post(
        "/api/v1/groups/group-a/desensitization/files/analyze",
        files={"file": ("a.txt", b"a", "text/plain")},
    )
    assert blocked.status_code == 503

    monkeypatch.setattr(service, "model_status", lambda: {"enabled": True, "status": "ready"})
    traversal = client.post(
        "/api/v1/groups/group-a/desensitization/batches",
        files=[("files", ("a.txt", b"a", "text/plain"))],
        data={"manifest": '{"entries":[{"relative_path":"../a.txt"}]}'},
    )
    assert traversal.status_code == 422


def test_batch_manifest_preserves_mixed_folder_paths(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "model_status", lambda: {"enabled": True, "status": "ready"})
    observed: list[tuple[str, bytes]] = []

    def create(group_id, staged, root_name, mode, options, idempotency_key):
        assert group_id == "group-a"
        assert root_name == "资料"
        assert mode == "review"
        for relative, path in staged:
            observed.append((relative, path.read_bytes()))
            path.unlink()
        return {"id": "batch", "status": "queued", "mode": mode, "total": len(observed)}

    monkeypatch.setattr(service, "create_batch_from_paths", create)
    manifest = {
        "root_name": "资料",
        "mode": "review",
        "entries": [
            {"relative_path": "资料/文字/a.txt"},
            {"relative_path": "资料/图片/a.png"},
        ],
    }
    response = client.post(
        "/api/v1/groups/group-a/desensitization/batches",
        files=[
            ("files", ("a.txt", b"text", "text/plain")),
            ("files", ("a.png", b"image", "image/png")),
        ],
        data={"manifest": json.dumps(manifest, ensure_ascii=False)},
    )
    assert response.status_code == 200
    assert observed == [("资料/文字/a.txt", b"text"), ("资料/图片/a.png", b"image")]


def test_task_lookup_and_binary_download_remain_group_scoped(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = tmp_path / "masked.txt"
    artifact.write_text("masked", encoding="utf-8")

    def analysis(group_id: str, task_id: str) -> dict[str, object]:
        if group_id != "group-a":
            raise ValueError("task not found")
        return {"analysis_id": task_id, "filename": "a.txt", "kind": "txt", "entities": []}

    def task_file(group_id: str, task_id: str, kind: str) -> tuple[Path, str, str]:
        if group_id != "group-a":
            raise ValueError("task not found")
        return artifact, "masked.txt", "attachment"

    monkeypatch.setattr(service, "get_analysis", analysis)
    monkeypatch.setattr(service, "task_file", task_file)
    task_id = "b" * 32
    assert client.get(f"/api/v1/groups/group-b/desensitization/tasks/{task_id}/analysis").status_code == 404
    response = client.get(f"/api/v1/groups/group-a/desensitization/tasks/{task_id}/artifact")
    assert response.status_code == 200
    assert response.content == b"masked"
    assert response.headers["cache-control"] == "private, no-store"
