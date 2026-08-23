import {
  Activity,
  Archive,
  ArrowUpRight,
  BarChart3,
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  CircleDot,
  ExternalLink,
  FileCode2,
  FolderOpen,
  GitFork,
  Globe2,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  MessageSquare,
  Network,
  PanelRightClose,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Square,
  Target,
  Terminal,
  Trash2,
  TrendingUp,
  Upload,
  Save,
  UsersRound,
  WandSparkles,
  WifiOff,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { AttachmentSelection, BootstrapData, ConversationSummary, CredentialStatus, PortfolioBook, RunEvent, RunView, SkillDetail, SkillSummary, WorkspaceSummary } from '../shared/contracts';
import { api } from './bridge';
import { REPOSITORY_URL } from '../shared/contracts';
import { MarkdownContent } from './MarkdownContent';
import { MacroReport } from './MacroReport';
import { ResearchReport } from './ResearchReport';

type View = 'agent' | 'research' | 'portfolio' | 'candidates' | 'macro' | 'skills' | 'settings';
type AnyRecord = Record<string, any>;
type RunContext = { run: RunView | null; events: RunEvent[]; goal: string };

const navigation = [
  { id: 'agent', label: '投研助手', icon: Bot },
  { id: 'research', label: '个股研究', icon: BarChart3 },
  { id: 'candidates', label: '候选池', icon: ListChecks },
  { id: 'portfolio', label: '持仓与自选', icon: LayoutDashboard },
  { id: 'macro', label: '宏观核验', icon: Globe2 },
  { id: 'skills', label: 'Skills', icon: WandSparkles },
  { id: 'settings', label: '设置', icon: Settings },
] as const;

const examples = [
  '研究一下 600050.SH 什么时候可以买入',
  '扫描我的候选池，按风险收益排序',
  '结合最新宏观数据检查通信板块判断是否失效',
];

const modelOptions = [
  { id: 'deepseek-v4-flash', name: 'V4 Flash', detail: '快速·经济' },
  { id: 'deepseek-v4-pro', name: 'V4 Pro', detail: '复杂推理' },
  { id: 'deepseek-v4-flash-vision-exp', name: 'V4 Flash Vision Exp', detail: '自定义端点·图文实验' },
] as const;

function activeWorkspace(items: WorkspaceSummary[]): WorkspaceSummary | undefined {
  return items.find((item) => item.active) || items[0];
}

function statusLabel(status: string): string {
  return { running: '运行中', queued: '排队中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }[status] || status;
}

function journalPercent(value: unknown): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
}

function journalPrice(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number.toFixed(4) : '—';
}

function AppLogo() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  );
}

function EmptyState({ icon: Icon, title, description }: { icon: typeof Bot; title: string; description: string }) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon size={22} /></div>
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

