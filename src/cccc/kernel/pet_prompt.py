from __future__ import annotations

from typing import Any, Dict

from .context import ContextStorage
from .group import Group
from .pet_actor import PET_ACTOR_ID
from .pet_profile import build_pet_profile
from .pet_signals import build_pet_signal_summary_lines, load_pet_signals
from .pet_task_triage import build_task_triage_payload, join_task_briefs
from .prompt_files import HELP_FILENAME, load_builtin_help_markdown, read_group_prompt_file
from ..ports.mcp.utils.help_markdown import parse_help_markdown


def load_pet_help_markdown(group: Group) -> str:
    pf = read_group_prompt_file(group, HELP_FILENAME)
    if pf.found and isinstance(pf.content, str) and pf.content.strip():
        return str(pf.content)
    return str(load_builtin_help_markdown() or "")


def build_pet_snapshot_text(group: Any, context_payload: Dict[str, Any]) -> str:
    parts: list[str] = []
    title = str(group.doc.get("title") or group.group_id or "").strip() or "unknown-group"
    state = str(group.doc.get("state") or "").strip() or "unknown"
    parts.append(f"Group: {title}")
    parts.append(f"Group State: {state}")

    tasks_summary = context_payload.get("tasks_summary") if isinstance(context_payload.get("tasks_summary"), dict) else {}
    if tasks_summary:
        parts.append(
            "Tasks: total={total}, active={active}, done={done}, archived={archived}".format(
                total=int(tasks_summary.get("total") or 0),
                active=int(tasks_summary.get("active") or 0),
                done=int(tasks_summary.get("done") or 0),
                archived=int(tasks_summary.get("archived") or 0),
            )
        )

    agent_states = context_payload.get("agent_states") if isinstance(context_payload.get("agent_states"), list) else []
    snapshots: list[str] = []
    for item in agent_states[:6]:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "").strip()
        hot = item.get("hot") if isinstance(item.get("hot"), dict) else {}
        active_task_id = str(hot.get("active_task_id") or "").strip()
        focus = str(hot.get("focus") or "").strip()
        if not agent_id:
            continue
        if active_task_id and focus:
            snapshots.append(f"{agent_id}: {active_task_id} | {focus}")
        elif active_task_id:
            snapshots.append(f"{agent_id}: {active_task_id}")
        elif focus:
            snapshots.append(f"{agent_id}: {focus}")
        else:
            snapshots.append(agent_id)
    if snapshots:
        parts.append(f"Agent Snapshot: {' ; '.join(snapshots)}")

    blocked_tasks = context_payload.get("blocked_tasks") if isinstance(context_payload.get("blocked_tasks"), list) else []
    if blocked_tasks:
        rendered = join_task_briefs(blocked_tasks)
        if rendered:
            parts.append(f"Blocked Tasks: {rendered}")

    waiting_user_tasks = (
        context_payload.get("waiting_user_tasks")
        if isinstance(context_payload.get("waiting_user_tasks"), list)
        else []
    )
    if waiting_user_tasks:
        rendered = join_task_briefs(waiting_user_tasks)
        if rendered:
            parts.append(f"Waiting User Tasks: {rendered}")

    handoff_tasks = context_payload.get("handoff_tasks") if isinstance(context_payload.get("handoff_tasks"), list) else []
    if handoff_tasks:
        rendered = join_task_briefs(handoff_tasks)
        if rendered:
            parts.append(f"Handoff Tasks: {rendered}")

    planned_backlog_tasks = (
        context_payload.get("planned_backlog_tasks")
        if isinstance(context_payload.get("planned_backlog_tasks"), list)
        else []
    )
    if planned_backlog_tasks:
        rendered = join_task_briefs(planned_backlog_tasks)
        if rendered:
            parts.append(f"Planned Backlog: {rendered}")

    signal_payload = context_payload.get("pet_signals") if isinstance(context_payload.get("pet_signals"), dict) else None
    if signal_payload is None:
        signal_payload = load_pet_signals(group, context_payload=context_payload)
    parts.extend(build_pet_signal_summary_lines(signal_payload))

    return "\n".join(parts)


