import os
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    PROJECT_ROOT / "scripts" / "run_daily_fixed_sources.sh",
    PROJECT_ROOT / "scripts" / "run_weekly_backup.sh",
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
    assert '--backup-dir "$APP_DIR/backups"' in script
    for filename in (
        "fixed-sources.sqlite",
        "facts.sqlite",
        "runtime.sqlite",
        "relationships.sqlite",
        "search-budget.sqlite",
        "feishu-projection.sqlite",
        "audit.sqlite",
        "ops-metrics.sqlite",
    ):
        assert f'"$APP_DIR/data/{filename}"' in script