function StockChart({ result }: { result: AnyRecord }) {
  const chart = result.chart as AnyRecord | undefined;
  const data = useMemo(() => {
    if (!chart?.bars) return [];
    return chart.bars.map((bar: AnyRecord, index: number) => ({
      date: String(bar.date).slice(5),
      close: Number(bar.close),
      ma5: chart.ma5?.[index],
      ma20: chart.ma20?.[index],
    }));
  }, [chart]);
  if (!data.length) return null;
  return (
    <div className="chart-card" data-testid="research-chart">
      <div className="card-heading compact">
        <div><span className="eyebrow">价格与趋势</span><h3>日线 · 前复权</h3></div>
        <div className="chart-legend"><span className="close-dot" />收盘 <span className="ma5-dot" />MA5 <span className="ma20-dot" />MA20</div>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <AreaChart data={data} margin={{ top: 12, right: 10, left: -18, bottom: 0 }}>
          <defs>
            <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#176b53" stopOpacity={0.18} />
              <stop offset="100%" stopColor="#176b53" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#e6e4dd" strokeDasharray="3 5" vertical={false} />
          <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#77756d' }} axisLine={false} tickLine={false} minTickGap={28} />
          <YAxis domain={['dataMin - 0.08', 'dataMax + 0.08']} tick={{ fontSize: 11, fill: '#77756d' }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #dedbd1', boxShadow: '0 12px 30px rgba(32,31,27,.1)' }} />
          <Area type="monotone" dataKey="close" stroke="#176b53" strokeWidth={2.2} fill="url(#priceFill)" />
          <Line type="monotone" dataKey="ma5" stroke="#c48a2d" strokeWidth={1.4} dot={false} />
          <Line type="monotone" dataKey="ma20" stroke="#6b74a8" strokeWidth={1.4} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
      <div className="chart-foot"><ShieldCheck size={14} />公式版本 {String(chart?.formulaVersion || '—')} · 图表只渲染 Python 计算结果</div>
    </div>
  );
}

function ResearchWorkspace({ networkEnabled, defaultSource, tushareConfigured, initialRuns, onRun }: { networkEnabled: boolean; defaultSource: string; tushareConfigured: boolean; initialRuns: RunView[]; onRun: (run: RunView, goal: string) => void }) {
  const [symbol, setSymbol] = useState('600050.SH');
  const [source, setSource] = useState(networkEnabled && ['baostock', 'tushare'].includes(defaultSource) ? defaultSource : 'demo');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AnyRecord | null>(null);
  const [error, setError] = useState('');
  const [history, setHistory] = useState<RunView[]>([]);
  const [historyRefreshing, setHistoryRefreshing] = useState(false);
  const [historyWarning, setHistoryWarning] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const runRef = useRef('');

  useEffect(() => setSource(networkEnabled && ['baostock', 'tushare'].includes(defaultSource) ? defaultSource : 'demo'), [networkEnabled, defaultSource]);
  const loadHistory = async (refresh: boolean) => {
    setHistoryRefreshing(true);
    setHistoryWarning('');
    try {
      const response = await api.research.history({ refresh });
      setHistory(response.items);
    } catch (reason) {
      const researchRuns = initialRuns.filter((run) => run.kind === 'research' && run.status === 'succeeded');
      const items = await Promise.all(researchRuns.map((run) => api.runs.get(run.runId).catch(() => null)));
      setHistory(items.filter((item): item is RunView => Boolean(item)));
      setHistoryWarning(`结果刷新暂不可用，仍可回看已保存决策：${String(reason)}`);
    } finally {
      setHistoryRefreshing(false);
    }
  };
  useEffect(() => { void loadHistory(networkEnabled); }, [initialRuns, networkEnabled]);
  useEffect(() => api.runs.subscribe(async (event) => {
    if (event.runId !== runRef.current || !['run.succeeded', 'run.failed', 'run.cancelled'].includes(event.type)) return;
    const run = await api.runs.get(event.runId);
    setRunning(false);
    if (event.type === 'run.succeeded') {
      setResult((run.result || null) as AnyRecord | null);
      setHistory((current) => [run, ...current.filter((item) => item.runId !== run.runId)]);
      setSelectedRunId(run.runId);
    }
    else setError(run.error?.message || '个股研究未完成，请检查证券代码、网络或数据源后重试。');
  }), []);

  const submit = async () => {
    if (!/^\d{6}\.(SH|SZ|BJ)$/i.test(symbol)) return;
    if (source === 'tushare' && !tushareConfigured) {
      setError('请先在“设置”中安全保存 Tushare Token。');
      return;
    }
    setRunning(true);
    setResult(null);
    setError('');
    try {
      const run = await api.research.start({ symbol: symbol.toUpperCase(), source, adjustment: 'qfq' });
      runRef.current = run.runId;
      onRun(run, `研究 ${symbol.toUpperCase()} 的买入条件`);
    } catch (reason) {
      setRunning(false);
      setError(`无法启动个股研究：${String(reason)}`);
    }
  };

  const restore = async (item: RunView) => {
    const run = item.result ? item : await api.runs.get(item.runId);
    const restored = (run.result || null) as AnyRecord | null;
    setResult(restored);
    setSelectedRunId(run.runId);
    if (restored?.symbol) setSymbol(String(restored.symbol));
    setError('');
    onRun(run, `回看 ${String(restored?.symbol || '个股')} 研究`);
  };

  const removeHistory = async (runId: string) => {
    await api.runs.delete(runId);
    setHistory((current) => current.filter((item) => item.runId !== runId));
    if (selectedRunId === runId) {
      setSelectedRunId('');
      setResult(null);
    }
  };

  return (
    <div className="workspace-page">
      <header className="page-header">
        <div><span className="eyebrow">STOCK RESEARCH</span><h1>个股研究</h1><p>数据、公式、结论与失效条件在同一条证据链上。</p></div>
      </header>
      <div className="research-shell">
        <aside className="research-history" aria-label="个股决策账本">
          <div className="history-heading"><div><span className="eyebrow">DECISION JOURNAL</span><h2>决策账本</h2></div><div className="history-heading-actions"><span>{history.length}</span><button aria-label="刷新决策结果" title="按最新收盘价刷新" onClick={() => void loadHistory(networkEnabled)} disabled={historyRefreshing}>{historyRefreshing ? <LoaderCircle className="spin" size={13} /> : <RefreshCw size={13} />}</button></div></div>
          <p className="journal-disclaimer">回看当时结论，并用同复权收盘价验证后续表现；不代表真实成交。</p>
          {historyWarning && <p className="journal-warning">{historyWarning}</p>}
          <div className="history-list">{history.map((item) => {
            const payload = (item.result || {}) as AnyRecord;
            const historySymbol = String(payload.symbol || '个股研究');
            const advice = (payload.advice || {}) as AnyRecord;
            const outcome = (payload.outcome || {}) as AnyRecord;
            const resultValue = outcome.decision_return_pct ?? outcome.price_change_pct;
            const resultPrefix = outcome.decision_return_pct == null ? '价格变化' : '假设结果';
            return <article className={`history-item journal-item ${selectedRunId === item.runId ? 'selected' : ''}`} key={item.runId}>
              <button aria-label={`${historySymbol}${String(advice.action_label || '研究完成')}${String(outcome.status_label || '待观察')}`} onClick={() => void restore(item)}>
                <div className="journal-item-top"><strong>{historySymbol}</strong><span className={`outcome-badge ${String(outcome.status || 'pending')}`}>{String(outcome.status_label || '待观察')}</span></div>
                <span className="journal-decision"><b>{String(advice.action_label || '研究完成')}</b> · {String(payload.asOf || payload.as_of || '—')}</span>
                <dl className="journal-prices"><div><dt>当时价</dt><dd>{journalPrice(outcome.baseline_price ?? advice.current_price)}</dd></div><div><dt>最新价</dt><dd>{journalPrice(outcome.latest_price)}</dd></div></dl>
                <div className="journal-result"><span>{resultPrefix}</span><strong className={Number(resultValue) > 0 ? 'positive' : Number(resultValue) < 0 ? 'negative' : ''}>{journalPercent(resultValue)}</strong></div>
                <small>{Number(outcome.trading_days || 0)} 个交易日 · 更新至 {String(outcome.latest_as_of || payload.asOf || '—')}</small>
              </button>
              <button className="history-delete" aria-label={`删除 ${historySymbol} 研究记录`} onClick={() => void removeHistory(item.runId)}><Trash2 size={13} /></button>
            </article>;
          })}{historyRefreshing && !history.length && <p className="history-empty">正在读取并核验本机决策记录…</p>}{!historyRefreshing && !history.length && <p className="history-empty">完成一次研究后，这里会永久保留当时决策；联网打开时自动核验后续表现。</p>}</div>
        </aside>
        <div className="research-current">
          <div className="research-toolbar">
            <label><span>证券代码</span><div className="input-with-icon"><Search size={16} /><input aria-label="证券代码" value={symbol} onChange={(event) => setSymbol(event.target.value)} /></div></label>
            <label><span>数据模式</span><select aria-label="数据来源" value={source} onChange={(event) => setSource(event.target.value)}><option value="baostock">自动联网 · BaoStock</option><option value="tushare">专业行情 · Tushare{tushareConfigured ? '' : '（需 Token）'}</option><option value="demo">离线演示 · 仅测试</option></select></label>
            <button className="primary-button" onClick={submit} disabled={running || (source === 'tushare' && !tushareConfigured)}>{running ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}{running ? '研究中' : '开始研究'}</button>
          </div>
          {source === 'tushare' && !tushareConfigured && <div className="macro-stop"><CircleAlert size={18} /><div><strong>Tushare 尚未配置</strong><p>前往“设置”保存 Token 后即可使用；Token 只由主进程安全注入，不会进入研究记录。</p></div></div>}
          {running && <div className="run-banner"><LoaderCircle className="spin" size={18} /><div><strong>Python 研究流水线正在执行</strong><span>数据加载 → 指标计算 → 多周期信号 → 风险门控 → 结论</span></div></div>}
          {error && <div className="macro-stop"><CircleAlert size={18} /><div><strong>个股研究未完成</strong><p>{error}</p></div></div>}
          {!running && !result && <EmptyState icon={TrendingUp} title="从一个明确问题开始" description="输入证券代码，结论会同时展示数据截止日、公式版本和失效条件。" />}
          {result && <div className="research-results"><ResearchReport result={result} chart={<StockChart result={result} />} /></div>}
        </div>
      </div>
    </div>
  );
}

function AgentWorkspace({ bootstrap, selectedSkills, onRun, onWorkspaceChange, onSettingsChange, onOpenSkills, onOpenSettings }: {
  bootstrap: BootstrapData;
  selectedSkills: Set<string>;
  onRun: (run: RunView | null, goal: string) => void;
  onWorkspaceChange: (items: WorkspaceSummary[]) => void;
  onSettingsChange: (settings: Record<string, unknown>) => void;
  onOpenSkills: () => void;
  onOpenSettings: () => void;
}) {
  const [question, setQuestion] = useState(examples[0] ?? '');
  const [messages, setMessages] = useState<Array<{ role: 'user' | 'assistant'; text: string; meta?: string; runId?: string | null; attachments?: string[] }>>([]);
  const [attachments, setAttachments] = useState<AttachmentSelection[]>([]);
  const [running, setRunning] = useState(false);
  const [threads, setThreads] = useState<ConversationSummary[]>(bootstrap.conversations || []);
  const [activeThreadId, setActiveThreadId] = useState('');
  const [loadingThread, setLoadingThread] = useState(true);
  const [workspaceId, setWorkspaceId] = useState(activeWorkspace(bootstrap.workspaces)?.id || '');
  const [model, setModel] = useState(String(bootstrap.settings.deepSeekModel || 'deepseek-v4-flash'));
  const [workspacePermission, setWorkspacePermission] = useState(String(bootstrap.settings.agentPermissionMode || 'read-only'));
  const runRef = useRef('');
  const conversationRef = useRef<HTMLDivElement>(null);
  const modelCredentialName = bootstrap.settings.modelProvider === 'openai-compatible' ? 'custom' : 'deepseek';
  const modelCredentialSaved = Boolean(bootstrap.credentials?.[modelCredentialName]);
  const modelReady = Boolean(bootstrap.settings.enableDeepSeek && modelCredentialSaved);

  const refreshThreads = async () => setThreads((await api.conversations.list()).items);
  const openThread = async (threadId: string) => {
    setLoadingThread(true);
    const state = await api.conversations.get(threadId);
    setActiveThreadId(threadId);
    setMessages(state.turns.map((turn) => ({ role: turn.role, text: turn.content, runId: turn.runId, attachments: turn.attachments?.map((item) => item.name) })));
    const lastRunId = [...state.turns].reverse().find((turn) => turn.role === 'assistant' && turn.runId)?.runId;
    if (lastRunId) {
      try { onRun(await api.runs.get(lastRunId), `回看「${state.turns.find((turn) => turn.role === 'user')?.content.slice(0, 60) || '求衡投研助手'}」`); } catch { /* legacy conversation without persisted run */ }
    } else onRun(null, '');
    setLoadingThread(false);
  };
  const newThread = async () => {
    const state = await api.conversations.create();
    await refreshThreads();
    setActiveThreadId(state.threadId);
    setMessages([]);
    setLoadingThread(false);
    onRun(null, '');
  };

  useEffect(() => {
    const first = bootstrap.conversations?.[0];
    if (first) void openThread(first.threadId);
    else void newThread();
  }, []);
  useEffect(() => setWorkspaceId(activeWorkspace(bootstrap.workspaces)?.id || ''), [bootstrap.workspaces]);
  useEffect(() => setModel(String(bootstrap.settings.deepSeekModel || 'deepseek-v4-flash')), [bootstrap.settings.deepSeekModel]);
  useEffect(() => setWorkspacePermission(String(bootstrap.settings.agentPermissionMode || 'read-only')), [bootstrap.settings.agentPermissionMode]);

  useEffect(() => api.runs.subscribe(async (event) => {
    if (event.runId !== runRef.current || !['run.succeeded', 'run.failed', 'run.cancelled'].includes(event.type)) return;
    const run = await api.runs.get(event.runId);
    const result = (run.result || {}) as AnyRecord;
    setRunning(false);
    if (event.type === 'run.succeeded') {
      const modelInfo = (result.model || {}) as AnyRecord;
      setMessages((current) => [...current, { role: 'assistant', text: String(result.answer || '任务已完成。'), runId: run.runId, meta: result.answer_mode === 'local' ? '固定规则分析 · 未调用大模型' : `${String(modelInfo.id || model)}${result.vision?.used ? ' · 已使用图文模型' : ''}` }]);
      void refreshThreads();
    }
    else setMessages((current) => [...current, { role: 'assistant', text: `## 任务未完成\n\n${run.error?.message || '任务已取消或本地服务暂时不可用。'}\n\n请检查网络或设置后重试。`, meta: statusLabel(run.status) }]);
  }), []);
  useEffect(() => {
    const element = conversationRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages, running]);

  const submit = async () => {
    const clean = question.trim();
    if (!clean || running || !activeThreadId) return;
    const selectedAttachments = attachments;
    setMessages((current) => [...current, { role: 'user', text: clean, attachments: selectedAttachments.map((item) => item.name) }]);
    setQuestion('');
    setAttachments([]);
    setRunning(true);
    try {
      const run = await api.agent.start({ question: clean, skillNames: [...selectedSkills], threadId: activeThreadId, attachments: selectedAttachments.map((item) => ({ token: item.token })), workspaceId, model, modelProvider: bootstrap.settings.modelProvider || 'deepseek-official', workspacePermission });
      runRef.current = run.runId;
      onRun(run, clean);
    } catch (reason) {
      setRunning(false);
      setMessages((current) => [...current, { role: 'assistant', text: `## 无法启动任务\n\n${String(reason)}` }]);
    }
  };

  const chooseAttachments = async () => setAttachments(await api.native.chooseAttachments());

  const chooseWorkspace = async () => {
    const path = await api.native.chooseDirectory();
    if (!path) return;
    const result = await api.workspaces.add(path);
    setWorkspaceId(result.activeId);
    onWorkspaceChange(result.items);
  };

  const selectWorkspace = async (nextId: string) => {
    const result = await api.workspaces.select(nextId);
    setWorkspaceId(result.activeId);
    onWorkspaceChange(result.items);
  };

  const selectModel = async (nextModel: string) => {
    setModel(nextModel);
    onSettingsChange(await api.settings.patch({ deepSeekModel: nextModel }));
  };

  const selectPermission = async (nextPermission: string) => {
    setWorkspacePermission(nextPermission);
    onSettingsChange(await api.settings.patch({ agentPermissionMode: nextPermission }));
  };

  const deleteThread = async (threadId: string) => {
    if (!window.confirm('删除这个对话及其上下文？此操作不可撤销。')) return;
    await api.conversations.delete(threadId);
    const remaining = (await api.conversations.list()).items;
    setThreads(remaining);
    if (threadId !== activeThreadId) return;
    if (remaining[0]) await openThread(remaining[0].threadId);
    else await newThread();
  };

  return (
    <div className="agent-page">
      <header className="agent-header">
        <div><span className="eyebrow">EQUISEEK RESEARCH ASSISTANT</span><h1>把研究问题交给求衡</h1></div>
        <div className={`mode-badge ${modelReady ? '' : 'local-rule'}`}><CircleDot size={13} /><span>{bootstrap.settings.enableNetwork ? '联网研究已开启' : '离线模式'} · {modelReady ? modelOptions.find((item) => item.id === model)?.name || model : '固定规则 · 未调用大模型'}</span></div>
      </header>
      <div className="agent-workbench">
      <aside className="conversation-rail" aria-label="对话列表">
        <button className="new-conversation" onClick={() => void newThread()} disabled={running}><Plus size={15} />新对话</button>
        <div className="thread-list">{threads.map((thread) => <article className={`thread-item ${thread.threadId === activeThreadId ? 'active' : ''}`} key={thread.threadId}><button onClick={() => void openThread(thread.threadId)} disabled={running}><MessageSquare size={14} /><span><strong>{thread.title || '新对话'}</strong><small>{thread.preview || '还没有消息'}</small></span></button><button className="thread-delete" aria-label={`删除对话 ${thread.title}`} onClick={() => void deleteThread(thread.threadId)} disabled={running}><Trash2 size={12} /></button></article>)}</div>
        <div className="context-note"><ShieldCheck size={14} /><span>会话保存在本机<br />长对话自动业务压缩</span></div>
      </aside>
      <div className="agent-conversation-main">
      <div className="conversation" aria-live="polite" ref={conversationRef}>
        {loadingThread && <div className="thread-loading"><LoaderCircle className="spin" size={17} />正在载入本地对话…</div>}
        {!messages.length && (
          <div className="agent-welcome">
            <div className="welcome-orbit"><Sparkles size={26} /></div>
            <h2>这次想研究什么？</h2>
            <p>我会先制定计划，再选择 Skill 和工具；数据来源、任务过程与 HTML 成果都可以检查。</p>
            <div className="example-grid">{examples.map((item, index) => <button key={item} onClick={() => setQuestion(item)}><span>0{index + 1}</span>{item}<ArrowUpRight size={15} /></button>)}</div>
          </div>
        )}
        {messages.map((message, index) => (
          <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
            <div className="message-avatar">{message.role === 'user' ? '你' : <AppLogo />}</div>
            <div className="message-body"><span className="message-author">{message.role === 'user' ? '你' : '求衡投研助手'}</span>{message.role === 'assistant' ? <MarkdownContent compact>{message.text}</MarkdownContent> : <p className="user-message-text">{message.text}</p>}{message.attachments?.length ? <div className="message-attachments">{message.attachments.map((name) => <span key={name}><FileCode2 size={12} />{name}</span>)}</div> : null}{message.meta && <small>{message.meta}</small>}</div>
          </article>
        ))}
        {running && <article className="message assistant"><div className="message-avatar"><AppLogo /></div><div className="message-body"><span className="message-author">求衡投研助手</span><div className="thinking"><LoaderCircle className="spin" size={16} />正在拆解目标并选择工具…</div></div></article>}
      </div>
      <div className="composer-wrap">
        {!modelReady && <section className="model-setup-notice" role="status" aria-label="大模型配置状态"><CircleAlert size={17} /><div><strong>{modelCredentialSaved ? '大模型已配置，但当前未启用' : `尚未配置${modelCredentialName === 'deepseek' ? ' DeepSeek' : '自定义端点'} API Key`}</strong><span>当前提问只会使用本地固定规则；可继续做规则型股票分析，但不会获得大模型的动态理解与回答。</span></div><button type="button" onClick={onOpenSettings}>{modelCredentialSaved ? '前往设置开启' : '前往设置配置'}<ArrowUpRight size={14} /></button></section>}
        <div className="composer">
          {attachments.length > 0 && <div className="attachment-tray">{attachments.map((item) => <span key={item.token}><FileCode2 size={12} />{item.name}<button aria-label={`移除附件 ${item.name}`} onClick={() => setAttachments((current) => current.filter((entry) => entry.token !== item.token))}><X size={11} /></button></span>)}</div>}
          {loadingThread ? <div className="composer-loading"><LoaderCircle className="spin" size={14} />正在准备本地会话…</div> : <><textarea aria-label="向求衡投研助手提问" value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="描述目标，例如：结合最新数据研究 600050.SH 的买入条件" rows={2} disabled={!activeThreadId} /><div className="composer-actions">
            <div className="composer-tool-row" aria-label="Agent 运行配置">
              <button className="icon-text-button" aria-label="添加文件" onClick={() => void chooseAttachments()} disabled={running}><Plus size={15} /><span>文件</span></button>
              <span className="composer-tool-divider" aria-hidden="true" />
              <label className="runtime-chip runtime-chip-workspace" title="切换工作区"><FolderOpen size={14} /><select aria-label="Agent 工作区" value={workspaceId} onChange={(event) => void selectWorkspace(event.target.value)}>{bootstrap.workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown size={12} aria-hidden="true" /></label>
              <button type="button" className="runtime-chip-icon" aria-label="添加工作区" title="添加工作区" onClick={() => void chooseWorkspace()}><Plus size={14} /></button>
              <label className="runtime-chip runtime-chip-model" title="切换模型"><Bot size={14} /><select aria-label="Agent 模型" value={model} onChange={(event) => void selectModel(event.target.value)}>{modelOptions.map((item) => <option key={item.id} value={item.id} disabled={bootstrap.settings.modelProvider !== 'openai-compatible' && item.id === 'deepseek-v4-flash-vision-exp'}>{item.name}</option>)}</select><ChevronDown size={12} aria-hidden="true" /></label>
              <label className="runtime-chip runtime-chip-permission" title={workspacePermission === 'workspace-write' ? '可编辑工作区文件' : '只读安全模式'}><Terminal size={14} /><select aria-label="Agent 工具权限" value={workspacePermission} onChange={(event) => void selectPermission(event.target.value)}><option value="read-only">只读</option><option value="workspace-write">可编辑</option></select><ChevronDown size={12} aria-hidden="true" /></label>
              <button type="button" className="runtime-chip runtime-chip-skills" aria-label={`管理 Skills，${selectedSkills.size ? `已选 ${selectedSkills.size} 个` : '当前自动选择'}`} title="管理本轮 Skills" onClick={onOpenSkills}><WandSparkles size={14} /><span>{selectedSkills.size ? `${selectedSkills.size} Skills` : 'Skills'}</span></button>
            </div>
            <button aria-label="发送给求衡投研助手" data-testid="send-agent" className="send-button" onClick={submit} disabled={running || !question.trim() || !activeThreadId}>{running ? <Square size={14} /> : <ArrowUpRight size={18} />}</button>
          </div></>}
        </div>
        <p className="disclaimer">研究工具不连接券商，不自动下单；结论需结合数据时效与个人风险预算。</p>
      </div>
      </div>
      </div>
    </div>
  );
}

function Inspector({ run, events, goal, onClose }: { run: RunView | null; events: RunEvent[]; goal: string; onClose: () => void }) {
  const [artifactError, setArtifactError] = useState('');
  const result = (run?.result || {}) as AnyRecord;
  const trace = (result.trace || []) as AnyRecord[];
  const artifacts = (result.artifacts || []) as AnyRecord[];
  const activeSkills = (result.active_skills || []) as AnyRecord[];
  const runtimeModel = (result.model || {}) as AnyRecord;
  const runtimeWorkspace = (result.workspace_context || {}) as AnyRecord;
  const skillIndex = new Map<string, AnyRecord>();
  activeSkills.forEach((skill) => skillIndex.set(String(skill.name), skill));
  trace.forEach((step) => (step.skill_names || []).forEach((name: string) => {
    if (!skillIndex.has(name)) skillIndex.set(name, { name, provider: '研究流水线' });
  }));
  const visibleSkills = [...skillIndex.values()];
  const openArtifact = async (artifact: AnyRecord) => {
    if (!artifact.path) return;
    try {
      const message = await api.native.openPath(String(artifact.path));
      setArtifactError(message || '');
    } catch (reason) {
      setArtifactError(`无法打开成果：${String(reason)}`);
    }
  };
  return (
    <aside className="inspector" data-testid="run-inspector">
      <div className="inspector-head"><div><span className="eyebrow">RUN INSPECTOR</span><h2>运行过程</h2></div><button aria-label="关闭运行检查器" onClick={onClose}><PanelRightClose size={18} /></button></div>
      {!run ? <EmptyState icon={Activity} title="暂无运行" description="启动研究或 Agent 后，这里会出现 Goal、Plan、Trace 与成果。" /> : (
        <div className="inspector-scroll">
          <section className="inspector-section"><div className="section-title"><Target size={15} />Goal <span className={`run-status ${run.status}`}>{statusLabel(run.status)}</span></div><p className="goal-text">{goal}</p><code>{run.runId}</code></section>
          {run.kind === 'agent' && <section className="inspector-section runtime-facts"><div className="section-title"><Terminal size={15} />Runtime</div><dl><div><dt>模型</dt><dd>{runtimeModel.enabled ? runtimeModel.id : '本地规则规划'}</dd></div><div><dt>供应商</dt><dd>{runtimeModel.provider === 'openai-compatible' ? '自定义兼容端点' : 'DeepSeek 官方'}</dd></div><div><dt>工作区</dt><dd title={String(runtimeWorkspace.path || '')}>{String(runtimeWorkspace.path || '—')}</dd></div><div><dt>权限</dt><dd>{runtimeWorkspace.permission === 'workspace-write' ? '可编辑' : '只读'}</dd></div><div><dt>工具</dt><dd>{Array.isArray(runtimeWorkspace.tools) ? runtimeWorkspace.tools.join(' · ') : '—'}</dd></div></dl></section>}
          <section className="inspector-section"><div className="section-title"><ListChecks size={15} />Plan</div><ol className="plan-list">
            {trace.length ? trace.map((item, index) => <li key={`${item.title}-${index}`}><span className={item.status === 'succeeded' ? 'done' : ''}>{item.status === 'succeeded' ? <Check size={12} /> : index + 1}</span><div><strong>{item.title}</strong><small>{item.summary}</small></div></li>) : <><li><span className="done"><Check size={12} /></span><div><strong>理解目标与约束</strong><small>识别证券、时效和输出要求</small></div></li><li><span className={run.status === 'running' ? 'active' : ''}>2</span><div><strong>选择 Skill 与工具</strong><small>仅调用已授权能力</small></div></li><li><span>3</span><div><strong>形成可追溯成果</strong><small>写入工作区并输出 HTML</small></div></li></>}
          </ol></section>
          <section className="inspector-section"><div className="section-title"><UsersRound size={15} />Agents</div><div className="agent-pills">{trace.length ? [...new Set(trace.map((item) => item.agent_name).filter(Boolean))].map((name) => <span key={name}><Bot size={12} />{name}</span>) : <span><Bot size={12} />investment-lead-agent</span>}</div></section>
          <section className="inspector-section" data-testid="inspector-skills"><div className="section-title"><WandSparkles size={15} />Skills <span>{visibleSkills.length}</span></div>{visibleSkills.length ? visibleSkills.map((skill) => <div className="skill-row mini" key={skill.name}><div><strong>{skill.name}</strong><small>{skill.provider}</small></div><Check size={14} /></div>) : <p className="muted">完成后显示实际启用的 Skill。</p>}</section>
          <section className="inspector-section"><div className="section-title"><Network size={15} />Trace <span>{Math.max(events.length, trace.length)}</span></div><div className="trace-list detailed">{(trace.length ? trace : events.map((event) => ({ title: event.type, summary: event.at, status: 'succeeded' }))).map((item: AnyRecord, index: number) => <details className="trace-entry" key={`${item.title}-${index}`} open={index === 0}><summary><i className={item.status} /><div><strong>{item.title}</strong><small>{item.tool_name || item.agent_name || item.agent || item.summary}</small></div><ChevronDown size={12} /></summary><div className="trace-detail"><p>{item.summary || '该步骤没有额外摘要。'}</p>{(item.skill_names || item.skills)?.length > 0 && <div><span>Skills</span><code>{(item.skill_names || item.skills).join(' · ')}</code></div>}{(item.agent_name || item.agent) && <div><span>Agent</span><code>{item.agent_name || item.agent}</code></div>}{item.tool_name && <div><span>Tool</span><code>{item.tool_name}</code></div>}{item.evidence_path && <button onClick={() => openArtifact({ path: item.evidence_path })}>打开证据文件 <ArrowUpRight size={11} /></button>}</div></details>)}</div></section>
          <section className="inspector-section"><div className="section-title"><Archive size={15} />Artifacts <span>{artifacts.length}</span></div>{artifacts.length ? artifacts.map((artifact) => <button className="artifact" key={artifact.name} onClick={() => openArtifact(artifact)}><FileCode2 size={17} /><div><strong>{artifact.name}</strong><small>{artifact.media_type} · {Math.ceil(artifact.size_bytes / 1024)} KB · 点击打开</small></div><ArrowUpRight size={14} /></button>) : <p className="muted">任务完成后，HTML/JSON 成果会保存在工作区。</p>}{artifactError && <p className="artifact-error mini">{artifactError}</p>}</section>
        </div>
      )}
    </aside>
  );
}

function PortfolioPage({ initial }: { initial: PortfolioBook }) {
  const [book, setBook] = useState(initial);
  useEffect(() => setBook(initial), [initial]);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({ symbol: '601318.SH', name: '中国平安', quantity: '1000', cost_price: '48.20', industry: '非银金融' });
  const save = async () => {
    const updated = await api.portfolio.upsertPosition({ ...form, quantity: Number(form.quantity), cost_price: Number(form.cost_price) });
    setBook(updated); setShowForm(false);
  };
  return <div className="workspace-page"><header className="page-header split"><div><span className="eyebrow">LOCAL PORTFOLIO</span><h1>持仓与自选</h1><p>数据只保存在本机，不需要登录或 PostgreSQL。</p></div><button className="primary-button" onClick={() => setShowForm(true)}><Plus size={16} />添加持仓</button></header>
    <div className="summary-strip"><div><span>持仓标的</span><strong>{book.positions.length}</strong></div><div><span>自选标的</span><strong>{book.watchlist.length}</strong></div><div><span>存储</span><strong className="text-value">本地 JSON + SQLite</strong></div></div>
    <section className="table-card"><div className="card-heading"><div><span className="eyebrow">POSITIONS</span><h3>当前持仓</h3></div></div><table><thead><tr><th>证券</th><th>名称</th><th>数量</th><th>成本</th><th>行业</th><th /></tr></thead><tbody>{book.positions.map((item: AnyRecord) => <tr key={item.symbol}><td><strong>{item.symbol}</strong></td><td>{item.name || '—'}</td><td>{item.quantity}</td><td>{item.cost_price}</td><td><span className="neutral-pill">{item.industry || '未分类'}</span></td><td><button aria-label={`删除 ${item.symbol}`} onClick={async () => setBook(await api.portfolio.removePosition(item.symbol))}><Trash2 size={15} /></button></td></tr>)}</tbody></table></section>
    {showForm && <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="position-title"><div className="modal-head"><div><span className="eyebrow">NEW POSITION</span><h2 id="position-title">添加持仓</h2></div><button aria-label="关闭" onClick={() => setShowForm(false)}><X size={18} /></button></div><div className="form-grid">{Object.entries(form).map(([key, value]) => <label key={key}><span>{{ symbol: '证券代码', name: '名称', quantity: '数量', cost_price: '成本价', industry: '行业' }[key]}</span><input value={value} onChange={(event) => setForm({ ...form, [key]: event.target.value })} /></label>)}</div><div className="modal-actions"><button className="secondary-button" onClick={() => setShowForm(false)}>取消</button><button className="primary-button" onClick={save}>保存到本机</button></div></div></div>}
  </div>;
}

function SkillsPage({ bootstrap, selected, onToggle, onSkillsChange }: { bootstrap: BootstrapData; selected: Set<string>; onToggle: (name: string) => void; onSkillsChange: (skills: SkillSummary[]) => void }) {
  const [skills, setSkills] = useState(bootstrap.skills);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [draft, setDraft] = useState('');
  const [notice, setNotice] = useState('');
  const open = async (name: string) => { const next = await api.skills.get(name); setDetail(next); setDraft(next.content); setNotice(''); };
  const refresh = async () => { const next = (await api.skills.list()).items; setSkills(next); onSkillsChange(next); };
  const importSkill = async () => { const imported = await api.skills.importFile(); if (!imported) return; await refresh(); setDetail(imported); setDraft(imported.content); setNotice('Skill 已导入并通过声明校验。'); };
  const save = async () => { if (!detail) return; const next = await api.skills.save(detail.name, draft); setDetail(next); setDraft(next.content); await refresh(); setNotice('已保存，下一轮对话即可使用。'); };
  const remove = async () => { if (!detail || !window.confirm(`删除用户 Skill ${detail.name}？`)) return; await api.skills.delete(detail.name); setDetail(null); setDraft(''); await refresh(); };
  return <div className="workspace-page"><header className="page-header split"><div><span className="eyebrow">REPLACEABLE CAPABILITIES</span><h1>Skill 管理</h1><p>上传、查看并编辑自己的 Skill；同名用户 Skill 会替换内置版本。</p></div><div className="header-actions"><button className="secondary-button" onClick={() => void api.skills.openRoot()}><FolderOpen size={16} />打开目录</button><button className="primary-button" onClick={() => void importSkill()}><Upload size={16} />导入 SKILL.md</button></div></header><div className="skill-info"><ShieldCheck size={18} /><div><strong>文件只保存到本机，启用状态由每轮对话决定</strong><span>点击左侧条目查看完整内容并加入本轮；内置 Skill 只读，用户 Skill 可编辑和删除。</span></div></div><div className="skill-manager"><div className="skill-list-panel">{skills.map((skill) => <article className={`skill-list-item ${detail?.name === skill.name ? 'active' : ''}`} key={`${skill.provider}-${skill.name}`}><button className="skill-open" onClick={() => { void open(skill.name); if (!selected.has(skill.name)) onToggle(skill.name); }}><span className={skill.sourceLabel === '用户 Skill' ? 'user-source' : 'builtin-source'}>{skill.sourceLabel}</span><strong>{skill.name}</strong><small>{skill.description}</small><em>{selected.has(skill.name) ? '本轮启用' : '点击查看并启用'}</em></button><button className={`skill-enable ${selected.has(skill.name) ? 'on' : ''}`} aria-label={selected.has(skill.name) ? '从本轮移除' : '加入本轮'} onClick={() => onToggle(skill.name)}>{selected.has(skill.name) ? <Check size={13} /> : <Plus size={13} />}</button></article>)}</div><section className="skill-detail-panel">{detail ? <><div className="skill-detail-head"><div><span className="eyebrow">{detail.editable ? 'USER SKILL' : 'BUILT-IN SKILL'}</span><h2>{detail.name}</h2><p>{detail.description}</p></div><span className="neutral-pill">v{detail.version}</span></div><textarea aria-label="Skill 内容" value={draft} onChange={(event) => setDraft(event.target.value)} readOnly={!detail.editable} spellCheck={false} /><div className="skill-detail-actions"><span>{notice || (detail.editable ? '修改 YAML frontmatter 或执行说明后保存。' : '内置 Skill 为只读；可导入同名用户版本覆盖。')}</span>{detail.editable && <><button className="secondary-button danger" onClick={() => void remove()}><Trash2 size={14} />删除</button><button className="primary-button" onClick={() => void save()}><Save size={14} />保存</button></>}</div></> : <EmptyState icon={WandSparkles} title="选择一个 Skill" description="可查看完整 SKILL.md、来源、版本与是否允许编辑。" />}</section></div></div>;
}

function MacroPage({ networkEnabled, onRun }: { networkEnabled: boolean; onRun: (run: RunView, goal: string) => void }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AnyRecord | null>(null);
  const [error, setError] = useState('');
  const runRef = useRef('');
  useEffect(() => api.runs.subscribe(async (event) => {
    if (event.runId !== runRef.current || !['run.succeeded', 'run.failed', 'run.cancelled'].includes(event.type)) return;
    const run = await api.runs.get(event.runId);
    setRunning(false);
    if (event.type === 'run.succeeded') setResult((run.result || null) as AnyRecord | null);
    else setError(run.error?.message || '宏观研究未完成，请检查网络后重试。');
    onRun(run, '联网核验官方宏观数据并判断当前结论是否仍有效');
  }), []);
  const verify = async () => {
    setRunning(true);
    setError('');
    try {
      const run = await api.macro.start();
      runRef.current = run.runId;
      onRun(run, '联网核验官方宏观数据并判断当前结论是否仍有效');
    } catch (reason) {
      setRunning(false);
      setError(`无法启动宏观研究：${String(reason)}`);
    }
  };
  return <div className="workspace-page macro-workspace"><header className="page-header split"><div><span className="eyebrow">MACRO INVESTMENT RESEARCH</span><h1>宏观研究</h1><p>一键联网核验官方发布时效，并恢复资本三流、成本转嫁、长期配置和行业决策全景。</p></div><button className="primary-button" disabled={!networkEnabled || running} onClick={verify}>{running ? <LoaderCircle className="spin" size={16} /> : <Globe2 size={16} />}{running ? '正在研究与核验' : networkEnabled ? '开始宏观研究' : '离线模式已开启'}</button></header>
    {!networkEnabled && <div className="macro-stop"><WifiOff size={18} /><div><strong>宏观研究需要联网核验</strong><p>在“设置”中开启“使用联网公开数据”即可；应用首次安装默认已开启。</p></div></div>}
    {running && <div className="run-banner"><LoaderCircle className="spin" size={18} /><div><strong>正在抓取官方最新正文并重算</strong><span>发现最新发布 → 解析 25 项高频指标 → 保留低频统计期 → 资本三流 / 成本转嫁 → 配置与风险门禁</span></div></div>}
    {error && <div className="macro-stop"><CircleAlert size={18} /><div><strong>宏观研究未完成</strong><p>{error}</p></div></div>}
    {!running && !result && !error && <EmptyState icon={Globe2} title="一键生成完整宏观决策报告" description="联网核验不是结论本身；报告还会展示评分、传导路径、配置、行业、来源和失效条件。" />}
    {result && <MacroReport result={result} />}
  </div>;
}

function SettingsPage({ bootstrap, onUpdate, onWorkspaceChange, onCredentialsChange }: { bootstrap: BootstrapData; onUpdate: (settings: Record<string, unknown>) => void; onWorkspaceChange: (items: WorkspaceSummary[]) => void; onCredentialsChange: (credentials: CredentialStatus) => void }) {
  const [settings, setSettings] = useState(bootstrap.settings);
  const [credentials, setCredentials] = useState(bootstrap.credentials || { deepseek: false, custom: false, tushare: false });
  const [deepseekKey, setDeepseekKey] = useState('');
  const [tushareToken, setTushareToken] = useState('');
  useEffect(() => setSettings(bootstrap.settings), [bootstrap.settings]);
  useEffect(() => setCredentials(bootstrap.credentials || { deepseek: false, custom: false, tushare: false }), [bootstrap.credentials]);
  const patch = async (input: Record<string, unknown>) => { const next = await api.settings.patch(input); setSettings(next); onUpdate(next); };
  const choose = async () => { const path = await api.native.chooseDirectory(); if (!path) return; const result = await api.workspaces.add(path); onWorkspaceChange(result.items); const next = await api.settings.patch({ workspaceRoot: path }); setSettings(next); onUpdate(next); };
  const selectWorkspace = async (workspaceId: string) => { const result = await api.workspaces.select(workspaceId); onWorkspaceChange(result.items); const next = await api.settings.patch({ workspaceRoot: result.items.find((item) => item.active)?.path || '' }); setSettings(next); onUpdate(next); };
  const credentialName = settings.modelProvider === 'openai-compatible' ? 'custom' : 'deepseek';
  const currentCredentialSaved = credentials[credentialName];
  const applyCredentials = (next: CredentialStatus) => { setCredentials(next); onCredentialsChange(next); };
  const saveKey = async () => { applyCredentials(await api.credentials.set(credentialName, deepseekKey)); setDeepseekKey(''); await patch({ enableDeepSeek: true }); };
  const clearKey = async () => { applyCredentials(await api.credentials.clear(credentialName)); await patch({ enableDeepSeek: false }); };
  const saveTushare = async () => { applyCredentials(await api.credentials.set('tushare', tushareToken)); setTushareToken(''); };
  const clearTushare = async () => applyCredentials(await api.credentials.clear('tushare'));
  const selectProvider = async (provider: string) => { const nextCredentialName = provider === 'openai-compatible' ? 'custom' : 'deepseek'; setDeepseekKey(''); await patch({ modelProvider: provider, enableDeepSeek: Boolean(credentials[nextCredentialName]), ...(provider === 'deepseek-official' ? { modelBaseUrl: 'https://api.deepseek.com', ...(settings.deepSeekModel === 'deepseek-v4-flash-vision-exp' ? { deepSeekModel: 'deepseek-v4-flash' } : {}) } : {}) }); };
  const currentWorkspace = activeWorkspace(bootstrap.workspaces);
  return <div className="workspace-page narrow"><header className="page-header"><div><span className="eyebrow">SIMPLE LOCAL SETTINGS</span><h1>设置</h1><p>无需账号和登录；默认联网获取公开市场数据，所有成果仍保存在本机。</p></div></header><section className="settings-card">
    <div className="settings-group"><div><Globe2 size={18} /><span><strong>使用联网公开数据（推荐）</strong><small>开启 BaoStock、官方宏观研究和需要网络的 Skill；关闭后仅用于流程测试</small></span></div><button aria-label="使用联网公开数据" role="switch" aria-checked={Boolean(settings.enableNetwork)} className={`switch ${settings.enableNetwork ? 'on' : ''}`} onClick={() => void patch({ enableNetwork: !settings.enableNetwork, dataSource: settings.enableNetwork ? 'demo' : 'baostock' })}><i /></button></div>
    <div className="settings-group vertical model-setting"><div><BarChart3 size={18} /><span><strong>市场行情数据源</strong><small>BaoStock 无需凭据；Tushare Token 由系统安全存储加密，不写入运行记录或报告</small></span></div><div className="model-provider-grid"><label><span>默认数据源</span><select aria-label="默认市场数据源" disabled={!settings.enableNetwork} value={String(settings.dataSource || 'baostock')} onChange={(event) => void patch({ dataSource: event.target.value })}><option value="baostock">BaoStock</option><option value="tushare">Tushare</option>{!settings.enableNetwork && <option value="demo">离线 Demo</option>}</select></label></div><div className="credential-row"><input aria-label="Tushare Token" type="password" value={tushareToken} onChange={(event) => setTushareToken(event.target.value)} placeholder={credentials.tushare ? 'Tushare Token 已安全保存（输入可替换）' : '输入 Tushare Token'} /><button aria-label="保存 Tushare Token" className="secondary-button" disabled={!tushareToken.trim()} onClick={() => void saveTushare()}>保存</button>{credentials.tushare && <button aria-label="清除 Tushare Token" className="text-danger" onClick={() => void clearTushare()}>清除</button>}</div><p className="credential-boundary">当前接入范围与 Python Provider 一致：A 股、境内指数和基金；全球市场仍在后续路线图中。</p></div>
    <div className="settings-group vertical model-setting"><div><Bot size={18} /><span><strong>DeepSeek 模型与供应商</strong><small>未配置 API Key 时，投研助手只使用本地固定规则；保存当前供应商 Key 后会自动启用大模型</small></span><button aria-label="启用 DeepSeek 推理" role="switch" aria-checked={Boolean(settings.enableDeepSeek && currentCredentialSaved)} disabled={!currentCredentialSaved} className={`switch ${settings.enableDeepSeek && currentCredentialSaved ? 'on' : ''}`} onClick={() => void patch({ enableDeepSeek: !settings.enableDeepSeek })}><i /></button></div><div className="model-provider-grid"><label><span>供应商</span><select aria-label="DeepSeek 供应商" value={String(settings.modelProvider || 'deepseek-official')} onChange={(event) => void selectProvider(event.target.value)}><option value="deepseek-official">DeepSeek 官方 API</option><option value="openai-compatible">自定义 OpenAI 兼容端点</option></select></label><label><span>模型</span><select aria-label="DeepSeek 模型" value={String(settings.deepSeekModel || 'deepseek-v4-flash')} onChange={(event) => void patch({ deepSeekModel: event.target.value })}>{modelOptions.map((item) => <option key={item.id} value={item.id} disabled={settings.modelProvider !== 'openai-compatible' && item.id === 'deepseek-v4-flash-vision-exp'}>{item.name} · {item.detail}</option>)}</select></label><label className="provider-url"><span>API 地址</span><input aria-label="模型 API 地址" readOnly={settings.modelProvider !== 'openai-compatible'} value={String(settings.modelBaseUrl || 'https://api.deepseek.com')} onChange={(event) => setSettings({ ...settings, modelBaseUrl: event.target.value })} onBlur={(event) => void patch({ modelBaseUrl: event.target.value })} /></label></div><div className="model-capabilities"><span><Terminal size={13} />Tool Calls</span><span>1M Context</span><span className={settings.deepSeekModel === 'deepseek-v4-flash-vision-exp' ? 'vision-on' : ''}>{settings.deepSeekModel === 'deepseek-v4-flash-vision-exp' ? '图文多模态 · 端点自证' : '文本输入'}</span></div><div className="credential-row"><input aria-label="DeepSeek API Key" type="password" value={deepseekKey} onChange={(event) => setDeepseekKey(event.target.value)} placeholder={currentCredentialSaved ? '当前供应商凭据已安全保存（输入可替换）' : '输入当前供应商 API Key'} /><button aria-label="保存模型 API Key" className="secondary-button" disabled={!deepseekKey.trim()} onClick={() => void saveKey()}>保存并启用</button>{currentCredentialSaved && <button className="text-danger" onClick={() => void clearKey()}>清除</button>}</div><p className="credential-boundary">官方 API 与自定义端点的凭据分别保存，切换供应商不会交叉发送。官方当前仅公布 V4 Pro 和 V4 Flash。</p></div>
    <div className="settings-group"><div><WandSparkles size={18} /><span><strong>包含内置 Skill</strong><small>关闭后只使用用户 Skill 目录中的能力</small></span></div><button aria-label="包含内置 Skill" role="switch" aria-checked={Boolean(settings.includeBuiltinSkills)} className={`switch ${settings.includeBuiltinSkills ? 'on' : ''}`} onClick={() => void patch({ includeBuiltinSkills: !settings.includeBuiltinSkills })}><i /></button></div>
    <div className="settings-group vertical"><div><FolderOpen size={18} /><span><strong>Agent 工作区</strong><small>文件编辑器和持久 Shell 只能在当前选定目录中运行</small></span></div><div className="workspace-settings-row"><select aria-label="默认 Agent 工作区" value={currentWorkspace?.id || ''} onChange={(event) => void selectWorkspace(event.target.value)}>{bootstrap.workspaces.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.path}</option>)}</select><button className="secondary-button" onClick={choose}><Plus size={14} />添加目录</button></div><div className="path-picker"><code>{currentWorkspace?.path}</code><span className="workspace-access"><ShieldCheck size={12} />{currentWorkspace?.writable ? '可读写' : '只读'}</span></div></div>
    <div className="settings-group repository-setting"><div><GitFork size={18} /><span><strong>开源项目</strong><small>Apache-2.0 · 查看源码、提交问题或参与贡献</small><code>{REPOSITORY_URL.replace('https://', '')}</code></span></div><button className="secondary-button repository-link" aria-label="访问 EquiSeek GitHub 仓库" onClick={() => void api.system.openRepository()}>访问 GitHub<ExternalLink size={14} /></button></div>
  </section><section className="privacy-card"><ShieldCheck size={20} /><div><h3>凭据和工作区有强制边界</h3><p>API Key 由系统安全存储加密；文件覆盖前必须先读取且版本未变，Shell 通过 macOS Seatbelt 限定在选定工作区内。</p></div></section></div>;
}

