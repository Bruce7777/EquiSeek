from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from html import escape

# The embedded stylesheet intentionally remains a readable, self-contained artifact.
# ruff: noqa: E501

_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_CODE = re.compile(r"`([^`\n]+)`")


def _inline(value: str) -> str:
    safe = escape(value, quote=True)
    safe = _BOLD.sub(r"<strong>\1</strong>", safe)
    return _CODE.sub(r"<code>\1</code>", safe)


def _markdown_blocks(value: str) -> str:
    """Render a deliberately small, escaped Markdown subset without raw HTML."""
    blocks: list[str] = []
    list_items: list[str] = []

    def flush_list() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(list_items) + "</ul>")
            list_items.clear()

    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            flush_list()
            continue
        if line.startswith("- "):
            list_items.append(f"<li>{_inline(line[2:])}</li>")
            continue
        flush_list()
        if line.startswith("### "):
            blocks.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            blocks.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            blocks.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.startswith("> "):
            blocks.append(f"<aside>{_inline(line[2:])}</aside>")
        else:
            blocks.append(f"<p>{_inline(line)}</p>")
    flush_list()
    return "".join(blocks)


def render_investment_html(
    *,
    title: str,
    goal: str,
    content: str,
    skills: Sequence[str] = (),
    trace: Sequence[Mapping[str, object]] = (),
    data_source: str = "",
    generated_at: datetime | None = None,
) -> str:
    """Create a portable local report with a strict no-script/no-remote-assets CSP."""
    moment = (generated_at or datetime.now(UTC)).astimezone()
    skill_markup = "".join(f'<span class="tag">{escape(item)}</span>' for item in skills)
    if not skill_markup:
        skill_markup = '<span class="muted">平台固定能力</span>'
    trace_markup = "".join(
        "<li>"
        f'<span class="state">{escape(str(item.get("status", "unknown")))}</span>'
        f"<strong>{escape(str(item.get('title', '研究步骤')))}</strong>"
        f"<p>{escape(str(item.get('summary', '')))}</p>"
        "</li>"
        for item in trace
    )
    if not trace_markup:
        trace_markup = '<li><span class="muted">本轮没有可展示的执行步骤</span></li>'
    source = escape(data_source or "本轮结构化研究上下文")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme:light; --ink:#1f2926; --muted:#6f7b76; --line:#e2e7e3; --accent:#238566; --soft:#eef7f3 }}
    * {{ box-sizing:border-box }} body {{ margin:0; background:#f7f8f6; color:var(--ink); font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif }}
    main {{ width:min(980px,calc(100% - 32px)); margin:32px auto 64px }} header,.card {{ background:#fff; border:1px solid var(--line); border-radius:16px; box-shadow:0 8px 30px rgba(27,44,37,.05) }}
    header {{ padding:34px 38px }} .eyebrow {{ color:var(--accent); font-size:12px; font-weight:800; letter-spacing:.12em }} h1 {{ margin:8px 0 12px; font-size:32px; line-height:1.2 }} h2 {{ margin:0 0 16px; font-size:20px }} h3 {{ margin:22px 0 8px; font-size:16px }} p {{ margin:8px 0 }}
    .meta,.muted {{ color:var(--muted) }} .grid {{ display:grid; grid-template-columns:minmax(0,1.6fr) minmax(280px,.8fr); gap:18px; margin-top:18px }} .card {{ padding:26px 28px }} .answer {{ min-width:0 }}
    aside {{ margin:10px 0; padding:11px 14px; background:var(--soft); border-left:3px solid var(--accent); border-radius:7px }} code,.tag,.state {{ font:12px ui-monospace,SFMono-Regular,Menlo,monospace }} code {{ padding:2px 5px; background:#f0f3f1; border-radius:5px }}
    .tag {{ display:inline-block; margin:0 6px 6px 0; padding:5px 8px; color:#17674f; background:var(--soft); border:1px solid #cfe7dd; border-radius:999px }} ol.timeline {{ list-style:none; margin:0; padding:0 }}
    .timeline li {{ position:relative; padding:0 0 20px 20px; border-left:1px solid #cfd8d3 }} .timeline li:last-child {{ padding-bottom:0 }} .timeline li:before {{ content:""; position:absolute; left:-5px; top:8px; width:9px; height:9px; background:var(--accent); border-radius:50% }}
    .timeline p {{ color:var(--muted); font-size:13px }} .state {{ float:right; color:var(--muted) }} footer {{ margin-top:18px; color:var(--muted); font-size:12px; text-align:center }} @media(max-width:760px) {{ .grid {{ grid-template-columns:1fr }} header,.card {{ padding:22px }} h1 {{ font-size:26px }} }}
  </style>
</head>
<body><main>
  <header><div class="eyebrow">EQUISEEK · LOCAL RESEARCH ARTIFACT</div><h1>{escape(title)}</h1><p>{escape(goal)}</p><p class="meta">生成时间 {escape(moment.strftime("%Y-%m-%d %H:%M:%S %Z"))} · 数据源 {source}</p></header>
  <section class="grid"><article class="card answer"><h2>研究结论</h2>{_markdown_blocks(content)}</article><aside class="card"><h2>任务上下文</h2><h3>本轮 Skill</h3><div>{skill_markup}</div><h3>可审阅执行链</h3><ol class="timeline">{trace_markup}</ol></aside></section>
  <footer>仅用于基于历史证据的投资研究，不保证收益、不连接券商、不自动下单。HTML 已转义且不包含脚本或远程资源。</footer>
</main></body></html>
"""
