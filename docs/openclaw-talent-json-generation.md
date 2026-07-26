# 复用 OpenClaw 凭证的直接 LLM JSON 生成

## 算力与调用边界

05:00 生产流程读取 OpenClaw `main` 的模型与 Provider 配置，但不调用
OpenClaw Agent。当前服务器解析出的配置是：

```text
provider: minimax
model: MiniMax-M3（Lead Radar 默认覆盖；OpenClaw 全局主模型不变）
base URL: https://api.minimaxi.com/v1
API protocol: OpenAI-compatible Chat Completions
```

实际调用链：

```text
Lead Radar
  → 读取 OpenClaw 主模型和 Provider 配置
  → POST /v1/chat/completions
  → 解析结构化 JSON
  → 执行确定性校验
```

因此不会继承 OpenClaw Agent 的 system prompt、Skills、会话历史或工具层。
Lead Radar 不复制、不保存、不记录 API Key；凭证只在请求生命周期中从
OpenClaw 配置或其环境变量读取。

## 配置来源

```text
/home/admin/.openclaw/openclaw.json
/home/admin/.openclaw/agents/main/agent/models.json
```

生产脚本通过 `OPENCLAW_CONFIG_PATH` 和 `OPENCLAW_MODELS_PATH` 指向上述文件。
每日任务默认设置 `LEAD_RADAR_LLM_MODEL=minimax/MiniMax-M3`；也可以用同一变量显式覆盖或回退模型，但 Provider 仍必须
存在于 OpenClaw 的 `models.json` 中。

## Evidence-bound 分阶段生成

1. Evidence Compiler 为每家公司选择跨事件类型的公开证据并分配稳定 `evidence_id`。
2. MiniMax 每次只分析一家公司，先判断阶段变化和能力缺口，再决定是否形成 0–3 个 Director+ 岗位。
3. 每个岗位必须引用真实 `evidence_id`；证据不足时只返回 `watch_for`，不生成职位。
4. 具体岗位按职能和能力词聚成可复用人才主题，并保留来源岗位 ID。
5. MiniMax 每次只为一个主题生成一条猎聘 JSON；失败时只允许一次主题级修复。

请求使用独立 system/user message，并启用 `reasoning_split=true`。模型输出在持久化
前必须通过 evidence 引用、具体职位标题、主题标题一致性、主题自己的关键词、
单城市、猎聘字段、匿名化及同批去重校验。失败时 fail-closed，不读取旧草稿，
也不会进入猎聘发布链路。

## 模板 fallback

离线模板只保留为显式应急能力：

```bash
python scripts/generate_talent_pool_drafts.py \
  --generator direct-llm \
  --allow-template-fallback \
  ...
```

05:00 生产脚本不启用 fallback，避免 API 失败时把模板结果冒充模型结果。

完整 `provider/model` 会写入人才池 bundle，并显示在飞书每日汇总中，便于审计和回退。
