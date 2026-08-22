from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Lock
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx

from aegisrun.core.security import canonical_hash
from aegisrun.macro.models import MacroSnapshot

MacroValidityStatus = Literal["current", "stale", "unverified"]
SourceCheckStatus = Literal["succeeded", "failed"]

MAX_OFFICIAL_PAGE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_AGE_DAYS = 45
MIN_SUCCESSFUL_SOURCE_CHECKS = 3
_VALIDITY_CACHE: dict[str, MacroValidity] = {}
_VALIDITY_CACHE_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class OfficialMacroSource:
    key: str
    name: str
    url: str
    hostname: str


OFFICIAL_MACRO_SOURCES = (
    OfficialMacroSource(
        "nbs",
        "国家统计局",
        "https://www.stats.gov.cn/sj/zxfb/",
        "stats.gov.cn",
    ),
    OfficialMacroSource(
        "pboc",
        "中国人民银行",
        "https://www.pbc.gov.cn/diaochatongjisi/116219/index.html",
        "pbc.gov.cn",
    ),
    OfficialMacroSource(
        "safe",
        "国家外汇管理局",
        "https://www.safe.gov.cn/safe/tjsj1/",
        "safe.gov.cn",
    ),
    OfficialMacroSource(
        "mof",
        "财政部",
        "https://gks.mof.gov.cn/tongjishuju/",
        "mof.gov.cn",
    ),
)


@dataclass(frozen=True, slots=True)
class MacroSourceCheck:
    key: str
    name: str
    url: str
    status: SourceCheckStatus
    latest_published_on: date | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["latest_published_on"] = (
            self.latest_published_on.isoformat() if self.latest_published_on else None
        )
        return value


@dataclass(frozen=True, slots=True)
class MacroValidity:
    status: MacroValidityStatus
    checked_at: datetime
    snapshot_as_of: date
    age_days: int
    max_age_days: int
    current_decision_allowed: bool
    reason: str
    source_checks: tuple[MacroSourceCheck, ...]
    newer_release_count: int

    @property
    def status_label(self) -> str:
        return {
            "current": "官方发布核验通过",
            "stale": "当前结论已失效",
            "unverified": "当前结论不可用",
        }[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "status_label": self.status_label,
            "checked_at": self.checked_at.isoformat(),
            "snapshot_as_of": self.snapshot_as_of.isoformat(),
            "age_days": self.age_days,
            "max_age_days": self.max_age_days,
            "current_decision_allowed": self.current_decision_allowed,
            "reason": self.reason,
            "source_checks": [item.to_dict() for item in self.source_checks],
            "newer_release_count": self.newer_release_count,
        }


class MacroFreshnessVerifier(Protocol):
    async def verify(
        self, snapshot: MacroSnapshot, *, today: date | None = None
    ) -> MacroValidity: ...


