from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..kernel.group import load_group
from ..util.fs import atomic_write_text
from .models import WorkflowDefinition
from .triggers import validate_trigger


class WorkflowNotFound(KeyError):
    pass


class RevisionConflict(RuntimeError):
    def __init__(self, current_revision: int):
        super().__init__("workflow was updated by another editor")
        self.current_revision = current_revision


class WorkflowStore:
    MAX_WORKFLOWS = 100

    def __init__(self, home: Path):
        self.home = home
        self._lock = threading.RLock()

    def _group(self, group_id: str):
        group = load_group(group_id)
        if group is None:
            raise WorkflowNotFound(f"group {group_id} not found")
        return group

    def root(self, group_id: str) -> Path:
        return self._group(group_id).path / "computer-control" / "workflows"

    def state_root(self, group_id: str) -> Path:
        return self._group(group_id).path / "state" / "computer-control"

    def _workflow_dir(self, group_id: str, workflow_id: str) -> Path:
        if not workflow_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in workflow_id):
            raise WorkflowNotFound("invalid workflow id")
        return self.root(group_id) / workflow_id

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkflowNotFound(str(path)) from exc
        if not isinstance(doc, dict):
            raise WorkflowNotFound(str(path))
        return doc

    @staticmethod
    def _write_json(path: Path, value: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

    def list(self, group_id: str, *, include_archived: bool = False) -> List[Dict[str, Any]]:
        root = self.root(group_id)
        if not root.exists():
            return []
        result = []
        for path in root.iterdir():
            if not path.is_dir():
                continue
            try:
                manifest = self._read_json(path / "manifest.json")
            except WorkflowNotFound:
                continue
            if not include_archived and manifest.get("archived"):
                continue
            result.append(manifest)
        return sorted(result, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    def get(self, group_id: str, workflow_id: str, *, version: Optional[int] = None) -> Dict[str, Any]:
        directory = self._workflow_dir(group_id, workflow_id)
        manifest = self._read_json(directory / "manifest.json")
        target = int(version or manifest.get("current_version") or 0)
        definition = self._read_json(directory / "versions" / f"{target}.json")
        return {"manifest": manifest, "version": target, "definition": definition}

    def create(self, group_id: str, definition: WorkflowDefinition, *, created_by: str = "user") -> Dict[str, Any]:
        with self._lock:
            group = self._group(group_id)
            for trigger in definition.triggers:
                validate_trigger(trigger, group_root=group.path)
            if len(self.list(group_id, include_archived=True)) >= self.MAX_WORKFLOWS:
                raise ValueError("workflow limit reached")
            workflow_id = "wf_" + uuid.uuid4().hex[:12]
            now = time.time()
            manifest = {
                "workflow_id": workflow_id,
                "name": definition.name,
                "description": definition.description,
                "revision": 1,
                "current_version": 1,
                "published_version": None,
                "trusted": {},
                "archived": False,
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
            }
            directory = self._workflow_dir(group_id, workflow_id)
            self._write_json(directory / "versions" / "1.json", definition.model_dump(mode="json"))
            self._write_json(directory / "manifest.json", manifest)
            return self.get(group_id, workflow_id)

    def update(
        self,
        group_id: str,
        workflow_id: str,
        definition: WorkflowDefinition,
        *,
        expected_revision: int,
        changed_by: str = "user",
        change_note: str = "",
    ) -> Dict[str, Any]:
        del changed_by
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            group = self._group(group_id)
            for trigger in definition.triggers:
                validate_trigger(trigger, group_root=group.path)
            manifest = self._read_json(directory / "manifest.json")
            current_revision = int(manifest.get("revision") or 0)
            if current_revision != expected_revision:
                raise RevisionConflict(current_revision)
            version = int(manifest.get("current_version") or 0) + 1
            payload = definition.model_dump(mode="json")
            payload["change_note"] = change_note
            self._write_json(directory / "versions" / f"{version}.json", payload)
            manifest.update(
                {
                    "name": definition.name,
                    "description": definition.description,
                    "revision": current_revision + 1,
                    "current_version": version,
                    "updated_at": time.time(),
                }
            )
            self._write_json(directory / "manifest.json", manifest)
            return self.get(group_id, workflow_id)

    def versions(self, group_id: str, workflow_id: str) -> List[Dict[str, Any]]:
        directory = self._workflow_dir(group_id, workflow_id)
        manifest = self._read_json(directory / "manifest.json")
        current = int(manifest.get("current_version") or 0)
        result = []
        for version in range(current, 0, -1):
            doc = self._read_json(directory / "versions" / f"{version}.json")
            result.append({"version": version, "name": doc.get("name"), "change_note": doc.get("change_note", "")})
        return result

    def create_proposal(
        self,
        group_id: str,
        workflow_id: str,
        definition: WorkflowDefinition,
        *,
        base_version: int,
        created_by: str,
        summary: str,
    ) -> Dict[str, Any]:
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            self._read_json(directory / "versions" / f"{base_version}.json")
            proposal_id = "proposal_" + uuid.uuid4().hex[:12]
            proposal = {
                "proposal_id": proposal_id,
                "workflow_id": workflow_id,
                "base_version": base_version,
                "created_by": created_by,
                "summary": str(summary or "AI 自适应优化建议")[:500],
                "created_at": time.time(),
                "status": "pending",
                "definition": definition.model_dump(mode="json"),
            }
            self._write_json(directory / "proposals" / f"{proposal_id}.json", proposal)
            return proposal

    def proposals(self, group_id: str, workflow_id: str) -> List[Dict[str, Any]]:
        root = self._workflow_dir(group_id, workflow_id) / "proposals"
        if not root.exists():
            return []
        values = []
        for path in root.glob("proposal_*.json"):
            try:
                values.append(self._read_json(path))
            except WorkflowNotFound:
                continue
        return sorted(values, key=lambda item: float(item.get("created_at") or 0), reverse=True)

    def decide_proposal(self, group_id: str, workflow_id: str, proposal_id: str, *, accept: bool) -> Dict[str, Any]:
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            path = directory / "proposals" / f"{proposal_id}.json"
            proposal = self._read_json(path)
            if str(proposal.get("status") or "pending") != "pending":
                raise ValueError("optimization proposal has already been reviewed")
            if accept:
                manifest = self._read_json(directory / "manifest.json")
                definition = WorkflowDefinition.model_validate(proposal.get("definition") or {})
                created = self.update(
                    group_id,
                    workflow_id,
                    definition,
                    expected_revision=int(manifest.get("revision") or 0),
                    changed_by="user",
                    change_note=f"accepted optimization proposal {proposal_id}",
                )
                proposal["accepted_version"] = created["version"]
                proposal["status"] = "accepted"
            else:
                proposal["status"] = "rejected"
            proposal["reviewed_at"] = time.time()
            self._write_json(path, proposal)
            return proposal

    def publish(self, group_id: str, workflow_id: str, version: int) -> Dict[str, Any]:
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            manifest = self._read_json(directory / "manifest.json")
            self._read_json(directory / "versions" / f"{version}.json")
            manifest["published_version"] = version
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.time()
            self._write_json(directory / "manifest.json", manifest)
            return manifest

    def rollback(self, group_id: str, workflow_id: str, version: int) -> Dict[str, Any]:
        """Create a new editable version from an immutable historical version."""
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            manifest = self._read_json(directory / "manifest.json")
            source = self._read_json(directory / "versions" / f"{version}.json")
            next_version = int(manifest.get("current_version") or 0) + 1
            source.pop("change_note", None)
            source["change_note"] = f"rollback from version {version}"
            self._write_json(directory / "versions" / f"{next_version}.json", source)
            manifest["current_version"] = next_version
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.time()
            manifest["published_version"] = None
            self._write_json(directory / "manifest.json", manifest)
            return self.get(group_id, workflow_id)

    def trust(self, group_id: str, workflow_id: str, version: int, *, fingerprint: str, permissions: List[str]) -> Dict[str, Any]:
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            manifest = self._read_json(directory / "manifest.json")
            self._read_json(directory / "versions" / f"{version}.json")
            trusted = manifest.get("trusted") if isinstance(manifest.get("trusted"), dict) else {}
            trusted[str(version)] = {
                "group_id": group_id,
                "fingerprint": fingerprint,
                "permissions": permissions,
                "trusted_at": time.time(),
            }
            manifest["trusted"] = trusted
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            self._write_json(directory / "manifest.json", manifest)
            return manifest

    def revoke_stale_trust(self, fingerprint: str) -> int:
        changed = 0
        groups_root = self.home / "groups"
        if not groups_root.exists():
            return 0
        with self._lock:
            for manifest_path in groups_root.glob("*/computer-control/workflows/*/manifest.json"):
                try:
                    manifest = self._read_json(manifest_path)
                except WorkflowNotFound:
                    continue
                trusted = manifest.get("trusted") if isinstance(manifest.get("trusted"), dict) else {}
                kept = {key: val for key, val in trusted.items() if isinstance(val, dict) and val.get("fingerprint") == fingerprint}
                if kept != trusted:
                    manifest["trusted"] = kept
                    self._write_json(manifest_path, manifest)
                    changed += 1
        return changed

    def archive(self, group_id: str, workflow_id: str, archived: bool = True) -> Dict[str, Any]:
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            manifest = self._read_json(directory / "manifest.json")
            manifest["archived"] = bool(archived)
            manifest["revision"] = int(manifest.get("revision") or 0) + 1
            manifest["updated_at"] = time.time()
            self._write_json(directory / "manifest.json", manifest)
            return manifest

    def delete(self, group_id: str, workflow_id: str) -> None:
        with self._lock:
            directory = self._workflow_dir(group_id, workflow_id)
            if not directory.exists():
                raise WorkflowNotFound(workflow_id)
            shutil.rmtree(directory)

    def duplicate(self, group_id: str, workflow_id: str) -> Dict[str, Any]:
        current = self.get(group_id, workflow_id)
        definition = dict(current["definition"])
        definition.pop("change_note", None)
        definition["name"] = f"{definition.get('name', 'Workflow')} copy"
        return self.create(group_id, WorkflowDefinition.model_validate(definition))

    @staticmethod
    def fingerprint(package_version: str, tools: List[Dict[str, Any]]) -> str:
        stable = [
            {"name": item.get("name"), "inputSchema": item.get("inputSchema", {})}
            for item in sorted(tools, key=lambda row: str(row.get("name") or ""))
        ]
        payload = json.dumps({"package_version": package_version, "tools": stable}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
