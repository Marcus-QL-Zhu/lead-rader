from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_runner_uses_protected_env_without_cross_project_fallback():
    script = (
        PROJECT_ROOT / "scripts" / "run_daily_fixed_sources.sh"
    ).read_text(encoding="utf-8")

    assert (
        'ENV_FILE="${HT_LEAD_ENV_FILE:-/home/admin/.openclaw/secrets/lead-radar.env}"'
        in script
    )
    assert "deployment/exec_with_runtime_env.py" in script
    assert "HT_LEAD_RUNTIME_ENV_LOADED" not in script
    assert "run_daily_fixed_sources_inner.sh" in script
    assert 'ENV_FILE="$JOSINT_DIR/.env"' not in script
    assert '--fallback-env-file "$JOSINT_DIR/.env"' not in script
    assert '--env-file "$ENV_FILE" \\\n  --josint-db' not in script
