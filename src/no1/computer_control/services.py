from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

from .lease import ComputerControlLease
from .derived_authority import DerivedAuthorityStore
from .run_authority import RunAuthorityStore
from .mcp import WindowsMCPSetup, WindowsMCPSession
from .runtime import WorkflowRunner
from .recording import RecordingStore
from .requests import ComputerRequestStore
from .scheduler import ComputerControlScheduler
from .storage import WorkflowStore
from .picker import ElementPickerManager
from .observation import ElementObservationService
from ..daemon.messaging.turn_provenance import (
    get_actor_turn_generation,
    get_daemon_turn_issuer_epoch,
)


class ComputerControlServices:
    def __init__(self, home: Path):
        self.home = home
        self.store = WorkflowStore(home)
        self.requests = ComputerRequestStore(self.store)
        self.authorities = DerivedAuthorityStore(
            home,
            issuer_epoch_provider=get_daemon_turn_issuer_epoch,
            generation_provider=get_actor_turn_generation,
        )
        self.run_authorities = RunAuthorityStore(
            home,
            issuer_epoch_provider=get_daemon_turn_issuer_epoch,
            generation_provider=get_actor_turn_generation,
        )
        self.lease = ComputerControlLease(home)
        self.session = WindowsMCPSession()
        self.setup = WindowsMCPSetup(home, self.session, self.store, self.lease)
        self.observation = ElementObservationService()
        self.picker = ElementPickerManager(
            home,
            self.lease,
            snapshot_provider=self._snapshot_for_picker,
        )
        self.recordings = RecordingStore(
            home,
            self.store,
            self.requests,
            self.authorities,
            self.lease,
            self.session,
            fingerprint_provider=lambda: str(self.setup.status().get("fingerprint") or ""),
            observation_provider=self.observation,
        )
        self.runner = WorkflowRunner(
            home,
            self.store,
            self.lease,
            self.session,
            run_authorities=self.run_authorities,
            requests=self.requests,
            fingerprint_provider=lambda: str(self.setup.status().get("fingerprint") or ""),
            observation_provider=self.observation,
        )
        self.scheduler = ComputerControlScheduler(self)

    def _snapshot_for_picker(self) -> Any:
        """Return one MCP snapshot for picker fallback integrations.

        Native UIA is preferred by :class:`ElementPickerManager`; keeping this
        callback here allows a future helper or a browser DOM adapter to use
        the existing supervised MCP session without importing MCP code into
        the picker module.
        """
        tools = self.session.catalog_sync()
        name = next((str(item.get("name") or "") for item in tools if str(item.get("name") or "").casefold() == "snapshot"), "")
        if not name:
            raise RuntimeError("Windows-MCP Snapshot tool is unavailable")
        return self.session.call_tool_sync(name, {})


_services: Dict[str, ComputerControlServices] = {}
_lock = threading.Lock()


def get_services(home: Path) -> ComputerControlServices:
    key = str(home.resolve())
    with _lock:
        service = _services.get(key)
        if service is None:
            service = ComputerControlServices(home.resolve())
            _services[key] = service
        return service
