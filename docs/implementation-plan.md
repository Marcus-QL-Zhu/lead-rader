# HT Lead Radar Implementation Plan / Delivery Ledger

- 状态：Implementation complete for the self-contained MVP；外部凭证/受限信源项单列为 Blocked
- 对齐 Spec：`docs/alignment-spec.md`（Approved v1）
- 最近更新：2026-07-25
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
| P1-01 | DONE | 旧版企业/媒体固定列表抓取器继续可用 |
| P1-02 | DONE | `config/source-packs.json`：29 个核验来源、6 个复用来源包、19 个默认启用来源 |
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

### Phase 6 — 原生业务结果学习

P6-01 至 P6-05 全部为 `NOT-PLANNED`：不建设业务结果数据模型、Precision@K 仪表板、自动调权、训练集、Float 转化漏斗。用户根据实际判断直接要求 Agent 修改规则、来源或展示。

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

默认方向由 `HT_LEAD_DAILY_DIRECTION` 控制，缺省为 `具身智能`。cron 必须为：

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
