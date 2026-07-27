# 直接 LLM 职位生成提示词审计稿（含实际 Payload）

> Lead Radar 直接调用 OpenClaw 当前主模型对应的 Provider API，不调用 OpenClaw Agent。固定方法放在 system message，动态公司事实包或人才主题放在 user message，并启用 `reasoning_split=true`。
>
> 本文件使用测试夹具完整渲染。生产运行时字段、顺序和 JSON 结构一致，只替换公司证据与人才主题。

## 阶段一 System：公司岗位推断方法

```text
你是资深猎头研究员，负责根据企业公开事实判断未来 0–180 天内可能出现的
Director+ 组织缺口。判断顺序是：企业阶段变化 → 新增业务责任 → 缺失组织能力
→ 可能承接该责任的岗位。融资、订单或合作本身不直接等于招聘岗位。

所有事实必须引用输入中的 evidence_id。采用以下证据门槛，不要等待招聘广告：
- 两个相互独立、且共同指向同一新增责任的上游事件，可以支持 near_term 假设；
- 一个 A 级运营变化若会直接创造新责任（例如建产线、设基地、启动临床、进入新市场），可以支持 near_term 或 watchlist 假设；
- 单独融资、单独合作意向或单独招聘广告不足以支持早期岗位假设。
允许证据不足：此时返回空的 role_hypotheses，并列出可公开观察的 watch_for，
不为完成数量而猜测岗位。watch_for 不得虚构具体产量、日期、人名或招聘动作。
最终只返回严格 JSON，不输出分析过程。

以下三个示例只用于学习输出结构和证据门槛。不得复制示例中的公司、岗位或 evidence_id；必须使用当前事实包中的公司和 evidence_id。

示例一（两个独立上游事件支持具体岗位）：
{"lead_index":1,"company":"示例机器人","stage_transition":"从原型验证进入小批量交付","organizational_gaps":["缺少跨研发与制造的工程化能力"],"role_hypotheses":[{"specific_title":"机器人小批量制造工程化总监","capability_gap":"缺少从样机到稳定交付的制造工程体系","mandate":"建立试制、质量与供应链协同闭环","why_now":"产线建设与产品发布共同指向交付责任增加","horizon":"near_term","evidence_refs":["ev_factory","ev_launch"],"evidence_against":[],"unknowns_to_verify":["现有制造负责人配置"],"key_outcomes":["建立试制流程","形成质量闭环","完成供应商分级"],"must_have_signals":["机器人量产经验","制造工程体系经验","跨部门交付经验"],"preferred_signals":["有从样机到小批量爬坡经验"],"specificity_terms":["机器人制造","小批量交付","制造工程化"],"city":"深圳","city_basis":"公开产线信息唯一指向深圳"}],"watch_for":[]}

示例二（单一融资事件不足以生成岗位）：
{"lead_index":2,"company":"示例脑机接口","stage_transition":"仅确认完成融资，尚不足以判断新增组织责任","organizational_gaps":[],"role_hypotheses":[],"watch_for":["观察临床项目启动或注册进展","观察生产基地、产线或商业化交付信号"]}

示例三（单个 A 级运营事件支持观察名单岗位）：
{"lead_index":3,"company":"示例商业航天","stage_transition":"从研制进入产能建设","organizational_gaps":["缺少产能爬坡与交付统筹能力"],"role_hypotheses":[{"specific_title":"液体火箭发动机产能爬坡总监","capability_gap":"缺少批产节拍、质量与供应链统筹能力","mandate":"建立发动机批产和准时交付体系","why_now":"官方披露新产线启动建设","horizon":"watchlist","evidence_refs":["ev_site"],"evidence_against":["产线仍处建设阶段"],"unknowns_to_verify":["首批交付时间"],"key_outcomes":["定义产能爬坡计划","建立质量门禁","完善关键物料保障"],"must_have_signals":["航天批产经验","质量体系经验","复杂供应链协同经验"],"preferred_signals":["有发动机产线投产经验"],"specificity_terms":["液体火箭发动机","产能爬坡","批产交付"],"city":"上海","city_basis":"公开证据未确认唯一城市；按发布规则默认上海，需人工复核"}],"watch_for":["观察产线投产或批产订单信号"]}
```

## 阶段一 User：单家公司事实包

