# Lead Rader repository instructions

## Three environments

This project exists in three places:

- GitHub canonical repository: `https://github.com/Marcus-QL-Zhu/lead-rader`
- Local development checkout: `C:\Users\wande\Documents\Codex_workspace\hardtech-lead-generator`
- Production server deployment: `admin@139.224.164.156:/home/admin/.openclaw/workspace/skills/hardtech-lead-radar`

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
7. Record the deployed commit SHA on the server in `.deployed_git_sha`, then run
   production smoke checks.

Never treat an unpushed local commit or an ad-hoc server edit as the canonical
version. Emergency server fixes must be reproduced in the local checkout,
reviewed, pushed to GitHub, and redeployed promptly.

## Deployment boundaries

Source deployments may replace files tracked by GitHub. They must preserve
server runtime state and secrets, including:

- `.env` and other credential files
- `data/`
- `logs/`
- generated `reports*/`
- `backups/`

Do not commit credentials, database files, generated reports, logs, or runtime
state to GitHub. Back up source and material databases before a production
deployment.

The production daily task is:

- cron time: 05:00 Asia/Shanghai
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
- run a Python version preflight;
- run a JOSINT adapter smoke test;
- manually run the daily launcher when operationally safe;
- inspect the generated report, health output, and exit status.
