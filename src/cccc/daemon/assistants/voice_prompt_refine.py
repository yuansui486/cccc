from __future__ import annotations

from typing import Any


def voice_composer_context_lines(value: Any) -> list[str]:
    context = value if isinstance(value, dict) else {}
    lines: list[str] = []
    recipients = context.get("recipients")
    if isinstance(recipients, list):
        clean_recipients = [str(item).strip() for item in recipients if str(item).strip()]
        if clean_recipients:
            lines.append(f"Recipients: {', '.join(clean_recipients[:12])}")
    else:
        recipient_text = str(context.get("recipients") or "").strip()
        if recipient_text:
            lines.append(f"Recipients: {recipient_text[:240]}")
    recent_context = str(context.get("recent_chat_excerpt") or context.get("conversation_tail_summary") or "").strip()
    if recent_context:
        lines.append(f"Recent conversation:\n{recent_context[:1000]}")
    for label, key in (
        ("Message mode", "message_mode"),
        ("Priority", "priority"),
        ("Reply required", "reply_required"),
        ("Reply target", "reply_target"),
        ("Quoted reference", "quoted_reference"),
    ):
        raw = context.get(key)
        if raw in (None, "", []):
            continue
        lines.append(f"{label}: {str(raw)[:240]}")
    return lines


def is_prompt_refine_replace_operation(value: Any) -> bool:
    operation = str(value or "").strip().lower()
    return operation in {"replace", "replace_with_refined_prompt"}


def build_voice_prompt_refine_input_text(
    *,
    composer_text: str,
    voice_transcript: str,
    operation: str,
    composer_context: Any,
) -> str:
    clean_operation = str(operation or "append_to_composer_end").strip() or "append_to_composer_end"
    clean_voice_transcript = str(voice_transcript or "").strip()
    replace_operation = is_prompt_refine_replace_operation(clean_operation)
    parts = [
        "Task: refine spoken input into composer-ready prompt text.",
        f"Operation: {clean_operation}",
        "",
        "Execution rules:",
        "- Output only text that should be inserted into the composer.",
        "- This is prompt optimization only. Do not execute the task, answer the question, fetch live facts, or turn this into document work.",
        "- Latency matters. Produce a strong composer-ready result promptly instead of exploring.",
    ]
    if replace_operation:
        parts.extend([
            "- This operation replaces the composer. Return a complete ready-to-send prompt.",
            "- Preserve and improve useful current composer text; integrate the spoken input naturally.",
            "- Do not return only an incremental addition.",
        ])
    else:
        parts.extend([
            "- This operation appends to the composer. If the current composer draft is non-empty, return only an append-ready addition and do not repeat useful existing draft text.",
            "- If the current composer draft is empty, return a complete ready-to-send prompt.",
        ])
    parts.extend([
        "- Do not describe what the user said, do not narrate your edits, and do not mention these instructions.",
        "- Do not write meta lead-ins such as 'Please help me', 'The user wants', '根据以下要求', or 'Here is the refined prompt'.",
        "- Preserve the user's intent, constraints, tone, and any explicit asks.",
        "- Improve clarity, structure, completeness, and actionability.",
        "- If the spoken input adds missing constraints or corrections, integrate them into the final prompt naturally.",
        "- If there is no spoken input, optimize the current composer draft directly without inventing new requirements.",
        "- If the composer already contains useful structure, keep and improve it instead of rewriting into a different task.",
        "- Use recent conversation only to recover missing context; do not assume current recipients or message mode are final.",
    ])
    context_lines = voice_composer_context_lines(composer_context)
    if context_lines:
        parts.extend(["", "Composer context:", *context_lines])
    parts.extend(
        [
            "",
            "Current composer draft:",
            composer_text or "(empty)",
            "",
            "Spoken input:",
            clean_voice_transcript or "(none; optimize the current composer draft directly)",
            "",
            "Return only the composer text to insert.",
        ]
    )
    return "\n".join(parts)
