import {
  REPOSITORY_URL,
  type BootstrapData,
  type ConversationState,
  type DesktopApi,
  type PortfolioBook,
  type RunEvent,
  type RunView,
} from '../shared/contracts';

const skills = [
  {
    name: 'a-share-market-data',
    description: '加载并核验 A 股日线、复权方式和数据截止日。',
    provider: 'builtin',
    version: '1.0.0',
    sourceLabel: '内置',
    model_invocable: true,
    user_invocable: true,
  },
  {
    name: 'technical-indicators',
    description: '用 MACD、WR 与多周期方向形成可追溯的技术研究结论。',
    provider: 'builtin',
    version: '1.0.0',
    sourceLabel: '内置',
    model_invocable: true,
    user_invocable: true,
  },
  {
    name: 'macro-official-freshness',
    description: '联网核验宏观官方页面的发布日期和证据时效。',
    provider: 'builtin',
    version: '1.0.0',
    sourceLabel: '内置',
    model_invocable: true,
    user_invocable: false,
  },
  {
    name: 'steady-long-term',
    description: '用户自定义的稳健长线筛选规则，可替换同名内置 Skill。',
    provider: 'user-1',
    version: '0.3.0',
    sourceLabel: '用户 Skill',
    model_invocable: true,
    user_invocable: true,
  },
];

let portfolio: PortfolioBook = {
  schema_version: 1,
  positions: [
    { symbol: '600050.SH', name: '中国联通', quantity: 2000, cost_price: 4.42, industry: '通信' },
  ],
  watchlist: [
    { symbol: '600519.SH', name: '贵州茅台', notes: '等待估值与趋势共振', industry: '食品饮料' },
  ],
};

const listeners = new Set<(event: RunEvent) => void>();
const runs = new Map<string, RunView>();
let sequence = 0;
const conversations = new Map<string, ConversationState>();

function conversationItems() {
  return [...conversations.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).map((state) => ({
    threadId: state.threadId,
    title: state.turns.find((turn) => turn.role === 'user')?.content.slice(0, 22) || '新对话',
    preview: state.turns.at(-1)?.content.slice(0, 38) || '',
    turnCount: state.turns.length,
    updatedAt: state.updatedAt,
  }));
}

