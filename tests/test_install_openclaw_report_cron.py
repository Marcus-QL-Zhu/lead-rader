import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "install_openclaw_report_cron.py"
spec = importlib.util.spec_from_file_location("install_openclaw_report_cron", SCRIPT)
assert spec and spec.loader
cron = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cron)


def test_reconcile_cron_runs_exactly_twice_and_targets_main_session():
    command = cron.desired_command("openclaw")
    assert command[:3] == ["openclaw", "cron", "add"]
    assert command[command.index("--cron") + 1] == "50 5,6 * * *"
    assert command[command.index("--tz") + 1] == "Asia/Shanghai"
    assert command[command.index("--session") + 1] == "main"
    assert command[command.index("--wake") + 1] == "now"
    assert "heartbeat" not in " ".join(command).lower()
    assert "--enable" not in command
    event = command[command.index("--system-event") + 1]
    assert "show-pending" in event
    assert "/home/admin/.pyenv/versions/3.11.14/bin/python3" in event
    assert "never an approval" in event


def test_existing_named_job_is_edited_not_duplicated():
    command = cron.desired_command("openclaw", "job-123")
    assert command[:4] == ["openclaw", "cron", "edit", "job-123"]
    assert "--enable" in command
