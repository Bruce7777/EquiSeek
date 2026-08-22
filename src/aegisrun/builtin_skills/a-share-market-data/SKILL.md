---
name: a-share-market-data
description: 获取并校验 A 股历史日 K 数据，保留数据源、复权方式和截止日期。
version: 1.1.0
allowed-agents:
  - market-data-agent
allowed-tools:
  - market-data-read
network-required: true
---

# A 股历史行情

只获取用户指定股票和日期范围的日 K。真实行情优先从本地 SQLite 读取，仅向上游补齐
尚未覆盖的日期；尾部增量请求包含一个重叠交易日，用来发现前/后复权历史重构。

缓存和输出必须按 `source + symbol + adjustment + trade_date` 隔离并记录 source、
adjustment、as_of、bar count、缓存命中/新增数量和数据警告。检测到重叠 K 线变化时，
只清除并重建同一来源、同一股票、同一复权口径的序列，禁止跨来源或跨复权混用。
离线模拟数据不得写入真实行情缓存。

不得把模拟数据表述为真实行情；少于 30 个有效交易日时停止后续分析。
