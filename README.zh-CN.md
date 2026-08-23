# EquiSeek 求衡

[English](README.md) | 简体中文

> 求真知，衡风险，成策略。

求衡（EquiSeek）是一款面向个人投资者的本地优先、开源智能投研平台。它把投研助手、个股与宏观研究、持仓与自选管理、可复用 Skill、可恢复的 Goal 与 Plan、工作区工具和可审计运行轨迹集中在一个桌面应用中。

当前桌面产品是 `apps/desktop` 下的浅色 Electron 应用。它无需登录，不连接券商、不自动下单，也不承诺收益。

> **版本提示：** `make desktop` 启动当前 Electron 客户端。原深色 PySide/Qt 客户端仅通过 `make desktop-legacy` 保留作兼容验证，不再是默认发布产物。

## 主要能力

- 投研助手：持久会话、Markdown/HTML 成果、附件、Goal、Plan、子智能体和运行 Trace。
- 个股研究：本地计算 MA、MACD、KDJ、RSI、ATR、BOLL 和 WR 指标，提供多周期规则、行情时效、触发条件、失效条件和可追溯报告。
- 决策账本：成功的个股研究结论保存在本机并可随时回看；可选择用同源收盘价跟踪后续变化，且明确标注为假设结果而非真实交易业绩。
- 宏观研究：发现并核验官方发布，通过时效门禁后才更新资本流向、成本转嫁、配置或行业结论。
- 持仓与自选：通过界面或投研助手查询和维护本地持仓、自选与候选标的。
- 工作区与 Skill：在用户明确选择的工作区内使用持久 Shell 和受权限控制的文件工具；可以查看、启停、覆盖或扩展内置 Skill。
- 可选模型：没有 API Key 时仍能使用确定性本地研究；需要语言推理时可配置 DeepSeek 或 OpenAI 兼容供应商。
- Agent Harness：可恢复 Run、持久 DAG、审批、工具权限、幂等副作用、事件回放、Artifact、隔离工作区和确定性评测。

## 快速开始

| 目的 | 命令 | 是否需要 PostgreSQL |
| --- | --- | --- |
| 启动当前桌面应用 | `make desktop-install && make desktop` | 否 |
| 构建当前桌面应用 | `make desktop-build` | 否 |
| 生成当前平台桌面包 | `make desktop-package` | 否 |
| 启动本地 API 与 Worker | `make local-api` / `make local-worker` | 否，默认 SQLite |
| 运行本地 Harness 演示 | `make bootstrap && make demo-fake` | 否 |
| 运行多 Worker 部署 | `make up` | 是，由 Docker Compose 启动 |
| 启动旧 Qt 客户端 | `make desktop-legacy` | 否；仅兼容用途 |

## 从源码启动桌面应用

### 环境要求

- macOS 或 Windows
- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 24（Node.js 22 及以上也可）和 npm
- 首次安装依赖时需要网络

macOS 或 Linux：

```bash
git clone https://github.com/Bruce7777/EquiSeek.git
cd EquiSeek
make desktop-install
make desktop
```

`make desktop-install` 会同时安装 Python sidecar 和 Electron 依赖；之后日常开发只需执行 `make desktop`。

Windows PowerShell：

```powershell
uv sync --frozen --extra desktop --extra desktop-build --extra dev
npm ci --prefix apps/desktop
$env:EQUISEEK_REPO_ROOT = (Get-Location).Path
$env:EQUISEEK_PYTHON = "$env:EQUISEEK_REPO_ROOT\.venv\Scripts\python.exe"
npm --prefix apps/desktop run dev
```

如果看到旧的深色 Qt 窗口，通常是执行了 `make desktop-legacy` 或使用了旧安装包。请拉取最新代码并重新构建 Electron 客户端。

## 构建桌面应用

生成当前平台的未签名、解包应用：

```bash
make desktop-install
make desktop-build
```

构建产物位于 `apps/desktop/out/`。macOS 常见路径为：

```text
apps/desktop/out/EquiSeek-darwin-*/EquiSeek.app
```

生成当前平台的未签名测试包：

```bash
make desktop-package
```

产物位于 `apps/desktop/out/make/`：macOS 本地无证书构建为未签名 ZIP，Windows 为未签名 Squirrel Setup.exe。PyInstaller sidecar 与 Electron 应用必须在目标架构上原生构建。

