# Semantic Event Gold 标注指南

- 版本：v1
- 日期：2026-08-01
- 用途：统一人工与 GPT-5.6-terra medium 对聚合新闻事件的标注口径

## 1. 标注对象

Gold 标注的是“该文章在发布时间向读者报告的公司经营事件”，不是文章中出现过的全部事实，也不是模型可能推断出的业务含义。

每条事件必须能够回答：哪个公司，在什么时间状态下，做了什么可验证动作。

## 2. 当前事件与历史背景

满足任一条件时，事实可以是当前事件：

- 标题或导语把它作为本篇主要新增事实；
- 正文用近期、日前、今日、宣布、完成、启动、正在等语义报告；
- 它是公司当前公开路线图中的明确未来动作；
- 同年发生且正文把它作为本篇最新进展，而不是履历、发展历程或比较背景。

满足任一条件时标记为历史背景，不生成当前事件：

- 原文明示此前、曾于、去年、上一轮、回顾、当时、早在；
- 出现在创始人履历、公司发展历程、融资历史或产品回顾中，仅用于解释本篇主事件；
- 日期虽与文章同年，但段落功能明确是履历或背景；
- 第三方案例只用于行业比较或评论。

同年不是自动当前，早于发布时间也不是自动历史。以文章叙事功能和明确时间语义共同判断。

## 3. 事件原子性

一个事件只能包含一个能够独立判断状态的动作。

例如“完成 A 轮融资，并计划建设新产线”必须拆成：

1. `funding / completed`；
2. `factory_or_capacity / target`。

同一融资事件的标题、摘要和正文重复描述不拆分；不同轮次、不同主体或不同状态动作必须拆分。

## 4. 状态

- `completed`：加盟、辞任、出任、签署、获批、发布、交付、完成融资等动作已经发生。
- `started`：融资接触、建设、临床、谈判、筹备等过程已经启动但尚未完成。
- `target`：计划、拟、将、预计、目标、力争的动作尚未开始或完成。

“已加盟、已入局、全面投身”属于已经发生的人员变化，标记 `completed`。同句中的目标金额或目标年份不能改变另一个已经启动动作的状态。

## 5. 主体

- 主体必须是承担经营动作的公司；
- 投资机构、人物、政府、媒体、协会、产品、项目、基地和时间短语不能冒充公司；
- 收购方、出售方、合作双方可以分别成为主体，但必须分别有相应动作；
- 公司简称或指代必须能够在同段或明确别名定义中唯一回指；不能依靠外部常识补全。

## 6. 证据与字段

- Gold 证据使用原始正文连续 Span；
- 标题可以提示主事件，但不能单独作为事实证据；
- 金额、轮次、累计金额和投资方必须在该事件证据中出现；
- 本轮金额不能填入累计金额；估值不能填入融资金额；
- 未具名投资人不进入投资人列表；
- 无法确定的可选字段留空，不允许推断。

## 7. 路线图与纯愿景

具体公司的明确路线图动作可以形成 `target`，例如计划启动临床、建设基地、扩产、量产或启动下一轮融资。

纯战略口号、行业趋势、能力描述、融资资金用途和媒体预测不形成事件，除非同时存在已经发生或明确计划的具体公司动作。

## 8. 分歧处理

评审不一致时：

1. 记录双方依据和原文 Span；
2. 按本指南重新判断叙事功能、主体、动作和状态；
3. 仍不能唯一判断时标记 `gold_ambiguous`；
4. `gold_ambiguous` 不计入训练硬正例，也不能用于宣称验收通过。

## 9. Gold JSON 契约

每个 case 的 `annotation` 必须使用以下结构：

```json
{
  "annotation_status": "complete",
  "candidate_dispositions": [
    {
      "claim_id": "candidate_claim_id_from_packet",
      "disposition": "accepted",
      "reason_code": "current_atomic_company_event"
    }
  ],
  "gold_events": [
    {
      "canonical_company": "原文可唯一回指的公司名",
      "event_type": "funding",
      "event_status": "completed",
      "importance": "strong",
      "atomic_discriminator": "funding_round=A轮",
      "claim_ids": ["candidate_claim_id_from_packet"],
      "candidate_gap": false,
      "evidence_span": {
        "text": "正文中的连续原文",
        "char_start": 12,
        "char_end": 30
      }
    }
  ]
}
```

契约要求：

- `candidate_dispositions` 必须恰好覆盖 packet 中所有 `required_claim_ids`，不多不少；
- `disposition` 只能是 `accepted / rejected / ambiguous`，并必须有非空 `reason_code`；
- 每个 `accepted` claim 必须且只能被一个 Gold event 的 `claim_ids` 覆盖；
- 没有候选 claim 但原文明示的真实事件使用空 `claim_ids` 和
  `candidate_gap=true`；有 claim 时 `candidate_gap=false`；
- `event_type` 使用系统允许的具体事件类型，不能使用 `other`；
- `event_status` 只能使用 `completed / started / target`，不能使用
  `cumulative`；
- `importance` 只能是 `strong / weak`；
- 通常省略 `atomic_discriminator`。只有同一公司、事件类型、状态和原文
  Span 对应多个可独立输出的原子事实时，它才是必填项，而且组内必须唯一；
  当前只允许 `funding_round=...`、`funding_amount=...` 或
  `cumulative_funding_amount=...`。例如同一句“完成 A 轮及 A+ 轮融资”必须拆成
  两个事件，并分别标记 `funding_round=A轮` 与 `funding_round=A+轮`；
- `char_start` 为正文中首字符的零基索引，`char_end` 为半开区间终点，必须满足
  `clean_body[char_start:char_end] == text`；不得手工改写、增加省略号或拼接不连续句子；
- `annotation_status=gold_ambiguous` 只用于整个 case 无法形成唯一裁决的情况。