export function App() {
  const [view, setView] = useState<View>('agent');
  const [bootstrap, setBootstrap] = useState<BootstrapData | null>(null);
  const [error, setError] = useState('');
  const emptyContext = (): RunContext => ({ run: null, events: [], goal: '' });
  const [runContexts, setRunContexts] = useState<Record<View, RunContext>>({ agent: emptyContext(), research: emptyContext(), portfolio: emptyContext(), candidates: emptyContext(), macro: emptyContext(), skills: emptyContext(), settings: emptyContext() });
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [selectedSkills, setSelectedSkills] = useState<Set<string>>(new Set());

  useEffect(() => { api.system.bootstrap().then(setBootstrap).catch((reason) => setError(String(reason))); }, []);
  useEffect(() => api.runs.subscribe(async (event) => {
    let currentRun: RunView | null = null;
    try { currentRun = await api.runs.get(event.runId); } catch { return; }
    setRunContexts((current) => Object.fromEntries(Object.entries(current).map(([key, context]) => context.run?.runId === event.runId ? [key, { ...context, run: currentRun, events: context.events.some((item) => item.seq === event.seq) ? context.events : [...context.events, event] }] : [key, context])) as Record<View, RunContext>);
    if (event.type === 'run.succeeded' && currentRun.kind === 'agent') {
      try { const portfolio = await api.portfolio.get(); setBootstrap((current) => current ? { ...current, portfolio } : current); } catch { /* portfolio page can still refresh on next bootstrap */ }
    }
  }), []);

  const startRun = (surface: View, run: RunView | null, nextGoal: string) => { setRunContexts((current) => ({ ...current, [surface]: { run, goal: nextGoal, events: [] } })); setInspectorOpen(true); };
  const toggleSkill = (name: string) => setSelectedSkills((current) => {
    const next = new Set(current);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    return next;
  });
  const updateWorkspaces = (workspaces: WorkspaceSummary[]) => setBootstrap((current) => current ? { ...current, workspaces, settings: { ...current.settings, workspaceRoot: activeWorkspace(workspaces)?.path || '' } } : current);
  const selectGlobalWorkspace = async (workspaceId: string) => updateWorkspaces((await api.workspaces.select(workspaceId)).items);
  const addGlobalWorkspace = async () => { const path = await api.native.chooseDirectory(); if (path) updateWorkspaces((await api.workspaces.add(path)).items); };

  if (error) return <main className="startup-error"><WifiOff size={28} /><h1>本地研究服务未启动</h1><p>{error}</p><button onClick={() => location.reload()}>重试</button></main>;
  if (!bootstrap) return <main className="startup"><AppLogo /><LoaderCircle className="spin" size={19} /><span>正在连接本地 Python sidecar…</span></main>;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><AppLogo /><div><strong>EquiSeek 求衡</strong><span>智能投研平台</span></div></div>
        <nav aria-label="主导航">{navigation.map((item) => { const Icon = item.icon; return <button aria-label={item.label} key={item.id} className={view === item.id ? 'active' : ''} onClick={() => setView(item.id)} data-testid={`nav-${item.id}`}><Icon size={18} /><span>{item.label}</span>{runContexts[item.id].run?.status === 'running' && <i className="nav-running" />}</button>; })}</nav>
        <div className="sidebar-bottom">
          <button className="sidebar-repository-link" aria-label="在 GitHub 查看 EquiSeek" title="在 GitHub 查看 EquiSeek" onClick={() => void api.system.openRepository()}><GitFork size={14} /><span>GitHub 开源仓库</span><ExternalLink size={12} /></button>
          <div className="workspace-switch"><div className="workspace-avatar">QS</div><label><span>当前工作区</span><select aria-label="当前工作区" value={activeWorkspace(bootstrap.workspaces)?.id || ''} onChange={(event) => void selectGlobalWorkspace(event.target.value)}>{bootstrap.workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><button aria-label="新增工作区" onClick={() => void addGlobalWorkspace()}><Plus size={14} /></button></div><div className="local-status"><span><i />Sidecar 已连接</span><small>协议 v1.0</small></div>
        </div>
      </aside>
      <main className="main-content">
        <section className="view-pane" hidden={view !== 'agent'}><AgentWorkspace bootstrap={bootstrap} selectedSkills={selectedSkills} onRun={(run, nextGoal) => startRun('agent', run, nextGoal)} onWorkspaceChange={updateWorkspaces} onSettingsChange={(settings) => setBootstrap((current) => current ? { ...current, settings } : current)} onOpenSkills={() => setView('skills')} onOpenSettings={() => setView('settings')} /></section>
        <section className="view-pane" hidden={view !== 'research'}><ResearchWorkspace networkEnabled={Boolean(bootstrap.settings.enableNetwork)} defaultSource={String(bootstrap.settings.dataSource || 'baostock')} tushareConfigured={Boolean(bootstrap.credentials?.tushare)} initialRuns={bootstrap.recentRuns} onRun={(run, nextGoal) => startRun('research', run, nextGoal)} /></section>
        <section className="view-pane" hidden={view !== 'portfolio'}><PortfolioPage initial={bootstrap.portfolio} /></section>
        <section className="view-pane" hidden={view !== 'candidates'}><div className="workspace-page"><header className="page-header"><div><span className="eyebrow">CANDIDATE WORKBENCH</span><h1>候选池</h1><p>把本地持仓与自选交给 Agent，按显式策略 Skill 形成排序。</p></div></header><div className="candidate-cta"><div><ListChecks size={24} /><h2>候选池由 Agent 任务驱动</h2><p>当前有 {bootstrap.portfolio.positions.length + bootstrap.portfolio.watchlist.length} 个可研究标的。空候选池会直接返回，不启动无意义的网络请求。</p></div><button className="primary-button" onClick={() => setView('agent')}>前往 Agent 扫描 <ArrowUpRight size={16} /></button></div></div></section>
        <section className="view-pane" hidden={view !== 'macro'}><MacroPage networkEnabled={Boolean(bootstrap.settings.enableNetwork)} onRun={(run, nextGoal) => startRun('macro', run, nextGoal)} /></section>
        <section className="view-pane" hidden={view !== 'skills'}><SkillsPage bootstrap={bootstrap} selected={selectedSkills} onToggle={toggleSkill} onSkillsChange={(skills) => setBootstrap((current) => current ? { ...current, skills } : current)} /></section>
        <section className="view-pane" hidden={view !== 'settings'}><SettingsPage bootstrap={bootstrap} onUpdate={(settings) => setBootstrap((current) => current ? { ...current, settings } : current)} onWorkspaceChange={updateWorkspaces} onCredentialsChange={(credentials) => setBootstrap((current) => current ? { ...current, credentials } : current)} /></section>
      </main>
      {inspectorOpen && <Inspector run={runContexts[view].run} events={runContexts[view].events} goal={runContexts[view].goal} onClose={() => setInspectorOpen(false)} />}
      {!inspectorOpen && <button className="open-inspector" aria-label="打开运行检查器" onClick={() => setInspectorOpen(true)}><Activity size={17} /></button>}
    </div>
  );
}
