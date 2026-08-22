---
name: steady-long-term
description: 稳健长线 A 股候选筛选示例，要求市场门控并偏好消费与医药行业。
version: 1.0.0
allowed-agents:
  - advice-agent
allowed-tools: []
network-required: false
resources:
  - strategy.json
---

# 稳健长线候选筛选

1. 只使用求衡已完成的确定性研究结果，不自行编造指标。
2. 按 `strategy.json` 过滤和排序本地持仓/自选池。
3. 输出时解释最低置信度、市场门控、行业偏好和排除条件。
4. 不承诺收益，不自动下单。