const researchResult = (symbol: string, source = 'baostock'): Record<string, unknown> => {
  const isDemo = source === 'demo';
  const bars = Array.from({ length: 90 }, (_, index) => {
    const base = 4.08 + index * 0.004 + Math.sin(index / 6) * 0.11;
    return {
      date: new Date(2026, 4, 18 + index).toISOString().slice(0, 10),
      open: base - 0.025,
      high: base + 0.06,
      low: base - 0.07,
      close: base,
      volume: 8_000_000 + Math.sin(index / 4) * 1_000_000,
    };
  });
  const strategy = {
    version: 'macd-wr-mtf-2026.08.3',
    direction: 'bearish',
    direction_label: '下跌结构',
    direction_score: -3,
    risk_flags: ['月线零轴上多次金叉后转弱', '周线尚未形成买入共振'],
    timing: {
      action: 'wait',
      label: '等待入场触发',
      strength: 58,
      reasons: ['月线高位转弱', '日线 WR 尚未进入深度超卖后转强的触发窗口'],
    },
    macd: {
      monthly: { label: '月线', as_of: '2026-07-31', latest_available_as_of: '2026-08-20', provisional_excluded: true, dif: 0.182, dea: 0.211, histogram: -0.058, phase_label: '零轴上多次金叉后转弱', cross: 'death', zero_zone: 'above_zero' },
      weekly: { label: '周线', as_of: '2026-08-14', latest_available_as_of: '2026-08-20', provisional_excluded: true, dif: -0.018, dea: -0.006, histogram: -0.024, phase_label: '零轴附近弱势整理', cross: 'none', zero_zone: 'cross_zero' },
      daily: { label: '日线', as_of: '2026-08-20', latest_available_as_of: '2026-08-20', provisional_excluded: false, dif: 0.009, dea: 0.003, histogram: 0.013, phase_label: '零轴附近修复', cross: 'golden', zero_zone: 'cross_zero' },
    },
    wr: {
      monthly: { label: '月线', value: 32.18, zone_label: '中性', as_of: '2026-07-31' },
      weekly: { label: '周线', value: 63.42, zone_label: '中性', as_of: '2026-08-14' },
      daily: { label: '日线', value: 57.2, zone_label: '中性', as_of: '2026-08-20' },
    },
    decision_path: [
      { key: 'monthly_direction', title: '① 月线 MACD 定大方向', status: 'block', status_label: '风险', summary: '下跌结构；零轴上多次金叉后转弱', evidence: ['DIF 0.182 / DEA 0.211'] },
      { key: 'weekly_confirmation', title: '② 周线 MACD 判延续/调整', status: 'warn', status_label: '观察', summary: '零轴附近弱势整理', evidence: ['高周期尚未转多'] },
      { key: 'top_structure', title: '③ 检查背离与第二顶部', status: 'warn', status_label: '观察', summary: '保留高位结构风险', evidence: ['月线零轴上多次金叉后转弱'] },
      { key: 'wr_timing', title: '④ WR 只负责具体时机', status: 'warn', status_label: '观察', summary: '日线 WR10 57.20，尚无入场触发', evidence: ['等待深度超卖后方向转多'] },
      { key: 'final_action', title: '⑤ 输出动作与失效条件', status: 'block', status_label: '未通过', summary: '等待', evidence: ['触发强度 58/100'] },
    ],
  };
  const marketContext = {
    status: 'stock_not_bullish',
    status_label: '个股方向未转强',
    priority_label: '非优先候选',
    buy_gate_open: false,
    confidence_adjustment: -4,
    benchmark: { instrument: { name: '上证综指', symbol: '000001.SH' }, available: true, direction_label: '震荡结构', as_of: '2026-08-20' },
    sector: null,
    reasons: ['个股自身高周期方向未转强，不进入共振买入候选'],
  };
  return {
    kind: 'research',
    symbol,
    source,
    sourceKind: isDemo ? 'synthetic' : 'public-history',
    adjustment: 'qfq',
    asOf: '2026-08-20',
    fetchedAt: '2026-08-21T10:18:30+08:00',
    warnings: isDemo ? ['当前为离线演示数据，正式判断前需切换 BaoStock 并重新运行。'] : [],
    snapshot: {
      symbol,
      as_of: '2026-08-20',
      start_date: '2024-03-01',
      source: isDemo ? 'synthetic-demo' : 'baostock',
      source_kind: isDemo ? 'synthetic' : 'public-history',
      adjustment: 'qfq',
      formula_version: 'canonical-cn-2026.08.1',
      bars: 645,
      latest_close: 4.28,
      previous_close: 4.23,
      cache_status: 'disabled',
      warnings: isDemo ? ['当前为离线合成演示数据，不是真实证券行情，不得用于投资判断。'] : [],
      indicators: { MA5: 4.26, MA10: 4.23, MA20: 4.19, MA30: 4.16, MA60: 4.08, DIF: 0.009, DEA: 0.003, MACD: 0.013, K: 43.2, D: 39.8, J: 50.0, RSI6: 54.7, RSI12: 51.3, RSI24: 49.8, ATR14: 0.082, ATR20: 0.085, BOLL_LOWER: 4.01, BOLL_MID: 4.19, BOLL_UPPER: 4.37, WR6: 48.6, WR10: 57.2 },
    },
    advice: {
      action: 'wait',
      action_label: '等待',
      confidence: 58,
      confidence_label: '低',
      technical_confidence: 62,
      market_confidence_adjustment: -4,
      macro_confidence_adjustment: 0,
      current_price: 4.28,
      as_of: '2026-08-20',
      direction: 'bearish',
      direction_label: '下跌结构',
      action_zone_low: null,
      action_zone_high: null,
      invalidation_price: 4.02,
      invalidation_condition: '若收盘跌破 4.02，当前震荡修复假设失效。',
      thesis: ['月线 MACD 锚定大方向，周线确认延续或调整，日线只辅助执行。', 'WR 使用正向 0–100 口径，当前没有形成明确入场时机。', '大盘与板块门控没有通过，因此不能把日线修复直接解释成买点。'],
      evidence: ['MA5 已回到 MA20 上方', '日线 MACD 柱体转正但强度有限', '周线与月线仍未形成买入共振'],
      risk_controls: ['等待周线方向确认后重新运行', 'ATR 区间是波动参考，不是目标价', '周/月线收盘后重新计算', '单笔风险预算不超过计划上限'],
      limitations: ['规则研究不保证未来结果', '当前展示规则情景分，不是统计胜率', isDemo ? '离线合成数据不得用于真实投资判断' : '公开历史行情存在发布延迟，需核对数据截止日'],
      forecasts: [
        { trading_days: 5, direction: 'sideways', direction_label: '偏震荡', scenario_score: 48, probability_pct: null, expected_return_pct: null, sample_count: 0, price_range_low: 4.08, price_range_high: 4.46, basis_label: 'MACD/WR 规则情景分，不是统计胜率' },
        { trading_days: 10, direction: 'down', direction_label: '偏下跌', scenario_score: 42, probability_pct: null, expected_return_pct: null, sample_count: 0, price_range_low: 4.01, price_range_high: 4.55, basis_label: 'MACD/WR 规则情景分，不是统计胜率' },
        { trading_days: 20, direction: 'down', direction_label: '偏下跌', scenario_score: 38, probability_pct: null, expected_return_pct: null, sample_count: 0, price_range_low: 3.92, price_range_high: 4.62, basis_label: 'MACD/WR 规则情景分，不是统计胜率' },
      ],
    },
    strategy,
    marketContext,
    summary: `# ${symbol} 完整规则研究报告

## 数据快照

- **截止日期：** 2026-08-20
- **来源：** ${isDemo ? 'synthetic-demo（合成演示）' : 'BaoStock（联网公开历史行情）'}
- **样本：** 645 个日 K
- **公式：** \`canonical-cn-2026.08.1\`

## 投资结论

| 项目 | 结论 |
| --- | --- |
| 建议动作 | **等待** |
| 规则置信度 | 58 / 100 |
| 大方向 | 下跌结构 |
| 市场共振 | 非优先候选 |

### 判断依据

1. 月线 MACD 高位转弱。
2. 周线方向和日线 WR 时机尚未共振。
3. ${isDemo ? '当前数据为离线演示，不能代替真实行情。' : '已联网加载公开历史行情，并明确显示截止日。'}

> 失效条件：若收盘跌破 4.02，当前震荡修复假设失效。

## 方法边界

- 规则情景分不是统计胜率。
- ATR 区间不是目标价或收益承诺。
- 系统不会连接券商或自动下单。`,
    answerMode: 'local',
    outcome: {
      schema_version: 1,
      status: isDemo ? 'demo' : 'observing',
      status_label: isDemo ? '演示记录' : '未执行',
      action: 'wait',
      action_label: '等待',
      baseline_price: 4.28,
      baseline_as_of: '2026-08-20',
      latest_price: isDemo ? 4.28 : 4.35,
      latest_as_of: isDemo ? '2026-08-20' : '2026-08-22',
      price_change_pct: isDemo ? 0 : 1.64,
      decision_return_pct: null,
      trading_days: isDemo ? 0 : 2,
      is_real_market_data: !isDemo,
      methodology: isDemo ? '离线合成数据仅用于界面演示，不计算真实市场表现。' : '等待决策未假设成交，仅跟踪研究后价格变化。',
    },
    trace: [
      { stage: 'research-task', title: '加载并校验行情数据', status: 'succeeded', summary: `${source} · 截止 2026-08-20 · 645 根日 K`, agent_name: 'market-data-agent', skill_names: ['a-share-market-data'] },
      { stage: 'research-task', title: '本地计算完整技术指标', status: 'succeeded', summary: 'MA、MACD、KDJ、RSI、ATR、BOLL、WR', agent_name: 'indicator-agent', skill_names: ['technical-indicators'] },
      ...strategy.decision_path.map((item) => ({ stage: 'decision-gate', title: item.title, status: item.status, summary: item.summary, agent_name: 'decision-agent', skill_names: ['multi-timeframe-macd-wr'] })),
      { stage: 'guardrail', title: '输出风险与失效条件校验', status: 'succeeded', summary: '已校验非收益承诺和失效边界', agent_name: 'compliance-agent', skill_names: ['investment-output-guardrail'] },
    ],
    active_skills: [
      { name: 'a-share-market-data', provider: 'research-pipeline' },
      { name: 'technical-indicators', provider: 'research-pipeline' },
      { name: 'multi-timeframe-macd-wr', provider: 'research-pipeline' },
      { name: 'investment-output-guardrail', provider: 'research-pipeline' },
    ],
    artifacts: [{ name: `${symbol}-research-report.html`, path: `/workspace/artifacts/${symbol}-research-report.html`, media_type: 'text/html', size_bytes: 16420 }],
    chart: {
      formulaVersion: 'canonical-cn-2026.08.1',
      bars,
      ma5: bars.map((item) => Number((item.close + 0.02).toFixed(4))),
      ma20: bars.map((item) => Number((item.close - 0.03).toFixed(4))),
      macd: bars.map((_, index) => Math.sin(index / 8) * 0.04),
      wr10: bars.map((_, index) => 50 + Math.sin(index / 7) * 24),
    },
  };
};

