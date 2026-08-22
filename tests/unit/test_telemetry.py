from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from aegisrun.telemetry.tracing import JsonLinesSpanExporter, configure_tracing, get_tracer


def test_json_lines_exporter(tmp_path: Path) -> None:
    context = SimpleNamespace(trace_id=1, span_id=2)
    span = SimpleNamespace(
        name="tool.execute",
        get_span_context=lambda: context,
        start_time=1,
        end_time=2,
        status=SimpleNamespace(status_code=SimpleNamespace(name="OK")),
        attributes={"aegisrun.run_id": "run-1"},
    )
    target = tmp_path / "traces.jsonl"
    JsonLinesSpanExporter(target).export([span])
    payload = json.loads(target.read_text())
    assert payload["trace_id"].endswith("1")
    assert payload["attributes"]["aegisrun.run_id"] == "run-1"


def test_configure_tracing_and_get_tracer(tmp_path: Path) -> None:
    configure_tracing(tmp_path / "spans.jsonl")
    assert get_tracer() is not None
