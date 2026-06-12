"""System notification contracts.

System notifications are separated from chat messages to avoid polluting user conversations.

Kinds:
- nudge: remind an actor to handle unread messages
- keepalive: remind an actor to continue work (after detecting a "Next:" declaration)
- help_nudge: remind an actor to refresh the collaboration playbook (onecolleague_help)
- actor_idle: actor idle alert (to foreman)
- silence_check: group silence alert (to foreman)
- auto_idle: group automatically transitioned to idle after repeated silence checks
- automation: user-defined automation rule notification
- task_empty_active: no active tasks remain on the board
- task_empty_planned: no planned tasks remain on the board
- task_active_overdue: active task runtime reminder
- task_planned_unassigned: planned task remains unassigned
- status_change: actor/group status change
- error: system error
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


NotifyKind = Literal[
    "nudge",           # Remind about unread messages
    "keepalive",       # Remind to continue work
    "help_nudge",      # Ask actor to refresh onecolleague_help
    "actor_idle",      # Actor idle alert (to foreman)
    "silence_check",   # Group silence alert (to foreman)
    "auto_idle",       # Group auto-idled after repeated silence checks
    "automation",      # User-defined automation rule notification
    "task_empty_active",  # No active tasks remain
    "task_empty_planned",  # No planned tasks remain
    "task_active_overdue",  # Active task runtime reminder
    "task_planned_unassigned",  # Planned task remains unassigned
    "status_change",   # Status change notification
    "error",           # Error notification
    "info",            # Informational notification
]

NotifyPriority = Literal["low", "normal", "high", "urgent"]


class SystemNotifyData(BaseModel):
    """System notification payload."""

    # Type
    kind: NotifyKind
    priority: NotifyPriority = "normal"

    # Content
    title: str = ""
    message: str = ""

    # Target
    target_actor_id: Optional[str] = None  # Target actor (None = broadcast)

    # Context
    context: Dict[str, Any] = Field(default_factory=dict)

    # Acknowledgement
    requires_ack: bool = False

    # Related event
    related_event_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class NotifyAckData(BaseModel):
    """Notification acknowledgement payload."""

    notify_event_id: str  # The acknowledged notify event_id
    actor_id: str         # Actor who acknowledged

    model_config = ConfigDict(extra="forbid")
