from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict

from .lease import ComputerControlLease
from .mcp import WindowsMCPSetup, WindowsMCPSession
from .runtime import WorkflowRunner
from .recording import RecordingStore
from .requests import ComputerRequestStore
from .scheduler import ComputerControlScheduler
from .storage import WorkflowStore


class ComputerControlServices:
    def __init__(self, home: Path):
        self.home = home
        self.store = WorkflowStore(home)
        self.requests = ComputerRequestStore(self.store)
        self.lease = ComputerControlLease(home)
        self.session = WindowsMCPSession()
        self.setup = WindowsMCPSetup(home, self.session, self.store)
        self.recordings = RecordingStore(
            home,
            self.store,
            self.requests,
            self.lease,
            self.session,
            fingerprint_provider=lambda: str(self.setup.status().get("fingerprint") or ""),
        )
        self.runner = WorkflowRunner(home, self.store, self.lease, self.session)
        self.scheduler = ComputerControlScheduler(self)


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
