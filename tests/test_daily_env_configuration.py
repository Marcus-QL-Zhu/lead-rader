from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_runner_prefers_project_env_and_keeps_josint_fallback():
    script = (
        PROJECT_ROOT / "scripts" / "run_daily_fixed_sources.sh"
    ).read_text(encoding="utf-8")

    assert 'ENV_FILE="${HT_LEAD_ENV_FILE:-$APP_DIR/.env}"' in script
    assert 'ENV_FILE="$JOSINT_DIR/.env"' in script
    assert '--env-file "$ENV_FILE"' in script
    assert '--env-file "$JOSINT_DIR/.env"' not in script
