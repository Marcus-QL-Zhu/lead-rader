# HT Lead Radar Implementation Plan / Delivery Ledger

- 状态：Implementation complete for the self-contained MVP；外部凭证/受限信源项单列为 Blocked
- 对齐 Spec：`docs/alignment-spec.md`（Approved v1）
- 最近更新：2026-07-29
- 生产目录：`/home/admin/.openclaw/workspace/skills/hardtech-lead-radar`
- 生产计划：Asia/Shanghai 每天 05:00

本文件同时是实施计划、交付清单和上下文恢复锚点。后续上下文被压缩时，以这里的状态和验收命令为准，不把 `BLOCKED-EXTERNAL` 或 `NOT-PLANNED` 描述成已完成。

## 1. 已批准、不得悄悄改变的产品决策

1. 一个统一后端，两个显式模式：`Market Scan` 与 `Candidate Float`。
2. 默认中国大陆招聘市场相关性；任意行业/技术输入由 Agent 生成临时行业地图。
3. 目标 Top 20，允许降低软分数门槛；不得放宽以下两个硬门槛：
   - 必须能形成总监级以上岗位假设；
   - 必须有至少一条招聘广告之前的上游信号。
4. 招聘广告-only 公司只进入晚期机会附录。
5. 每日 Top 20 只做基础研究；主动深挖或任何 Float 才做投资人和企业内部决策者深研。
6. 深研对象包括：
   - 外部投资机构、领投方、疑似主导投资的 Partner/MD；
   - 企业业务 Hiring Manager；
   - HR/TA/HRBP；
   - 创始团队及其公开工作/投资/合作关系。
7. 后端事实库是唯一事实源；飞书是增量行动队列；OpenClaw/Codex 是自然语言研究界面。
8. 不生成触达话术、不发送消息、不抓私人联系方式。
9. 不建设简历库；Candidate Profile 和候选人派生分析不写入事实库、checkpoint、飞书或人物图。
10. 不建设原生业务结果评分、反馈训练、自动调权或 A/B 系统；保留可解释公司/Float 排序分。

## 2. 实施状态总览

状态：

- `DONE`：已实现并有自动测试或生产验收。
- `READY-CODE`：代码完整，等待外部配置即可启用。
- `BLOCKED-EXTERNAL`：缺少凭证、官方稳定接口或允许的访问路径，不能安全完成。
- `DEFERRED-SCALE`：当前 SQLite/规则规模不需要，达到阈值再做。
- `NOT-PLANNED`：用户明确不要求。

### Phase 0 — 通用请求与 Agent 入口

| ID | 状态 | 交付 |
|---|---|---|
| P0-01 | DONE | D-01 至 D-09 对齐完成，Spec 为 Approved v1 |
| P0-02 | DONE | 结构化 `OpportunityRequest`：模式、行业、地域、180/90天时间策略、职级、深研策略 |
| P0-03 | DONE | 自动识别 Market Scan / Candidate Float；歧义时渐进补问 |
| P0-04 | DONE | 任意中文行业的动态四层行业地图和 planner queries；五个硬科技方向有增强地图 |
| P0-05 | DONE | 每份报告保存经治理的请求解释、参数、来源、过滤、run ID；Float 候选人内容按治理规则脱敏且不持久化 |

### Phase 1 — 固定信源网络

| ID | 状态 | 交付 |
|---|---|---|
| P1-01 | DONE | 旧版固定列表抓取器继续可用；企业专属官网入口已禁用并由代码层拒绝进入每日发现 |
| P1-02 | DONE | `config/source-packs.json`：43 个登记来源、6 个复用来源包、27 个默认启用的行业级/通用来源 |
| P1-03 | DONE | 通用中国大陆政策、项目、环评、招投标、融资来源包 |
| P1-04 | BLOCKED-EXTERNAL | SSE/SZSE/巨潮公告入口已登记但默认禁用；需官方稳定/文档化查询路径和代码映射，不采用网上流传的未文档化接口 |
| P1-05 | READY-CODE | direct HTTP、Miniflux、RSSHub、changedetection 适配器、health-check 和 spike API 已实现；生产机剩余内存有限，未常驻部署 sidecar |
| P1-06 | READY-CODE | CNIPA 集成电路源启用；临床/药监来源因浏览器、412或访问策略禁用；OpenAlex/专利人才图按需复用 Academic Mapping，不混入每日低成本链 |
| P1-07 | DONE | 脑机接口、半导体、商业航天、核聚变、具身智能首批来源包 |
| P1-08 | DONE | 来源成功率、最后成功、零产出、错误、结构/解析产出和健康摘要 |
| P1-09 | READY-CODE | RSSHub 抽象存在，但只有技术 spike 证明降低维护成本后才部署 |
| P1-10 | DONE | Source 一级实体：tier/owner/adapter/schedule/permission/retention/cost/tags |
| P1-11 | DONE | Miniflux/RSSHub/changedetection 技术接口和 spike；生产不为“技术栈完整”强行运行 |
| P1-12 | DONE | ETag/Last-Modified、自适应频率、backoff+jitter、circuit breaker、失败隔离 |
| P1-13 | DONE | 来源包真实采集器：HTML/RSS/JSON Feed、详情页、谨慎公司归属、document-only observation、SQLite 增量状态 |
| P1-14 | DONE | 每日发现只使用政府、园区、协会、行业媒体、融资媒体、交易/注册披露等覆盖公司集合的来源；`company_official` 仅保留登记审计，不参与调度 |
| P1-15 | DONE | 新增北京亦庄重大项目、苏州机器人协会、深圳半导体协会、核聚变垂直媒体，并复核公开列表、日期与详情入口 |
| P1-16 | DONE | 通用列表适配器支持标题前缀、公司法定名、复合行业词和高管变动中的雇主抽取 |
| P1-17 | DONE | 每日五个硬科技主题合并为一次采集计划，启用来源取并集且每个来源只调度一次；统一归一化、聚簇、评分并输出“硬科技组合” Top 20 |
| P1-18 | DONE | 手动指定方向保持定向模式：只选择通用来源包与匹配的行业来源包；多主题日报和定向探索使用不同幂等键 |

