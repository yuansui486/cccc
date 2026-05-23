"""Worker entry point."""
from __future__ import annotations

import asyncio
import logging

from common.settings import WorkerSettings
from worker.client import WorkerApp


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = WorkerSettings.from_env()
    app = WorkerApp(settings)
    asyncio.run(app.run_forever())


if __name__ == "__main__":
    main()
