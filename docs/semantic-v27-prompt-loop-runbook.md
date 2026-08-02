# Semantic V27 Prompt Loop Runbook

本 runbook 只用于本地 opened-development 实验。未通过全部门禁前，不更新生产
Prompt，不同步 GitHub 或服务器。

## 固定输入

- Bundle：`evaluation/semantic-v27/development-v2-bundle.jsonl`
- Gold：`evaluation/semantic-v27/development-v2-gold/adjudication.json`
- Split：`evaluation/semantic-v27/development-v3-split.json`
- MiniMax 凭证：从本机 personal development app 的 `.env` 读取；
  代码直接调用 provider API，不调用 OpenClaw agent。

先运行 split validator，必须 PASS：

```powershell
$env:PYTHONPATH='src;.'
python scripts/validate_semantic_v27_split.py --split evaluation/semantic-v27/development-v3-split.json --bundle evaluation/semantic-v27/development-v2-bundle.jsonl --gold evaluation/semantic-v27/development-v2-gold/adjudication.json --output .acceptance/semantic-v25/v27-development-v3-split-validation.json
```

## 每轮执行

第 1 轮三个候选都以 production base 为父版本；第 2–3 轮都必须通过
`--parent-config` 指向上一轮唯一胜者。

```powershell
python scripts/run_semantic_v27_prompt_loop.py --bundle evaluation/semantic-v27/development-v2-bundle.jsonl --gold evaluation/semantic-v27/development-v2-gold/adjudication.json --split evaluation/semantic-v27/development-v3-split.json --round 1 --variant a --env-file 'C:\Users\wande\Documents\Codex_workspace\personal development app\.env' --output experiments/semantic-v27-loop/round-1/variant-a-prediction.json
```

将 `--variant` 分别改为 `b`、`c`。任何输出如果不是
`status=complete`，或有 failed Claim / infrastructure error，直接淘汰，不生成
winner。

分别评价三个候选。Evaluator 会自动使用 prediction 中冻结的
`selected_keys`，不会把 30 篇 Gold 全部混入该轮：

```powershell
python scripts/evaluate_semantic_v27_development.py --gold evaluation/semantic-v27/development-v2-gold/adjudication.json --prediction experiments/semantic-v27-loop/round-1/variant-a-prediction.json --output experiments/semantic-v27-loop/round-1/variant-a-evaluation.json
```

三个 prediction 和三个 evaluation 齐全后生成匿名包与私有映射：

```powershell
python scripts/build_semantic_v27_round_review.py --gold evaluation/semantic-v27/development-v2-gold/adjudication.json --prediction experiments/semantic-v27-loop/round-1/variant-a-prediction.json --prediction experiments/semantic-v27-loop/round-1/variant-b-prediction.json --prediction experiments/semantic-v27-loop/round-1/variant-c-prediction.json --evaluation experiments/semantic-v27-loop/round-1/variant-a-evaluation.json --evaluation experiments/semantic-v27-loop/round-1/variant-b-evaluation.json --evaluation experiments/semantic-v27-loop/round-1/variant-c-evaluation.json --seed semantic-v27-round-1 --output experiments/semantic-v27-loop/round-1/blind-review-packet.json --mapping-output experiments/semantic-v27-loop/round-1/private-label-mapping.json
```

把匿名包分别交给三个独立子代理。三名评审都按包内 rubric 排序并提供逐 case
证据；主代理 fan-in 后最多选一名胜者。若三版都有硬错误，则该轮无胜者，返回
Entity/Action 宿主层。

胜者完整 `prompt_config` 单独保存为
`experiments/semantic-v27-loop/round-N/winner-config.json`，下一轮通过
`--parent-config` 继承。最多三轮，最多消耗 9 篇训练文章，每篇只属于一轮；如果第 1 或第 2 轮已无改善，可以提前停止。

## Holdout

收敛后先基于最终 Prompt 哈希冻结随机顺序：

```powershell
python scripts/select_semantic_v27_holdout.py --split evaluation/semantic-v27/development-v3-split.json --prompt-config experiments/semantic-v27-loop/final-winner-config.json --output experiments/semantic-v27-loop/holdout-sequence.json
```

将 manifest 中三个 key 按原顺序分别传给 prompt runner 的 `--key`，同时使用
`--resolved-config` 指向最终 Prompt。不得先看结果再换 key。

最后运行：

```powershell
python scripts/evaluate_semantic_v27_holdout_sequence.py --gold evaluation/semantic-v27/development-v2-gold/adjudication.json --prediction experiments/semantic-v27-loop/holdout-prediction.json --manifest experiments/semantic-v27-loop/holdout-sequence.json --output experiments/semantic-v27-loop/holdout-evaluation.json
```

三篇必须各自独立通过，不能用总体平均掩盖某一篇失败。Internal holdout 通过后，
仍需另建时间、公司和近重复隔离的 Final-v3，才能作最终独立验收声明。

## Local completion record (2026-08-02)

- Round 1 completed with the maximum-three-variant contract; variant C was the
  only passing candidate on its round-1 development articles.
- Round 2 was run from the round-1 winner. Variants A/B/C all passed their
  deterministic development gates. Three blind reviewers selected anonymous
  candidate-gamma, privately mapped to variant B.
- The production projection is frozen at
  `experiments/semantic-v27-loop/final-winner-config.json` with prompt hash
  `e38779c3a0deabd680e44032e920e4bf83e89f025c95406e8a4839506d1315db`.
- Final host changes must be included before any holdout rerun. The accepted
  holdout artifact is
  `experiments/semantic-v27-loop/holdout-evaluation.json`: the preselected
  three-case sequence passed 3/3 consecutive cases, with zero failed claims and
  zero unsupported final events. Cyzone exact support was 35/35 (precision 1.00, recall 1.00),
  with no unsupported final events.
- This runbook does not authorize a server or GitHub sync. Fresh ten-adapter
  semantic acceptance and the Director+ historical backtest remain release
  blockers.

## Fresh adapter acceptance note (2026-08-02)

The first fresh ten-source run has a passing citation layer (22/22 exact quote
checks; 10/10 strict-ready articles). Focused post-fix artifacts are retained
locally for 36Kr, 创业邦, 智东西 and 工信部科技司. They cover the identified
subject/status/amount/duplicate-event blockers and were independently reviewed;
the remaining release gate is a full post-fix ten-source semantic run, not more
prompt-loop iterations.
