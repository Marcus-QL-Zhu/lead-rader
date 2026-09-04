# Lead Radar 项目踩坑、教训与工程护栏

> 更新日期：2026-09-05
> 适用范围：Lead Radar 的信源采集、语义处理、公司与事件归一、岗位预判、历史回测、OpenClaw/飞书协作、猎聘草稿及生产运维。
> 目的：把已经付出过代价的错误变成后续开发必须遵守的约束，避免上下文压缩、人员切换或版本迭代后重复踩坑。

## 0. 证据口径

本文有意区分三种表述：

- **已确认产品决策**：用户已经明确取舍，后续不得靠开发者猜测改回去。
- **项目实测**：来自仓库测试、冻结评估、服务器运行记录或已复核产物。
- **工程推断/待验证**：已有代码或运行证据支持，但仍需生产回放才能最终确认。

新闻、职位广告、LLM 输出、OpenClaw 对话和飞书消息都不是天然真相。任何结论必须能回到原始公开证据、稳定实体、事件时间和处理版本。

## 1. 北极星：预测“责任与能力缺口”，不是搜职位

### 踩过的坑

1. 把融资、产品发布、订单等事件直接映射成固定岗位，例如“融资 = 商业化总监”。结果很快收敛成机械、电气、软件、算法等同质化岗位。
2. 只看公司自身新闻，遗漏高管变动、上游需求、客户放量、法规变化、产业链瓶颈等更早的信号。
3. 把已经发布的招聘广告当作发现来源，导致猎头只能在企业公开招聘之后追单。
4. 把“公司可能扩张”“候选人能力匹配”和“公司愿意委托招聘”混为同一个判断。

### 教训与护栏

- 核心预测对象是潜在需求 `N`、组织响应 `R` 和公开观测 `O`：
  - `N`：责任、能力或产能缺口；
  - `R`：招聘、内部晋升、外包、并购、合作、自动化或暂缓；
  - `O`：职位广告、团队变动、采购、任命等外部可见结果。
- Lead 推理必须写出机制链：
  `事件/外部冲击 → 公司暴露 → 责任或瓶颈变化 → 可能的组织响应 → Director+ 岗位假设`。
- 公司 Alpha 事件、行业 Beta 冲击和供应链/客户/竞争/人才网络暴露要同时考虑；“属于同一行业”本身不构成公司级证据。
- 高管到任、区域负责人更替、组织重组是高价值前置信号，因为新负责人常会重建下属团队；但仍需结合职责范围和后续经营动作。
- 行业景气改善不等于总人数必然上升。系统应优先预测“哪类责任会变重、瓶颈在哪里、可能采用什么响应方式”。
- JOSINT 和公开招聘信息只能做晚期验证、结果标签或补充观察，不能回流成历史预测输入。
- 允许 `unknown`、`abstain` 和右删失。没有找到职位广告，不等于公司没有需求。

相关学术模型见：

- `[[concepts/enterprise-demand-as-latent-organizational-state]]`
- `[[concepts/enterprise-demand-alpha-beta-network-model]]`
- `[[concepts/enterprise-demand-multimechanism-model]]`
- `[[explorations/enterprise-demand-organizational-theory-lenses]]`
- `[[explorations/beta-network-osint-architecture]]`

## 2. 已确认的产品边界必须优先于“技术上可以做”

### 主输出

- 日常主榜只关心总监级及以上，或实际承担同等级组织责任的岗位。
- 目标是 Top 20，按可解释分数降序。候选不足时允许降低软门槛补足，但不能放宽两项硬门槛：
  1. 能形成 Director+ 责任假设；
  2. 至少有一条公开招聘广告之前的上游信号。
- 只有职位广告、没有上游信号的公司只能进入晚期机会附录。
- 公司进入榜单必须展示分数来源、证据强弱、时效、加减分和关键不确定性，最终由人判断。
- 公司冷却期为 7 天；出现实质新证据时可以提前回归。

### 自动化边界