### Phase 2 — 事实、实体与事件

| ID | 状态 | 交付 |
|---|---|---|
| P2-00 | DONE | `SourceDocument → Statement → BusinessEvent → CanonicalEntity` 四层模型 |
| P2-01 | DONE | 规范实体、别名和稳定 ID |
| P2-02 | DONE | 规范名称/显式别名/确定性 key 匹配 |
| P2-03 | DEFERRED-SCALE | Splink 等概率匹配只有在实体量和人工样本足够后评估 |
| P2-04 | DONE | 公司局部上下文归属；项目名、基地、实验室等不得冒充公司 |
| P2-05 | DONE | URL/内容哈希、不可变版本、exact duplicate 指针和审计历史 |
| P2-06 | DONE | canonical URL、exact content、Story/Document、Business Event 分层去重 |
| P2-07 | DONE | emerging/corroborated/developing/stale/superseded/retracted 等生命周期与历史 |
| P2-08 | DONE | A/B/C/D 规范证据选择、独立来源组和支持数 |
| P2-09 | DONE | positive/negative/unsure/no-judgement 可撤销实体判断；事件 merge/split/supersedes 可撤销 |
| P2-10 | DEFERRED-SCALE | news-please 中文正文 benchmark 未达到引入第三方栈的必要性 |

### Phase 3 — 总监级需求推理与 Float

| ID | 状态 | 交付 |
|---|---|---|
| P3-01 | DONE | 脑机、半导体、商业航天、核聚变、具身智能行业特定组织能力→总监级岗位 |
| P3-02 | DONE | 一句话/指定内容生成 runtime-only Candidate Profile，渐进补问 |
| P3-03 | DONE | 能力、行业、领导跨度、地域、目标方向、排除项结构 |
| P3-04 | DONE | Candidate-to-account matching，基于上游事件和未来组织缺口而非广告 |
| P3-05 | DONE | Float 四维分：公司需求35、候选人匹配35、时机20、公开关系可研究性10 |
| P3-06 | DONE | 排除项/地域/职能/领导跨度/广告窗口冲突和待核清单 |
| P3-07 | DONE | 事实、推断、缺失信息、风险和“哪些新证据会改变排名” |
| P3-08 | DONE | Top 20 软门槛补足、两个硬门槛不变、逐项得分解释 |
| P3-09 | DONE | Event ID 计分；重复报道不重复加事件分，只增加独立佐证 |

### Phase 4 — 基础研究、深研和投资图谱

| ID | 状态 | 交付 |
|---|---|---|
| P4-00 | DONE | 每日 basic / 用户深挖或 Float deep 两级执行 |
| P4-01 | DONE | 企业 Buying Center：业务 Hiring Manager、HR、创始团队 |
| P4-02 | DONE | 公开姓名/职位/机构/来源/置信度；找不到就明确缺口 |
| P4-03 | DONE | 创始人前雇主、校友、投资和合作公开关系提示 |
| P4-04 | NOT-PLANNED | 触达话术和发送 |
| P4-05 | DONE | 输出明确要求人物/关系在使用前人工打开来源复核 |
| P4-06 | DONE | 融资机构、领投、发表评论投资人抽取；评论只能形成推断 |
| P4-07 | DONE | Partner/MD 赛道覆盖研究查询和证据置信度 |
| P4-08 | DONE | 90天深研缓存；人物、机构、关系、核验时间增量保存 |
| P4-09 | DONE | Person—Institution—Company 关系图和公司筛选 |
| P4-10 | DONE | 领投/参投/评论/推断边区分 |

### Phase 5 — 工作台与交付

