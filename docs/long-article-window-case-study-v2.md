# 长文章窗口实测 v2

## 样本

- 来源：机器之心产业分析
- 文章 ID：`2026-08-01-2`
- 标题：`ChatGPT成「杀猪盘」帮凶，OpenAI出手封禁柬埔寨诈骗网络`
- 清洗后长度：`2,128` 字符
- 文章类型：`long_feature`
- Gold 事件：OpenAI 完成账号封禁、威胁指标共享及提高重新获取服务难度，类型为 `technical_milestone`

## 路由结果

```json
{
  "mode": "single_event_expansion",
  "reason": "concrete_lead_event_followed_by_long_form",
  "original_chars": 2128,
  "semantic_chars": 2000,
  "prefix_action_count": 5,
  "tail_action_count": 0,
  "prefix_has_concrete_event": true,
  "interview_cue_count": 0
}
```

这篇文章不是采访或周报，而是“一个事件 + 后续解释”。前 2,000 字已经出现完整的 OpenAI 运营动作，尾部 128 字主要是结语和参考链接，没有新增动作，因此语义提取器只接收前 2,000 字。尾部不是直接删除，而是替换为空格，保持原文字符偏移和证据引用位置不变。

## 主体与动作复核

- Lead-scope 主体：仅 `OpenAI`，没有把 ChatGPT、诈骗团伙、报告、媒体或受害者识别为公司线索。
- Gold 事件所在区间：字符 `1623–1691`，完全位于 2,000 字窗口内。
- 对应动作证据：`OpenAI 表示已封禁与该行动相关的账号，向行业伙伴和有关部门共享了威胁指标，并采取措施提高这些行为者重新获取其产品和服务的难度。`
- 结论：主体、动作和证据均保留；尾部没有信息遗漏。

底层动作候选还会产生少量“建立信任”“AI 带来的变化”等上下文性候选，这些不是公司运营事件，必须由后续 claim adjudication 拒绝，不能直接作为 Lead。该样本因此同时验证了：窗口路由通过，主体精度通过，语义裁决仍承担最后一道“把背景句拒绝掉”的职责。

## 验收结论

**通过（窗口策略）。** 该样本满足“前 2,000 字已说明重大事件，后文只是扩写”的规则；事件没有被截断，尾部没有引入新的公司或动作。它不能单独证明所有长文都通过，采访式和多事件汇总仍由对应的 `skip_low_value` / `multi_event_digest` 测试覆盖。
