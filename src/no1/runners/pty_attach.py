from __future__ import annotations

from typing import Any, Dict, Optional


class PtyAttachBusyError(RuntimeError):
    code = "terminal_attach_busy"


class PtyAttachReservation:
    def __init__(
        self,
        *,
        session: Any,
        fileno: int,
        reserved_client: Any,
        previous_writer_fd: Optional[int],
        previous_writer_client: Any,
        metadata: Dict[str, object],
    ) -> None:
        self._session = session
        self._fileno = fileno
        self._reserved_client = reserved_client
        self._previous_writer_fd = previous_writer_fd
        self._previous_writer_client = previous_writer_client
        self._metadata = dict(metadata)
        self._state = "pending"

    @property
    def metadata(self) -> Dict[str, object]:
        return dict(self._metadata)

    def activate(self) -> bool:
        return bool(self._session._activate_attach_reservation(self))

    def cancel(self) -> None:
        self._session._cancel_attach_reservation(self)
