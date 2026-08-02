# Semantic v27 Final-v2 失败结论与后续约束

- 日期：2026-08-01
- Final-v2 状态：\`FAILED / FROZEN\`
- 用途：开发错误集，不再具有独立测试资格。

## 结论

Final-v2 的主要失败发生在 MiniMax 之前：Entity Ledger 向模型暴露了大量非公司主体，Action Ledger 又漏掉了多数真实经营动作并把复合动作合并。因此，继续只修改提示词无法达到生产要求。

原始 Final-v2 prediction、Gold 和 evaluation 保持不可变。后续只使用带 lineage 的 corrected Gold 诊断宿主层；最终通过必须来自严格未见的 Final-v3。

## 已确认的根因

1. 整篇文档路由同时承担结构切分和事实过滤，混合型融资稿、访谈、周报被错误压制。
2. 公司 discovery 与 eligible 资格没有真正分离；action subject、company surface、产品 owner、角色和 bulletin scope 会相互自证。
3. 公司 canonical 边界会吸入 \`旗下\`、\`拟与\`、\`与\` 及描述性长前缀。
4. Action 词表不完整，模型看不到宿主没有生成的合作、临床、产能、客户验证、高管变化等 Claim。
5. 复合句的动作、主体、状态和产品未充分原子化；跨句继承会扩大 evidence span。
6. 原 evaluator 把主体匹配与 evidence/type/status 混在一起，不能直接解释为主体精度。
7. 原 Gold 存在过度纳入和审计 provenance 不足，已用只增量修订的 lineage 版本纠正，原件不覆盖。

## 当前开发基线

- corrected Gold：86 个事件；所有目标 action 已能被 Ledger 发现，action claim recall 100%。
- eligible Entity 独立复审：84 个候选中 TP 30、FP 54，precision 35.71%，未通过。
- 当前首要瓶颈：Entity eligible precision，而不是 MiniMax Prompt。

## 固定后续顺序

1. 完成 Gold lineage 独立复核与 evaluator schema v2。
2. 把 eligible entity 收紧为可靠 seed + 唯一回指别名，实体 precision/recall 同时验收。
3. 稳定 Action 原子化和事件族覆盖。
4. 执行五轮、每轮三版的 MiniMax loop。
5. 用严格未见随机连续三篇和 10 个聚合适配器独立对照验收。
6. 最后进行 Director+ 三个月历史职位回测、全量工程门禁和三端发布。

详细门槛与变更控制见 \`docs/semantic-v27-development-v3-plan.md\`。
