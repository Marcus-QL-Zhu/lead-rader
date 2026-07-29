# Holdout v15 独立未来职位标签审计

## 标签口径

- 检索协议：`uniform-director-plus-v1`
- 公司范围：`manifest.json` 中全部 18 家公司
- 地域：中国大陆
- 发布时间窗口：`[2026-04-01, 2026-07-01)`
- 纳入层级：Director、VP、SVP、EVP、CxO，以及具有明确组织所有权的 Head
- 排除层级：经理、专家、工程师、普通 Lead、Associate/Assistant/Deputy Director、AVP
- 独立性：标签制作期间未读取预测、matcher、旧标签或 `.acceptance` 下的 snapshot/report/diagnostic，也未运行 evaluator。

原计划使用 `browser-act` 执行浏览器检索，但本机没有可用的 `browser-act` 命令；根据该技能的安装约束，未擅自安装，改用只读公开网页搜索与官方招聘入口交叉核验。所有公司仍按相同协议各执行一次 `official_careers` 与一次 `public_web_search`。

## 最终结果

- 合格职位：1 个
- 命中公司：1 家
- 公司审计覆盖：18/18
- 搜索执行覆盖：36/36（每家公司恰好 2 条）

| 公司类型 | 命中公司数 | 审计公司数 | 命中公司 |
|---|---:|---:|---|
| startup_private | 0 | 6 | 无 |
| listed | 1 | 6 | 隆基绿能 |
| foreign | 0 | 6 | 无 |

## 纳入职位

### 隆基绿能 — 设计总监

- 地点：无锡市新吴区
- 发布时间：2026-04-23
- 来源：[隆基绿能招聘页面](https://www.longi.com/cn/career/)；[公开招聘聚合页](https://www.jobui.com/company/13647928/jobs/shuoshi/)
- 判定：标题为“设计总监”，达到 Director 层级；地点属于中国大陆；公开结果显示的发布时间在标签窗口内，因此纳入。

## 逐公司审计

所有审计的统一执行时间为 `2026-07-28T21:31:45+08:00`，窗口均为 `[2026-04-01, 2026-07-01)`。每家公司在 `jobs.json` 中均有且仅有两条搜索记录，顺序为 `official_careers`、`public_web_search`。

| # | 公司 | 类型 | 审计结果 | 主要判定依据 |
|---:|---|---|---|---|
| 1 | 斯克斯机器人科技有限公司 | startup_private | no_eligible_job | 公开招聘以工程师等非 Director+ 岗位为主；未发现窗口内合格职位。 |
| 2 | 汉可智能装备有限公司 | startup_private | no_eligible_job | 官方/公开结果仅见普通岗位；未发现窗口内合格职位。 |
| 3 | 后摩智能 | startup_private | no_eligible_job | 招聘入口及公开结果未发现窗口内有效 Director+ 职位。 |
| 4 | 伯芯微电子（天津）有限公司 | startup_private | no_eligible_job | 公开岗位为技术类非 Director+ 职位；未发现合格职位。 |
| 5 | 赛富乐斯半导体 | startup_private | no_eligible_job | 当前公开职位均未达到纳入层级。 |
| 6 | 海东红狮半导体有限公司 | startup_private | no_eligible_job | 未发现标签窗口内的合格职位。 |
| 7 | 隆基绿能 | listed | matched | 发现窗口内中国大陆“设计总监”职位，纳入 1 个。 |
| 8 | 京东方华灿光电 | listed | no_eligible_job | 官方/公开结果以校招、普通岗位或公司资讯为主；无合格职位。 |
| 9 | 申能股份 | listed | no_eligible_job | 未发现窗口内公开真实 Director+ 招聘职位。 |
| 10 | 长安汽车 | listed | no_eligible_job | “招聘总监/研发总监”等结果为现任人员或活动信息；子公司岗位不按清单中的精确雇主归入。 |
| 11 | 蔚来 | listed | no_eligible_job | 官方/公开结果未发现满足层级、地域和日期三项条件的职位。 |
| 12 | 长电科技 | listed | no_eligible_job | 结果以校招、普通岗位或新闻为主；无合格职位。 |
| 13 | BD（碧迪医疗） | foreign | no_eligible_job | 中国招聘入口及公开搜索未发现窗口内合格职位。 |
| 14 | 立邦 Nippon Paint | foreign | no_eligible_job | 发现“人力资源副总监”等结果，但 Deputy Director 明确排除。 |
| 15 | Huba Control | foreign | no_eligible_job | 中国大陆范围未发现合格职位；海外岗位不纳入。 |
| 16 | 天津大冢饮料有限公司 | foreign | no_eligible_job | 公开职位包括“区域主管”等，层级不足。 |
| 17 | 瓦克化学 WACKER | foreign | no_eligible_job | 中国公开招聘以校招或普通岗位为主；未发现合格职位。 |
| 18 | 宝马集团 BMW Group | foreign | no_eligible_job | 中国区 VP 任命新闻不是公开招聘职位，予以排除。 |

## 一致性说明

- `jobs.json` 中仅隆基绿能的 `result` 为 `matched`，并且仅该公司存在合格职位记录。
- 其余 17 家公司的 `result` 均为 `no_eligible_job`，与空职位结果一致。
- 新闻中的高管任命、在职人员头衔、活动嘉宾头衔、不同法人或子公司岗位，以及日期不在窗口内或无法确认在窗口内的结果，均未作为正标签。