| ID | 状态 | 交付 |
|---|---|---|
| P5-01 | DONE | SQLite事实库 + 飞书投影 + OpenClaw/Codex自然语言组合工作台 |
| P5-02 | DONE | Markdown/JSON Lead Card：公司、分数、事件、岗位、人物、风险、来源 |
| P5-03 | DONE | 稳定 Company ID、create/update/deactivate、cursor/state、失败重试、dry-run、真实 API client；专用表和字段已配置 |
| P5-04 | DONE | 飞书“待研究/需要深挖/已查看”状态字段已配置；不进入训练 |
| P5-05 | DONE | `ask`、`float`、`deep-research`、`run-status`、`resume`、`replay-run` Agent/CLI |
| P5-06 | BLOCKED-EXTERNAL | 飞书状态→OpenClaw webhook 仍需要事件回调、订阅权限和 OpenClaw 接收端配置 |
| P5-07 | DONE | 后端 checkpoint、Feishu projection state、增量变化/撤选 |
| P5-08 | DONE | 每日机会使用已汇报历史做 7 天冷却；出现新证据可提前回归，冷却结束后归入持续观察 |
| P5-09 | DONE | 同一家公司每天最多进入一个人才主题；OpenClaw 上下文区分新增、持续观察和仍在冷却的公司 |
| P5-10 | DONE | 每日默认把具身智能、半导体、商业航天、核聚变、脑机接口合并为一次多主题扫描，统一形成最多 20 家组合结果 |

### Phase 6 — 历史样本与预测校准

2026-07-28 决策更新：原“不建设训练集和自动调权”的范围决定已被用户明确推翻。现在建设基于公开历史事实的公司—月份训练集、可解释权重校准和 Precision@K 等离线验收；仍不把人工 BD 结果评分或 Float 转化漏斗做成系统原生能力。

### Phase 7 — 合规、可靠性与运维

| ID | 状态 | 交付 |
|---|---|---|
| P7-01 | DONE | 网页证据/公开人物保留策略；候选人内容不进入系统事实库 |
| P7-02 | DONE | 所有入口一致的公司/人物 suppression/opt-out gate |
| P7-03 | DONE | run/view/export/modify 安全审计；拒绝秘密、Token、原始简历写入审计 |
| P7-04 | DONE | cron、run、checkpoint、来源失败/零产出、Metaso预算、结果异常监控 |
| P7-05 | DONE | SQLite 在线一致性备份和完整性检查；PostgreSQL 为达到并发阈值后的迁移路径 |
| P7-06 | DONE | 公开访问、robots/条款、许可、字段白名单、私人联系方式和访问控制边界 |
| P7-07 | DONE | 公开人物角色/评论/投资方向时效策略和 stale 标记 |
| P7-08 | DONE | 来源许可、全文/元数据/事实摘要和保留策略 |
| P7-09 | DONE | 六阶段幂等 checkpoint/replay；昂贵 effect cache 防止重复消耗 Metaso |
| P7-10 | DONE | 持久化 Metaso 日账本：项目30分默认预算、500分提供方硬上限 |

## 3. 当前外部条件与待确认项

1. **飞书真实 Lead 写入待用户确认**  
   本地和服务器凭证均已找到并验证；独立的 `HT Lead Radar` 表及 13 个字段已创建。完整配置安全暂存在 `.env.feishu-live`，日任务继续 dry-run，避免未经明确确认把真实公司分析写入外部表格。

2. **飞书状态触发 OpenClaw 深研**  
   表和字段已经具备；尚需配置飞书事件回调/webhook、订阅权限和 OpenClaw 接收端。

3. **交易所/巨潮动态公告**  
   官方入口已登记，但没有采用未文档化接口。只有获得稳定文档化查询路径或经批准的浏览器适配，才能启用。

4. **NMPA/ChiCTR/验证码/412/动态网页**  
   来源保留但禁用；不绕过访问控制。已有 browser/changedetection 适配抽象，等待允许且稳定的访问方式。

5. **采集 sidecar 常驻部署**  
   生产机资源有限；当前直接采集器已覆盖启用源。只有 spike 证明净降低维护成本且资源扩容后才启动 Miniflux/RSSHub/changedetection。

## 4. 端到端验收

### A. Market Scan

```bash
python scripts/run_lead_radar_v2.py ask \
  --question "最近脑机接口行业有哪些公司可能要招总监以上职位？" \
  --provider auto \
  --metaso-verify-limit 0
```

验收：

- 请求被识别为 Market Scan；
- 显示行业四层地图、地域和180/90天策略；
- 选择 generic + 脑机来源包；
- 主榜只含同时通过两个硬门槛的公司；
- 最多20家、分数降序、每分有来源；
- 广告-only 公司只在附录；
- 日常只做基础研究。

### B. Candidate Float