- 每日 Top 20 只做基础研究。只有用户主动要求深度研究或执行 Float，才深入研究投资人、业务 Hiring Manager、HR 和创始团队关系路径。
- 系统可以缓存公开投资机构、投资人、赛道、轮次和证据，逐步形成投资图谱。
- 不生成触达话术，不自动联系外部人员，不把公开职业信息扩展为私人联系方式。
- 不建设简历库，不长期保存候选人原始简历或派生 CandidateProfile。临时工作区已有文件无需主动删除。
- 应持久化“公司—信号—岗位假设—人才主题—猎聘 JSON”的关系，以便收到简历后反向 Float；这与保存候选人数据是两件事。
- 不建设原生业务结果评分、自动调权或反馈训练系统。Lead 排序分仍然保留，但不能冒充业务效果评估。
- 不增加负面信号扣分模块。负面表述只用于避免把取消、传闻、否认、失败等误判成已发生的正向事件；经营困难本身也可能带来转型招聘。
- 不从旧对话推断“任务结束后关机”。只有当前明确指令才授权关机。

## 3. 信源体系：宽覆盖靠组合，不靠公司官网或一个通用爬虫

### 踩过的坑

1. 每个赛道分别请求同一批通用信源，造成重复抓取、重复语义处理和配额浪费。
2. 用特定公司官网作为每日发现入口，候选公司宇宙被预置名单锁死。
3. 只做一个泛化列表抓取器，面对 36 氪、创业邦等高价值聚合站时只拿到极少条目，无法证明“每日增量完整”。
4. 把站点抓取成功率当作事实准确率；实际上列表、详情、主体、事件和投影每一层都可能分别出错。
5. 详情失败后静默丢弃，让“没有线索”和“系统没抓到”无法区分。

### 教训与护栏

- 日常任务应把所有通用和五个硬科技方向信源合并成一次来源并集：每个物理信源只抓一次，再统一归一、聚簇、评分和选 Top 20。
- 手动指定行业时，只启用通用来源包和该方向来源包，避免无关成本。
- 日常发现禁止特定公司官网。公司已经由广泛来源发现后，官网可用于人工或按需核验。
- 高价值聚合站使用专属 adapter；调度、状态、重试、死信、语义缓存和健康指标由共享框架负责，不能每站自建一套小系统。
- 每个专属 adapter 都要保留：原始列表/详情快照、稳定文章 ID、源发布时间、规范 URL、正文哈希、解析版本、降级状态和可重放产物。
- 验收必须逐项对照：
  `原始网页增量 → adapter 条目 → 确定性候选 → MiniMax 裁决 → 归一事件 → OpenClaw 展示`。
- “死信”只是某篇文章的详情或语义处理没有通过，进入隔离重试队列；它不是整天任务失败，也不能被当作已处理成功。
- 一个信源失败时要隔离并继续其他信源；同时在日报中明确标记 `partial/error`，不能静默伪装为健康。
- Metaso 每日额度有限，应只做低频全网核验和覆盖抽查，不承担日常发现主链路。

## 4. Scrapling 的正确边界：只修 DOM 漂移

### 踩过的坑

- 把 Scrapling 当成万能抓取器，或者在普通采集路径中无条件启用 adaptive/`auto_save`，会引入持久化开销、Chromium 资源占用和大量无意义告警。
- 服务器内存有限，多 Chromium 或高并发会把采集阶段拖成长时间满 CPU，甚至需要外部 SIGTERM。
- 网络库的 socket timeout 不等于整个操作有 wall-clock 上限；DNS、读取流、子进程和自适应存储都可能绕开预期超时。

### 教训与护栏

- 精确 CSS/XPath 或公开 API 是主路径；Scrapling 只在已验证选择器发生 DOM 漂移时做受控重定位。
- 禁止用 stealth、代理轮换、验证码处理或访问控制绕过能力。
- adaptive 关闭时，不得传 `auto_save`，不得打开或迁移 adaptive 存储。
- 生产服务器最多同时运行一个 Chromium；浏览器并发与 LLM 并发必须分开配置。
- 普通 HTTP 失败应按来源隔离、记录并跳过；不能为了救一个站点挂住整个日批。
- 连接、响应读取、单来源、采集阶段和整个日批都需要独立 wall-clock deadline。

## 5. 增量识别：动态展示字段绝不能污染内容身份

### 关键事故模式

