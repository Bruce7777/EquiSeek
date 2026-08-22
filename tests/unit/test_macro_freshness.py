from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import httpx
import pytest

from aegisrun.macro.freshness import (
    MacroSourceCheck,
    OfficialMacroFreshnessVerifier,
    assess_macro_freshness,
    extract_official_page_dates,
    snapshot_is_verified_current,
)
from aegisrun.macro.providers import BundledOfficialMacroProvider


def _check(
    key: str,
    latest: date | None,
    *,
    status: str = "succeeded",
) -> MacroSourceCheck:
    return MacroSourceCheck(
        key,
        key.upper(),
        f"https://{key}.gov.cn/releases/",
        status,  # type: ignore[arg-type]
        latest,
        "test",
    )


def test_newer_official_release_invalidates_even_recent_snapshot() -> None:
    snapshot = replace(BundledOfficialMacroProvider().load(), as_of=date(2026, 8, 10))
    checks = (
        _check("nbs", date(2026, 8, 17)),
        _check("pboc", date(2026, 8, 10)),
        _check("safe", date(2026, 8, 8)),
        _check("mof", date(2026, 8, 9)),
    )

    validity = assess_macro_freshness(
        snapshot,
        checks,
        today=date(2026, 8, 21),
        checked_at=datetime(2026, 8, 21, tzinfo=UTC),
    )

    assert validity.status == "stale"
    assert validity.current_decision_allowed is False
    assert validity.newer_release_count == 1
    assert "2026-08-17" in validity.reason


def test_old_snapshot_is_stale_even_when_network_checks_fail() -> None:
    snapshot = BundledOfficialMacroProvider().load()
    checks = tuple(_check(key, None, status="failed") for key in ("nbs", "pboc", "safe", "mof"))

    validity = assess_macro_freshness(snapshot, checks, today=date(2026, 8, 21))

    assert validity.status == "stale"
    assert validity.age_days == 52
    assert "超过 45 天" in validity.reason


def test_recent_snapshot_with_insufficient_official_checks_is_unverified() -> None:
    snapshot = replace(BundledOfficialMacroProvider().load(), as_of=date(2026, 8, 15))
    checks = (
        _check("nbs", date(2026, 8, 15)),
        _check("pboc", date(2026, 8, 14)),
        _check("safe", None, status="failed"),
        _check("mof", None, status="failed"),
    )

    validity = assess_macro_freshness(snapshot, checks, today=date(2026, 8, 21))

    assert validity.status == "unverified"
    assert validity.current_decision_allowed is False


def test_recent_snapshot_passes_only_with_sufficient_checks_and_no_new_release() -> None:
    snapshot = replace(BundledOfficialMacroProvider().load(), as_of=date(2026, 8, 15))
    checks = (
        _check("nbs", date(2026, 8, 15)),
        _check("pboc", date(2026, 8, 14)),
        _check("safe", date(2026, 8, 12)),
        _check("mof", None, status="failed"),
    )

    validity = assess_macro_freshness(snapshot, checks, today=date(2026, 8, 21))

    assert validity.status == "current"
    assert validity.current_decision_allowed is True


def test_date_parser_ignores_old_navigation_and_future_values() -> None:
    values = extract_official_page_dates(
        "2020-01-01 2026年8月17日 2026/08/09 2026-08-30",
        today=date(2026, 8, 21),
    )

    assert values == (date(2026, 8, 9), date(2026, 8, 17))


@pytest.mark.asyncio
async def test_official_verifier_records_each_source_and_detects_latest_date() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="<a>2026-08-09</a><time>2026年8月17日</time>",
            request=request,
        )

    verifier = OfficialMacroFreshnessVerifier(transport=httpx.MockTransport(handler))
    validity = await verifier.verify(
        BundledOfficialMacroProvider().load(), today=date(2026, 8, 21)
    )

    assert len(validity.source_checks) == 4
    assert all(item.status == "succeeded" for item in validity.source_checks)
    assert all(item.latest_published_on == date(2026, 8, 17) for item in validity.source_checks)
    assert validity.status == "stale"


@pytest.mark.asyncio
async def test_official_verifier_fails_closed_on_non_official_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "evil.example":
            return httpx.Response(200, text="2026-08-21", request=request)
        return httpx.Response(
            302,
            headers={"location": "https://evil.example/latest"},
            request=request,
        )

    verifier = OfficialMacroFreshnessVerifier(transport=httpx.MockTransport(handler))
    validity = await verifier.verify(
        replace(BundledOfficialMacroProvider().load(), as_of=date(2026, 8, 20)),
        today=date(2026, 8, 21),
    )

    assert validity.status == "unverified"
    assert all(item.status == "failed" for item in validity.source_checks)
    assert all("白名单域名" in item.detail for item in validity.source_checks)


@pytest.mark.asyncio
async def test_only_exact_snapshot_with_current_successful_verification_can_drive_overlay() -> None:
    today = date.today()
    snapshot = replace(BundledOfficialMacroProvider().load(), as_of=today)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=today.isoformat(), request=request)

    verifier = OfficialMacroFreshnessVerifier(transport=httpx.MockTransport(handler))
    validity = await verifier.verify(snapshot, today=today)

    assert validity.current_decision_allowed is True
    assert snapshot_is_verified_current(snapshot, reference_date=today) is True
    changed_snapshot = replace(snapshot, version=f"{snapshot.version}-changed")
    assert snapshot_is_verified_current(changed_snapshot, reference_date=today) is False


def test_age_guard_blocks_bundled_snapshot_from_current_stock_research() -> None:
    snapshot = BundledOfficialMacroProvider().load()

    assert snapshot_is_verified_current(snapshot, reference_date=date(2026, 8, 21)) is False