function complete(run: RunView, result: Record<string, unknown>, progressType: string): void {
  const now = new Date().toISOString();
  const progress: RunEvent = {
    runId: run.runId,
    seq: 2,
    type: progressType,
    at: now,
    payload: { kind: 'step-ended', title: '形成结论与证据索引', status: 'succeeded' },
  };
  listeners.forEach((listener) => listener(progress));
  run.status = 'succeeded';
  run.lastSeq = 3;
  run.result = result;
  const ended: RunEvent = {
    runId: run.runId,
    seq: 3,
    type: 'run.succeeded',
    at: now,
    payload: { resultKind: result.kind },
  };
  listeners.forEach((listener) => listener(ended));
}

function start(kind: 'agent' | 'research', input: Record<string, unknown>): RunView {
  const run: RunView = {
    runId: `${kind}-browser-${++sequence}`,
    kind,
    status: 'running',
    createdAt: new Date().toISOString(),
    lastSeq: 1,
    error: null,
  };
  runs.set(run.runId, run);
  const started: RunEvent = {
    runId: run.runId,
    seq: 1,
    type: 'run.started',
    at: run.createdAt,
    payload: { kind },
  };
  queueMicrotask(() => listeners.forEach((listener) => listener(started)));
  setTimeout(() => {
    if (kind === 'research') {
      complete(run, researchResult(String(input.symbol || '600050.SH'), String(input.source || 'baostock')), 'research.progress');
      return;
    }
    const question = String(input.question || '研究 600050.SH 什么时候可以买入');
    complete(
      run,
      {
        kind: 'agent',
        run_id: run.runId,
        status: 'succeeded',
        answer_mode: 'local',
        answer: `## 600050.SH 买入条件研究

我先把“什么时候可以买”拆成 **数据时效、方向、触发条件、失效条件** 四部分。

| 检查项 | 当前状态 | 结论 |
| --- | --- | --- |
| 数据时效 | BaoStock 截止 2026-08-20 | 已显示来源与截止日 |
| 月/周方向 | 尚未共振 | 暂不满足买入门槛 |
| 日线 WR | 未形成触发 | 继续等待 |
| 市场门控 | 未通过 | 非优先候选 |

### 当前结论

- **动作：等待**
- **触发条件：** 周线 MACD 转强，且日线 WR 进入技术窗口后方向转多
- **失效条件：** 收盘跌破观察位后，重新计算全部周期

> 当前使用 BaoStock 联网公开历史行情；数据截止 2026-08-20，不等同于交易所实时行情。

已使用 BaoStock；如需把宏观环境叠加到结论，再运行“宏观研究”并通过官方时效门禁：

\`数据加载 → 多周期分析 → 市场门控 → 失效检查\``,
        warning: 'BaoStock 为公开历史行情，可能存在发布延迟；已显示数据截止日。',
        workspace: '/Users/demo/.aegisrun/user-data/investment-agent-workspaces/investment-online',
        model: {
          provider: String(input.modelProvider || 'deepseek-official'),
          base_url: 'https://api.deepseek.com',
          id: String(input.model || 'deepseek-v4-flash'),
          enabled: false,
          vision: input.model === 'deepseek-v4-flash-vision-exp',
        },
        workspace_context: {
          id: String(input.workspaceId || 'default'),
          path: bootstrap.workspaces.find((item) => item.id === input.workspaceId)?.path || bootstrap.workspaces[0]?.path,
          permission: String(input.workspacePermission || 'read-only'),
          tools: ['list_files', 'read', 'bash', ...(input.workspacePermission === 'workspace-write' ? ['write', 'edit'] : [])],
        },
        active_skills: [
          { name: 'a-share-market-data', provider: 'builtin', version: '1.0.0' },
          { name: 'technical-indicators', provider: 'builtin', version: '1.0.0' },
        ],
        tool_calls: ['market.load', 'research.analyze', 'artifact.write'],
        trace: [
          { stage: 'plan', title: '拆解研究目标', status: 'succeeded', summary: question, agent_name: 'investment-lead-agent', depends_on: [] },
          { stage: 'tool', title: '加载 A 股行情', status: 'succeeded', summary: '已联网加载 BaoStock 并记录截止日', skill_names: ['a-share-market-data'], tool_name: 'market.load', agent_name: 'market-researcher', depends_on: ['plan'] },
          { stage: 'tool', title: '多周期信号分析', status: 'succeeded', summary: '日/周方向尚未形成买入共振', skill_names: ['technical-indicators'], tool_name: 'research.analyze', agent_name: 'signal-researcher', depends_on: ['market.load'] },
          { stage: 'artifact', title: '生成 HTML 研究报告', status: 'succeeded', summary: '静态成果已写入工作区', skill_names: ['html-research-report'], tool_name: 'artifact.write', agent_name: 'reporter', depends_on: ['research.analyze'] },
        ],
        artifacts: [
          { name: 'investment-report.html', path: '/workspace/artifacts/investment-report.html', media_type: 'text/html', size_bytes: 18422 },
          { name: 'research-evidence.json', path: '/workspace/artifacts/research-evidence.json', media_type: 'application/json', size_bytes: 6281 },
        ],
      },
      'agent.progress',
    );
  }, 900);
  return run;
}