同一篇旧文章在列表页可能从“3 分钟前”变成“2 小时前”“3 天前”，或者因为翻页位置、抓取时间、游标变化而得到不同 payload。如果这些字段进入 `content_hash`，系统会把旧文误判为新文，再次下载、再次调用 MiniMax、再次聚簇。

2026-09-04 的服务器运行证据显示：一次任务处理了 261 篇文章，116 篇进入 MiniMax；部分来源中大量旧文章被重新送入语义层，并在 30 分钟采集预算内未完成。这一现象说明“增加总超时”不能替代修复增量身份。

同日 22:45 的复跑又在 30 分钟 watchdog 处终止，并产生 608 条“adaptive 未启用但仍传入 `auto_save`”告警；运行账本中的 `collect` checkpoint 仍停留在 `running`。这证明日志噪声、增量重算和外部终止后的状态收口是三个问题，必须分别修复。

### 强制规则

- 语义内容指纹只能由稳定字段构成：规范 URL/源 ID、规范标题、稳定摘要或正文、必要的静态结构字段。
- 以下字段不得进入语义指纹：`time_label`、相对时间、列表位置、页码、游标、访问时间、`checked_at`、`discovered_at`、抓取批次和 adapter 运行元数据。
- 原始快照哈希、规范文章哈希、语义输入哈希要分开；不能用一个 hash 同时承担审计、增量和模型缓存三种职责。
- 对每个高价值 adapter 增加“两次抓取仅动态字段变化，稳定内容哈希不变且 MiniMax 不重跑”的回归测试。
- Prompt 或模型版本变化不应自动把全部历史文章重新送入正式日批。需要迁移时使用显式离线回放计划和独立预算。
- 列表 hash 迁移后，如果详情正文 hash 未变，只有在 prompt、模型、claim contract 均相同且不存在开放死信时，才可把语义审计重新绑定到新列表 hash；正文变化仍必须重跑语义层。
- 语义审计必须同时记录 `index_content_hash`、`article_content_hash`、`final_event_count`、Prompt、模型和 claim contract。2026-09-05 复核发现早期 V27 审计遗漏两个 hash，导致缓存永久 miss；只修读取条件而不修所有审计生产者，会让问题原样保留。
- 审计、事件和别名必须作为一个事务提交。若先提交“成功审计”、再替换事件，进程恰在两步之间终止，旧事件的数量又碰巧相同，下次运行可能把旧 Prompt 的结果误认成新结果。
- 旧 V27 缓存迁移必须 fail closed：正事件要求正文 hash、事件数和事件 Prompt 全部一致；零事件没有事件行可证明正文归属，只能在 clean article 明确早于该审计且之后未被重写时恢复。不能为了减少 MiniMax 调用而无条件补 hash。
- 结构化 URL 字段只能用 URL 专用脱敏器处理。通用手机号正则会把日期式文章 ID（如 `2026-08-12-7`）误删，破坏 canonical URL、死信恢复和唯一约束。
- 同日重跑应复用同一快照和已完成阶段；已经汇报的结果返回 `already_reported/no_change`，不能用误导性的 `LookupError` 表示正常幂等状态。

## 6. LLM 不是第一道工序，也不是事实创造器

### 踩过的坑

1. 直接把长文章、多个条目、所有实体和所有任务一起塞给 MiniMax，模型难以抓住主体和事件。
2. 出错后只改 Prompt，掩盖了更早的列表遗漏、条目切分、实体边界、动作候选和评测投影问题。
3. 允许模型自由生成公司名、事件类型或证据句，产生句子碎片、产品、机构或上下文词被当作公司。
4. Few-shot 结构变稳定后，就误以为事实也准确；结构正确与事实正确是两个验收轴。

### 正确分工

1. 先做文档路由：单公司快讯、多公司简报、重大事件扩写、采访/评论、政策/路线图、低价值背景。
2. 多条目文章先按条目切分，再建立条目内实体和动作边界；不能先截断整篇再猜主体。
3. 单主体长文通常只保留前 2,000 字。若前 2,000 字仍没有清晰公司动作，允许判为低价值并跳过。
4. 确定性代码建立 `Entity Ledger`、`Action/Claim Ledger`、原文 span 和稳定 ID。
5. MiniMax 只做有限裁决：接受/拒绝候选、从 ledger 选主体、判断状态、消除别名和有限字段歧义。
6. 宿主锁定事件类型、claim ID 和证据边界，并对输出做确定性 schema 与逐字 grounding 校验。
7. 一次修复后仍不合格就保留已验证规则结果、记录失败并进入重试/死信；不能无限自我修复。

