# 工作计划、子任务、工作区与沙箱

## 1. 工作计划

`ExecutionPlan` 是版本化、可序列化的有向无环图。任务状态增加 `superseded`，用于保留失败证据并切换到显式恢复分支。计划支持待执行任务的原子修订；修改 active/completed task、无效 DAG 或无原因变更会被拒绝。每次修订记录 actor、原因、时间和变更集合。

API Run 将计划存入数据库 `runs.runtime_state.plan`，同时镜像到工作区 `.state/plan.json`；桌面研究以原子 `plan.json` 为本地事实源。读任务中断可恢复为 `pending` 并增加恢复次数；副作用任务必须声明幂等键，中断后进入 `unknown_outcome`，不自动重放。取消、预算耗尽和运行异常会同步关闭计划中的未完成任务。

## 2. 子任务

`SubtaskExecutor` 按依赖选择 ready tasks，使用 `subtask_max_concurrency` 限制并发。任务有 handler、agent、skills、required capabilities、超时、最大尝试次数、网络、隔离、审批和副作用策略。审批任务在执行前进入 `waiting_approval`，批准不会计为一次执行尝试。失败任务按上限重试，最终失败后依赖任务自动跳过。

`LocalAgentRuntime` 使用 `AgentSpec` 注册本地专业子智能体，注册时发布 digest 派生的不可变 Profile generation，执行前检查 handler、Skill、工具、能力、网络、最大深度和最大 child 数量。每次普通委派通过 one-shot `SubagentProvider` 返回可取消、可释放的 RunHandle，并形成配对的 start/end 事件。委派仍保留总预算、单 Agent 并发上限和 `.state/delegations.json` 兼容台账。当前股票研究计划仍只允许 lead-agent 到专业 Agent 的单层委派，不提供模型可见的递归 delegate 工具。

Harness 另提供 `ContinuableSubagentManager` 作为二次开发 seam：durable descriptor 固定 profile generation、tool filter、sandbox override 和 `approval=never`；每个 child 同时只允许一个 activation；并发 follow-up 按 FIFO 进入独立 Turn；`subagent/report` 与 runtime `subagent/settled` 分离；重启后可以从事件冷恢复。恢复发现未结算 Turn 时只记录 `outcome unknown`，不自动重放可能已经产生副作用的工作。该机制当前作为框架能力开放，尚未替换股票研究的确定性 one-shot DAG。

## 3. Prompt、请求与对话表面

`PromptRegistry` 分别管理有序 System section、动态 Context、严格变量和 Tool Schema 快照。父子 registry 以近层覆盖，注册可撤销；缺失变量、多个 complete prompt 或非 JSON Tool Schema 会失败关闭。`RuntimeContextProjection` 仅在上下文 digest 改变时追加模型可见的 user message，并对上下文清空生成显式通知。

`.state/events.jsonl` 是 Raw Event 事实流；`derive_surface` 只选择 user/assistant/tool message，并应用 `surface/replace` 生成 Conversation Surface。每次模型请求前写 `request/header`，包含完整 system、tool schemas、effective/default config、prompt/request/messages digest、surface event seq 和 credential reference；随后 `model/request.header_seq` 必须精确引用该 Header。凭据字段不能进入 `ModelRequestEnvelope`，DeepSeek API Key 仅存在于 HTTP Authorization header 或操作系统密钥库。

## 4. Skill 渐进加载

`SkillCatalog` 发现时只形成名称、描述和策略摘要；任务实际启动时才加载 `SKILL.md` 正文及显式声明的 UTF-8 资源。加载会校验 Agent/工具/网络白名单、目录名、重复名称、符号链接、路径逃逸、文件大小与 SHA-256。Skill 不会自动执行脚本；可执行能力必须由应用代码注册。

`SkillRegistry` 在 Catalog 之上提供多 Provider、rank、完整性和 last-good 语义。Provider 报告不完整或暂时失败时，不会用半份目录替换最后一次完整快照。`disable-model-invocation` 与 `user-invocable` 分别控制模型和用户调用面，Skill 文本本身不能授予工具或网络权限。

投资桌面端在 Registry 上增加 `SkillWorkspace`：内置 Provider rank 为 100，用户目录 Provider 从 300 起，因此同名用户 Skill 会替换内置定义；设置 `include_builtin=false` 可完全不注册内置 Provider。用户可用 `/skill-name 问题` 显式激活单轮 Skill。当前轮正文按需加载，长期上下文只保存名称、Provider、版本和 hash 引用。

候选筛选进一步使用 `aegisrun-candidate-strategy/v1` 声明式资源契约。Skill 只能缩小候选范围、设置过滤阈值和组合既有确定性分数；平台先排除退出、卖出、减仓和回避动作，再应用用户规则。解析器不接受代码字段，所有权重、分数和结果数量均有边界，因此 Skill 包仍是受审计的数据输入而不是脚本执行单元。

## 4.1 投资对话、记忆与压缩

`InvestmentIntentRouter` 是投资页面的 lead-agent 前置路由，只选择筛选、策略、个股、持仓或 Skill 能力，不产生证券动作。`InvestmentConversationStore` 按用户和证券线程保存最近对话；超过阈值后，`InvestmentContextEngine` 把旧消息压成带角色和业务意图的摘要，最近消息保持原文。

长期 `InvestmentMemory` 只接受用户明确表达的风险类型、投资周期、最大回撤、偏好/回避板块和策略偏好。持仓数量、成本、备注、证券代码、凭据和一次性许可不进入长期记忆。模型请求把摘要、偏好和 Skill 引用作为低权限 data message，把最新确定性证券证据作为独立只读通道；压缩不会替代行情事实或规则动作。

