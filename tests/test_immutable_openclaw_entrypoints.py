from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
ENTRY_POINTS = (
    "openclaw_daily_report.py",
    "talent_pool_control.py",
    "run_lead_radar_v2.py",
    "query_talent_opportunities.py",
)


@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_openclaw_entry_point_does_not_write_bytecode_without_python_b_flag(
    tmp_path: Path,
    entry_point: str,
) -> None:
    release = tmp_path / ("a" * 40)
    shutil.copytree(
        ROOT / "src",
        release / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (release / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts" / entry_point, release / "scripts" / entry_point)
    if entry_point == "openclaw_daily_report.py":
        (release / "references").mkdir()
        shutil.copy2(
            ROOT / "references" / "openclaw-daily-operator.md",
            release / "references" / "openclaw-daily-operator.md",
        )

    environment = os.environ.copy()
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, str(release / "scripts" / entry_point), "--help"],
        cwd=release,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert not list(release.rglob("__pycache__"))
    assert not list(release.rglob("*.pyc"))


def test_skill_python_commands_use_no_bytecode_flag() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "/bin/python3 scripts/" not in skill
    assert skill.count("/bin/python3 -B scripts/") == 6
