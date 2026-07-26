import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "install_openclaw_report_cron.py"
spec = importlib.util.spec_from_file_location("install_openclaw_report_cron", SCRIPT)
assert spec and spec.loader
cron = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cron)


def test_reconcile_cron_runs_exactly_twice_and_calls_main_bridge():
    command = cron.desired_command("openclaw")
    assert command[:3] == ["openclaw", "cron", "add"]
    assert command[command.index("--cron") + 1] == "50 5,6 * * *"
    assert command[command.index("--tz") + 1] == "Asia/Shanghai"
    assert command[command.index("--session") + 1] == "isolated"
    assert command[command.index("--wake") + 1] == "now"
    assert "--no-deliver" in command
    assert "heartbeat" not in " ".join(command).lower()
    assert "--enable" not in command
    message = command[command.index("--message") + 1]
    assert "openclaw_daily_report.py" in message
    assert "wake --source scheduled-reconcile" in message
    assert "/home/admin/.pyenv/versions/3.11.14/bin/python3" in message
    assert "/home/admin/.local/share/pnpm/openclaw" in message
    assert "current main Feishu session" in message
    command_line = message.split("BEGIN_COMMAND\n", 1)[1].split("\nEND_COMMAND", 1)[0]
    assert command_line.endswith(
        "/home/admin/.openclaw/agents/main/sessions/sessions.json"
    )
    assert not command_line.endswith(".")


def test_existing_named_job_is_edited_not_duplicated():
    command = cron.desired_command("openclaw", "job-123")
    assert command[:4] == ["openclaw", "cron", "edit", "job-123"]
    assert "--enable" in command
