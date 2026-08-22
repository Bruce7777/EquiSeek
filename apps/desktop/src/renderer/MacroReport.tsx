import {
  ArrowUpRight,
  BookOpenCheck,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  FileCode2,
  Network,
  ShieldAlert,
  ShieldCheck,
  Target,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { MarkdownContent } from './MarkdownContent';
import { api } from './bridge';

type AnyRecord = Record<string, any>;

const tabs = [
  ['overview', '决策总览'],
  ['allocation', '长期配置'],
  ['flows', '资本三流'],
  ['costs', '成本转嫁'],
  ['sectors', '行业配置'],
  ['sources', '数据与来源'],
  ['plan', 'Agent 计划'],
  ['report', '完整报告'],
] as const;

function list(value: unknown): AnyRecord[] {
  return Array.isArray(value) ? value : [];
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

function ScoreCard({ title, score, label, risk = false }: { title: string; score: number; label: string; risk?: boolean }) {
  return <article className={`macro-score-card ${risk ? 'risk' : ''}`}>
    <div><span>{title}</span><strong>{score}<small>/100</small></strong></div>
    <div className="score-track"><i style={{ width: `${Math.max(0, Math.min(100, score))}%` }} /></div>
    <p>{label}</p>
  </article>;
}

function BulletList({ values, empty = '暂无记录' }: { values: unknown; empty?: string }) {
  const items = strings(values);
  return items.length ? <ul className="macro-bullets">{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p className="muted">{empty}</p>;
}

export function MacroReport({ result }: { result: AnyRecord }) {
  const [tab, setTab] = useState<(typeof tabs)[number][0]>('overview');
  const [amount, setAmount] = useState(100000);
  const [artifactError, setArtifactError] = useState('');
  const analysis = (result.analysis || {}) as AnyRecord;
  const validity = (result.validity || {}) as AnyRecord;
  const snapshot = (analysis.snapshot || {}) as AnyRecord;
  const flow = (analysis.capital_flow || {}) as AnyRecord;
  const transfer = (analysis.cost_transfer || {}) as AnyRecord;
  const view = (analysis.investment_view || {}) as AnyRecord;
  const plans = list(view.allocation_plans);
  const [profile, setProfile] = useState(String(view.default_allocation_profile || plans[0]?.profile || ''));
  const plan = useMemo(() => plans.find((item) => String(item.profile) === profile) || plans[0] || {}, [plans, profile]);
  const checks = list(validity.source_checks);
  const succeeded = checks.filter((item) => item.status === 'succeeded').length;
  const decisionAllowed = Boolean(validity.current_decision_allowed);
  const directionScore = Math.max(0, Math.min(100, Number(flow.direction_score || 0)));
  const equityTarget = list(plan.targets).filter((item) => item.asset_class === 'equity').reduce((sum, item) => sum + Number(item.target_pct || 0), 0);
  const htmlArtifact = list(result.artifacts).find((item) => item.media_type === 'text/html');

  const openSource = (url: string) => {
    if (/^https:\/\//.test(url)) window.open(url, '_blank', 'noopener,noreferrer');
  };
  const openArtifact = async () => {
    if (!htmlArtifact?.path) return;
    try {
      const message = await api.native.openPath(String(htmlArtifact.path));
      setArtifactError(message || '');
    } catch (reason) {
      setArtifactError(`无法打开 HTML 成果：${String(reason)}`);
    }
  };

  return <div className="macro-report" data-testid="macro-report">
    <section className={`macro-validity-hero ${validity.status || 'unverified'}`} aria-label="宏观研究有效性">
      <div className="validity-icon">{decisionAllowed ? <ShieldCheck size={25} /> : <ShieldAlert size={25} />}</div>
      <div className="validity-copy">
        <span className="eyebrow">MACRO LIVE REFRESH · 官方最新正文抓取与时效核验</span>
        <h2>{validity.status_label || '宏观结论尚未核验'}</h2>
        <p>{validity.reason || '必须完成官方来源核验后才能用于当前投资决策。'}</p>
      </div>
      <dl className="validity-meta">
        <div><dt>证据采集截止</dt><dd>{snapshot.as_of || validity.snapshot_as_of || '—'}</dd></div>
        <div><dt>快照年龄</dt><dd>{validity.age_days ?? '—'} 天</dd></div>
        <div><dt>官方站点</dt><dd>{succeeded} / {checks.length || '—'}</dd></div>
        <div><dt>当前决策</dt><dd>{decisionAllowed ? '允许' : '已阻止'}</dd></div>
      </dl>
    </section>

    <div className="macro-boundary" role="note">
      <CircleAlert size={17} />
      <p><strong>数据边界：</strong>本次联网会动态发现官方最新发布、抓取正文并更新结构化指标；<code>{snapshot.version || '未加载'}</code> 的采集截止为 {snapshot.as_of || '—'}，每项指标仍保留自己的真实统计期。{decisionAllowed ? '当前已完成重算并通过时效门禁。' : '本次抓取或完整性校验未通过，下方结论不作为今天的仓位建议。'}</p>
    </div>

    <div className="macro-tabs" role="tablist" aria-label="宏观研究报告章节">
      {tabs.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>)}
    </div>

    {tab === 'overview' && <div className="macro-tab-panel" role="tabpanel">
      <div className="macro-section-heading"><div><span className="eyebrow">MACRO DASHBOARD</span><h2>{analysis.regime || '宏观投资仪表盘'}</h2></div><span>{snapshot.label || '官方宏观基线'}</span></div>
      <div className="macro-score-grid" data-testid="macro-scores">
        <ScoreCard title="资本流量" score={Number(flow.volume_score || 0)} label={String(flow.volume_label || '—')} />
        <ScoreCard title="资本流向" score={directionScore} label={String(flow.direction_label || '—')} />
        <ScoreCard title="资本流速" score={Number(flow.speed_score || 0)} label={String(flow.speed_label || '—')} />
        <ScoreCard title="实体传导" score={Number(flow.transmission_score || 0)} label={String(flow.transmission_label || '—')} />
        <ScoreCard title="成本转嫁压力" score={Number(transfer.pressure_score || 0)} label={String(transfer.pressure_label || '—')} risk />
        <ScoreCard title="权益风险偏好" score={Number(view.risk_appetite_score || 0)} label={String(view.risk_appetite_label || '—')} />
      </div>
      <div className="macro-two-column">
        <section className="macro-panel decision"><div className="macro-panel-title"><Target size={17} /><h3>{decisionAllowed ? '当前资产配置结论' : '历史模型结论（当前已停用）'}</h3></div><p className="macro-lead">{view.equity_exposure || '暂无配置结论'}</p><BulletList values={view.decision_summary} /></section>
        <section className="macro-panel warning"><div className="macro-panel-title"><CircleAlert size={17} /><h3>当前传导堵点</h3></div><BulletList values={flow.bottlenecks} /></section>
      </div>
      <div className="macro-two-column">
        <section className="macro-panel"><div className="macro-panel-title"><BookOpenCheck size={17} /><h3>研究含义</h3></div><BulletList values={analysis.research_implications} /></section>
        <section className="macro-panel"><div className="macro-panel-title"><ShieldAlert size={17} /><h3>限制与失效边界</h3></div><BulletList values={analysis.limitations} /></section>
      </div>
    </div>}

    {tab === 'allocation' && <div className="macro-tab-panel" role="tabpanel" data-testid="macro-allocation">
      <div className="macro-section-heading"><div><span className="eyebrow">LONG-TERM ALLOCATION</span><h2>{decisionAllowed ? '长期资产配置' : '历史配置回放'}</h2></div><span>不使用杠杆 · 不自动下单</span></div>
      {!decisionAllowed && <div className="macro-stop"><ShieldAlert size={18} /><div><strong>执行已停止</strong><p>旧模型如何形成配置仍可审阅，但不代表今天应采用该仓位。更新同口径结构化数据并通过官方门禁后再计算。</p></div></div>}
      <div className="allocation-controls">
        <label><span>风险画像</span><select aria-label="长期配置风险画像" value={profile} onChange={(event) => setProfile(event.target.value)}>{plans.map((item) => <option value={item.profile} key={item.profile}>{item.label}</option>)}</select></label>
        <label><span>可投资金额（不含应急金）</span><input aria-label="可投资金额" type="number" min="10000" step="10000" value={amount} onChange={(event) => setAmount(Math.max(0, Number(event.target.value)))} /></label>
        <div className="allocation-equity"><span>权益目标</span><strong>{equityTarget}%</strong><small>{plan.strategy_version || '—'}</small></div>
      </div>
      <section className="macro-panel allocation-summary"><h3>{plan.label || '尚无配置方案'}</h3><p>{plan.suitability}</p><div><span>{plan.horizon}</span><span>{plan.drawdown_tolerance}</span></div></section>
      <div className="macro-table-wrap"><table><thead><tr><th>资产桶</th><th>战略基准</th><th>目标</th><th>允许区间</th><th>对应金额</th><th>动作</th><th>实现方式</th><th>配置原因与风险</th></tr></thead><tbody>{list(plan.targets).map((item) => <tr key={item.key}><td><strong>{item.label}</strong></td><td>{item.strategic_pct}%</td><td>{item.target_pct}%</td><td>{item.minimum_pct}%–{item.maximum_pct}%</td><td>¥{Math.round(amount * Number(item.target_pct || 0) / 100).toLocaleString()}</td><td><span className="neutral-pill">{item.action_label}</span></td><td>{item.vehicles}</td><td>{item.purpose}；{item.macro_rationale}<small>风险：{item.primary_risk}</small></td></tr>)}</tbody></table></div>
      <h3 className="macro-subheading">分批建立步骤</h3>
      <div className="allocation-steps">{list(plan.build_steps).map((item) => <article key={item.order}><span>第 {item.order} 批 · {item.timing}</span><strong>¥{Math.round(amount * Number(item.portfolio_pct || 0) / 100).toLocaleString()} <small>({item.portfolio_pct}%)</small></strong><p>{item.instruction}</p><small>执行前：{item.gate}</small></article>)}</div>
      <div className="macro-three-column"><section className="macro-panel"><h3>再平衡规则</h3><BulletList values={plan.rebalance_rules} /></section><section className="macro-panel"><h3>加风险 / 降风险</h3><h4>加风险</h4><BulletList values={plan.increase_risk_triggers} /><h4>降风险</h4><BulletList values={plan.decrease_risk_triggers} /></section><section className="macro-panel"><h3>组合护栏</h3><p>{plan.prerequisite}</p><BulletList values={plan.guardrails} /></section></div>
    </div>}

    {tab === 'flows' && <div className="macro-tab-panel" role="tabpanel" data-testid="capital-flow-paths">
      <div className="macro-section-heading"><div><span className="eyebrow">CAPITAL THREE FLOWS</span><h2>资本流量、流向、流速与实体传导</h2></div><span>{list(flow.paths).length} 条可核验路径</span></div>
      <p className="macro-theory">流量回答“钱有多少”，流向回答“流到哪里”，流速回答“周转是否活跃”；实体传导单列，避免把宽货币直接当作景气。</p>
      <div className="flow-path-list">{list(flow.paths).map((item) => <article key={item.name}><div className="flow-path-head"><div><span>{item.dimension}</span><h3>{item.name}</h3></div><strong>{item.status} · {item.score}/100</strong></div><div className="flow-route"><span>{item.source}</span><i>→</i><span>{item.channel}</span><i>→</i><span>{item.destination}</span></div><p>{item.investment_effect}</p><BulletList values={item.evidence} /></article>)}</div>
      <section className="macro-panel"><h3>配置证据</h3><BulletList values={flow.allocation_evidence} /></section>
    </div>}

    {tab === 'costs' && <div className="macro-tab-panel" role="tabpanel" data-testid="cost-transfer-chains">
      <div className="macro-section-heading"><div><span className="eyebrow">COST TRANSFER</span><h2>成本转嫁来源、通道与承接者</h2></div><span>{list(transfer.chains).length} 条压力链</span></div>
      <p className="macro-theory">识别代价从哪里产生、通过什么制度/价格/资产负债表通道转移、由谁承担、谁相对受益。评分是可验证代理，不是收益预测。</p>
      <div className="macro-table-wrap"><table><thead><tr><th>成本转嫁链</th><th>压力</th><th>成本来源</th><th>传导通道</th><th>承接主体</th><th>相对受益者</th><th>投资含义</th><th>验证 / 反转</th></tr></thead><tbody>{list(transfer.chains).map((item) => <tr key={item.name}><td><strong>{item.name}</strong></td><td>{item.pressure_score}/100</td><td>{item.source}</td><td>{item.channel}</td><td>{item.bearer}</td><td>{item.beneficiary}</td><td>{item.investment_effect}</td><td>{item.confirmation}<small>反转：{item.reversal_conditions}</small></td></tr>)}</tbody></table></div>
      <div className="macro-two-column"><section className="macro-panel"><h3>主要通道</h3><BulletList values={transfer.channels} /></section><section className="macro-panel"><h3>缓冲因素</h3><BulletList values={transfer.offsets} /></section></div>
    </div>}

    {tab === 'sectors' && <div className="macro-tab-panel" role="tabpanel" data-testid="macro-sectors">
      <div className="macro-section-heading"><div><span className="eyebrow">SECTOR ALLOCATION</span><h2>行业配置与确认条件</h2></div><span>{strings(view.style_tilt).join(' · ')}</span></div>
      <div className="sector-grid">{list(view.sectors).map((item) => <article key={item.sector}><div><h3>{item.sector}</h3><span className={`stance ${item.stance}`}>{item.stance_label}</span></div><strong>{item.confidence}/100</strong><p>{item.rationale}</p><dl><div><dt>确认条件</dt><dd>{item.confirmation}</dd></div><div><dt>主要风险</dt><dd>{item.risk}</dd></div></dl></article>)}</div>
    </div>}

    {tab === 'sources' && <div className="macro-tab-panel" role="tabpanel" data-testid="macro-sources">
      <div className="macro-section-heading"><div><span className="eyebrow">DATA PROVENANCE</span><h2>最新结构化指标与官方来源</h2></div><span>{list(snapshot.metrics).length} 项指标</span></div>
      <div className="macro-source-checks"><h3>本次官方正文采集与时效核验</h3>{checks.map((item) => <article key={item.key || item.name}><div>{item.status === 'succeeded' ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}<strong>{item.name || item.key}</strong><span>{item.status === 'succeeded' ? '已采集' : '失败'}</span></div><p>{item.detail || '未返回说明'}</p><button onClick={() => openSource(String(item.url || ''))}>最新发布 {item.latest_published_on || '未识别'} <ArrowUpRight size={13} /></button></article>)}</div>
      {strings(snapshot.warnings).length > 0 && <section className="macro-panel"><h3>刷新说明</h3><BulletList values={snapshot.warnings} /></section>}
      <div className="macro-table-wrap"><table><thead><tr><th>指标</th><th>数值</th><th>统计期</th><th>来源</th><th>口径说明</th></tr></thead><tbody>{list(snapshot.metrics).map((item) => <tr key={item.code}><td><strong>{item.name}</strong><small>{item.code}</small></td><td>{item.value} {item.unit}</td><td>{item.period}</td><td><button className="table-link" onClick={() => openSource(String(item.source_url || ''))}>{item.source_name}<ArrowUpRight size={12} /></button></td><td>{item.note || '—'}</td></tr>)}</tbody></table></div>
      <section className="macro-panel"><h3>方法来源</h3>{list(snapshot.methodology_sources).map((item, index) => <button className="method-source" key={`${item[0]}-${index}`} onClick={() => openSource(String(item[1] || ''))}>{item[0]}<ArrowUpRight size={14} /></button>)}</section>
    </div>}

    {tab === 'plan' && <div className="macro-tab-panel" role="tabpanel" data-testid="macro-agent-plan">
      <div className="macro-section-heading"><div><span className="eyebrow">AGENT EXECUTION</span><h2>宏观 Agent 任务计划</h2></div><span>{result.plan?.status || '—'} · {result.workspace || '本地工作区'}</span></div>
      <div className="macro-plan-list">{list(result.plan?.tasks).map((item, index) => <article key={item.id || index}><span>{String(index + 1).padStart(2, '0')}</span><div><div><h3>{item.title || item.id}</h3><strong>{item.status}</strong></div><p>{item.agent}</p><div className="plan-skills">{strings(item.skills).map((skill) => <i key={skill}>{skill}</i>)}</div></div></article>)}</div>
      <div className="macro-plan-note"><Network size={17} /><p>页面展示可审计的任务摘要、工具、Skill 与依赖关系，不展示模型隐藏思维链。</p></div>
    </div>}

    {tab === 'report' && <div className="macro-tab-panel" role="tabpanel" data-testid="macro-markdown-report">
      <div className="macro-section-heading"><div><span className="eyebrow">DETERMINISTIC OUTPUT</span><h2>完整宏观规则报告</h2></div>{htmlArtifact ? <button className="secondary-button compact-button" onClick={openArtifact}><ExternalLink size={14} />打开本机 HTML 成果</button> : <FileCode2 size={20} />}</div>
      {artifactError && <div className="artifact-error">{artifactError}</div>}
      <div className="full-markdown"><MarkdownContent>{String(result.report || '# 暂无完整报告\n宏观流水线尚未返回 Markdown。')}</MarkdownContent></div>
    </div>}
  </div>;
}
