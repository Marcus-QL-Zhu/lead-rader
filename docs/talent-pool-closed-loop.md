# 人才蓄水闭环：本地实现与猎聘边界

## 目标

每日 Lead Rader 报告完成后，系统只使用当日报告中的公司、Director+ 岗位假设和上游事件，聚合出 3–10 个跨客户可复用的人才画像，生成匿名人才蓄水广告草稿。默认生成 5 个。草稿与市场 Lead 排名合并在同一条飞书日报中。

人才蓄水广告不代表任何一家真实企业已经委托招聘。公开广告不包含触发 Lead 的公司、别名、产品、创始人、客户、证据或内部得分。

## 能力矩阵

| 能力 | 归属 | 当前处理 |
|---|---|---|
| 公司信号、Director+ 岗位假设 | Lead Rader | 直接复用当日 JSON 报告，不增加 Metaso 调用 |
| 人才画像聚合、去重、多样性 | Lead Rader | 新增确定性离线生成器 |
| 匿名广告与泄漏闸门 | Lead Rader | 新增公开层/内部层隔离与禁词检查 |
| 职位 JSON | 猎聘 Skills 契约 | 按真实 `publish_job.py` 可消费字段和枚举生成 |
| 明确批准、7 天有效期、审计 | Lead Rader | 新增最小 SQLite 状态层 |
| 发布幂等、串行队列、错误分类 | Lead Rader | 新增薄编排层 |
| 浏览器发布 | `liepin-job-posting` | 直接调用，Lead Rader 不重写客户端 |
| Sourcing 接管 | `liepin-full-pipeline` | 发布取得 `ejob_id` 后传入完整 criteria |
| 自动回复与后续状态 | 现有猎聘 Skills | 继续由原链路负责 |
| 飞书消息发送 | Lead Rader 现有通知器 | 复用同一条 05:00 汇总，不增加第二条通知 |

## 只读盘点确认的真实调用边界

真实职位发布入口：

```bash
python3 liepin-job-posting/scripts/publish_job.py /path/to/payload.json --no-pipeline
```

必须先把 payload 写入临时 JSON 文件，并把该文件路径放在第一个参数；不得传 JSON 字符串，也不得无参数调用，因为原脚本会对第一个参数执行 `open(path)`，且无参数时存在执行真实发布的内置回退。发布成功不能只看退出码，必须同时确认 `runtime/job_postings.json` 新增了唯一 `ejob_id`。

取得 `ejob_id` 后，Lead Rader 把完整 criteria 写入临时 JSON，并调用：

```bash
python3 liepin-full-pipeline/scripts/orchestrate.py /path/to/criteria.json
```

发布脚本内置的 pipeline handoff 会丢失部分筛选字段，因此闭环明确使用 `--no-pipeline`，再把未被裁剪的 criteria 交给 full-pipeline。

## 状态与批准

状态为：

```text
pending_approval -> approved -> publishing -> published
                  -> rejected
                  -> publish_failed
                  -> expired
```

只接受四类精确指令：

- `发布全部`
- `发布 1,3,5`
- `跳过全部`
- `查看 2 的完整广告 JSON`

“可以”“发吧”“发布1,3”、中文逗号或越界编号均不构成批准。批准记录 actor、时间、原始命令和 payload hash。公开 payload 发生任何变化，批准立即失效。草稿默认 7 天过期。

发布按日报顺序串行执行。普通单项字段错误会记录失败并继续；登录、验证码、风控、限流、要求人工处理或结果不确定会立即停止剩余队列。`draft_id + payload_hash` 是本地幂等键。职位已经创建但后续 Sourcing 接管失败时仍保留为 `published`，阻止重复发广告，并记录阻断告警。

## 安全开关

`scripts/talent_pool_control.py` 默认只写批准状态，不调用猎聘。测试只能使用 `--fake-publish`。真实调用必须同时满足：

1. 用户刚刚发出了上面的明确发布指令；
2. 操作者显式传入 `--execute-real`；
3. 传入现有猎聘 Skills 根目录；
4. 草稿仍在有效期内、payload hash 未变化且尚未发布。

本次交付没有运行浏览器、没有发布职位、没有启动 Sourcing/回复，也没有修改猎聘 Skills。

## 对猎聘 Skills 的只读改进建议

以下是建议，不属于本次修改：

1. 为发布器增加 `validate-only` / dry-run、结构化结果 JSON 和非零失败退出码。
2. 把批准令牌作为发布器代码层必填项，而不仅是 Skill 提示词约束。
3. 发布器应保留并传递完整 criteria，避免内置 pipeline handoff 丢字段。
4. 以 `ejob_id` 建唯一约束和 upsert，并持久化外部职位 ID 与 sourcing 内部任务 ID 的映射。
5. full-pipeline 不应把 `paused_auth_required`、recoverable failure 或 timeout 归类为 completed。
6. 自动回复的高风险 LLM 超时路径应 fail-closed，不能推导为真实“不感兴趣”动作。
7. 服务器上的猎聘目录应恢复 GitHub 可追踪部署，并把 crontab 明文凭证迁移到权限受控的环境文件。

## 本地验收

```bash
python -m pytest -q
python -m ruff check .
python -m compileall -q src scripts
git diff --check
```

安全端到端：

```bash
python scripts/generate_talent_pool_drafts.py \
  --direction 具身智能 \
  --run-date 2026-07-26 \
  --report /path/to/current-report.json \
  --state-db /tmp/talent-pool.sqlite \
  --output-dir /tmp/talent-pool

python scripts/talent_pool_control.py \
  --command "发布 1,3,5" \
  --actor local-acceptance \
  --direction 具身智能 \
  --run-date 2026-07-26 \
  --state-db /tmp/talent-pool.sqlite \
  --fake-publish
```

该端到端只产生 `fake-*` 职位 ID，不接触猎聘账号。
