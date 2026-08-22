from __future__ import annotations

import asyncio
import json
from html import escape
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

import httpx
import typer
from rich.console import Console
from rich.table import Table

from aegisrun.api.app import run_view
from aegisrun.config import get_settings
from aegisrun.core.domain import ApprovalDecision, BudgetSnapshot, PolicySnapshot
from aegisrun.persistence.database import Database
from aegisrun.persistence.repository import ApprovalRepository, RunRepository
from aegisrun.runtime.worker import Worker

app = typer.Typer(help="EquiSeek CLI")
console = Console()


@app.command()
def init_db() -> None:
    """Create development database tables."""

    async def execute() -> None:
        database = Database()
        await database.create_schema()
        await database.dispose()

    asyncio.run(execute())
    console.print("[green]database ready[/green]")


@app.command("worker")
def run_worker(
    once: Annotated[bool, typer.Option(help="Handle one run then exit")] = False,
) -> None:
    async def execute() -> None:
        settings = get_settings()
        database = Database(settings)
        if settings.auto_create_schema:
            await database.create_schema()
        worker = Worker(database, settings)
        if once:
            await worker.run_once()
        else:
            await worker.run_forever()
        await database.dispose()

    asyncio.run(execute())


@app.command("demo-fake")
def demo_fake(
    auto_approve: Annotated[bool, typer.Option(help="Approve the patch automatically")] = True,
    report: Annotated[Path, typer.Option(help="Static HTML report path")] = Path("run-report.html"),
) -> None:
    """Run the complete offline issue-triage golden path."""

    async def execute() -> dict[str, object]:
        settings = get_settings()
        database = Database(settings)
        await database.create_schema()
        async with database.session() as session:
            repository = RunRepository(session)
            run, _ = await repository.create(
                agent_name="issue_triage",
                input_json={"issue": "ISSUE.md", "mode": "fake"},
                policy=PolicySnapshot(),
                budget=BudgetSnapshot(),
                idempotency_key=f"demo-{uuid4()}",
            )
            run_id = run.id
            await session.commit()
        worker = Worker(database, settings)
        for _ in range(50):
            await worker.run_once()
            async with database.session() as session:
                repository = RunRepository(session)
                run = await repository.get(run_id)
                approval = await ApprovalRepository(session).pending_for_run(run_id)
                if approval and auto_approve:
                    await ApprovalRepository(session).decide(
                        approval.id,
                        ApprovalDecision.APPROVE,
                        approval.version,
                        "offline golden demo",
                    )
                    await session.commit()
                    continue
                if run.status in {"succeeded", "failed", "cancelled", "waiting_approval"}:
                    events = await repository.list_events(run_id, 0, 500)
                    payload: dict[str, object] = {
                        "run": run_view(run, approval).model_dump(mode="json"),
                        "events": [
                            {
                                "seq": event.seq,
                                "type": event.event_type,
                                "phase": event.phase,
                                "payload": event.payload_public,
                            }
                            for event in events
                        ],
                    }
                    break
        else:
            raise RuntimeError("offline demo did not reach a stable state within 50 iterations")
        await database.dispose()
        return payload

    payload = asyncio.run(execute())
    _write_report(report, payload)
    run = cast(dict[str, Any], payload["run"])
    style = "bold green" if run["status"] == "succeeded" else "bold red"
    console.print(f"[{style}]Run {run['id']} -> {run['status']}[/{style}]")
    if run["status"] != "succeeded" and auto_approve:
        raise typer.Exit(code=1)
    console.print(f"Report: {report.resolve()}")


@app.command("show")
def show_run(run_id: str, base_url: str = "http://127.0.0.1:8000") -> None:
    response = httpx.get(f"{base_url}/api/runs/{run_id}", timeout=10)
    response.raise_for_status()
    data = response.json()
    table = Table(title=f"Run {run_id}")
    table.add_column("Field")
    table.add_column("Value")
    for key in ("status", "terminal_reason", "version", "thread_id"):
        table.add_row(key, str(data.get(key)))
    console.print(table)


def _write_report(path: Path, payload: dict[str, object]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    escaped = escape(data)
    html_document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>EquiSeek report</title>
<style>
body{{font:15px/1.5 ui-monospace,monospace;background:#0b1020;color:#dbeafe;
margin:2rem auto;max-width:1100px}}h1{{color:#67e8f9}}
pre{{background:#111827;padding:1.5rem;border:1px solid #334155;
border-radius:12px;white-space:pre-wrap}}.badge{{color:#86efac}}
</style></head><body><h1>EquiSeek deterministic run report</h1>
<p class="badge">Generated without an external model key.</p>
<pre>{escaped}</pre></body></html>"""
    path.write_text(html_document, encoding="utf-8")


if __name__ == "__main__":
    app()
