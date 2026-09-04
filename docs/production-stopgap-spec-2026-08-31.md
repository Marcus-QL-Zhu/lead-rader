# Production stopgap specification (2026-08-31)

## Purpose

This hotfix stabilizes the existing production Lead Radar before the paused
multi-mechanism redesign resumes. It is deliberately limited to three outcomes:

1. remove shared-secret and untraceable-deployment risks;
2. make daily completion, draft generation, delivery, source health, and the
   seven-day company cooldown truthful and independently observable;
3. freeze a sanitized 14-day production replay set for regression testing.

The branch must stop after those outcomes. It must not merge or deploy any file
from `feature/multimechanism-episode-redesign`.

## Production facts being corrected

- The 05:00 analysis completed on each audited day, while talent draft
  generation still returned a non-zero status on every day. One overall status
  currently hides that distinction.
- Six of the audited fourteen notifications used direct Feishu fallback. Those
  deliveries were not made available to OpenClaw and did not enter cooldown
  history.
- The seven-day cooldown is applied only after the daily Top 20 has already been
  selected, so the report can repeat the same companies without replacements.
- Source-pack failures are summarized under a successful umbrella source and
  the operations database records only two aggregate providers.
- A critical health report can coexist with an apparently completed daily run
  without being shown in the OpenClaw summary.
- Production source is not an independently verifiable Git checkout. Its
  deployment marker cannot prove that the files came from the recorded GitHub
  commit.
- Feishu credentials are shared across project-local env files; at least one
  fallback env has overly broad permissions and unrelated crontab entries embed
  credentials directly.

## Security and deployment contract

### Secret boundary

- Production secrets live only under `/home/admin/.openclaw/secrets/`.
- The directory is owned by `admin:admin` and mode `0700`; each env file is
  owned by `admin:admin` and mode `0600`.
- Lead Radar loads exactly the absolute file named by `HT_LEAD_ENV_FILE`, with
  `/home/admin/.openclaw/secrets/lead-radar.env` as the production default.
- Missing, non-regular, symlinked, wrong-owner, group/world-readable, or
  relative env files fail in the one-time credential wrapper before the daily
  pipeline starts.
- Lead Radar must not silently read the JOSINT/web-ad-radar env file.
- Crontab, command arguments, logs, reports, Git history, and deployment output
  must contain no credential value.
- A Feishu app-secret rotation is complete only after every authorized consumer
  has moved to its protected env file and its authentication smoke test passes.

### Exact-SHA deployment

- GitHub `main` remains the only source-code authority.
- A release is checked out at
  `/home/admin/.openclaw/workspace/skills/hardtech-lead-radar-releases/<sha>`.
- Runtime state is outside releases at
  `/home/admin/.openclaw/workspace/skills/hardtech-lead-radar-state/` and contains
  `data`, `logs`, `reports-daily`, and `backups`.
- The stable production path points to the selected release. Each release links
  the four state directories and reads secrets from the protected secret file.
- Deployment refuses a non-40-hex SHA, a checkout whose HEAD differs from the
  requested SHA, any unexpected tracked/untracked/ignored release content, or
  a target SHA whose canonical GitHub Actions run has not succeeded.
- Deployment writes `.deployed_git_sha` only after smoke checks pass. A failed
  deployment leaves the prior release selectable and does not roll back runtime
  state.
- Bootstrap, deploy, and rollback take the same non-blocking transaction lock
  under the external runtime root and then the same daily-task lock, in that
  fixed order, before inspecting or changing the live selector. Both locks
  remain held through activation commit or cleanup, so release work cannot race
  another release transaction or the 05:00 task.
- The first migration from a legacy real live directory creates and verifies a
  backup of the legacy databases and legacy manifests, migrates only mutable
  state, archives the remaining legacy source tree for recovery, and restores
  the legacy layout and selector metadata on every pre-commit failure/signal.
- Every production activation is gated by a fresh verified backup set. The
  default required set contains the nine daily-workflow SQLite databases
  (including `talent-pool.sqlite` and `feishu-notifications.sqlite`). The
  deep-research-only `relationships.sqlite` is created lazily and may be absent;
  when it exists, direct `data/` discovery includes it in the verified backup,
  together with every other additional database. The fixed-source, source-pack,
  and OpenClaw cron configuration manifests are always required. Missing
  required inputs fail closed. Only isolated tests may use the explicitly named
  non-production bypass.
- A backup set is private (`0700` directories and `0600` files) and includes a
  canonical hash manifest recording the source and backup paths, byte sizes,
  SHA-256 values, SQLite integrity result, temporary-restore result, UTC time,
  and deployment commit. Creation and independent validation both recalculate
  every backup hash and restore each SQLite backup into a temporary file for
  `PRAGMA integrity_check`; no database or configuration content is printed.
- Existing server-only source drift is never copied wholesale. Each useful
  behavior must be understood, implemented on this branch, reviewed, tested,
  committed, and deployed by exact SHA.

## Daily operational state contract

### Wall-clock and reconciliation SLA

- The 05:00 portfolio analysis has a fixed 1,800-second outer wall-clock
  deadline, including all ten selected sources and child processes.