## 4.2 投资长程 Agent

`InvestmentAgentRuntime` 在上述上下文之上运行有界 Lead Agent 循环，默认最多 8 步。DeepSeek 规划适配器只能返回注册工具名和 JSON 参数；没有模型时确定性规划器使用同一工具注册表。可用工具涵盖组合快照、当前证据、本地宏观快照、既有个股研究图、候选筛选、可选 Tavily 搜索以及当前运行 workspace 内的受限文本读写。

每个工具动作写入配对开始/结束事件，运行状态原子写入 `.state/investment-run.json`，最终自动生成 `investment-agent-report.md`。密钥不进入请求状态、事件或成果，组合上下文默认排除数量、成本和备注。用户 Skill 只能影响规划说明和已声明策略资源，不能增加 Shell、宿主文件或网络权限。

投资循环外层由确定性的 `AgentLoopHarness` 管理控制状态：默认建立可审阅的两阶段计划，任意时刻最多一个项目为 `in_progress`。运行时生成的兜底计划可以随工具结果自动推进；模型通过 `plan.update` 提交完整快照后，计划所有权切换给模型，单次工具成功不再被错误等同于研究任务完成。

工具目录先经过 `PolicySnapshot` 过滤，再计算稳定 SHA-256 指纹。模型首步只接收已晋升工具的完整 Schema，同时可看到未晋升工具的轻量名称、描述和风险目录；需要能力时可通过用途搜索或 `select:tool.name` 精确晋升。晋升只改变模型可见面，不能新增 Policy 未授权的工具。连续同工具同规范化参数调用会在第 3、5、8 次给出分级提醒，但不会仅因重复而阻止执行；未知或未晋升工具仍会失败关闭。

单条和整轮工具观察都有字符预算。超限结果完整保存在当前 Run 的 `.state/tool-results`，模型先获得摘要、预览、相对路径、真实文件 SHA-256 和大小；需要核查时使用始终晋升的只读 `tool_results.read` 按字符分页读取。该工具只接受当前 Run 内的常规 JSON 结果文件，拒绝路径穿越、符号链接和缺失文件，单页上限 5000 字符。Harness 与普通工具 Trace 优先从 append-only `events.jsonl` 重建，并附事件序号和证据路径；这些内容是可审计执行摘要，不是模型隐藏思维链。

该能力借鉴长任务、渐进 Skill、工作区和成果管理思想，但不是无边界通用代码 Agent：只在用户明确选择的工作区和权限模式下提供受控文件工具与持久 Shell，不执行 Skill 脚本，也不在进程中断后自动恢复顶层循环。目录指纹已为未来恢复时废弃过期晋升状态提供依据，但真正的顶层 resume 尚未实现。单机持久化继续使用 SQLite；只有未来多 Worker 任务租约才需要 PostgreSQL。

## 5. 工作区

```text
workspace://RUN_ID/
├── shared/                 # 同一 Run 的受控共享输入/源码
├── tasks/TASK_ID/
│   ├── input/context.json  # 该任务最小输入快照
│   ├── output/result.json  # 结构化结果
│   ├── logs/               # 有界错误信息
│   └── tmp/                # 临时文件
├── artifacts/              # 中间产物
├── .state/
│   ├── workspace.json
│   ├── plan.json
│   ├── delegations.json
│   └── events.jsonl       # append-only Harness Session 事实流
└── logs/
```

Run/Task ID 只能使用安全字符；路径经过规范化并拒绝绝对路径、`..` 和符号链接逃逸。JSON 状态先写同目录临时文件再原子替换。`workspace_max_bytes_per_run` 是应用层检查点配额；清理接口目前只提供显式的单任务清理，以避免自动删除审计证据。

## 6. 沙箱策略

`SandboxPolicy` 统一声明网络、是否强制隔离、工作区只读、输出上限、内存、CPU、PID 和显式环境变量。每次结果和能力探针使用 `full/partial/none` 如实报告 enforcement。Local 后端为 `none`，只用于可信开发：不经过 shell、清理继承环境、限制输出和超时，但不能禁止网络，也不是安全边界。若任务或部署要求严格隔离，Local 后端会拒绝运行。

Docker 后端报告 `full`，默认禁网，根文件系统只读，丢弃全部 capabilities，启用 `no-new-privileges`，使用 UID/GID 65532，限制 CPU/内存/PID，`/tmp` 使用 `noexec,nosuid` tmpfs，并只挂载当前任务工作区。`/api/capabilities` 可供部署探针验证实际后端及控制能力，同时保留原有布尔字段兼容旧客户端。

桌面研究的内置 Agent 默认进程内运行，因此不依赖 Docker 或 Kubernetes。工作区是审计和数据隔离机制，不是恶意代码安全边界；高风险代码执行仍应显式选择 Docker 后端。

## 7. API 与运维

- `GET /api/capabilities`：计划、子任务、Prompt/Surface/request envelope、Profile generation、one-shot/continuable child、工作区、沙箱能力与是否为安全边界。
- `GET /api/runs/{run_id}/plan`：计划与任务状态、尝试和恢复次数、摘要。
- `GET /api/runs/{run_id}/workspace`：逻辑工作区 ID、初始化状态、任务列表、用量和配额；不暴露宿主机路径。

不可信代码部署必须设置 `EQUISEEK_SANDBOX_BACKEND=docker` 和 `EQUISEEK_SANDBOX_REQUIRE_ISOLATION=true`。Compose 默认是可信演示栈，不能被当作多租户沙箱。
