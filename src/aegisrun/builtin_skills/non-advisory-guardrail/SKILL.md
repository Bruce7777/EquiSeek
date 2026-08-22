---
name: non-advisory-guardrail
description: 对研究输出执行非投顾合规检查，并阻止买卖、目标价、仓位和收益承诺。
version: 1.0.0
allowed-agents:
  - compliance-agent
allowed-tools: []
network-required: false
---

# 非投顾输出门

必须检查确定性摘要与模型摘要。出现个性化买卖指令、目标价、仓位、止损或收益承诺时拒绝输出。
