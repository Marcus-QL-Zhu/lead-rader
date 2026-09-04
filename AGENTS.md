# Lead Rader repository instructions

## Three environments

This project exists in three places:

- GitHub canonical repository: `https://github.com/Marcus-QL-Zhu/lead-rader`
- Local development checkout: `C:\Users\wande\Documents\Codex_workspace\hardtech-lead-generator`
- Production server stable symlink:
  `admin@139.224.164.156:/home/admin/.openclaw/workspace/skills/hardtech-lead-radar`
- Production exact-SHA releases:
  `/home/admin/.openclaw/workspace/skills/hardtech-lead-radar-releases/<sha>`
- Production mutable state:
  `/home/admin/.openclaw/workspace/skills/hardtech-lead-radar-state/`

The GitHub `main` branch is the single source of truth for source code, tracked
configuration, documentation, migrations, and deployment scripts. A local or
server-side file is not authoritative merely because it is newer.

## Required change flow

1. Fetch GitHub and confirm the local branch is not behind before editing.
2. Make and test changes in the local checkout.
3. Before updating GitHub, run an independent sub-agent full code review and
   address all actionable findings.
4. Commit and push the reviewed change to GitHub.
5. Wait for the GitHub Actions workflow for that exact commit to pass.
6. Deploy that exact GitHub commit to the production server.
7. Select the exact-SHA release through the stable symlink. The deployment tool
   writes `.deployed_git_sha` only after post-activation smoke checks succeed.

Never treat an unpushed local commit or an ad-hoc server edit as the canonical
version. Emergency server fixes must be reproduced in the local checkout,
reviewed, pushed to GitHub, and redeployed promptly.

## Deployment boundaries

Source deployments create a new immutable release from the canonical GitHub
repository. They must preserve external server runtime state, including:

- `data/`
- `logs/`
- generated `reports*/`
- `backups/`

Production credentials live only under `/home/admin/.openclaw/secrets/`; that
directory must be owned by the service account with mode `0700`, and each env
file must be a regular, non-symlink file with mode `0600`. Lead Radar's default
file is `/home/admin/.openclaw/secrets/lead-radar.env`. The launcher must load it
through `deployment/exec_with_runtime_env.py` and must never fall back to the
JOSINT project `.env`.

Do not commit credentials, database files, generated reports, logs, or runtime
state to GitHub. Do not put credential values in cron, command arguments, or
deployment output. Back up material databases and source manifests before a
production deployment.

The production daily task is:

- Lead generation cron time: 05:00 Asia/Shanghai
- OpenClaw report reconciliation cron: exactly 05:50 and 06:50 Asia/Shanghai (50 5,6 * * *); no heartbeat
- launcher:
  `/home/admin/.openclaw/workspace/skills/hardtech-lead-radar/scripts/run_daily_fixed_sources.sh`
- supported Python: 3.10 or newer
- preferred server interpreter:
  `/home/admin/.pyenv/versions/3.11.14/bin/python3`
- default Lead Radar LLM override: `minimax/MiniMax-M3` via
  `LEAD_RADAR_LLM_MODEL`; this must not change OpenClaw's global primary model

The launcher must not rely on cron's default `PATH`, because `/usr/bin/python3`
on the server is an unsupported Python 3.6 installation.

## JOSINT dependency

Lead Rader reads the deployed JOSINT database from:

`/home/admin/.openclaw/workspace/skills/web-ad-radar/data/jobs.sqlite`

JOSINT is a separate GitHub project at:

`https://github.com/Marcus-QL-Zhu/JOSINT`

Do not copy JOSINT source into this repository. Keep the integration compatible
with JOSINT's canonical schema and its legacy fallback, and test both paths when
changing the adapter.

## Verification

Before pushing:

- run the full pytest suite;
- run Ruff on the repository;
- run `compileall` on `src` and `scripts`;
- run `git diff --check`;
- scan staged content for credentials and unintended runtime artifacts.

After deploying:

- verify `.deployed_git_sha` equals the GitHub commit deployed;
- verify the live symlink resolves to the exact-SHA release and the release
  tree contains no unexpected tracked, untracked, or ignored payload;
- verify the secrets directory and env-file ownership/modes;
- run a Python version preflight;
- run a JOSINT adapter smoke test;
- manually run the daily launcher when operationally safe;
- inspect the generated report, health output, and exit status.

## Mandatory project lessons and regression guardrails

Before changing source collection, incremental identity, semantic processing,
company/event normalization, demand inference, talent drafts, historical
evaluation, OpenClaw/Feishu delivery, approval, or production runtime, read:

- `docs/project-pitfalls-and-lessons.md` in this repository; and
- the local knowledge copy at
  `C:\Users\wande\Documents\LLM-wiki\wiki\explorations\lead-radar-project-pitfalls-and-lessons.md`
  when it is available.

