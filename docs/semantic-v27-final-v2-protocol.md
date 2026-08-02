# Semantic V27 Final-v2 一次性验收协议

- 状态：`FROZEN BEFORE SELECTION`
- 冻结日期：2026-08-02
- 目标：在不读取 Gold 的前提下，对冻结的 V27 语义合同做一次严格未见测试。

## 样本与排除

1. Final-v2 使用 20 篇正式样本：五类文档各 4 篇，不设置 reserve。
2. 20 篇仍位于用户原先要求的 10–20 家独立测试规模内；旧协议的
   40+20 已分别用于 final-v1 与其 reserve，不能通过复用来扩大样本。
3. 选择时严格排除 final-v1、reserve-v1/formal-v1、Development-v2 的文章键、
   canonical URL、正文哈希、近重复正文和公司主体。
4. 正式集必须覆盖融资、高管变动、产能、订单、合作、技术、临床/监管、
   并购/上市八个候选事件组。选择器只能依据确定性候选覆盖与文档配额，
   不读取 Gold。

## 顺序与不可变性

1. 先冻结 unlabelled manifest、bundle、bundle SHA-256 和代码合同 SHA-256。
2. 再运行且只运行一次 MiniMax 正式预测；预测程序不得接收 Gold 路径。
3. 预测完整落盘后，才允许两名独立标注者阅读文章并生成 Gold；第三名只裁决分歧。
4. 最终评估无论通过或失败均保留。读取 Gold 后不得修改语义合同、重跑预测并宣称独立通过。

## 硬门槛

- 编造主体或事件、无原文引用、JSON/投影失败、candidate 静默遗漏：均为 0；
- 公司主体准确率至少 98%；
- 强当前事件召回率至少 90%；
- `completed / started / target` 状态准确率至少 90%；
- 所有 Claim 必须进入 accepted/rejected/failed 之一，且 failed 为 0；
- 五类文档和主要事件类型分别报告，不能只看总体平均。

Final-v2 只评价冻结 V27 的泛化能力；它不替代历史 Director+ 招聘预测回测和十个信源适配器验收。