- Draft generation has a separate fixed 600-second outer wall-clock deadline.
- Either watchdog expiry persists a zero-draft completion snapshot before
  notification is attempted: portfolio expiry records analysis `failed` and
  draft generation `not_run`; draft expiry preserves completed analysis and
  records draft generation `failed`.
- These bounds leave the completion snapshot available before the first 05:50
  OpenClaw reconciliation, while the completion hook remains the primary
  immediate delivery path and 06:50 remains the second reconciliation attempt.

One daily completion record has these independent fields:

| Field | Allowed values |
| --- | --- |
| `analysis_status` | `completed`, `partial`, `failed`, `not_run` |
| `draft_generation_status` | `complete`, `partial`, `failed`, `not_run` |
| `notification_status` | `pending`, `hook_reported`, `hook_failed`, `hook_failed_fallback_sent`, `fallback_sent`, `fallback_failed`, `not_attempted` |
| `source_health_status` | `healthy`, `warning`, `critical`, `unavailable` |

The fields must never be collapsed into a misleading single success value.
Draft failure may keep a non-zero launcher exit code, but it must not erase a
completed analysis, its report, or the completion record that OpenClaw reads.

### OpenClaw and delivery

- A zero-draft or failed-draft day still creates a current snapshot and a
  pending OpenClaw report with the analysis report reference and bounded error
  class.
- If the main analysis completed or was partial, the completion hook always
  attempts to wake OpenClaw, regardless of draft status.
- Direct Feishu is a delivery fallback, not a replacement data model. A
  successful fallback is recorded in the same delivery ledger and is eligible
  for cooldown history.
- `hook_failed` is the truthful intermediate state after the completion hook
  fails and before a fallback attempt has finished. It is not a confirmed
  delivery and therefore does not enter cooldown history.
- The 05:50 and 06:50 reconcile job can read the newest undelivered completion
  record and must not require a draft to exist.
- OpenClaw receives the four status fields, draft count/error class, all critical
  health issues, and bounded per-source warnings.

### Source observability

Each selected adapter emits, where available:

- source ID and exact status (`ok`, `not_modified`, `partial`, `error`,
  `disabled`, or `unsupported_adapter`);
- listing/discovered and incremental counts;
- detail successes and failures;
- semantic attempts, accepted/prefiltered/failures, and evidence yield;
- open dead-letter count and a bounded error class.

`partial` and `error` may never be recorded as `ok`. Legacy adapters that cannot
provide a field store `null` rather than an invented zero. Critical source
health is copied into the daily completion record and OpenClaw context.

## Seven-day cooldown contract

- Cooldown applies to the final report Top 20, not only to draft generation.
- History is based on confirmed user delivery through either OpenClaw or direct
  Feishu, not merely on an OpenClaw row status.
- A company delivered during the prior seven calendar days is suppressed when
  its evidence URL set is unchanged.
- Materially new evidence bypasses cooldown.
- A company may return after the cooldown window.
- The scorer supplies a larger ordered candidate pool; cooldown is applied and
  the report is then filled to at most 20 from the remaining score order.
- A same-day retry is idempotent and cannot manufacture new evidence or consume
  an extra cooldown day.
- The report persists input, eligible, selected, suppressed, new-evidence, and
  returning counts plus company identities for human audit.

## Sanitized 14-day replay contract

The frozen window is 2026-08-18 through 2026-08-31 (Asia/Shanghai). Each day is
exported as canonical JSON with:

- safe report manifest and source report SHA-256;
- the four independent operational statuses;
- candidate gate counts and selected companies/roles/scores;
- event type/date/source ID or source domain, with evidence URLs hashed by
  default;
- cooldown segments;
- per-source health counters when observed;
- draft count/status/error class and notification channel/status.

The export excludes credentials, headers, raw HTML, LLM prompt/response text,
absolute server paths, resumes, candidates, contacts, people records, phone
numbers, and email addresses. Missing legacy observations are `null` and marked
`capture_version: pre-hotfix`; they are not reconstructed.

`manifest.json` contains the window, schema version, generator Git SHA,
sanitizer policy, per-file byte count and SHA-256, and an overall digest. A
validator recomputes every digest and fails on forbidden keys or values.

## Acceptance conditions

- Full pytest, Ruff, compileall, diff check, and credential scan pass.
- A 30-candidate replay proves suppression, material-evidence bypass,
  post-window return, score-ordered replacement, and a filled Top 20.
- A fatal draft-generation test still creates an OpenClaw-readable completion
  snapshot and preserves `analysis_status=completed` with
  `draft_generation_status=failed`.
- Hook failure followed by Feishu success records a delivered event used by
  cooldown.
- A partial source is never stored as healthy; a critical health issue is visible
  in OpenClaw context.
- All fourteen sanitized daily files validate against their manifest without
  network or LLM calls.
- An independent full code review has no unresolved actionable finding.
- The exact pushed commit has green GitHub Actions, is deployed by exact SHA,
  and passes production secret-permission, Python, JOSINT, report, health,
  OpenClaw, and rollback-smoke checks.
