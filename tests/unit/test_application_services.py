from __future__ import annotations

import os
import subprocess
import sys
from datetime import date

import pytest

from aegisrun.application import services
from aegisrun.application.requests import ResearchRequest
from aegisrun.desktop import workers
from aegisrun.marketdata.models import AdjustmentMode
from aegisrun.marketdata.providers import DemoMarketDataProvider


def test_application_package_imports_without_qt_runtime() -> None:
    environment = dict(os.environ)
    environment.pop("QT_QPA_PLATFORM", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import aegisrun.application; "
            "assert not any(name.startswith('PySide6') for name in sys.modules)",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr


def test_desktop_workers_keep_request_import_compatibility() -> None:
    assert workers.ResearchRequest is ResearchRequest


def test_market_data_provider_factory_remains_framework_free() -> None:
    provider = services.market_data_provider("demo", None)

    assert isinstance(provider, DemoMarketDataProvider)
    with pytest.raises(ValueError, match="Tushare Token"):
        services.market_data_provider("tushare", None)


@pytest.mark.asyncio
async def test_research_service_owns_provider_lifecycle_and_blocks_stale_macro(
    monkeypatch, tmp_path,
) -> None:
    captured: dict[str, object] = {}

    class Provider:
        closed = False

        def close(self) -> None:
            self.closed = True

    provider = Provider()

    async def capture(*args, **kwargs):
        captured["provider"] = args[0]
        captured["macro_overlay"] = kwargs["macro_overlay"]
        captured["workspace_root"] = kwargs["workspace_root"]
        return "research-result"

    monkeypatch.setattr(services, "run_research", capture)
    request = ResearchRequest(
        source="demo",
        symbol="600519.SH",
        start_date=date(2025, 1, 1),
        end_date=date(2026, 8, 21),
        adjustment=AdjustmentMode.QFQ,
        tushare_token=None,
        deepseek_api_key=None,
        use_ai=False,
        industry="白酒",
        workspace_root=str(tmp_path),
    )

    result = await services.execute_research(
        request,
        provider=provider,  # type: ignore[arg-type]
    )

    assert result == "research-result"
    assert captured == {
        "provider": provider,
        "macro_overlay": None,
        "workspace_root": tmp_path,
    }
    assert provider.closed is True
