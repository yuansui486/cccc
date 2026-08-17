from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from no1.contracts.v1 import DaemonResponse
from no1.daemon.messaging.chat_ops import _prepare_message_skill


def test_prepare_message_skill_projects_stable_openclaw_name() -> None:
    capability_id = "skill:onecolleague_skill_library:copywriting"
    stable_name = f"onecolleague-{hashlib.sha256(capability_id.encode('utf-8')).hexdigest()[:16]}"
    enabled = DaemonResponse(ok=True, result={"enabled": True, "state": "activation_pending"})

    with patch("no1.daemon.messaging.chat_ops.find_actor", return_value={"runtime": "openclaw"}), patch(
        "no1.daemon.ops.capability_ops._canonical_capability_id", return_value=capability_id
    ), patch(
        "no1.daemon.ops.capability_ops._admission.resolve_current_admission",
        return_value={"activation_sources": {}},
    ), patch(
        "no1.daemon.ops.capability_ops.handle_capability_enable", return_value=enabled
    ) as enable, patch(
        "no1.daemon.ops.capability_ops.prepare_openclaw_skill_package_overlay_for_actor",
        return_value={"selected_names": [stable_name]},
    ) as project:
        name, error = _prepare_message_skill(
            SimpleNamespace(group_id="g-test"),
            capability_id=capability_id,
            recipient_ids=["openclaw-1"],
        )

    assert error is None
    assert name == stable_name
    assert enable.call_args.args[0]["scope"] == "session"
    assert enable.call_args.args[0]["ttl_seconds"] == 3600
    project.assert_called_once()


def test_prepare_message_skill_rolls_back_new_session_on_projection_failure() -> None:
    capability_id = "skill:onecolleague_skill_library:copywriting"
    enabled = DaemonResponse(ok=True, result={"enabled": True, "state": "activation_pending"})
    disabled = DaemonResponse(ok=True, result={"enabled": False, "state": "disabled"})

    with patch("no1.daemon.messaging.chat_ops.find_actor", return_value={"runtime": "openclaw"}), patch(
        "no1.daemon.ops.capability_ops._canonical_capability_id", return_value=capability_id
    ), patch(
        "no1.daemon.ops.capability_ops._admission.resolve_current_admission",
        return_value={"activation_sources": {}},
    ), patch(
        "no1.daemon.ops.capability_ops.handle_capability_enable",
        side_effect=[enabled, disabled],
    ) as enable, patch(
        "no1.daemon.ops.capability_ops.prepare_openclaw_skill_package_overlay_for_actor",
        return_value={"selected_names": []},
    ):
        name, error = _prepare_message_skill(
            SimpleNamespace(group_id="g-test"),
            capability_id=capability_id,
            recipient_ids=["openclaw-1"],
        )

    assert not name
    assert error is not None
    assert error.error is not None
    assert error.error.code == "skill_unavailable"
    assert enable.call_count == 2
    assert enable.call_args_list[1].args[0]["enabled"] is False

