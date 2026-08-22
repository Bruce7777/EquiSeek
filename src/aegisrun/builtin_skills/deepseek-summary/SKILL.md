---
name: deepseek-summary
description: 使用 DeepSeek 对结构化历史事实做语言整理，不增加预测和交易指令。
version: 1.0.0
allowed-agents:
  - language-agent
allowed-tools:
  - deepseek-chat
network-required: true
---

# 模型语言整理

模型只能整理输入事实，不得补充未经输入支持的价格、事件、评级、仓位或交易建议。服务失败时安全降级。
