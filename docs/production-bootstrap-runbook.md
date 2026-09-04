# Legacy production bootstrap runbook

This runbook is only for the one-time conversion of the old
`hardtech-lead-radar` real directory into the exact-SHA release layout. It is
not the normal upgrade path. Normal upgrades use
`deployment/deploy_exact_sha_release.sh`.

## Preconditions

- The final artifact commit B, not the artifact-free source commit A, is green
  in the canonical GitHub repository.
- The protected runtime env passes `deployment/validate_runtime_env.py`.
- The legacy live path is a real directory, not a symlink.
- The legacy `data/` directory contains all material SQLite databases and the
  legacy `config/` directory contains all three source/config manifests.
- No manual daily task or release operation is running. The script also checks
  both locks and fails with exit 75 if either is active.

## Command

Run the bootstrap script from a clean checkout of the final commit B. Supply
the exact 40-hex commit explicitly; never substitute a branch name.

```sh
/bin/sh deployment/bootstrap_legacy_exact_sha_release.sh \
  --sha EXACT_40_HEX_COMMIT_B \
  --releases-dir /home/admin/.openclaw/workspace/skills/hardtech-lead-radar-releases \
  --live-path /home/admin/.openclaw/workspace/skills/hardtech-lead-radar \
  --runtime-dir /home/admin/.openclaw/workspace/skills/hardtech-lead-radar-state \
  --env-file /home/admin/.openclaw/secrets/lead-radar.env \
  --josint-db /home/admin/.openclaw/workspace/skills/web-ad-radar/data/jobs.sqlite \
  --python /home/admin/.pyenv/versions/3.11.14/bin/python3
```

The script takes the release transaction lock and the daily-task lock in that
order, creates and independently verifies a fresh backup of the legacy
databases and manifests, migrates only `data`, `logs`, `backups`, and every
top-level `reports*` directory, archives the remaining legacy source directory,
then invokes the ordinary exact-SHA deploy and verification gates while still
holding both locks.

Any non-zero exit or handled signal before the final verification restores the
legacy real directory, migrated state directories, and release selector
metadata. The verified pre-bootstrap backup is retained. The script never
recursively deletes a caller-supplied path.

## Postconditions

Verify all of the following before restoring cron:

- the live path is a symlink to `...-releases/EXACT_40_HEX_COMMIT_B`;
- `.deployed_git_sha` equals the exact commit;
- `data`, `logs`, `backups`, and `reports-daily` in the release are links to the
  external runtime state;
- the fresh bootstrap and activation backup manifests pass `verify-backup`;
- the archived legacy source directory remains under
  `hardtech-lead-radar-state/legacy-source-archives/`;
- the release smoke reports the supported Python and a read-only JOSINT pass.

Do not remove the legacy archive during this stopgap. Recovery from a later
application-level regression uses the exact-SHA rollback script, which also
takes both locks and creates its own fresh verified backup before switching.
