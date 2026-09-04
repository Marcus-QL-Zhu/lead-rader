import os
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    PROJECT_ROOT / "scripts" / "run_daily_fixed_sources.sh",
    PROJECT_ROOT / "scripts" / "run_weekly_backup.sh",
    PROJECT_ROOT / "deployment" / "deploy_exact_sha_release.sh",
    PROJECT_ROOT / "deployment" / "verify_exact_sha_release.sh",
    PROJECT_ROOT / "deployment" / "rollback_exact_sha_release.sh",
    PROJECT_ROOT / "deployment" / "bootstrap_legacy_exact_sha_release.sh",
    PROJECT_ROOT / "scripts" / "run_daily_fixed_sources_inner.sh",
)


def test_production_shell_scripts_are_lf_posix_shell():
    for script in SCRIPTS:
        payload = script.read_bytes()
        assert payload.startswith(b"#!/bin/sh\n")
        assert b"\r" not in payload
        if os.name != "nt":
            subprocess.run(
                ["/bin/sh", "-n", str(script)],
                check=True,
                capture_output=True,
                text=True,
            )


def test_weekly_backup_uses_absolute_app_paths_for_every_project_database():
    script = SCRIPTS[1].read_text(encoding="utf-8")
    assert '/home/admin/.pyenv/versions/3.11.14/bin/python3' in script
    assert '--backup-dir "$APP_DIR/backups"' in script
    for filename in (
        "fixed-sources.sqlite",
        "facts.sqlite",
        "runtime.sqlite",
        "search-budget.sqlite",
        "feishu-projection.sqlite",
        "audit.sqlite",
        "ops-metrics.sqlite",
        "talent-pool.sqlite",
        "feishu-notifications.sqlite",
    ):
        assert f'"$APP_DIR/data/{filename}"' in script
    for filename in (
        "fixed-sources.json",
        "source-packs.json",
        "openclaw-report-cron.json",
    ):
        assert f'"$APP_DIR/config/{filename}"' in script
    assert '--git-sha "$GIT_SHA"' in script
    assert 'DATA_DIR=$(readlink -f -- "$APP_DIR/data")' in script
    assert '--discover-data-dir "$DATA_DIR"' in script
    # Deep-research state is lazy: discovery captures it when present, while an
    # installation that has only run the daily workflow may omit it.
    assert '"$APP_DIR/data/relationships.sqlite"' not in script