function startMacro(): RunView {
  const run: RunView = {
    runId: `macro-browser-${++sequence}`,
    kind: 'macro',
    status: 'running',
    createdAt: new Date().toISOString(),
    lastSeq: 1,
    error: null,
  };
  runs.set(run.runId, run);
  queueMicrotask(() => listeners.forEach((listener) => listener({
    runId: run.runId,
    seq: 1,
    type: 'run.started',
    at: run.createdAt,
    payload: { kind: 'macro' },
  })));
  const metrics = [
    ['m2_yoy', 'M2 同比', 8.0, '%', '2026-06', '中国人民银行'],
    ['tsf_stock_yoy', '社融存量同比', 7.4, '%', '2026-06', '中国人民银行'],
    ['rmb_loan_yoy', '人民币贷款同比', 5.2, '%', '2026-06', '中国人民银行'],
    ['bank_fx_settlement', '银行结汇', 20299, '亿元', '2026-06', '国家外汇管理局'],
    ['bank_fx_sales', '银行售汇', 16437, '亿元', '2026-06', '国家外汇管理局'],
    ['gdp_real_yoy', '实际 GDP 同比', 4.7, '%', '2026-H1', '国家统计局'],
    ['fixed_asset_yoy', '固定资产投资同比', -5.7, '%', '2026-H1', '国家统计局'],
    ['private_investment_yoy', '民间投资同比', -8.5, '%', '2026-H1', '国家统计局'],
    ['retail_sales_yoy', '社会消费品零售总额同比', 1.3, '%', '2026-H1', '国家统计局'],
    ['cpi_yoy', '居民消费价格同比', 1.0, '%', '2026-06', '国家统计局'],
    ['ppi_yoy', '工业生产者出厂价格同比', 4.1, '%', '2026-06', '国家统计局'],
    ['land_sale_revenue_yoy', '国有土地使用权出让收入同比', -31.5, '%', '2026-H1', '财政部'],
  ].map(([code, name, value, unit, period, source]) => ({ code, name, value, unit, period, source_name: source, source_url: 'https://www.gov.cn/', note: code === 'm2_yoy' ? '同口径官方历史基线' : '' }));
  const allocationTargets = [
    ['cash', '现金与货币工具', 'cash', 10, 13, 10, 16, '增配 +3pct', '货币基金/存款', '流动性缓冲'],
    ['rmb_fixed_income', '人民币高等级固收', 'fixed_income', 25, 28, 23, 33, '增配 +3pct', '国债/高等级债基', '降低组合波动'],
    ['domestic_broad', 'A 股宽基', 'equity', 20, 17, 12, 22, '减配 3pct', '宽基指数基金', '保留长期增长敞口'],
    ['dividend_quality', '红利质量', 'equity', 10, 13, 10, 16, '增配 +3pct', '红利质量指数', '偏向强现金流'],
    ['advanced_manufacturing', '先进制造', 'equity', 10, 10, 7, 13, '维持战略仓', '制造业主题指数', '捕捉结构性景气'],
    ['global_equity', '全球权益', 'equity', 15, 10, 7, 13, '减配 5pct', '全球宽基指数', '分散单一市场风险'],
    ['gold', '黄金', 'alternative', 10, 9, 6, 12, '维持战略仓', '黄金 ETF', '尾部风险对冲'],
  ].map(([key, label, asset_class, strategic_pct, target_pct, minimum_pct, maximum_pct, action_label, vehicles, purpose]) => ({ key, label, asset_class, strategic_pct, target_pct, minimum_pct, maximum_pct, action_label, vehicles, purpose, macro_rationale: '依据资本传导、成本压力与风险偏好综合调整', primary_risk: '价格波动、跟踪误差与流动性风险' }));
  const macroPlan = {
    profile: 'balanced', label: '稳健平衡', suitability: '有稳定现金流，可接受中等波动，希望兼顾增长与抗风险', horizon: '至少 7–10 年', drawdown_tolerance: '应能承受约 20% 左右、极端时期更高的阶段回撤', prerequisite: '先在组合之外留足 6–12 个月必要支出的应急金，偿还高息负债。', strategy_version: 'macro-allocation-2026.08.1', targets: allocationTargets,
    build_steps: [
      { order: 1, timing: '第 0 周', portfolio_pct: 40, instruction: '按目标比例建立第一批，权益只买核心指数', gate: '已另留应急金' },
      { order: 2, timing: '第 4 周', portfolio_pct: 20, instruction: '补目标缺口，不因短期上涨提高权益比例', gate: '宏观数据未降级' },
      { order: 3, timing: '第 8 周', portfolio_pct: 20, instruction: '按实际仓位与目标仓位差额投入', gate: '个人现金流没有恶化' },
      { order: 4, timing: '第 12 周', portfolio_pct: 20, instruction: '完成目标组合，新增资金优先补低配资产', gate: '重新通过宏观时效门禁' },
    ],
    rebalance_rules: ['每季度检查一次，不根据日内新闻调整长期组合。', '资产越过允许区间才触发再平衡，优先使用新增资金。', '宏观方向连续两个统计期确认，单次最多调整 5 个百分点。'],
    increase_risk_triggers: ['资本流速与实体传导连续两个统计期不低于 45。', '风险偏好达到 65 且民间投资、私人利润同步确认。'],
    decrease_risk_triggers: ['跨境方向与资本流量同步恶化。', '成本压力不低于 75 且私人部门传导低于 35。'],
    guardrails: ['不用融资、期权或借款放大仓位。', '全部个股合计不超过组合 10%，单只不超过 3%。', '基金买入前核对费用、跟踪误差、流动性和溢价。'],
  };
  const tasks = [
    ['macro_data', '加载并校验官方宏观快照', 'macro-data-agent', 'macro-official-data'],
    ['freshness_gate', '联网核验官方发布时效', 'macro-freshness-agent', 'macro-official-freshness'],
    ['capital_flow', '计算资本三流代理', 'capital-flow-agent', 'capital-three-flows'],
    ['cost_transfer', '计算成本转嫁压力链', 'cost-transfer-agent', 'cost-transfer-lens'],
    ['macro_synthesis', '合成配置与行业建议', 'macro-synthesis-agent', 'macro-investment-synthesis'],
    ['guardrail', '执行投资输出风险门禁', 'compliance-agent', 'investment-output-guardrail'],
  ].map(([id, title, agent, skill]) => ({ id, title, agent, status: 'succeeded', skills: [skill] }));
  setTimeout(() => complete(run, {
    kind: 'macro',
    validity: {
      status: 'current',
      status_label: '官方发布核验通过',
      current_decision_allowed: true,
      reason: '4/4 个官方来源完成核验，未发现晚于快照截止日的新发布，结构化基线可用于当前决策。',
      snapshot_as_of: '2026-06-30', age_days: 38, max_age_days: 45, newer_release_count: 0, checked_at: '2026-08-07T10:20:00+08:00',
      source_checks: [
        { key: 'nbs', name: '国家统计局', url: 'https://www.stats.gov.cn/sj/zxfb/', status: 'succeeded', latest_published_on: '2026-06-30', detail: '发布页可访问，未发现晚于基线的新统计发布' },
        { key: 'pboc', name: '中国人民银行', url: 'https://www.pbc.gov.cn/', status: 'succeeded', latest_published_on: '2026-06-30', detail: '金融统计发布页核验成功' },
        { key: 'safe', name: '国家外汇管理局', url: 'https://www.safe.gov.cn/', status: 'succeeded', latest_published_on: '2026-06-30', detail: '跨境收付发布页核验成功' },
        { key: 'mof', name: '财政部', url: 'https://www.mof.gov.cn/', status: 'succeeded', latest_published_on: '2026-06-30', detail: '财政收支发布页核验成功' },
      ],
    },
    analysis: {
      version: 'macro-three-flows-cost-transfer-2026.08.4', regime: '资本流量中性偏宽；跨境净流入；资本流速偏慢；成本转嫁压力偏高',
      snapshot: { version: 'cn-macro-official-h1-2026.2', label: '中国宏观官方基线（2026 年上半年）', as_of: '2026-06-30', metrics, methodology_sources: [['资本流量/流向/流速解释框架', 'https://www.weibo.com/'], ['资本化与制度成本转嫁', 'https://www.aisixiang.com/']], warnings: [] },
      capital_flow: {
        direction_score: 31, direction_label: '跨境流向接近平衡', volume_score: 62, volume_label: '资本流量中性偏宽', speed_score: 37, speed_label: '资本流速偏慢，金融与实体分化', transmission_score: 34, transmission_label: '实体传导受阻，资金供给未充分转成私人需求',
        bottlenecks: ['民间投资偏弱，金融资本未充分转成私人部门扩张。', '消费增速弱于 GDP，资金周转与终端需求存在分化。'], allocation_evidence: ['制造业投资相对整体投资更有韧性。', '政府融资占比较高，私人承接仍需确认。'],
        paths: [
          { name: '金融体系资本供给', dimension: '流量', score: 62, status: '分化', source: 'M2、社融、人民币贷款', channel: '银行与资本市场融资', destination: '政府、企业与居民资产负债表', investment_effect: '总量不弱，但需要继续检查资金流向与实体承接。', evidence: ['M2 8.0%', '社融 7.4%', '贷款 5.2%'] },
          { name: '跨境资本方向', dimension: '流向', score: 66, status: '净流入', source: '银行结售汇、涉外收付款', channel: '跨境结算与外汇市场', destination: '境内人民币资产与外汇流动性', investment_effect: '外部流动性改善，但不能单独证明资金进入股票。', evidence: ['结售汇净额 3862 亿元', '涉外收付款净额 2531 亿元'] },
          { name: '政策融资配置', dimension: '流向', score: 72, status: '畅通', source: '政府债券净融资', channel: '财政与政策项目', destination: '基建、公共服务与重点项目', investment_effect: '政策资本通道较强，需跟踪对私人需求的带动。', evidence: ['政府债券净融资 6.44 万亿元'] },
          { name: '私人部门实体承接', dimension: '流速', score: 31, status: '阻滞', source: '居民与民营企业现金流', channel: '消费、民间投资与工资收入', destination: '终端需求与私人资本形成', investment_effect: '私人投资与消费承接偏弱，约束高杠杆行业。', evidence: ['民间投资 -8.5%', '零售 1.3%'] },
        ],
      },
      cost_transfer: {
        pressure_score: 76, pressure_label: '成本转嫁压力偏高', channels: ['地产下行与土地财政收缩', '上游购进价向中下游利润转移', '公共成本与债务付息挤压'], offsets: ['农村收入增速高于城镇', '制造业投资相对整体投资更有韧性'],
        chains: [
          { name: '地产—土地财政链', pressure_score: 90, source: '房地产开发投资下行', channel: '土地出让收入与地方财政', bearer: '地方财政、地产链企业', beneficiary: '低杠杆现金流资产', investment_effect: '低配高杠杆地产链，关注现金流质量。', confirmation: '地产投资 -18.0%；土地收入 -31.5%', reversal_conditions: '销售、投资与土地收入连续改善' },
          { name: '财政—公共成本链', pressure_score: 68, source: '基金收入收缩', channel: '支出压缩与债务付息', bearer: '依赖地方回款的主体', beneficiary: '中央财政支持方向', investment_effect: '警惕地方应收账款暴露。', confirmation: '基金收入/支出同步收缩', reversal_conditions: '地方财政收入与回款周期改善' },
          { name: '上游价格—利润链', pressure_score: 71, source: '购进价格高于 CPI', channel: '原材料与中间品价格', bearer: '议价能力弱的中下游', beneficiary: '资源与强定价权企业', investment_effect: '优先选择能转嫁成本的强品牌/资源品。', confirmation: 'PPI 4.1%；CPI 1.0%', reversal_conditions: '购进价与终端价格剪刀差收窄' },
          { name: '收入—消费链', pressure_score: 62, source: '财产收入偏弱', channel: '居民资产负债表', bearer: '可选消费与小微企业', beneficiary: '必选消费与高股息现金流', investment_effect: '消费复苏需等待收入与资产效应确认。', confirmation: '零售 1.3%', reversal_conditions: '财产收入与零售连续回升' },
        ],
      },
      investment_view: {
        risk_appetite_score: 36, risk_appetite_label: '防守', equity_exposure: '权益风险预算中性偏低；新增风险优先给强现金流和制造业景气确认方向', style_tilt: ['大盘/质量优于纯小盘', '现金流优于高杠杆', '结构制造优于地产链普涨'],
        decision_summary: ['资本总量不弱，但流速与实体传导偏慢。', '成本转嫁压力主要集中在地产—土地财政和弱议价主体。', '偏配高端制造与稳定现金流，低配高杠杆地产链。'], default_allocation_profile: 'balanced', allocation_plans: [macroPlan, { ...macroPlan, profile: 'conservative', label: '保守防守' }, { ...macroPlan, profile: 'growth', label: '成长进取' }],
        sectors: [
          { sector: '高端制造', stance: 'overweight', stance_label: '偏配', confidence: 72, rationale: '结构性投资与政策资本相对占优。', confirmation: '订单、制造业投资与利润同步改善', risk: '外需和估值回落' },
          { sector: '公用事业/运营商/高股息', stance: 'overweight', stance_label: '偏配', confidence: 78, rationale: '强现金流更适合传导偏弱环境。', confirmation: '自由现金流和分红覆盖稳定', risk: '利率上行或资本开支超预期' },
          { sector: '券商', stance: 'neutral', stance_label: '中性', confidence: 55, rationale: '等待成交与风险偏好持续确认。', confirmation: '成交额与两融连续扩张', risk: '市场活跃度回落' },
          { sector: '房地产产业链', stance: 'underweight', stance_label: '低配', confidence: 84, rationale: '投资与土地财政链仍承压。', confirmation: '销售、投资、土地收入同时转正', risk: '政策反转速度快于预期' },
          { sector: '可选消费/小盘', stance: 'underweight', stance_label: '谨慎', confidence: 69, rationale: '私人投资与财富效应不足。', confirmation: '收入、零售与民间投资连续改善', risk: '低基数带来快速反弹' },
        ],
      },
      research_implications: ['资本总量与实体传导必须分开判断。', '行业偏配仍需与个股盈利、估值及技术时机交叉验证。'], limitations: ['内置结构化指标是官方历史基线，不是网页抓取的实时数值。', '联网核验只判断发布页时效，发现更新即使旧结论失效。', '系统不保证收益、不连接券商、不自动下单。'],
    },
    report: '# 宏观投资结论\n\n## 数据与时效\n\n- **基线：** 中国宏观官方基线（2026 年上半年）\n- **截止：** 2026-06-30\n- **联网核验：** 4/4 官方来源通过\n\n## 综合判断\n\n| 维度 | 分数 | 结论 |\n| --- | ---: | --- |\n| 资本流量 | 62 | 中性偏宽 |\n| 资本流速 | 37 | 金融与实体分化 |\n| 实体传导 | 34 | 传导受阻 |\n| 成本转嫁 | 76 | 压力偏高 |\n| 权益风险偏好 | 36 | 防守 |\n\n### 配置含义\n\n1. 权益风险预算中性偏低。\n2. 偏配强现金流、高股息与确认后的先进制造。\n3. 低配高杠杆地产链，券商等待成交确认。\n\n> 失效条件：任一官方来源出现晚于结构化基线的新发布，或快照超过 45 天，当前仓位结论立即停止使用。\n\n## 方法边界\n\n- 联网核验日期，不把网页片段冒充结构化指标。\n- 不构成收益承诺，不连接券商，不自动下单。',
    plan: { id: 'macro-browser-plan', status: 'succeeded', tasks }, workspace: '/Users/demo/.aegisrun/user-data/investment-agent-workspaces/macro-browser-plan',
    trace: tasks.map((task) => ({ title: task.title, status: task.status, summary: task.agent, agent_name: task.agent, skill_names: task.skills })),
    active_skills: tasks.map((task) => ({ name: task.skills[0], provider: 'builtin' })),
    artifacts: [{ name: 'macro-report.html', path: '/workspace/artifacts/macro-report.html', media_type: 'text/html', size_bytes: 12280 }],
  }, 'macro.progress'), 900);
  return run;
}

