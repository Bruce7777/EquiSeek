---
name: macro-investment-synthesis
description: 合成资本三流与成本转嫁事实，生成带来源、时点、行业偏配低配建议和失效条件的宏观投资报告。
version: 1.1.0
allowed-agents:
  - macro-synthesis-agent
  - macro-linkage-agent
allowed-tools: []
network-required: false
---

# 宏观投资研究合成

区分资本流量、流向、流速、实体盈利和资产价格，先形成风险偏好，再输出行业偏配、观察
或低配建议。个股关联只允许使用用户填写的行业标签做透明关键字映射；未填写或未命中时
保持 `unmapped`，不得猜测行业。宏观调整有上限，只影响置信度和候选排序，不能覆盖
MACD/WR 技术动作。每项配置必须有证据、截止时点、确认条件和风险，不从宏观总量直接
推出个股涨跌。不得承诺收益或声称已自动交易。
