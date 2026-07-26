from pathlib import Path


def test_daily_launcher_pins_supported_server_python_and_checks_version():
    script = (
        Path(__file__).parents[1] / "scripts" / "run_daily_fixed_sources.sh"
    ).read_text(encoding="utf-8")

    assert "/home/admin/.pyenv/versions/3.11.14/bin/python3" in script
    assert "sys.version_info >= (3, 10)" in script
    assert 'PYTHON_BIN="${PYTHON_BIN:-python3}"' not in script
    assert "scripts/send_daily_feishu_summary.py" in script
    assert '--task-exit-code "$status"' in script
    assert '--fallback-env-file "$JOSINT_DIR/.env"' in script
    assert "data/feishu-notifications.sqlite" in script
    assert 'exit "$notification_status"' in script
    assert script.index("scripts/send_daily_feishu_summary.py") < script.index(
        'exit "$status"'
    )
