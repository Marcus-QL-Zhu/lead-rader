# 飞书一键批准并由 OpenClaw 发布：实现设计

## 已核实的现状

当前 OpenClaw 飞书插件能发送 `msg_type: interactive` 的交互卡片，也能更新
卡片；但其事件监听器只注册了普通消息、已读和机器人进群事件，没有注册
`card.action.trigger`。因此，现阶段即使日报发出带按钮的卡片，按钮点击也不会
被 OpenClaw 接收。

现有文本指令链路已经具备发布所需的核心安全能力：

```text
飞书明确指令
→ OpenClaw main Agent
→ hardtech-lead-radar Skill
→ talent_pool_control.py
→ SQLite 批准审计与 payload hash 校验
→ 猎聘 Skills 串行发布
```

## 推荐实现

不新增公网回调服务，复用 OpenClaw 飞书 WebSocket 长连接，在飞书插件增加
最薄的 `card.action.trigger` 事件胶水层：

1. Lead Rader 日报由纯文本改成飞书交互卡片。
2. 卡片显示每个具体职位的标题、画像、城市和“查看 JSON”按钮。
3. 卡片底部提供清晰的高风险按钮：
   `批准并发布全部`、`暂不发布`。
4. 按钮 value 只携带不可变批次引用：
   `run_date`、`direction`、`source_run_id`、`batch_hash` 和 action。
5. OpenClaw 飞书插件监听 `card.action.trigger`，读取真实操作者 `open_id`，
   把点击事件转换成内部控制消息，而不是普通自然语言：
   `LEAD_RADER_APPROVAL_V1 {...}`。
6. `main` Agent 按 Lead Rader Skill 调用 `talent_pool_control.py`，并传入
   operator、批次标识和原始 action event ID。
7. 控制脚本再次检查当前批次、所有 payload hash、7 天有效期、已发布状态和
   全局发布锁；任一不一致都 fail-closed。
8. 批准成功后才调用现有猎聘 Skills 串行发布；遇到登录、验证码、风控、
   限流或结果不确定立即停止。
9. OpenClaw 更新原卡片，逐条显示 published / failed / blocked 和职位 ID。

## 为什么不采用按钮 URL

按钮 URL 需要额外公网 HTTP 服务，而且无法像飞书 action event 一样天然获得
可信的操作者 `open_id`；链接还可能被转发。使用现有 WebSocket 的
`card.action.trigger` 能保持用户身份、事件 ID 和飞书应用边界，安全性更高。

## 部署边界

该方案需要一处 OpenClaw 飞书插件胶水层改动。它不需要修改猎聘 Skills，也
不重写猎聘发布、Sourcing 或回复能力。Lead Rader 侧需要增加卡片构建、批次
hash 绑定和 action envelope 校验。

当前本地优化尚未修改服务器 `/opt/openclaw/extensions/feishu`。在正式部署前，
应先为 OpenClaw 插件改动建立可追踪的补丁或独立扩展，并完成：

- 伪造、重复、过期和旧批次 action 测试；
- 非本人点击与 operator 缺失测试；
- 按钮点击只批准、不绕过发布前校验的测试；
- 卡片更新失败不重复发布的测试；
- OpenClaw 重启和飞书事件重投的幂等测试。