```bash
python scripts/run_lead_radar_v2.py float \
  --candidate "数据采集总监，负责多源采集体系、数据闭环和50人团队" \
  --direction 具身智能 \
  --provider auto \
  --metaso-verify-limit 0
```

验收：

- 模式为 Candidate Float；
- 结果含需求/匹配/时机/关系可研究性四维分；
- 含卖点、风险、缺失和会改变排名的新证据；
- 自动深研公开投资人/HM/HR/创始团队；
- Candidate Profile/派生分析不出现在 facts、runtime checkpoint、Feishu 或 relationship DB；
- 不生成触达话术、不发送。

### C. 可靠性

```bash
python -m pytest -q
python -m compileall -q src
python scripts/run_lead_radar_v2.py run \
  --direction 灵巧手 --demo --metaso-verify-limit 0
```

重复运行同一 idempotency key 应复用 checkpoint；`replay-run` 默认复用昂贵阶段。

## 5. 服务器运行与恢复

每日入口：

```bash
/home/admin/.openclaw/workspace/skills/hardtech-lead-radar/scripts/run_daily_fixed_sources.sh
```

默认子方向由 `HT_LEAD_DAILY_DIRECTIONS` 控制，缺省为 `具身智能|半导体|商业航天|核聚变|脑机接口`；组合报告方向缺省为 `硬科技组合`。cron 必须为：

```cron
0 5 * * * /home/admin/.openclaw/workspace/skills/hardtech-lead-radar/scripts/run_daily_fixed_sources.sh
```

查看/恢复：

```bash
python3 scripts/run_lead_radar_v2.py run-status --run-id RUN_ID
python3 scripts/run_lead_radar_v2.py resume --run-id RUN_ID
python3 scripts/run_lead_radar_v2.py replay-run --run-id RUN_ID --from-stage normalize
python3 scripts/run_lead_radar_v2.py monitor
python3 scripts/run_lead_radar_v2.py backup --backup-dir backups
```

Float 失败后跨进程不能 resume，这是有意的数据治理结果：候选人画像不持久化；用户应重新发起 Float。

## 6. 上下文恢复步骤

1. 读 `docs/alignment-spec.md` 的 D-01 至 D-09。
2. 读本文件“已批准决策”“实施状态总览”和“当前外部条件与待确认项”。
3. 读 `docs/best-practices-landscape-2026-07-25.md`，不要重新做同一轮最佳实践搜索。
4. 运行全量测试和 compileall。
5. 检查服务器 05:00 cron、最近报告、`health-latest.json` 和 Metaso budget ledger。
6. 不把 `READY-CODE` 说成已在外部系统启用，不把 `BLOCKED-EXTERNAL` 猜测性补齐。
7. 不重新加入用户已经排除的触达发送、简历库或业务结果评分系统。

## 7. 变更日志

- 2026-07-25：完成 D-01 至 D-09 对齐，Spec Approved v1。
- 2026-07-25：完成至少15个核心、20个总样本的外部最佳实践 fan-out/fan-in 调研。
- 2026-07-25：交付通用请求、来源包、四层事实库、事件去重/生命周期、Top20、Float、深研/投资图、Feishu投影、checkpoint、配额、监控、备份和合规边界。
- 2026-07-25：融资固定来源从 2 个主要媒体入口扩为 13 个可发出融资信号的来源；生产健康检查 13/13 正常。
- 2026-07-25：用固定随机种子从 65 个融资候选中抽取 10 项，Metaso 逐项核验投资方，消耗 60 积分；公开具名投资方 10/10 覆盖，1 项因发行方未披露全部机构名称而不可观测。
- 2026-07-25：把所有剩余项收敛为明确外部阻塞或规模后延期，移除过时的“尚未开始”状态。
- 2026-07-29：根据信源扩域决策，禁止企业官网进入每日发现；每日主链路改为行业级稳定来源与通用适配器。
- 2026-07-29：加入五主题单次采集与统一 Top 20、7 天冷却、新证据提前回归、单公司单主题和 OpenClaw 机会分段。


## 8. 生产交付验收（2026-07-25）

- 本地全量自动测试：160 项通过；Ruff 与 compileall 通过。
- 生产版本：`0.3.0`，目录 `/home/admin/.openclaw/workspace/skills/hardtech-lead-radar`。
- 真实脑机接口固定信源运行：`run_bd8614af6eddbc4092f5813093ba413c`；1 家公司通过全部硬门槛，旧机器人来源零串榜，Metaso 0 分。
- 按需深研：10 个机构、2 名投资人、6 名创始团队相关人物；关系库为 10 个机构、8 名人物、20 条关系。
- 监控状态 `ok`；每日 05:00 运行；每周日 05:45 在线一致性备份。
- 8 个 SQLite 数据库备份的 `integrity_check` 全部为 `ok`。
- 飞书凭证、专用表和字段已配置；真实 Lead 写入待用户明确批准，webhook 仍为 `BLOCKED-EXTERNAL`。
- 最终验收详情：`docs/delivery-status-2026-07-25.md`。