### 模型与成本规则

- Lead Radar 读取 OpenClaw 所属 provider 的配置和 API key，但直接调用 MiniMax API；不能把分析任务交给 OpenClaw Agent，也不能修改 OpenClaw 全局主模型。
- 当前默认模型为 `minimax/MiniMax-M3`，温度 0；模型名必须可配置和可回退。
- Prompt 优化最多 3 轮。每轮必须使用冻结训练样本和独立评审，不得反复窥视 final holdout。
- 测试环境可允许 MiniMax 4 并发；低内存生产服务器默认 1 个 LLM worker，并且最多 1 个 Chromium。
- 稳定的 `内容指纹 + 模型 + Prompt/contract 版本` 应命中语义缓存，避免重复消耗。
- 给模型的输入要最小化；Few-shot 能帮助格式，不能修复被动态字段污染、主体切错或证据缺失的输入。

## 7. 公司主体、事件与聚簇：先解决“谁做了什么”

### 踩过的坑

- 早期冻结评估出现过公司主体准确率仅 18.97%、强当前事件召回 18.60% 的失败；这证明输出 JSON 可解析并不代表系统可用。
- 同一融资的标题、摘要、详情和转载被重复计分；累计融资背景又被误当成新一轮融资。
- 多公司简报出现跨条目主体串线；公司、投资机构、政府、项目、产品和媒体角色混在同一实体候选集合。
- 用字符串后处理替换错误公司名，修复了一个样本却破坏了可泛化边界。

### 教训与护栏

- 公司实体必须来自可信 seed：明确法定名、显式简称、ticker、标题中的动作主体或具有角色锚点的公司描述。
- 政府、研究机构、产品、项目、赛道、栏目名和句子碎片默认不得进入可运营公司集合。
- 模型只能返回 ledger 中的 `entity_id`；新别名必须有原文 span 和归一证据。
- 条目边界优先于整篇媒体上下文。多公司列表不得继承上一条公司的主体。
- 事件去重按“主体 + 动作 + 时间/状态 + 证据簇”进行；转载增加佐证，不增加事件分。
- 不同融资轮次、不同主体、完成与计划等独立动作不能因为关键词接近被错误合并。
- 指标至少拆成三轴：主体身份、事件支持/类型、状态；不能用一个混合准确率掩盖具体失败。
- 每篇高价值文章的停止条件是：清晰增量遗漏为 0、编造主体/事件为 0、无证据 claim 为 0、投影失败为 0。

## 8. 岗位预判与猎聘 JSON：具体责任先于广告文案

### 踩过的坑

1. 先用宽泛规则生成“销售/算法/工程总监”，再让广告生成器润色，导致职位高度同质化且缺少吸引力。
2. 同一家公司或同一人才主题重复生成多条相似草稿，日报每天仍是少数几家公司。
3. 草稿字段看似完整，却不符合 `liepin-job-posting/SKILL.md`：如 `job_type`、`languages`、`seniority`、`position_scope` 结构和 `cities` 数量错误。
4. OpenClaw 在对话中修了 JSON，但未写回持久化存储；用户批准后发布的仍是旧 payload。
5. `expires_at` 为空导致草稿状态或发布失败。
6. 系统提示词加入用户没有要求的“人才蓄水声明”，既污染输出又降低职位吸引力。

### 教训与护栏

