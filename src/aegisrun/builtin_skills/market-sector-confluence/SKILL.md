---
name: market-sector-confluence
description: 用同源历史日 K 计算个股、大盘和行业指数代理的多周期趋势共振，买入优先选择方向同步的候选。
version: 1.0.0
allowed-agents:
  - market-context-agent
allowed-tools:
  - market-data-read
network-required: true
disable-model-invocation: true
user-invocable: false
---

# 大盘与板块趋势共振

1. 大盘按上市板块选择上证综指、深证成指、创业板指或科创 50。
2. 大盘与个股使用同一行情来源，并分别按来源、证券和不复权口径缓存。
3. 板块只在用户请求时加载；使用明确标注的中证一级行业指数作为趋势代理。
4. 方向仍由月/周/日 MACD 结构生成，WR 只负责时机，不改变高周期方向。
5. 买入或加仓信号必须通过大盘门控；板块加载后还必须通过板块门控。
6. 卖出、减仓或回避信号不得被大盘或板块的正向趋势覆盖。
7. 数据不可用、来源不一致或板块映射未知时必须显式降级，不得猜测。