完整交付后只应继续处理外部配置、被允许的新信源适配或用户提出的新迭代，不得重新把已排除的触达发送、简历库或原生结果评分系统加入范围。

# 2026-07-26：Evidence-bound MiniMax 重构

状态：本地实现完成，等待用户验收；尚未同步 GitHub 或服务器。

- [x] 固定信源证据编译为逐公司 packet，并生成稳定 `evidence_id`
- [x] 证据按事件类型、来源等级和时间多样化选择，不再截取前六条
- [x] MiniMax 改为一家公司一次调用
- [x] 允许证据不足时返回空岗位与 `watch_for`
- [x] 岗位必须引用 packet 中真实存在的 `evidence_id`
- [x] 统一时间窗口为 `near_term`（0–90 天）与 `watchlist`（91–180 天）
- [x] 具体岗位显式聚簇为人才主题，保留来源岗位 ID
- [x] MiniMax 改为一个人才主题一次 JD 调用
- [x] 标题、城市和 specificity terms 按当前主题独立校验
- [x] 请求拆分 system/user message，并启用 `reasoning_split=true`
- [x] 公司岗位标题强制使用无歧义 Director+ 职级，裸“负责人”不通过
- [x] 候选人 preferred 能力与人工待核问题分离
- [x] 人才主题优先选择独立证据更多的假设
- [x] 公司分析与 JD 分别保留一次有界修复和 fail-closed
- [x] 确定性执行证据门槛，单条融资和单条招聘广告不能形成 near-term 岗位
- [x] LLM 有限重试、240 秒单次超时、3600 秒批次 deadline、每日任务防重叠锁
- [x] 部分分析失败非零退出并进入飞书；公司列表改用 MiniMax 具体岗位
- [x] 历史策略曾将未知城市排除；现已按业务决定改为默认上海；飞书不展示尚未接通的发布指令
- [x] 飞书严格匹配当前报告 source_run_id，生成硬失败时不读取同日旧草稿
- [x] 真实 MiniMax 临时链路验收：生成“运动控制算法工程化总监”，单城市上海，绑定两条证据
- [x] 本地自动化验收：224 tests passed，Ruff、compileall、git diff --check 全部通过
- [ ] 用户验收
- [ ] 子代理 full code review（仅在准备更新 GitHub 前执行）
- [ ] Commit、Push、GitHub Actions、按 GitHub SHA 部署服务器

# 2026-07-26：MiniMax 结构化输出加固

- [x] 公司岗位推断 system prompt 增加三个多样 few-shot：充分证据、证据不足和 A 级运营事件。
- [x] 一次修复提示增加与当前公司绑定的完整安全 JSON 示例，并禁止 JSON 外文字。
- [x] 对展示型列表溢出和重复项做确定性去重裁剪；岗位数量、证据引用、反证、待核项、Director+ 标题和证据门槛保持严格。
- [x] 城市为空或不唯一时默认上海，并标记需人工复核。
- [x] 新增回归测试；本地全量测试及 Ruff 通过。

# 2026-07-26：MiniMax M3 对照与生产切换

- [x] 使用同一份 10 家公司事实包隔离对照 M2.7-highspeed 与 M3。
- [x] M3 完成 10/10 公司分析、0 错误、5 个岗位假设、4 个去重主题，约 5 分钟完成。
- [x] 每日任务默认使用 `minimax/MiniMax-M3`，保留 `LEAD_RADAR_LLM_MODEL` 显式回退。
- [x] 不修改 OpenClaw 全局主模型，不复制 Provider 凭证。
- [x] 人才池 bundle、生成脚本摘要和飞书汇总记录完整 `provider/model`。
- [x] 对照结果记录于 `docs/minimax-m3-ab-2026-07-26.md`。

# 2026-07-26：飞书职位关联与 Float 机会持久化

- [x] 修复同一 `source_run_id` 多次生成时旧草稿混入当前飞书的问题。
- [x] 当前审批批次改用精确 snapshot draft membership，不再按日期累积。
- [x] 飞书每条职位展示目标公司、对应公司岗位假设和完整猎聘 JSON。
- [x] 新增不可变 bundle snapshot 与规范化 company-role-opportunity 历史表。
- [x] 保存证据 URL、人才画像、公司岗位假设和 Liepin payload，候选人简历仍不持久化。
- [x] 新增 `query_talent_opportunities.py`，供后续 Float Agent 查询历史或当前机会。
- [x] 不增加结果评分和自动发布能力。
- [x] 单个人才主题经一次修复仍失败时降级为部分成功（退出码 72），保留并推送其他有效职位；仅全部主题失败时退出 71。

# 2026-07-27：OpenClaw reset-safe 日报与轻量审批桥

状态：代码与本地测试完成，待独立代码审查、GitHub CI、生产部署与飞书冒烟验证。

