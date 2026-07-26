# OpenClaw 人才蓄水 JSON 生成

## 算力归属

05:00 生产流程默认使用 OpenClaw `main` Agent：

```bash
openclaw agent --agent main --session-id <deterministic-uuid> \
  --message <structured-prompt> --thinking medium --json
```

该命令经 OpenClaw Gateway 使用 `main` Agent 当前配置的主模型与凭证。
Lead Rader 不复制、不保存上游模型 API Key，也不把模板当作生产默认算力。

## 生成与验证边界

Lead Rader 先用确定性规则把当日 Lead 聚合为 3–10 个安全种子。OpenClaw
负责生成人才画像、职能、吸引角度、Director+ 标题、why-now 和完整猎聘
payload 文案。

模型输出不能直接落库，必须依次通过以下确定性闸门：

- 返回数量和 ordinal 与种子一一对应；
- 标题为总监级以上；
- `public_payload` 字段集合、类型和枚举符合现有猎聘 Skills；
- 工龄、薪资区间和城市字段合法；
- 公司、创始人、投资人、产品、客户等内部标识没有泄漏；
- 同批人才画像和公开 payload 不重复。

任何验证失败都 fail-closed：当日日报显示“草稿生成失败”，不读取前一天
草稿，也不会进入猎聘发布链路。

## 模板 fallback

离线模板只保留为显式应急能力：

```bash
python scripts/generate_talent_pool_drafts.py \
  --generator openclaw \
  --allow-template-fallback \
  ...
```

也可以人工指定 `--generator template` 做离线测试。05:00 生产脚本不启用
fallback，避免 API 失败时悄悄把模板结果冒充模型结果。

## 当前生产路径

服务器 OpenClaw 可执行文件：

```text
/home/admin/.local/share/pnpm/openclaw
```

生产启动脚本将该路径写入 `OPENCLAW_BIN`，不依赖 cron 的默认 `PATH`。
本次代码仍只在本地修改；用户验收前不部署服务器、不推送 GitHub。
