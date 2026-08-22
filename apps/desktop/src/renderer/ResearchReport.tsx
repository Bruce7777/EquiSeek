import {
  AlertTriangle,
  BarChart3,
  BookOpenCheck,
  CalendarClock,
  Check,
  CircleDot,
  Database,
  ExternalLink,
  Gauge,
  Info,
  Layers3,
  ShieldAlert,
  ShieldCheck,
  TableProperties,
  Target,
  TrendingDown,
  TrendingUp,
  WifiOff,
} from 'lucide-react';
import { type ReactNode, useState } from 'react';
import { api } from './bridge';
import { MarkdownContent } from './MarkdownContent';

type AnyRecord = Record<string, any>;

const timeframeOrder = ['monthly', 'weekly', 'daily'] as const;

function value(input: unknown, digits = 2): string {
  if (input === null || input === undefined || input === '') return '—';
  const numeric = Number(input);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : String(input);
}

function signed(input: unknown): string {
  const numeric = Number(input || 0);
  return `${numeric >= 0 ? '+' : ''}${numeric}`;
}

function items(input: unknown): string[] {
  return Array.isArray(input) ? input.map(String) : [];
}

function DataProvenance({ result }: { result: AnyRecord }) {
  const snapshot = (result.snapshot || {}) as AnyRecord;
  const warnings = items(result.warnings || snapshot.warnings);
  return (
    <section className="data-provenance" aria-label="研究数据状态">
      <div className="provenance-grid">
        <div><Database size={15} /><span>来源<strong>{result.source || snapshot.source || '—'}</strong></span></div>
        <div><CalendarClock size={15} /><span>数据截止<strong>{result.asOf || snapshot.as_of || '—'}</strong></span></div>
        <div><Layers3 size={15} /><span>样本 / 复权<strong>{snapshot.bars ?? '—'} 日 K · {result.adjustment || snapshot.adjustment || '—'}</strong></span></div>
        <div><Gauge size={15} /><span>公式 / 缓存<strong>{snapshot.formula_version || result.chart?.formulaVersion || '—'} · {snapshot.cache_status || '—'}</strong></span></div>
      </div>
      {result.sourceKind === 'synthetic' && (
        <div className="source-warning"><WifiOff size={17} /><div><strong>离线合成演示数据，不可用于真实投资判断</strong><span>请在设置中允许联网，并使用 BaoStock/Tushare 重新运行后再评估。</span></div></div>
      )}
      {warnings.map((warning) => <div className="inline-warning" key={warning}><AlertTriangle size={14} />{warning}</div>)}
    </section>
  );
}