- [x] 每个已提交人才池 bundle 在同一 SQLite 事务中登记待汇报状态；相同 snapshot 重跑不重复汇报。
- [x] 05:00 任务完成后动态读取当日 `agent:main:main` 的 session ID 与飞书路由，通过 `openclaw agent --session-id ... --deliver` 执行真实主会话 turn；内部消息只包含项目地图和读取命令，不携带网页正文或完整 JSON。
- [x] 新增 `references/openclaw-daily-operator.md`，供 OpenClaw 在 04:00 会话重置后恢复 Lead Rader 所需的最小上下文。
- [x] 新增按需查询：当前日报、待汇报日报、指定编号的完整公司—岗位—证据—猎聘 JSON。
- [x] 正常路径由 OpenClaw 在主飞书会话汇报；原飞书 REST 汇总只在 hook/任务/草稿生成失败时兜底。
- [x] OpenClaw cron 只在 05:50 和 06:50 运行（`50 5,6 * * *`，Asia/Shanghai）；isolated cron turn 仅调用同一个 bridge，由 bridge 唤醒 main session，不使用 heartbeat/system event。
- [x] 用户可以自然表达“发布第一个草稿”“把 1 和 3 发掉”或上下文明确后的“确认”；OpenClaw 解析 action/indexes，用户无需复述机器指令或提供 snapshot code。
- [x] hook 和 cron 均只能汇报、查询和询问；发布必须来自真实飞书入站的批准语义，并记录用户原文、actor 与当前 snapshot。
- [ ] 独立子代理 full code review。
- [ ] 推送 GitHub 并等待 exact SHA 的 Actions 通过。
- [ ] 备份生产源代码与数据库，部署 exact SHA，安装唯一的两次 cron 并执行 reset-safe/飞书冒烟测试。
# 2026-07-28：前置信号扩展与即时历史回测

状态：本地实现和验收完成；按用户要求尚未同步服务器或 GitHub。

- [x] 统一扩展高管变动、并购、合资/分拆、上市、新实体/基地、扩产、项目建设、项目征集、环评许可、采购意向/招标、重大订单、客户验证、融资、海外/渠道扩张、技术里程碑、数据/模型、法规/临床、科研/IP、企业系统、合作和政策/标准等前置信号。
- [x] 不新增负面信号；终止、撤回、暂停、停产、裁员等文本只做极性防误判，不形成扣分或负面预测。
- [x] 经理/专家/工程师 workforce cluster 已接入生产信号面，但在本轮 Director+ 验收预测中强制关闭。
- [x] MiniMax 先做阶段变化和职能依赖展开，再生成具体 Director+ 岗位；A 级工厂/扩产、重大订单、新实体、高管更替和上市事件必须覆盖相应基础职能。
- [x] MiniMax 请求温度降为 0.0，以提高重复运行稳定性和结构化 JSON 一致性。
- [x] 历史回放只向 MiniMax 提供 cutoff 之前、具有公开可用时间戳及内容哈希的非招聘证据；职位广告只在预测冻结后用于未来三个月验证。
- [x] 快照冻结输入包、完整 system/user prompt、原始响应、修复响应、Provider/模型、公司类型和所有哈希；证据和验证职位的内容哈希均校验。
- [x] 验收不依赖 JOSINT，也不把经理、专家、工程师岗位作为测试信号。
- [x] 三个时间切片岗位命中分别为 2、3、3；共 74 个不同标题、72 个规范岗位键、16 个岗位族、5 个不同真实后续职位。
- [x] 初创民企、上市公司、外企三类均有真实岗位命中；`.acceptance/v7-aggregate.json` 全部门槛通过。
- [x] 本轮只改本地；未同步服务器或 GitHub；完成后不关机，以免打断其他会话长任务。
## 2026-07-28：冻结提示词后的独立盲测

- [x] 将 v3-v7 明确降级为开发期 pilot：它验证了回放基础设施，但因同一批未来职位参与多轮调参，不能单独证明泛化能力。
- [x] 在首次调用 MiniMax 前冻结 `holdout-v1` 的两组互不重叠时间窗、四家全新公司和计数门槛。
- [x] 盲测输入仅含 cutoff 前公开的非招聘证据，不含职位、JOSINT、分析师备注或 workforce cluster。
- [x] MiniMax-M3 在 A 组生成 9 个、B 组生成 6 个具体 Director+ 假设，共 15 个不同标题和 9 个岗位族。
- [x] 盲测暴露并修复英文 `Director` 内部字符 `cto` 被误当 CTO 的词边界缺陷；增加通用 `application_solutions` 岗位族并保留模型原始快照不变。
- [x] 修复后的确定性验证命中 4 个不同后续职位：A 组 3 个、B 组 1 个；上市、初创民企、外企三类均覆盖。
- [x] `.acceptance/holdout-v1-aggregate.json` 机械计数通过，但独立审计判定科学验收无效：标签打开后修改了 matcher，且候选全为已知正例；保留为失败诊断，不作为最终结论。`validator` 仍已增加响应重解析和 analyses 一致性校验。
- [ ] 严格 holdout-v2：先由 cutoff 前信号冻结完整候选全集、对照公司和所有哈希，再运行预测，最后才统一搜索未来职位标签。
- [x] 本轮保持本地、服务器持续运行；不关机，也未同步服务器或 GitHub。

