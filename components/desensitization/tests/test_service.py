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


@pytest.mark.parametrize("relative", ["../outside", "/absolute", "C:/outside", "a/../../b", "a//b", "a/.", "a.", "a "])
def test_confirmed_batch_output_rejects_invalid_paths(group_roots, relative):
    source = service.create_upload_path("group-a", "a.txt")
    source.write_text("sample")
    with pytest.raises(ValueError):
        service.create_batch_from_paths("group-a", [("a.txt", source)], options={
            "output_relative_path": relative, "scope_root": str(group_roots["group-a"] / "scope"), "scope_key": ""})
    assert source.exists()
    assert service.list_batches("group-a")["total"] == 0


def test_confirmed_output_is_exact_and_frozen(group_roots, monkeypatch):
    monkeypatch.setattr(service, "_enqueue", lambda *args: None)
    root = group_roots["group-a"] / "scope"
    options = {"output_relative_path": "脱敏后/确认目录", "scope_root": str(root), "scope_key": ""}
    source = service.create_upload_path("group-a", "a.txt")
    source.write_text("sample")
    batch = service.create_batch_from_paths("group-a", [("nested/a.txt", source)], options=options)
    assert Path(batch["output_path"]) == root / "脱敏后" / "确认目录"
    source.write_text("sample")
    with pytest.raises(ValueError, match="already exists"):
        service.create_batch_from_paths("group-a", [("a.txt", source)], options=options)
    monkeypatch.setattr(service, "resolve_scope_root", lambda _: group_roots["group-a"] / "changed")
    assert service.get_batch("group-a", batch["id"])["output_path"] == batch["output_path"]
    with pytest.raises(ValueError, match="working directory changed"):
        service.create_batch_from_paths("group-a", [("a.txt", source)], options=options)


def test_batch_partial_counts_and_report(group_roots, monkeypatch):
    import json
    monkeypatch.setattr(service, "_enqueue", lambda *args: None)
    source = service.create_upload_path("group-a", "a.txt")
    source.write_text("sample")
    batch = service.create_batch_from_paths("group-a", [("nested/a.txt", source)])
    monkeypatch.setattr(service, "get_analysis", lambda *args: {"entities": [], "boxes": []})
    monkeypatch.setattr(service, "mask_file", lambda *args: {"status": "partial"})
    service._run_batch("group-a", batch["id"])
    result = service.get_batch("group-a", batch["id"])
    assert result["status"] == "partial" and result["completed"] == 1 and result["failed"] == 0
    report = json.loads(Path(result["report_path"]).read_text(encoding="utf-8"))
    assert report["items"][0]["status"] == "partial"
    assert "analysis" not in report["items"][0]


def test_confirmed_output_rejects_symlink_escape(group_roots):
    root = group_roots["group-a"] / "scope"
    source = service.create_upload_path("group-a", "a.txt")
    source.write_text("sample")
    link = root / "link"
    try:
        link.symlink_to(group_roots["group-b"], target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require Windows developer mode")
    with pytest.raises(ValueError):
        service.create_batch_from_paths("group-a", [("a.txt", source)], options={
            "output_relative_path": "link/output", "scope_root": str(root), "scope_key": ""})
    assert source.exists()


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


def test_sensitive_crud_and_policy_persistence(tmp_path, monkeypatch):
    from cccc_desensitization import main
    root = tmp_path / "engine"
    root.mkdir()
    monkeypatch.setattr(engine, "_load_engine", lambda: main)
    monkeypatch.setattr(engine, "group_engine_root", lambda _: root)
    monkeypatch.setattr(engine, "model_root", lambda: tmp_path / "models")
    payload = {"mode": "literal", "value": "secret", "name": "sample", "enabled": True}
    rule_id = service.create_custom_sensitive("group-a", payload)["id"]
    service.update_custom_sensitive("group-a", rule_id, {**payload, "value": "changed", "enabled": False})
    item = service.list_custom_sensitive("group-a")["items"][0]
    assert item["value"] == "changed" and not item["enabled"]
    service.put_policies("group-a", {"CUSTOM": {"text_action": "replace", "replacement": "masked"}})
    assert service.get_policies("group-a")["policies"]["CUSTOM"]["replacement"] == "masked"
    service.delete_custom_sensitive("group-a", rule_id)
    assert service.list_custom_sensitive("group-a")["items"] == []


def test_docx_service_end_to_end(tmp_path, monkeypatch):
    import io
    from docx import Document
    from cccc_desensitization import main
    from test_embedded_images import sample
    roots = {"group-a": tmp_path / "a", "group-b": tmp_path / "b"}
    for root in roots.values(): root.mkdir()
    monkeypatch.setattr(service, "load_group", lambda gid: SimpleNamespace(path=roots[gid], doc={}))
    monkeypatch.setattr(service, "resolve_scope_root", lambda gid: roots[gid])
    def engine_root(gid):
        root = roots[gid] / "engine"
        (root / "tasks").mkdir(parents=True, exist_ok=True)
        return root
    monkeypatch.setattr(service, "group_engine_root", engine_root)
    monkeypatch.setattr(engine, "group_engine_root", engine_root)
    monkeypatch.setattr(engine, "model_root", lambda: tmp_path / "models")
    monkeypatch.setattr(engine, "_load_engine", lambda: main)
    monkeypatch.setattr(main, "analyze", lambda text: [{"id": "text", "text": "secret", "start": 0, "end": 6, "type": "CUSTOM", "selected": True}])
    monkeypatch.setenv("ONECOLLEAGUE_DESENSITIZATION_MIN_FREE_DISK_BYTES", "0")
    monkeypatch.setattr(main, "_analyze_image_content", lambda *args: (40, 30, [], [], [{"code": "OCR_FAILED", "message": "private content"}]))
    data, _ = sample()
    analysis = service.analyze_file("group-a", data, "sample.docx")
    task_id = analysis["analysis_id"]
    image = analysis["embedded_images"][0]
    assert image["source_preview"].endswith(image["image_id"])
    assert service.embedded_image("group-a", task_id, image["image_id"]).startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="task not found"):
        service.embedded_image("group-b", task_id, image["image_id"])
    result = service.mask_file("group-a", task_id, policies={"CUSTOM": {"text_action": "replace", "replacement": "hidden"}})
    assert result["status"] == "partial"
    assert service.list_tasks("group-a")["items"][0]["status"] == "partial"
    document = Document(io.BytesIO(Path(result["artifact"]).read_bytes()))
    assert document.paragraphs[0].text == "hidden"
    report = service.task_file("group-a", task_id, "report")[0].read_text(encoding="utf-8")
    assert "private content" not in report and "secret" not in report
    preview = service.preview_file("group-a", task_id, {"policies": {"CUSTOM": {"text_action": "replace", "replacement": "hidden"}}})
    assert service.embedded_image("group-a", task_id, image["image_id"], preview["preview_id"]).startswith(b"\x89PNG")