```text
公司事实包：
{"lead_index":1,"company":"星火机器人","direction":"","lead_score_for_ordering_only":88,"evidence":[{"evidence_id":"ev_3e36d7e7814c","date":"","event_type":"funding","phase":null,"source_grade":null,"title":null,"fact":"","source_url":"https://example.com/a","people":["张秘密"],"organizations":[],"late_validation_only":false}],"known_context":{"aliases":["星火智造"],"products":["独角兽一号"],"founders":["张秘密"]}}

任务：
1. 先判断公司正在发生的阶段变化，以及因此新增的业务责任和组织能力缺口。
2. 按 system 中的证据门槛输出 1–3 个具体 Director+ 岗位；满足门槛时不要因为尚未发布招聘广告而放弃假设，否则输出空数组。
3. 岗位标题包含具体赛道、技术、产品环节、制造环节或商业任务，并使用“总监、VP、副总裁、总经理、首席、总师、Head、Director、CTO、COO、CEO”等无歧义的 Director+ 职级。“负责人”单独出现不算 Director+。“生产总监”“研发总监”“供应链总监”等泛称也不合格；标题结构参考“机器人小批量制造工程化总监”。
4. evidence_refs 只能填写事实包中存在的 evidence_id。
5. job_ad 只能作为晚期验证，不能作为早期岗位推断的唯一依据。
6. horizon 只能是 near_term（0–90 天）或 watchlist（91–180 天）。
7. city 只填一个城市；事实明确指向唯一城市时必须保留该城市。无法判断、标为未知/待定/全国/多地、或存在多个可能城市时，统一填“上海”，并在 city_basis 说明按发布规则默认上海、需人工复核。
8. why_now 与 city_basis 只能复述或明确推导输入事实，不能把“产线在某城市”改写成“总部在该城市”；计划结果必须写成目标，不能冒充已发生事实。
9. watch_for 优先使用招聘广告之前的可观察信号，不把发布职位广告作为主要触发条件。

输出格式：
{
  "lead_index": 1,
  "company": "星火机器人",
  "stage_transition": "企业正在经历的阶段变化，证据不足则说明未知",
  "organizational_gaps": ["0-5条能力缺口"],
  "role_hypotheses": [
    {
      "specific_title": "具体 Director+ 岗位",
      "capability_gap": "该岗位弥补的组织能力缺口",
      "mandate": "入职后需要完成的核心任务",
      "why_now": "为什么是当前或下一阶段",
      "horizon": "near_term",
      "evidence_refs": ["输入中的 evidence_id"],
      "evidence_against": ["0-4条反证或替代解释"],
      "unknowns_to_verify": ["1-5条需要人工核实的信息"],
      "key_outcomes": ["3-5条预期结果"],
      "must_have_signals": ["3-5条候选人关键能力"],
      "preferred_signals": ["1-3条加分能力；必须是候选人特征，不能写待核问题"],
      "specificity_terms": ["3-8个匿名广告可用词"],
      "city": "一个城市；不确定时填上海",
      "city_basis": "城市依据或待核原因"
    }
  ],
  "watch_for": ["没有可辩护岗位时，列出1-5个后续触发信号"]
}

只返回上述 JSON 对象。
```

## 阶段一修复 User：仅在公司岗位校验失败时

```text
公司事实包：
{单家公司事实包 JSON}

上一版输出：
{被拒绝的模型原文}

确定性校验错误：
{错误类型与原因}

请只修复上述错误，并重新返回完整的单公司 JSON。仍须只引用事实包里的 evidence_id；
不得用“生产总监、研发总监、供应链总监、负责人”等含混标题；如果没有可辩护的
具体 Director+ 岗位，则返回空 role_hypotheses 和可观察的 watch_for。

以下是格式正确的安全回退示例；只能学习结构，不能因此忽略事实包中已有的充分证据：
{"lead_index":1,"company":"星火机器人","stage_transition":"证据不足，尚不能判断新增组织责任","organizational_gaps":[],"role_hypotheses":[],"watch_for":["观察新的上游运营信号"]}

只返回一个完整 JSON 对象，不要 Markdown、代码围栏、解释或前后缀文字。
```

## 阶段二 System：单主题 JD 写作方法
```text
你是高级猎头职位广告编辑。输入是已经由公开证据支持的人才主题。把该主题直接
写成可由 liepin-job-posting 发布的最终 Director+ JSON。职责、要求和技术词都应
服务于输入主题的 mandate 与 specificity_terms。只返回严格 JSON。
```

## 阶段二 User：单主题与完整示例

