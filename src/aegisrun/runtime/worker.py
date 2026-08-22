from __future__ import annotations

import asyncio
import signal

from aegisrun.config import Settings, get_settings
from aegisrun.persistence.database import Database
from aegisrun.persistence.repository import RunRepository
from aegisrun.runtime.issue_triage import IssueTriageRuntime
from aegisrun.telemetry import configure_tracing, get_tracer


class Worker:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.runtime = IssueTriageRuntime(database, settings)
        self.stopping = asyncio.Event()
        if settings.telemetry_jsonl:
            configure_tracing(settings.telemetry_jsonl)

    async def run_once(self) -> bool:
        with get_tracer().start_as_current_span("aegisrun.worker.iteration") as span:
            async with self.database.session() as session:
                repository = RunRepository(session)
                run = await repository.claim_next(
                    self.settings.worker_id, self.settings.lease_seconds
                )
                if not run:
                    return False
                run_id = run.id
                span.set_attribute("aegisrun.run_id", run_id)
                span.set_attribute("aegisrun.thread_id", run.thread_id)
                await session.commit()
            await self.runtime.execute_claimed(run_id, self.settings.worker_id)
        return True

    async def run_forever(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self.stopping.set)
        while not self.stopping.is_set():
            handled = await self.run_once()
            if not handled:
                try:
                    await asyncio.wait_for(
                        self.stopping.wait(), timeout=self.settings.worker_poll_seconds
                    )
                except TimeoutError:
                    pass


async def main() -> None:
    settings = get_settings()
    database = Database(settings)
    if settings.auto_create_schema:
        await database.create_schema()
    try:
        await Worker(database, settings).run_forever()
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