function DecisionOverview({ result }: { result: AnyRecord }) {
  const advice = (result.advice || {}) as AnyRecord;
  const context = (result.marketContext || advice.market_context || {}) as AnyRecord;
  const action = String(advice.action || 'wait');
  const zone = advice.action_zone_low == null || advice.action_zone_high == null
    ? '暂无动作参考区'
    : `${value(advice.action_zone_low, 4)} – ${value(advice.action_zone_high, 4)}`;
  const DirectionIcon = advice.direction === 'bullish' ? TrendingUp : advice.direction === 'bearish' ? TrendingDown : CircleDot;
  return (
    <section className={`decision-overview action-${action}`} data-testid="decision-card">
      <div className="decision-hero-row">
        <div className="decision-identity">
          <span className={`action-badge ${action}`}>{advice.action_label || '待确认'}</span>
          <div><span className="eyebrow">规则结论 · 截止 {result.asOf || advice.as_of || '—'}</span><h2>{result.symbol || advice.symbol}</h2></div>
        </div>
        <div className="confidence-ring" aria-label={`规则置信度 ${advice.confidence ?? '未知'}/100`}>
          <strong>{advice.confidence ?? '—'}</strong><span>/100</span><small>{advice.confidence_label || '规则置信度'}</small>
        </div>
      </div>
      <div className="decision-kpis">
        <div><span>大方向</span><strong><DirectionIcon size={15} />{advice.direction_label || '—'}</strong><small>排序分 {result.strategy?.direction_score ?? '—'}</small></div>
        <div><span>当前价</span><strong>{value(advice.current_price, 4)}</strong><small>动作参考区 {zone}</small></div>
        <div><span>市场共振</span><strong>{context.priority_label || context.status_label || '未加载'}</strong><small>买入门控 {context.buy_gate_open ? '已打开' : '未通过'}</small></div>
        <div><span>时机窗口</span><strong>{result.strategy?.timing?.label || '—'}</strong><small>触发强度 {result.strategy?.timing?.strength ?? '—'}/100</small></div>
      </div>
      <div className="confidence-breakdown" aria-label="置信度拆分">
        <div><span>技术规则</span><strong>{advice.technical_confidence ?? '—'}</strong><i style={{ width: `${Math.max(0, Math.min(100, Number(advice.technical_confidence || 0)))}%` }} /></div>
        <div><span>大盘/板块调整</span><strong>{signed(advice.market_confidence_adjustment)}</strong><i className={Number(advice.market_confidence_adjustment) < 0 ? 'negative' : ''} /></div>
        <div><span>宏观行业调整</span><strong>{signed(advice.macro_confidence_adjustment)}</strong><i className={Number(advice.macro_confidence_adjustment) < 0 ? 'negative' : ''} /></div>
      </div>
      <div className="invalidation-box"><ShieldAlert size={18} /><div><span>逻辑失效条件{advice.invalidation_price != null ? ` · 参考价 ${value(advice.invalidation_price, 4)}` : ''}</span><strong>{advice.invalidation_condition || '等待规则生成'}</strong></div></div>
    </section>
  );
}

