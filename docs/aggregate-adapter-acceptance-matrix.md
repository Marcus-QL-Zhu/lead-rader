# 聚合信源适配器 v2 验收矩阵

验收日期：2026-07-30
语义版本：`aggregate-semantic-v18`
模型：`minimax/MiniMax-M3`（直接使用 OpenClaw 所属 provider 配置，不调用 OpenClaw Agent）

## 验收口径

- 每个站点选择已结束的公开自然日或冻结窗口；
- 原始列表必须逐条建立索引，禁止在标题过滤阶段静默删除；
- 详情不可访问时必须记录失败；若公开列表 API 已提供完整快讯正文，允许使用可审计的列表正文回退；
- MiniMax 只能消解确定性事件种子的语义和主体歧义，不能新增没有规则种子的事实；
- 主体、事件、状态、金额、轮次、投资方和证据逐字校验；
- 通过门槛为：清晰增量遗漏为 0，主体/事件事实错误为 0；
- 完整版权正文仅保留在本地验收工作区，不提交 GitHub。

## 最终矩阵

| 信源 | 冻结窗口覆盖 | 语义验收 | 结果 | 说明 |
|---|---:|---:|---|---|
| 36氪融资快报 | 20 篇语义样本 | 21 个事件 | PASS | 0 遗漏、0 主体/事件/字段错误 |
| 投资界 Pedaily | 列表/详情 11/11；7 篇语义样本 | 9 个事件 | PASS | 融资、并购、技术里程碑均有逐字证据 |
| 创业邦 Cyzone | 列表/详情 27/27；2 篇语义样本 | 2 个事件 | PASS | 月之暗面主体及融资状态已修正 |
| 猎云网 Lieyun | 14 篇 | 5 个事件 | PASS | 9 篇无规则种子，不调用 MiniMax；无静默删除 |
| 动脉网 VBDATA | 2 篇 | 1 个事件 | PASS | 另一篇无规则种子 |
| 甲子光年 Jazzyear | 原始归档 45 条；目标 2 篇 | 4 个事件 | PASS | 政策、招采、合作、技术里程碑均已覆盖 |
| 智东西 Zhidx | 1 篇 | 1 个事件 | PASS | 新鲜 MiniMax 响应通过生产校验 |
| 财联社 CLS | 628 条索引；169 条当前路由；36 个残余/边界 fixture | 36/36 fixture 精确匹配 | PASS | 订单、扩产、监管、融资、IPO/M&A、合作等零清晰遗漏；英文主体保留 |
| 证券时报 STCN | 325/325 条索引；当前路由 128 条 | 冻结复核 14 个事件 | PASS（降级可用） | 详情页触发访问控制后不绕过，改用公开 API 的 `wap_content`；一篇 MiniMax 成功，一篇超时后保留已验真的规则事件 |
| 工信部 MIIT | 原始归档 24 条；窗口 1 条 | 1 个事件 | PASS | 0 列表/详情/主体/事件/历史错误 |

## MiniMax 最终验收

- 36Kr、Cyzone、Pedaily、Lieyun、VBDATA、Zhidx、Jazzyear 共复放 47 篇：
  - 36 篇有规则种子并通过生产等价校验；
  - 11 篇无规则种子，确定性跳过 MiniMax；
  - 11 条必须新跑的响应全部被生产解析与事实校验接受；
  - 生产 repair 调用为 0。
- STCN：
  - `4048267` 的 MiniMax 响应通过；
  - `4048192` 为 13 个事件的复合公告，MiniMax 空响应/超时被明确记录，最终保留 13 个已经逐条验真的规则事件；
  - 该降级路径不会丢弃规则事实，也不会生成未经证据支持的模型事实。
- Lieyun `502035` 没有规则种子。验收阶段曾观察到超时、空响应和截断思考内容；生产 v18 会在调用前跳过它，因此不消耗正式调用，也不影响事实结果。

## 访问控制与 Scrapling 边界

Scrapling 仅用于保存已验证元素特征，并在 CSS/XPath 漂移时重新定位元素。它不承担事实判断，也不使用 stealth、代理轮换、验证码处理或反爬绕过能力。

- STCN 详情页低频验收按每次至少 2 秒发起；
- 首次请求返回访问控制内容后立即停止；
- 未尝试绕过，失败和停止原因写入验收清单；
- 生产改用同一公开列表 API 已返回的完整快讯正文，保留来源 URL、文章 ID、时间戳和失败原因。

参考：

- [Scrapling GitHub](https://github.com/D4Vinci/Scrapling)
- [Scrapling adaptive scraping](https://scrapling.readthedocs.io/en/latest/parsing/adaptive.html)
- [Scrapling Selector API](https://scrapling.readthedocs.io/en/latest/api-reference/selector.html)

## 可复核产物

本地 `.acceptance/aggregate-v2/` 保存：

- 每站原始列表、详情和哈希；
- 规则种子、MiniMax 原始响应和最终事件；
- prompt/system prompt SHA-256；
- 独立审阅结果和错误计数；
- STCN 访问控制停止记录；
- v18 最终生产等价审计。

这些运行时验收产物受 `.gitignore` 保护；GitHub 只提交最小化 fixture、测试、验收矩阵和实现代码。

## 工程质量门禁

- `pytest -q`：653 passed；
- `ruff check .`：通过；
- `compileall -q src scripts`：通过；
- wheel 无隔离构建：通过，10 个适配器均包含在发行包内；
- `git diff --check`：通过；
- 独立 Full Code Review：原始 4 个 P1、1 个 P2 已全部修复并复核，无遗留 P0/P1。