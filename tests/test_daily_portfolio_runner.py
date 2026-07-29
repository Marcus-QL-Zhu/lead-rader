import importlib.util
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "run_daily_hardtech_portfolio.py"
spec = importlib.util.spec_from_file_location("run_daily_hardtech_portfolio", SCRIPT)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def _report(direction: str) -> dict:
    return {
        "schema_version": 2,
        "manifest": {
            "as_of": date.today().isoformat(),
            "direction": direction,
            "run_id": f"run-{direction}",
            "generated_at": "2026-07-29T05:00:00+08:00",
            "source_summary": {},
        },
        "leads": [
            {
                "company": f"{direction}公司",
                "direction": direction,
                "score": 60,
                "evidence": [],
            }
        ],
    }


def test_child_scans_skip_feishu_and_combined_portfolio_projects_once(
    tmp_path, monkeypatch
):
    commands = []
    projection_calls = []

    class Completed:
        returncode = 0

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda command, check: commands.append(command) or Completed(),
    )
    monkeypatch.setattr(
        runner,
        "find_report",
        lambda _directory, *, run_date, direction: (
            tmp_path / f"{direction}.json",
            _report(direction),
        ),
    )
    monkeypatch.setattr(
        runner,
        "sync_portfolio_projection",
        lambda portfolio, args: projection_calls.append(portfolio)
        or {"mode": "dry_run", "change_count": 1},
    )
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    code = runner.main(
        [
            "--directions",
            "具身智能|半导体",
            "--run-date",
            date.today().isoformat(),
            "--output-dir",
            str(tmp_path),
            "--env-file",
            str(env_file),
            "--josint-db",
            str(tmp_path / "josint.sqlite"),
        ]
    )

    assert code == 0
    assert len(commands) == 2
    assert all("--skip-feishu-projection" in command for command in commands)
    assert len(projection_calls) == 1
