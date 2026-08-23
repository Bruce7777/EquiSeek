from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult


class JsonLinesSpanExporter(SpanExporter):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Any) -> SpanExportResult:
        with self.path.open("a", encoding="utf-8") as output:
            for span in spans:
                output.write(json.dumps(self._serialize(span), default=str) + "\n")
        return SpanExportResult.SUCCESS

    @staticmethod
    def _serialize(span: ReadableSpan) -> dict[str, Any]:
        context = span.get_span_context()
        return {
            "name": span.name,
            "trace_id": format(context.trace_id, "032x") if context else None,
            "span_id": format(context.span_id, "016x") if context else None,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "status": span.status.status_code.name,
            "attributes": dict(span.attributes or {}),
        }


def configure_tracing(jsonl_path: Path | None = None) -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": "aegisrun"}))
    if jsonl_path:
        provider.add_span_processor(SimpleSpanProcessor(JsonLinesSpanExporter(jsonl_path)))
    trace.set_tracer_provider(provider)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("aegisrun.runtime", "0.2.0")