def test_exact_sha_release_scripts_are_auditable_and_never_embed_credentials():
    deploy = (PROJECT_ROOT / "deployment" / "deploy_exact_sha_release.sh").read_text(
        encoding="utf-8"
    )
    verify = (PROJECT_ROOT / "deployment" / "verify_exact_sha_release.sh").read_text(
        encoding="utf-8"
    )
    rollback = (
        PROJECT_ROOT / "deployment" / "rollback_exact_sha_release.sh"
    ).read_text(encoding="utf-8")
    bootstrap = (
        PROJECT_ROOT / "deployment" / "bootstrap_legacy_exact_sha_release.sh"
    ).read_text(encoding="utf-8")

    for script in (deploy, rollback, bootstrap):
        assert '--discover-data-dir "$RUNTIME_DIR/data"' in script or (
            '--discover-data-dir "$LIVE_PATH/data"' in script
        )
        assert "relationships.sqlite" not in script

    assert 'CANONICAL_REPO="https://github.com/Marcus-QL-Zhu/lead-rader.git"' in deploy
    assert 'fetch --depth=1 "$CANONICAL_REPO" "$SHA"' in deploy
    assert 'checkout --detach "$SHA"' in deploy
    assert 'validate_runtime_env.py" "$ENV_FILE"' in deploy
    assert 'verify_github_ci.py" "$SHA"' in deploy
    assert "gh api" not in deploy
    assert "--repo-url" not in deploy
    assert "--ci-proof" not in deploy
    assert 'rm -rf' not in deploy
    assert 'refusing to replace existing non-symlink runtime path' in deploy
    assert 'runtime state child must not be a symlink' in deploy
    assert 'ln -s -- "$STATE_CHILD" "$TARGET"' in deploy
    assert 'mv -Tf -- "$LINK_TMP" "$LIVE_LINK"' in deploy
    assert "smoke_release.py" in deploy
    assert "verify_release_tree.py" in deploy
    assert "--untracked-files=no" not in deploy
    assert "post-activation smoke failed; previous release restored" in deploy
    assert 'TRANSACTION_LOCK="$RUNTIME_DIR/.release-transaction.lock"' in deploy
    assert 'TRANSACTION_LOCK="$RUNTIME_DIR/.release-transaction.lock"' in rollback
    assert "flock -n 9" in deploy
    assert "flock -n 9" in rollback
    assert deploy.index("flock -n 9") < deploy.index('PREVIOUS_TARGET=""')
    assert rollback.index("flock -n 9") < rollback.index(
        'CURRENT=$(readlink -f -- "$LIVE_LINK")'
    )
    assert "production backup gate failed; release was not activated" in deploy
    assert "talent-pool.sqlite" in deploy
    assert "feishu-notifications.sqlite" in deploy
    assert "--nonproduction-skip-backup" not in deploy
    assert 'DAILY_LOCK="$RUNTIME_DIR/data/daily-task.lock"' in deploy
    assert 'DAILY_LOCK="$RUNTIME_DIR/data/daily-task.lock"' in rollback
    assert deploy.index("flock -n 9") < deploy.index("flock -n 8")
    assert rollback.index("flock -n 9") < rollback.index("flock -n 8")
    assert 'BACKUP_MANIFEST_ROOT="$PREVIOUS_TARGET"' in deploy
    assert '"$CURRENT/config/fixed-sources.json"' in rollback
    assert "production rollback backup gate failed" in rollback
    assert "frozen artifact commit B is required" in deploy
    assert 'CANONICAL_REPO="https://github.com/Marcus-QL-Zhu/lead-rader.git"' in bootstrap
    assert bootstrap.index("flock -n 9") < bootstrap.index("flock -n 8")
    assert bootstrap.index("flock -n 8") < bootstrap.index(
        '"$LIVE_PATH/config/fixed-sources.json"'
    )
    assert '"$LIVE_PATH/config/fixed-sources.json"' in bootstrap
    assert '"$LIVE_PATH/config/source-packs.json"' in bootstrap
    assert '"$LIVE_PATH/config/openclaw-report-cron.json"' in bootstrap
    assert 'HT_RELEASE_LOCKS_HELD=1' in bootstrap
    assert 'legacy-source-archives' in bootstrap
    assert 'restore_legacy' in bootstrap
    assert "rm -rf" not in bootstrap
    assert "nonproduction" not in bootstrap
    assert 'previous=%s' in deploy
    assert 'safe_git --git-dir="$RELEASE_DIR/.git" rev-parse HEAD' in verify
    assert 'RELEASE_DIR" = "$RELEASES_DIR/$SHA' in verify
    assert 'safe_git --git-dir="$CHECKOUT/.git" rev-parse HEAD' in rollback
    for payload in (deploy, verify, rollback, bootstrap):
        assert "--untracked-files=no" not in payload
        assert "FEISHU_APP_SECRET=" not in payload
        assert "MINIMAX_API_KEY=" not in payload


def test_versioned_secret_and_cron_templates_contain_paths_not_secret_values():
    cron = (PROJECT_ROOT / "deployment" / "lead-radar.crontab.example").read_text(
        encoding="utf-8"
    )
    env_template = (PROJECT_ROOT / "deployment" / "runtime.env.example").read_text(
        encoding="utf-8"
    )
    secret_helper = (
        PROJECT_ROOT / "deployment" / "prepare_runtime_secret.sh"
    ).read_text(encoding="utf-8")

    assert "HT_LEAD_ENV_FILE=/home/admin/.openclaw/secrets/lead-radar.env" in cron
    assert cron.count("0 5 * * *") == 1
    assert "50 5,6 * * *" not in cron
    assert "FEISHU_APP_SECRET=" not in cron
    assert "MINIMAX_API_KEY=" not in cron
    assert "# FEISHU_APP_SECRET=" in env_template
    assert "# METASO_API_KEY=" in env_template
    assert "MINIMAX_API_KEY" not in env_template
    assert ": > \"$ENV_FILE\"" in secret_helper
    assert "read " not in secret_helper


def test_commit_boundary_disarms_rollback_before_idempotent_temp_cleanup():
    deploy = (PROJECT_ROOT / "deployment" / "deploy_exact_sha_release.sh").read_text(encoding="utf-8")
    rollback = (PROJECT_ROOT / "deployment" / "rollback_exact_sha_release.sh").read_text(encoding="utf-8")
    assert deploy.rfind("ACTIVATION_IN_PROGRESS=0") < deploy.rfind("cleanup_activation_temps")
    assert rollback.rfind("ACTIVATION_IN_PROGRESS=0") < rollback.rfind("cleanup_rollback_temps")


@pytest.mark.skipif(os.name == "nt", reason="requires a POSIX shell")
@pytest.mark.parametrize("sha", ["A" * 40, "g" * 40, "a" * 39, "a" * 41])
def test_deploy_script_rejects_noncanonical_sha_before_any_side_effect(sha):
    completed = subprocess.run(
        [
            "/bin/sh",
            str(PROJECT_ROOT / "deployment" / "deploy_exact_sha_release.sh"),
            "--sha",
            sha,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 64
    assert "sha" in completed.stderr.lower()
