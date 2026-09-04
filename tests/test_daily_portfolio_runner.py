import importlib.util
import json
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
        "leads": [{"company": "示例公司", "score": 60, "evidence": []}],
    }


def test_daily_topics_are_collected_by_one_application_run(tmp_path, monkeypatch):
    commands = []

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
            "--run-id-file",
            str(tmp_path / "active-run-id"),
        ]
    )

    assert code == 0
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("--direction") + 1] == "硬科技组合"
    assert command[command.index("--source-topics") + 1] == "具身智能|半导体"
    assert "--skip-feishu-projection" not in command
    assert command[command.index("--run-id-file") + 1] == str(
        tmp_path / "active-run-id"
    )


def test_daily_refresh_is_forwarded_to_the_single_run(tmp_path, monkeypatch):
    commands = []

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
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")

    assert (
        runner.main(
            [
                "--refresh",
                "--env-file",
                str(env_file),
                "--josint-db",
                str(tmp_path / "josint.sqlite"),
                "--output-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert "--refresh" in commands[0]


def test_valid_zero_company_exit_two_is_normalized_to_completed(tmp_path, monkeypatch, capsys):
    class Completed:
        returncode = 2

    report = _report("硬科技组合")
    report["leads"] = []
    monkeypatch.setattr(runner.subprocess, "run", lambda command, check: Completed())
    monkeypatch.setattr(
        runner,
        "find_report",
        lambda *_args, **_kwargs: (tmp_path / "report.json", report),
    )
    assert runner.main([
        "--run-date", date.today().isoformat(),
        "--output-dir", str(tmp_path),
        "--josint-db", str(tmp_path / "josint.sqlite"),
    ]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert output["company_count"] == 0
