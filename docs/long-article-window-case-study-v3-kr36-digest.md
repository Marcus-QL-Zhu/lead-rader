# 长文窗口与路由验收：36Kr 真实聚合文章

## 样本

- source：`36kr-financing-flash`
- article：`3916547493965442`
- 原始详情：`.acceptance/aggregate-v2/kr36-reference-v4/36kr-financing-flash/detail-3916547493965442.html`
- 清洗正文：2248 字符，包含 13 个 DOM 条目、5 个编辑栏目标题

## 路由结论

这不是一篇单公司长特写，而是一个多公司日报/快报。适配器保留 DOM 的 `h2`、`p` 和 `<strong>` 顺序，形成 13 个不可变条目边界；路由为 `multi_company_bulletin`，语义窗口为 `multi_event_digest`，因此不做 2000 字截断。

条目主体由适配器在条目范围内提取，公开部门、政策和海外市场条目不强行绑定到前一条公司的主体。实体和动作 ledger 以适配器条目边界优先于宽泛媒体标记，避免融资、扩产和量产事件跨条目串线。

## MiniMax 验收

复跑命令使用 `minimax/MiniMax-M3`、claim-centric V27 和真实 DOM 回放：

- `status=accepted`
- `strict_ready=true`
- 12 个候选 claim：7 个接受、5 个拒绝、0 个失败
- 5 个最终事件全部有可回溯证据，主体分别为：智谷天厨融资、月之暗面融资、联电扩产、集创北方芯片量产/技术里程碑
- `validation_issues=[]`、`infrastructure_errors=[]`

## 回归门禁

新增测试验证：月之暗面、联电、集创北方、智谷天厨等主体只在自己的条目内获得动作；“社区开展汽车”和“此项”等政策/句片伪主体不会进入可运营实体或动作主体。长文窗口、36Kr DOM 解析、实体 ledger、动作 ledger、路由和正文 scope 定向测试共 130 个通过。

## 对路由 gate 的启发

在调用 LLM 前必须先完成文档类型判定和条目切分。此样本应进入 `multi_company_bulletin`/`multi_event_digest` 路径，而不是 `single_company_flash` 或普通 `long_feature` 路径；只有单主体长文才继续判定“重大事件扩写”与“采访/评论”，再决定是否保留前 2000 字。
