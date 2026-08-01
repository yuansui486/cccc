from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch


class TestCapabilityOps(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def _call(self, op: str, args: dict):
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def _write_allowlist_override(self, *, mcp_registry_level: str = "mounted", extra: str = "") -> Path:
        home = Path(str(os.environ.get("CCCC_HOME") or "")).expanduser()
        cfg_dir = home / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        path = cfg_dir / "capability-allowlist.user.yaml"
        body = (
            "defaults:\n"
            "  source_level:\n"
            f"    mcp_registry_official: {mcp_registry_level}\n"
            "    anthropic_skills: mounted\n"
            "    github_skills_curated: indexed\n"
            "    onecolleague_builtin: enabled\n"
        )
        if extra.strip():
            body = f"{body}{extra.rstrip()}\n"
        path.write_text(
            body,
            encoding="utf-8",
        )
        return path

    def _create_group(self, title: str = "capability-test") -> str:
        create_resp, _ = self._call("group_create", {"title": title, "topic": "", "by": "user"})
        self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
        gid = str((create_resp.result or {}).get("group_id") or "").strip()
        self.assertTrue(gid)
        return gid

    def _add_actor(self, group_id: str, actor_id: str, *, by: str = "user") -> None:
        add_resp, _ = self._call(
            "actor_add",
            {
                "group_id": group_id,
                "actor_id": actor_id,
                "runtime": "codex",
                "runner": "headless",
                "by": by,
            },
        )
        self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

    def _skill_zip_bytes(self, files: dict[str, str] | None = None) -> bytes:
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in (files or {"demo-skill/SKILL.md": "Use this full skill package.\n"}).items():
                zf.writestr(name, text)
        return buf.getvalue()

    def _seed_runtime_external_install(
        self,
        ops: Any,
        runtime_doc: dict,
        *,
        capability_id: str = "mcp:test-server",
        url: str = "http://127.0.0.1:9900/mcp",
        synthetic_tool_name: str = "onecolleague_ext_deadbeef_echo",
        real_tool_name: str = "echo",
        state: str = "installed",
        last_error: str = "",
        tools: Any = None,
    ) -> str:
        self._write_allowlist_override(mcp_registry_level="mounted")
        catalog_path, catalog_doc = ops._load_catalog_doc()
        catalog_doc["records"].setdefault(
            capability_id,
            {
                "capability_id": capability_id,
                "kind": "mcp_toolpack",
                "name": capability_id.rsplit(":", 1)[-1],
                "description_short": "Deterministic external MCP test fixture",
                "source_id": "mcp_registry_official",
                "source_tier": "official",
                "trust_tier": "official",
                "qualification_status": "qualified",
                "enable_supported": True,
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": url},
            },
        )
        ops._save_catalog_doc(catalog_path, catalog_doc)
        rec = {
            "install_mode": "remote_only",
            "install_spec": {"transport": "http", "url": url},
        }
        install_key = ops._external_artifact_cache_key(rec, capability_id=capability_id)
        artifact_id = ops._external_artifact_id(rec, capability_id=capability_id)
        normalized_tools = (
            list(tools)
            if isinstance(tools, list)
            else [
                {
                    "name": synthetic_tool_name,
                    "real_tool_name": real_tool_name,
                    "description": f"{real_tool_name} tool",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ]
        )
        install_payload = {
            "state": state,
            "installer": "remote_http",
            "install_mode": "remote_only",
            "invoker": {"type": "remote_http", "url": url},
            "tools": normalized_tools,
            "last_error": last_error,
            "updated_at": "2026-02-25T00:00:00Z",
        }
        artifact = ops._artifact_entry_from_install(
            install_payload,
            artifact_id=artifact_id,
            install_key=install_key,
            capability_id=capability_id,
        )
        ops._upsert_runtime_artifact_for_capability(
            runtime_doc,
            artifact_id=artifact_id,
            capability_id=capability_id,
            artifact_entry=artifact,
        )
        return artifact_id

    def test_capability_state_defaults_to_core_surface(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            state_resp, _ = self._call("capability_state", {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"})
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            result = state_resp.result if isinstance(state_resp.result, dict) else {}
            visible = result.get("visible_tools") if isinstance(result.get("visible_tools"), list) else []
            self.assertIn("onecolleague_help", visible)
            self.assertIn("onecolleague_capability_search", visible)
            self.assertIn("onecolleague_file", visible)
            self.assertNotIn("onecolleague_space", visible)
        finally:
            cleanup()

    def test_onecolleague_skill_library_refresh_confirm_and_rollback(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            calls: list[tuple[str, dict[str, Any]]] = []
            platform_record = {
                "capability_id": "skill:onecolleague:test-skill",
                "kind": "skill",
                "name": "test-skill",
                "description_short": "Test skill",
                "source_uri": "http://skills.local/skills/test-skill",
                "source_record_id": "test-skill",
                "source_record_version": "1.0.0",
                "updated_at_source": "2026-04-28T00:00:00Z",
                "tags": ["skill", "onecolleague"],
                "trust_tier": "tier1",
                "source_tier": "tier1",
                "qualification_status": "qualified",
                "capsule_text": "Use this test skill for focused verification.",
                "requires_capabilities": [],
            }
            platform_hash = "sha256:" + hashlib.sha256(
                json.dumps(platform_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            platform_record["content_hash"] = platform_hash

            def fake_get_json_obj(url: str, **kwargs: Any) -> dict[str, Any]:
                calls.append((url, dict(kwargs)))
                self.assertNotIn("headers", kwargs)
                if url.endswith("/source/metadata"):
                    return {
                        "source_id": "onecolleague_skill_library",
                        "display_name": "OneColleague Skill Library",
                        "schema_version": "1",
                    }
                if "/capabilities/index" in url:
                    return {
                        "server_time": str(platform_record.get("updated_at_source") or "2026-04-28T00:00:00Z"),
                        "items": [
                            {
                                "capability_id": "skill:onecolleague:test-skill",
                                "kind": "skill",
                                "name": "test-skill",
                                "description_short": str(platform_record.get("description_short") or ""),
                                "source_record_id": str(platform_record.get("source_record_id") or ""),
                                "source_record_version": str(platform_record.get("source_record_version") or ""),
                                "updated_at_source": str(platform_record.get("updated_at_source") or ""),
                                "checksum": platform_hash,
                            }
                        ],
                    }
                if "/capabilities/records" in url:
                    return {"items": [dict(platform_record)]}
                raise AssertionError(f"unexpected url: {url}")

            with patch("no1.daemon.ops.capability_ops._onecolleague_source._http_get_json_obj", side_effect=fake_get_json_obj):
                update_resp, _ = self._call(
                    "capability_source_config_update",
                    {"subscription_link": "http://skills.local/api/v1/skill-library", "enabled": True, "by": "user"},
                )
                self.assertTrue(update_resp.ok, getattr(update_resp, "error", None))
                self.assertEqual((update_resp.result or {}).get("source", {}).get("subscription_link"), "http://skills.local/api/v1/skill-library")

                test_resp, _ = self._call("capability_source_test", {"by": "user"})
                self.assertTrue(test_resp.ok, getattr(test_resp, "error", None))

                refresh_resp, _ = self._call("capability_source_refresh", {"by": "user"})
                self.assertTrue(refresh_resp.ok, getattr(refresh_resp, "error", None))
                summary = (refresh_resp.result or {}).get("summary") or {}
                self.assertEqual(summary.get("new"), 1)
                self.assertEqual(summary.get("auto_imported"), 1)
                self.assertEqual(summary.get("pending"), 0)

            pending_resp, _ = self._call("capability_source_pending_list", {"by": "user"})
            self.assertTrue(pending_resp.ok, getattr(pending_resp, "error", None))
            items = (pending_resp.result or {}).get("items") or []
            self.assertEqual(items, [])

            overview_resp, _ = self._call(
                "capability_overview",
                {"query": "test-skill", "include_indexed": True, "limit": 20},
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            ids = {str(item.get("capability_id") or "") for item in ((overview_resp.result or {}).get("items") or [])}
            self.assertIn("skill:onecolleague:test-skill", ids)

            platform_record = dict(platform_record)
            platform_record["source_record_version"] = "1.0.1"
            platform_record["updated_at_source"] = "2026-04-28T01:00:00Z"
            platform_record["description_short"] = "Updated test skill"
            platform_record["capsule_text"] = "Use this updated test skill for focused verification."
            platform_record.pop("content_hash", None)
            platform_hash = "sha256:" + hashlib.sha256(
                json.dumps(platform_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            platform_record["content_hash"] = platform_hash

            with patch("no1.daemon.ops.capability_ops._onecolleague_source._http_get_json_obj", side_effect=fake_get_json_obj):
                refresh_resp, _ = self._call("capability_source_refresh", {"by": "user"})
                self.assertTrue(refresh_resp.ok, getattr(refresh_resp, "error", None))
                summary = (refresh_resp.result or {}).get("summary") or {}
                self.assertEqual(summary.get("updated"), 1)
                self.assertEqual(summary.get("pending"), 1)

            pending_resp, _ = self._call("capability_source_pending_list", {"by": "user"})
            self.assertTrue(pending_resp.ok, getattr(pending_resp, "error", None))
            items = (pending_resp.result or {}).get("items") or []
            self.assertEqual(len(items), 1)
            pending_id = str(items[0]["pending_id"])

            probe_resp, _ = self._call(
                "capability_source_pending_probe",
                {"group_id": gid, "by": "user", "actor_id": "user", "pending_ids": [pending_id]},
            )
            self.assertTrue(probe_resp.ok, getattr(probe_resp, "error", None))
            self.assertTrue(((probe_resp.result or {}).get("results") or [])[0].get("ok"))

            confirm_resp, _ = self._call(
                "capability_source_pending_confirm",
                {"group_id": gid, "by": "user", "actor_id": "user", "pending_ids": [pending_id]},
            )
            self.assertTrue(confirm_resp.ok, getattr(confirm_resp, "error", None))
            self.assertTrue(((confirm_resp.result or {}).get("results") or [])[0].get("ok"))

            overview_resp, _ = self._call(
                "capability_overview",
                {"query": "test-skill", "include_indexed": True, "limit": 20},
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            ids = {str(item.get("capability_id") or "") for item in ((overview_resp.result or {}).get("items") or [])}
            self.assertIn("skill:onecolleague:test-skill", ids)

            rollback_resp, _ = self._call("capability_source_rollback", {"pending_id": pending_id, "by": "user"})
            self.assertTrue(rollback_resp.ok, getattr(rollback_resp, "error", None))
            self.assertEqual((rollback_resp.result or {}).get("rollback_action"), "restored_previous")
        finally:
            cleanup()

    def test_onecolleague_skill_store_web_flow_surfaces_import_and_enable_state(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request
        from no1.ports.web.app import create_app
        from fastapi.testclient import TestClient

        _, cleanup = self._with_home()
        try:
            gid = self._create_group("skill-store-web")
            self._add_actor(gid, "agent1")
            platform_record = {
                "capability_id": "skill:onecolleague:store-flow",
                "kind": "skill",
                "name": "store-flow",
                "description_short": "Store flow skill",
                "source_uri": "http://skills.local/skills/store-flow",
                "source_record_id": "store-flow",
                "source_record_version": "1.2.3",
                "updated_at_source": "2026-04-29T00:00:00Z",
                "tags": ["skill", "onecolleague"],
                "trust_tier": "tier1",
                "source_tier": "tier1",
                "qualification_status": "qualified",
                "capsule_text": "Use this store flow skill for storefront verification.",
                "requires_capabilities": [],
            }
            platform_hash = "sha256:" + hashlib.sha256(
                json.dumps(platform_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            platform_record["content_hash"] = platform_hash

            def fake_get_json_obj(url: str, **kwargs: Any) -> dict[str, Any]:
                self.assertNotIn("headers", kwargs)
                if url.endswith("/source/metadata"):
                    return {
                        "source_id": "onecolleague_skill_library",
                        "display_name": "OneColleague Skill Library",
                        "schema_version": "1",
                    }
                if "/capabilities/index" in url:
                    return {
                        "server_time": "2026-04-29T00:00:00Z",
                        "items": [
                            {
                                "capability_id": platform_record["capability_id"],
                                "kind": "skill",
                                "name": "store-flow",
                                "description_short": "Store flow skill",
                                "source_record_id": "store-flow",
                                "source_record_version": "1.2.3",
                                "updated_at_source": "2026-04-29T00:00:00Z",
                                "checksum": platform_hash,
                            }
                        ],
                    }
                if "/capabilities/records" in url:
                    return {"items": [dict(platform_record)]}
                raise AssertionError(f"unexpected url: {url}")

            def local_call_daemon(req: dict[str, Any], **_: Any) -> dict[str, Any]:
                resp, _mutated = handle_request(DaemonRequest.model_validate(req))
                return resp.model_dump()

            with (
                patch("no1.daemon.ops.capability_ops._onecolleague_source._http_get_json_obj", side_effect=fake_get_json_obj),
                patch("no1.ports.web.app.call_daemon", side_effect=local_call_daemon),
            ):
                with TestClient(create_app()) as client:
                    source_resp = client.get("/api/v1/capabilities/sources/onecolleague_skill_library")
                    self.assertEqual(source_resp.status_code, 200)
                    self.assertTrue(source_resp.json().get("ok"), source_resp.json())

                    refresh_resp = client.post(
                        "/api/v1/capabilities/sources/onecolleague_skill_library/refresh",
                        json={"by": "user", "limit": 200},
                    )
                    self.assertEqual(refresh_resp.status_code, 200)
                    refresh_body = refresh_resp.json()
                    self.assertTrue(refresh_body.get("ok"), refresh_body)
                    self.assertEqual(((refresh_body.get("result") or {}).get("summary") or {}).get("new"), 1)
                    self.assertEqual(((refresh_body.get("result") or {}).get("summary") or {}).get("auto_imported"), 1)

                    pending_resp = client.get("/api/v1/capabilities/sources/onecolleague_skill_library/pending")
                    self.assertEqual(pending_resp.status_code, 200)
                    pending_body = pending_resp.json()
                    self.assertTrue(pending_body.get("ok"), pending_body)
                    pending_items = (pending_body.get("result") or {}).get("items") or []
                    self.assertEqual(pending_items, [])

                    overview_resp = client.get("/api/v1/capabilities/overview?query=store-flow&include_indexed=true&limit=20")
                    self.assertEqual(overview_resp.status_code, 200)
                    overview_body = overview_resp.json()
                    self.assertTrue(overview_body.get("ok"), overview_body)
                    overview_items = (overview_body.get("result") or {}).get("items") or []
                    self.assertIn("skill:onecolleague:store-flow", {str(item.get("capability_id") or "") for item in overview_items})

                    enable_resp = client.post(
                        f"/api/v1/groups/{gid}/capabilities/enable",
                        json={
                            "actor_id": "agent1",
                            "capability_id": "skill:onecolleague:store-flow",
                            "enabled": True,
                            "scope": "actor",
                            "ttl_seconds": 0,
                            "reason": "skill_store_test",
                        },
                    )
                    self.assertEqual(enable_resp.status_code, 200)
                    enable_body = enable_resp.json()
                    self.assertTrue(enable_body.get("ok"), enable_body)

                    state_resp = client.get(f"/api/v1/groups/{gid}/capabilities/state?actor_id=agent1")
                    self.assertEqual(state_resp.status_code, 200)
                    state_body = state_resp.json()
                    self.assertTrue(state_body.get("ok"), state_body)
                    state = state_body.get("result") or {}
                    self.assertIn("skill:onecolleague:store-flow", state.get("enabled_capabilities") or [])
                    active_ids = {str(item.get("capability_id") or "") for item in state.get("active_capsule_skills") or []}
                    self.assertIn("skill:onecolleague:store-flow", active_ids)
        finally:
            cleanup()

    def test_onecolleague_skill_package_confirm_installs_after_refresh_only(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        home, cleanup = self._with_home()
        old_codex_home = os.environ.get("CODEX_HOME")
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            workspace = Path(home) / "workspace"
            workspace.mkdir()
            attach_resp, _ = self._call("attach", {"group_id": gid, "path": str(workspace), "by": "user"})
            self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))
            source_codex = Path(home) / "source-codex"
            source_codex.mkdir()
            (source_codex / "auth.json").write_text('{"ok":true}\n', encoding="utf-8")
            (source_codex / "config.toml").write_text("[mcp_servers]\n", encoding="utf-8")
            os.environ["CODEX_HOME"] = str(source_codex)

            arbitrary_files = {
                "SKILL.md": "Use this complete package.\n",
                "playbooks/checklist.md": "- verify arbitrary directories\n",
                "bin-tools/run.sh": "echo ok\n",
                "knowledge base/notes.txt": "space-containing directory survives\n",
                "nested/custom/deep.json": '{"ok":true}\n',
            }
            package_bytes = self._skill_zip_bytes(arbitrary_files)
            package_sha = hashlib.sha256(package_bytes).hexdigest()
            platform_record = {
                "capability_id": "skill:onecolleague:demo-skill",
                "kind": "skill",
                "name": "demo-skill",
                "description_short": "Demo package skill",
                "source_uri": "http://skills.local/skills/demo-skill",
                "source_record_id": "demo-skill",
                "source_record_version": "1.0.0",
                "updated_at_source": "2026-04-28T00:00:00Z",
                "tags": ["skill", "onecolleague"],
                "trust_tier": "tier1",
                "source_tier": "tier1",
                "qualification_status": "qualified",
                "capsule_text": "Use this complete package.",
                "requires_capabilities": [],
                "install_mode": "codex_skill_package",
                "skill_package_url": "http://skills.local/packages/demo-skill.zip",
                "skill_package_sha256": package_sha,
                "skill_package_size": len(package_bytes),
                "package_format": "zip",
                "skill_slug": "demo-skill",
            }
            platform_hash = "sha256:" + hashlib.sha256(
                json.dumps(platform_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            platform_record["content_hash"] = platform_hash

            def fake_get_json_obj(url: str, **kwargs: Any) -> dict[str, Any]:
                self.assertNotIn("headers", kwargs)
                if "/capabilities/index" in url:
                    return {
                        "server_time": "2026-04-28T00:00:00Z",
                        "items": [
                            {
                                "capability_id": "skill:onecolleague:demo-skill",
                                "kind": "skill",
                                "name": "demo-skill",
                                "source_record_version": "1.0.0",
                                "checksum": platform_hash,
                            }
                        ],
                    }
                if "/capabilities/records" in url:
                    return {"items": [dict(platform_record)]}
                if url.endswith("/source/metadata"):
                    return {"source_id": "onecolleague_skill_library"}
                raise AssertionError(f"unexpected url: {url}")

            with patch("no1.daemon.ops.capability_ops._onecolleague_source._http_get_json_obj", side_effect=fake_get_json_obj), patch(
                "no1.daemon.ops.capability_ops._skill_packages._download_package_bytes",
                return_value=package_bytes,
            ) as download:
                refresh_resp, _ = self._call("capability_source_refresh", {"by": "user", "subscription_link": "http://skills.local/api/v1/skill-library"})
                self.assertTrue(refresh_resp.ok, getattr(refresh_resp, "error", None))
                download.assert_called_once()
                auto_imported = (refresh_resp.result or {}).get("auto_imported") or []
                self.assertEqual(len(auto_imported), 1)
                package_install = auto_imported[0].get("package_install") if isinstance(auto_imported[0], dict) else {}
                extracted = Path(str((package_install or {}).get("extracted_path") or ""))
                self.assertTrue((extracted / "SKILL.md").is_file())
                for rel_path in arbitrary_files:
                    self.assertTrue((extracted / rel_path).is_file(), rel_path)

                pending_resp, _ = self._call("capability_source_pending_list", {"by": "user"})
                self.assertEqual((pending_resp.result or {}).get("items") or [], [])

                catalog_path, catalog_doc = ops._load_catalog_doc()
                rec = catalog_doc.get("records", {}).get("skill:onecolleague:demo-skill")
                self.assertEqual(str((rec or {}).get("install_mode") or ""), "codex_skill_package")
                self.assertEqual(str(((rec or {}).get("install_spec") or {}).get("package_sha256") or ""), package_sha)

                requested_group = SimpleNamespace(
                    group_id=gid,
                    doc={
                        "capability_defaults": {
                            "autoload_capabilities": ["skill:onecolleague:demo-skill"]
                        },
                        "actors": [
                            {
                                "id": "peer-1",
                                "runtime": "codex",
                                "runner": "headless",
                                "capability_autoload": ["skill:onecolleague:demo-skill"],
                            }
                        ],
                    },
                )
                ops._state_path().unlink(missing_ok=True)
                self.assertEqual(
                    ops.prepare_codex_skill_package_overlay_for_actor(
                        requested_group,
                        "peer-1",
                        {},
                    ),
                    {},
                )

                enable_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": "skill:onecolleague:demo-skill",
                        "scope": "actor",
                        "enabled": True,
                    },
                )
                self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
                enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
                self.assertTrue(bool(enable_result.get("enabled")))
                self.assertEqual(str(enable_result.get("state") or ""), "runnable")
                project_package = enable_result.get("project_package") if isinstance(enable_result.get("project_package"), dict) else {}
                project_path = Path(str(project_package.get("project_path") or ""))
                managed_path = Path(str(project_package.get("managed_path") or ""))
                self.assertTrue(project_path.exists(), str(project_path))
                self.assertTrue(managed_path.exists(), str(managed_path))
                for rel_path in arbitrary_files:
                    self.assertTrue((project_path / rel_path).is_file(), rel_path)

                state_resp, _ = self._call("capability_state", {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"})
                self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
                state = state_resp.result if isinstance(state_resp.result, dict) else {}
                self.assertIn("skill:onecolleague:demo-skill", state.get("enabled_capabilities") or [])
                binding_states = state.get("external_binding_states") if isinstance(state.get("external_binding_states"), dict) else {}
                skill_binding = binding_states.get("skill:onecolleague:demo-skill") if isinstance(binding_states.get("skill:onecolleague:demo-skill"), dict) else {}
                self.assertEqual(str(skill_binding.get("state") or ""), "runnable")
                active_skills = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
                active_ids = {str(item.get("capability_id") or "") for item in active_skills if isinstance(item, dict)}
                self.assertIn("skill:onecolleague:demo-skill", active_ids)

                group = SimpleNamespace(
                    group_id=gid,
                    doc={
                        "actors": [
                            {
                                "id": "peer-1",
                                "runtime": "codex",
                                "runner": "headless",
                            }
                        ]
                    },
                )
                overlay_env = ops.prepare_codex_skill_package_overlay_for_actor(group, "peer-1", {})
                overlay = Path(str(overlay_env.get("CODEX_HOME") or ""))
                self.assertTrue(overlay.is_dir())
                for rel_path in arbitrary_files:
                    self.assertTrue((overlay / "skills" / "demo-skill" / rel_path).is_file(), rel_path)
                self.assertFalse((source_codex / "skills" / "demo-skill").exists())
                system_skill = overlay / "skills" / ".system" / "managed.md"
                system_skill.parent.mkdir(parents=True, exist_ok=True)
                system_skill.write_text("preserve system skill\n", encoding="utf-8")
                sibling_skill = (
                    overlay.parent / "peer-2" / "skills" / "sibling-skill" / "SKILL.md"
                )
                sibling_skill.parent.mkdir(parents=True, exist_ok=True)
                sibling_skill.write_text("preserve sibling actor\n", encoding="utf-8")

                state_path, state_doc = ops._load_state_doc()
                ops._set_blocked_capability(
                    state_doc,
                    scope="group",
                    group_id=gid,
                    capability_id="skill:onecolleague:demo-skill",
                    by="user",
                    reason="test",
                    ttl_seconds=0,
                )
                ops._save_state_doc(state_path, state_doc)
                self.assertEqual(
                    ops.prepare_codex_skill_package_overlay_for_actor(
                        requested_group,
                        "peer-1",
                        {},
                    ),
                    {},
                )
                self.assertFalse((overlay / "skills" / "demo-skill").exists())
                self.assertTrue(system_skill.is_file())
                self.assertTrue(sibling_skill.is_file())

                state_path, state_doc = ops._load_state_doc()
                ops._unset_blocked_capability(
                    state_doc,
                    scope="group",
                    group_id=gid,
                    capability_id="skill:onecolleague:demo-skill",
                )
                ops._set_removed_capability(
                    state_doc,
                    group_id=gid,
                    capability_id="skill:onecolleague:demo-skill",
                    removed=True,
                )
                ops._save_state_doc(state_path, state_doc)
                self.assertEqual(
                    ops.prepare_codex_skill_package_overlay_for_actor(
                        requested_group,
                        "peer-1",
                        {},
                    ),
                    {},
                )

                state_path, state_doc = ops._load_state_doc()
                ops._set_removed_capability(
                    state_doc,
                    group_id=gid,
                    capability_id="skill:onecolleague:demo-skill",
                    removed=False,
                )
                ops._save_state_doc(state_path, state_doc)
                disable_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": "skill:onecolleague:demo-skill",
                        "scope": "actor",
                        "enabled": False,
                    },
                )
                self.assertTrue(disable_resp.ok, getattr(disable_resp, "error", None))
                self.assertEqual(
                    ops.prepare_codex_skill_package_overlay_for_actor(
                        requested_group,
                        "peer-1",
                        {},
                    ),
                    {},
                )
                ops._state_path().write_text("{invalid", encoding="utf-8")
                self.assertEqual(
                    ops.prepare_codex_skill_package_overlay_for_actor(
                        requested_group,
                        "peer-1",
                        {},
                    ),
                    {},
                )

            self.assertFalse((Path(home) / ".codex" / "skills" / "demo-skill").exists())
        finally:
            if old_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_codex_home
            cleanup()

    def test_skill_package_enable_downloads_and_materializes_existing_catalog_record(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        home, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            workspace = Path(home) / "workspace"
            workspace.mkdir()
            attach_resp, _ = self._call("attach", {"group_id": gid, "path": str(workspace), "by": "user"})
            self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))

            package_files = {
                "SKILL.md": "Use image generation.\n",
                "shierkeji_image_create/scripts/peer_image_generate.py": "print('ok')\n",
            }
            package_bytes = self._skill_zip_bytes(package_files)
            package_sha = hashlib.sha256(package_bytes).hexdigest()
            cap_id = "skill:onecolleague_skill_library:image"

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"][cap_id] = {
                "capability_id": cap_id,
                "kind": "skill",
                "name": "image",
                "description_short": "Generate images",
                "source_id": "onecolleague_skill_library",
                "source_uri": "http://skills.local/skills/image",
                "source_record_version": "1.0.0",
                "qualification_status": "qualified",
                "capsule_text": "Use ./image/shierkeji_image_create/scripts/peer_image_generate.py.",
                "requires_capabilities": [],
                "install_mode": "codex_skill_package",
                "install_spec": {
                    "package_url": "http://skills.local/packages/image.zip",
                    "package_sha256": package_sha,
                    "package_size": len(package_bytes),
                    "package_format": "zip",
                    "skill_slug": "image",
                },
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            with patch(
                "no1.daemon.ops.capability_ops._skill_packages._download_package_bytes",
                return_value=package_bytes,
            ) as download:
                enable_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": cap_id,
                        "scope": "actor",
                        "enabled": True,
                    },
                )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            download.assert_called_once()
            result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            self.assertTrue(bool(result.get("enabled")))
            self.assertEqual(str(result.get("state") or ""), "runnable")
            project_package = result.get("project_package") if isinstance(result.get("project_package"), dict) else {}
            project_path = Path(str(project_package.get("project_path") or ""))
            self.assertEqual(project_path, workspace / "image")
            self.assertTrue((project_path / "SKILL.md").is_file())
            self.assertTrue((project_path / "shierkeji_image_create" / "scripts" / "peer_image_generate.py").is_file())
            self.assertTrue((workspace / "shierkeji_image_create" / "scripts" / "peer_image_generate.py").is_file())
            self.assertTrue((workspace / ".onecolleague" / "skills" / "image" / "SKILL.md").is_file())
            resource_paths = project_package.get("resource_paths") if isinstance(project_package.get("resource_paths"), list) else []
            self.assertIn(str(workspace / "shierkeji_image_create"), resource_paths)
            package_install = result.get("package_install") if isinstance(result.get("package_install"), dict) else {}
            self.assertEqual(str(package_install.get("package_sha256") or ""), package_sha)
        finally:
            cleanup()

    def test_skill_package_default_file_limit_allows_large_skill_package(self) -> None:
        from no1.daemon.ops.capability_ops import ensure_codex_skill_package_installed

        _, cleanup = self._with_home()
        old_limit = os.environ.get("CCCC_SKILL_PACKAGE_MAX_FILES")
        try:
            package_files = {"large-skill/SKILL.md": "Use this large skill package.\n"}
            for idx in range(1001):
                package_files[f"large-skill/assets/file-{idx:04d}.txt"] = "ok\n"
            package_bytes = self._skill_zip_bytes(package_files)
            rec = {
                "capability_id": "skill:onecolleague_skill_library:large",
                "kind": "skill",
                "name": "large",
                "install_mode": "codex_skill_package",
                "install_spec": {
                    "package_url": "http://skills.local/packages/large.zip",
                    "package_sha256": hashlib.sha256(package_bytes).hexdigest(),
                    "package_size": len(package_bytes),
                    "package_format": "zip",
                    "skill_slug": "large",
                    "package_version": "1.0.0",
                },
            }

            with patch("no1.daemon.ops.capability_ops._skill_packages._download_package_bytes", return_value=package_bytes):
                installed = ensure_codex_skill_package_installed(rec)
            self.assertEqual(str(installed.get("state") or ""), "installed")
            extracted = Path(str(installed.get("extracted_path") or ""))
            self.assertTrue((extracted / "SKILL.md").is_file())
            self.assertTrue((extracted / "assets" / "file-1000.txt").is_file())

            os.environ["CCCC_SKILL_PACKAGE_MAX_FILES"] = "1000"
            rec["capability_id"] = "skill:onecolleague_skill_library:large-limited"
            with patch("no1.daemon.ops.capability_ops._skill_packages._download_package_bytes", return_value=package_bytes):
                with self.assertRaisesRegex(ValueError, "too many files"):
                    ensure_codex_skill_package_installed(rec)
        finally:
            if old_limit is None:
                os.environ.pop("CCCC_SKILL_PACKAGE_MAX_FILES", None)
            else:
                os.environ["CCCC_SKILL_PACKAGE_MAX_FILES"] = old_limit
            cleanup()

    def test_skill_package_rejects_zip_slip_and_symlink(self) -> None:
        from no1.daemon.ops.capability_ops import ensure_codex_skill_package_installed

        _, cleanup = self._with_home()
        try:
            zip_slip = self._skill_zip_bytes({"../SKILL.md": "bad\n"})
            rec = {
                "capability_id": "skill:onecolleague:bad",
                "kind": "skill",
                "name": "bad",
                "install_mode": "codex_skill_package",
                "install_spec": {
                    "package_url": "http://skills.local/bad.zip",
                    "package_sha256": hashlib.sha256(zip_slip).hexdigest(),
                    "package_size": len(zip_slip),
                    "package_format": "zip",
                    "skill_slug": "bad",
                },
            }
            with patch("no1.daemon.ops.capability_ops._skill_packages._download_package_bytes", return_value=zip_slip):
                with self.assertRaisesRegex(ValueError, "unsafe package path"):
                    ensure_codex_skill_package_installed(rec)

            import io

            buf = io.BytesIO()
            info = zipfile.ZipInfo("bad/link")
            info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("bad/SKILL.md", "ok\n")
                zf.writestr(info, "SKILL.md")
            symlink_zip = buf.getvalue()
            rec["install_spec"]["package_sha256"] = hashlib.sha256(symlink_zip).hexdigest()
            rec["install_spec"]["package_size"] = len(symlink_zip)
            with patch("no1.daemon.ops.capability_ops._skill_packages._download_package_bytes", return_value=symlink_zip):
                with self.assertRaisesRegex(ValueError, "unsupported package entry type"):
                    ensure_codex_skill_package_installed(rec)
        finally:
            cleanup()

    def test_skill_package_import_requires_zip_skill_md_entrypoint(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            base_record = {
                "capability_id": "skill:onecolleague:bad-entry",
                "kind": "skill",
                "name": "bad-entry",
                "description_short": "Bad entry",
                "source_record_id": "bad-entry",
                "source_record_version": "1.0.0",
                "capsule_text": "Bad package metadata.",
                "install_mode": "codex_skill_package",
                "skill_package_url": "http://skills.local/bad-entry.zip",
                "skill_package_sha256": "a" * 64,
                "skill_package_size": 123,
                "package_format": "zip",
                "entrypoint": "README.md",
            }
            resp, _ = self._call(
                "capability_import",
                {"group_id": gid, "actor_id": "peer-1", "record": base_record, "dry_run": True, "probe": False, "by": "peer-1"},
            )
            self.assertFalse(resp.ok)
            self.assertIn("entrypoint must be SKILL.md", str(getattr(resp, "error", None).message if getattr(resp, "error", None) else ""))

            bad_format = dict(base_record)
            bad_format["entrypoint"] = "SKILL.md"
            bad_format["package_format"] = "tar"
            resp, _ = self._call(
                "capability_import",
                {"group_id": gid, "actor_id": "peer-1", "record": bad_format, "dry_run": True, "probe": False, "by": "peer-1"},
            )
            self.assertFalse(resp.ok)
            self.assertIn("package_format must be zip", str(getattr(resp, "error", None).message if getattr(resp, "error", None) else ""))
        finally:
            cleanup()

    def test_onecolleague_pending_confirm_revalidates_content_hash_before_install(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            record = {
                "capability_id": "skill:onecolleague:tampered",
                "kind": "skill",
                "name": "tampered",
                "description_short": "Tampered package skill",
                "source_id": "onecolleague_skill_library",
                "source_record_id": "tampered",
                "source_record_version": "1.0.0",
                "updated_at_source": "2026-04-28T00:00:00Z",
                "tags": ["skill"],
                "trust_tier": "tier1",
                "source_tier": "tier1",
                "qualification_status": "qualified",
                "capsule_text": "This pending item was modified after refresh.",
                "requires_capabilities": [],
                "install_mode": "codex_skill_package",
                "install_spec": {
                    "package_url": "http://skills.local/tampered.zip",
                    "package_sha256": "a" * 64,
                    "package_size": 123,
                    "package_format": "zip",
                    "skill_slug": "tampered",
                },
            }
            record_hash = "sha256:" + hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            record["capsule_text"] = "This pending item was modified after refresh and hash capture."
            pending_doc = {
                "v": 1,
                "items": {
                    "p1": {
                        "pending_id": "p1",
                        "source_id": "onecolleague_skill_library",
                        "capability_id": "skill:onecolleague:tampered",
                        "status": "new",
                        "checksum": "sha256:not-the-current-record",
                        "record_content_hash": record_hash,
                        "record": record,
                    }
                },
            }
            pending_path = Path(os.environ["CCCC_HOME"]) / "state" / "capabilities" / "onecolleague_skill_library_pending.json"
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.write_text(json.dumps(pending_doc), encoding="utf-8")

            with patch("no1.daemon.ops.capability_ops._skill_packages._download_package_bytes") as download:
                confirm_resp, _ = self._call(
                    "capability_source_pending_confirm",
                    {"group_id": gid, "by": "user", "actor_id": "user", "pending_ids": ["p1"]},
                )
            self.assertTrue(confirm_resp.ok, getattr(confirm_resp, "error", None))
            download.assert_not_called()
            result = ((confirm_resp.result or {}).get("results") or [])[0]
            self.assertFalse(bool(result.get("ok")))
            self.assertEqual(((result.get("error") or {}).get("code")), "pending_record_hash_mismatch")
        finally:
            cleanup()

    def test_onecolleague_skill_library_default_uses_proxy_domain_and_migrates_legacy_default(self) -> None:
        _, cleanup = self._with_home()
        try:
            expected = "https://dongdongkc.shierkeji.com:5205/onecolleague_agent/api/v1/skill-library"
            config_resp, _ = self._call("capability_source_config_get", {"by": "user"})
            self.assertTrue(config_resp.ok, getattr(config_resp, "error", None))
            self.assertEqual((config_resp.result or {}).get("source", {}).get("subscription_link"), expected)

            legacy_resp, _ = self._call(
                "capability_source_config_update",
                {"subscription_link": "http://dongdongkc.top:8012/api/v1/skill-library", "enabled": True, "by": "user"},
            )
            self.assertTrue(legacy_resp.ok, getattr(legacy_resp, "error", None))
            self.assertEqual((legacy_resp.result or {}).get("source", {}).get("subscription_link"), expected)
        finally:
            cleanup()

    def test_onecolleague_skill_library_refresh_rejects_hash_mismatch(self) -> None:
        _, cleanup = self._with_home()
        try:
            def fake_get_json_obj(url: str, **kwargs: Any) -> dict[str, Any]:
                self.assertNotIn("headers", kwargs)
                if "/capabilities/index" in url:
                    return {
                        "items": [
                            {
                                "capability_id": "skill:onecolleague:bad-hash",
                                "kind": "skill",
                                "name": "bad-hash",
                                "description_short": "Bad hash",
                                "source_record_id": "bad-hash",
                                "source_record_version": "1.0.0",
                                "updated_at_source": "2026-04-28T00:00:00Z",
                                "checksum": "sha256:not-the-real-hash",
                            }
                        ],
                    }
                if "/capabilities/records" in url:
                    return {
                        "items": [
                            {
                                "capability_id": "skill:onecolleague:bad-hash",
                                "kind": "skill",
                                "name": "bad-hash",
                                "description_short": "Bad hash",
                                "source_uri": "http://skills.local/skills/bad-hash",
                                "source_record_id": "bad-hash",
                                "source_record_version": "1.0.0",
                                "updated_at_source": "2026-04-28T00:00:00Z",
                                "tags": ["skill"],
                                "trust_tier": "tier1",
                                "source_tier": "tier1",
                                "qualification_status": "qualified",
                                "capsule_text": "This record should not import because the hash is wrong.",
                                "requires_capabilities": [],
                                "content_hash": "sha256:not-the-real-hash",
                            }
                        ]
                    }
                raise AssertionError(f"unexpected url: {url}")

            with patch("no1.daemon.ops.capability_ops._onecolleague_source._http_get_json_obj", side_effect=fake_get_json_obj):
                update_resp, _ = self._call(
                    "capability_source_config_update",
                    {"subscription_link": "http://skills.local/api/v1/skill-library", "enabled": True, "by": "user"},
                )
                self.assertTrue(update_resp.ok, getattr(update_resp, "error", None))
                refresh_resp, _ = self._call("capability_source_refresh", {"by": "user"})
                self.assertTrue(refresh_resp.ok, getattr(refresh_resp, "error", None))
                result = refresh_resp.result or {}
                self.assertEqual((result.get("summary") or {}).get("invalid"), 1)
                self.assertEqual((result.get("summary") or {}).get("pending"), 0)
                self.assertIn("content_hash_mismatch", ((result.get("invalid") or [])[0].get("error") or ""))

            pending_resp, _ = self._call("capability_source_pending_list", {"by": "user"})
            self.assertTrue(pending_resp.ok, getattr(pending_resp, "error", None))
            self.assertEqual((pending_resp.result or {}).get("items"), [])
        finally:
            cleanup()

    def test_group_settings_update_persists_capability_defaults(self) -> None:
        from no1.daemon.ops import capability_ops as ops
        from no1.kernel.group import load_group

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            capability_id = "skill:anthropic:triage"
            patch = {
                "group_id": gid,
                "patch": {
                    "capability_defaults": {
                        "autoload_capabilities": [capability_id],
                        "default_scope": "session",
                        "session_ttl_seconds": 7200,
                    },
                },
                "by": "user",
            }
            resp, _ = self._call("group_settings_update", patch)
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            settings = result.get("settings") if isinstance(result.get("settings"), dict) else {}
            defaults = settings.get("capability_defaults") if isinstance(settings.get("capability_defaults"), dict) else {}
            self.assertEqual(defaults.get("default_scope"), "session")
            self.assertEqual(defaults.get("session_ttl_seconds"), 7200)
            self.assertIn(capability_id, defaults.get("autoload_capabilities") or [])

            group = load_group(gid)
            self.assertIsNotNone(group)
            stored = (group.doc.get("capability_defaults") if group is not None else {}) or {}
            self.assertEqual(stored.get("default_scope"), "session")
            self.assertIn(capability_id, stored.get("autoload_capabilities") or [])

            def load_state() -> dict[str, Any]:
                state_resp, _ = self._call(
                    "capability_state",
                    {"group_id": gid, "actor_id": "user", "by": "user"},
                )
                self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
                return state_resp.result if isinstance(state_resp.result, dict) else {}

            def assert_requested_but_not_effective(state: dict[str, Any]) -> None:
                self.assertIn(
                    capability_id,
                    state.get("group_requested_autoload_capabilities") or [],
                )
                self.assertNotIn(
                    capability_id,
                    state.get("group_autoload_capabilities") or [],
                )
                self.assertNotIn(capability_id, state.get("autoload_capabilities") or [])
                autoload_skills = (
                    state.get("autoload_skills")
                    if isinstance(state.get("autoload_skills"), list)
                    else []
                )
                autoload_ids = {
                    str(item.get("capability_id") or "")
                    for item in autoload_skills
                    if isinstance(item, dict)
                }
                self.assertNotIn(capability_id, autoload_ids)

            assert_requested_but_not_effective(load_state())

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"][capability_id] = {
                "capability_id": capability_id,
                "kind": "skill",
                "name": "triage",
                "description_short": "Issue triage checklist",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "user",
                    "capability_id": capability_id,
                    "scope": "group",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enabled_state = load_state()
            self.assertIn(capability_id, enabled_state.get("group_autoload_capabilities") or [])
            self.assertIn(capability_id, enabled_state.get("autoload_capabilities") or [])
            enabled_autoload_skills = (
                enabled_state.get("autoload_skills")
                if isinstance(enabled_state.get("autoload_skills"), list)
                else []
            )
            self.assertIn(
                capability_id,
                {
                    str(item.get("capability_id") or "")
                    for item in enabled_autoload_skills
                    if isinstance(item, dict)
                },
            )

            state_path, state_doc = ops._load_state_doc()
            ops._set_blocked_capability(
                state_doc,
                scope="group",
                group_id=gid,
                capability_id=capability_id,
                by="user",
                reason="test",
                ttl_seconds=0,
            )
            ops._save_state_doc(state_path, state_doc)
            assert_requested_but_not_effective(load_state())

            state_path, state_doc = ops._load_state_doc()
            ops._unset_blocked_capability(
                state_doc,
                scope="group",
                group_id=gid,
                capability_id=capability_id,
            )
            ops._set_removed_capability(
                state_doc,
                group_id=gid,
                capability_id=capability_id,
                removed=True,
            )
            ops._save_state_doc(state_path, state_doc)
            assert_requested_but_not_effective(load_state())

            state_path, state_doc = ops._load_state_doc()
            ops._set_removed_capability(
                state_doc,
                group_id=gid,
                capability_id=capability_id,
                removed=False,
            )
            ops._save_state_doc(state_path, state_doc)
            disable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "user",
                    "capability_id": capability_id,
                    "scope": "group",
                    "enabled": False,
                },
            )
            self.assertTrue(disable_resp.ok, getattr(disable_resp, "error", None))
            assert_requested_but_not_effective(load_state())
        finally:
            cleanup()

    def test_mcp_tool_listing_never_recovers_capability_tools_from_yaml(self) -> None:
        from no1.daemon.ops import capability_ops as ops
        from no1.ports.mcp import server as mcp_server

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            group = ops._ensure_group(gid)
            actor = next(
                item
                for item in group.doc.get("actors") or []
                if isinstance(item, dict) and str(item.get("id") or "") == "peer-1"
            )
            actor["capability_autoload"] = ["pack:space"]
            group.save()

            runtime_context = Mock(
                group_id=gid,
                actor_id="peer-1",
                source="remote",
            )
            cases = (
                ("daemon_failure", RuntimeError("daemon unavailable"), None),
                ("state_missing", None, {}),
                ("empty_visible_tools", None, {"visible_tools": [], "dynamic_tools": []}),
            )
            for label, error, result in cases:
                with self.subTest(case=label), patch.dict(
                    os.environ,
                    {"CCCC_MCP_TOOL_PROFILE": ""},
                    clear=False,
                ), patch.object(
                    mcp_server,
                    "_runtime_context",
                    return_value=runtime_context,
                ), patch.object(
                    mcp_server,
                    "_call_daemon_or_raise",
                    side_effect=error,
                    return_value=result,
                ):
                    names = {
                        str(item.get("name") or "")
                        for item in mcp_server.list_tools_for_caller()
                        if isinstance(item, dict)
                    }
                self.assertIn("onecolleague_help", names)
                self.assertNotIn("onecolleague_space", names)
        finally:
            cleanup()

    def test_enable_session_pack_updates_visible_tools(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "pack:space",
                    "scope": "session",
                    "ttl_seconds": 600,
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            self.assertTrue(bool(enable_result.get("refresh_required")))

            state_resp, _ = self._call("capability_state", {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"})
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            result = state_resp.result if isinstance(state_resp.result, dict) else {}
            visible = result.get("visible_tools") if isinstance(result.get("visible_tools"), list) else []
            self.assertIn("onecolleague_space", visible)
            self.assertIn("pack:space", result.get("enabled_capabilities") or [])
            enabled_rows = result.get("enabled") if isinstance(result.get("enabled"), list) else []
            enabled_row = next(
                (
                    item
                    for item in enabled_rows
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "pack:space"
                ),
                {},
            )
            self.assertEqual(str(enabled_row.get("scope") or ""), "session")
            self.assertEqual(str(enabled_row.get("actor_id") or ""), "peer-1")
            self.assertGreater(int(enabled_row.get("ttl_seconds") or 0), 0)
        finally:
            cleanup()

    def test_non_foreman_cannot_enable_group_scope(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            self._add_actor(gid, "peer-2", by="user")
            resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-2",
                    "actor_id": "peer-2",
                    "capability_id": "pack:space",
                    "scope": "group",
                    "enabled": True,
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual((resp.error.code if resp.error else ""), "permission_denied")
        finally:
            cleanup()

    def test_search_returns_builtin_records_without_network(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "space",
                    "include_external": False,
                    "limit": 20,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            ids = {str(item.get("capability_id") or "") for item in items if isinstance(item, dict)}
            self.assertIn("pack:space", ids)
        finally:
            cleanup()

    def test_search_without_query_returns_builtin_packs(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "",
                    "kind": "mcp_toolpack",
                    "include_external": False,
                    "limit": 20,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            ids = {str(item.get("capability_id") or "") for item in items if isinstance(item, dict)}
            self.assertIn("pack:space", ids)
            self.assertIn("pack:group-runtime", ids)
            self.assertGreaterEqual(len(ids), 5)
        finally:
            cleanup()

    def test_search_without_external_catalog_returns_builtin_skill_for_symptom_query(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "web startup",
                    "kind": "skill",
                    "include_external": False,
                    "limit": 20,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            skill = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "skill:onecolleague:runtime-bootstrap"
                ),
                {},
            )
            self.assertEqual(str(skill.get("source_id") or ""), "onecolleague_builtin")
            self.assertEqual(str(skill.get("kind") or ""), "skill")
        finally:
            cleanup()

    def test_search_empty_query_uses_context_signal_for_pack_ranking(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            create_resp, _ = self._call(
                "context_sync",
                {
                    "group_id": gid,
                    "by": "user",
                    "ops": [
                        {
                            "op": "task.create",
                            "title": "Automation reminder cleanup",
                            "outcome": "stabilize reminder jobs",
                            "assignee": "peer-1",
                        }
                    ],
                },
            )
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            tasks_resp, _ = self._call("task_list", {"group_id": gid})
            self.assertTrue(tasks_resp.ok, getattr(tasks_resp, "error", None))
            tasks = tasks_resp.result.get("tasks") if isinstance(tasks_resp.result, dict) else []
            self.assertTrue(isinstance(tasks, list) and tasks)
            task_id = str((tasks[0] if isinstance(tasks[0], dict) else {}).get("id") or "")
            self.assertTrue(task_id)
            sync_agent_resp, _ = self._call(
                "context_sync",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "ops": [
                        {
                            "op": "agent_state.update",
                            "actor_id": "peer-1",
                            "active_task_id": task_id,
                            "focus": "automation schedule hygiene",
                        }
                    ],
                },
            )
            self.assertTrue(sync_agent_resp.ok, getattr(sync_agent_resp, "error", None))

            resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "",
                    "kind": "mcp_toolpack",
                    "include_external": False,
                    "limit": 5,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            self.assertTrue(items)
            top = items[0] if isinstance(items[0], dict) else {}
            self.assertEqual(str(top.get("capability_id") or ""), "pack:automation")
        finally:
            cleanup()

    def test_search_builtin_pack_includes_tool_names(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "space",
                    "include_external": False,
                    "limit": 20,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            pack = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "pack:space"
                ),
                {},
            )
            tool_names = pack.get("tool_names") if isinstance(pack.get("tool_names"), list) else []
            self.assertIn("onecolleague_space", tool_names)
            self.assertGreaterEqual(int(pack.get("tool_count") or 0), len(tool_names))
        finally:
            cleanup()

    def test_allowlist_overlay_update_validate_and_reset(self) -> None:
        _, cleanup = self._with_home()
        try:
            get_before, _ = self._call("capability_allowlist_get", {"by": "user"})
            self.assertTrue(get_before.ok, getattr(get_before, "error", None))
            before = get_before.result if isinstance(get_before.result, dict) else {}
            revision_before = str(before.get("revision") or "")
            self.assertTrue(revision_before)
            self.assertEqual(str(before.get("external_capability_safety_mode") or ""), "normal")

            validate, _ = self._call(
                "capability_allowlist_validate",
                {
                    "mode": "patch",
                    "patch": {
                        "defaults": {"source_level": {"mcp_registry_official": "indexed"}},
                    },
                },
            )
            self.assertTrue(validate.ok, getattr(validate, "error", None))
            validate_result = validate.result if isinstance(validate.result, dict) else {}
            self.assertTrue(bool(validate_result.get("valid")))

            update, _ = self._call(
                "capability_allowlist_update",
                {
                    "by": "user",
                    "mode": "patch",
                    "expected_revision": revision_before,
                    "patch": {
                        "defaults": {"source_level": {"mcp_registry_official": "indexed"}},
                    },
                },
            )
            self.assertTrue(update.ok, getattr(update, "error", None))
            update_result = update.result if isinstance(update.result, dict) else {}
            revision_after = str(update_result.get("revision") or "")
            self.assertTrue(revision_after)
            self.assertNotEqual(revision_after, revision_before)
            effective = update_result.get("effective") if isinstance(update_result.get("effective"), dict) else {}
            defaults = effective.get("defaults") if isinstance(effective.get("defaults"), dict) else {}
            source_level = defaults.get("source_level") if isinstance(defaults.get("source_level"), dict) else {}
            self.assertEqual(str(source_level.get("mcp_registry_official") or ""), "indexed")

            get_after, _ = self._call("capability_allowlist_get", {"by": "user"})
            self.assertTrue(get_after.ok, getattr(get_after, "error", None))
            fresh_revision = str((get_after.result or {}).get("revision") or "")
            self.assertEqual(revision_after, fresh_revision)

            reset, _ = self._call("capability_allowlist_reset", {"by": "user"})
            self.assertTrue(reset.ok, getattr(reset, "error", None))
            reset_result = reset.result if isinstance(reset.result, dict) else {}
            self.assertEqual(str(reset_result.get("external_capability_safety_mode") or ""), "normal")
            overlay_after_reset = (
                reset_result.get("overlay") if isinstance(reset_result.get("overlay"), dict) else {}
            )
            self.assertEqual(overlay_after_reset, {})
        finally:
            cleanup()

    def test_allowlist_update_rejects_revision_mismatch(self) -> None:
        _, cleanup = self._with_home()
        try:
            first, _ = self._call(
                "capability_allowlist_update",
                {
                    "by": "user",
                    "mode": "patch",
                    "patch": {"defaults": {"source_level": {"anthropic_skills": "mounted"}}},
                },
            )
            self.assertTrue(first.ok, getattr(first, "error", None))
            stale_revision = "deadbeef"
            second, _ = self._call(
                "capability_allowlist_update",
                {
                    "by": "user",
                    "mode": "patch",
                    "expected_revision": stale_revision,
                    "patch": {"defaults": {"source_level": {"anthropic_skills": "indexed"}}},
                },
            )
            self.assertFalse(second.ok)
            self.assertEqual(getattr(second.error, "code", ""), "allowlist_revision_mismatch")
        finally:
            cleanup()

    def test_current_admission_invalidates_policy_errors_and_recovers(self) -> None:
        from no1.daemon.ops import capability_ops as ops
        from no1.daemon.ops.capability_ops import _policy
        from no1.daemon.ops.capability_ops._admission import resolve_current_admission

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            enabled, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "scope": "actor",
                    "capability_id": "pack:space",
                    "enabled": True,
                },
            )
            self.assertTrue(enabled.ok, getattr(enabled, "error", None))
            baseline = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertIn("pack:space", baseline.get("admitted_capabilities") or [])
            baseline_revision = str(baseline.get("policy_revision") or "")

            overlay = _policy._allowlist_user_overlay_path()
            overlay.parent.mkdir(parents=True, exist_ok=True)
            overlay.write_text("defaults: [\n", encoding="utf-8")
            _policy._clear_policy_cache()
            broken = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertTrue(bool(broken.get("stable")))
            self.assertFalse(bool(broken.get("valid")))
            self.assertNotEqual(str(broken.get("policy_revision") or ""), baseline_revision)
            self.assertIn("invalid_overlay_yaml", str(broken.get("policy_error") or ""))
            self.assertEqual(broken.get("admitted_capabilities"), [])

            self._write_allowlist_override(mcp_registry_level="mounted")
            _policy._clear_policy_cache()
            repaired = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertTrue(bool(repaired.get("valid")))
            self.assertIn("pack:space", repaired.get("admitted_capabilities") or [])

            default_doc, default_text, _ = _policy._load_allowlist_default_doc_with_error()
            repaired_snapshot = _policy._allowlist_effective_snapshot()
            with patch.object(
                _policy,
                "_load_allowlist_default_doc_with_error",
                return_value=({}, default_text, "failed_to_read_default:test"),
            ):
                failed_snapshot = _policy._allowlist_effective_snapshot()
                _policy._clear_policy_cache()
                failed_policy = _policy._allowlist_policy()
            self.assertNotEqual(failed_snapshot.get("revision"), repaired_snapshot.get("revision"))
            self.assertEqual(failed_snapshot.get("default_error"), "failed_to_read_default:test")
            self.assertTrue(default_doc)
            self.assertTrue(all(value is False for value in failed_policy.get("source_enabled", {}).values()))
            _policy._clear_policy_cache()
        finally:
            cleanup()

    def test_current_admission_revalidates_sources_catalog_and_external_runtime(self) -> None:
        from no1.daemon.ops import capability_ops as ops
        from no1.daemon.ops.capability_ops._admission import resolve_current_admission

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="mounted")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            skill_id = "skill:anthropic:current-admission"
            external_id = "mcp:current-admission"
            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"][skill_id] = {
                "capability_id": skill_id,
                "kind": "skill",
                "name": "current-admission",
                "source_id": "anthropic_skills",
                "qualification_status": "qualified",
                "enable_supported": True,
            }
            catalog_doc["records"][external_id] = {
                "capability_id": external_id,
                "kind": "mcp_toolpack",
                "name": "current-admission",
                "source_id": "mcp_registry_official",
                "qualification_status": "qualified",
                "enable_supported": True,
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9919/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)
            state_path, state_doc = ops._load_state_doc()
            for capability_id in (skill_id, external_id):
                ops._set_enabled_capability(
                    state_doc,
                    group_id=gid,
                    actor_id="peer-1",
                    scope="actor",
                    capability_id=capability_id,
                    enabled=True,
                    ttl_seconds=3600,
                )
            ops._save_state_doc(state_path, state_doc)

            initial = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertIn(skill_id, initial.get("authorized_capabilities") or [])
            self.assertIn(skill_id, initial.get("admitted_capabilities") or [])
            self.assertIn(external_id, initial.get("authorized_capabilities") or [])
            self.assertNotIn(external_id, initial.get("admitted_capabilities") or [])
            self.assertEqual(
                (initial.get("denied_capabilities") or {}).get(external_id),
                "runtime_not_executable",
            )

            with patch.dict(
                os.environ,
                {"CCCC_CAPABILITY_SOURCE_ANTHROPIC_SKILLS_ENABLED": "0"},
                clear=False,
            ):
                source_disabled = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertIn(skill_id, source_disabled.get("raw_bindings") or [])
            self.assertNotIn(skill_id, source_disabled.get("authorized_capabilities") or [])
            self.assertEqual(
                (source_disabled.get("denied_capabilities") or {}).get(skill_id),
                "source_disabled_by_runtime_config",
            )

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"][skill_id]["qualification_status"] = "blocked"
            ops._save_catalog_doc(catalog_path, catalog_doc)
            downgraded = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertNotIn(skill_id, downgraded.get("admitted_capabilities") or [])
            self.assertEqual(
                (downgraded.get("denied_capabilities") or {}).get(skill_id),
                "qualification_not_qualified",
            )

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(
                ops,
                runtime_doc,
                capability_id=external_id,
                synthetic_tool_name="onecolleague_ext_current_echo",
                real_tool_name="echo",
            )
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id=external_id,
                artifact_id=artifact_id,
                state="runnable",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)
            executable = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertIn(external_id, executable.get("admitted_capabilities") or [])
            self.assertEqual(len(executable.get("external_tool_grants") or []), 1)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            runtime_doc["artifacts"][artifact_id]["tools"] = []
            ops._save_runtime_doc(runtime_path, runtime_doc)
            empty_manifest = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertIn(external_id, empty_manifest.get("authorized_capabilities") or [])
            self.assertNotIn(external_id, empty_manifest.get("admitted_capabilities") or [])
            with patch(
                "no1.daemon.ops.capability_ops._invoke_installed_external_tool_with_aliases",
            ) as invoke:
                call_resp, _ = self._call(
                    "capability_tool_call",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "capability_id": external_id,
                        "tool_name": "echo",
                        "arguments": {},
                    },
                )
            self.assertFalse(call_resp.ok)
            self.assertEqual(getattr(call_resp.error, "code", ""), "capability_tool_not_found")
            invoke.assert_not_called()
        finally:
            cleanup()

    def test_builtin_capsule_skill_admission_does_not_require_catalog_record(self) -> None:
        from no1.daemon.ops.capability_ops._admission import resolve_current_admission

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            enabled, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "scope": "actor",
                    "capability_id": "skill:onecolleague:install",
                    "enabled": True,
                },
            )
            self.assertTrue(enabled.ok, getattr(enabled, "error", None))
            admission = resolve_current_admission(group_id=gid, actor_id="peer-1")
            self.assertIn("skill:onecolleague:install", admission.get("admitted_capabilities") or [])
            record = (admission.get("admitted_records") or {}).get("skill:onecolleague:install") or {}
            self.assertEqual(record.get("source_id"), "onecolleague_builtin")
            self.assertEqual(record.get("qualification_status"), "qualified")
        finally:
            cleanup()

    def test_search_without_external_never_triggers_auto_sync(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            with patch("no1.daemon.ops.capability_ops._auto_sync_catalog", return_value=False) as auto_sync:
                resp, _ = self._call(
                    "capability_search",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "query": "",
                        "include_external": False,
                        "limit": 10,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            auto_sync.assert_not_called()
        finally:
            cleanup()

    def test_search_with_external_never_triggers_auto_sync(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            with patch("no1.daemon.ops.capability_ops._auto_sync_catalog", return_value=False) as auto_sync:
                resp, _ = self._call(
                    "capability_search",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "query": "",
                        "include_external": True,
                        "limit": 10,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            auto_sync.assert_not_called()
        finally:
            cleanup()

    def test_search_with_external_ignores_auto_sync_flag(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            with patch.dict(os.environ, {"CCCC_CAPABILITY_SEARCH_AUTO_SYNC": "1"}, clear=False), patch(
                "no1.daemon.ops.capability_ops._auto_sync_catalog",
                return_value=False,
            ) as auto_sync:
                resp, _ = self._call(
                    "capability_search",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "query": "",
                        "include_external": True,
                        "limit": 10,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            auto_sync.assert_not_called()
        finally:
            cleanup()

    def test_sync_anthropic_skills_accepts_github_list_payload(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        class _Resp:
            def __init__(self, text: str) -> None:
                self._body = text.encode("utf-8")

            def read(self) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        catalog = ops._new_catalog_doc()
        skill_md = (
            "---\n"
            "name: example-skill\n"
            "description: Example skill\n"
            "license: MIT\n"
            "---\n"
            "body\n"
        )
        with patch(
            "no1.daemon.ops.capability_ops._http_get_json",
            return_value=[{"type": "dir", "name": "example-skill", "sha": "abc123"}],
        ), patch("no1.daemon.ops.capability_ops.urlopen", return_value=_Resp(skill_md)):
            upserted = ops._sync_anthropic_skills_source(catalog, force=True)

        self.assertEqual(upserted, 1)
        record = catalog.get("records", {}).get("skill:anthropic:example-skill")
        self.assertIsInstance(record, dict)
        self.assertEqual(record.get("name"), "example-skill")
        source_state = catalog.get("sources", {}).get("anthropic_skills", {})
        self.assertEqual(source_state.get("sync_state"), "fresh")

    def test_auto_sync_respects_source_enable_flags(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        catalog = ops._new_catalog_doc()
        with patch.dict(
            os.environ,
            {
                "CCCC_CAPABILITY_SOURCE_MCP_REGISTRY_ENABLED": "1",
                "CCCC_CAPABILITY_SOURCE_ANTHROPIC_SKILLS_ENABLED": "0",
            },
            clear=False,
        ), patch("no1.daemon.ops.capability_ops._sync_mcp_registry_source", return_value=0), patch(
            "no1.daemon.ops.capability_ops._sync_anthropic_skills_source",
            side_effect=AssertionError("anthropic source should be disabled"),
        ):
            changed = ops._auto_sync_catalog(catalog)

        self.assertTrue(changed)
        sources = catalog.get("sources", {})
        anthropic = sources.get("anthropic_skills", {})
        self.assertEqual(anthropic.get("sync_state"), "disabled")
        self.assertEqual(anthropic.get("error"), "source_disabled_by_policy")

    def test_sync_capability_catalog_once_saves_when_changed(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        fake_path = Path("/tmp/fake-capability-catalog.json")
        fake_doc = ops._new_catalog_doc()
        with patch(
            "no1.daemon.ops.capability_ops._load_catalog_doc",
            return_value=(fake_path, fake_doc),
        ), patch(
            "no1.daemon.ops.capability_ops._sync_catalog",
            return_value={"changed": True, "upserted_total": 2, "upserted": {"mcp_registry_official": 2}},
        ), patch(
            "no1.daemon.ops.capability_ops._save_catalog_doc",
        ) as save_doc:
            result = ops.sync_capability_catalog_once(force=True)

        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("changed"))
        self.assertEqual(int(result.get("upserted_total") or 0), 2)
        save_doc.assert_called_once_with(fake_path, fake_doc)

    def test_external_enable_succeeds_for_qualified_external(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            catalog_path, catalog = ops._load_catalog_doc()
            catalog["records"]["mcp:test-server"] = {
                "capability_id": "mcp:test-server",
                "kind": "mcp_toolpack",
                "name": "test-server",
                "source_id": "manual_import",
                "source_tier": "local",
                "trust_tier": "local",
                "qualification_status": "qualified",
                "enable_supported": True,
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9900/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog)
            installed = {
                "state": "installed",
                "installer": "remote_http",
                "install_mode": "remote_only",
                "invoker": {"type": "remote_http", "url": "http://127.0.0.1:9900/mcp"},
                "tools": [],
                "last_error": "",
                "updated_at": "2026-02-25T00:00:00Z",
            }
            with patch(
                "no1.daemon.ops.capability_ops._install_external_capability",
                return_value=installed,
            ):
                resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": "mcp:test-server",
                        "scope": "session",
                        "enabled": True,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "activation_pending")
            self.assertTrue(bool(result.get("enabled")))
        finally:
            cleanup()

    def test_external_enable_installs_and_exposes_dynamic_tools(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            catalog_path, catalog = ops._load_catalog_doc()
            catalog["records"]["mcp:test-server"] = {
                "capability_id": "mcp:test-server",
                "kind": "mcp_toolpack",
                "name": "test-server",
                "source_id": "manual_import",
                "source_tier": "local",
                "trust_tier": "local",
                "qualification_status": "qualified",
                "enable_supported": True,
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9900/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog)
            installed = {
                "state": "installed",
                "installer": "remote_http",
                "install_mode": "remote_only",
                "invoker": {"type": "remote_http", "url": "http://127.0.0.1:9900/mcp"},
                "tools": [
                    {
                        "name": "onecolleague_ext_deadbeef_echo",
                        "real_tool_name": "echo",
                        "description": "echo tool",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    }
                ],
                "last_error": "",
                "updated_at": "2026-02-25T00:00:00Z",
            }
            with patch(
                "no1.daemon.ops.capability_ops._install_external_capability",
                return_value=installed,
            ):
                enable_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": "mcp:test-server",
                        "scope": "session",
                        "enabled": True,
                    },
                )
                self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
                state_resp, _ = self._call(
                    "capability_state",
                    {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
                )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            visible = state.get("visible_tools") if isinstance(state.get("visible_tools"), list) else []
            self.assertIn("onecolleague_ext_deadbeef_echo", visible)
            dynamic = state.get("dynamic_tools") if isinstance(state.get("dynamic_tools"), list) else []
            names = {str(x.get("name") or "") for x in dynamic if isinstance(x, dict)}
            self.assertIn("onecolleague_ext_deadbeef_echo", names)
        finally:
            cleanup()

    def test_external_enable_reports_degraded_install_state(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            catalog = ops._new_catalog_doc()
            catalog["records"]["mcp:test-server"] = {
                "capability_id": "mcp:test-server",
                "kind": "mcp_toolpack",
                "name": "test-server",
                "qualification_status": "qualified",
                "install_mode": "package",
                "install_spec": {"registry_type": "npm", "identifier": "@example/mcp-server"},
            }
            installed = {
                "state": "installed_degraded",
                "installer": "npm_npx",
                "install_mode": "package",
                "invoker": {"type": "package_stdio", "command": ["npx", "-y", "@example/mcp-server"]},
                "tools": [],
                "last_error": "stdio mcp request timed out",
                "last_error_code": "probe_timeout",
                "updated_at": "2026-02-25T00:00:00Z",
            }
            with patch("no1.daemon.ops.capability_ops._load_catalog_doc", return_value=(Path("/tmp/cat.json"), catalog)), patch(
                "no1.daemon.ops.capability_ops._install_external_capability",
                return_value=installed,
            ):
                resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": "mcp:test-server",
                        "scope": "session",
                        "enabled": True,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "activation_pending")
            self.assertTrue(bool(result.get("degraded")))
            self.assertEqual(str(result.get("install_state") or ""), "installed_degraded")
            self.assertEqual(str(result.get("install_error_code") or ""), "probe_timeout")
            self.assertIn("tools_not_listed_call_capability_use", str(result.get("degraded_call_hint") or ""))
        finally:
            cleanup()

    def test_capability_tool_call_invokes_enabled_dynamic_tool(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            # Seed enabled state directly for deterministic test.
            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(ops, runtime_doc)
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            with patch(
                "no1.daemon.ops.capability_ops._invoke_installed_external_tool",
                return_value={"content": [{"type": "text", "text": "ok"}]},
            ):
                resp, _ = self._call(
                    "capability_tool_call",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "tool_name": "onecolleague_ext_deadbeef_echo",
                        "arguments": {"message": "hello"},
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("capability_id") or ""), "mcp:test-server")
            call_result = result.get("result") if isinstance(result.get("result"), dict) else {}
            self.assertIn("content", call_result)
        finally:
            cleanup()

    def test_capability_tool_call_accepts_real_tool_name_with_capability_hint(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(ops, runtime_doc)
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            with patch(
                "no1.daemon.ops.capability_ops._invoke_installed_external_tool",
                return_value={"content": [{"type": "text", "text": "ok"}]},
            ):
                resp, _ = self._call(
                    "capability_tool_call",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "capability_id": "mcp:test-server",
                        "tool_name": "echo",
                        "arguments": {"message": "hello"},
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("capability_id") or ""), "mcp:test-server")
            self.assertEqual(str(result.get("resolved_tool_name") or ""), "onecolleague_ext_deadbeef_echo")
            self.assertEqual(str(result.get("real_tool_name") or ""), "echo")
        finally:
            cleanup()

    def test_capability_tool_call_real_name_requires_capability_when_ambiguous(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            for cap_id in ("mcp:test-server-a", "mcp:test-server-b"):
                ops._set_enabled_capability(
                    state_doc,
                    group_id=gid,
                    actor_id="peer-1",
                    scope="session",
                    capability_id=cap_id,
                    enabled=True,
                    ttl_seconds=600,
                )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_a = self._seed_runtime_external_install(
                ops,
                runtime_doc,
                capability_id="mcp:test-server-a",
                synthetic_tool_name="onecolleague_ext_deadbeef_echo_a",
                real_tool_name="echo",
            )
            artifact_b = self._seed_runtime_external_install(
                ops,
                runtime_doc,
                capability_id="mcp:test-server-b",
                synthetic_tool_name="onecolleague_ext_deadbeef_echo_b",
                real_tool_name="echo",
            )
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server-a",
                artifact_id=artifact_a,
                state="activation_pending",
                last_error="",
            )
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server-b",
                artifact_id=artifact_b,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            resp, _ = self._call(
                "capability_tool_call",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "tool_name": "echo",
                    "arguments": {"message": "hello"},
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual((resp.error.code if resp.error else ""), "capability_tool_ambiguous")
        finally:
            cleanup()

    def test_disable_external_capability_hides_dynamic_tools(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:test-server"] = {
                "capability_id": "mcp:test-server",
                "kind": "mcp_toolpack",
                "name": "test-server",
                "qualification_status": "qualified",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9900/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(ops, runtime_doc)
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            before_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(before_resp.ok, getattr(before_resp, "error", None))
            before_state = before_resp.result if isinstance(before_resp.result, dict) else {}
            before_visible = before_state.get("visible_tools") if isinstance(before_state.get("visible_tools"), list) else []
            self.assertIn("onecolleague_ext_deadbeef_echo", before_visible)

            disable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "mcp:test-server",
                    "scope": "session",
                    "enabled": False,
                },
            )
            self.assertTrue(disable_resp.ok, getattr(disable_resp, "error", None))

            after_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(after_resp.ok, getattr(after_resp, "error", None))
            after_state = after_resp.result if isinstance(after_resp.result, dict) else {}
            after_visible = after_state.get("visible_tools") if isinstance(after_state.get("visible_tools"), list) else []
            self.assertNotIn("onecolleague_ext_deadbeef_echo", after_visible)
        finally:
            cleanup()

    def test_external_install_failure_persists_runtime_failure_state(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:test-server"] = {
                "capability_id": "mcp:test-server",
                "kind": "mcp_toolpack",
                "name": "test-server",
                "qualification_status": "qualified",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9900/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            with patch(
                "no1.daemon.ops.capability_ops._install_external_capability",
                side_effect=RuntimeError("probe_failed"),
            ):
                resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": "mcp:test-server",
                        "scope": "session",
                        "enabled": True,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "blocked")
            self.assertEqual(str(result.get("reason") or ""), "install_failed:probe_failed")
            self.assertEqual(str(result.get("install_error_code") or ""), "probe_failed")
            self.assertTrue(bool(result.get("retryable")))
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
            self.assertTrue(diagnostics)
            first_diag = diagnostics[0] if isinstance(diagnostics[0], dict) else {}
            self.assertEqual(str(first_diag.get("code") or ""), "probe_failed")

            _, runtime_doc = ops._load_runtime_doc()
            _, entry = ops._runtime_install_for_capability(runtime_doc, capability_id="mcp:test-server")
            self.assertIsInstance(entry, dict)
            self.assertEqual(str(entry.get("state") or ""), "install_failed")
            self.assertIn("probe_failed", str(entry.get("last_error") or ""))
        finally:
            cleanup()

    def test_external_install_failure_classifies_runtime_dependency_missing(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:test-server"] = {
                "capability_id": "mcp:test-server",
                "kind": "mcp_toolpack",
                "name": "test-server",
                "qualification_status": "qualified",
                "install_mode": "package",
                "install_spec": {"registry_type": "npm", "identifier": "@example/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            with patch(
                "no1.daemon.ops.capability_ops._install_external_capability",
                side_effect=RuntimeError("stdio mcp exited with code 1: Error: Cannot find module 'foo'"),
            ):
                resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": "mcp:test-server",
                        "scope": "session",
                        "enabled": True,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "blocked")
            self.assertEqual(str(result.get("reason") or ""), "install_failed:runtime_dependency_missing")
            self.assertEqual(str(result.get("install_error_code") or ""), "runtime_dependency_missing")
            self.assertFalse(bool(result.get("retryable")))
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
            self.assertTrue(diagnostics)
            first_diag = diagnostics[0] if isinstance(diagnostics[0], dict) else {}
            self.assertEqual(str(first_diag.get("code") or ""), "runtime_dependency_missing")
        finally:
            cleanup()

    def test_capability_tool_call_rejects_tool_when_capability_not_enabled(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(ops, runtime_doc)
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            resp, _ = self._call(
                "capability_tool_call",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "tool_name": "onecolleague_ext_deadbeef_echo",
                    "arguments": {"message": "hello"},
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual((resp.error.code if resp.error else ""), "capability_tool_not_found")
        finally:
            cleanup()

    def test_capability_tool_call_requires_actor_runtime_binding(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            self._seed_runtime_external_install(ops, runtime_doc)
            # Intentionally no runtime actor binding for peer-1.
            ops._save_runtime_doc(runtime_path, runtime_doc)

            resp, _ = self._call(
                "capability_tool_call",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "tool_name": "onecolleague_ext_deadbeef_echo",
                    "arguments": {"message": "hello"},
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual((resp.error.code if resp.error else ""), "capability_tool_not_found")
        finally:
            cleanup()

    def test_capability_tool_call_accepts_hyphen_underscore_alias(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(
                ops,
                runtime_doc,
                capability_id="mcp:test-server",
                synthetic_tool_name="onecolleague_ext_deadbeef_resolve_library_id",
                real_tool_name="resolve_library_id",
                state="installed",
            )
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            with patch(
                "no1.daemon.ops.capability_ops._invoke_installed_external_tool_with_aliases",
                return_value=({"ok": True}, "resolve_library_id"),
            ):
                resp, _ = self._call(
                    "capability_tool_call",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "capability_id": "mcp:test-server",
                        "tool_name": "resolve-library-id",
                        "arguments": {"libraryName": "onecolleague"},
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("real_tool_name") or ""), "resolve_library_id")
        finally:
            cleanup()

    def test_capability_enable_emits_action_id_and_audit_event(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "pack:space",
                    "scope": "session",
                    "enabled": True,
                    "reason": "need space tools for research",
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            action_id = str(result.get("action_id") or "")
            self.assertTrue(action_id.startswith("cact_"), msg=f"missing action_id: {action_id}")

            audit_path = ops._audit_path()
            self.assertTrue(audit_path.exists())
            lines = [line.strip() for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertTrue(lines)
            last = json.loads(lines[-1])
            self.assertEqual(str(last.get("action_id") or ""), action_id)
            self.assertEqual(str(last.get("op") or ""), "capability_enable")
            details = last.get("details") if isinstance(last.get("details"), dict) else {}
            self.assertIn("need space tools", str(details.get("reason") or ""))
        finally:
            cleanup()

    def test_capability_search_supports_source_trust_and_qualification_filters(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:alpha"] = {
                "capability_id": "mcp:alpha",
                "kind": "mcp_toolpack",
                "name": "alpha",
                "description_short": "alpha",
                "source_id": "mcp_registry_official",
                "source_tier": "official",
                "trust_tier": "official",
                "qualification_status": "qualified",
                "enable_supported": True,
                "sync_state": "fresh",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9900/mcp"},
            }
            catalog_doc["records"]["mcp:beta"] = {
                "capability_id": "mcp:beta",
                "kind": "mcp_toolpack",
                "name": "beta",
                "description_short": "beta",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "community",
                "qualification_status": "unavailable",
                "enable_supported": True,
                "sync_state": "fresh",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9901/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "include_external": True,
                    "source_id": "anthropic_skills",
                    "trust_tier": "community",
                    "qualification_status": "unavailable",
                    "limit": 20,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            ids = {str(item.get("capability_id") or "") for item in items if isinstance(item, dict)}
            self.assertIn("mcp:beta", ids)
            self.assertNotIn("mcp:alpha", ids)
        finally:
            cleanup()

    def test_search_skill_query_multiple_names_returns_union_matches(self) -> None:
        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(
                extra=(
                    "skills:\n"
                    "  source_overrides:\n"
                    "    - source_id: github_skills_curated\n"
                    "      level: mounted\n"
                    "  curated:\n"
                    "    - capability_id: skill:github:demo:quant-analyst\n"
                    "      level: mounted\n"
                    "      source_id: github_skills_curated\n"
                    "      source_uri: https://github.com/demo/quant-analyst\n"
                    "      qualification_status: qualified\n"
                    "      description_short: Quant analyst toolkit.\n"
                    "    - capability_id: skill:github:demo:search-specialist\n"
                    "      level: mounted\n"
                    "      source_id: github_skills_curated\n"
                    "      source_uri: https://github.com/demo/search-specialist\n"
                    "      qualification_status: qualified\n"
                    "      description_short: Search specialist toolkit.\n"
                )
            )
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            search_resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "quant-analyst search-specialist",
                    "kind": "skill",
                    "include_external": True,
                    "limit": 20,
                },
            )
            self.assertTrue(search_resp.ok, getattr(search_resp, "error", None))
            result = search_resp.result if isinstance(search_resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            ids = {str(item.get("capability_id") or "") for item in items if isinstance(item, dict)}
            self.assertIn("skill:github:demo:quant-analyst", ids)
            self.assertIn("skill:github:demo:search-specialist", ids)
        finally:
            cleanup()

    def test_search_surfaces_curated_third_party_skill_as_enable_now(self) -> None:
        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(
                extra=(
                    "skills:\n"
                    "  source_overrides:\n"
                    "    - source_id: github_skills_curated\n"
                    "      level: mounted\n"
                    "  curated:\n"
                    "    - capability_id: skill:github:blader:claudeception\n"
                    "      level: mounted\n"
                    "      source_id: github_skills_curated\n"
                    "      source_uri: https://github.com/blader/Claudeception\n"
                    "      qualification_status: qualified\n"
                    "      description_short: Captures reusable lessons.\n"
                )
            )
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            search_resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "claudeception",
                    "kind": "skill",
                    "include_external": True,
                    "limit": 20,
                },
            )
            self.assertTrue(search_resp.ok, getattr(search_resp, "error", None))
            result = search_resp.result if isinstance(search_resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            row = next(
                (x for x in items if isinstance(x, dict) and str(x.get("capability_id") or "") == "skill:github:blader:claudeception"),
                None,
            )
            self.assertIsNotNone(row)
            item = row if isinstance(row, dict) else {}
            self.assertEqual(str(item.get("source_id") or ""), "github_skills_curated")
            self.assertEqual(str(item.get("qualification_status") or ""), "qualified")
            self.assertEqual(str(item.get("enable_hint") or ""), "enable_now")
            readiness = item.get("readiness_preview") if isinstance(item.get("readiness_preview"), dict) else {}
            self.assertEqual(str(readiness.get("preview_status") or ""), "enableable")

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "scope": "session",
                    "capability_id": "skill:github:blader:claudeception",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            self.assertEqual(str(enable_result.get("state") or ""), "runnable")
        finally:
            cleanup()

    def test_default_policy_surfaces_risky_third_party_mcp_not_hidden(self) -> None:
        """Risky third-party MCPs (desktop-commander) are indexed by default policy,
        so they are hidden from normal search results (policy_hidden_count > 0)."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            search_resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "desktop-commander",
                    "kind": "mcp_toolpack",
                    "include_external": True,
                    "limit": 20,
                },
            )
            self.assertTrue(search_resp.ok, getattr(search_resp, "error", None))
            result = search_resp.result if isinstance(search_resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            row = next(
                (
                    x
                    for x in items
                    if isinstance(x, dict)
                    and str(x.get("capability_id") or "") == "mcp:io.github.wonderwhy-er/desktop-commander"
                ),
                None,
            )
            # desktop-commander is indexed in default allowlist → hidden from search
            self.assertIsNone(row)
            diag = result.get("search_diagnostics") if isinstance(result.get("search_diagnostics"), dict) else {}
            self.assertGreater(int(diag.get("policy_hidden_count") or 0), 0)
        finally:
            cleanup()

    def test_search_hides_indexed_capability_by_default_policy(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="indexed")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:indexed-default"] = {
                "capability_id": "mcp:indexed-default",
                "kind": "mcp_toolpack",
                "name": "indexed-default",
                "description_short": "indexed-default",
                "source_id": "mcp_registry_official",
                "source_tier": "official",
                "trust_tier": "official",
                "qualification_status": "qualified",
                "enable_supported": True,
                "sync_state": "fresh",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9910/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "indexed-default",
                    "include_external": True,
                    "limit": 20,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            ids = {str(item.get("capability_id") or "") for item in items if isinstance(item, dict)}
            self.assertNotIn("mcp:indexed-default", ids)
            diagnostics = result.get("search_diagnostics") if isinstance(result.get("search_diagnostics"), dict) else {}
            self.assertGreaterEqual(int(diagnostics.get("policy_hidden_count") or 0), 1)
        finally:
            cleanup()

    def test_enable_rejects_indexed_policy_level(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="indexed")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:indexed-default"] = {
                "capability_id": "mcp:indexed-default",
                "kind": "mcp_toolpack",
                "name": "indexed-default",
                "description_short": "indexed-default",
                "source_id": "mcp_registry_official",
                "source_tier": "official",
                "trust_tier": "official",
                "qualification_status": "qualified",
                "enable_supported": True,
                "sync_state": "fresh",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9911/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "user",
                    "scope": "session",
                    "capability_id": "mcp:indexed-default",
                    "enabled": True,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "blocked")
            self.assertEqual(str(result.get("reason") or ""), "policy_level_indexed")
            self.assertEqual(str(result.get("policy_level") or ""), "indexed")
        finally:
            cleanup()

    def test_ensure_curated_catalog_records_refreshes_existing_entry(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        catalog = ops._new_catalog_doc()
        policy_v1 = ops._compile_allowlist_policy(
            {
                "skills": {
                    "curated": [
                        {
                            "capability_id": "skill:github:demo:s1",
                            "level": "mounted",
                            "source_id": "github_skills_curated",
                            "description_short": "v1",
                        }
                    ]
                }
            }
        )
        changed_v1 = ops._ensure_curated_catalog_records(catalog, policy=policy_v1)
        self.assertTrue(changed_v1)

        rec_v1 = catalog.get("records", {}).get("skill:github:demo:s1")
        self.assertIsInstance(rec_v1, dict)
        self.assertEqual(str((rec_v1 or {}).get("description_short") or ""), "v1")

        policy_v2 = ops._compile_allowlist_policy(
            {
                "skills": {
                    "curated": [
                        {
                            "capability_id": "skill:github:demo:s1",
                            "level": "enabled",
                            "source_id": "github_skills_curated",
                            "description_short": "v2",
                        }
                    ]
                }
            }
        )
        changed_v2 = ops._ensure_curated_catalog_records(catalog, policy=policy_v2)
        self.assertTrue(changed_v2)

        rec_v2 = catalog.get("records", {}).get("skill:github:demo:s1")
        self.assertIsInstance(rec_v2, dict)
        self.assertEqual(str((rec_v2 or {}).get("description_short") or ""), "v2")

    def test_ensure_curated_catalog_records_includes_builtin_runtime_bootstrap_skill(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        catalog = ops._new_catalog_doc()
        policy = ops._compile_allowlist_policy({})
        changed = ops._ensure_curated_catalog_records(catalog, policy=policy)
        self.assertTrue(changed)

        rec = catalog.get("records", {}).get("skill:onecolleague:runtime-bootstrap")
        self.assertIsInstance(rec, dict)
        self.assertEqual(str((rec or {}).get("source_id") or ""), "onecolleague_builtin")
        self.assertEqual(str((rec or {}).get("kind") or ""), "skill")
        requires = (rec or {}).get("requires_capabilities") if isinstance(rec, dict) else []
        self.assertEqual(requires, ["pack:diagnostics", "pack:group-runtime"])

    def test_catalog_normalization_accepts_legacy_builtin_source_id(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        raw = ops._new_catalog_doc()
        raw["sources"] = {
            "cccc_builtin": {"sync_state": "legacy", "last_synced_at": "2026-01-01T00:00:00Z", "record_count": 1}
        }
        raw["records"] = {
            "skill:legacy:demo": {
                "capability_id": "skill:legacy:demo",
                "kind": "skill",
                "source_id": "cccc_builtin",
                "qualification_status": "qualified",
                "enable_supported": True,
            }
        }

        normalized = ops._normalize_catalog_doc(raw)
        sources = normalized.get("sources") if isinstance(normalized.get("sources"), dict) else {}
        self.assertIn("onecolleague_builtin", sources)
        self.assertNotIn("cccc_builtin", sources)
        rec = normalized.get("records", {}).get("skill:legacy:demo")
        self.assertIsInstance(rec, dict)
        self.assertEqual(str((rec or {}).get("source_id") or ""), "onecolleague_builtin")

    def test_catalog_normalization_canonicalizes_legacy_builtin_skill_id(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        raw = ops._new_catalog_doc()
        raw["records"] = {
            "skill:cccc:runtime-bootstrap": {
                "capability_id": "skill:cccc:runtime-bootstrap",
                "kind": "skill",
                "source_id": "cccc_builtin",
                "qualification_status": "qualified",
                "enable_supported": True,
            }
        }

        normalized = ops._normalize_catalog_doc(raw)
        records = normalized.get("records") if isinstance(normalized.get("records"), dict) else {}
        self.assertIn("skill:onecolleague:runtime-bootstrap", records)
        self.assertNotIn("skill:cccc:runtime-bootstrap", records)
        rec = records.get("skill:onecolleague:runtime-bootstrap")
        self.assertEqual(str((rec or {}).get("capability_id") or ""), "skill:onecolleague:runtime-bootstrap")
        self.assertEqual(str((rec or {}).get("source_id") or ""), "onecolleague_builtin")

    def test_allowlist_policy_accepts_legacy_builtin_source_id(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        policy = ops._compile_allowlist_policy(
            {
                "defaults": {
                    "source_level": {
                        "cccc_builtin": "indexed",
                    }
                }
            }
        )

        source_levels = policy.get("source_levels") if isinstance(policy.get("source_levels"), dict) else {}
        self.assertEqual(str(source_levels.get("onecolleague_builtin") or ""), "indexed")
        self.assertNotIn("cccc_builtin", source_levels)

    def test_capability_state_reports_scope_mismatch_and_unavailable_hidden_reasons(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="mounted")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            self._add_actor(gid, "peer-2", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:shared"] = {
                "capability_id": "mcp:shared",
                "kind": "mcp_toolpack",
                "name": "shared",
                "description_short": "shared",
                "source_id": "mcp_registry_official",
                "source_tier": "official",
                "trust_tier": "official",
                "qualification_status": "qualified",
                "enable_supported": True,
                "sync_state": "fresh",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9902/mcp"},
            }
            catalog_doc["records"]["mcp:manual"] = {
                "capability_id": "mcp:manual",
                "kind": "mcp_toolpack",
                "name": "manual",
                "description_short": "manual",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "community",
                "qualification_status": "unavailable",
                "enable_supported": False,
                "sync_state": "fresh",
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9903/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-2",
                scope="actor",
                capability_id="mcp:shared",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            hidden = result.get("hidden_capabilities") if isinstance(result.get("hidden_capabilities"), list) else []
            by_id = {
                str(item.get("capability_id") or ""): str(item.get("reason") or "")
                for item in hidden
                if isinstance(item, dict)
            }
            self.assertEqual(by_id.get("mcp:manual"), "unavailable")
            self.assertEqual(by_id.get("mcp:shared"), "scope_mismatch")
            rows_by_id = {
                str(item.get("capability_id") or ""): item
                for item in hidden
                if isinstance(item, dict)
            }
            manual_row = rows_by_id.get("mcp:manual") if isinstance(rows_by_id.get("mcp:manual"), dict) else {}
            self.assertEqual(str(manual_row.get("name") or ""), "manual")
            self.assertEqual(str(manual_row.get("description_short") or ""), "manual")
            self.assertEqual(str(manual_row.get("kind") or ""), "mcp_toolpack")
            self.assertEqual(str(manual_row.get("source_id") or ""), "anthropic_skills")
            shared_row = rows_by_id.get("mcp:shared") if isinstance(rows_by_id.get("mcp:shared"), dict) else {}
            self.assertEqual(str(shared_row.get("name") or ""), "shared")
            self.assertEqual(str(shared_row.get("description_short") or ""), "shared")
        finally:
            cleanup()

    def test_capability_enable_respects_actor_quota(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            with patch.dict(os.environ, {"CCCC_CAPABILITY_MAX_ENABLED_PER_ACTOR": "1"}, clear=False):
                first, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": "pack:space",
                        "scope": "session",
                        "enabled": True,
                    },
                )
                self.assertTrue(first.ok, getattr(first, "error", None))
                first_result = first.result if isinstance(first.result, dict) else {}
                self.assertEqual(str(first_result.get("state") or ""), "activation_pending")

                second, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": "pack:groups",
                        "scope": "session",
                        "enabled": True,
                    },
                )
            self.assertTrue(second.ok, getattr(second, "error", None))
            second_result = second.result if isinstance(second.result, dict) else {}
            self.assertEqual(str(second_result.get("state") or ""), "blocked")
            self.assertIn("quota_enabled_actor_exceeded", str(second_result.get("reason") or ""))
        finally:
            cleanup()

    def test_capability_uninstall_revokes_binding_and_removes_installation(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "foreman-1", by="user")
            self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "foreman-1",
                    "by": "user",
                    "patch": {"role": "foreman"},
                },
            )

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="foreman-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(ops, runtime_doc, tools=[])
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            uninstall_resp, _ = self._call(
                "capability_uninstall",
                {
                    "group_id": gid,
                    "by": "foreman-1",
                    "actor_id": "foreman-1",
                    "capability_id": "mcp:test-server",
                    "reason": "cleanup",
                },
            )
            self.assertTrue(uninstall_resp.ok, getattr(uninstall_resp, "error", None))
            uninstall_result = uninstall_resp.result if isinstance(uninstall_resp.result, dict) else {}
            self.assertTrue(bool(uninstall_result.get("removed_installation")))
            self.assertGreaterEqual(int(uninstall_result.get("removed_bindings") or 0), 1)

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "foreman-1", "by": "foreman-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state_result = state_resp.result if isinstance(state_resp.result, dict) else {}
            self.assertNotIn("mcp:test-server", state_result.get("enabled_capabilities") or [])

            _, runtime_doc_after = ops._load_runtime_doc()
            _, install_after = ops._runtime_install_for_capability(runtime_doc_after, capability_id="mcp:test-server")
            self.assertIsNone(install_after)
        finally:
            cleanup()

    def test_capability_uninstall_isolated_to_target_group(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid_a = self._create_group(title="cap-uninstall-a")
            gid_b = self._create_group(title="cap-uninstall-b")
            self._add_actor(gid_a, "peer-1", by="user")
            self._add_actor(gid_b, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid_a,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._set_enabled_capability(
                state_doc,
                group_id=gid_b,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(ops, runtime_doc, capability_id="mcp:test-server")
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid_a,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid_b,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            uninstall_resp, _ = self._call(
                "capability_uninstall",
                {
                    "group_id": gid_a,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": "mcp:test-server",
                    "reason": "group-a cleanup",
                },
            )
            self.assertTrue(uninstall_resp.ok, getattr(uninstall_resp, "error", None))
            uninstall_result = uninstall_resp.result if isinstance(uninstall_resp.result, dict) else {}
            self.assertFalse(bool(uninstall_result.get("removed_installation")))
            self.assertEqual(
                str(uninstall_result.get("cleanup_skipped_reason") or ""),
                "cleanup_skipped_capability_still_bound",
            )

            _, state_doc_after = ops._load_state_doc()
            enabled_a, _ = ops._collect_enabled_capabilities(state_doc_after, group_id=gid_a, actor_id="peer-1")
            enabled_b, _ = ops._collect_enabled_capabilities(state_doc_after, group_id=gid_b, actor_id="peer-1")
            self.assertNotIn("mcp:test-server", set(enabled_a))
            self.assertIn("mcp:test-server", set(enabled_b))

            state_b_resp, _ = self._call(
                "capability_state",
                {"group_id": gid_b, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_b_resp.ok, getattr(state_b_resp, "error", None))
            state_b = state_b_resp.result if isinstance(state_b_resp.result, dict) else {}
            visible_b = state_b.get("visible_tools") if isinstance(state_b.get("visible_tools"), list) else []
            self.assertIn("onecolleague_ext_deadbeef_echo", visible_b)
        finally:
            cleanup()

    def test_capability_state_dynamic_tools_respects_visibility_limit(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(
                ops,
                runtime_doc,
                tools=[
                    {
                        "name": "onecolleague_ext_deadbeef_a",
                        "real_tool_name": "a",
                        "description": "a",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    },
                    {
                        "name": "onecolleague_ext_deadbeef_b",
                        "real_tool_name": "b",
                        "description": "b",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    },
                    {
                        "name": "onecolleague_ext_deadbeef_c",
                        "real_tool_name": "c",
                        "description": "c",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    },
                ],
            )
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            with patch.dict(os.environ, {"CCCC_CAPABILITY_MAX_DYNAMIC_TOOLS_VISIBLE": "2"}, clear=False):
                resp, _ = self._call(
                    "capability_state",
                    {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            dynamic = result.get("dynamic_tools") if isinstance(result.get("dynamic_tools"), list) else []
            self.assertEqual(len(dynamic), 2)
            self.assertEqual(int(result.get("dynamic_tool_limit") or 0), 2)
            self.assertEqual(int(result.get("dynamic_tool_dropped") or 0), 1)
        finally:
            cleanup()

    def test_group_block_by_foreman_revokes_binding_and_hides_tools(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "foreman-1", by="user")
            self._add_actor(gid, "peer-1", by="user")
            self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "foreman-1",
                    "by": "user",
                    "patch": {"role": "foreman"},
                },
            )

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(ops, runtime_doc, tools=[])
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id="mcp:test-server",
                artifact_id=artifact_id,
                state="activation_pending",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            block_resp, _ = self._call(
                "capability_block",
                {
                    "group_id": gid,
                    "by": "foreman-1",
                    "actor_id": "foreman-1",
                    "scope": "group",
                    "capability_id": "mcp:test-server",
                    "blocked": True,
                    "reason": "side_effect_detected",
                },
            )
            self.assertTrue(block_resp.ok, getattr(block_resp, "error", None))
            block_result = block_resp.result if isinstance(block_resp.result, dict) else {}
            self.assertTrue(bool(block_result.get("refresh_required")))
            self.assertGreaterEqual(int(block_result.get("removed_bindings") or 0), 1)

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state_result = state_resp.result if isinstance(state_resp.result, dict) else {}
            self.assertNotIn("mcp:test-server", state_result.get("enabled_capabilities") or [])
            blocked_rows = state_result.get("blocked_capabilities") if isinstance(state_result.get("blocked_capabilities"), list) else []
            blocked_ids = {str(item.get("capability_id") or "") for item in blocked_rows if isinstance(item, dict)}
            self.assertIn("mcp:test-server", blocked_ids)

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "mcp:test-server",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            self.assertEqual(str(enable_result.get("reason") or ""), "blocked_by_group_policy")
        finally:
            cleanup()

    def test_group_block_requires_foreman(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "foreman-1", by="user")
            self._add_actor(gid, "peer-1", by="user")
            self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "foreman-1",
                    "by": "user",
                    "patch": {"role": "foreman"},
                },
            )
            self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "user",
                    "patch": {"role": "peer"},
                },
            )
            resp, _ = self._call(
                "capability_block",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "scope": "group",
                    "capability_id": "mcp:test-server",
                    "blocked": True,
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual(str(getattr(resp.error, "code", "")), "permission_denied")
        finally:
            cleanup()

    def test_global_block_requires_user(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "foreman-1", by="user")
            self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "foreman-1",
                    "by": "user",
                    "patch": {"role": "foreman"},
                },
            )
            resp, _ = self._call(
                "capability_block",
                {
                    "group_id": gid,
                    "by": "foreman-1",
                    "actor_id": "foreman-1",
                    "scope": "global",
                    "capability_id": "mcp:test-server",
                    "blocked": True,
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual(str(getattr(resp.error, "code", "")), "permission_denied")
        finally:
            cleanup()

    def test_capability_overview_returns_builtin_items_and_sources(self) -> None:
        _, cleanup = self._with_home()
        try:
            resp, _ = self._call(
                "capability_overview",
                {
                    "query": "",
                    "limit": 200,
                    "include_indexed": True,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            self.assertTrue(items)
            ids = {str(item.get("capability_id") or "") for item in items if isinstance(item, dict)}
            self.assertIn("pack:group-runtime", ids)
            sources = result.get("sources") if isinstance(result.get("sources"), dict) else {}
            self.assertIn("onecolleague_builtin", sources)
            self.assertIn("onecolleague_skill_library", sources)
            self.assertNotIn("cccc_builtin", sources)
        finally:
            cleanup()

    def test_capability_state_slash_view_omits_capsule_text(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "scope": "session",
                    "capability_id": "skill:onecolleague:install",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))

            with patch(
                "no1.daemon.ops.capability_ops._search._render_source_states",
                side_effect=AssertionError("slash command state must not render full source states"),
            ):
                state_resp, _ = self._call(
                    "capability_state",
                    {"group_id": gid, "actor_id": "peer-1", "by": "peer-1", "view": "slash_commands"},
                )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            self.assertEqual(str(state.get("view") or ""), "slash_commands")
            active_capsule_skills = (
                state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            )
            install_row = next(
                (
                    item
                    for item in active_capsule_skills
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "skill:onecolleague:install"
                ),
                {},
            )
            self.assertEqual(str(install_row.get("name") or ""), "install")
            self.assertIn("capsule_preview", install_row)
            self.assertNotIn("capsule_text", install_row)
            self.assertNotIn("visible_tools", state)
            self.assertNotIn("external_binding_states", state)
        finally:
            cleanup()

    def test_capability_overview_includes_recent_success_entry(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(
                extra=(
                    "mcp_overrides:\n"
                    "  - capability_id: mcp:io.github.upstash/context7\n"
                    "    level: indexed"
                )
            )
            runtime_path, runtime_doc = ops._load_runtime_doc()
            ops._record_runtime_recent_success(
                runtime_doc,
                capability_id="mcp:io.github.upstash/context7",
                group_id="g_recent",
                actor_id="peer-1",
                action="enable",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            resp, _ = self._call(
                "capability_overview",
                {
                    "query": "context7",
                    "limit": 50,
                    "include_indexed": True,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            target = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and str(item.get("capability_id") or "") == "mcp:io.github.upstash/context7"
                ),
                {},
            )
            self.assertTrue(target)
            recent = target.get("recent_success") if isinstance(target.get("recent_success"), dict) else {}
            self.assertEqual(int(recent.get("success_count") or 0), 1)
            self.assertEqual(str(recent.get("last_action") or ""), "enable")
            self.assertFalse(bool(target.get("autoload_candidate")))
            self.assertEqual(str(target.get("current_admission_reason") or ""), "policy_level_indexed")
        finally:
            cleanup()

    def test_capability_overview_marks_global_blocked_entries(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            block_resp, _ = self._call(
                "capability_block",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "user",
                    "scope": "global",
                    "capability_id": "pack:space",
                    "blocked": True,
                    "reason": "manual_block_test",
                },
            )
            self.assertTrue(block_resp.ok, getattr(block_resp, "error", None))

            overview_resp, _ = self._call(
                "capability_overview",
                {
                    "query": "pack:space",
                    "limit": 50,
                    "include_indexed": True,
                },
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            result = overview_resp.result if isinstance(overview_resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            target = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "pack:space"
                ),
                {},
            )
            self.assertTrue(target)
            self.assertTrue(bool(target.get("blocked_global")))
            blocked = result.get("blocked_capabilities") if isinstance(result.get("blocked_capabilities"), list) else []
            blocked_ids = {str(item.get("capability_id") or "") for item in blocked if isinstance(item, dict)}
            self.assertIn("pack:space", blocked_ids)
        finally:
            cleanup()

    def test_skill_enable_session_reports_active_skill_and_applies_dependencies(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["skill:anthropic:write-pr"] = {
                "capability_id": "skill:anthropic:write-pr",
                "kind": "skill",
                "name": "write-pr",
                "description_short": "Write concise PR summaries",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "source_uri": "https://example.invalid/skills/write-pr",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
                "capsule_text": "Use structured PR summary format.",
                "requires_capabilities": ["pack:space"],
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "skill:anthropic:write-pr",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            skill_payload = enable_result.get("skill") if isinstance(enable_result.get("skill"), dict) else {}
            self.assertEqual(str(skill_payload.get("capability_id") or ""), "skill:anthropic:write-pr")
            applied = (
                skill_payload.get("applied_dependencies")
                if isinstance(skill_payload.get("applied_dependencies"), list)
                else []
            )
            self.assertIn("pack:space", applied)

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            enabled = state.get("enabled_capabilities") if isinstance(state.get("enabled_capabilities"), list) else []
            self.assertIn("skill:anthropic:write-pr", enabled)
            self.assertIn("pack:space", enabled)
            active_capsule_skills = (
                state.get("active_capsule_skills")
                if isinstance(state.get("active_capsule_skills"), list)
                else []
            )
            active_ids = {
                str(item.get("capability_id") or "")
                for item in active_capsule_skills
                if isinstance(item, dict)
            }
            self.assertIn("skill:anthropic:write-pr", active_ids)
            autoload_skills = state.get("autoload_skills") if isinstance(state.get("autoload_skills"), list) else []
            autoload_ids = {str(item.get("capability_id") or "") for item in autoload_skills if isinstance(item, dict)}
            self.assertNotIn("skill:anthropic:write-pr", autoload_ids)
            binding_states = (
                state.get("external_binding_states") if isinstance(state.get("external_binding_states"), dict) else {}
            )
            skill_binding = binding_states.get("skill:anthropic:write-pr") if isinstance(binding_states, dict) else {}
            self.assertEqual(str((skill_binding or {}).get("mode") or ""), "skill")
        finally:
            cleanup()

    def test_skill_enable_skips_group_removed_dependency(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "foreman-1", by="user")
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["skill:anthropic:write-pr"] = {
                "capability_id": "skill:anthropic:write-pr",
                "kind": "skill",
                "name": "write-pr",
                "description_short": "Write concise PR summaries",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
                "capsule_text": "Use structured PR summary format.",
                "requires_capabilities": ["pack:space"],
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            uninstall_resp, _ = self._call(
                "capability_uninstall",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": "pack:space",
                },
            )
            self.assertTrue(uninstall_resp.ok, getattr(uninstall_resp, "error", None))
            uninstall_result = uninstall_resp.result if isinstance(uninstall_resp.result, dict) else {}
            self.assertTrue(bool(uninstall_result.get("removed_group_marker")))

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": "skill:anthropic:write-pr",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            skill_payload = enable_result.get("skill") if isinstance(enable_result.get("skill"), dict) else {}
            self.assertNotIn("pack:space", skill_payload.get("applied_dependencies") or [])
            self.assertIn(
                {"capability_id": "pack:space", "reason": "removed_by_group_policy"},
                skill_payload.get("skipped_dependencies") or [],
            )

            _, state_doc = ops._load_state_doc()
            self.assertIn("pack:space", set(ops._collect_removed_capabilities(state_doc, group_id=gid)))
            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            self.assertIn("skill:anthropic:write-pr", state.get("enabled_capabilities") or [])
            self.assertNotIn("pack:space", state.get("enabled_capabilities") or [])
            self.assertNotIn("onecolleague_space", state.get("visible_tools") or [])

            restore_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "foreman-1",
                    "actor_id": "foreman-1",
                    "capability_id": "pack:space",
                    "scope": "group",
                    "enabled": True,
                },
            )
            self.assertTrue(restore_resp.ok, getattr(restore_resp, "error", None))
            _, restored_state = ops._load_state_doc()
            self.assertNotIn(
                "pack:space",
                set(ops._collect_removed_capabilities(restored_state, group_id=gid)),
            )

            block_resp, _ = self._call(
                "capability_block",
                {
                    "group_id": gid,
                    "by": "foreman-1",
                    "actor_id": "foreman-1",
                    "scope": "group",
                    "capability_id": "pack:space",
                    "blocked": True,
                },
            )
            self.assertTrue(block_resp.ok, getattr(block_resp, "error", None))
            blocked_enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": "skill:anthropic:write-pr",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(blocked_enable_resp.ok, getattr(blocked_enable_resp, "error", None))
            blocked_enable_result = (
                blocked_enable_resp.result if isinstance(blocked_enable_resp.result, dict) else {}
            )
            blocked_skill = (
                blocked_enable_result.get("skill")
                if isinstance(blocked_enable_result.get("skill"), dict)
                else {}
            )
            self.assertIn(
                {"capability_id": "pack:space", "reason": "blocked_by_group_policy"},
                blocked_skill.get("skipped_dependencies") or [],
            )
            blocked_state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(blocked_state_resp.ok, getattr(blocked_state_resp, "error", None))
            blocked_state = blocked_state_resp.result if isinstance(blocked_state_resp.result, dict) else {}
            self.assertNotIn("pack:space", blocked_state.get("enabled_capabilities") or [])
            self.assertNotIn("onecolleague_space", blocked_state.get("visible_tools") or [])

            unblock_resp, _ = self._call(
                "capability_block",
                {
                    "group_id": gid,
                    "by": "foreman-1",
                    "actor_id": "foreman-1",
                    "scope": "group",
                    "capability_id": "pack:space",
                    "blocked": False,
                },
            )
            self.assertTrue(unblock_resp.ok, getattr(unblock_resp, "error", None))
            self._write_allowlist_override(
                extra=(
                    "mcp_overrides:\n"
                    "  - capability_id: pack:space\n"
                    "    level: indexed"
                )
            )
            indexed_enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": "skill:anthropic:write-pr",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(indexed_enable_resp.ok, getattr(indexed_enable_resp, "error", None))
            indexed_enable_result = (
                indexed_enable_resp.result if isinstance(indexed_enable_resp.result, dict) else {}
            )
            indexed_skill = (
                indexed_enable_result.get("skill")
                if isinstance(indexed_enable_result.get("skill"), dict)
                else {}
            )
            self.assertIn(
                {
                    "capability_id": "pack:space",
                    "reason": "policy_level_indexed",
                    "policy_level": "indexed",
                },
                indexed_skill.get("skipped_dependencies") or [],
            )
        finally:
            cleanup()

    def test_skill_group_dependency_quota_uses_transaction_state(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="group",
                capability_id="pack:diagnostics",
                enabled=True,
                ttl_seconds=3600,
            )
            ops._save_state_doc(state_path, state_doc)

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["skill:anthropic:write-pr"] = {
                "capability_id": "skill:anthropic:write-pr",
                "kind": "skill",
                "name": "write-pr",
                "description_short": "Write concise PR summaries",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
                "capsule_text": "Use structured PR summary format.",
                "requires_capabilities": ["pack:space"],
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            with patch.dict(
                os.environ,
                {"CCCC_CAPABILITY_MAX_ENABLED_PER_GROUP": "2"},
                clear=False,
            ):
                enable_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": "skill:anthropic:write-pr",
                        "scope": "group",
                        "enabled": True,
                    },
                )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            skill_payload = enable_result.get("skill") if isinstance(enable_result.get("skill"), dict) else {}
            self.assertNotIn("pack:space", skill_payload.get("applied_dependencies") or [])
            self.assertIn(
                {"capability_id": "pack:space", "reason": "quota_enabled_group_exceeded:2"},
                skill_payload.get("skipped_dependencies") or [],
            )

            _, final_state_doc = ops._load_state_doc()
            group_enabled = final_state_doc.get("group_enabled")
            group_items = set(group_enabled.get(gid) or []) if isinstance(group_enabled, dict) else set()
            self.assertEqual(
                group_items,
                {"pack:diagnostics", "skill:anthropic:write-pr"},
            )
            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            self.assertNotIn("pack:space", state.get("enabled_capabilities") or [])
            self.assertNotIn("onecolleague_space", state.get("visible_tools") or [])
        finally:
            cleanup()

    def test_concurrent_group_enable_quota_is_linearized_at_commit(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="group",
                capability_id="pack:diagnostics",
                enabled=True,
                ttl_seconds=3600,
            )
            ops._save_state_doc(state_path, state_doc)

            capability_ids = ("skill:anthropic:race-a", "skill:anthropic:race-b")
            catalog_path, catalog_doc = ops._load_catalog_doc()
            for capability_id in capability_ids:
                catalog_doc["records"][capability_id] = {
                    "capability_id": capability_id,
                    "kind": "skill",
                    "name": capability_id.rsplit(":", 1)[-1],
                    "description_short": "Concurrent quota test skill",
                    "source_id": "anthropic_skills",
                    "source_tier": "tier1",
                    "trust_tier": "tier1",
                    "qualification_status": "qualified",
                    "enable_supported": True,
                    "capsule_text": "Use the concurrent quota test procedure.",
                    "requires_capabilities": [],
                }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            barrier = threading.Barrier(2)
            barrier_lock = threading.Lock()
            aligned: set[str] = set()
            original_supported = ops._record_enable_supported

            def align_after_precheck(rec, *, capability_id=""):  # type: ignore[no-untyped-def]
                supported = original_supported(rec, capability_id=capability_id)
                should_wait = False
                with barrier_lock:
                    if capability_id in capability_ids and capability_id not in aligned:
                        aligned.add(capability_id)
                        should_wait = True
                if should_wait:
                    barrier.wait(timeout=5.0)
                return supported

            responses: dict[str, Any] = {}

            def enable(capability_id: str) -> None:
                response, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": capability_id,
                        "scope": "group",
                        "enabled": True,
                    },
                )
                responses[capability_id] = response

            with patch.dict(
                os.environ,
                {"CCCC_CAPABILITY_MAX_ENABLED_PER_GROUP": "2"},
                clear=False,
            ), patch(
                "no1.daemon.ops.capability_ops._record_enable_supported",
                side_effect=align_after_precheck,
            ):
                threads = [threading.Thread(target=enable, args=(capability_id,)) for capability_id in capability_ids]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10.0)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(set(responses), set(capability_ids))
            results = []
            for response in responses.values():
                self.assertTrue(response.ok, getattr(response, "error", None))
                results.append(response.result if isinstance(response.result, dict) else {})
            self.assertEqual(sum(str(result.get("state") or "") == "runnable" for result in results), 1)
            self.assertEqual(
                sum(str(result.get("reason") or "") == "quota_enabled_group_exceeded:2" for result in results),
                1,
            )

            _, final_state_doc = ops._load_state_doc()
            group_enabled = final_state_doc.get("group_enabled")
            group_items = set(group_enabled.get(gid) or []) if isinstance(group_enabled, dict) else set()
            self.assertEqual(len(group_items), 2)
            self.assertIn("pack:diagnostics", group_items)
            self.assertEqual(len(group_items.intersection(capability_ids)), 1)
        finally:
            cleanup()

    def test_external_group_quota_loser_preserves_existing_runtime_binding(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "mcp:test-server"

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="actor",
                capability_id=capability_id,
                enabled=True,
                ttl_seconds=3600,
            )
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="group",
                capability_id="pack:diagnostics",
                enabled=True,
                ttl_seconds=3600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            artifact_id = self._seed_runtime_external_install(
                ops,
                runtime_doc,
                capability_id=capability_id,
                tools=[],
                state="installed",
            )
            ops._set_runtime_actor_binding(
                runtime_doc,
                group_id=gid,
                actor_id="peer-1",
                capability_id=capability_id,
                artifact_id=artifact_id,
                state="runnable",
                last_error="",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)
            prior_binding = dict(runtime_doc["actor_instances"][gid]["peer-1"][capability_id])

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"][capability_id] = {
                "capability_id": capability_id,
                "kind": "mcp_toolpack",
                "name": "test-server",
                "description_short": "External quota ownership test",
                "source_id": "manual_import",
                "source_tier": "tier2",
                "trust_tier": "tier2",
                "qualification_status": "qualified",
                "enable_supported": True,
                "install_mode": "remote_only",
                "install_spec": {"transport": "http", "url": "http://127.0.0.1:9900/mcp"},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            original_supported = ops._record_enable_supported
            injected = False

            def fill_group_quota(rec, *, capability_id=""):  # type: ignore[no-untyped-def]
                nonlocal injected
                supported = original_supported(rec, capability_id=capability_id)
                if capability_id == "mcp:test-server" and not injected:
                    injected = True
                    current_path, current_doc = ops._load_state_doc()
                    ops._set_enabled_capability(
                        current_doc,
                        group_id=gid,
                        actor_id="peer-1",
                        scope="group",
                        capability_id="pack:space",
                        enabled=True,
                        ttl_seconds=3600,
                    )
                    ops._save_state_doc(current_path, current_doc)
                return supported

            with patch.dict(
                os.environ,
                {"CCCC_CAPABILITY_MAX_ENABLED_PER_GROUP": "2"},
                clear=False,
            ), patch(
                "no1.daemon.ops.capability_ops._record_enable_supported",
                side_effect=fill_group_quota,
            ):
                enable_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": capability_id,
                        "scope": "group",
                        "enabled": True,
                    },
                )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            self.assertEqual(str(enable_result.get("reason") or ""), "quota_enabled_group_exceeded:2")

            _, final_state_doc = ops._load_state_doc()
            actor_enabled = final_state_doc.get("actor_enabled")
            actor_group = actor_enabled.get(gid) if isinstance(actor_enabled, dict) else {}
            self.assertIn(capability_id, actor_group.get("peer-1") or [])
            group_enabled = final_state_doc.get("group_enabled")
            self.assertNotIn(capability_id, group_enabled.get(gid) or [])

            _, final_runtime_doc = ops._load_runtime_doc()
            final_binding = final_runtime_doc["actor_instances"][gid]["peer-1"][capability_id]
            self.assertEqual(final_binding, prior_binding)

            def reset_loser_state() -> None:
                current_path, current_doc = ops._load_state_doc()
                ops._set_enabled_capability(
                    current_doc,
                    group_id=gid,
                    actor_id="peer-1",
                    scope="actor",
                    capability_id=capability_id,
                    enabled=False,
                    ttl_seconds=3600,
                )
                ops._set_enabled_capability(
                    current_doc,
                    group_id=gid,
                    actor_id="peer-1",
                    scope="group",
                    capability_id="pack:space",
                    enabled=False,
                    ttl_seconds=3600,
                )
                ops._save_state_doc(current_path, current_doc)
                current_runtime_path, current_runtime_doc = ops._load_runtime_doc()
                ops._remove_runtime_actor_binding(
                    current_runtime_doc,
                    group_id=gid,
                    actor_id="peer-1",
                    capability_id=capability_id,
                )
                ops._save_runtime_doc(current_runtime_path, current_runtime_doc)

            reset_loser_state()
            injected = False
            with patch.dict(
                os.environ,
                {"CCCC_CAPABILITY_MAX_ENABLED_PER_GROUP": "2"},
                clear=False,
            ), patch(
                "no1.daemon.ops.capability_ops._record_enable_supported",
                side_effect=fill_group_quota,
            ):
                no_prior_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": capability_id,
                        "scope": "group",
                        "enabled": True,
                    },
                )
            self.assertTrue(no_prior_resp.ok, getattr(no_prior_resp, "error", None))
            _, no_prior_runtime = ops._load_runtime_doc()
            actor_instances = no_prior_runtime.get("actor_instances")
            per_group = actor_instances.get(gid) if isinstance(actor_instances, dict) else {}
            per_actor = per_group.get("peer-1") if isinstance(per_group, dict) else {}
            self.assertNotIn(capability_id, per_actor)

            reset_loser_state()
            injected = False
            original_save_runtime = ops._save_runtime_doc
            competing_binding: dict[str, Any] = {}
            replaced_reservation = False

            def replace_owned_reservation(path, doc):  # type: ignore[no-untyped-def]
                nonlocal replaced_reservation, competing_binding
                original_save_runtime(path, doc)
                actor_instances = doc.get("actor_instances")
                per_group = actor_instances.get(gid) if isinstance(actor_instances, dict) else None
                per_actor = per_group.get("peer-1") if isinstance(per_group, dict) else None
                binding = per_actor.get(capability_id) if isinstance(per_actor, dict) else None
                if replaced_reservation or not isinstance(binding, dict) or not binding.get("mutation_id"):
                    return
                replaced_reservation = True
                competing_path, competing_doc = ops._load_runtime_doc()
                ops._set_runtime_actor_binding(
                    competing_doc,
                    group_id=gid,
                    actor_id="peer-1",
                    capability_id=capability_id,
                    artifact_id=artifact_id,
                    state="runnable",
                    last_error="concurrent-writer",
                )
                original_save_runtime(competing_path, competing_doc)
                competing_binding = dict(
                    competing_doc["actor_instances"][gid]["peer-1"][capability_id]
                )

            with patch.dict(
                os.environ,
                {"CCCC_CAPABILITY_MAX_ENABLED_PER_GROUP": "2"},
                clear=False,
            ), patch(
                "no1.daemon.ops.capability_ops._record_enable_supported",
                side_effect=fill_group_quota,
            ), patch(
                "no1.daemon.ops.capability_ops._state._save_runtime_doc",
                side_effect=replace_owned_reservation,
            ):
                stale_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": capability_id,
                        "scope": "group",
                        "enabled": True,
                    },
                )
            self.assertTrue(stale_resp.ok, getattr(stale_resp, "error", None))
            self.assertTrue(replaced_reservation)
            _, stale_runtime = ops._load_runtime_doc()
            self.assertEqual(
                stale_runtime["actor_instances"][gid]["peer-1"][capability_id],
                competing_binding,
            )

            reset_loser_state()
            original_save_state = ops._save_state_doc

            def fail_external_state_commit(path, doc):  # type: ignore[no-untyped-def]
                group_enabled = doc.get("group_enabled")
                enabled_ids = group_enabled.get(gid) if isinstance(group_enabled, dict) else []
                if capability_id in set(enabled_ids or []):
                    raise OSError("state commit failed")
                original_save_state(path, doc)

            with patch(
                "no1.daemon.ops.capability_ops._state._save_state_doc",
                side_effect=fail_external_state_commit,
            ):
                failed_commit_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": capability_id,
                        "scope": "group",
                        "enabled": True,
                    },
                )
            self.assertFalse(failed_commit_resp.ok)
            _, failed_state = ops._load_state_doc()
            group_enabled = failed_state.get("group_enabled")
            self.assertNotIn(capability_id, group_enabled.get(gid) or [])
            _, failed_runtime = ops._load_runtime_doc()
            actor_instances = failed_runtime.get("actor_instances")
            per_group = actor_instances.get(gid) if isinstance(actor_instances, dict) else {}
            per_actor = per_group.get("peer-1") if isinstance(per_group, dict) else {}
            self.assertNotIn(capability_id, per_actor)
            recent_success = failed_runtime.get("recent_success")
            self.assertNotIn(capability_id, recent_success if isinstance(recent_success, dict) else {})
            successful_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": capability_id,
                    "scope": "group",
                    "enabled": True,
                },
            )
            self.assertTrue(successful_resp.ok, getattr(successful_resp, "error", None))

            disabled_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": capability_id,
                    "scope": "group",
                    "enabled": False,
                },
            )
            self.assertTrue(disabled_resp.ok, getattr(disabled_resp, "error", None))
        finally:
            cleanup()

    def test_builtin_runtime_bootstrap_enable_applies_builtin_dependencies(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "skill:onecolleague:runtime-bootstrap",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            skill_payload = enable_result.get("skill") if isinstance(enable_result.get("skill"), dict) else {}
            self.assertEqual(str(skill_payload.get("capability_id") or ""), "skill:onecolleague:runtime-bootstrap")
            applied = skill_payload.get("applied_dependencies") if isinstance(skill_payload.get("applied_dependencies"), list) else []
            self.assertEqual(applied, ["pack:diagnostics", "pack:group-runtime"])

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            enabled = set(state.get("enabled_capabilities") or [])
            self.assertIn("skill:onecolleague:runtime-bootstrap", enabled)
            self.assertIn("pack:diagnostics", enabled)
            self.assertIn("pack:group-runtime", enabled)
            active_capsule_skills = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_ids = {str(item.get("capability_id") or "") for item in active_capsule_skills if isinstance(item, dict)}
            self.assertIn("skill:onecolleague:runtime-bootstrap", active_ids)
            active_row = next(
                (
                    item
                    for item in active_capsule_skills
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "skill:onecolleague:runtime-bootstrap"
                ),
                {},
            )
            self.assertIn("Restate the exact symptom", str(active_row.get("capsule_preview") or ""))
            self.assertIn("Gather evidence first", str(active_row.get("capsule_preview") or ""))
            visible_tools = set(state.get("visible_tools") or [])
            self.assertIn("onecolleague_terminal", visible_tools)
            self.assertIn("onecolleague_actor", visible_tools)
        finally:
            cleanup()

    def test_legacy_builtin_runtime_bootstrap_id_enables_canonical_skill(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "skill:cccc:runtime-bootstrap",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            self.assertEqual(str(enable_result.get("capability_id") or ""), "skill:onecolleague:runtime-bootstrap")

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            enabled = set(state.get("enabled_capabilities") or [])
            self.assertIn("skill:onecolleague:runtime-bootstrap", enabled)
            self.assertNotIn("skill:cccc:runtime-bootstrap", enabled)
        finally:
            cleanup()

    def test_skill_actor_scope_enable_is_active_not_autoload(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["skill:anthropic:triage"] = {
                "capability_id": "skill:anthropic:triage",
                "kind": "skill",
                "name": "triage",
                "description_short": "Issue triage checklist",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
                "capsule_text": "Use strict triage checklist.",
                "requires_capabilities": [],
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "skill:anthropic:triage",
                    "scope": "actor",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            autoload_skills = state.get("autoload_skills") if isinstance(state.get("autoload_skills"), list) else []
            autoload_ids = {str(item.get("capability_id") or "") for item in autoload_skills if isinstance(item, dict)}
            # actor-scope enable does not mutate startup autoload config.
            self.assertNotIn("skill:anthropic:triage", autoload_ids)
            active_capsule_skills = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_ids = {str(item.get("capability_id") or "") for item in active_capsule_skills if isinstance(item, dict)}
            self.assertIn("skill:anthropic:triage", active_ids)
            active_row = next(
                (
                    item
                    for item in active_capsule_skills
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "skill:anthropic:triage"
                ),
                {},
            )
            activation_sources = (
                active_row.get("activation_sources")
                if isinstance(active_row.get("activation_sources"), list)
                else []
            )
            self.assertEqual({str(item.get("scope") or "") for item in activation_sources if isinstance(item, dict)}, {"actor"})
        finally:
            cleanup()

    def test_actor_autoload_skill_is_reported_in_autoload_skills(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            add_resp, _ = self._call(
                "actor_add",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "runtime": "codex",
                    "runner": "headless",
                    "capability_autoload": ["skill:anthropic:triage"],
                    "by": "user",
                },
            )
            self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["skill:anthropic:triage"] = {
                "capability_id": "skill:anthropic:triage",
                "kind": "skill",
                "name": "triage",
                "description_short": "Issue triage checklist",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            actor_autoload = (
                state.get("actor_autoload_capabilities")
                if isinstance(state.get("actor_autoload_capabilities"), list)
                else []
            )
            self.assertIn("skill:anthropic:triage", actor_autoload)
            autoload_skills = state.get("autoload_skills") if isinstance(state.get("autoload_skills"), list) else []
            autoload_ids = {str(item.get("capability_id") or "") for item in autoload_skills if isinstance(item, dict)}
            self.assertNotIn("skill:anthropic:triage", autoload_ids)

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "capability_id": "skill:anthropic:triage",
                    "scope": "actor",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))

            active_state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(active_state_resp.ok, getattr(active_state_resp, "error", None))
            active_state = active_state_resp.result if isinstance(active_state_resp.result, dict) else {}
            effective_autoload_skills = (
                active_state.get("autoload_skills")
                if isinstance(active_state.get("autoload_skills"), list)
                else []
            )
            self.assertIn(
                "skill:anthropic:triage",
                {
                    str(item.get("capability_id") or "")
                    for item in effective_autoload_skills
                    if isinstance(item, dict)
                },
            )
            active_capsule_skills = (
                active_state.get("active_capsule_skills")
                if isinstance(active_state.get("active_capsule_skills"), list)
                else []
            )
            active_row = next(
                (
                    item
                    for item in active_capsule_skills
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == "skill:anthropic:triage"
                ),
                {},
            )
            self.assertTrue(active_row)
            activation_sources = (
                active_row.get("activation_sources")
                if isinstance(active_row.get("activation_sources"), list)
                else []
            )
            self.assertEqual({str(item.get("scope") or "") for item in activation_sources if isinstance(item, dict)}, {"actor"})
        finally:
            cleanup()

    def test_disable_with_cleanup_removes_cached_installation(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            self._seed_runtime_external_install(ops, runtime_doc, tools=[])
            ops._save_runtime_doc(runtime_path, runtime_doc)

            resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "mcp:test-server",
                    "scope": "session",
                    "enabled": False,
                    "cleanup": True,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertTrue(bool(result.get("removed_installation")))

            _, runtime_after = ops._load_runtime_doc()
            _, install_after = ops._runtime_install_for_capability(runtime_after, capability_id="mcp:test-server")
            self.assertIsNone(install_after)
        finally:
            cleanup()

    def test_disable_with_cleanup_skips_when_capability_still_bound_elsewhere(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            self._add_actor(gid, "peer-2", by="user")

            state_path, state_doc = ops._load_state_doc()
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-1",
                scope="session",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._set_enabled_capability(
                state_doc,
                group_id=gid,
                actor_id="peer-2",
                scope="actor",
                capability_id="mcp:test-server",
                enabled=True,
                ttl_seconds=600,
            )
            ops._save_state_doc(state_path, state_doc)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            self._seed_runtime_external_install(ops, runtime_doc, tools=[])
            ops._save_runtime_doc(runtime_path, runtime_doc)

            resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "mcp:test-server",
                    "scope": "session",
                    "enabled": False,
                    "cleanup": True,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertFalse(bool(result.get("removed_installation")))
            self.assertEqual(
                str(result.get("cleanup_skipped_reason") or ""),
                "cleanup_skipped_capability_still_bound",
            )

            _, runtime_after = ops._load_runtime_doc()
            _, install_after = ops._runtime_install_for_capability(runtime_after, capability_id="mcp:test-server")
            self.assertIsInstance(install_after, dict)
        finally:
            cleanup()

    def test_catalog_prune_respects_configured_limit(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        catalog = ops._new_catalog_doc()
        for i in range(205):
            catalog["records"][f"mcp:test-{i}"] = {
                "capability_id": f"mcp:test-{i}",
                "kind": "mcp_toolpack",
                "name": f"test-{i}",
                "source_id": "mcp_registry_official",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "updated_at_source": f"2026-02-25T00:00:0{i}Z",
                "last_synced_at": f"2026-02-25T00:00:0{i}Z",
            }

        with patch.dict(os.environ, {"CCCC_CAPABILITY_CATALOG_MAX_RECORDS": "200"}, clear=False):
            pruned = ops._prune_catalog_records(catalog)
            ops._refresh_source_record_counts(catalog)
        self.assertEqual(pruned, 5)
        records = catalog.get("records") if isinstance(catalog.get("records"), dict) else {}
        self.assertEqual(len(records), 200)
        source_state = catalog.get("sources", {}).get("mcp_registry_official", {})
        self.assertEqual(int(source_state.get("record_count") or 0), 200)

    def test_search_remote_fallback_augments_catalog(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="mounted")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            registry_payload = {
                "servers": [
                    {
                        "server": {
                            "name": "test-server",
                            "description": "Test MCP server",
                            "version": "1.0.0",
                            "remotes": [{"type": "http", "url": "http://127.0.0.1:9900/mcp"}],
                        },
                        "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}},
                    }
                ],
                "metadata": {"nextCursor": ""},
            }
            with patch(
                "no1.daemon.ops.capability_ops._http_get_json_obj",
                return_value=registry_payload,
            ) as get_json, patch.dict(
                os.environ,
                {
                    "CCCC_CAPABILITY_SOURCE_SKILLSMP_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_CLAWHUB_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_OPENCLAW_SKILLS_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_CLAWSKILLS_REMOTE_ENABLED": "0",
                },
                clear=False,
            ):
                resp, _ = self._call(
                    "capability_search",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "query": "test-server",
                        "kind": "mcp",
                        "include_external": True,
                        "limit": 20,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            ids = {str(item.get("capability_id") or "") for item in items if isinstance(item, dict)}
            self.assertIn("mcp:test-server", ids)
            diag = result.get("search_diagnostics") if isinstance(result.get("search_diagnostics"), dict) else {}
            self.assertTrue(bool(diag.get("remote_augmented")))
            self.assertEqual(int(diag.get("remote_added") or 0), 1)
            get_json.assert_called_once()
            applied_filters = result.get("applied_filters") if isinstance(result.get("applied_filters"), dict) else {}
            self.assertEqual(str(applied_filters.get("kind") or ""), "mcp_toolpack")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            self.assertTrue(catalog_path.exists())
            self.assertIn("mcp:test-server", catalog_doc.get("records", {}))
        finally:
            cleanup()

    def test_search_remote_fallback_can_be_disabled(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            with patch.dict(os.environ, {"CCCC_CAPABILITY_SEARCH_REMOTE_FALLBACK": "0"}, clear=False), patch(
                "no1.daemon.ops.capability_ops._http_get_json_obj",
                side_effect=AssertionError("remote fallback must be disabled"),
            ):
                resp, _ = self._call(
                    "capability_search",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "query": "definitely-not-local",
                        "include_external": True,
                        "limit": 10,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            diag = result.get("search_diagnostics") if isinstance(result.get("search_diagnostics"), dict) else {}
            self.assertFalse(bool(diag.get("remote_augmented")))
        finally:
            cleanup()

    def test_search_remote_fallback_augments_skill_from_openclaw(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            tree_payload = {
                "tree": [
                    {
                        "path": "skills/demo/rareskilltoken/SKILL.md",
                        "type": "blob",
                    }
                ]
            }
            openclaw_markdown = (
                "---\n"
                "name: rareskilltoken\n"
                "description: Unique OpenClaw test skill\n"
                "---\n"
                "Workflow notes\n"
            )

            def _fake_github_json(url: str, *, headers=None, timeout=10.0):
                if "/git/trees/" in str(url):
                    return tree_payload
                raise AssertionError(f"unexpected github URL: {url}")

            with patch(
                "no1.daemon.ops.capability_ops._http_get_json_obj",
                side_effect=_fake_github_json,
            ), patch(
                "no1.daemon.ops.capability_ops._http_get_text",
                return_value=openclaw_markdown,
            ), patch.dict(
                "no1.daemon.ops.capability_ops._OPENCLAW_TREE_CACHE",
                {"fetched_at": 0.0, "paths": []},
                clear=True,
            ), patch.dict(
                os.environ,
                {
                    "CCCC_CAPABILITY_SOURCE_CLAWHUB_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_CLAWSKILLS_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_OPENCLAW_SKILLS_REMOTE_ENABLED": "1",
                    "CCCC_CAPABILITY_SOURCE_SKILLSMP_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_OPENCLAW_FRONTMATTER_FETCH_MAX": "1",
                },
                clear=False,
            ):
                # Override allowlist so openclaw_skills_remote is mounted (visible in search)
                self._write_allowlist_override(
                    extra="    openclaw_skills_remote: mounted\n",
                )
                ops._POLICY_CACHE.clear()
                resp, _ = self._call(
                    "capability_search",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "query": "rareskilltoken",
                        "kind": "skill",
                        "include_external": True,
                        "limit": 20,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            row = next(
                (
                    x
                    for x in items
                    if isinstance(x, dict)
                    and str(x.get("capability_id") or "").startswith("skill:openclaw:rareskilltoken-")
                ),
                None,
            )
            self.assertIsNotNone(row)
            item = row if isinstance(row, dict) else {}
            self.assertEqual(str(item.get("source_id") or ""), "openclaw_skills_remote")
            self.assertEqual(str(item.get("enable_hint") or ""), "enable_now")
            diag = result.get("search_diagnostics") if isinstance(result.get("search_diagnostics"), dict) else {}
            self.assertTrue(bool(diag.get("remote_augmented")))
            self.assertGreaterEqual(int(diag.get("remote_added") or 0), 1)

            catalog_path, catalog_doc = ops._load_catalog_doc()
            self.assertTrue(catalog_path.exists())
            cached_ids = {
                str(k or "")
                for k in (
                    catalog_doc.get("records").keys()
                    if isinstance(catalog_doc.get("records"), dict)
                    else []
                )
            }
            self.assertTrue(any(cid.startswith("skill:openclaw:rareskilltoken-") for cid in cached_ids))
        finally:
            cleanup()

    def test_search_remote_skill_fallback_can_be_disabled(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            with patch.dict(
                os.environ,
                {
                    "CCCC_CAPABILITY_SOURCE_SKILLSMP_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_CLAWHUB_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_OPENCLAW_SKILLS_REMOTE_ENABLED": "0",
                    "CCCC_CAPABILITY_SOURCE_CLAWSKILLS_REMOTE_ENABLED": "0",
                },
                clear=False,
            ), patch(
                "no1.daemon.ops.capability_ops._http_get_json_obj",
                side_effect=AssertionError("openclaw remote fallback must be disabled"),
            ), patch(
                "no1.daemon.ops.capability_ops._http_get_text",
                side_effect=AssertionError("skillsmp/clawhub/clawskills remote fallback must be disabled"),
            ):
                resp, _ = self._call(
                    "capability_search",
                    {
                        "group_id": gid,
                        "actor_id": "peer-1",
                        "by": "peer-1",
                        "query": "creative",
                        "kind": "skill",
                        "include_external": True,
                        "limit": 20,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            diag = result.get("search_diagnostics") if isinstance(result.get("search_diagnostics"), dict) else {}
            self.assertFalse(bool(diag.get("remote_augmented")))
        finally:
            cleanup()

    def test_parse_skillsmp_proxy_search_markdown(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        markdown = (
            '[claudeception.md 1.7k ### export claudeception from "blader/Claudeception" '
            "Continuous learning skill 2026-02-21]"
            "(https://skillsmp.com/skills/blader-claudeception-skill-md)\n"
        )
        rows = ops._parse_skillsmp_proxy_search_markdown(markdown, limit=10)
        self.assertTrue(rows)
        first = rows[0] if isinstance(rows[0], dict) else {}
        self.assertEqual(str(first.get("source_id") or ""), "skillsmp_remote")
        self.assertEqual(str(first.get("kind") or ""), "skill")
        self.assertTrue(str(first.get("capability_id") or "").startswith("skill:skillsmp:"))
        self.assertIn("Continuous learning skill", str(first.get("description_short") or ""))

    def test_remote_search_skill_records_aggregates_sources(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        with patch(
            "no1.daemon.ops.capability_ops._remote_search_skillsmp_records",
            return_value=[
                {
                    "capability_id": "skill:skillsmp:a",
                    "kind": "skill",
                    "name": "a",
                    "description_short": "a",
                    "source_id": "skillsmp_remote",
                }
            ],
        ), patch(
            "no1.daemon.ops.capability_ops._remote_search_openclaw_skill_records",
            return_value=[
                {
                    "capability_id": "skill:openclaw:b",
                    "kind": "skill",
                    "name": "b",
                    "description_short": "b",
                    "source_id": "openclaw_skills_remote",
                }
            ],
        ), patch(
            "no1.daemon.ops.capability_ops._remote_search_clawhub_records",
            return_value=[
                {
                    "capability_id": "skill:clawhub:d",
                    "kind": "skill",
                    "name": "d",
                    "description_short": "d",
                    "source_id": "clawhub_remote",
                }
            ],
        ), patch(
            "no1.daemon.ops.capability_ops._remote_search_clawskills_records",
            return_value=[
                {
                    "capability_id": "skill:clawskills:c",
                    "kind": "skill",
                    "name": "c",
                    "description_short": "c",
                    "source_id": "clawskills_remote",
                }
            ],
        ):
            rows = ops._remote_search_skill_records(query="skill", limit=4)
        self.assertEqual(len(rows), 4)
        ids = {str(item.get("capability_id") or "") for item in rows if isinstance(item, dict)}
        self.assertIn("skill:skillsmp:a", ids)
        self.assertIn("skill:openclaw:b", ids)
        self.assertIn("skill:clawhub:d", ids)
        self.assertIn("skill:clawskills:c", ids)

    def test_remote_search_skill_records_honors_source_filter(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        with patch(
            "no1.daemon.ops.capability_ops._remote_search_skillsmp_records",
            return_value=[
                {
                    "capability_id": "skill:skillsmp:a",
                    "kind": "skill",
                    "name": "a",
                    "description_short": "a",
                    "source_id": "skillsmp_remote",
                }
            ],
        ), patch(
            "no1.daemon.ops.capability_ops._remote_search_openclaw_skill_records",
            return_value=[
                {
                    "capability_id": "skill:openclaw:b",
                    "kind": "skill",
                    "name": "b",
                    "description_short": "b",
                    "source_id": "openclaw_skills_remote",
                }
            ],
        ), patch(
            "no1.daemon.ops.capability_ops._remote_search_clawhub_records",
            return_value=[
                {
                    "capability_id": "skill:clawhub:d",
                    "kind": "skill",
                    "name": "d",
                    "description_short": "d",
                    "source_id": "clawhub_remote",
                }
            ],
        ), patch(
            "no1.daemon.ops.capability_ops._remote_search_clawskills_records",
            return_value=[
                {
                    "capability_id": "skill:clawskills:c",
                    "kind": "skill",
                    "name": "c",
                    "description_short": "c",
                    "source_id": "clawskills_remote",
                }
            ],
        ):
            rows = ops._remote_search_skill_records(query="skill", limit=5, source_filter="openclaw_skills_remote")
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0].get("source_id") or ""), "openclaw_skills_remote")

    def test_parse_clawskills_data_js(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        script = (
            "// Auto-generated from awesome-openclaw-skills README.md\n"
            "var SKILLS_DATA = [\n"
            "  {name:'humanizer',desc:\"Remove AI tone from text\",category:'Writing',slug:'humanizer',author:'biostartechnology'},\n"
            "  {name:'foo',desc:\"bar\",category:'Misc',slug:'foo',author:'alice'}\n"
            "];\n"
        )
        rows = ops._parse_clawskills_data_js(script=script, query="humanizer", limit=10)
        self.assertTrue(rows)
        first = rows[0] if isinstance(rows[0], dict) else {}
        self.assertEqual(str(first.get("source_id") or ""), "clawskills_remote")
        self.assertEqual(str(first.get("name") or ""), "humanizer")
        self.assertTrue(str(first.get("capability_id") or "").startswith("skill:clawskills:"))

    def test_clawhub_item_to_record(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        record = ops._clawhub_item_to_record(
            {
                "slug": "humanizer",
                "displayName": "Humanizer",
                "summary": "Remove AI tone from text",
                "latestVersion": {"version": "1.2.3"},
            },
            now_iso="2026-02-28T00:00:00Z",
        )
        self.assertIsInstance(record, dict)
        row = record if isinstance(record, dict) else {}
        self.assertEqual(str(row.get("source_id") or ""), "clawhub_remote")
        self.assertEqual(str(row.get("name") or ""), "humanizer")
        self.assertEqual(str(row.get("source_record_version") or ""), "1.2.3")
        self.assertTrue(str(row.get("capability_id") or "").startswith("skill:clawhub:"))

    def test_enable_can_fetch_missing_mcp_record_from_registry(self) -> None:
        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="mounted")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            registry_payload = {
                "servers": [
                    {
                        "server": {
                            "name": "test-server",
                            "description": "Test MCP server",
                            "version": "1.0.0",
                            "remotes": [{"type": "http", "url": "http://127.0.0.1:9900/mcp"}],
                        },
                        "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}},
                    }
                ],
                "metadata": {"nextCursor": ""},
            }
            installed = {
                "state": "installed",
                "installer": "remote_http",
                "install_mode": "remote_only",
                "invoker": {"type": "remote_http", "url": "http://127.0.0.1:9900/mcp"},
                "tools": [],
                "last_error": "",
                "updated_at": "2026-02-25T00:00:00Z",
            }
            with patch(
                "no1.daemon.ops.capability_ops._http_get_json_obj",
                return_value=registry_payload,
            ), patch(
                "no1.daemon.ops.capability_ops._install_external_capability",
                return_value=installed,
            ):
                resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": "peer-1",
                        "capability_id": "mcp:test-server",
                        "scope": "session",
                        "enabled": True,
                    },
                )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "activation_pending")
        finally:
            cleanup()

    def test_supported_external_install_record_accepts_pypi_and_oci(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        supported_pypi, reason_pypi = ops._supported_external_install_record(
            {
                "install_mode": "package",
                "install_spec": {
                    "registry_type": "pypi",
                    "identifier": "example-mcp",
                    "version": "1.2.3",
                    "runtime_hint": "uvx",
                },
            }
        )
        self.assertTrue(supported_pypi, reason_pypi)
        self.assertEqual(reason_pypi, "")

        supported_oci, reason_oci = ops._supported_external_install_record(
            {
                "install_mode": "package",
                "install_spec": {
                    "registry_type": "oci",
                    "identifier": "ghcr.io/example/mcp:0.1.0",
                    "runtime_hint": "docker",
                },
            }
        )
        self.assertTrue(supported_oci, reason_oci)
        self.assertEqual(reason_oci, "")

        supported_alias_python, reason_alias_python = ops._supported_external_install_record(
            {
                "install_mode": "package",
                "install_spec": {
                    "registry_type": "python",
                    "identifier": "example-mcp",
                    "runtime_hint": "pipx",
                },
            }
        )
        self.assertTrue(supported_alias_python, reason_alias_python)
        self.assertEqual(reason_alias_python, "")

        supported_alias_docker, reason_alias_docker = ops._supported_external_install_record(
            {
                "install_mode": "package",
                "install_spec": {
                    "registry_type": "docker",
                    "identifier": "ghcr.io/example/mcp:0.1.0",
                },
            }
        )
        self.assertTrue(supported_alias_docker, reason_alias_docker)
        self.assertEqual(reason_alias_docker, "")

        supported_alias_js, reason_alias_js = ops._supported_external_install_record(
            {
                "install_mode": "package",
                "install_spec": {
                    "registry_type": "javascript",
                    "identifier": "@example/mcp-server",
                    "runtime_hint": "nodejs",
                },
            }
        )
        self.assertTrue(supported_alias_js, reason_alias_js)
        self.assertEqual(reason_alias_js, "")

        supported_command, reason_command = ops._supported_external_install_record(
            {
                "install_mode": "command",
                "install_spec": {
                    "command_candidates": [["npx", "-y", "@example/mcp-server"]],
                },
            }
        )
        self.assertTrue(supported_command, reason_command)
        self.assertEqual(reason_command, "")

    def test_install_external_capability_pypi_prefers_uvx(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "pypi",
                "identifier": "realtimex-browser-use",
                "version": "0.7.10",
                "runtime_hint": "uvx",
                "runtime_arguments": [{"type": "positional", "value": "realtimex-browser-use[cli]@0.7.10"}],
                "package_arguments": [{"type": "positional", "value": "--mcp"}],
            },
        }

        with patch(
            "no1.daemon.ops.capability_ops._stdio_mcp_roundtrip",
            return_value=[
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "search"}]}},
            ],
        ) as probe:
            install = ops._install_external_capability(rec, capability_id="mcp:io.github.therealtimex/browser-use")

        self.assertEqual(str(install.get("installer") or ""), "pypi_uvx")
        invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
        self.assertEqual(str(invoker.get("type") or ""), "package_stdio")
        command = invoker.get("command") if isinstance(invoker.get("command"), list) else []
        self.assertTrue(command and str(command[0]) == "uvx")
        self.assertIn("--mcp", [str(x) for x in command])
        called_cmd = probe.call_args[0][0]
        self.assertTrue(isinstance(called_cmd, list) and called_cmd and str(called_cmd[0]) == "uvx")

    def test_install_external_capability_oci_builds_container_command(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "oci",
                "identifier": "ghcr.io/github/github-mcp-server:0.31.0",
                "runtime_hint": "docker",
                "runtime_arguments": [
                    {"type": "named", "name": "-e", "value": "GITHUB_PERSONAL_ACCESS_TOKEN={token}"}
                ],
                "required_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
            },
        }

        with patch.dict(os.environ, {"GITHUB_PERSONAL_ACCESS_TOKEN": "dummy-token"}, clear=False), patch(
            "no1.daemon.ops.capability_ops._stdio_mcp_roundtrip",
            return_value=[
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "create_issue"}]}},
            ],
        ) as probe:
            install = ops._install_external_capability(rec, capability_id="mcp:io.github.github/github-mcp-server")

        self.assertEqual(str(install.get("installer") or ""), "oci_docker")
        invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
        command = invoker.get("command") if isinstance(invoker.get("command"), list) else []
        self.assertGreaterEqual(len(command), 6)
        self.assertEqual(str(command[0]), "docker")
        self.assertEqual(str(command[1]), "run")
        self.assertIn("ghcr.io/github/github-mcp-server:0.31.0", [str(x) for x in command])
        self.assertIn("GITHUB_PERSONAL_ACCESS_TOKEN", [str(x) for x in command])
        called_cmd = probe.call_args[0][0]
        self.assertTrue(isinstance(called_cmd, list) and called_cmd and str(called_cmd[0]) == "docker")

    def test_install_external_capability_fails_fast_when_required_env_missing(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "npm",
                "identifier": "@brave/brave-search-mcp-server",
                "version": "2.0.75",
                "required_env": ["BRAVE_API_KEY"],
            },
        }
        with patch.dict(os.environ, {"BRAVE_API_KEY": ""}, clear=False):
            with self.assertRaises(ValueError) as ctx:
                ops._install_external_capability(rec, capability_id="mcp:io.github.brave/brave-search-mcp-server")
        self.assertIn("missing_required_env:BRAVE_API_KEY", str(ctx.exception))

    def test_install_external_capability_command_mode_installs(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "command",
            "install_spec": {
                "command_candidates": [["npx", "-y", "@example/mcp-server"]],
            },
        }

        with patch(
            "no1.daemon.ops.capability_ops._stdio_mcp_roundtrip",
            return_value=[
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "demo"}]}},
            ],
        ) as probe:
            install = ops._install_external_capability(rec, capability_id="mcp:example/command")

        self.assertEqual(str(install.get("state") or ""), "installed")
        self.assertEqual(str(install.get("install_mode") or ""), "command")
        self.assertIn(str(install.get("installer") or ""), {"command_stdio", "command_npx"})
        invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
        self.assertEqual(str(invoker.get("type") or ""), "command_stdio")
        command = invoker.get("command") if isinstance(invoker.get("command"), list) else []
        self.assertTrue(command and str(command[0]) == "npx")
        called_cmd = probe.call_args[0][0]
        self.assertTrue(isinstance(called_cmd, list) and called_cmd and str(called_cmd[0]) == "npx")

    def test_install_external_capability_package_falls_back_to_command_candidates(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "mystery-registry",
                "identifier": "",
                "fallback_command_candidates": [["npx", "-y", "@example/mcp-server"]],
            },
        }

        with patch(
            "no1.daemon.ops.capability_ops._stdio_mcp_roundtrip",
            return_value=[
                {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "demo"}]}},
            ],
        ):
            install = ops._install_external_capability(rec, capability_id="mcp:example/fallback-command")

        self.assertEqual(str(install.get("state") or ""), "installed")
        self.assertEqual(str(install.get("install_mode") or ""), "command")
        self.assertEqual(str(install.get("fallback_from") or ""), "package")

    def test_preflight_external_install_detects_missing_env_and_binary(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec_missing_env = {
            "install_mode": "command",
            "install_spec": {
                "command_candidates": [["npx", "-y", "@example/mcp-server"]],
                "required_env": ["OPENAI_API_KEY"],
            },
        }
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            preflight_env = ops._preflight_external_install(rec_missing_env, capability_id="mcp:example/preflight-env")
        self.assertFalse(bool(preflight_env.get("ok")))
        self.assertEqual(str(preflight_env.get("code") or ""), "missing_required_env")
        required_env = preflight_env.get("required_env") if isinstance(preflight_env.get("required_env"), list) else []
        self.assertIn("OPENAI_API_KEY", required_env)

        rec_missing_bin = {
            "install_mode": "command",
            "install_spec": {
                "command_candidates": [["definitely-missing-runtime-binary", "--version"]],
            },
        }
        preflight_bin = ops._preflight_external_install(rec_missing_bin, capability_id="mcp:example/preflight-bin")
        self.assertFalse(bool(preflight_bin.get("ok")))
        self.assertEqual(str(preflight_bin.get("code") or ""), "runtime_binary_missing")
        missing_binaries = (
            preflight_bin.get("missing_binaries")
            if isinstance(preflight_bin.get("missing_binaries"), list)
            else []
        )
        self.assertIn("definitely-missing-runtime-binary", missing_binaries)

    def test_preflight_external_install_detects_invalid_remote_url(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "remote_only",
            "install_spec": {
                "transport": "http",
                "url": "ftp://example.invalid/mcp",
            },
        }
        preflight = ops._preflight_external_install(rec, capability_id="mcp:example/preflight-url")
        self.assertFalse(bool(preflight.get("ok")))
        self.assertEqual(str(preflight.get("code") or ""), "invalid_remote_url")

    def test_capability_enable_returns_preflight_failed_for_missing_runtime_binary(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="mounted")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["mcp:example/preflight-fail"] = {
                "capability_id": "mcp:example/preflight-fail",
                "kind": "mcp_toolpack",
                "name": "preflight-fail",
                "description_short": "test preflight fail",
                "source_id": "mcp_registry_official",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
                "install_mode": "command",
                "install_spec": {
                    "command_candidates": [["definitely-missing-runtime-binary", "--version"]],
                },
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "mcp:example/preflight-fail",
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "blocked")
            self.assertEqual(str(result.get("reason") or ""), "preflight_failed:runtime_binary_missing")
            missing_binaries = result.get("missing_binaries") if isinstance(result.get("missing_binaries"), list) else []
            self.assertIn("definitely-missing-runtime-binary", missing_binaries)
            diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
            self.assertTrue(diagnostics)
        finally:
            cleanup()

    def test_install_external_capability_package_falls_back_to_next_runner(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "pypi",
                "identifier": "demo-mcp",
                "version": "1.0.0",
                "runtime_hint": "auto",
            },
        }

        def _roundtrip(cmd, requests, *, timeout_s):  # type: ignore[no-untyped-def]
            if isinstance(cmd, list) and cmd and str(cmd[0]) == "uvx":
                raise RuntimeError("uvx failed")
            return [{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "demo"}]}}]

        with patch("no1.daemon.ops.capability_ops._stdio_mcp_roundtrip", side_effect=_roundtrip):
            install = ops._install_external_capability(rec, capability_id="mcp:demo/runner-fallback")

        self.assertEqual(str(install.get("installer") or ""), "pypi_pipx")
        invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
        command = invoker.get("command") if isinstance(invoker.get("command"), list) else []
        self.assertTrue(command and str(command[0]) == "pipx")

    def test_install_external_capability_npm_retries_with_safe_env_flags(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "npm",
                "identifier": "@example/mcp-server",
            },
        }

        call_count = {"n": 0}

        def _roundtrip(cmd, requests, *, timeout_s, env_override=None):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if not env_override:
                raise RuntimeError("stdio mcp exited with code 1: Error: Cannot find module 'puppeteer'")
            return [{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "demo"}]}}]

        with patch("no1.daemon.ops.capability_ops._stdio_mcp_roundtrip", side_effect=_roundtrip):
            install = ops._install_external_capability(rec, capability_id="mcp:example/npm-autofix")

        self.assertEqual(str(install.get("state") or ""), "installed")
        self.assertEqual(str(install.get("installer") or ""), "npm_npx")
        invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
        self.assertEqual(str(invoker.get("type") or ""), "package_stdio")
        self.assertIn("env", invoker)
        env = invoker.get("env") if isinstance(invoker.get("env"), dict) else {}
        self.assertEqual(str(env.get("PUPPETEER_SKIP_DOWNLOAD") or ""), "1")
        self.assertGreaterEqual(call_count["n"], 2)

    def test_install_external_capability_npm_falls_back_to_unpinned_version(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "npm",
                "identifier": "@example/mcp-server",
                "version": "1.0.0",
            },
        }

        def _roundtrip(cmd, requests, *, timeout_s, env_override=None):  # type: ignore[no-untyped-def]
            token = " ".join(str(x) for x in cmd)
            if "@example/mcp-server@1.0.0" in token:
                raise RuntimeError("stdio mcp exited with code 1: Error: Cannot find module 'broken-version'")
            return [{"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "demo"}]}}]

        with patch("no1.daemon.ops.capability_ops._stdio_mcp_roundtrip", side_effect=_roundtrip):
            install = ops._install_external_capability(rec, capability_id="mcp:example/npm-unpinned-fallback")

        self.assertEqual(str(install.get("state") or ""), "installed")
        invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
        command = invoker.get("command") if isinstance(invoker.get("command"), list) else []
        cmd_token = " ".join(str(x) for x in command)
        self.assertIn("@example/mcp-server", cmd_token)
        self.assertNotIn("@example/mcp-server@1.0.0", cmd_token)

    def test_install_external_capability_degrades_when_probe_times_out(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        rec = {
            "install_mode": "package",
            "install_spec": {
                "registry_type": "npm",
                "identifier": "@example/mcp-server",
                "runtime_hint": "node",
            },
        }

        with patch("no1.daemon.ops.capability_ops._stdio_mcp_roundtrip", side_effect=TimeoutError("stdio mcp request timed out")), patch(
            "no1.daemon.ops.capability_ops._choose_available_command",
            return_value=[["npx", "-y", "@example/mcp-server"]],
        ):
            install = ops._install_external_capability(rec, capability_id="mcp:example/timeout")

            self.assertEqual(str(install.get("state") or ""), "installed_degraded")
            self.assertEqual(str(install.get("installer") or ""), "npm_npx")
            invoker = install.get("invoker") if isinstance(install.get("invoker"), dict) else {}
            self.assertEqual(str(invoker.get("type") or ""), "package_stdio")
            command = invoker.get("command") if isinstance(invoker.get("command"), list) else []
            self.assertTrue(command and str(command[0]) == "npx")
        self.assertEqual(install.get("tools"), [])
        self.assertEqual(str(install.get("last_error_code") or ""), "probe_timeout")

    def test_classify_external_install_error_detects_runtime_permission_denied(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        info = ops._classify_external_install_error(
            RuntimeError(
                "docker: permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock"
            )
        )
        self.assertEqual(str(info.get("code") or ""), "runtime_permission_denied")

    def test_capability_uninstall_skill_removes_bindings(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "foreman-1", by="user")
            self._add_actor(gid, "peer-1", by="user")

            catalog_path, catalog_doc = ops._load_catalog_doc()
            catalog_doc["records"]["skill:anthropic:triage"] = {
                "capability_id": "skill:anthropic:triage",
                "kind": "skill",
                "name": "triage",
                "description_short": "triage helper",
                "source_id": "anthropic_skills",
                "source_tier": "tier1",
                "trust_tier": "tier1",
                "qualification_status": "qualified",
                "enable_supported": True,
                "install_mode": "builtin",
                "install_spec": {},
            }
            ops._save_catalog_doc(catalog_path, catalog_doc)

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": "skill:anthropic:triage",
                    "scope": "actor",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))

            uninstall_resp, _ = self._call(
                "capability_uninstall",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": "skill:anthropic:triage",
                    "reason": "cleanup skill binding",
                },
            )
            self.assertTrue(uninstall_resp.ok, getattr(uninstall_resp, "error", None))
            result = uninstall_resp.result if isinstance(uninstall_resp.result, dict) else {}
            self.assertGreaterEqual(int(result.get("removed_bindings") or 0), 1)
            self.assertFalse(bool(result.get("removed_installation")))

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            enabled = set(state.get("enabled_capabilities") or [])
            self.assertNotIn("skill:anthropic:triage", enabled)
            active_capsule_skills = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_ids = {str(item.get("capability_id") or "") for item in active_capsule_skills if isinstance(item, dict)}
            self.assertNotIn("skill:anthropic:triage", active_ids)
            autoload_skills = state.get("autoload_skills") if isinstance(state.get("autoload_skills"), list) else []
            autoload_ids = {str(item.get("capability_id") or "") for item in autoload_skills if isinstance(item, dict)}
            self.assertNotIn("skill:anthropic:triage", autoload_ids)

            search_resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "triage",
                    "kind": "skill",
                    "include_external": False,
                    "limit": 20,
                },
            )
            self.assertTrue(search_resp.ok, getattr(search_resp, "error", None))
            search_result = search_resp.result if isinstance(search_resp.result, dict) else {}
            search_ids = {
                str(item.get("capability_id") or "")
                for item in (search_result.get("items") if isinstance(search_result.get("items"), list) else [])
                if isinstance(item, dict)
            }
            self.assertNotIn("skill:anthropic:triage", search_ids)

            overview_resp, _ = self._call(
                "capability_overview",
                {"group_id": gid, "query": "triage", "include_indexed": True, "limit": 20},
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            overview_result = overview_resp.result if isinstance(overview_resp.result, dict) else {}
            overview_ids = {
                str(item.get("capability_id") or "")
                for item in (
                    overview_result.get("items") if isinstance(overview_result.get("items"), list) else []
                )
                if isinstance(item, dict)
            }
            self.assertNotIn("skill:anthropic:triage", overview_ids)

            for scope in ("actor", "session"):
                blocked_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "capability_id": "skill:anthropic:triage",
                        "scope": scope,
                        "enabled": True,
                    },
                )
                self.assertTrue(blocked_resp.ok, getattr(blocked_resp, "error", None))
                blocked_result = blocked_resp.result if isinstance(blocked_resp.result, dict) else {}
                self.assertEqual(str(blocked_result.get("state") or ""), "blocked")
                self.assertEqual(str(blocked_result.get("reason") or ""), "removed_by_group_policy")

            _, state_doc = ops._load_state_doc()
            self.assertIn(
                "skill:anthropic:triage",
                set(ops._collect_removed_capabilities(state_doc, group_id=gid)),
            )

            other_gid = self._create_group("capability-other-group")
            self._add_actor(other_gid, "peer-2", by="user")
            other_search_resp, _ = self._call(
                "capability_search",
                {
                    "group_id": other_gid,
                    "actor_id": "peer-2",
                    "by": "peer-2",
                    "query": "triage",
                    "kind": "skill",
                    "include_external": False,
                    "limit": 20,
                },
            )
            self.assertTrue(other_search_resp.ok, getattr(other_search_resp, "error", None))
            other_result = other_search_resp.result if isinstance(other_search_resp.result, dict) else {}
            other_ids = {
                str(item.get("capability_id") or "")
                for item in (other_result.get("items") if isinstance(other_result.get("items"), list) else [])
                if isinstance(item, dict)
            }
            self.assertIn("skill:anthropic:triage", other_ids)

            restore_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "foreman-1",
                    "actor_id": "foreman-1",
                    "capability_id": "skill:anthropic:triage",
                    "scope": "group",
                    "enabled": True,
                },
            )
            self.assertTrue(restore_resp.ok, getattr(restore_resp, "error", None))
            restore_result = restore_resp.result if isinstance(restore_resp.result, dict) else {}
            self.assertEqual(str(restore_result.get("state") or ""), "runnable")
            _, restored_state = ops._load_state_doc()
            self.assertNotIn(
                "skill:anthropic:triage",
                set(ops._collect_removed_capabilities(restored_state, group_id=gid)),
            )
        finally:
            cleanup()

    def test_capability_import_dry_run_does_not_persist_record(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:github:demo:triage"
            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "name": "Demo Triage",
                        "capsule_text": "Use triage checklist",
                    },
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertTrue(bool(result.get("dry_run")))
            self.assertFalse(bool(result.get("imported")))
            self.assertEqual(str(result.get("state") or ""), "enableable")
            self.assertEqual(str(result.get("capability_id") or ""), capability_id)

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            self.assertNotIn(capability_id, rows)
        finally:
            cleanup()

    def test_capability_import_dry_run_normalizes_source_and_reports_policy_enableable(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": "mcp:example/manual-import",
                        "kind": "mcp_toolpack",
                        "source_id": "github:random/repo",
                        "install_mode": "command",
                        "install_spec": {
                            "command": ["uvx", "demo-mcp"],
                        },
                    },
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            record = result.get("record") if isinstance(result.get("record"), dict) else {}
            self.assertEqual(str(record.get("source_id") or ""), "manual_import")
            self.assertEqual(str(result.get("effective_policy_level") or ""), "mounted")
            self.assertTrue(bool(result.get("enableable_now")))
            self.assertEqual(str(result.get("enable_block_reason") or ""), "")
            readiness = result.get("readiness_preview") if isinstance(result.get("readiness_preview"), dict) else {}
            self.assertEqual(str(readiness.get("preview_status") or ""), "enableable")
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_skill_source_is_narrowly_enableable(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": "skill:agent_self_proposed:repro-triage",
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Repro Triage",
                        "description_short": "Reusable repro-first triage checklist",
                        "capsule_text": (
                            "Skill: Repro Triage\n"
                            "When to use:\n"
                            "- Recurring debugging work after a stable repro path was found.\n"
                            "Avoid when:\n"
                            "- One-off status reporting.\n"
                            "Procedure:\n"
                            "1. Capture the failing trigger.\n"
                            "Pitfalls:\n"
                            "- Do not generalize one lucky workaround.\n"
                            "Verification:\n"
                            "- Re-run the same repro case."
                        ),
                    },
                },
            )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            record = result.get("record") if isinstance(result.get("record"), dict) else {}
            self.assertEqual(str(record.get("source_id") or ""), "agent_self_proposed")
            self.assertEqual(str(result.get("effective_policy_level") or ""), "mounted")
            self.assertTrue(bool(result.get("enableable_now")))
            readiness = result.get("readiness_preview") if isinstance(result.get("readiness_preview"), dict) else {}
            self.assertEqual(str(readiness.get("preview_status") or ""), "enableable")
        finally:
            cleanup()

    def test_self_evolution_curated_skills_have_actionable_capsules(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            policy = ops._allowlist_policy()
            records = ops._build_curated_records_from_policy(policy)
            for capability_id in [
                "skill:anthropic:skill-creator",
                "skill:github:blader:claudeception",
            ]:
                record = records.get(capability_id) if isinstance(records.get(capability_id), dict) else {}
                capsule_text = str(record.get("capsule_text") or "")
                self.assertIn("When to use:", capsule_text)
                self.assertIn("Avoid when:", capsule_text)
                self.assertIn("Procedure:", capsule_text)
                self.assertIn("Pitfalls:", capsule_text)
                self.assertIn("Verification:", capsule_text)
                self.assertIn("skill:agent_self_proposed:", capsule_text)
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_skill_persists_enables_and_is_visible(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:agent_self_proposed:self-evolution-smoke"

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "user",
                    "enable_after_import": True,
                    "scope": "session",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Self Evolution Smoke",
                        "description_short": "Smoke-test skill for autonomous proposal visibility.",
                        "capsule_text": (
                            "Skill: Self Evolution Smoke\n"
                            "When to use:\n"
                            "- Verify that self-proposed skill proposals are visible and activatable.\n"
                            "Avoid when:\n"
                            "- Production workflow execution.\n"
                            "Procedure:\n"
                            "1. Import and enable the proposed skill.\n"
                            "Pitfalls:\n"
                            "- Do not confuse catalog presence with runtime activation.\n"
                            "Verification:\n"
                            "- Re-read capability_state.active_capsule_skills."
                        ),
                    },
                },
            )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertIn(str(result.get("state") or ""), {"activation_pending", "runnable"})
            self.assertEqual(str(result.get("scope") or ""), "session")
            self.assertEqual(str(result.get("effective_policy_level") or ""), "mounted")
            self.assertTrue(bool(result.get("enableable_now")))
            enable_result = result.get("enable_result") if isinstance(result.get("enable_result"), dict) else {}
            self.assertEqual(str(enable_result.get("scope") or ""), "session")

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "user", "by": "user"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            enabled = set(state.get("enabled_capabilities") or [])
            self.assertIn(capability_id, enabled)
            active_capsule_skills = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_rows = [item for item in active_capsule_skills if isinstance(item, dict)]
            active_ids = {str(item.get("capability_id") or "") for item in active_rows}
            self.assertIn(capability_id, active_ids)
            active_row = next(item for item in active_rows if str(item.get("capability_id") or "") == capability_id)
            self.assertEqual(str(active_row.get("source_id") or ""), "agent_self_proposed")

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            record = rows.get(capability_id) if isinstance(rows.get(capability_id), dict) else {}
            self.assertEqual(str(record.get("source_id") or ""), "agent_self_proposed")
            self.assertEqual(str(record.get("origin_group_id") or ""), gid)
            self.assertEqual(str(record.get("qualification_status") or ""), "qualified")

            overview_resp, _ = self._call(
                "capability_overview",
                {
                    "query": capability_id,
                    "limit": 50,
                    "include_indexed": True,
                },
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            overview_result = overview_resp.result if isinstance(overview_resp.result, dict) else {}
            overview_items = overview_result.get("items") if isinstance(overview_result.get("items"), list) else []
            overview_row = next(
                (
                    item
                    for item in overview_items
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            self.assertTrue(overview_row)
            self.assertEqual(str(overview_row.get("source_id") or ""), "agent_self_proposed")
            self.assertEqual(str(overview_row.get("source_record_id") or ""), capability_id)
            self.assertEqual(str(overview_row.get("origin_group_id") or ""), gid)
            self.assertTrue(str(overview_row.get("updated_at_source") or "").strip())
            self.assertTrue(str(overview_row.get("last_synced_at") or "").strip())
            self.assertIn("Self Evolution Smoke", str(overview_row.get("capsule_text") or ""))
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_reimport_reports_update_and_active_binding(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:agent_self_proposed:self-evolution-active-update"

            def _capsule(version: str, procedure: str) -> str:
                return (
                    "Skill: Self Evolution Active Update\n"
                    "When to use:\n"
                    f"- Maintain an active self-proposed skill after {version} evidence.\n"
                    "Avoid when:\n"
                    "- The skill is not already scoped to the current actor.\n"
                    "Procedure:\n"
                    f"1. {procedure}\n"
                    "Pitfalls:\n"
                    "- Use import_action for create/update/unchanged state.\n"
                    "Verification:\n"
                    "- Re-read capability_state.active_capsule_skills."
                )

            first_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "enable_after_import": True,
                    "scope": "session",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Self Evolution Active Update",
                        "description_short": "initial active update guidance",
                        "capsule_text": _capsule("initial", "Capture the first proven active path."),
                    },
                },
            )
            self.assertTrue(first_resp.ok, getattr(first_resp, "error", None))
            first = first_resp.result if isinstance(first_resp.result, dict) else {}
            self.assertEqual(str(first.get("import_action") or ""), "created")
            self.assertEqual(str(first.get("scope") or ""), "session")
            self.assertFalse(bool(first.get("record_changed")))
            self.assertFalse(bool(first.get("already_active")))
            self.assertTrue(bool(first.get("active_after_import")))
            self.assertNotIn("deduped", first)
            self.assertNotIn("record_existed", first)
            self.assertIn(str(first.get("state") or ""), {"activation_pending", "runnable"})

            second_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Self Evolution Active Update",
                        "description_short": "revised active update guidance",
                        "capsule_text": _capsule("revised", "Patch the active capability_id in place."),
                    },
                },
            )
            self.assertTrue(second_resp.ok, getattr(second_resp, "error", None))
            second = second_resp.result if isinstance(second_resp.result, dict) else {}
            self.assertEqual(str(second.get("import_action") or ""), "updated")
            self.assertEqual(str(second.get("scope") or ""), "session")
            self.assertTrue(bool(second.get("record_changed")))
            self.assertTrue(bool(second.get("already_active")))
            self.assertTrue(bool(second.get("active_after_import")))
            self.assertNotIn("deduped", second)
            self.assertNotIn("record_existed", second)
            self.assertEqual(str(second.get("state") or ""), "runnable")
            second_readiness = second.get("readiness_preview") if isinstance(second.get("readiness_preview"), dict) else {}
            self.assertEqual(str(second_readiness.get("preview_status") or ""), "active")
            self.assertEqual(str(second_readiness.get("next_step") or ""), "none")
            self.assertTrue(bool(second_readiness.get("already_active")))
            second_record = second.get("record") if isinstance(second.get("record"), dict) else {}

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            active_rows = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_row = next(
                (
                    item
                    for item in active_rows
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            self.assertIn("Patch the active capability_id in place.", str(active_row.get("capsule_text") or ""))
            self.assertIn("Verification:", str(active_row.get("capsule_text") or ""))

            search_resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": capability_id,
                    "include_external": True,
                    "limit": 20,
                },
            )
            self.assertTrue(search_resp.ok, getattr(search_resp, "error", None))
            search_result = search_resp.result if isinstance(search_resp.result, dict) else {}
            search_items = search_result.get("items") if isinstance(search_result.get("items"), list) else []
            search_row = next(
                (
                    item
                    for item in search_items
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            self.assertEqual(str(search_row.get("enable_hint") or ""), "active")
            search_readiness = (
                search_row.get("readiness_preview") if isinstance(search_row.get("readiness_preview"), dict) else {}
            )
            self.assertEqual(str(search_readiness.get("preview_status") or ""), "active")
            self.assertEqual(str(search_readiness.get("next_step") or ""), "none")
            self.assertTrue(bool(search_readiness.get("already_active")))

            third_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Self Evolution Active Update",
                        "description_short": "revised active update guidance",
                        "capsule_text": _capsule("revised", "Patch the active capability_id in place."),
                    },
                },
            )
            self.assertTrue(third_resp.ok, getattr(third_resp, "error", None))
            third = third_resp.result if isinstance(third_resp.result, dict) else {}
            self.assertEqual(str(third.get("import_action") or ""), "unchanged")
            self.assertEqual(str(third.get("scope") or ""), "session")
            self.assertFalse(bool(third.get("record_changed")))
            self.assertTrue(bool(third.get("already_active")))
            self.assertTrue(bool(third.get("active_after_import")))
            self.assertNotIn("deduped", third)
            self.assertNotIn("record_existed", third)
            self.assertEqual(str(third.get("state") or ""), "runnable")
            third_readiness = third.get("readiness_preview") if isinstance(third.get("readiness_preview"), dict) else {}
            self.assertEqual(str(third_readiness.get("preview_status") or ""), "active")
            self.assertEqual(str(third_readiness.get("next_step") or ""), "none")
            self.assertTrue(bool(third_readiness.get("already_active")))
            third_record = third.get("record") if isinstance(third.get("record"), dict) else {}
            self.assertEqual(
                str(third_record.get("last_synced_at") or ""),
                str(second_record.get("last_synced_at") or ""),
            )
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_invalid_update_preserves_active_record(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:agent_self_proposed:self-evolution-invalid-update"
            initial_capsule = (
                "Skill: Self Evolution Invalid Update\n"
                "When to use:\n"
                "- Preserve the last valid skill when a malformed update arrives.\n"
                "Avoid when:\n"
                "- The update already passes the required capsule template.\n"
                "Procedure:\n"
                "1. Keep the active record intact.\n"
                "Pitfalls:\n"
                "- Do not let an invalid update remove a live capsule.\n"
                "Verification:\n"
                "- Re-read active_capsule_skills and the catalog row."
            )
            invalid_capsule = (
                "Skill: Self Evolution Invalid Update\n"
                "When to use:\n"
                "- This malformed update intentionally omits one required section.\n"
                "Avoid when:\n"
                "- The existing active record is more complete.\n"
                "Procedure:\n"
                "1. This should be rejected before it overwrites catalog state.\n"
                "Verification:\n"
                "- This should never replace the active record."
            )

            first_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "enable_after_import": True,
                    "scope": "session",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Self Evolution Invalid Update",
                        "description_short": "initial valid capsule",
                        "capsule_text": initial_capsule,
                    },
                },
            )
            self.assertTrue(first_resp.ok, getattr(first_resp, "error", None))
            first = first_resp.result if isinstance(first_resp.result, dict) else {}
            self.assertTrue(bool(first.get("active_after_import")))

            invalid_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Self Evolution Invalid Update",
                        "description_short": "malformed update",
                        "capsule_text": invalid_capsule,
                    },
                },
            )
            self.assertFalse(invalid_resp.ok)
            self.assertEqual(getattr(invalid_resp.error, "code", ""), "capability_import_invalid")
            details = invalid_resp.error.details if invalid_resp.error is not None else {}
            self.assertTrue(bool(details.get("active_record_preserved")))
            self.assertTrue(bool(details.get("already_active")))
            self.assertIn("pitfalls", details.get("missing_sections") or [])

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            active_rows = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_row = next(
                (
                    item
                    for item in active_rows
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            self.assertIn("Keep the active record intact.", str(active_row.get("capsule_text") or ""))
            self.assertNotIn("This should be rejected", str(active_row.get("capsule_text") or ""))

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            record = rows.get(capability_id) if isinstance(rows.get(capability_id), dict) else {}
            self.assertEqual(str(record.get("qualification_status") or ""), "qualified")
            self.assertIn("Keep the active record intact.", str(record.get("capsule_text") or ""))
            self.assertNotIn("This should be rejected", str(record.get("capsule_text") or ""))
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_skill_reimport_updates_same_record(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:agent_self_proposed:self-evolution-maintenance"

            def _capsule(version: str, procedure: str) -> str:
                return (
                    "Skill: Self Evolution Maintenance\n"
                    "When to use:\n"
                    f"- Maintain a self-proposed skill after {version} evidence.\n"
                    "Avoid when:\n"
                    "- The existing skill belongs to an external curated source.\n"
                    "Procedure:\n"
                    f"1. {procedure}\n"
                    "Pitfalls:\n"
                    "- Do not create a near-duplicate for the same workflow.\n"
                    "Verification:\n"
                    "- Re-read the catalog record by capability_id."
                )

            for version, procedure in [
                ("initial", "Capture the first proven maintenance path."),
                ("revised", "Patch the existing capability_id with revised capsule_text."),
            ]:
                resp, _ = self._call(
                    "capability_import",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "probe": False,
                        "record": {
                            "capability_id": capability_id,
                            "kind": "skill",
                            "source_id": "agent_self_proposed",
                            "name": "Self Evolution Maintenance",
                            "description_short": f"{version} maintenance guidance",
                            "capsule_text": _capsule(version, procedure),
                        },
                    },
                )
                self.assertTrue(resp.ok, getattr(resp, "error", None))

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            matching_ids = [cap_id for cap_id in rows.keys() if str(cap_id or "") == capability_id]
            self.assertEqual(matching_ids, [capability_id])

            record = rows.get(capability_id) if isinstance(rows.get(capability_id), dict) else {}
            self.assertEqual(str(record.get("source_id") or ""), "agent_self_proposed")
            self.assertEqual(str(record.get("description_short") or ""), "revised maintenance guidance")
            capsule_text = str(record.get("capsule_text") or "")
            self.assertIn("Patch the existing capability_id with revised capsule_text.", capsule_text)
            self.assertNotIn("Capture the first proven maintenance path.", capsule_text)
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_reimport_preserves_origin_group(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            origin_gid = self._create_group()
            other_gid = self._create_group()
            self._add_actor(origin_gid, "peer-1", by="user")
            self._add_actor(other_gid, "peer-2", by="user")
            capability_id = "skill:agent_self_proposed:origin-group-preserved"

            def _capsule(version: str) -> str:
                return (
                    "Skill: Origin Group Preserved\n"
                    "When to use:\n"
                    f"- Track origin group metadata during {version} imports.\n"
                    "Avoid when:\n"
                    "- The record is not self-proposed.\n"
                    "Procedure:\n"
                    "1. Re-import the same capability id from another group.\n"
                    "Pitfalls:\n"
                    "- Do not silently reassign the origin group on edit.\n"
                    "Verification:\n"
                    "- Re-read the catalog record and overview row."
                )

            first_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": origin_gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Origin Group Preserved",
                        "description_short": "initial origin group",
                        "origin_group_id": other_gid,
                        "capsule_text": _capsule("initial"),
                    },
                },
            )
            self.assertTrue(first_resp.ok, getattr(first_resp, "error", None))

            second_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": other_gid,
                    "by": "peer-2",
                    "actor_id": "peer-2",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Origin Group Preserved",
                        "description_short": "updated elsewhere",
                        "origin_group_id": other_gid,
                        "capsule_text": _capsule("cross-group update"),
                    },
                },
            )
            self.assertTrue(second_resp.ok, getattr(second_resp, "error", None))
            second = second_resp.result if isinstance(second_resp.result, dict) else {}
            second_record = second.get("record") if isinstance(second.get("record"), dict) else {}
            self.assertEqual(str(second_record.get("origin_group_id") or ""), origin_gid)

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            record = rows.get(capability_id) if isinstance(rows.get(capability_id), dict) else {}
            self.assertEqual(str(record.get("origin_group_id") or ""), origin_gid)

            overview_resp, _ = self._call(
                "capability_overview",
                {
                    "query": capability_id,
                    "limit": 200,
                    "include_indexed": True,
                },
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            overview = overview_resp.result if isinstance(overview_resp.result, dict) else {}
            rows = overview.get("items") if isinstance(overview.get("items"), list) else []
            row = next(
                (
                    item
                    for item in rows
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            self.assertTrue(row)
            self.assertEqual(str(row.get("origin_group_id") or ""), origin_gid)
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_skill_requires_template_sections(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": "skill:agent_self_proposed:thin-note",
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Thin Note",
                        "capsule_text": "Use a triage checklist.",
                    },
                },
            )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            record = result.get("record") if isinstance(result.get("record"), dict) else {}
            self.assertEqual(str(record.get("qualification_status") or ""), "blocked")
            reasons = record.get("qualification_reasons") if isinstance(record.get("qualification_reasons"), list) else []
            self.assertTrue(any("missing_agent_self_proposed_sections" in str(item) for item in reasons))
            self.assertFalse(bool(result.get("enableable_now")))
            self.assertEqual(str(result.get("enable_block_reason") or ""), "qualification_blocked")
            readiness = result.get("readiness_preview") if isinstance(result.get("readiness_preview"), dict) else {}
            self.assertEqual(str(readiness.get("preview_status") or ""), "blocked")
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_skill_requires_canonical_namespace(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": "skill:github:example:self-proposed-collision",
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Self Proposed Collision",
                        "capsule_text": (
                            "Skill: Self Proposed Collision\n"
                            "When to use:\n"
                            "- Verify namespace collision protection.\n"
                            "Avoid when:\n"
                            "- Writing curated namespace records.\n"
                            "Procedure:\n"
                            "1. Try to import a self-proposed skill under a curated namespace.\n"
                            "Pitfalls:\n"
                            "- A valid capsule body must not bypass namespace protection.\n"
                            "Verification:\n"
                            "- The import is rejected before catalog persistence."
                        ),
                    },
                },
            )

            self.assertFalse(resp.ok)
            self.assertEqual(resp.error.code if resp.error else "", "capability_import_invalid")
            self.assertIn("skill:agent_self_proposed:", resp.error.message if resp.error else "")
        finally:
            cleanup()

    def test_legacy_agent_self_proposed_skill_namespace_is_not_enableable(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:agent:legacy-self-proposed"

            path, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            rows[capability_id] = {
                "capability_id": capability_id,
                "kind": "skill",
                "source_id": "agent_self_proposed",
                "name": "Legacy Self Proposed",
                "description_short": "Legacy non-canonical self-proposed skill.",
                "qualification_status": "qualified",
                "enable_supported": True,
                "capsule_text": (
                    "Skill: Legacy Self Proposed\n"
                    "When to use:\n"
                    "- Verify legacy namespace rejection.\n"
                    "Avoid when:\n"
                    "- Enabling current self-proposed skills.\n"
                    "Procedure:\n"
                    "1. Try to enable the legacy capability id.\n"
                    "Pitfalls:\n"
                    "- Do not carry old skill:agent: ids forward.\n"
                    "Verification:\n"
                    "- Enable returns capability_unavailable."
                ),
            }
            catalog_doc["records"] = rows
            ops._save_catalog_doc(path, catalog_doc)
            _, normalized_catalog = ops._load_catalog_doc()
            normalized_rows = (
                normalized_catalog.get("records") if isinstance(normalized_catalog.get("records"), dict) else {}
            )
            normalized_row = (
                normalized_rows.get(capability_id)
                if isinstance(normalized_rows.get(capability_id), dict)
                else {}
            )
            self.assertFalse(bool(normalized_row.get("enable_supported")))
            self.assertEqual(str(normalized_row.get("qualification_status") or ""), "blocked")
            qualification_reasons = (
                normalized_row.get("qualification_reasons")
                if isinstance(normalized_row.get("qualification_reasons"), list)
                else []
            )
            self.assertIn("legacy_agent_self_proposed_namespace", qualification_reasons)

            runtime_path, runtime_doc = ops._load_runtime_doc()
            ops._record_runtime_recent_success(
                runtime_doc,
                capability_id=capability_id,
                group_id=gid,
                actor_id="peer-1",
                action="enable",
            )
            ops._save_runtime_doc(runtime_path, runtime_doc)

            overview_resp, _ = self._call(
                "capability_overview",
                {
                    "query": capability_id,
                    "limit": 10,
                    "include_indexed": True,
                },
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            overview = overview_resp.result if isinstance(overview_resp.result, dict) else {}
            items = overview.get("items") if isinstance(overview.get("items"), list) else []
            row = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            self.assertFalse(bool(row.get("enable_supported")))
            readiness = row.get("readiness_preview") if isinstance(row.get("readiness_preview"), dict) else {}
            self.assertEqual(str(readiness.get("preview_status") or ""), "blocked")
            self.assertEqual(
                str(readiness.get("enable_block_reason") or ""),
                "legacy_agent_self_proposed_namespace",
            )
            self.assertEqual(
                str(readiness.get("next_step") or ""),
                "reimport_under_canonical_self_proposed_id",
            )
            self.assertEqual(str(readiness.get("canonical_prefix") or ""), "skill:agent_self_proposed:")
            self.assertIn("recent_success", readiness)

            actor_update, _ = self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "user",
                    "patch": {"capability_autoload": [capability_id]},
                },
            )
            self.assertTrue(actor_update.ok, getattr(actor_update, "error", None))

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            actor_autoload = (
                state.get("actor_autoload_capabilities")
                if isinstance(state.get("actor_autoload_capabilities"), list)
                else []
            )
            self.assertIn(capability_id, actor_autoload)
            autoload_skills = state.get("autoload_skills") if isinstance(state.get("autoload_skills"), list) else []
            autoload_ids = {str(item.get("capability_id") or "") for item in autoload_skills if isinstance(item, dict)}
            self.assertNotIn(capability_id, autoload_ids)
            active_skills = (
                state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            )
            active_ids = {str(item.get("capability_id") or "") for item in active_skills if isinstance(item, dict)}
            self.assertNotIn(capability_id, active_ids)
            hidden_rows = state.get("hidden_capabilities") if isinstance(state.get("hidden_capabilities"), list) else []
            hidden = next(
                (
                    item
                    for item in hidden_rows
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            self.assertEqual(str(hidden.get("reason") or ""), "legacy_agent_self_proposed_namespace")
            self.assertEqual(str(hidden.get("name") or ""), "Legacy Self Proposed")
            self.assertEqual(str(hidden.get("description_short") or ""), "Legacy non-canonical self-proposed skill.")
            self.assertEqual(str(hidden.get("kind") or ""), "skill")
            self.assertEqual(str(hidden.get("source_id") or ""), "agent_self_proposed")

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "capability_id": capability_id,
                    "scope": "session",
                    "enabled": True,
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
            enable_result = enable_resp.result if isinstance(enable_resp.result, dict) else {}
            self.assertEqual(str(enable_result.get("state") or ""), "blocked")
            self.assertEqual(str(enable_result.get("reason") or ""), "legacy_agent_self_proposed_namespace")
            diagnostics = (
                enable_result.get("diagnostics")
                if isinstance(enable_result.get("diagnostics"), list)
                else []
            )
            codes = {str(item.get("code") or "") for item in diagnostics if isinstance(item, dict)}
            self.assertIn("legacy_agent_self_proposed_namespace", codes)
            hints = {
                str(hint or "")
                for item in diagnostics
                if isinstance(item, dict)
                for hint in (item.get("action_hints") if isinstance(item.get("action_hints"), list) else [])
            }
            self.assertIn("reimport_the_capsule_under_skill_agent_self_proposed_stable_slug", hints)
            self.assertIn("call_cccc_capability_uninstall_on_the_legacy_capability_id_after_migration", hints)

            uninstall_resp, _ = self._call(
                "capability_uninstall",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": capability_id,
                    "reason": "cleanup legacy self-proposed skill",
                },
            )
            self.assertTrue(uninstall_resp.ok, getattr(uninstall_resp, "error", None))
            uninstall_result = uninstall_resp.result if isinstance(uninstall_resp.result, dict) else {}
            self.assertTrue(bool(uninstall_result.get("removed_record")))
            self.assertGreaterEqual(int(uninstall_result.get("removed_actor_autoload") or 0), 1)

            _, catalog_after = ops._load_catalog_doc()
            rows_after = catalog_after.get("records") if isinstance(catalog_after.get("records"), dict) else {}
            self.assertNotIn(capability_id, rows_after)

            actor_list, _ = self._call("actor_list", {"group_id": gid, "by": "user"})
            self.assertTrue(actor_list.ok, getattr(actor_list, "error", None))
            actors = actor_list.result.get("actors") if isinstance(actor_list.result, dict) else []
            peer = next(
                (item for item in actors if isinstance(item, dict) and str(item.get("id") or "") == "peer-1"),
                {},
            )
            self.assertNotIn(capability_id, peer.get("capability_autoload") or [])
        finally:
            cleanup()

    def test_capability_uninstall_self_proposed_skill_removes_record_and_references(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:agent_self_proposed:delete-cleanly"
            capsule_text = (
                "Skill: Remove Cleanly\n"
                "When to use:\n"
                "- Verify self-proposed skill removal.\n"
                "Avoid when:\n"
                "- Deleting curated skills.\n"
                "Procedure:\n"
                "1. Uninstall the self-proposed skill and clean references.\n"
                "Pitfalls:\n"
                "- Catalog deletion without autoload cleanup leaves stale references.\n"
                "Verification:\n"
                "- Re-read catalog, actor autoload, profile defaults, and capability state."
            )
            import_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "enable_after_import": True,
                    "scope": "session",
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Delete Cleanly",
                        "description_short": "delete cleanup validation",
                        "capsule_text": capsule_text,
                    },
                },
            )
            self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

            actor_update, _ = self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "user",
                    "patch": {"capability_autoload": [capability_id, "pack:space"]},
                },
            )
            self.assertTrue(actor_update.ok, getattr(actor_update, "error", None))

            profile_upsert, _ = self._call(
                "actor_profile_upsert",
                {
                    "by": "user",
                    "profile": {
                        "name": "Delete Cleanup Profile",
                        "runtime": "codex",
                        "runner": "headless",
                        "command": [],
                        "submit": "enter",
                        "capability_defaults": {
                            "autoload_capabilities": [capability_id, "pack:space"],
                            "default_scope": "actor",
                        },
                    },
                },
            )
            self.assertTrue(profile_upsert.ok, getattr(profile_upsert, "error", None))
            profile = (profile_upsert.result or {}).get("profile") if isinstance(profile_upsert.result, dict) else {}
            profile_id = str(profile.get("id") or "").strip() if isinstance(profile, dict) else ""
            self.assertTrue(profile_id)

            uninstall_resp, _ = self._call(
                "capability_uninstall",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": capability_id,
                    "reason": "user removed generated skill",
                },
            )
            self.assertTrue(uninstall_resp.ok, getattr(uninstall_resp, "error", None))
            uninstall_result = uninstall_resp.result if isinstance(uninstall_resp.result, dict) else {}
            self.assertTrue(bool(uninstall_result.get("removed_record")))
            self.assertGreaterEqual(int(uninstall_result.get("removed_bindings") or 0), 1)
            self.assertGreaterEqual(int(uninstall_result.get("removed_actor_autoload") or 0), 1)
            self.assertGreaterEqual(int(uninstall_result.get("removed_profile_autoload") or 0), 1)

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            self.assertNotIn(capability_id, rows)

            actor_list, _ = self._call("actor_list", {"group_id": gid, "by": "user"})
            self.assertTrue(actor_list.ok, getattr(actor_list, "error", None))
            actors = actor_list.result.get("actors") if isinstance(actor_list.result, dict) else []
            peer = next(
                (item for item in actors if isinstance(item, dict) and str(item.get("id") or "") == "peer-1"),
                {},
            )
            self.assertNotIn(capability_id, peer.get("capability_autoload") or [])
            self.assertIn("pack:space", peer.get("capability_autoload") or [])

            profile_get, _ = self._call("actor_profile_get", {"by": "user", "profile_id": profile_id})
            self.assertTrue(profile_get.ok, getattr(profile_get, "error", None))
            profile_after = (profile_get.result or {}).get("profile") if isinstance(profile_get.result, dict) else {}
            defaults = profile_after.get("capability_defaults") if isinstance(profile_after, dict) else {}
            profile_autoload = defaults.get("autoload_capabilities") if isinstance(defaults, dict) else []
            self.assertNotIn(capability_id, profile_autoload)
            self.assertIn("pack:space", profile_autoload)

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            self.assertNotIn(capability_id, state.get("enabled_capabilities") or [])
            active_rows = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_ids = {str(item.get("capability_id") or "") for item in active_rows if isinstance(item, dict)}
            self.assertNotIn(capability_id, active_ids)
        finally:
            cleanup()

    def test_capability_state_reports_capability_usage_summary(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            self._add_actor(gid, "peer-2", by="user")
            capability_id = "skill:agent_self_proposed:usage-summary"
            capsule_text = (
                "Skill: Usage Summary\n"
                "When to use:\n"
                "- Verify current-use display for generated skills.\n"
                "Avoid when:\n"
                "- The skill is not self-proposed.\n"
                "Procedure:\n"
                "1. Enable the skill at group, actor, session, and autoload scopes.\n"
                "Pitfalls:\n"
                "- A settings UI must not confuse current state with next action.\n"
                "Verification:\n"
                "- Re-read capability_state.capability_usage."
            )
            import_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "user",
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Usage Summary",
                        "description_short": "usage summary validation",
                        "capsule_text": capsule_text,
                    },
                    "probe": False,
                },
            )
            self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

            profile_upsert, _ = self._call(
                "actor_profile_upsert",
                {
                    "by": "user",
                    "profile": {
                        "name": "Usage Summary Profile",
                        "runtime": "custom",
                        "runner": "headless",
                        "command": [],
                        "submit": "enter",
                        "capability_defaults": {
                            "autoload_capabilities": [capability_id],
                            "default_scope": "actor",
                        },
                    },
                },
            )
            self.assertTrue(profile_upsert.ok, getattr(profile_upsert, "error", None))
            profile = (profile_upsert.result or {}).get("profile") if isinstance(profile_upsert.result, dict) else {}
            profile_id = str(profile.get("id") or "").strip() if isinstance(profile, dict) else ""
            self.assertTrue(profile_id)
            profile_actor, _ = self._call(
                "actor_add",
                {
                    "group_id": gid,
                    "actor_id": "peer-3",
                    "runtime": "custom",
                    "runner": "headless",
                    "profile_id": profile_id,
                    "by": "user",
                },
            )
            self.assertTrue(profile_actor.ok, getattr(profile_actor, "error", None))

            for actor_id, scope in (("user", "group"), ("peer-1", "actor"), ("peer-2", "session")):
                enable_resp, _ = self._call(
                    "capability_enable",
                    {
                        "group_id": gid,
                        "by": "user",
                        "actor_id": actor_id,
                        "capability_id": capability_id,
                        "scope": scope,
                        "ttl_seconds": 3600,
                    },
                )
                self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))

            actor_update, _ = self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "user",
                    "patch": {"capability_autoload": [capability_id]},
                },
            )
            self.assertTrue(actor_update.ok, getattr(actor_update, "error", None))

            state_resp, _ = self._call(
                "capability_state",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "user",
                    "capability_id": capability_id,
                },
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state_result = state_resp.result if isinstance(state_resp.result, dict) else {}
            usage = state_result.get("capability_usage") if isinstance(state_result.get("capability_usage"), dict) else {}
            self.assertEqual(usage.get("capability_id"), capability_id)
            self.assertTrue(usage.get("used"))
            self.assertTrue(usage.get("group_enabled"))
            self.assertEqual(int(usage.get("group_actor_count") or 0), 3)
            self.assertEqual(int(usage.get("active_actor_count") or 0), 3)
            self.assertEqual(int(usage.get("startup_autoload_actor_count") or 0), 2)
            self.assertIn("peer-1", {str(item.get("actor_id") or "") for item in usage.get("actor_enabled") or []})
            self.assertIn("peer-2", {str(item.get("actor_id") or "") for item in usage.get("session_enabled") or []})
            self.assertIn("peer-1", {str(item.get("actor_id") or "") for item in usage.get("actor_autoload") or []})
            profile_autoload_rows = usage.get("profile_autoload") if isinstance(usage.get("profile_autoload"), list) else []
            profile_autoload_peer = next(
                (
                    item
                    for item in profile_autoload_rows
                    if isinstance(item, dict) and str(item.get("actor_id") or "") == "peer-3"
                ),
                {},
            )
            self.assertEqual(str(profile_autoload_peer.get("profile_id") or ""), profile_id)
            self.assertEqual(str(profile_autoload_peer.get("profile_name") or ""), "Usage Summary Profile")
            session_rows = usage.get("session_enabled") if isinstance(usage.get("session_enabled"), list) else []
            self.assertTrue(any(int(item.get("ttl_seconds") or 0) > 0 for item in session_rows if isinstance(item, dict)))

            peer_state_resp, _ = self._call(
                "capability_state",
                {
                    "group_id": gid,
                    "by": "peer-2",
                    "actor_id": "peer-2",
                },
            )
            self.assertTrue(peer_state_resp.ok, getattr(peer_state_resp, "error", None))
            peer_state = peer_state_resp.result if isinstance(peer_state_resp.result, dict) else {}
            active_rows = (
                peer_state.get("active_capsule_skills")
                if isinstance(peer_state.get("active_capsule_skills"), list)
                else []
            )
            active_row = next(
                (
                    item
                    for item in active_rows
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            activation_sources = (
                active_row.get("activation_sources")
                if isinstance(active_row.get("activation_sources"), list)
                else []
            )
            source_scopes = {str(item.get("scope") or "") for item in activation_sources if isinstance(item, dict)}
            self.assertEqual(source_scopes, {"group", "session"})
            session_sources = [
                item
                for item in activation_sources
                if isinstance(item, dict) and str(item.get("scope") or "") == "session"
            ]
            self.assertTrue(any(int(item.get("ttl_seconds") or 0) > 0 for item in session_sources))
        finally:
            cleanup()

    def test_self_proposed_actor_assignment_persists_autoload_and_can_activate_now(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            self._add_actor(gid, "peer-2", by="user")
            capability_id = "skill:agent_self_proposed:assignment-activate-now"
            capsule_text = (
                "Skill: Assignment Activate Now\n"
                "When to use:\n"
                "- Verify actor assignment writes startup autoload and supports immediate activation.\n"
                "Avoid when:\n"
                "- The skill should only be tried in a temporary session.\n"
                "Procedure:\n"
                "1. Persist actor capability_autoload, then enable actor scope for immediate use.\n"
                "Pitfalls:\n"
                "- Autoload metadata alone is not current runtime activation.\n"
                "Verification:\n"
                "- Re-read actor_autoload_capabilities and active_capsule_skills."
            )
            import_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "user",
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "source_id": "agent_self_proposed",
                        "name": "Assignment Activate Now",
                        "description_short": "assignment activation validation",
                        "capsule_text": capsule_text,
                    },
                    "probe": False,
                },
            )
            self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

            actor_update, _ = self._call(
                "actor_update",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "user",
                    "patch": {"capability_autoload": [capability_id]},
                },
            )
            self.assertTrue(actor_update.ok, getattr(actor_update, "error", None))

            before_enable, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(before_enable.ok, getattr(before_enable, "error", None))
            before_state = before_enable.result if isinstance(before_enable.result, dict) else {}
            self.assertIn(capability_id, before_state.get("actor_autoload_capabilities") or [])
            before_active = (
                before_state.get("active_capsule_skills")
                if isinstance(before_state.get("active_capsule_skills"), list)
                else []
            )
            before_active_ids = {
                str(item.get("capability_id") or "")
                for item in before_active
                if isinstance(item, dict)
            }
            self.assertNotIn(capability_id, before_active_ids)

            enable_resp, _ = self._call(
                "capability_enable",
                {
                    "group_id": gid,
                    "by": "user",
                    "actor_id": "peer-1",
                    "capability_id": capability_id,
                    "scope": "actor",
                    "enabled": True,
                    "reason": "web_self_proposed_actor_assignment",
                },
            )
            self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))

            after_enable, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(after_enable.ok, getattr(after_enable, "error", None))
            after_state = after_enable.result if isinstance(after_enable.result, dict) else {}
            self.assertIn(capability_id, after_state.get("actor_autoload_capabilities") or [])
            self.assertIn(capability_id, after_state.get("enabled_capabilities") or [])
            after_active = (
                after_state.get("active_capsule_skills")
                if isinstance(after_state.get("active_capsule_skills"), list)
                else []
            )
            after_active_ids = {
                str(item.get("capability_id") or "")
                for item in after_active
                if isinstance(item, dict)
            }
            self.assertIn(capability_id, after_active_ids)

            other_actor, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-2", "by": "peer-2"},
            )
            self.assertTrue(other_actor.ok, getattr(other_actor, "error", None))
            other_state = other_actor.result if isinstance(other_actor.result, dict) else {}
            self.assertNotIn(capability_id, other_state.get("enabled_capabilities") or [])
        finally:
            cleanup()

    def test_capability_import_agent_self_proposed_mcp_stays_indexed_by_default(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": "mcp:example/agent-proposed-tool",
                        "kind": "mcp_toolpack",
                        "source_id": "agent_self_proposed",
                        "install_mode": "command",
                        "install_spec": {
                            "command": ["uvx", "demo-mcp"],
                        },
                    },
                },
            )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            record = result.get("record") if isinstance(result.get("record"), dict) else {}
            self.assertEqual(str(record.get("source_id") or ""), "agent_self_proposed")
            self.assertEqual(str(result.get("effective_policy_level") or ""), "indexed")
            self.assertFalse(bool(result.get("enableable_now")))
            self.assertEqual(str(result.get("enable_block_reason") or ""), "policy_level_indexed")
            readiness = result.get("readiness_preview") if isinstance(result.get("readiness_preview"), dict) else {}
            self.assertEqual(str(readiness.get("preview_status") or ""), "blocked")
        finally:
            cleanup()

    def test_capability_import_dry_run_reports_policy_indexed_block(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            get_before, _ = self._call("capability_allowlist_get", {"by": "user"})
            self.assertTrue(get_before.ok, getattr(get_before, "error", None))
            before = get_before.result if isinstance(get_before.result, dict) else {}
            revision_before = str(before.get("revision") or "")
            self.assertTrue(revision_before)
            self.assertEqual(str(before.get("external_capability_safety_mode") or ""), "normal")

            update, _ = self._call(
                "capability_allowlist_update",
                {
                    "by": "user",
                    "mode": "patch",
                    "expected_revision": revision_before,
                    "patch": {
                        "defaults": {"source_level": {
                            "manual_import": "indexed",
                            "onecolleague_skill_library": "indexed",
                            "mcp_registry_official": "indexed",
                            "anthropic_skills": "indexed",
                            "github_skills_curated": "indexed",
                            "skillsmp_remote": "indexed",
                            "clawhub_remote": "indexed",
                            "openclaw_skills_remote": "indexed",
                            "clawskills_remote": "indexed",
                        }},
                    },
                },
            )
            self.assertTrue(update.ok, getattr(update, "error", None))
            update_result = update.result if isinstance(update.result, dict) else {}
            self.assertEqual(str(update_result.get("external_capability_safety_mode") or ""), "conservative")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": "mcp:example/manual-indexed",
                        "kind": "mcp_toolpack",
                        "install_mode": "command",
                        "install_spec": {
                            "command": ["uvx", "demo-mcp"],
                        },
                    },
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("effective_policy_level") or ""), "indexed")
            self.assertFalse(bool(result.get("enableable_now")))
            self.assertEqual(str(result.get("enable_block_reason") or ""), "policy_level_indexed")
            readiness = result.get("readiness_preview") if isinstance(result.get("readiness_preview"), dict) else {}
            self.assertEqual(str(readiness.get("policy_source") or ""), "external_capability_safety_mode")
            self.assertEqual(str(readiness.get("policy_mode") or ""), "conservative")
        finally:
            cleanup()

    def test_capability_import_copies_fallback_command_shortcuts_into_install_spec(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "mcp:example/import-fallback"
            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": capability_id,
                        "kind": "mcp_toolpack",
                        "install_mode": "package",
                        "install_spec": {
                            "registry_type": "npm",
                            "identifier": "@example/mcp-server",
                        },
                        "fallback_command": ["npx", "-y", "@example/mcp-server"],
                        "fallback_command_candidates": [["npx", "-y", "@example/mcp-server"]],
                    },
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            record = result.get("record") if isinstance(result.get("record"), dict) else {}
            install_spec = record.get("install_spec") if isinstance(record.get("install_spec"), dict) else {}
            self.assertEqual(install_spec.get("fallback_command"), ["npx", "-y", "@example/mcp-server"])
            self.assertEqual(
                install_spec.get("fallback_command_candidates"),
                [["npx", "-y", "@example/mcp-server"]],
            )
        finally:
            cleanup()

    def test_capability_import_mcp_persists_record_and_enables(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            self._write_allowlist_override(mcp_registry_level="mounted")
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "mcp:example/echo-server"

            install_payload = {
                "state": "installed",
                "installer": "remote_http",
                "install_mode": "remote_only",
                "invoker": {"type": "remote_http", "url": "http://127.0.0.1:9900/mcp"},
                "tools": [
                    {
                        "name": "onecolleague_ext_deadbeef_echo",
                        "real_tool_name": "echo",
                        "description": "Echo tool",
                        "inputSchema": {"type": "object", "properties": {}, "required": []},
                    }
                ],
                "updated_at": "2026-03-01T00:00:00Z",
            }
            with patch(
                "no1.daemon.ops.capability_ops._install_external_capability",
                return_value=install_payload,
            ):
                resp, _ = self._call(
                    "capability_import",
                    {
                        "group_id": gid,
                        "by": "peer-1",
                        "actor_id": "peer-1",
                        "enable_after_import": True,
                        "scope": "session",
                        "record": {
                            "capability_id": capability_id,
                            "kind": "mcp_toolpack",
                            "name": "Echo Server",
                            "description_short": "Imported echo MCP",
                            "source_id": "mcp_registry_official",
                            "install_mode": "remote_only",
                            "install_spec": {
                                "transport": "http",
                                "url": "http://127.0.0.1:9900/mcp",
                            },
                        },
                    },
                )

            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertTrue(bool(result.get("imported")))
            self.assertEqual(str(result.get("state") or ""), "activation_pending")
            self.assertEqual(str(result.get("capability_id") or ""), capability_id)
            enable_result = result.get("enable_result") if isinstance(result.get("enable_result"), dict) else {}
            self.assertEqual(str(enable_result.get("state") or ""), "activation_pending")
            self.assertTrue(bool(enable_result.get("refresh_required")))

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            enabled = set(state.get("enabled_capabilities") or [])
            self.assertIn(capability_id, enabled)

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            self.assertIn(capability_id, rows)
        finally:
            cleanup()

    def test_capability_import_skill_persists_and_enables(self) -> None:
        from no1.daemon.ops import capability_ops as ops

        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:github:demo:triage"

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "enable_after_import": True,
                    "scope": "actor",
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "name": "Demo Triage",
                        "description_short": "Imported skill capsule",
                        "source_id": "github_skills_curated",
                        "capsule_text": "Use triage checklist",
                        "requires_capabilities": ["pack:group-runtime"],
                        "use_when": ["incident triage and debugging work"],
                        "avoid_when": ["greenfield feature implementation"],
                        "gotchas": ["capture concrete failing evidence before routing work"],
                        "evidence_kind": "error log plus minimal repro note",
                    },
                },
            )
            self.assertTrue(resp.ok, getattr(resp, "error", None))
            result = resp.result if isinstance(resp.result, dict) else {}
            self.assertEqual(str(result.get("state") or ""), "activation_pending")
            self.assertEqual(str(result.get("scope") or ""), "actor")
            enable_result = result.get("enable_result") if isinstance(result.get("enable_result"), dict) else {}
            self.assertEqual(str(enable_result.get("scope") or ""), "actor")
            skill = enable_result.get("skill") if isinstance(enable_result.get("skill"), dict) else {}
            self.assertEqual(str(skill.get("capability_id") or ""), capability_id)

            state_resp, _ = self._call(
                "capability_state",
                {"group_id": gid, "actor_id": "peer-1", "by": "peer-1"},
            )
            self.assertTrue(state_resp.ok, getattr(state_resp, "error", None))
            state = state_resp.result if isinstance(state_resp.result, dict) else {}
            enabled = set(state.get("enabled_capabilities") or [])
            self.assertIn(capability_id, enabled)
            active_capsule_skills = state.get("active_capsule_skills") if isinstance(state.get("active_capsule_skills"), list) else []
            active_ids = {str(item.get("capability_id") or "") for item in active_capsule_skills if isinstance(item, dict)}
            self.assertIn(capability_id, active_ids)
            active_row = next(
                (
                    item
                    for item in active_capsule_skills
                    if isinstance(item, dict) and str(item.get("capability_id") or "") == capability_id
                ),
                {},
            )
            activation_sources = (
                active_row.get("activation_sources")
                if isinstance(active_row.get("activation_sources"), list)
                else []
            )
            self.assertEqual({str(item.get("scope") or "") for item in activation_sources if isinstance(item, dict)}, {"actor"})

            _, catalog_doc = ops._load_catalog_doc()
            rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
            self.assertIn(capability_id, rows)
            record = rows.get(capability_id) if isinstance(rows.get(capability_id), dict) else {}
            self.assertEqual(record.get("use_when"), ["incident triage and debugging work"])
            self.assertEqual(record.get("avoid_when"), ["greenfield feature implementation"])
            self.assertEqual(record.get("gotchas"), ["capture concrete failing evidence before routing work"])
            self.assertEqual(str(record.get("evidence_kind") or ""), "error log plus minimal repro note")
        finally:
            cleanup()

    def test_capability_overview_search_matches_recommendation_fields(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:github:demo:triage"

            import_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "name": "Demo Triage",
                        "description_short": "Imported skill capsule",
                        "source_id": "github_skills_curated",
                        "capsule_text": "Use triage checklist",
                        "use_when": ["incident triage and debugging work"],
                        "avoid_when": ["greenfield feature implementation"],
                        "gotchas": ["capture concrete failing evidence before routing work"],
                        "evidence_kind": "error log plus minimal repro note",
                    },
                },
            )
            self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

            secondary_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "record": {
                        "capability_id": "skill:github:demo:minimal-checklist",
                        "kind": "skill",
                        "name": "Minimal Checklist",
                        "description_short": "Minimal checklist",
                        "source_id": "github_skills_curated",
                        "capsule_text": "Use a short checklist",
                    },
                },
            )
            self.assertTrue(secondary_resp.ok, getattr(secondary_resp, "error", None))

            overview_resp, _ = self._call(
                "capability_overview",
                {
                    "query": "minimal repro note",
                    "limit": 50,
                    "include_indexed": True,
                    "source_id": "github_skills_curated",
                },
            )
            self.assertTrue(overview_resp.ok, getattr(overview_resp, "error", None))
            result = overview_resp.result if isinstance(overview_resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            self.assertEqual(str((items[0] if items else {}).get("capability_id") or ""), capability_id)
            row = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and str(item.get("capability_id") or "") == capability_id
                ),
                None,
            )
            self.assertIsNotNone(row)
            item = row if isinstance(row, dict) else {}
            self.assertEqual(item.get("use_when"), ["incident triage and debugging work"])
            self.assertEqual(item.get("avoid_when"), ["greenfield feature implementation"])
            self.assertEqual(item.get("gotchas"), ["capture concrete failing evidence before routing work"])
            self.assertEqual(str(item.get("evidence_kind") or ""), "error log plus minimal repro note")

            paged_resp, _ = self._call(
                "capability_overview",
                {
                    "query": "",
                    "limit": 1,
                    "offset": 0,
                    "include_indexed": True,
                    "kind": "skill",
                    "source_id": "github_skills_curated",
                },
            )
            self.assertTrue(paged_resp.ok, getattr(paged_resp, "error", None))
            paged_result = paged_resp.result if isinstance(paged_resp.result, dict) else {}
            paged_items = paged_result.get("items") if isinstance(paged_result.get("items"), list) else []
            self.assertEqual(len(paged_items), 1)
            self.assertGreaterEqual(int(paged_result.get("total_count") or 0), 1)
            self.assertIn("has_more", paged_result)
            only_item = paged_items[0] if paged_items and isinstance(paged_items[0], dict) else {}
            self.assertEqual(str(only_item.get("kind") or ""), "skill")
            self.assertEqual(str(only_item.get("source_id") or ""), "github_skills_curated")
        finally:
            cleanup()

    def test_capability_search_matches_recommendation_fields(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")
            capability_id = "skill:github:demo:triage"

            import_resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "record": {
                        "capability_id": capability_id,
                        "kind": "skill",
                        "name": "Demo Triage",
                        "description_short": "Imported skill capsule",
                        "source_id": "github_skills_curated",
                        "capsule_text": "Use triage checklist",
                        "use_when": ["incident triage and debugging work"],
                        "gotchas": ["capture concrete failing evidence before routing work"],
                        "evidence_kind": "error log plus minimal repro note",
                    },
                },
            )
            self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

            search_resp, _ = self._call(
                "capability_search",
                {
                    "group_id": gid,
                    "actor_id": "peer-1",
                    "by": "peer-1",
                    "query": "failing evidence",
                    "kind": "skill",
                    "include_external": True,
                    "limit": 20,
                },
            )
            self.assertTrue(search_resp.ok, getattr(search_resp, "error", None))
            result = search_resp.result if isinstance(search_resp.result, dict) else {}
            items = result.get("items") if isinstance(result.get("items"), list) else []
            row = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and str(item.get("capability_id") or "") == capability_id
                ),
                None,
            )
            self.assertIsNotNone(row)
            item = row if isinstance(row, dict) else {}
            self.assertEqual(item.get("gotchas"), ["capture concrete failing evidence before routing work"])
            self.assertEqual(str(item.get("evidence_kind") or ""), "error log plus minimal repro note")
        finally:
            cleanup()

    def test_capability_import_invalid_record_returns_validation_error(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "record": {"kind": "skill"},
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual((resp.error.code if resp.error else ""), "capability_import_invalid")
        finally:
            cleanup()

    def test_capability_import_command_mode_rejects_empty_command_shortcut(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._add_actor(gid, "peer-1", by="user")

            resp, _ = self._call(
                "capability_import",
                {
                    "group_id": gid,
                    "by": "peer-1",
                    "actor_id": "peer-1",
                    "dry_run": True,
                    "probe": False,
                    "record": {
                        "capability_id": "mcp:example/empty-command",
                        "kind": "mcp_toolpack",
                        "install_mode": "command",
                        "install_spec": {},
                        "command": [],
                    },
                },
            )
            self.assertFalse(resp.ok)
            self.assertEqual((resp.error.code if resp.error else ""), "capability_import_invalid")
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
