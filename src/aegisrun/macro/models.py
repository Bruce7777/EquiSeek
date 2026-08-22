from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

METRIC_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass(frozen=True, slots=True)
class MacroMetric:
    code: str
    name: str
    value: float
    unit: str
    period: str
    source_name: str
    source_url: str
    note: str = ""

    def __post_init__(self) -> None:
        if not METRIC_CODE.fullmatch(self.code):
            raise ValueError(f"invalid macro metric code: {self.code}")
        if not self.name.strip() or not self.unit.strip() or not self.period.strip():
            raise ValueError("macro metric labels cannot be empty")
        if not math.isfinite(self.value):
            raise ValueError("macro metric value must be finite")
        if not self.source_name.strip() or not self.source_url.startswith("https://"):
            raise ValueError("macro metric requires an HTTPS source")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MacroMetric:
        return cls(
            code=str(value["code"]),
            name=str(value["name"]),
            value=float(value["value"]),
            unit=str(value["unit"]),
            period=str(value["period"]),
            source_name=str(value["source_name"]),
            source_url=str(value["source_url"]),
            note=str(value.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class MacroSnapshot:
    version: str
    label: str
    as_of: date
    metrics: tuple[MacroMetric, ...]
    methodology_sources: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.version.strip() or not self.label.strip() or not self.metrics:
            raise ValueError("macro snapshot metadata and metrics are required")
        codes = [metric.code for metric in self.metrics]
        if len(codes) != len(set(codes)):
            raise ValueError("macro metric codes must be unique")
        if any(
            not name.strip() or not url.startswith("https://")
            for name, url in self.methodology_sources
        ):
            raise ValueError("methodology sources require labels and HTTPS URLs")

    def metric(self, code: str) -> MacroMetric:
        try:
            return next(metric for metric in self.metrics if metric.code == code)
        except StopIteration as error:
            raise ValueError(f"macro snapshot is missing required metric: {code}") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "label": self.label,
            "as_of": self.as_of.isoformat(),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "methodology_sources": [list(item) for item in self.methodology_sources],
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MacroSnapshot:
        raw_metrics = value.get("metrics")
        raw_sources = value.get("methodology_sources")
        if not isinstance(raw_metrics, list) or any(
            not isinstance(item, dict) for item in raw_metrics
        ):
            raise ValueError("macro snapshot metrics must be an object list")
        if not isinstance(raw_sources, list) or any(
            not isinstance(item, list) or len(item) != 2 for item in raw_sources
        ):
            raise ValueError("macro methodology_sources must contain name/url pairs")
        return cls(
            version=str(value["version"]),
            label=str(value["label"]),
            as_of=date.fromisoformat(str(value["as_of"])),
            metrics=tuple(MacroMetric.from_dict(item) for item in raw_metrics),
            methodology_sources=tuple((str(item[0]), str(item[1])) for item in raw_sources),
            warnings=tuple(str(item) for item in value.get("warnings", [])),
        )
