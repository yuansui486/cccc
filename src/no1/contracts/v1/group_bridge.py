"""Transport-independent contracts for Group Bridge."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .message import INSIGHT_MAX_CHARS, normalize_insight

GroupBridgeAccessLevel = Literal["messages", "read", "full"]
GROUP_BRIDGE_ACCESS_LEVELS = ("messages", "read", "full")
DEFAULT_GROUP_BRIDGE_ACCESS_LEVEL: GroupBridgeAccessLevel = "messages"

RegistrationStatus = Literal["active", "unauthorized", "revoked", "error"]
RemoteSendStatus = Literal["queued", "sending", "retrying", "sent", "failed"]
GroupBridgePairingStatus = Literal["submitted", "pending", "approving", "approved", "rejected", "expired"]


class RemoteSendPayload(BaseModel):
    text: str
    insight: Optional[str] = Field(default=None, max_length=INSIGHT_MAX_CHARS)
    format: Literal["plain", "markdown"] = "plain"
    priority: Literal["normal", "attention"] = "normal"
    reply_required: bool = False
    to: List[str] = Field(default_factory=list)
    refs: List[Dict[str, object]] = Field(default_factory=list)
    attachments: List[Dict[str, object]] = Field(default_factory=list)
    source_by: str = ""

    @field_validator("insight", mode="before")
    @classmethod
    def _normalize_insight(cls, value: object) -> Optional[str]:
        return normalize_insight(value)

    model_config = ConfigDict(extra="forbid")


class RemoteSendEnvelope(BaseModel):
    src_group_id: str
    registration_id: str
    idempotency_key: str
    payload: RemoteSendPayload

    model_config = ConfigDict(extra="forbid")


class RemoteSendError(BaseModel):
    code: str
    message: str = ""
    retriable: bool = False
    transport: str = ""
    http_status: Optional[int] = None

    model_config = ConfigDict(extra="forbid")


class RemoteSendReceipt(BaseModel):
    ok: bool = False
    status: RemoteSendStatus = "queued"
    idempotency_key: str = ""
    registration_id: str = ""
    request_fingerprint: str = ""
    transport: str = ""
    remote_event_id: Optional[str] = None
    attempt: int = 0
    max_attempts: int = 5
    first_queued_at: str = ""
    last_attempt_at: str = ""
    next_attempt_at: str = ""
    accepted_at: str = ""
    error: Optional[RemoteSendError] = None

    model_config = ConfigDict(extra="forbid")


class GroupBridgeSessionMessage(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)
    format: Literal["plain", "markdown"] = "plain"
    priority: Literal["normal", "attention"] = "normal"
    reply_required: bool = False
    insight: Optional[str] = Field(default=None, max_length=INSIGHT_MAX_CHARS)
    source_by: str = Field(default="", max_length=256)

    @field_validator("insight", mode="before")
    @classmethod
    def _normalize_insight(cls, value: object) -> Optional[str]:
        return normalize_insight(value)

    model_config = ConfigDict(extra="forbid", strict=True)


class RemoteSendQueuedRequest(BaseModel):
    """Immutable, transport-independent facts retained for an enqueue."""

    src_group_id: str = Field(min_length=1, max_length=256)
    registration_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(min_length=1, max_length=256, pattern=r"gbs_[0-9a-f]{32}")
    payload: GroupBridgeSessionMessage

    model_config = ConfigDict(extra="forbid", strict=True)


class GroupBridgeSignedMessageEnvelope(BaseModel):
    version: Literal[1] = 1
    kind: Literal["message"] = "message"
    transport: Literal["group_bridge_session"] = "group_bridge_session"
    nonce: str = Field(min_length=43, max_length=43, pattern=r"[A-Za-z0-9_-]{43}")
    issued_at: str = Field(min_length=1, max_length=40)
    source_group_id: str = Field(min_length=1, max_length=256)
    source_peer_id: str = Field(min_length=1, max_length=256)
    source_public_key: str = Field(min_length=44, max_length=44, pattern=r"[A-Za-z0-9+/]{43}=")
    source_endpoint: str = Field(min_length=1, max_length=2048)
    target_group_id: str = Field(min_length=1, max_length=256)
    target_peer_id: str = Field(min_length=1, max_length=256)
    target_endpoint: str = Field(min_length=1, max_length=2048)
    payload: GroupBridgeSessionMessage
    signature: str = Field(min_length=88, max_length=88, pattern=r"[A-Za-z0-9+/]{86}==")

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("version", mode="before")
    @classmethod
    def _closed_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("version must be the integer 1")
        return value


class GroupBridgeSignedReceiptEnvelope(BaseModel):
    version: Literal[1] = 1
    kind: Literal["receipt"] = "receipt"
    transport: Literal["group_bridge_session"] = "group_bridge_session"
    request_nonce_hash: str = Field(min_length=64, max_length=64, pattern=r"[0-9a-f]{64}")
    request_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"[0-9a-f]{64}")
    issued_at: str = Field(min_length=1, max_length=40)
    source_group_id: str = Field(min_length=1, max_length=256)
    source_peer_id: str = Field(min_length=1, max_length=256)
    source_public_key: str = Field(min_length=44, max_length=44, pattern=r"[A-Za-z0-9+/]{43}=")
    source_endpoint: str = Field(min_length=1, max_length=2048)
    target_group_id: str = Field(min_length=1, max_length=256)
    target_peer_id: str = Field(min_length=1, max_length=256)
    target_endpoint: str = Field(min_length=1, max_length=2048)
    status: Literal["accepted"] = "accepted"
    remote_event_id: str = Field(min_length=1, max_length=512)
    signature: str = Field(min_length=88, max_length=88, pattern=r"[A-Za-z0-9+/]{86}==")

    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("version", mode="before")
    @classmethod
    def _closed_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("version must be the integer 1")
        return value


class GroupBridgePairingConnectionEnvelope(BaseModel):
    """One-time signed connection information shown by the invite issuer."""

    version: Literal[1] = 1
    kind: Literal["pairing_connection"] = "pairing_connection"
    transport: Literal["group_bridge_session"] = "group_bridge_session"
    invite_id: str = Field(pattern=r"pinv_[0-9a-f]{16}")
    pairing_code: str = Field(pattern=r"[0-9A-F]{8}(?:-[0-9A-F]{8}){3}")
    issuer_group_id: str = Field(min_length=1, max_length=256)
    issuer_peer_id: str = Field(min_length=1, max_length=256)
    issuer_public_key: str = Field(min_length=44, max_length=44, pattern=r"[A-Za-z0-9+/]{43}=")
    issuer_endpoint: str = Field(min_length=1, max_length=2048)
    issued_at: str = Field(min_length=1, max_length=40)
    expires_at: str = Field(min_length=1, max_length=40)
    signature: str = Field(min_length=88, max_length=88, pattern=r"[A-Za-z0-9+/]{86}==")

    model_config = ConfigDict(extra="forbid", strict=True)


class GroupBridgePairingRequestEnvelope(BaseModel):
    """Signed requester facts submitted to the invite issuer."""

    version: Literal[1] = 1
    kind: Literal["pairing_request"] = "pairing_request"
    transport: Literal["group_bridge_session"] = "group_bridge_session"
    invite_id: str = Field(pattern=r"pinv_[0-9a-f]{16}")
    pairing_code: str = Field(pattern=r"[0-9A-F]{8}(?:-[0-9A-F]{8}){3}")
    client_nonce: str = Field(min_length=43, max_length=43, pattern=r"[A-Za-z0-9_-]{43}")
    expires_at: str = Field(min_length=1, max_length=40)
    issuer_group_id: str = Field(min_length=1, max_length=256)
    issuer_peer_id: str = Field(min_length=1, max_length=256)
    issuer_public_key: str = Field(min_length=44, max_length=44, pattern=r"[A-Za-z0-9+/]{43}=")
    issuer_endpoint: str = Field(min_length=1, max_length=2048)
    requester_group_id: str = Field(min_length=1, max_length=256)
    requester_group_title: str = Field(default="", max_length=256)
    requester_peer_id: str = Field(min_length=1, max_length=256)
    requester_public_key: str = Field(min_length=44, max_length=44, pattern=r"[A-Za-z0-9+/]{43}=")
    requester_endpoint: str = Field(min_length=1, max_length=2048)
    signature: str = Field(min_length=88, max_length=88, pattern=r"[A-Za-z0-9+/]{86}==")

    model_config = ConfigDict(extra="forbid", strict=True)


class GroupBridgePairingStatusEnvelope(BaseModel):
    """Issuer-signed status consumed by one exact requester outbound."""

    version: Literal[1] = 1
    kind: Literal["pairing_status"] = "pairing_status"
    transport: Literal["group_bridge_session"] = "group_bridge_session"
    request_id: str = Field(pattern=r"preq_[0-9a-f]{16}")
    invite_id: str = Field(pattern=r"pinv_[0-9a-f]{16}")
    request_fingerprint: str = Field(pattern=r"[0-9a-f]{64}")
    status: Literal["pending", "approving", "approved", "rejected", "expired"]
    issued_at: str = Field(min_length=1, max_length=40)
    expires_at: str = Field(min_length=1, max_length=40)
    issuer_group_id: str = Field(min_length=1, max_length=256)
    issuer_peer_id: str = Field(min_length=1, max_length=256)
    issuer_public_key: str = Field(min_length=44, max_length=44, pattern=r"[A-Za-z0-9+/]{43}=")
    issuer_endpoint: str = Field(min_length=1, max_length=2048)
    requester_group_id: str = Field(min_length=1, max_length=256)
    requester_peer_id: str = Field(min_length=1, max_length=256)
    requester_endpoint: str = Field(min_length=1, max_length=2048)
    signature: str = Field(min_length=88, max_length=88, pattern=r"[A-Za-z0-9+/]{86}==")

    model_config = ConfigDict(extra="forbid", strict=True)


class RegistrationRecord(BaseModel):
    """Persisted target metadata; raw credentials are deliberately absent."""

    registration_id: str
    registration_fingerprint: str
    group_id: str
    url: str
    transport: str = "registry_hub"
    remote_group_id: str = ""
    remote_peer_id: str = ""
    multiaddrs: List[str] = Field(default_factory=list)
    credential_ref: str = ""
    user_id: str = ""
    status: RegistrationStatus = "active"
    created_at: str
    updated_at: str
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None

    model_config = ConfigDict(extra="forbid")