## 2026-07-28：严格基线结论与数据驱动换挡

状态：现有 prompt/规则路线的最终基线已完成；未达到预测准确性目标，不再机械创建 v16/v17。下一阶段改为立即历史回填、信源扩展和权重校准。

### 冻结基线的真实结论

- `holdout-v1`：标签打开后修改 matcher，且候选存在正例选择，科学验收无效。
- `holdout-v2` 至 `holdout-v13`：用于发现泄漏、层级判断、来源审计、岗位映射和编码等问题；均不得作为最终泛化结论。
- `holdout-v14`：首个完整审查的严格盲测。12 家公司全部生成，58 个不同岗位、16 个岗位族；未来窗口观察到 4 家 Director+ 职位，只匹配 2 个岗位族，未达到 3 个匹配及三类公司覆盖门槛。
- `holdout-v15`：18 家全新公司、三类各 6 家；18/18 分析成功，84 个不同岗位、16 个岗位族、0 生成失败，模型输入无真实公司名或来源网址。
- v15 标签侧在预测快照冻结后，统一完成 18 家公司、36 次搜索；未来三个月只观察到 1 个合格 Director+ 职位（隆基绿能“设计总监”），与冻结的 5 个隆基岗位假设均不同族，因此为 0 个岗位匹配。
- v15 是内部记录一致、按预注册门槛明确失败的回溯盲法实验，但不是强前瞻有效性证据：预测发生在历史标签窗口结束后，历史招聘页面也没有逐页快照和内容哈希。它证明岗位生成具有多样性，但没有证明预测准确；仅靠继续修改同一输入下的提示词无法解决信息不足和公开职位可观测性问题。

### 新实施方向

- [x] 冻结 v15，不根据已经打开的标签调 matcher 或 prompt。
- [x] 新建 `docs/data-driven-calibration-plan.md`，明确历史样本、信源增量、标签可观测性、数据划分、轻量权重模型和验收指标。
- [x] 采用历史回填，不等待生产系统运行三个月：从真实历史 Director+ 职位反向抓取发布前 1—6 个月的信息，并按自然月生成多个公司—cutoff 样本。
- [x] 确定首轮公司级划分：40—50 家训练/校准公司，15—20 家完全隔离测试公司；同一公司的全部月份只能进入同一分区。
- [x] 建立历史数据账本 schema、来源快照契约、月度切片器、标签可观测性字段、逐行哈希和数据版本哈希。
- [x] 建立首批公司级隔离池：36 家训练、9 家校准、18 家测试；三类公司等比例，holdout-v14 排除开发、holdout-v15 全部固定为测试。覆盖不足的样本标为 `unknown`。
- [ ] 为开发池补齐可重放负例：当前只有 search-only 审计，没有同时带 URL、抓取时间、原始页面和 SHA-256 的确认负例，因此招聘倾向模型保持禁用。
- [ ] 批量回填公司新闻、公告、审批、招投标、融资、客户、组织和高管变动事件，严格使用当时的 `available_at`。
- [x] 训练首个加权逻辑回归岗位族排序基线；将招聘倾向和岗位族排序拆成两阶段，低权重对比负例不得冒充确认市场负例。必要时再比较小型梯度提升树，不先微调大模型。
- [ ] 以 `Precision@20`、`Recall@20`、岗位族 macro-F1、Brier 校准、提前量、公司类型切片和信源消融做冻结验收。
- [ ] 最终测试集只在特征、权重和阈值全部冻结后打开；失败后不得继续在同一测试集调参并声称仍为独立验收。

详细方案：[data-driven-calibration-plan.md](data-driven-calibration-plan.md)。

### training-v1 当前结果

