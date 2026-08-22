from datetime import UTC, datetime

from aegisrun.artifacts.html_report import render_investment_html


def test_html_report_escapes_untrusted_content_and_has_no_remote_capability() -> None:
    report = render_investment_html(
        title='600050.SH <script>alert("title")</script>',
        goal='<img src="https://tracker.invalid/x"> 什么时候买入',
        content='## 结论\n\n<script>alert("answer")</script>\n\n- **等待** `WR` 触发',
        skills=("user-skill",),
        trace=(
            {
                "title": '<img src=x onerror="alert(1)">',
                "status": "succeeded",
                "summary": "本地证据",
            },
        ),
        data_source="demo",
        generated_at=datetime(2026, 8, 21, 8, 0, tzinfo=UTC),
    )

    assert "<script" not in report
    assert "<img" not in report
    assert "&lt;script&gt;" in report
    assert "default-src 'none'" in report
    assert "https://tracker.invalid" in report
    assert "<strong>等待</strong>" in report
    assert "<code>WR</code>" in report
