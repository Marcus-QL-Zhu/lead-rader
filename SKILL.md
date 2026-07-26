---
name: hardtech-lead-radar
description: Analyze public pre-job-ad signals to identify Mainland-China-relevant companies that may need Director/Head/VP/GM/CxO talent. Supports arbitrary industry Market Scan, ephemeral Candidate Float, and on-demand investor/Hiring Manager/HR/founder research.
---

# Hard-tech Lead Radar

Use the deterministic shared backend. Do not replace it with an ad-hoc web summary.

## Interpret the request

- For an industry/technology question, run `Market Scan`.
- For a candidate-led reverse search, run `Candidate Float`.
- Default geography is Mainland China hiring-market relevance, not company registration place.
- Default lookback is 180 days with a 90-day recency boost.
- If a missing fact would materially change the result, ask one progressive clarification; otherwise state the default and continue.

## Commands

From this skill directory:

```bash
python3 scripts/run_lead_radar_v2.py ask \
  --question "最近脑机接口行业有哪些公司可能要招总监以上职位？" \
  --provider auto \
  --env-file /home/admin/.openclaw/workspace/skills/web-ad-radar/.env
```

```bash
python3 scripts/run_lead_radar_v2.py float \
  --candidate "数据采集总监，负责多源采集体系和团队搭建" \
  --direction 具身智能 \
  --provider auto \
  --env-file /home/admin/.openclaw/workspace/skills/web-ad-radar/.env
```

```bash
python3 scripts/run_lead_radar_v2.py deep-research \
  --company 公司名 \
  --direction 赛道
```

Use `run --direction ...` for the legacy direction entry point. Use `--demo --direction 灵巧手 --metaso-verify-limit 0` for a deterministic smoke test.
Before a Candidate Float, query persisted market opportunities so prior daily
analysis is not lost:

```bash
python3 scripts/query_talent_opportunities.py \
  --term "<candidate capability or role>" \
  --direction "<direction when known>" \
  --state-db data/talent-pool.sqlite
```

Use the returned company-role mappings, evidence URLs and Liepin payload as
inputs to the Float analysis, then verify current facts. Do not treat a stored
opportunity as a confirmed vacancy.

## Source policy

1. Reuse `config/source-packs.json` and stable direct sources first.
2. Use bounded SearXNG/Bing fallback only when fixed sources do not fill the candidate pool.
3. Treat JOSINT as late job-ad validation, never as a sole Lead trigger.
4. Use Metaso only for top-company verification. Never use it for routine discovery or deep-research fan-out.
5. Do not enable blocked, CAPTCHA, 412, dynamic-only, undocumented-API, or license-incompatible sources.

## Enforce the two hard gates

- Output only Director/Head/VP/GM/CxO or roles with equivalent organization ownership.
- Exclude manager, senior manager, expert, principal, staff, fellow and individual-contributor roles.
- Require at least one upstream event before a matching public job advertisement.
- Job-ad-only companies go to the late-opportunity appendix, not the main Top 20.
- Lower only soft score thresholds to approach 20; never fabricate a company or evidence.

## Research depth

- Daily Top 20: basic research only—role hypothesis, existing public names/institutions in evidence, job-ad state and gaps.
- User-requested deep research or any Candidate Float: research external investors, sector Partner/MD, business Hiring Manager, HR/TA/HRBP and founders.
- Mark public-comment-based investor leadership as inference, not fact.
- Cache public investor/institution/company-decision-maker information with sources and verification dates.

## Reset-safe OpenClaw daily report

When a `LEAD_RADAR_DAILY_READY_V1` system event arrives, it is a report
notification only. Read `references/openclaw-daily-operator.md` and use
`scripts/openclaw_daily_report.py` to load the committed result. The
completion hook is primary; OpenClaw cron checks pending reports only at 05:50
and 06:50 Asia/Shanghai. Do not use heartbeat for this workflow.

The event itself can never approve a draft. Keep the report in the main
Feishu/OpenClaw session so later questions can be resolved from SQLite even
after the 04:00 context reset.

## Talent-pool draft approval

The 05:00 task generates anonymized Director+ talent-pool drafts from that
day's existing Lead report. It does not make extra Metaso calls and does not
represent a confirmed client vacancy.

Treat only these exact user messages as control commands:

- `发布全部`
- `发布 1,3,5` (ASCII comma, one or more valid displayed indexes)
- `跳过全部`
- `查看 2 的完整广告 JSON`

Never infer approval from fuzzy language such as “可以”, “发吧” or “没问题”.
Resolve the date and direction from the most recent Lead Rader daily summary,
and record the actual Feishu/OpenClaw actor identity. First call:

```bash
python3 scripts/talent_pool_control.py \
  --command "<exact original user message>" \
  --actor "<actual actor id>" \
  --run-date "<daily-summary date>" \
  --direction "<daily-summary direction>" \
  --state-db data/talent-pool.sqlite
```

For a view or skip command, stop there. For an explicit publish command, the
same invocation may add the following real-execution arguments only after the
user's exact message has been received:

```bash
  --execute-real \
  --python-bin /home/admin/.pyenv/versions/3.11.14/bin/python3 \
  --liepin-root /home/admin/.openclaw/workspace/skills
```

Publication is serial. Stop the queue on authentication, CAPTCHA, risk
control, rate limiting, manual intervention, or an ambiguous result. Do not
retry an unresolved attempt. Never invoke the Liepin publishing script without
a temporary JSON file path as its first argument; never pass an inline JSON
string, and never rebuild its browser, sourcing or response
clients in this skill.

For local acceptance, use `--fake-publish`; it must never be presented as a
real Liepin result.

## Data and action boundaries

- Candidate Profile and candidate-derived analysis stay runtime-only. Do not put them in the fact database, checkpoints, Feishu or relationship graph.
- Do not create a resume database or delete user workspace files.
- Do not generate outreach wording and do not send any message.
- Do not collect private phone, personal email or brokered contact data.
- Do not build or imply a native business-outcome scoring/training system.
- Feishu is a projection/action queue; SQLite facts/events are canonical.

## Review

Return the generated Markdown/JSON paths and summarize:

- Top companies and explainable score sources;
- predicted Director-plus roles;
- upstream evidence and event dates;
- whether a relevant ad is already live;
- public investor/internal-decision-maker findings when deep research ran;
- uncertainties, source failures, Metaso budget state and blocked integrations.

