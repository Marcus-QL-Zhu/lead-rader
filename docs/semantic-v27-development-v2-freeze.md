# Semantic V27 Development-v2 冻结记录

- 冻结日期：2026-08-01
- 数据集：`semantic-v27-development-v2`（30 篇，已打开开发集）
- 冻结预测：`.acceptance/semantic-v25/v27-development-v2-r4-v10-full.json`
- 冻结评估：`.acceptance/semantic-v25/v27-development-v2-r4-v10-full-evaluation.json`
- Ledger 审计：`.acceptance/semantic-v25/v27-development-v2-ledger-audit-v30.json`
- 用途限制：本结果只证明开发集收敛，不属于独立测试。

## 冻结结果

| 指标 | 结果 |
| --- | ---: |
| Gold 事件 | 72 |
| 预测事件 | 71 |
| 精确匹配 | 71 |
| 精确率 | 100% |
| 精确召回率 | 98.61% |
| 公司主体准确率 | 100% |
| 强当前事件召回率 | 98.28% |
| 状态准确率 | 100% |
| Unsupported 事件 | 0 |
| Failed Claim | 0 |
| Strict-ready 文章 | 30/30 |

全部预先定义的 Development-v2 gate 已通过。唯一未命中的 Gold 事件不影响冻结门槛；禁止为了追求开发集 100% 再继续逐例调参。

## 本轮固定的通用约束

1. Article Entity Ledger 排除泛称公司、投资机构、产品、人物、公共机构和谓词片段。
2. Action Span Ledger 将复合融资轮次拆成独立 Claim，并锁定明确合同、量化扩建、战略合作及高置信当前融资。
3. 不同非空融资轮次不得互相作为重复事件删除。
4. DFI 注册、债务融资工具发行资格不得映射为已完成融资。
5. `host_mandatory` Claim 的模型可选字段即使不合规，也只降级为宿主锁定的最小有证据事件，不得整条静默丢失。
6. 每个 Claim 必须进入 accepted/rejected/failed 之一；最终事件必须带宿主 entity/action/span/claim ID。

## 冻结后的纪律

- Development-v2 从此只作回归审计，不再逐例调参。
- 下一次结论必须来自严格排除 final-v1、reserve-v1、formal-v1 和 Development-v2 的全新 `final-v2`。
- final-v2 预测只能运行一次；读取 Gold 后不得修改语义合同、重跑并声称独立通过。
- final-v2 无论通过或失败，都必须保留预测、Gold、评估、代码契约哈希和完整审计产物。