def assess_macro_freshness(
    snapshot: MacroSnapshot,
    checks: tuple[MacroSourceCheck, ...],
    *,
    today: date,
    checked_at: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> MacroValidity:
    age_days = max(0, (today - snapshot.as_of).days)
    newer = tuple(
        item
        for item in checks
        if item.status == "succeeded"
        and item.latest_published_on is not None
        and item.latest_published_on > snapshot.as_of
    )
    succeeded = sum(item.status == "succeeded" for item in checks)
    checked = checked_at or datetime.now(UTC)

    if newer or age_days > max_age_days:
        reasons: list[str] = []
        if newer:
            latest = max(
                item.latest_published_on
                for item in newer
                if item.latest_published_on is not None
            )
            reasons.append(
                f"检测到 {len(newer)} 个官方来源在快照之后发布内容，最晚 {latest.isoformat()}"
            )
        if age_days > max_age_days:
            reasons.append(f"快照距今天 {age_days} 天，超过 {max_age_days} 天时效上限")
        status: MacroValidityStatus = "stale"
        reason = "；".join(reasons)
    elif succeeded < MIN_SUCCESSFUL_SOURCE_CHECKS:
        status = "unverified"
        reason = (
            f"仅 {succeeded}/{len(checks)} 个官方来源完成联网核验，"
            f"少于 {MIN_SUCCESSFUL_SOURCE_CHECKS} 个可靠性门槛"
        )
    else:
        status = "current"
        reason = (
            f"{succeeded}/{len(checks)} 个官方来源完成核验，未发现晚于快照截止日的发布，"
            f"且快照年龄为 {age_days} 天"
        )

    return MacroValidity(
        status=status,
        checked_at=checked,
        snapshot_as_of=snapshot.as_of,
        age_days=age_days,
        max_age_days=max_age_days,
        current_decision_allowed=status == "current",
        reason=reason,
        source_checks=checks,
        newer_release_count=len(newer),
    )


def _snapshot_key(snapshot: MacroSnapshot) -> str:
    return canonical_hash(snapshot.to_dict())


def remember_macro_validity(snapshot: MacroSnapshot, validity: MacroValidity) -> None:
    with _VALIDITY_CACHE_LOCK:
        _VALIDITY_CACHE[_snapshot_key(snapshot)] = validity


def snapshot_is_verified_current(
    snapshot: MacroSnapshot,
    *,
    reference_date: date,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> bool:
    """Allow overlays only after the exact snapshot passed today's official-source gate."""

    if not 0 <= (reference_date - snapshot.as_of).days <= max_age_days:
        return False
    with _VALIDITY_CACHE_LOCK:
        validity = _VALIDITY_CACHE.get(_snapshot_key(snapshot))
    return bool(
        validity is not None
        and validity.current_decision_allowed
        and validity.checked_at.astimezone().date() == date.today()
    )


_ISO_DATE = re.compile(r"(?<!\d)(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])(?!\d)")
_CN_DATE = re.compile(r"(?<!\d)(20\d{2})年\s*(0?[1-9]|1[0-2])月\s*(0?[1-9]|[12]\d|3[01])日")


def extract_official_page_dates(text: str, *, today: date) -> tuple[date, ...]:
    values: set[date] = set()
    earliest = today - timedelta(days=730)
    latest = today + timedelta(days=1)
    for pattern in (_ISO_DATE, _CN_DATE):
        for match in pattern.finditer(text):
            try:
                value = date(*(int(part) for part in match.groups()))
            except ValueError:
                continue
            if earliest <= value <= latest:
                values.add(value)
    return tuple(sorted(values))


class OfficialMacroFreshnessVerifier:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 12.0,
        sources: tuple[OfficialMacroSource, ...] = OFFICIAL_MACRO_SOURCES,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.sources = sources

    async def verify(
        self, snapshot: MacroSnapshot, *, today: date | None = None
    ) -> MacroValidity:
        reference_date = today or date.today()
        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "EquiSeek/0.1 macro-freshness-verifier"},
        ) as client:
            checks = await asyncio.gather(
                *(self._check(client, source, reference_date) for source in self.sources)
            )
        validity = assess_macro_freshness(snapshot, tuple(checks), today=reference_date)
        remember_macro_validity(snapshot, validity)
        return validity

    async def _check(
        self,
        client: httpx.AsyncClient,
        source: OfficialMacroSource,
        today: date,
    ) -> MacroSourceCheck:
        try:
            response = await client.get(source.url)
            response.raise_for_status()
            hostname = (urlparse(str(response.url)).hostname or "").lower()
            if hostname != source.hostname and not hostname.endswith(f".{source.hostname}"):
                raise ValueError("官方页面重定向到了非白名单域名")
            if len(response.content) > MAX_OFFICIAL_PAGE_BYTES:
                raise ValueError("官方页面超过 2 MiB 安全读取上限")
            dates = extract_official_page_dates(response.text, today=today)
            latest = dates[-1] if dates else None
            detail = (
                f"页面可访问，识别到最近发布日期 {latest.isoformat()}"
                if latest
                else "页面可访问，但未识别到结构化发布日期"
            )
            return MacroSourceCheck(
                source.key, source.name, source.url, "succeeded", latest, detail
            )
        except (httpx.HTTPError, ValueError, UnicodeError) as error:
            return MacroSourceCheck(
                source.key,
                source.name,
                source.url,
                "failed",
                detail=f"{type(error).__name__}: {str(error)[:180]}",
            )
