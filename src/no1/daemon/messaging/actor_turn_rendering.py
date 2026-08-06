"""Shared actor-facing turn rendering helpers.

The daemon has multiple transports for delivering ledger events to actors
(PTY, headless app sessions, browser-delivered web models).  This module keeps
the actor-facing text shape shared so transport adapters only add transport
instructions, not their own message grammar.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from ...kernel.peer_insight import PEER_PERSPECTIVE_AGENT_LABEL, append_peer_perspective

from .inbound_rendering import ActorInboundEnvelope, render_actor_inbound_message


COLLABORATION_REQUIRED_INSTRUCTIONS = (
    "[onecolleague] 需协作：这条消息必须协作完成，不允许自己独立完成任务。所有过程输出和最终回复都必须使用中文。\n"
    "- 负责人首先拆分任务，定好任务拆分标准，并明确告诉执行成员必须按照该标准完成拆分。\n"
    "- 拆分任务时，必须告知所有必要前提条件和上下文，不能因为任务被简化而造成上下文不统一。\n"
    "- 执行标准必须包含硬性的结果标准，并且必须可量化。\n"
    "- 负责人必须制定任务验收规则，验收规则必须细化硬性标准和规范，禁止空泛验收。\n"
    "- 如果验收不通过，必须给出具体修改建议并反馈给负责人。\n"
    "- 成员回复交付后，第一件事是检查执行中和规划中的任务是否都已经完成。\n"
    "- 如果执行中和规划中的任务都完成了，但最终用户目标还没有完成，必须继续拆分后续任务。\n"
    "- 不允许出现最终目标未完成但成员空等待的情况。"
)


def render_computer_control_contract(request: dict[str, Any]) -> str:
    request_id = compact_delivery_text(request.get("request_id"), limit=64)
    mode = str(request.get("mode") or "create_and_run").strip()
    workflow_id = compact_delivery_text(request.get("workflow_id"), limit=100)
    actor_id = compact_delivery_text(request.get("actor_id"), limit=100)
    lines = [
        f"[onecolleague] 电脑控制执行契约（request_id={request_id}）：",
        f"- 模式：{'运行已有工作流' if mode == 'run_existing' else '根据需求新建工作流并运行'}",
        f"- 执行智能体：{actor_id}",
    ]
    if workflow_id:
        lines.append(f"- 工作流：{workflow_id}")
    if request.get("allow_high_risk") is True:
        lines.append("- 本次请求已授予完整电脑权限，可使用 PowerShell、文件写删、进程和注册表等高风险能力，无需逐项确认。")
    elevated = []
    if request.get("allow_publish") is True:
        elevated.append("发布版本")
    if request.get("allow_trust") is True:
        elevated.append("授予长期信任")
    if request.get("allow_unattended_triggers") is True:
        elevated.append("开启无人值守触发")
    if elevated:
        lines.append(f"- 用户额外授权：{'、'.join(elevated)}。只能用于本请求创建或指定的工作流。")
    lines.extend([
        "- 电脑控制工具可能不会直接出现在 tools/list；必须通过 onecolleague_capability_use 调用，capability_id 固定为 pack:computer-control-local。",
        "- 将本轮收到的 turn_grant_receipt 对象原样放入内层 tool_arguments；禁止放在 onecolleague_capability_use 顶层，也不要只传 authorization_secret。",
        '- catalog：onecolleague_capability_use(capability_id="pack:computer-control-local", tool_name="onecolleague_computer_control_catalog", tool_arguments={"turn_grant_receipt": <当前 turn_grant_receipt 对象>})',
        '- recording：onecolleague_capability_use(capability_id="pack:computer-control-local", tool_name="onecolleague_computer_recording", tool_arguments={"action": "start", "request_id": "<request_id>", "turn_grant_receipt": <当前 turn_grant_receipt 对象>})',
        '- workflow：onecolleague_capability_use(capability_id="pack:computer-control-local", tool_name="onecolleague_computer_workflow", tool_arguments={"action": "get", "workflow_id": "<workflow_id>", "turn_grant_receipt": <当前 turn_grant_receipt 对象>})',
        '- run：onecolleague_capability_use(capability_id="pack:computer-control-local", tool_name="onecolleague_computer_run", tool_arguments={"action": "start", "workflow_id": "<workflow_id>", "request_id": "<request_id>", "turn_grant_receipt": <当前 turn_grant_receipt 对象>})',
        "- 先分析观察点、动作、副作用和成功信号，再通过上述 catalog wrapper 读取精简目录；需要参数时在其 tool_arguments 中增加 tool 查询完整 schema。",
        "- 新建模式必须先通过上述 recording wrapper 执行 action=\"start\"，再用 action=\"call\"、record=false 观察桌面。后续每次调用仍须在内层携带当前 turn_grant_receipt。",
        "- 每次只执行一个动作；成功后立即录入，失败不入稿，并再次观察确认后再推进。Click/Type 必须先 Snapshot 解析唯一 UI 元素并确认前台窗口，禁止直接猜坐标。",
        "- 元素暂时无法解析时，只允许在重新观察并校验窗口后使用一次受控位置锚点；该步骤标记低稳定性，不能把坐标当作长期定位器。",
        "- 优先使用 App、Process、Screenshot/Snapshot、Click、Type；仅在原生工具已证实不足时使用 PowerShell。",
        "- 所有正式观察和动作都必须经过 OneColleague 电脑控制工具；禁止用裸 windows-mcp.* 绕过录制、租约或审计。",
        "- 将用于确认最终结果的 Snapshot 以 record=true 录为最后一个动作，确保完整回放产生可验证截图。",
        "- 探路完成后通过 recording wrapper 执行 action=\"commit\" 一次生成永久工作流，再通过 run wrapper 完整回放。",
        "- 回放到 awaiting_verification 后检查最终证据，并通过 run wrapper 调用 action=\"verify\"；验证通过后系统按授权自动发布和信任。",
        "- Windows-MCP 传输故障或 outcome_unknown 是基础设施状态：不得修改业务参数盲目重试，也不得改用裸工具执行有副作用动作。",
        "- 新建模式未完成 commit、完整回放和 verify 时，不得将任务标记 done；基础设施故障应保持任务未完成、waiting_on=external，并明确报告阻塞。",
        "- 遇到需要用户批准的高风险操作时停止调用并说明待批准内容。",
        "- 用户可见回复使用中文，说明录制步骤数、回放和验证结果或明确阻塞原因。",
    ])
    return "\n".join(lines)


def compact_delivery_text(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _presentation_slot_label(slot_id: str, label: str) -> str:
    if label:
        return label
    match = re.search(r"(\d+)$", slot_id)
    if match:
        try:
            return f"P{int(match.group(1))}"
        except Exception:
            pass
    return slot_id or "Presentation"


def render_delivery_refs(refs: list[dict[str, Any]]) -> list[str]:
    if not refs:
        return []

    lines = ["[onecolleague] References:"]
    rendered = 0

    for ref in refs:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "").strip()
        if kind == "task_ref":
            task_id = compact_delivery_text(ref.get("task_id"), limit=40)
            title = compact_delivery_text(ref.get("title"), limit=72)
            status = compact_delivery_text(ref.get("status"), limit=24)
            if task_id:
                label = f"- Task {task_id}"
                if status:
                    label += f" [{status}]"
                if title:
                    label += f" — {title}"
                lines.append(label)
                rendered += 1
                if rendered >= 4:
                    break
                continue

        if kind == "presentation_ref":
            slot_id = compact_delivery_text(ref.get("slot_id"), limit=32)
            label = _presentation_slot_label(
                slot_id,
                compact_delivery_text(ref.get("label"), limit=24),
            )
            locator_label = compact_delivery_text(ref.get("locator_label"), limit=48)
            title = compact_delivery_text(ref.get("title"), limit=72)
            header = f"- {label}"
            if slot_id:
                header += f" ({slot_id})"
            if locator_label:
                header += f" · {locator_label}"
            if title:
                header += f" — {title}"
            lines.append(header)
            excerpt = compact_delivery_text(ref.get("excerpt"), limit=120)
            if excerpt:
                lines.append(f'  excerpt: "{excerpt}"')
            href = compact_delivery_text(ref.get("href"), limit=120)
            if href:
                lines.append(f"  href: {href}")
            locator = ref.get("locator") if isinstance(ref.get("locator"), dict) else {}
            locator_url = compact_delivery_text(locator.get("url"), limit=120)
            if locator_url and locator_url != href:
                lines.append(f"  view_url: {locator_url}")
            captured_at = compact_delivery_text(locator.get("captured_at"), limit=48)
            if captured_at:
                lines.append(f"  captured_at: {captured_at}")
            viewer_scroll_top = locator.get("viewer_scroll_top")
            if isinstance(viewer_scroll_top, (int, float)) or str(viewer_scroll_top or "").strip():
                try:
                    scroll_value = int(float(viewer_scroll_top))
                except Exception:
                    scroll_value = None
                if scroll_value is not None and scroll_value >= 0:
                    lines.append(f"  scroll_top: {scroll_value}")
            snapshot = ref.get("snapshot") if isinstance(ref.get("snapshot"), dict) else {}
            snapshot_path = compact_delivery_text(snapshot.get("path"), limit=120)
            if snapshot_path:
                width = snapshot.get("width")
                height = snapshot.get("height")
                size_label = ""
                try:
                    width_value = int(width)
                    height_value = int(height)
                    if width_value > 0 and height_value > 0:
                        size_label = f" ({width_value}x{height_value})"
                except Exception:
                    size_label = ""
                lines.append(f"  snapshot: {snapshot_path}{size_label}")
            rendered += 1
            if rendered >= 4:
                break
            continue

        summary = compact_delivery_text(
            ref.get("title") or ref.get("path") or ref.get("url") or kind,
            limit=96,
        )
        if summary:
            prefix = kind or "ref"
            lines.append(f"- {prefix}: {summary}")
            rendered += 1
        if rendered >= 4:
            break

    if rendered == 0:
        return []
    if len(refs) > rendered:
        lines.append(f"- … ({len(refs) - rendered} more)")
    return lines


def build_actor_delivery_text(
    *,
    text: str,
    insight: str | None = None,
    priority: str,
    reply_required: bool,
    event_id: str,
    refs: list[dict[str, Any]],
    attachments: list[dict[str, Any]],
    collaboration_required: bool = False,
    src_group_id: str = "",
    src_event_id: str = "",
    computer_control_request: Any = None,
) -> str:
    delivery_text = text
    prefix_lines: list[str] = []
    if priority == "attention" and event_id:
        prefix_lines.append(f"[onecolleague] IMPORTANT (event_id={event_id}):")
    if reply_required and event_id:
        prefix_lines.append(f"[onecolleague] REPLY REQUIRED (event_id={event_id}): reply via onecolleague_message_reply.")
    if collaboration_required:
        prefix_lines.append(COLLABORATION_REQUIRED_INSTRUCTIONS)
    if isinstance(computer_control_request, dict):
        prefix_lines.append(render_computer_control_contract(computer_control_request))
    if src_group_id and src_event_id:
        prefix_lines.append(f"[onecolleague] RELAYED FROM (group_id={src_group_id}, event_id={src_event_id}):")
    if prefix_lines:
        delivery_text = "\n".join(prefix_lines) + "\n" + delivery_text
    ref_lines = render_delivery_refs(refs)
    if ref_lines:
        delivery_text = (delivery_text.rstrip("\n") + "\n\n" + "\n".join(ref_lines)).strip()
    if attachments:
        lines = [
            '[onecolleague] Attachments: use onecolleague_file(action="read", rel_path=...) for text; '
            'use action="blob_path" for binary/local tools.'
        ]
        for attachment in attachments[:8]:
            title = str(attachment.get("title") or attachment.get("path") or "file").strip()
            size_bytes = int(attachment.get("bytes") or 0)
            rel_path = str(attachment.get("path") or "").strip()
            lines.append(f"- {title} ({size_bytes} bytes) [{rel_path}]")
        if len(attachments) > 8:
            lines.append(f"- … ({len(attachments) - 8} more)")
        delivery_text = (delivery_text.rstrip("\n") + "\n\n" + "\n".join(lines)).strip()
    return append_peer_perspective(delivery_text, insight, label=PEER_PERSPECTIVE_AGENT_LABEL)


def build_actor_headless_delivery_text(
    *,
    by: str,
    to: list[str],
    body: str,
    reply_to: str = "",
    quote_text: str = "",
    source_platform: str = "",
    source_user_name: str = "",
    source_user_id: str = "",
) -> str:
    return render_actor_inbound_message(
        ActorInboundEnvelope(
            by=by,
            to=to,
            text=body,
            reply_to=reply_to,
            quote_text=quote_text,
            source_platform=source_platform,
            source_user_name=source_user_name,
            source_user_id=source_user_id,
        )
    )


def _normalize_to(raw: Any, *, actor_id: str = "") -> list[str]:
    if isinstance(raw, list):
        out = [str(item or "").strip() for item in raw if str(item or "").strip()]
        if out:
            return out
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return [actor_id] if actor_id else []


def render_actor_event_for_delivery(event: Dict[str, Any], *, actor_id: str = "") -> str:
    kind = str(event.get("kind") or "").strip()
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    event_id = str(event.get("id") or "").strip()
    by = str(event.get("by") or "").strip() or str(data.get("by") or "").strip() or "system"

    if kind == "chat.message":
        body = build_actor_delivery_text(
            text=str(data.get("text") or ""),
            insight=data.get("insight"),
            priority=str(data.get("priority") or "normal"),
            reply_required=bool(data.get("reply_required")),
            collaboration_required=bool(data.get("collaboration_required")),
            event_id=event_id,
            refs=[item for item in data.get("refs", []) if isinstance(item, dict)]
            if isinstance(data.get("refs"), list)
            else [],
            attachments=[item for item in data.get("attachments", []) if isinstance(item, dict)]
            if isinstance(data.get("attachments"), list)
            else [],
            src_group_id=str(data.get("src_group_id") or ""),
            src_event_id=str(data.get("src_event_id") or ""),
            computer_control_request=data.get("computer_control_request") if isinstance(data.get("computer_control_request"), dict) else None,
        )
        return build_actor_headless_delivery_text(
            by=by,
            to=_normalize_to(data.get("to"), actor_id=actor_id),
            body=body,
            reply_to=str(data.get("reply_to") or ""),
            quote_text=str(data.get("quote_text") or ""),
            source_platform=str(data.get("source_platform") or ""),
            source_user_name=str(data.get("source_user_name") or ""),
            source_user_id=str(data.get("source_user_id") or ""),
        )

    if kind == "system.notify":
        notify_kind = str(data.get("kind") or "info").strip() or "info"
        title = str(data.get("title") or data.get("summary") or "").strip()
        message = str(data.get("message") or data.get("text") or "").strip()
        body = "\n".join([item for item in (title, message) if item]).strip()
        return f"[onecolleague] SYSTEM ({notify_kind}): {body}".strip()

    text = str(data.get("text") or data.get("message") or data.get("summary") or "").strip()
    if not text:
        text = jsonish(data) if data else ""
    header = f"[onecolleague] {kind or 'event'}"
    if event_id:
        header += f" (event_id={event_id})"
    return f"{header}:\n{text}".strip()


def render_actor_event_batch_for_delivery(events: Iterable[Dict[str, Any]], *, actor_id: str = "") -> str:
    chunks: List[str] = []
    for event in events:
        if isinstance(event, dict):
            rendered = render_actor_event_for_delivery(event, actor_id=actor_id).strip()
            if rendered:
                chunks.append(rendered)
    return "\n\n".join(chunks).strip()


def jsonish(value: Any) -> str:
    try:
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value or "")