const bootstrap: BootstrapData = {
  settings: {
    dataSource: 'baostock',
    defaultSymbol: '600050.SH',
    adjustment: 'qfq',
    includeBuiltinSkills: true,
    enableNetwork: true,
    enableDeepSeek: false,
    deepSeekModel: 'deepseek-v4-flash',
    modelProvider: 'deepseek-official',
    modelBaseUrl: 'https://api.deepseek.com',
    agentPermissionMode: 'read-only',
    theme: 'light',
  },
  workspaces: [
    {
      id: 'default',
      name: '默认投资工作区',
      path: '/Users/demo/.aegisrun/user-data/investment-agent-workspaces',
      active: true,
      writable: true,
    },
  ],
  skills,
  portfolio,
  recentRuns: [],
  conversations: [],
  runtime: { mode: 'browser-fixture', database: 'SQLite + JSON', loginRequired: false, networkDefault: true },
  credentials: { deepseek: false, custom: false, tushare: false },
};

const browserApi: DesktopApi = {
  system: {
    bootstrap: async () => ({ ...bootstrap, portfolio, recentRuns: [...runs.values()], conversations: conversationItems() }),
    health: async () => ({ status: 'ok', protocolVersion: '1.0' }),
    openRepository: async () => REPOSITORY_URL,
  },
  settings: {
    patch: async (input) => Object.assign(bootstrap.settings, input),
  },
  workspaces: {
    list: async () => ({ items: bootstrap.workspaces }),
    add: async (path) => {
      const workspace = { id: `ws-${bootstrap.workspaces.length + 1}`, name: path.split('/').filter(Boolean).at(-1) || '本地工作区', path, active: true, writable: true };
      bootstrap.workspaces = [...bootstrap.workspaces.map((item) => ({ ...item, active: false })), workspace];
      bootstrap.settings.workspaceRoot = path;
      return { items: bootstrap.workspaces, activeId: workspace.id };
    },
    select: async (workspaceId) => {
      bootstrap.workspaces = bootstrap.workspaces.map((item) => ({ ...item, active: item.id === workspaceId }));
      const selected = bootstrap.workspaces.find((item) => item.active);
      if (selected) bootstrap.settings.workspaceRoot = selected.path;
      return { items: bootstrap.workspaces, activeId: workspaceId };
    },
  },
  credentials: {
    status: async () => bootstrap.credentials || { deepseek: false, custom: false, tushare: false },
    set: async (name) => { bootstrap.credentials = { ...(bootstrap.credentials || { deepseek: false, custom: false, tushare: false }), [name]: true }; return bootstrap.credentials; },
    clear: async (name) => { bootstrap.credentials = { ...(bootstrap.credentials || { deepseek: false, custom: false, tushare: false }), [name]: false }; return bootstrap.credentials; },
  },
  skills: {
    list: async () => ({ items: bootstrap.skills }),
    get: async (name) => {
      const skill = bootstrap.skills.find((item) => item.name === name);
      if (!skill) throw new Error(`unknown skill: ${name}`);
      return { ...skill, editable: skill.sourceLabel === '用户 Skill', content: `---\nname: ${skill.name}\ndescription: ${skill.description}\nversion: ${skill.version}\n---\n\n# ${skill.name}\n\n执行可审计的投资研究。\n` };
    },
    save: async (name, content) => ({ ...(await browserApi.skills.get(name)), content }),
    delete: async (name) => { bootstrap.skills = bootstrap.skills.filter((item) => item.name !== name); return { deleted: name, items: bootstrap.skills }; },
    importFile: async () => null,
    openRoot: async () => '',
  },
  research: {
    start: async (input) => start('research', input),
    history: async (input = {}) => ({
      items: [...runs.values()].filter((run) => run.kind === 'research' && run.status === 'succeeded').reverse().map((run) => structuredClone(run)),
      refreshed: Boolean(input.refresh),
    }),
  },
  agent: { start: async (input) => {
    const threadId = String(input.threadId || 'browser-default');
    const state = conversations.get(threadId);
    if (state) {
      state.turns.push({ role: 'user', content: String(input.question || '') });
      state.updatedAt = new Date().toISOString();
    }
    const run = start('agent', input);
    setTimeout(() => {
      const current = conversations.get(threadId);
      const answer = (runs.get(run.runId)?.result as Record<string, unknown> | undefined)?.answer;
      if (current && answer) {
        current.turns.push({ role: 'assistant', content: String(answer) });
        current.updatedAt = new Date().toISOString();
      }
    }, 950);
    return run;
  } },
  macro: { start: async () => startMacro() },
  runs: {
    get: async (runId) => {
      const run = runs.get(runId);
      if (!run) throw new Error(`unknown run: ${runId}`);
      return structuredClone(run);
    },
    listRecent: async () => ({ items: [...runs.values()].reverse().map((run) => structuredClone(run)) }),
    events: async (runId, afterSeq = 0) => ({
      items: runs.has(runId) && afterSeq < 1 ? [] : [],
    }),
    cancel: async (runId) => {
      const run = runs.get(runId);
      if (!run) throw new Error(`unknown run: ${runId}`);
      run.status = 'cancelled';
      return run;
    },
    delete: async (runId) => {
      runs.delete(runId);
      return { deleted: runId };
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  },
  conversations: {
    list: async () => ({ items: conversationItems() }),
    create: async () => {
      const threadId = `chat-browser-${++sequence}`;
      const state: ConversationState = { threadId, turns: [], summary: '', compressedTurnCount: 0, updatedAt: new Date().toISOString() };
      conversations.set(threadId, state);
      return structuredClone(state);
    },
    get: async (threadId) => {
      const state = conversations.get(threadId);
      if (!state) throw new Error(`unknown conversation: ${threadId}`);
      return structuredClone(state);
    },
    delete: async (threadId) => {
      conversations.delete(threadId);
      return { deleted: threadId };
    },
  },
  portfolio: {
    get: async () => portfolio,
    upsertPosition: async (input) => {
      portfolio = {
        ...portfolio,
        positions: [...portfolio.positions.filter((item) => item.symbol !== input.symbol), input],
      };
      return portfolio;
    },
    removePosition: async (symbol) => {
      portfolio = { ...portfolio, positions: portfolio.positions.filter((item) => item.symbol !== symbol) };
      return portfolio;
    },
    upsertWatch: async (input) => {
      portfolio = {
        ...portfolio,
        watchlist: [...portfolio.watchlist.filter((item) => item.symbol !== input.symbol), input],
      };
      return portfolio;
    },
    removeWatch: async (symbol) => {
      portfolio = { ...portfolio, watchlist: portfolio.watchlist.filter((item) => item.symbol !== symbol) };
      return portfolio;
    },
  },
  native: {
    chooseDirectory: async () => '/Users/demo/EquiSeek Workspace',
    chooseAttachments: async () => [{ token: 'browser-attachment', name: 'portfolio-note.md', mimeType: 'text/markdown', sizeBytes: 1024 }],
    openPath: async () => '',
  },
};

export const api: DesktopApi = window.aegisrun || browserApi;
