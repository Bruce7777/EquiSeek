---
name: investment-output-guardrail
description: 允许有指标证据和回测依据的规则型投资建议，同时阻止收益保证、绝对涨跌判断和未经授权的自动交易声明。
version: 1.0.0
allowed-agents:
  - compliance-agent
allowed-tools: []
network-required: false
---

# 规则投资输出门

允许输出本地确定性引擎已经生成的买入、加仓、持有、减仓、卖出、等待、回避、方向预测、ATR 风险区间和失效条件。

必须拒绝稳赚、必涨、必跌、保本、保证收益、100% 胜率等绝对表达，也必须拒绝“已经替用户下单、自动成交”等虚构执行。模型不得增加输入中没有的数字或改变规则动作。
