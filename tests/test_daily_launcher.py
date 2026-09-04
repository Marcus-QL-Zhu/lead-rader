from pathlib import Path


def test_daily_launcher_pins_supported_server_python_and_checks_version():
    outer = (
        Path(__file__).parents[1] / "scripts" / "run_daily_fixed_sources.sh"
    ).read_text(encoding="utf-8")
    inner = (
        Path(__file__).parents[1] / "scripts" / "run_daily_fixed_sources_inner.sh"
    ).read_text(encoding="utf-8")
    script = outer + inner

    assert "/home/admin/.pyenv/versions/3.11.14/bin/python3" in script
    assert "sys.version_info >= (3, 10)" in script
    assert "/home/admin/.local/share/pnpm/openclaw" in script
    assert '--openclaw-bin "$OPENCLAW_BIN"' in script
    assert '[ ! -x "$OPENCLAW_BIN" ]' in script
    assert "record-hook-preflight-failure" in script
    assert "OpenClawBinaryUnavailable" in script
    assert 'PYTHON_BIN="${PYTHON_BIN:-python3}"' not in script
    assert "scripts/openclaw_daily_report.py" in script
    assert "scripts/send_daily_feishu_summary.py" in script
    assert '--task-exit-code "$status"' in script
    assert '--fallback-env-file "$JOSINT_DIR/.env"' not in script
    assert "deployment/exec_with_runtime_env.py" in script
    assert "/home/admin/.openclaw/secrets/lead-radar.env" in script
    assert "data/feishu-notifications.sqlite" in script
    assert 'LEAD_RADAR_ADAPTIVE_SELECTORS="${LEAD_RADAR_ADAPTIVE_SELECTORS:-0}"' in script
    assert 'exit "$notification_status"' in script
    assert script.index("scripts/openclaw_daily_report.py") < script.index(
        'exit "$status"'
    )


def test_daily_launcher_persists_health_before_drafts_and_hooks_all_analysis_outcomes():
    script = (
        Path(__file__).parents[1] / "scripts" / "run_daily_fixed_sources_inner.sh"
    ).read_text(encoding="utf-8")

    monitor = script.index("run_lead_radar_v2.py monitor")
    generator = script.index("scripts/generate_talent_pool_drafts.py")
    hook = script.index("scripts/openclaw_daily_report.py")
    assert monitor < generator < hook
    assert '--analysis-status "$analysis_status"' in script
    assert 'analysis_status="partial"' not in script
    assert "--health-report reports-daily/health-latest.json" in script
    assert "PORTFOLIO_WALLCLOCK_SECONDS=1800" in script
    assert "DRAFT_WALLCLOCK_SECONDS=600" in script
    assert "FALLBACK_WALLCLOCK_SECONDS=60" in script
    assert "FALLBACK_KILL_GRACE_SECONDS=5" in script
    assert "--record-fallback-failure FeishuFallbackWallClockTimeout" in script
    assert "--record-analysis-failure" in script
    assert "finalize-interrupted-run" in script
    assert "--run-id-file data/daily-active-run-id" in script
    assert "--record-draft-failure" in script
    assert '--talent-completion-ready "$completion_ready"' in script
    assert "--require-report" in script

    hook_condition = 'if [ "$completion_ready" -eq 1 ]; then'
    hook_start = script.rfind(hook_condition, 0, hook)
    hook_block = script[hook_start : script.index("fi\n\nif", hook)]
    assert "talent_draft_status" not in hook_block