- 在公司级需求分析阶段就由 Agent/MiniMax 推理具体责任缺口、目标结果、团队/业务边界和为什么是现在；广告生成只把已验证岗位假设转为合规 payload。
- 岗位标题必须体现具体业务责任，且是 Director+；不能只用行业词加“总监”凑标题。
- 同一家公司每天最多进入一个人才主题；跨公司相似责任可以聚成一个可复用人才主题，但必须保留每个来源公司的映射。
- `cities` 在 JSON 中仍是数组以兼容 Skill，但长度必须严格等于 1；无法判断时使用 `['上海']`。
- 猎聘 payload 必须逐字段遵循当前 `liepin-job-posting/SKILL.md`，不能维护一份凭记忆写的平行 schema。
- `position_scope`、职级、经验、语言、招聘类型、薪资跨度等在进入人工审批前做确定性校验。
- 不生成“人才蓄水”“并非真实委托”等额外声明；广告匿名化通过不暴露公司、产品、证据和内部评分实现。
- 不用“禁止生成某某声明”这种无必要的负向措辞污染 LLM system prompt；未要求的概念应直接从 Prompt 省略，再由确定性输出校验守边界。
- 任何人工修订都必须以新 payload hash 写回权威草稿库并使旧批准失效；发布器只能读取持久化后的当前版本。
- 草稿创建时必须写入非空、可解释的 `expires_at`，并有迁移与回归测试。

## 9. 审批、OpenClaw 与飞书：对话自然，状态机器严格

### 踩过的坑

- 飞书只收到摘要，OpenClaw 当前会话不知道日报和草稿；用户无法自然追问“展开第 2 个 JSON”。
- OpenClaw 每天 04:00 重置会话，只加载 bootstrap 文件，旧对话不能作为项目知识源。
- 发布必须输入精确机器指令，OpenClaw 即使已经理解“发布第一个”也要求用户重说“发布 1”。
- 为安全加入过重的、用户可见的 snapshot code，增加操作负担。
- hook 或日报失败时，任务可能有结果但无人得知；反过来，未确认送达的消息却被当作已交付，错误触发冷却。
- 失败或零草稿没有生成 completion snapshot，05:50 的 OpenClaw 无内容可读。

### 教训与护栏

- 05:00 任务必须持久化当日 completion snapshot、公司排序、岗位映射、有效猎聘 JSON、四类状态和信源健康；飞书只是展示，不是事实源。
- 日报不能只展示最终三条草稿。至少要给出全局候选公司简表、入选/冷却/抑制/失败数量，以及每条草稿对应的公司和推测岗位，让用户能解释“为什么今天都来自同一行业”并继续追问。
- 日批结束后的 hook 是主通知路径；OpenClaw cron 仅在 05:50 和 06:50 两次对账，不使用 heartbeat。
- hook 给 OpenClaw 的输入应包含两件事：项目最小地图/操作指南位置，以及今天 snapshot 的稳定引用。
- OpenClaw 在空会话中必须能先读取 `SKILL.md`、operator guide 和当日 snapshot，再汇报并承接自然语言追问。
- 用户可说“发布第一个”“发 1 和 3”“确认”“查看第二个完整 JSON”。意图和编号由 OpenClaw 判断，不能强迫用户复述精确 CLI。
- 不向用户暴露 snapshot code；内部仍用 run identity、draft ID、payload hash、展示状态和幂等键防止旧批次、重复发布与竞态。
- hook/cron/飞书报告本身绝不构成批准。真实入站用户消息表达批准后，OpenClaw 才可调用执行入口。
- 只有当前日报已经完整展示、选择范围明确且草稿全部通过校验时才能发布；含糊“确认”只能继承 OpenClaw 上一条明确提议。
- 发布按顺序执行；登录、验证码、风控、限流或结果不确定时 fail closed，停止剩余队列。
- 通知去重以“确认送达”为准，不能只看 OpenClaw 队列表状态。hook 失败时可直接飞书兜底，但要共享 delivery ledger，防止双发。
- 分析完成但草稿失败、零草稿或来源部分失败时也必须保存可读 snapshot，并如实报告 `analysis/draft/delivery/health` 四个独立状态。

## 10. 历史训练集与评估：今天就能回测，但不能穿越

### 已确认方法

- 不等待系统运行三个月再积累标签；从历史新闻和历史职位构建 point-in-time 数据集。
- 以新闻时间戳模拟 cutoff。例如只给模型 1–4 月证据，预测 5 月，然后查看 5–7 月是否出现相似 Director+ 职位。
- 同一家公司可按不同月份形成多个样本，但训练/测试必须按公司分组；同一公司的不同月份不能跨 split。
- 训练集目标约 40–50 家，测试集 15–20 家。最终 holdout 只在规则、Prompt 和阈值冻结后运行一次。
- 经理、专家、工程师可以被系统采集和建模，但不得作为 Director+ 主评估的通过信号。

