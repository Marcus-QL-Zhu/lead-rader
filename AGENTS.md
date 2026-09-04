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
