---
name: html-research-report
description: 把求衡投研助手的目标、结论、Skill、执行步骤与风险边界组织为安全、便携的本地 HTML 研究成果。
version: 1.0.0
allowed-agents:
  - investment-lead-agent
  - advice-agent
allowed-tools: []
network-required: false
resources: []
---

# HTML 研究成果

1. 报告必须先给结论，再展示目标、数据来源、Skill、执行步骤和风险边界。
2. 只向平台 `workspace.render_html` 工具提供纯文本或 Markdown 子集；不得生成或要求执行原始 HTML、JavaScript、远程样式、远程图片或追踪代码。
3. 证券研究必须保留数据截止日期、复权口径、触发条件和失效条件；宏观研究必须明确数据域是实时查询还是带截止日的历史快照。
4. 报告用于审阅和分享研究过程，不得把规则情景分表述成统计胜率，不承诺收益，不连接券商，不自动下单。
5. 用户目录中的同名 Skill 可以替换本说明与内容编排，但最终 HTML 始终由平台安全渲染器生成。