### 踩过的坑与护栏

- 当前搜索结果、LLM 世界知识、缓存页、后见之明摘要和未来职位标题都可能泄漏到预测输入；必须保存 `published_at`、`observed_at` 和 `ingested_at`。
- 猎聘 SEO `/s/` 聚合页、搜索摘要或当前在招页不能冒充历史精确职位标签。合格标签需要可追溯的历史职位详情、首次观察时间和内容哈希。
- 没找到公开职位是 `unknown/right-censored`，不是严格负样本。评估可构造非事件对照，但这不等于产品里增加负面信号扣分。
- 开发集、reserve、final 一旦被用来调参，就必须降级为开发错误集；不能反复重跑后继续宣称独立测试通过。
- 测试要覆盖公司外、时间外、行业外和正文近重复隔离；同时报告 Precision@K、召回、主体准确率、状态准确率、校准和来源覆盖。
- 必须做消融：公司 Alpha only、Alpha+Beta、Alpha+网络暴露、完整组织机制。否则无法证明复杂架构真正增加了预测价值。
- 保存失败结论，不为达到目标而改标签。V15/V20/V25/V27 等冻结记录的价值正在于暴露系统在哪一层失效。

相关研究设计见：

- `[[explorations/lead-radar-phd-research-design]]`
- `[[explorations/lead-radar-phd-alpha-beta-research-amendment]]`
- `[[explorations/beta-labor-demand-evaluation]]`
- `[[explorations/online-job-postings-as-demand-labels-literature]]`

## 11. 生产运行：所有外部工作都必须有界、可恢复、可解释

### 已发生或已暴露的问题

| 现象 | 根因/机制 | 永久护栏 |
| --- | --- | --- |
| 05:00 任务因 Python 不匹配失败 | cron 命中服务器 `/usr/bin/python3` 3.6 | launcher 使用固定 Python 3.11.14，启动前检查 `>=3.10`，不依赖 cron PATH |
| 任务运行约 10 小时、CPU 长期满载 | Scrapling/Chromium、自适应存储、无边界网络读取和语义工作叠加；低内存服务器不适合高并发 | 单 Chromium、生产 LLM worker=1、分层 wall-clock deadline、逐来源耗时与阶段日志 |
| 外层 SIGTERM 后数据库仍显示 `running` | 父进程被杀时没有统一 finalize，checkpoint/effect 留在中间态 | 子任务原子写 run ID；wrapper 在 watchdog 返回后用独立进程只收口该 run 的在途 checkpoint/effect；操作幂等且不改已终态 run |
| 30 分钟内处理大量旧文后超时 | 动态列表字段污染指纹或增量门失效，旧文重新进入 MiniMax | 修稳定身份和二次抓取回归，不用单纯放大超时掩盖 |
| 修缓存后仍可能首轮大规模回填 | 旧 V27 audit 缺 index/body hash，且新校验无法证明旧 materialization | 按正文、事件数、Prompt、写入顺序和死信状态做受限恢复；无法证明的少量条目只重跑一次 |
| 新审计已落库但事件仍是旧版 | audit 与 events 分两次 commit，外部终止发生在中间 | `store_semantic_result` 单事务替换 audit/events/aliases，并用中断回滚测试验证 |
| 同日重跑返回 LookupError | 幂等正常状态被当成异常 | 返回结构化 `already_reported/no_change` 并引用现有 snapshot |
| 运行日志长时间没有新输出 | stdout 缓冲且缺少阶段心跳 | 关键阶段结构化落盘、刷新 stdout、按来源记录 start/end/count/duration |
| 来源详情失败后日报看似正常 | partial/dead-letter 没有传到顶层健康状态 | 每 adapter 独立状态；critical health 复制到 completion 和 OpenClaw 上下文 |
| 飞书有消息但 OpenClaw 无上下文 | 只走直接通知，没有持久化+hook project map | completion snapshot + hook；05:50/06:50 对账；04:00 reset-safe operator guide |

### 运行规则

