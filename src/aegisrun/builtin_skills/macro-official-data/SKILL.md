---
name: macro-official-data
description: 加载并校验带统计期、单位和官方来源链接的中国宏观历史快照。
version: 1.0.0
allowed-agents:
  - macro-data-agent
allowed-tools: []
network-required: false
---

# 官方宏观数据快照

每项指标必须包含唯一代码、数值、单位、统计期、来源机构和 HTTPS 原始链接。内置基线必须标注历史截止日，不得表述为实时数据。自定义 JSON 采用相同结构和校验规则。
