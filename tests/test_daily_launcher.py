from pathlib import Path


def test_daily_launcher_pins_supported_server_python_and_checks_version():
    script = (
        Path(__file__).parents[1] / "scripts" / "run_daily_fixed_sources.sh"
    ).read_text(encoding="utf-8")

    assert "/home/admin/.pyenv/versions/3.11.14/bin/python3" in script
    assert "sys.version_info >= (3, 10)" in script
    assert "/home/admin/.local/share/pnpm/openclaw" in script
    assert '--openclaw-bin "$OPENCLAW_BIN"' in script
    assert '[ ! -x "$OPENCLAW_BIN" ]' in script
    assert 'PYTHON_BIN="${PYTHON_BIN:-python3}"' not in script
    assert "scripts/openclaw_daily_report.py" in script
    assert "scripts/send_daily_feishu_summary.py" in script
    assert '--task-exit-code "$status"' in script
    assert '--fallback-env-file "$JOSINT_DIR/.env"' in script
    assert "data/feishu-notifications.sqlite" in script
    assert 'LEAD_RADAR_ADAPTIVE_SELECTORS="${LEAD_RADAR_ADAPTIVE_SELECTORS:-0}"' in script
    assert 'exit "$notification_status"' in script
    assert script.index("scripts/openclaw_daily_report.py") < script.index(
        'exit "$status"'
    )