- 数据集：63 家公司、24,986 条公司—月份—岗位族记录、116 条正标签、1,522 条低权重对比负例、23,348 条 unknown、0 条可重放确认负例。
- 开发池 45 家中，18 家已有历史 Director+ 职位锚点，27 家已生成一次一公司的历史职位发现任务；已知职位另外展开为前 1—4 个月的前置信号回填任务，共 211 个任务。
- 校准基线：24 个可评价公司—月份，Top-1 20.8%、Top-5 100%、macro-F1 2.9%、Precision@20 25%；上市与初创民企 Top-1 均为 0，明确判定不可生产使用。
- 测试分区尚无可映射的严格正标签，指标全部为 null；不宣称通过测试。
- MetaSo 候选发现器已实现预算账本、幂等检查点和多轮搜索标识。用户批准最多 300 积分后，首轮 27 次搜索记账 162 积分；另有一次失败语法试验和一次成功但全无关的详情搜索，累计记账 174 积分，保存 280 条候选。因实测边际价值接近零，剩余 126 积分未继续消耗。
- 确定性候选初筛已实现并运行。真实页面核验发现猎聘 `/s/` 链接是动态 SEO 聚合页而非对应公司历史职位，因此已收紧规则；最终为 0 条高优先级、14 条中优先级、266 条低优先级。MetaSo 搜索摘要、结果日期和 SEO 聚合页绝不直接成为标签；只有可归属公司的职位详情页经归档后才能补入训练集。
- 详细状态见 `evaluation/training-v1/README.md`；当前仍只在本地，未同步服务器或 GitHub。

## 2026-07-29: Post-V15 data-driven calibration round 1

Status: completed locally; rejected by the preregistered promotion gate; not synchronized to the server or GitHub.

- [x] Kept holdout-v15, its labels, the final test partition, prompt, ontology, matcher, and threshold frozen.
- [x] Added a development-only calibration runner which reads only `train` and `calibration`; raw test rows are skipped before their label or features are materialized.
- [x] Preregistered 40 candidates across two feature policies, five L2 values, and four learned/rule blend weights.
- [x] Required strict improvement in at least two of Top-1, Precision@20, and macro-F1, with no company-type Top-1 regression above 12.5 percentage points.
- [x] Preserved the disabled hiring-propensity model because replayable confirmed negatives remain zero.
- [x] Excluded provisional `training-v3` labels from model fitting and selection.
- [x] Ran the full grid. No candidate passed; the current logistic role ranker remains the baseline and no new model is promoted.
- [x] Diagnosed the binding data limitation: all strict positive companies in the training partition are foreign; strict listed and startup/private positives exist only in calibration.
- [ ] Before the next model iteration, add independent strict Director+ labels for listed and startup/private companies to the training partition without moving already-observed calibration companies.

Artifacts: `evaluation/calibration-r1/manifest.json`, `evaluation/calibration-r1/report.json`, and `evaluation/calibration-r1/README.md`.
## 2026-07-29: Strict holdout iteration V16–V23

Status: maximum iteration V23 reached locally. Independent review found the V20–V23 replay scientifically invalid as well as below the accuracy gate; nothing was synchronized before this review.

- [x] Preserved V15 and every later frozen snapshot; failed and invalid rounds remain auditable.
- [x] V16 was invalidated for its source whitelist; V17 and V18 failed the three-match gate; V19 failed prediction coverage before labels were opened.
- [x] V20 froze 18 companies (six startup/private, six listed, six foreign), Top-3 hypotheses, a three-month future window, no JOSINT, no job-ad prediction inputs, and no workforce-precursor acceptance signal.
- [x] V20 mechanical prediction-side counts were 14/18 companies with hypotheses (77.8%), 39 distinct titles, 12 role families, and 39 canonical role keys. Independent review then found dynamic mainstream-media profiles without pre-cutoff capture timestamps, so these counts cannot support a leakage-safe scientific conclusion.
- [x] Completion audit invalidated the first mechanical V20 pass. The public result for 众擎机器人“机器人创意设计负责人” exposed employer, title, and salary, but no source-backed team-management or organization-level scope. Its earlier description had added unsupported scope and cannot be a Director+ label.
- [x] Rebuilt V20 conservatively with two defensible matches: 国轩高科“电芯生产工艺总监(新站一厂)” and Boston Scientific “Director, Ops”. V20 therefore fails the minimum three matches, three companies, and all-company-types gates.
- [x] V21 added mandatory source-backed seniority scope; V22 added publication intervals that must fall wholly inside the three-month window; V23 added complete employer, scope, and date evidence. Review showed the stored search summaries and scope statements lacked replayable source artifacts, so the validator now requires artifact path, SHA-256 and excerpt verification and marks all four iterations unverified.
- [x] V21–V23 deliberately reuse the already-frozen V20 predictions as label-quality audits and are not claimed as independent prediction holdouts. They mechanically retain two conservative matches, but uniform search and label quality are both unverified because raw captures are absent; all iterations are scientifically invalid and fail closed.
- [x] Fixed the review findings: stable source-group pseudonyms, dynamic-media capture-date guard, conservative Chinese seniority exclusions, replayable label artifact contract, tracked frozen snapshot, and mandatory label-quality runtime gate.
- [x] Stopped at the preregistered maximum V23 without lowering the acceptance threshold or manufacturing a third label.

Artifacts: `evaluation/holdout-v20/prediction-snapshot.json`, `evaluation/holdout-v20/mechanical-acceptance-summary.invalid.json`, and `evaluation/holdout-v20` through `evaluation/holdout-v23`. Local generated reports remain under `.acceptance/` and are not canonical.