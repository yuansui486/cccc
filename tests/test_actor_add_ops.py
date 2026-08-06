from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from no1.daemon.actors.actor_add_ops import handle_actor_add


def _handle(
    args: dict,
    *,
    update_actor_private_env: Mock | None = None,
    delete_actor_private_env: Mock | None = None,
    load_actor_profile_secrets: Mock | None = None,
    get_actor_profile: Mock | None = None,
):
    update_private = update_actor_private_env or Mock(return_value={})
    delete_private = delete_actor_private_env or Mock()
    load_profile_secrets = load_actor_profile_secrets or Mock(return_value={})
    response = handle_actor_add(
        args,
        foreman_id=lambda _group: "",
        maybe_reset_automation_on_foreman_change=Mock(),
        start_actor_process=Mock(),
        effective_runner_kind=lambda runner: runner,
        validate_private_env_key=lambda key: str(key),
        coerce_private_env_value=lambda value: str(value),
        update_actor_private_env=update_private,
        delete_actor_private_env=delete_private,
        load_actor_private_env=Mock(return_value={}),
        private_env_max_keys=32,
        supported_runtimes=("codex", "web_model"),
        get_actor_profile=get_actor_profile or Mock(return_value=None),
        load_actor_profile_secrets=load_profile_secrets,
    )
    return response, update_private, delete_private


def test_profile_initialization_failure_rolls_back_created_actor_and_private_env() -> None:
    group = SimpleNamespace(group_id="g-test", ledger_path="ledger.jsonl")
    actor = {"id": "peer1", "runtime": "codex", "runner": "headless", "command": []}
    delete_private = Mock()

    with patch("no1.daemon.actors.actor_add_ops.load_group", return_value=group), patch(
        "no1.daemon.actors.actor_add_ops.require_actor_permission"
    ), patch("no1.daemon.actors.actor_add_ops.add_actor", return_value=actor), patch(
        "no1.daemon.actors.actor_add_ops.remove_actor"
    ) as remove, patch(
        "no1.daemon.actors.actor_add_ops.apply_profile_link_to_actor",
        side_effect=RuntimeError("profile secret update failed"),
    ), patch("no1.daemon.actors.actor_add_ops.append_event") as append_event:
        response, _update_private, _delete_private = _handle(
            {
                "group_id": "g-test",
                "actor_id": "peer1",
                "runtime": "codex",
                "runner": "headless",
                "profile_id": "profile-1",
                "by": "user",
            },
            delete_actor_private_env=delete_private,
            load_actor_profile_secrets=Mock(return_value={"ONECOLLEAGUE_API_KEY": "secret"}),
            get_actor_profile=Mock(
                return_value={
                    "id": "profile-1",
                    "runtime": "codex",
                    "runner": "headless",
                    "command": [],
                    "submit": "enter",
                }
            ),
        )

    assert not response.ok
    assert response.error is not None
    assert response.error.code == "actor_add_failed"
    remove.assert_called_once_with(group, "peer1")
    delete_private.assert_called_once_with("g-test", "peer1")
    append_event.assert_not_called()


def test_foreman_private_env_copy_failure_rolls_back_created_actor() -> None:
    group = SimpleNamespace(group_id="g-test", ledger_path="ledger.jsonl")
    foreman = {
        "id": "lead",
        "runtime": "codex",
        "runner": "headless",
        "command": ["codex"],
        "env": {"PUBLIC": "1"},
    }
    actor = {"id": "peer1", "runtime": "codex", "runner": "headless", "command": ["codex"]}
    update_private = Mock(side_effect=RuntimeError("private env write failed"))
    delete_private = Mock()

    with patch("no1.daemon.actors.actor_add_ops.load_group", return_value=group), patch(
        "no1.daemon.actors.actor_add_ops.require_actor_permission"
    ), patch("no1.daemon.actors.actor_add_ops.get_effective_role", return_value="foreman"), patch(
        "no1.daemon.actors.actor_add_ops.find_actor", return_value=foreman
    ), patch("no1.daemon.actors.actor_add_ops.actor_profile_ref", return_value=None), patch(
        "no1.daemon.actors.actor_add_ops.resolve_actor_launch_config",
        return_value={
            "runtime": "codex",
            "runner": "headless",
            "effective_runner": "headless",
            "command": ["codex"],
            "public_env": {"PUBLIC": "1"},
            "private_env": {"ONECOLLEAGUE_API_KEY": "secret"},
        },
    ), patch("no1.daemon.actors.actor_add_ops.add_actor", return_value=actor), patch(
        "no1.daemon.actors.actor_add_ops.remove_actor"
    ) as remove, patch("no1.daemon.actors.actor_add_ops.append_event") as append_event:
        response, _update_private, _delete_private = _handle(
            {
                "group_id": "g-test",
                "actor_id": "peer1",
                "runtime": "codex",
                "runner": "headless",
                "by": "lead",
            },
            update_actor_private_env=update_private,
            delete_actor_private_env=delete_private,
        )

    assert not response.ok
    remove.assert_called_once_with(group, "peer1")
    delete_private.assert_called_once_with("g-test", "peer1")
    append_event.assert_not_called()


def test_direct_private_env_failure_clears_web_model_marker_and_rolls_back() -> None:
    group = SimpleNamespace(group_id="g-test", ledger_path="ledger.jsonl")
    actor = {"id": "webpeer", "runtime": "web_model", "runner": "headless", "command": []}
    update_private = Mock(side_effect=RuntimeError("private env write failed"))
    delete_private = Mock()

    with patch("no1.daemon.actors.actor_add_ops.load_group", return_value=group), patch(
        "no1.daemon.actors.actor_add_ops.require_actor_permission"
    ), patch("no1.daemon.actors.actor_add_ops.require_no_other_chatgpt_web_model_actor"), patch(
        "no1.daemon.actors.actor_add_ops.add_actor", return_value=actor
    ), patch("no1.daemon.actors.actor_add_ops.remove_actor") as remove, patch(
        "no1.daemon.actors.actor_add_ops.clear_web_model_chatgpt_browser_actor_runtime"
    ) as clear_runtime, patch("no1.daemon.actors.actor_add_ops.append_event") as append_event:
        response, _update_private, _delete_private = _handle(
            {
                "group_id": "g-test",
                "actor_id": "webpeer",
                "runtime": "web_model",
                "runner": "headless",
                "env_private": {"TOKEN": "secret"},
                "by": "user",
            },
            update_actor_private_env=update_private,
            delete_actor_private_env=delete_private,
        )

    assert not response.ok
    assert response.error is not None
    assert response.error.message == "failed to store env_private"
    remove.assert_called_once_with(group, "webpeer")
    delete_private.assert_called_once_with("g-test", "webpeer")
    assert clear_runtime.call_count == 2
    clear_runtime.assert_called_with(group_id="g-test", actor_id="webpeer")
    append_event.assert_not_called()