function SnapshotMetrics({ result }: { result: AnyRecord }) {
  const snapshot = (result.snapshot || {}) as AnyRecord;
  const indicator = (snapshot.indicators || {}) as AnyRecord;
  const previous = Number(snapshot.previous_close);
  const latest = Number(snapshot.latest_close);
  const change = Number.isFinite(previous) && previous !== 0 ? ((latest - previous) / previous) * 100 : null;
  const groups = [
    { title: '价格与均线', values: [['收盘', snapshot.latest_close], ['日涨跌', change == null ? null : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`], ['MA5', indicator.MA5], ['MA10', indicator.MA10], ['MA20', indicator.MA20], ['MA30', indicator.MA30], ['MA60', indicator.MA60]] },
    { title: 'MACD', values: [['DIF', indicator.DIF], ['DEA', indicator.DEA], ['柱值', indicator.MACD]] },
    { title: '动量', values: [['K', indicator.K], ['D', indicator.D], ['J', indicator.J], ['RSI6', indicator.RSI6], ['RSI12', indicator.RSI12], ['RSI24', indicator.RSI24]] },
    { title: '波动与通道', values: [['ATR14', indicator.ATR14], ['ATR20', indicator.ATR20], ['BOLL 下', indicator.BOLL_LOWER], ['BOLL 中', indicator.BOLL_MID], ['BOLL 上', indicator.BOLL_UPPER]] },
    { title: 'WR 正向口径', values: [['WR6', indicator.WR6], ['WR10', indicator.WR10]] },
  ];
  return (
    <section className="report-section" data-testid="snapshot-metrics">
      <div className="report-section-head"><div><span className="eyebrow">MARKET SNAPSHOT</span><h3>完整指标快照</h3></div><span>Python 计算 · 不在前端补算</span></div>
      <div className="metric-groups">{groups.map((group) => <div className="metric-group" key={group.title}><h4>{group.title}</h4><div>{group.values.map(([label, metric]) => <span key={String(label)}><small>{label}</small><strong>{typeof metric === 'string' ? metric : value(metric, 2)}</strong></span>)}</div></div>)}</div>
    </section>
  );
}

function ForecastTable({ advice }: { advice: AnyRecord }) {
  const forecasts = Array.isArray(advice.forecasts) ? advice.forecasts : [];
  return (
    <section className="report-section" data-testid="forecast-table">
      <div className="report-section-head"><div><span className="eyebrow">SCENARIO FORECAST</span><h3>方向情景 · 不是收益承诺</h3></div><Info size={16} /></div>
      <div className="table-scroll"><table className="research-table"><thead><tr><th>周期</th><th>方向</th><th>测度</th><th>历史收益</th><th>ATR 风险区间</th><th>依据口径</th></tr></thead><tbody>{forecasts.map((forecast: AnyRecord) => {
        const measure = forecast.probability_pct != null
          ? `命中率 ${value(forecast.probability_pct)}% · 样本 ${forecast.sample_count ?? 0}`
          : `上涨情景分 ${value(forecast.scenario_score)}/100 · 非概率`;
        return <tr key={forecast.trading_days}><td><strong>{forecast.trading_days} 日</strong></td><td><span className={`direction-pill ${forecast.direction || ''}`}>{forecast.direction_label || '—'}</span></td><td>{measure}</td><td>{forecast.expected_return_pct == null ? '无可靠收益估计' : `${signed(value(forecast.expected_return_pct))}%`}</td><td>{forecast.price_range_low == null ? 'ATR 数据不足' : `${value(forecast.price_range_low, 4)} – ${value(forecast.price_range_high, 4)}`}</td><td><small>{forecast.basis_label || forecast.basis || '—'}</small></td></tr>;
      })}</tbody></table></div>
    </section>
  );
}

function TimeframeMatrix({ strategy }: { strategy: AnyRecord }) {
  const macd = (strategy.macd || {}) as AnyRecord;
  const wr = (strategy.wr || {}) as AnyRecord;
  return (
    <section className="report-section" data-testid="timeframe-matrix">
      <div className="report-section-head"><div><span className="eyebrow">MULTI-TIMEFRAME</span><h3>月线 → 周线 → 日线状态矩阵</h3></div><TableProperties size={16} /></div>
      <div className="table-scroll"><table className="research-table timeframe-table"><thead><tr><th>周期</th><th>MACD 阶段</th><th>DIF / DEA / 柱</th><th>交叉 / 零轴</th><th>WR10 / 区间</th><th>正式信号截止</th></tr></thead><tbody>{timeframeOrder.map((key) => {
        const m = (macd[key] || {}) as AnyRecord;
        const w = (wr[key] || {}) as AnyRecord;
        return <tr key={key}><td><strong>{m.label || w.label || key}</strong>{m.provisional_excluded && <span className="provisional-pill">形成中已排除</span>}</td><td>{m.phase_label || '数据不足'}</td><td><code>{value(m.dif, 3)} / {value(m.dea, 3)} / {value(m.histogram, 3)}</code></td><td>{m.cross === 'death' ? '死叉' : m.cross === 'golden' ? '金叉' : '无新交叉'} · {m.zero_zone === 'above_zero' ? '零轴上' : m.zero_zone === 'below_zero' ? '零轴下' : '零轴附近'}</td><td>{value(w.value)} / {w.zone_label || '—'}</td><td>{m.as_of || w.as_of || '—'}{m.provisional_excluded && <small>最新形成中 {m.latest_available_as_of}</small>}</td></tr>;
      })}</tbody></table></div>
      <p className="method-note">月线锚定大方向、周线确认延续或调整、日线辅助执行；未收盘周/月周期不会混入正式信号。</p>
    </section>
  );
}

function DecisionPath({ strategy }: { strategy: AnyRecord }) {
  const path = Array.isArray(strategy.decision_path) ? strategy.decision_path : [];
  return (
    <section className="report-section" data-testid="decision-path">
      <div className="report-section-head"><div><span className="eyebrow">DECISION GATES</span><h3>五级决策链</h3></div><Target size={16} /></div>
      <div className="decision-path">{path.map((step: AnyRecord, index: number) => <article className={`decision-step ${step.status || 'warn'}`} key={step.key || index}><div className="step-index">{step.status === 'satisfied' ? <Check size={15} /> : index + 1}</div><div><div className="step-title"><strong>{step.title}</strong><span>{step.status_label || step.status}</span></div><p>{step.summary}</p>{items(step.evidence).length > 0 && <small>{items(step.evidence).join('；')}</small>}</div></article>)}</div>
    </section>
  );
}

function EvidenceSections({ result }: { result: AnyRecord }) {
  const advice = (result.advice || {}) as AnyRecord;
  const context = (result.marketContext || {}) as AnyRecord;
  const sections = [
    { title: '决策逻辑', icon: BookOpenCheck, content: items(advice.thesis), tone: 'neutral' },
    { title: '触发证据', icon: Check, content: items(advice.evidence), tone: 'positive' },
    { title: '风险控制', icon: ShieldCheck, content: items(advice.risk_controls), tone: 'warning' },
    { title: '方法边界', icon: ShieldAlert, content: items(advice.limitations), tone: 'risk' },
  ];
  return (
    <section className="report-section evidence-report" data-testid="evidence-sections">
      <div className="report-section-head"><div><span className="eyebrow">EVIDENCE & BOUNDARY</span><h3>逻辑、证据与方法边界</h3></div></div>
      <div className="market-gate"><BarChart3 size={18} /><div><span>市场门控 · {context.status_label || '未加载'}</span><strong>{context.priority_label || '等待市场上下文'}</strong><p>{items(context.reasons).join('；') || '暂无市场门控理由。'}</p></div><span className={context.buy_gate_open ? 'gate-open' : 'gate-closed'}>{context.buy_gate_open ? '买入门控已打开' : '买入门控未通过'}</span></div>
      <div className="evidence-grid">{sections.map(({ title, icon: Icon, content, tone }) => <article className={`evidence-panel ${tone}`} key={title}><h4><Icon size={15} />{title}<span>{content.length}</span></h4>{content.length ? <ul>{content.map((line) => <li key={line}>{line}</li>)}</ul> : <p>暂无数据</p>}</article>)}</div>
    </section>
  );
}

export function ResearchReport({ result, chart }: { result: AnyRecord; chart: ReactNode }) {
  const [tab, setTab] = useState<'workbench' | 'markdown'>('workbench');
  const [artifactError, setArtifactError] = useState('');
  const advice = (result.advice || {}) as AnyRecord;
  const strategy = (result.strategy || {}) as AnyRecord;
  const htmlArtifact = (result.artifacts || []).find((item: AnyRecord) => item.media_type === 'text/html');
  const openArtifact = async () => {
    if (!htmlArtifact?.path) return;
    try {
      const message = await api.native.openPath(String(htmlArtifact.path));
      setArtifactError(message || '');
    } catch (reason) {
      setArtifactError(`无法打开 HTML 成果：${String(reason)}`);
    }
  };
  return (
    <div className="research-report" data-testid="research-report">
      <DataProvenance result={result} />
      <div className="report-tabs" role="tablist" aria-label="研究报告视图">
        <button role="tab" aria-selected={tab === 'workbench'} className={tab === 'workbench' ? 'active' : ''} onClick={() => setTab('workbench')}><Layers3 size={15} />决策工作台</button>
        <button role="tab" aria-selected={tab === 'markdown'} className={tab === 'markdown' ? 'active' : ''} onClick={() => setTab('markdown')}><BookOpenCheck size={15} />完整规则报告</button>
      </div>
      {tab === 'workbench' ? <div className="report-stack">
        <DecisionOverview result={result} />
        <SnapshotMetrics result={result} />
        {chart}
        <ForecastTable advice={advice} />
        <TimeframeMatrix strategy={strategy} />
        <DecisionPath strategy={strategy} />
        <EvidenceSections result={result} />
      </div> : <section className="full-markdown-report" data-testid="full-markdown-report"><div className="report-section-head"><div><span className="eyebrow">DETERMINISTIC REPORT</span><h3>{result.symbol} 完整规则研究报告</h3></div><div className="report-actions"><span>{result.answerMode === 'deepseek' ? 'AI 整理 · 事实仍来自 Python' : '本地确定性摘要'}</span>{htmlArtifact && <button className="secondary-button compact-button" onClick={openArtifact}><ExternalLink size={14} />打开本机 HTML 成果</button>}</div></div>{artifactError && <div className="artifact-error">{artifactError}</div>}<MarkdownContent>{String(result.summary || '暂无报告')}</MarkdownContent></section>}
    </div>
  );
}
