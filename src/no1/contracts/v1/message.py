from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


INSIGHT_MAX_CHARS = 1200


def normalize_insight(value: Any) -> Optional[str]:
    """Normalize the optional sender perspective carried by a chat message."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("insight must be a string")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > INSIGHT_MAX_CHARS:
        raise ValueError(f"insight must be at most {INSIGHT_MAX_CHARS} characters")
    return normalized


class Reference(BaseModel):
    """Reference to a file/commit/URL/text snippet."""

    kind: Literal["file", "url", "commit", "text"] = "url"
    url: str = ""
    path: str = ""
    title: str = ""
    sha: str = ""
    bytes: int = 0

    model_config = ConfigDict(extra="allow")


class Attachment(BaseModel):
    """Attachment metadata (payload stored under blobs/)."""

    kind: Literal["text", "image", "file"] = "file"
    path: str = ""
    title: str = ""
    mime_type: str = ""
    bytes: int = 0
    sha256: str = ""

    model_config = ConfigDict(extra="allow")


class TurnProvenance(BaseModel):
    """Daemon-authored origin record for one persisted chat event."""

    v: Literal[1] = 1
    origin: Literal["local_user", "local_actor", "im", "cross_group", "group_bridge", "untrusted"]
    ingress: Literal["web_user", "cli_user", "actor_mcp", "im", "cross_group", "group_bridge", "reply", "untrusted"]
    fresh_local_request: bool = False
    local_request_id: Optional[str] = None
    parent_event_id: Optional[str] = None
    root_event_id: Optional[str] = None
    source_transport: Optional[str] = None
    source_group_id: Optional[str] = None
    source_event_id: Optional[str] = None
    source_peer_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_origin_consistency(self) -> "TurnProvenance":
        expected_ingress = {
            "local_user": {"web_user", "cli_user"},
            "local_actor": {"actor_mcp"},
            "im": {"im", "reply"},
            "cross_group": {"cross_group", "reply"},
            "group_bridge": {"group_bridge", "reply"},
            "untrusted": {"untrusted", "reply"},
        }
        if self.ingress not in expected_ingress[self.origin]:
            raise ValueError("turn provenance origin and ingress are inconsistent")
        request_id = str(self.local_request_id or "").strip()
        if self.fresh_local_request:
            if self.origin != "local_user" or not request_id:
                raise ValueError("fresh local provenance requires a local-user request id")
        elif request_id:
            raise ValueError("non-fresh provenance cannot carry a local request id")

        identity_values = {
            "source_group_id": self.source_group_id,
            "source_event_id": self.source_event_id,
            "source_peer_id": self.source_peer_id,
        }
        for field_name, value in identity_values.items():
            if value is None:
                continue
            if (
                value != value.strip()
                or not value
                or len(value) > 512
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ValueError(f"{field_name} is not a normalized source identity")

        transport = self.source_transport
        if transport is not None and (
            transport != transport.strip().lower()
            or re.fullmatch(r"[a-z0-9][a-z0-9._:-]{0,127}", transport) is None
        ):
            raise ValueError("source_transport is not canonical")

        required_remote_identity = {
            "im": (transport, self.source_peer_id),
            "cross_group": (transport, self.source_group_id, self.source_event_id, self.source_peer_id),
            "group_bridge": (transport, self.source_group_id, self.source_peer_id),
        }
        required = required_remote_identity.get(self.origin)
        if required is not None and not all(required):
            raise ValueError("remote provenance requires complete source identity")
        if self.origin == "im" and (self.source_group_id is not None or self.source_event_id is not None):
            raise ValueError("IM provenance cannot carry group or event identity")
        if self.origin == "cross_group" and transport != "cross_group":
            raise ValueError("cross-group provenance requires the canonical transport")
        remote_identity = (self.source_group_id, self.source_event_id, self.source_peer_id)
        if self.origin == "local_user" and (
            transport != self.ingress or self.ingress not in {"web_user", "cli_user"} or any(remote_identity)
        ):
            raise ValueError("local-user provenance cannot carry remote identity")
        if self.origin == "local_actor" and (
            transport != "actor_mcp" or self.ingress != "actor_mcp" or any(remote_identity)
        ):
            raise ValueError("local-actor provenance cannot carry remote identity")
        if self.origin == "untrusted" and (transport is not None or any(remote_identity)):
            raise ValueError("untrusted provenance cannot carry trusted source identity")
        return self


class ChatMessageData(BaseModel):
    """IM-style chat message."""

    # Core content
    text: str
    format: Literal["plain", "markdown"] = "plain"
    insight: Optional[str] = Field(default=None, max_length=INSIGHT_MAX_CHARS)

    # Priority / workflow semantics
    priority: Literal["normal", "attention"] = "normal"
    reply_required: bool = False
    collaboration_required: bool = False
    computer_control_request: Optional[Dict[str, Any]] = None

    # IM semantics
    to: List[str] = Field(default_factory=list)  # @mentions (empty = broadcast)
    reply_to: Optional[str] = None  # The replied-to message event_id
    quote_text: Optional[str] = None  # Quoted snippet for display
    source_platform: Optional[str] = None  # External IM source platform, e.g. dingtalk
    source_user_name: Optional[str] = None  # External IM sender display name
    source_user_id: Optional[str] = None  # External IM sender platform user id
    mention_user_ids: Optional[List[str]] = None  # External IM real-mention targets
    sender_title: Optional[str] = None  # Immutable sender title snapshot for message rendering
    sender_runtime: Optional[str] = None  # Immutable sender runtime snapshot for message rendering
    sender_avatar_path: Optional[str] = None  # Immutable blob-backed sender avatar path

    # Cross-group provenance (for relays/forwarding)
    src_group_id: Optional[str] = None
    src_event_id: Optional[str] = None
    src_by: Optional[str] = None
    remote_reply_to: Optional[List[str]] = None

    # Cross-group destination metadata (for "send to other group" source messages)
    dst_group_id: Optional[str] = None
    dst_to: Optional[List[str]] = None

    # Attachments and references
    refs: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)

    # Reserved
    thread: str = ""  # Topic/thread ID (future)

    # Streaming
    stream_id: Optional[str] = None  # Links final message to its stream
    pending_event_id: Optional[str] = None  # Stable turn-scoped id to reconcile transient UI bubbles

    # Metadata
    client_id: Optional[str] = None  # Client-generated idempotency key
    request_fingerprint: Optional[str] = None  # Daemon-authored exact replay identity
    turn_provenance: Optional[TurnProvenance] = None

    @field_validator("insight", mode="before")
    @classmethod
    def _normalize_insight(cls, value: Any) -> Optional[str]:
        return normalize_insight(value)

    model_config = ConfigDict(extra="forbid")


class ChatStreamData(BaseModel):
    """Streaming chunk for real-time message rendering (not persisted)."""

    stream_id: str  # Unique stream identifier
    op: Literal["start", "update", "end"]
    mode: Literal["snapshot", "delta"] = "snapshot"
    text: str = ""
    format: Literal["plain", "markdown"] = "plain"
    seq: int = 0  # Monotonic sequence number

    # IM semantics
    to: List[str] = Field(default_factory=list)
    reply_to: Optional[str] = None

    # Metadata
    client_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ChatReactionData(BaseModel):
    """Message reaction (emoji)."""

    event_id: str  # Target message event_id
    actor_id: str  # Actor who reacted
    emoji: str  # Emoji reaction (e.g., ✅/❌/👍/🤔)

    model_config = ConfigDict(extra="forbid")
