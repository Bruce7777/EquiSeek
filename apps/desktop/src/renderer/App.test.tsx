import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { App } from './App';
import { api } from './bridge';

describe('EquiSeek desktop workspace', () => {
  it('loads a local-first agent workspace without login', async () => {
    render(<App />);

    expect(await screen.findByText('把研究问题交给求衡')).toBeInTheDocument();
    expect(screen.getByText('EquiSeek 求衡')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '投研助手' })).toBeInTheDocument();
    expect(screen.queryByText('AegisRun')).not.toBeInTheDocument();
    expect(screen.getAllByText('默认投资工作区').length).toBeGreaterThan(0);
    expect(screen.getByText('Sidecar 已连接')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /登录|注册/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /账号|用户名|密码/ })).not.toBeInTheDocument();
  });

  it('shows and opens the official source repository from the sidebar and settings', async () => {
    const user = userEvent.setup();
    const openRepository = vi.spyOn(api.system, 'openRepository');
    render(<App />);
    await screen.findByText('把研究问题交给求衡');

    await user.click(screen.getByRole('button', { name: '在 GitHub 查看 EquiSeek' }));
    expect(openRepository).toHaveBeenCalledTimes(1);
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByText('github.com/Bruce7777/EquiSeek')).toBeVisible();
    await user.click(screen.getByRole('button', { name: '访问 EquiSeek GitHub 仓库' }));
    expect(openRepository).toHaveBeenCalledTimes(2);
    openRepository.mockRestore();
  });

  it('shows replaceable user skills and makes the selection visible', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');

    await user.click(screen.getByTestId('nav-skills'));
    const customSkill = await screen.findByRole('button', { name: /steady-long-term/ });
    expect(customSkill).toHaveTextContent('用户 Skill');
    await user.click(customSkill);
    expect(customSkill).toHaveTextContent('本轮启用');
  });

  it('completes an agent run and exposes trace and HTML artifact', async () => {
    render(<App />);
    const composer = await screen.findByLabelText('向求衡投研助手提问');
    fireEvent.change(composer, { target: { value: '研究一下600050.SH什么时候可以买入' } });
    fireEvent.click(screen.getByTestId('send-agent'));

    expect(await screen.findByText('正在拆解目标并选择工具…')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/当前使用 BaoStock 联网公开历史行情/)).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByRole('heading', { name: '600050.SH 买入条件研究' })).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByText('动作：等待')).toBeInTheDocument();
    expect(screen.getAllByText('生成 HTML 研究报告')).toHaveLength(2);
    expect(screen.getByText('investment-report.html')).toBeInTheDocument();
    expect(screen.getByTestId('inspector-skills')).toHaveTextContent('html-research-report');
  });

  it('attaches a user-selected local file and keeps run inspection scoped by page', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByLabelText('向求衡投研助手提问');

    await user.click(screen.getByRole('button', { name: '添加文件' }));
    expect(await screen.findByText('portfolio-note.md')).toBeInTheDocument();
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByTestId('run-inspector')).toHaveTextContent('暂无运行');
  });

  it('shows full editable user Skill content and the three selectable DeepSeek V4 models', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');

    await user.click(screen.getByTestId('nav-skills'));
    await user.click(await screen.findByRole('button', { name: /steady-long-term/ }));
    const editor = await screen.findByLabelText('Skill 内容');
    expect(editor).not.toHaveAttribute('readonly');
    expect((editor as HTMLTextAreaElement).value).toContain('steady-long-term');

    await user.click(screen.getByTestId('nav-settings'));
    const model = screen.getByLabelText('DeepSeek 模型');
    expect(model).toHaveValue('deepseek-v4-flash');
    expect(model.querySelectorAll('option')).toHaveLength(3);
    expect(model).toHaveTextContent('V4 Pro');
    expect(model).toHaveTextContent('V4 Flash');
    expect(model).toHaveTextContent('Flash Vision Exp');
    expect(model.querySelector('option[value="deepseek-v4-flash-vision-exp"]')).toBeDisabled();
    expect(screen.getByLabelText('DeepSeek 供应商')).toHaveValue('deepseek-official');
    expect(screen.getByText(/官方 API 与自定义端点的凭据分别保存/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('DeepSeek 供应商'), 'openai-compatible');
    expect(model.querySelector('option[value="deepseek-v4-flash-vision-exp"]')).not.toBeDisabled();
    await user.selectOptions(model, 'deepseek-v4-flash-vision-exp');
    expect(model).toHaveValue('deepseek-v4-flash-vision-exp');
  });

  it('changes workspace, model, and file permission without switching Agent modes', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');

    expect(await screen.findByLabelText('Agent 工作区')).toHaveValue('default');
    await user.click(screen.getByRole('button', { name: '添加工作区' }));
    expect(screen.getByLabelText('Agent 工作区')).toHaveValue('ws-2');
    await user.selectOptions(screen.getByLabelText('Agent 模型'), 'deepseek-v4-pro');
    await user.selectOptions(screen.getByLabelText('Agent 工具权限'), 'workspace-write');

    expect(screen.getByLabelText('Agent 模型')).toHaveValue('deepseek-v4-pro');
    expect(screen.getByLabelText('Agent 工具权限')).toHaveValue('workspace-write');
    expect(screen.getByText('把研究问题交给求衡')).toBeInTheDocument();
    expect(screen.queryByText(/极简模式/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /管理 Skills/ }));
    expect(screen.getByText('Skill 管理')).toBeVisible();
  });

  it('renders the complete structured stock decision instead of a summary card', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');

    await user.click(screen.getByTestId('nav-research'));
    await user.click(screen.getByRole('button', { name: '开始研究' }));

    expect(await screen.findByText('Python 研究流水线正在执行')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('research-report')).toBeInTheDocument(), {
      timeout: 3000,
    });
    expect(screen.getByLabelText('研究数据状态')).toHaveTextContent('645 日 K');
    expect(screen.getByLabelText('置信度拆分')).toHaveTextContent('技术规则62');
    expect(screen.getByTestId('snapshot-metrics')).toHaveTextContent('RSI24');
    expect(screen.getByTestId('snapshot-metrics')).toHaveTextContent('BOLL 上');
    expect(screen.getByTestId('forecast-table')).toHaveTextContent('5 日');
    expect(screen.getByTestId('forecast-table')).toHaveTextContent('10 日');
    expect(screen.getByTestId('forecast-table')).toHaveTextContent('20 日');
    expect(screen.getByTestId('forecast-table')).toHaveTextContent('非概率');
    expect(screen.getByTestId('timeframe-matrix')).toHaveTextContent('月线');
    expect(screen.getByTestId('timeframe-matrix')).toHaveTextContent('周线');
    expect(screen.getByTestId('timeframe-matrix')).toHaveTextContent('日线');
    expect(screen.getByTestId('decision-path').querySelectorAll('.decision-step')).toHaveLength(5);
    expect(screen.getByTestId('evidence-sections')).toHaveTextContent('方法边界');
    expect(screen.getByTestId('evidence-sections')).toHaveTextContent('规则情景分，不是统计胜率');
    expect(screen.getByTestId('inspector-skills')).toHaveTextContent('multi-timeframe-macd-wr');
    expect(screen.getByRole('button', { name: /600050.SH-research-report.html/ })).toHaveTextContent('点击打开');
  });

  it('renders the complete deterministic report as semantic Markdown', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');
    await user.click(screen.getByTestId('nav-research'));
    await user.click(screen.getByRole('button', { name: '开始研究' }));
    await screen.findByTestId('research-report', {}, { timeout: 3000 });

    await user.click(screen.getByRole('tab', { name: '完整规则报告' }));
    const report = screen.getByTestId('full-markdown-report');
    expect(report).toContainElement(screen.getByRole('heading', { name: '600050.SH 完整规则研究报告', level: 1 }));
    expect(report.querySelector('table')).not.toBeNull();
    expect(report.querySelector('blockquote')).toHaveTextContent('失效条件');
    expect(report.querySelector('code')).toHaveTextContent('canonical-cn-2026.08.1');
    expect(screen.getByRole('button', { name: '打开本机 HTML 成果' })).toBeInTheDocument();
  });

  it('keeps the completed conversation alive while switching workspaces', async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText('向求衡投研助手提问');
    await user.click(screen.getByRole('button', { name: '新对话' }));
    fireEvent.change(composer, { target: { value: '研究 600050.SH 的买入条件' } });
    await user.click(screen.getByTestId('send-agent'));
    await screen.findAllByRole('heading', { name: '600050.SH 买入条件研究' }, { timeout: 3000 });

    await user.click(screen.getByTestId('nav-skills'));
    expect(await screen.findByRole('heading', { name: 'Skill 管理' })).toBeVisible();
    await user.click(screen.getByTestId('nav-agent'));
    expect(screen.getAllByRole('heading', { name: '600050.SH 买入条件研究' }).at(-1)).toBeVisible();
  });

  it('creates, switches, and restores local agent conversations', async () => {
    const user = userEvent.setup();
    render(<App />);
    const composer = await screen.findByLabelText('向求衡投研助手提问');
    await user.click(screen.getByRole('button', { name: '新对话' }));
    fireEvent.change(composer, { target: { value: '研究 600050.SH 的买入条件' } });
    await user.click(screen.getByTestId('send-agent'));
    await screen.findAllByRole('heading', { name: '600050.SH 买入条件研究' }, { timeout: 3000 });
    await waitFor(() => expect(screen.getByRole('button', { name: '新对话' })).toBeEnabled(), { timeout: 3000 });

    await user.click(screen.getByRole('button', { name: '新对话' }));
    expect((await screen.findAllByText('还没有消息')).length).toBeGreaterThan(0);
    await user.click(screen.getAllByRole('button', { name: /研究 600050.SH 的买入条件/ })[0]!);
    expect((await screen.findAllByRole('heading', { name: '600050.SH 买入条件研究' }))[0]).toBeVisible();
  });

  it('keeps the completed research report alive while switching workspaces', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');
    await user.click(screen.getByTestId('nav-research'));
    await user.click(screen.getByRole('button', { name: '开始研究' }));
    await screen.findByTestId('research-report', {}, { timeout: 3000 });

    await user.click(screen.getByTestId('nav-settings'));
    expect(await screen.findByRole('heading', { name: '设置' })).toBeVisible();
    await user.click(screen.getByTestId('nav-research'));
    expect(screen.getByTestId('research-report')).toBeVisible();
    expect(screen.getByTestId('decision-path').querySelectorAll('.decision-step')).toHaveLength(5);
  });

  it('adds completed stock research to replayable history', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');
    await user.click(screen.getByTestId('nav-research'));
    await user.click(screen.getByRole('button', { name: '开始研究' }));
    await screen.findByTestId('research-report', {}, { timeout: 3000 });

    const journal = screen.getByLabelText('个股决策账本');
    expect(journal).toHaveTextContent('600050.SH');
    expect(journal).toHaveTextContent('未执行');
    expect(journal).toHaveTextContent('当时价');
    expect(journal).toHaveTextContent('最新价');
    expect(journal).toHaveTextContent('+1.64%');
    await user.click(screen.getAllByRole('button', { name: /600050.SH等待未执行/ }).at(-1)!);
    expect(screen.getByTestId('research-report')).toBeVisible();
    expect(screen.getByTestId('run-inspector')).toHaveTextContent('回看 600050.SH 研究');
  });

  it('uses online public data by default with an explicit offline test option', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');

    expect(screen.getByText(/联网研究已开启/)).toBeInTheDocument();
    await user.click(screen.getByTestId('nav-research'));
    expect(screen.getByLabelText('数据来源')).toHaveValue('baostock');
    expect(screen.getByRole('option', { name: '专业行情 · Tushare（需 Token）' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '离线演示 · 仅测试' })).toBeInTheDocument();
    await user.click(screen.getByTestId('nav-settings'));
    expect(screen.getByRole('switch', { name: '使用联网公开数据' })).toHaveAttribute('aria-checked', 'true');
  });

  it('stores a Tushare token without exposing it to the research form', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');

    await user.click(screen.getByTestId('nav-settings'));
    const token = screen.getByLabelText('Tushare Token');
    expect(token).toHaveAttribute('type', 'password');
    await user.type(token, 'test-tushare-token');
    await user.click(screen.getByRole('button', { name: '保存 Tushare Token' }));
    await waitFor(() => expect(token).toHaveAttribute('placeholder', 'Tushare Token 已安全保存（输入可替换）'));
    await user.selectOptions(screen.getByLabelText('默认市场数据源'), 'tushare');

    await user.click(screen.getByTestId('nav-research'));
    expect(screen.getByLabelText('数据来源')).toHaveValue('tushare');
    expect(screen.getByRole('option', { name: '专业行情 · Tushare' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始研究' })).toBeEnabled();
    expect(screen.queryByDisplayValue('test-tushare-token')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('nav-settings'));
    await user.selectOptions(screen.getByLabelText('默认市场数据源'), 'baostock');
  });

  it('restores the complete macro decision dossier and semantic report', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText('把研究问题交给求衡');
    await user.click(screen.getByTestId('nav-macro'));
    await user.click(screen.getByRole('button', { name: '开始宏观研究' }));

    expect(await screen.findByText('正在抓取官方最新正文并重算')).toBeInTheDocument();
    const report = await screen.findByTestId('macro-report', {}, { timeout: 3000 });
    expect(report).toHaveTextContent('官方发布核验通过');
    expect(screen.getByTestId('macro-scores')).toHaveTextContent('资本流量');
    expect(screen.getByTestId('macro-scores')).toHaveTextContent('31/100');
    expect(screen.getByTestId('macro-scores')).toHaveTextContent('成本转嫁压力');
    expect(screen.getByTestId('macro-scores')).toHaveTextContent('权益风险偏好');

    await user.click(screen.getByRole('tab', { name: '长期配置' }));
    expect(screen.getByTestId('macro-allocation')).toHaveTextContent('现金与货币工具');
    expect(screen.getByTestId('macro-allocation')).toHaveTextContent('分批建立步骤');
    await user.click(screen.getByRole('tab', { name: '资本三流' }));
    expect(screen.getByTestId('capital-flow-paths')).toHaveTextContent('私人部门实体承接');
    await user.click(screen.getByRole('tab', { name: '成本转嫁' }));
    expect(screen.getByTestId('cost-transfer-chains')).toHaveTextContent('地产—土地财政链');
    await user.click(screen.getByRole('tab', { name: '行业配置' }));
    expect(screen.getByTestId('macro-sectors')).toHaveTextContent('公用事业/运营商/高股息');
    await user.click(screen.getByRole('tab', { name: '数据与来源' }));
    expect(screen.getByTestId('macro-sources')).toHaveTextContent('M2 同比');
    expect(screen.getByTestId('macro-sources')).toHaveTextContent('国家统计局');
    await user.click(screen.getByRole('tab', { name: 'Agent 计划' }));
    expect(screen.getByTestId('macro-agent-plan')).toHaveTextContent('macro-official-freshness');
    await user.click(screen.getByRole('tab', { name: '完整报告' }));
    const markdown = screen.getByTestId('macro-markdown-report');
    expect(markdown.querySelector('table')).not.toBeNull();
    expect(markdown.querySelector('blockquote')).toHaveTextContent('失效条件');
    expect(screen.getByTestId('inspector-skills')).toHaveTextContent('macro-investment-synthesis');
  });
});