```text
人才主题：
{"theme_id":"theme_2ddbfd722857","recommended_title":"机器人运动控制工程化总监","role_family":"研发与算法","shared_mandate":"建立技术研发到规模交付的完整闭环","why_now":"公开事件显示公司进入下一产品阶段","horizon":"near_term","specificity_terms":["运动控制","工程验证","规模交付"],"key_outcomes":["制定技术路线","建立工程验证闭环","搭建跨职能团队"],"must_have_signals":["十年以上相关经验","有技术工程化经验","有规模交付经验"],"preferred_signals":["有从样机验证推进至小批量交付的经验"],"city":"上海","city_basis":"公开研发活动所在地","source_hypothesis_ids":["lead_1_role_1"],"source_lead_indices":[1],"evidence_refs":["ev_3e36d7e7814c"]}

完整输出示例：
{"drafts":[{"ordinal":1,"talent_persona":"能够承担“建立技术研发到规模交付的完整闭环”的总监级人才","role_family":"研发与算法","attraction_angle":"公开事件显示公司进入下一产品阶段","recommended_title":"机器人运动控制工程化总监","why_now":"公开事件显示公司进入下一产品阶段","public_payload":{"position_name":"机器人运动控制工程化总监","position_scope":"【岗位职责】\n• 制定运动控制技术路线\n• 建立工程验证闭环\n• 搭建跨职能团队\n• 拆解年度目标与关键里程碑\n• 建立研发、业务与交付协同机制\n\n【任职要求】\n• 十年以上相关经验\n• 有技术工程化经验\n• 有规模交付经验\n• 具备总监级团队管理经验\n• 能够把复杂任务拆解为可量化结果","cities":["上海"],"seniority":"10年以上","work_experience_years":[10],"education":"本科","salary_low":"50k","salary_high":"70k","must_have_signals":["十年以上相关经验","有技术工程化经验","有规模交付经验","具备总监级团队管理经验","能够把复杂任务拆解为可量化结果"],"preferred_signals":["有从样机验证推进至小批量交付的经验"],"benefits":["五险一金","带薪年假"],"target_count":10,"job_type":"社招","recruit_count":1,"languages":["普通话"]}}]}

任务：
- 输出恰好一条 draft，ordinal 固定为 1。
- recommended_title 和 position_name 使用人才主题的具体标题。
- position_scope 严格使用【岗位职责】和【任职要求】两个章节；每个章节各写
  5–10 行以“• ”开头的 bullet，总长度不超过 500 个字符。
- public_payload 是持久化并直接发布的最终 JSON；字段、类型、枚举和值与示例
  完全一致，包括 job_type=社招、languages=[普通话]、seniority 无空格、
  benefits 包含五险一金。
- cities 只含人才主题中的一个城市。
- 公开内容保持匿名，并自然使用至少两个 specificity_terms。
- 工龄为 [10]；薪资使用 xxk，最高不超过 85k，区间差不超过 20k。

只返回 JSON 对象 {"drafts": [...]}。
```

## 修复 User：仅在主题校验失败时

```text
人才主题：
{"theme_id":"theme_2ddbfd722857","recommended_title":"机器人运动控制工程化总监","role_family":"研发与算法","shared_mandate":"建立技术研发到规模交付的完整闭环","why_now":"公开事件显示公司进入下一产品阶段","horizon":"near_term","specificity_terms":["运动控制","工程验证","规模交付"],"key_outcomes":["制定技术路线","建立工程验证闭环","搭建跨职能团队"],"must_have_signals":["十年以上相关经验","有技术工程化经验","有规模交付经验"],"preferred_signals":["有从样机验证推进至小批量交付的经验"],"city":"上海","city_basis":"公开研发活动所在地","source_hypothesis_ids":["lead_1_role_1"],"source_lead_indices":[1],"evidence_refs":["ev_3e36d7e7814c"]}

字段示例：
{"position_name":"机器人运动控制工程化总监","position_scope":"【岗位职责】\n• 制定运动控制技术路线\n• 建立工程验证闭环\n• 搭建跨职能团队\n• 拆解年度目标与关键里程碑\n• 建立研发、业务与交付协同机制\n\n【任职要求】\n• 十年以上相关经验\n• 有技术工程化经验\n• 有规模交付经验\n• 具备总监级团队管理经验\n• 能够把复杂任务拆解为可量化结果","cities":["上海"],"seniority":"10年以上","work_experience_years":[10],"education":"本科","salary_low":"50k","salary_high":"70k","must_have_signals":["十年以上相关经验","有技术工程化经验","有规模交付经验","具备总监级团队管理经验","能够把复杂任务拆解为可量化结果"],"preferred_signals":["有从样机验证推进至小批量交付的经验"],"benefits":["五险一金","带薪年假"],"target_count":10,"job_type":"社招","recruit_count":1,"languages":["普通话"]}

上一版：
{"drafts":[{"ordinal":1,"talent_persona":"能够承担“建立技术研发到规模交付的完整闭环”的总监级人才","role_family":"研发与算法","attraction_angle":"公开事件显示公司进入下一产品阶段","recommended_title":"机器人运动控制工程化总监","why_now":"公开事件显示公司进入下一产品阶段","public_payload":{"position_name":"机器人运动控制工程化总监","position_scope":"【岗位职责】\n• 制定运动控制技术路线\n• 建立工程验证闭环\n• 搭建跨职能团队\n• 拆解年度目标与关键里程碑\n• 建立研发、业务与交付协同机制\n\n【任职要求】\n• 十年以上相关经验\n• 有技术工程化经验\n• 有规模交付经验\n• 具备总监级团队管理经验\n• 能够把复杂任务拆解为可量化结果","cities":["上海"],"seniority":"10年以上","work_experience_years":[10],"education":"本科","salary_low":"50k","salary_high":"70k","must_have_signals":["十年以上相关经验","有技术工程化经验","有规模交付经验","具备总监级团队管理经验","能够把复杂任务拆解为可量化结果"],"preferred_signals":["有从样机验证推进至小批量交付的经验"],"benefits":["五险一金","带薪年假"],"target_count":10,"job_type":"社招","recruit_count":1,"languages":["普通话"]}}]}

确定性校验发现：
["示例校验错误"]

请修复后返回完整 JSON 对象 {"drafts": [{...}]}。ordinal 固定为 1，
字段集合与字段示例完全一致，内容继续只对应当前人才主题。
```