def build_pet_prompt_parts(
    group: Group,
    *,
    help_markdown: str,
    context_payload: Dict[str, Any],
    include_snapshot: bool = True,
) -> Dict[str, Any]:
    parsed = parse_help_markdown(help_markdown)
    persona = str(parsed.get("pet") or "").strip()
    profile = build_pet_profile(group, persona=persona)
    snapshot = build_pet_snapshot_text(group, context_payload) if include_snapshot else ""
    title = str(group.doc.get("title") or group.group_id or "").strip() or "unknown-group"
    persona_contract = "\n".join(
        [
            "Pet Persona Contract:",
            f"- Stable companion identity: {profile['name']} is a {profile['species']} companion.",
            f"- Identity: {profile['identity']}",
            f"- Temperament: {profile['temperament']}",
            f"- Speech style: {profile['speech_style']}",
            f"- Care style: {profile['care_style']}",
            "- Keep continuity stronger than novelty. Sound like the same nearby companion across sessions.",
            "- Be warm and observant, but never so theatrical that the next step becomes blurry.",
        ]
    )
    wording_contract = "\n".join(
        [
            "Pet Wording Contract:",
            "- Prefer one short observation plus one direct next step.",
            "- Sound like a companion beside the user, not a dashboard reading metrics aloud.",
            "- Avoid internal telemetry labels, raw status bundles, and board-state dumps in user-facing text.",
            "- Default to concise, lightly human wording. Expand only when needed to make the reminder actionable.",
            "- Do not sound like a second foreman. Nudge clearly, then get out of the way.",
        ]
    )
    decision_contract = "\n".join(
        [
            "Pet Contract:",
            "- pet_review: surface exactly one current highest-value recommendation, or clear when nothing clearly beats interruption cost.",
            "- During pet_review, work from current live state. Do not rely on stale startup text.",
            "- For pet_review, first inspect the latest unread notify with data.context.kind=pet_review and use its review_packet as your initial focus. Widen only if the packet is insufficient.",
            "- When review_packet.task_evidence or pet-context.task_evidence is present, treat each item as a hypothesis packet built from code-side evidence. Re-rank it against live state; do not mirror it mechanically.",
            "- Finish every pet_review with exactly one cccc_pet_decisions call: action=replace with the full current decision list, or action=clear.",
            "- Default to draft_message. Valid surfaced actions are draft_message, task_proposal, and restart_actor.",
            "- Do not call cccc_message_send, cccc_message_reply, or visible file-send tools from Pet; surface drafts through pet decisions instead.",
            "- When a surfaced decision is non-trivial, include confidence=low|medium|high and a short reasoning_brief grounded in current evidence.",
            "- When drafting, action.text must already be the exact message the user would likely want to send next.",
            "- A draft_message may be multi-sentence or a short bullet list when one control move needs structure.",
            "- Set action.to and action.reply_to when the routing is clear.",
            "- Do not paste internal telemetry labels, field names, slash-delimited status bundles, or board-state dumps into action.text.",
            "- Use task_proposal when board cleanup is the right abstraction; propose, do not broadly mutate shared tasks yourself.",
            "- user_model shapes wording only. It must not affect severity or ranking.",
            "- pet_profile_refresh is separate: if an unread notify has data.context.kind=pet_profile_refresh, update only cccc_agent_state(action=update, actor_id=pet-peer, user_model=...) from its sample_packet and do not touch cccc_pet_decisions.",
        ]
    )
    header_lines = [
        f"[CCCC PET] You are {PET_ACTOR_ID} in group '{title}'",
        f"group_id: {group.group_id}",
        "role: user-side draft-first attention assistant",
    ]
    sections = ["\n".join(header_lines).strip()]
    if persona:
        sections.append("Pet Persona:\n" + persona)
    sections.append(persona_contract)
    sections.append(wording_contract)
    sections.append(decision_contract)
    prompt = "\n\n".join(sections).strip()
    return {
        "persona": persona,
        "help": "Pet Persona:\n" + persona if persona else "",
        "snapshot": snapshot,
        "profile": profile,
        "prompt": prompt,
        "source": "help" if persona else "default",
    }


def _load_pet_runtime_context(group: Group) -> Dict[str, Any]:
    try:
        storage = ContextStorage(group)
        tasks = storage.list_tasks()
        agents = storage.load_agents()
    except Exception:
        return {}

    active_count = 0
    done_count = 0
    archived_count = 0
    for task in tasks:
        status = str(getattr(getattr(task, "status", None), "value", getattr(task, "status", "")) or "").strip().lower()
        if status in {"done", "completed"}:
            done_count += 1
        elif status == "archived":
            archived_count += 1
        else:
            active_count += 1
    task_triage = build_task_triage_payload(tasks, limit=3)
    agent_states = []
    for agent in list(getattr(agents, "agents", []) or []):
        agent_states.append(
            {
                "id": str(getattr(agent, "id", "") or "").strip(),
                "hot": {
                    "active_task_id": str(getattr(getattr(agent, "hot", None), "active_task_id", "") or "").strip(),
                    "focus": str(getattr(getattr(agent, "hot", None), "focus", "") or "").strip(),
                },
            }
        )

    signal_payload = load_pet_signals(group, context_payload=task_triage)
    return {
        "tasks_summary": {
            "total": len(tasks),
            "active": active_count,
            "done": done_count,
            "archived": archived_count,
        },
        "agent_states": agent_states,
        "pet_signals": signal_payload,
        **task_triage,
    }


def render_pet_system_prompt(group: Group, *, actor: Dict[str, Any], context_payload: Dict[str, Any] | None = None) -> str:
    help_markdown = load_pet_help_markdown(group)
    effective_context_payload = context_payload if isinstance(context_payload, dict) else _load_pet_runtime_context(group)
    parts = build_pet_prompt_parts(
        group,
        help_markdown=help_markdown,
        context_payload=effective_context_payload,
        include_snapshot=False,
    )
    return str(parts.get("prompt") or "").strip() + "\n"
