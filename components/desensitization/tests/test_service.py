from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from cccc_desensitization import service
from cccc_desensitization import engine


@pytest.fixture()
def group_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    roots = {"group-a": tmp_path / "a", "group-b": tmp_path / "b"}
    for root in roots.values():
        root.mkdir()
    monkeypatch.setattr(service, "load_group", lambda group_id: SimpleNamespace(path=roots[group_id], doc={}) if group_id in roots else None)
    monkeypatch.setattr(service, "resolve_scope_root", lambda group_id: roots[group_id] / "scope")
    monkeypatch.setattr(service, "group_engine_root", lambda group_id: roots[group_id] / "state" / "desensitization" / "engine")
    monkeypatch.setenv("ONECOLLEAGUE_DESENSITIZATION_MIN_FREE_DISK_BYTES", "0")

    def fake_engine_call(group_id: str, method: str, upload):
        assert method == "files_analyze"
        task_id = "a" * 32
        folder = service.group_engine_root(group_id) / "tasks" / task_id
        folder.mkdir(parents=True, exist_ok=True)
        data = upload.file.read()
        (folder / "source.bin").write_bytes(data)
        (folder / "analysis.json").write_text("{}", encoding="utf-8")
        return {
            "analysis_id": task_id,
            "filename": upload.filename,
            "kind": "txt",
            "text": data.decode(),
            "entities": [{"type": "PHONE", "text": "13800138000", "start": 0, "end": 11, "selected": True}],
            "sha256": __import__("hashlib").sha256(data).hexdigest(),
        }

    monkeypatch.setattr(service, "engine_call", fake_engine_call)
    return roots


def test_relative_path_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="invalid relative path"):
        service._relative_path("folder/../secret.txt")


def test_task_is_scoped_and_uses_snapshot_output(group_roots: dict[str, Path]) -> None:
    task = service.analyze_file("group-a", b"13800138000", "phone.txt")
    rows = service.list_tasks("group-a")["items"]
    assert rows[0]["id"] == task["task_id"]
    assert service.list_tasks("group-b")["items"] == []
    with pytest.raises(ValueError, match="task not found"):
        service.artifact_path("group-b", task["task_id"], "source")
    assert service.artifact_path("group-a", task["task_id"], "source").read_bytes() == b"13800138000"
    connection = service._connection("group-a")
    row = connection.execute("SELECT output_root,scope_root FROM tasks WHERE id=?", (task["task_id"],)).fetchone()
    connection.close()
    assert Path(row["output_root"]) == group_roots["group-a"] / "scope" / "脱敏后"
    assert Path(row["scope_root"]) == group_roots["group-a"] / "scope"


def test_capabilities_exposes_chunk_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONECOLLEAGUE_DESENSITIZATION_ENABLED", "0")
    value = service.capabilities()
    assert value["enabled"] is False
    assert value["chunk_bytes"] == 8 * 1024 * 1024
    assert value["limits"]["max_file_bytes"] == 0


def test_batch_rejects_duplicate_normalized_paths(group_roots: dict[str, Path]) -> None:
    first = service.create_upload_path("group-a", "first.txt")
    second = service.create_upload_path("group-a", "second.txt")
    first.write_text("a", encoding="utf-8")
    second.write_text("b", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        service.create_batch_from_paths(
            "group-a", [("Folder/A.txt", first), ("folder/a.TXT", second)]
        )


def test_engine_upload_read_limit_is_unbounded_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(DEFAULT_POLICIES={}, policy_store={}, _POLICY_STORE_LOADED=False)
    fake._load_policy_store = lambda: None
    monkeypatch.setattr(engine, "_load_engine", lambda: fake)
    monkeypatch.setattr(engine, "group_engine_root", lambda _group_id: tmp_path)

    with engine.engine_context("group-a"):
        assert fake.MAX_UPLOAD == 0


def test_engine_upload_reader_handles_disk_backed_stream_without_overflow() -> None:
    from cccc_desensitization import main
    from starlette.datastructures import UploadFile

    size = 50 * 1024 * 1024 + 17
    with tempfile.TemporaryFile() as stream:
        stream.write(b"x" * size)
        stream.seek(0)
        data = main._read_upload(UploadFile(file=stream, filename="large.txt"))
    assert len(data) == size


def test_engine_upload_reader_honors_explicit_limit() -> None:
    from cccc_desensitization import main
    from starlette.datastructures import UploadFile

    with tempfile.TemporaryFile() as stream:
        stream.write(b"x" * 32)
        stream.seek(0)
        with pytest.raises(Exception, match="配置上限"):
            main._read_upload(UploadFile(file=stream, filename="limited.txt"), 16)