The repository copy is the engineering source of truth. Keep the LLM-wiki copy,
its `wiki/index.md` entry, and its `manifests/sources.md` entry synchronized when
the lesson set changes.

The following are hard requirements, not optional design suggestions:

- Diagnose failures from the earliest responsible layer: source, stable
  identity, document routing/item scope, entity/action ledger, MiniMax
  adjudication, event clustering, role inference, draft/persistence, then
  delivery/runtime. Do not patch a prompt to hide an upstream defect.
- Do not include relative time labels, list positions, page/cursor values,
  access/check timestamps, discovery timestamps, or run metadata in semantic
  content fingerprints. Every high-value adapter needs a second-fetch
  regression proving dynamic display changes do not trigger a new LLM call.
- Treat relational canonical URL columns as identity authorities. Structured
  URL fields must use URL-aware credential/query sanitization; never run generic
  phone-number redaction over an article URL path. A listing-hash migration may
  rebind cached semantics only after an unchanged detail-body hash and an exact
  prompt/model/contract match are proven.
- Persist each semantic audit and its complete event/alias materialization in
  one transaction. A terminal cache hit requires matching index/body hashes,
  final event count, prompt, model, and claim contract. Legacy cache repair must
  fail closed unless those facts can be reconstructed from consistent rows;
  never invent missing hashes merely to avoid a model call.
- Daily discovery must use broad industry/aggregate sources fetched once as a
  union. Specific company websites are prohibited as daily discovery inputs and
  may only be used later for explicit verification.
- Scrapling is a bounded DOM-relocation fallback only. When adaptive mode is
  disabled, do not pass `auto_save` or open adaptive storage. Never use it to
  bypass authentication, captcha, rate limits, or access controls.
- Production permits at most one Chromium process at a time and defaults to one
  LLM worker. Test-only MiniMax concurrency may be four. HTTP reads, each source,
  collection, LLM batches, and the whole daily run all require wall-clock
  bounds and isolated failure handling.
- MiniMax may adjudicate deterministic claims but may not invent a company,
  event, status, quote, or evidence. It must choose stable entity/claim IDs and
  pass deterministic schema and verbatim-grounding checks. Lead Radar calls the
  configured provider API directly; it does not ask the OpenClaw Agent to do
  semantic analysis or job-ad generation.
- Director+ role hypotheses require an explicit mechanism chain from event or
  network exposure to responsibility/capability bottleneck and organizational
  response. Job advertisements and JOSINT are late validation, not historical
  prediction features.
- The Director+ main evaluation excludes manager, expert, and engineer postings
  as passing labels, even though the broader system may collect them. Historical
  evaluation must be point-in-time, company-grouped, leakage-audited, and keep
  `unknown/right-censored` distinct from negative.
- Daily Top 20 may relax soft score thresholds but never the Director+ and
  upstream-signal hard gates. Use a seven-day company cooldown, with a bypass
  only for materially new evidence. Do not add a negative-signal deduction
  module without a new user decision.
- Talent JSON must comply with the current `liepin-job-posting/SKILL.md`, use
  exactly one city (`上海` when uncertain), persist every human revision before
  approval, include a valid expiry, and never add an unsolicited talent-pool
  disclaimer. Persist the company-signal-role-draft relationship, not candidate
  resumes or derived candidate profiles.
- The OpenClaw/Feishu daily report must show the global candidate-company
  summary and selected/suppressed/failure counts, not only the generated drafts.
  Every draft must remain linked internally to its source company, inferred
  role, evidence, and persisted Liepin JSON while the public ad stays anonymous.
- Natural-language approval is valid when OpenClaw can unambiguously resolve
  the currently displayed drafts; never force the user to repeat an exact CLI
  string or expose a snapshot code. Hook/cron/report delivery is never approval.
  Keep internal draft IDs, payload hashes, delivery state, and publish
  idempotency strict.
- The 05:00 run must persist a readable completion snapshot even on zero drafts,
  partial source health, draft failure, timeout, or SIGTERM. The 05:50 and 06:50
  OpenClaw jobs only reconcile persisted state. A normal same-day replay should
  return `already_reported/no_change`, not a misleading lookup failure. An
  out-of-process watchdog must finalize the exact atomically recorded run ID;
  never sweep or rewrite unrelated or already-terminal runtime rows.
- Never infer server shutdown from an earlier conversation. Shut down only when
  the current user request explicitly authorizes it.
- Use user-scoped GitHub credential storage for cross-session `gh` access. Never
  place a GitHub token in this repository, project env files, prompts, or docs.

When a change fixes a production incident, a frozen-evaluation failure, or a
new product-boundary decision, update the lesson document in the same change.
Record the symptom, responsible layer, failed approach, permanent guardrail,
verification evidence, and any remaining uncertainty.