不会编程的试用者可从 [GitHub Releases](https://github.com/Bruce7777/EquiSeek/releases) 下载三架构未签名 Alpha。请只下载文件名包含 `unsigned` 的对应平台产物，先核对 `SHA256SUMS`，并阅读 Release 中的 Gatekeeper/SmartScreen 提示。未来取得证书后可另行发布签名稳定版；详见[桌面发布](docs/releasing-desktop.md)。

也可以不使用 Make：

```bash
cd packaging
uv run pyinstaller --noconfirm --clean --distpath ../dist --workpath ../build EquiSeekSidecar.spec
cd ..
npm --prefix apps/desktop run package
```

## 启动本地 API 与 Worker

单用户本机安装默认使用 SQLite/WAL，不需要 PostgreSQL 或 Docker：

```bash
make bootstrap
make local-init
make local-api
```

另开一个终端：

```bash
make local-worker
```

- 主数据库：`~/.equiseek/user-data/equiseek.sqlite3`
- LangGraph checkpoint：`~/.equiseek/user-data/equiseek-checkpoints.sqlite3`

PostgreSQL 用于多用户、多个竞争 Worker 或服务端部署：

```bash
make up
curl -s http://127.0.0.1:8000/health
```

## 运行 Agent Harness 演示

```bash
make bootstrap
make demo-fake
open .equiseek/demo-report.html   # macOS
make test
```

Fake Model 是可重复、无需外部模型凭据的发布基线。API 请求只负责将 Run 入队；执行时还必须同时启动 Worker。

## 数据、模型与安全边界

- 桌面应用无需模型 API Key 也能运行；本地规则、持仓、Skill、工作区和历史记录仍可使用。
- BaoStock 提供无需 Token 的公开历史行情；Tushare 是独立可选数据源，需要自己的 Token。
- 宏观研究会联网核验官方页面；网络失败时只能回退到最近一次完整本地快照，并显示时效和失效状态。
- DeepSeek、Tushare、Tavily 等凭据互不替代；可用模型取决于所配置的供应商与 Endpoint。
- 用户数据默认位于 `~/.equiseek/user-data`，可通过 `EQUISEEK_USER_DATA_ROOT` 修改。
- Shell 和文件工具只作用于用户明确选择的工作区。macOS 使用 Seatbelt 隔离；所需隔离不可用时会拒绝执行。
- 工作区是权限与审计边界，不是面向恶意多租户工作负载的完整虚拟机沙箱。

## Skill 与工作区

用户 Skill 默认存放在：

```text
~/.equiseek/user-data/skills/<skill-name>/SKILL.md
```

可以在桌面 Skill 管理器中导入、查看、编辑、启停 Skill，也可以手动安装示例：

```bash
mkdir -p ~/.equiseek/user-data/skills
cp -R examples/user-skills/steady-long-term ~/.equiseek/user-data/skills/
```

Skill 可以编排已授权工具，但不能扩大文件、Shell、网络或证券证据权限。每次运行会记录实际使用的 Skill、工具参数摘要、数据来源、时效、计划状态和 Artifact。Trace 是可审计的过程摘要，不展示模型隐藏思维链。

## 品牌与迁移兼容

当前项目和产品统一命名为 **求衡 / EquiSeek**，面向用户的新命令使用 `equiseek`。旧版本的命令名与本地数据位置只作为迁移别名继续兼容，升级不会丢弃已有数据或脚本。

两份 README 具有同等效力。修改任意一份后，应同步更新另一份并刷新配对记录：

```bash
python scripts/verify_readme_pair.py --write
python scripts/verify_readme_pair.py
```

## 测试

当前 Electron 桌面链路：

```bash
make docs-check
make desktop-smoke
make desktop-package
```

Python Harness：

```bash
make lint
make typecheck
make test
make test-fault
```

## 旧 Qt 客户端

旧 PySide/Qt 客户端暂时保留，用于回归早期规则和迁移数据，但所有入口都带有 `legacy` 标识：

```bash
make desktop-legacy
make desktop-legacy-smoke
make desktop-legacy-build
make desktop-legacy-package
```

新用户和发布包应使用 Electron 客户端。旧版 Python 桌面命令只作为未公开的迁移别名保留。

## 文档

- [桌面端使用与研究方法](docs/desktop.md)
- [本地与服务端快速开始](docs/quickstart.md)
- [Agent 编排、工作区和沙箱](docs/orchestration.md)
- [架构边界](docs/architecture.md)
- [宏观研究方法与官方来源](docs/macro-analysis.md)
- [个股—大盘—板块共振](docs/market-sector-confluence.md)
- [威胁模型](docs/threat-model.md)
- [评测与发布门禁](docs/evaluation.md)
- [负责任使用与合规边界](docs/responsible-use.zh-CN.md)

## 许可证与责任边界

项目采用 [Apache-2.0](LICENSE) 许可证。仓库不包含任何任职公司的代码、Prompt、Schema、数据或业务流程。

求衡用于投资研究和软件工程参考，不构成收益承诺。开源或本地运行不会自动获得证券投资咨询监管豁免。若面向公众收费或提供针对具体证券的个性化建议，应另行评估业务许可、行情数据授权、适当性、留痕、隐私、AI 服务治理和营销规范。