- 单来源故障、详情死信和单个 MiniMax 失败必须隔离；只有达到明确的全局终止条件才停止整批。
- 每个阶段落盘输入摘要、输出计数、耗时、失败分类和 checkpoint，使“卡在 collect”可以解释到具体 adapter、网络、解析还是 LLM。
- 总采集预算和草稿预算到期时，必须先持久化部分结果及失败 snapshot，再返回。
- 不在命令参数、日志、报告或异常中打印 token、API key、Cookie、飞书 secret 或原始候选人信息。
- 服务器飞书 app secret 的轮换当前由用户延期；不得在日志或文档中复制其值，也不得把“暂不轮换”误写成安全问题已经解决。

## 12. 三端与发布：GitHub 是唯一代码真源

### 踩过的坑

- 本地修了但服务器没更新，或服务器临时改了却没有回灌 GitHub，导致 OpenClaw 看到旧 bug。
- 本地 Windows 通过而 Linux CI 因换行、路径、Python 版本、symlink 权限或测试 fixture 失败。
- 用宿主机 `date.today()` 计算日报日期，而投递时间按上海时区解释，导致 UTC runner 在北京时间零点后把同一次投递误判成“未来记录”，冷却失效。
- 先宣称“已经部署”，后来才发现 commit 未 push、CI 未绿或服务器 symlink 仍指向旧版本。
- release 目录混入 pyc、运行时数据库或临时文件，破坏 exact-SHA 可审计性。

### 教训与护栏

- GitHub `main` 是源码、配置、文档、迁移和部署脚本的唯一真源；本地和服务器新文件都不自动获得权威性。
- 改动流程固定为：拉取并确认不落后 → 本地修改 → 全套测试 → 独立子代理 full code review → commit/push → exact SHA CI 绿色 → 部署该 SHA → post-deploy 验证。
- 不把服务器目录直接整体复制回仓库。紧急修复要在本地重做、测试、审查、推送并按 exact SHA 部署。
- 生产代码使用不可变 release，`data/`、`logs/`、`reports*/`、`backups/` 和 SQLite 状态放在 release 外部。
- 凭证只从 `/home/admin/.openclaw/secrets/lead-radar.env` 受控加载；禁止回退 JOSINT `.env`，禁止把密钥写入 cron、Git 或部署输出。
- 本机 GitHub 登录使用用户级凭证存储或 `gh auth`，使不同 Codex 会话共享登录状态；不得把 personal access token 写入仓库、任务 Prompt、项目 `.env` 或文档。
- JOSINT 是独立 GitHub 项目和晚期验证依赖。不得复制其源码或凭证到 Lead Radar；适配器要同时测试当前 canonical schema 与明确支持的 legacy fallback。
- 部署前备份并校验数据库与来源清单；部署后核验 `.deployed_git_sha`、stable symlink、树洁净度、Python、JOSINT、日报、健康状态和回滚指针。
- “测试通过”必须说清是哪一层：本地、Linux CI、生产 smoke 或真实日批。不能用低层测试替代生产结论。
- 日批入口只冻结一次 `Asia/Shanghai` 产品日，并显式传给分析、报告查找、草稿、通知和审批/过期判断；不能让各阶段各自读取时钟。UTC 只用于保存带时区的绝对时间戳。测试必须覆盖 UTC 16:00 之后已经跨入北京时间次日的边界。

## 13. 修 bug 时的层级诊断顺序

任何“漏抓、误判、重复、卡死、无草稿、日报异常”都先按以下顺序定位，不能直接改 Prompt：

1. **来源层**：列表是否完整、详情是否可达、源时间是否正确、是否有访问控制。
2. **增量层**：稳定 ID/哈希是否变化、是否错误重跑旧文、缓存是否命中。
3. **文档路由与切分层**：文章类型是否正确、多条目是否串线、长文窗口是否合适。
4. **实体与动作候选层**：主体 seed、别名、span、动作是否完整且有边界。
5. **MiniMax 裁决层**：候选是否有证据却被误拒、状态/主体选择是否错误、结构是否合规。
6. **事件归一与聚簇层**：是否重复、错合并、跨主体合并或累计融资当新事件。
7. **组织需求与岗位层**：机制链是否足以支持 Director+，角色是否具体且非模板化。
8. **草稿/审批/发布层**：Liepin schema、持久化 payload、有效期、批准和幂等是否一致。
9. **通知与运行层**：completion、delivery ledger、OpenClaw hook/cron、超时 finalize 是否正常。

