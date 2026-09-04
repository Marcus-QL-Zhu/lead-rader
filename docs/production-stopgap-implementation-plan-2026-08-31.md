# Production stopgap implementation plan (2026-08-31)

## Boundary

Branch: `hotfix/production-baseline-20260831`

Base: GitHub `origin/main` at
`3e9b07db2e0fc9652d0d2e80b85fb7a3259a916f`

The paused multi-mechanism worktree is out of scope and must remain unchanged.
Work stops after Phase 3 and production verification.

## Phase 1 — security and auditable deployment

1. Add a strict env-file preflight and remove the JOSINT env fallback from the
   Lead Radar launcher and direct notifier.
2. Add versioned secret and cron templates that contain variable names/paths
   only, never values.
3. Add a server deployment script that creates an exact-SHA release, preserves
   state through external directories, verifies HEAD/tree/smokes, and atomically
   selects the release with a rollback pointer.
4. Classify the known server drift file by file. Reimplement only accepted
   behavior locally with tests; do not copy server files wholesale.
5. On the server, back up material SQLite databases and source manifests; create
   protected secret files; remove inline credentials from all affected cron
   entries; rotate the shared Feishu app secret; verify each consumer.

Checkpoint evidence:

- static launcher/env/cron tests;
- deployment dry-run and rejection tests;
- secret scan and permission check;
- reviewed drift decision log;
- recoverable pre-change backup manifest.

## Phase 2 — truthful operations and cooldown

1. Persist a completion snapshot even when draft generation fails or yields zero
   drafts.
   Bound the 05:00 portfolio stage at 1,800 wall-clock seconds and the draft
   stage at 600 wall-clock seconds; watchdog expiry must persist that snapshot
   before the 05:50 reconciliation window.
2. Store independent analysis, draft, delivery, and health statuses; make the
   OpenClaw bridge render them.
3. Always attempt the completion hook after completed/partial analysis; record
   direct Feishu fallback in a shared delivery ledger.
4. Flatten source-pack results into per-adapter metrics. Preserve `partial` and
   `error`, add nullable stage counters, dead letters, and bounded error classes.
5. Copy critical health issues into the completion snapshot and OpenClaw report.
6. Move cooldown before final Top 20 publication. Score an oversupplied pool,
   suppress recently delivered unchanged companies, then keep at most 20 in
   score order without reintroducing suppressed companies.
7. Remove duplicate cooldown application from draft generation.

Checkpoint evidence:

- unit migrations are backward-compatible with production SQLite data;
- zero-draft/fatal-draft and hook/fallback integration tests;
- per-adapter partial/error/critical tests;
- 30-to-20 cooldown replay, same-day idempotency, and delivery-channel tests.

## Phase 3 — frozen 14-day replay

1. Implement a read-only exporter and validator for sanitized production
   reports and operational databases.
2. Export 2026-08-18 through 2026-08-31 without raw database copies.
3. Mark unavailable legacy fields as null and `pre-hotfix`; never infer them.
4. Generate canonical per-day JSON plus a content-addressed manifest.
5. Validate hashes, schema, forbidden keys/values, day coverage, evidence
   references, and offline replay determinism.
6. Commit only the explicitly sanitized fixture set and its manifest.

Checkpoint evidence:

- exactly fourteen daily fixture files;
- validator and credential/PII scan pass;
- offline replay and cooldown acceptance tests pass without network/LLM access.

## Review, GitHub, and production gate

1. Run the entire test suite, Ruff, compileall, `git diff --check`, and staged
   secret/runtime-artifact scans.
2. Give the whole change to an independent Terra sub-agent for adversarial code
   review and fix all actionable findings.
3. Commit and push the hotfix branch, open/merge it to GitHub `main`, and wait
   for GitHub Actions on the exact resulting SHA.
4. Deploy only that exact green SHA with the Phase 1 deployment mechanism.
5. Before activation, require the deploy command to create and independently
   verify a private canonical backup manifest. Confirm all nine daily-workflow
   SQLite databases, any additional database discovered under `data/`, and the
   three production source/config manifests are present. The lazily created
   `relationships.sqlite` may be absent, but must be discovered and backed up
   whenever present. Missing required production inputs, hash mismatch, failed
   SQLite integrity, or failed temporary restore blocks activation. The deploy
   and rollback scripts expose no backup bypass.
   `--nonproduction-allow-missing` exists only on the standalone backup CLI for
   isolated tests and must never appear in a production command.
   The deploy command runs this gate automatically. Operators can independently
   re-run it without opening any database content:

       python scripts/run_lead_radar_v2.py verify-backup --manifest /absolute/path/to/manifest.json

   `backup --git-sha SHA` remains the production form. For backward-compatible
   manual use, omitting `--git-sha` is accepted only when the command can safely
   resolve a real 40-hex `HEAD^{commit}` from the current Git checkout; otherwise
   it fails before creating the backup root.

6. Verify the deployed marker, protected secrets, Python 3.11.14, JOSINT adapter,
   report/health/completion records, OpenClaw reconcile, and rollback pointer.
7. Stop. Do not resume Phase 4 (the multi-mechanism redesign) without a new user
   instruction.