只有证据表明问题位于当前层，才修改该层。每次修复必须说明：为什么在这一层修、为什么更早的层不用改、如何用反例和二次运行证明没有引入新问题。

## 14. 未来改动的最低验收清单

### 信源或 adapter

- 原始列表和详情可重放；稳定 ID、源时间、规范 URL 和正文哈希齐全。
- 冷启动与增量两次运行均测试；只改变动态字段不产生新文章或新 LLM 调用。
- 正常路径、精确选择器漂移、普通 HTTP 失败、详情死信和访问控制均 fail closed。
- 独立核对原始网页与语义结果：清晰增量遗漏 0，主体+事件事实错误 0。

### 语义或推理

- 路由、条目切分、entity/action ledger、MiniMax 裁决和最终投影分别可测试。
- 所有主体、事件、状态和关键字段有原文 span；模型不能新造实体或事实。
- 缓存键不含动态抓取元数据；Prompt/模型迁移不污染正式日批。
- 在冻结开发集收敛后，只运行一次隔离 holdout；失败结果永久保留。

### 岗位与发布

- 每个岗位都有“为什么是现在”的机制链和公司映射。
- Director+、标题具体、单城市、匿名、同批不重复。
- 完全遵循当前 Liepin Skill schema；修订写回数据库并更新 payload hash。
- 真实用户批准前不发布；自然语言审批可用，旧批次和重复事件被幂等拦截。

### 生产

- 固定 Python、单 Chromium、分层 timeout、SIGTERM/timeout finalize 和陈旧运行恢复均有测试。
- 零草稿、草稿失败、来源 partial、hook 失败和飞书兜底都生成可读且不重复的状态。
- 全 pytest、Ruff、compileall、`git diff --check`、凭证/运行产物扫描通过。
- GitHub exact SHA CI 绿色后才部署；服务器 exact SHA、symlink、secret 权限和真实 smoke 均有证据。

## 15. 主要本地依据

产品与实现：

- `docs/alignment-spec.md`
- `docs/implementation-plan.md`
- `docs/solution-blueprint.md`
- `docs/aggregate-adapter-authoring-guide.md`
- `docs/aggregate-adapter-rebuild-plan.md`
- `docs/evidence-bound-minimax-workflow.md`
- `docs/talent-pool-closed-loop.md`
- `docs/talent-opportunity-persistence.md`
- `docs/production-stopgap-spec-2026-08-31.md`
- `docs/production-stopgap-implementation-plan-2026-08-31.md`

冻结评估与失败分析：

- `docs/semantic-v25-formal-v1-failure-analysis.md`
- `docs/semantic-v25-optimization-plan.md`
- `docs/semantic-v27-final-v2-failure-analysis.md`
- `docs/semantic-v27-fix-layer-analysis.md`
- `docs/semantic-v27-release-readiness.md`
- `docs/long-article-window-case-study-v3-kr36-digest.md`

知识库研究：

- `[[explorations/lead-radar-phd-academic-synthesis]]`
- `[[explorations/lead-radar-phd-research-design]]`
- `[[explorations/lead-radar-phd-alpha-beta-research-amendment]]`
- `[[explorations/enterprise-demand-organizational-theory-lenses]]`
- `[[explorations/industry-beta-mechanisms-deep-map]]`
- `[[explorations/beta-labor-demand-evaluation]]`
- `[[explorations/online-job-postings-as-demand-labels-literature]]`

## 16. 维护规则

- 每次生产事故、冻结评估失败、用户产品边界变更或跨层架构调整，都应在修复同一变更中更新本文。
- 新教训必须包含：现象、根因所在层、错误做法、永久护栏、验证证据和仍未解决项。
- 不把一次样本修复写成普遍规律；不把工程推断改写为已证实事实。
- 若项目仓库与 LLM wiki 版本不一致，先在项目仓库更新本文件，再同步到 wiki；GitHub 合并后的版本是工程规则的代码侧真源。
