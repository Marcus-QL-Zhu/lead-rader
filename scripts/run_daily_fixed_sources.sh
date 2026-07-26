#!/bin/sh
set -u

umask 077

APP_DIR="${HT_LEAD_APP_DIR:-/home/admin/.openclaw/workspace/skills/hardtech-lead-radar}"
JOSINT_DIR="${HT_LEAD_JOSINT_DIR:-/home/admin/.openclaw/workspace/skills/web-ad-radar}"
ENV_FILE="${HT_LEAD_ENV_FILE:-$APP_DIR/.env}"
SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"
if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x "$SERVER_PYTHON" ]; then
    PYTHON_BIN="$SERVER_PYTHON"
  else
    PYTHON_BIN="python3"
  fi
fi
OPENCLAW_BIN="${OPENCLAW_BIN:-/home/admin/.local/share/pnpm/openclaw}"
export OPENCLAW_BIN
DAILY_DIRECTION="${HT_LEAD_DAILY_DIRECTION:-具身智能}"

case "$APP_DIR" in
  /*/hardtech-lead-radar) ;;
  *)
    echo "HT_LEAD_APP_DIR must be an absolute hardtech-lead-radar directory" >&2
    exit 64
    ;;
esac

cd "$APP_DIR" || exit 1

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Lead Rader requires Python >= 3.10; got: $("$PYTHON_BIN" --version 2>&1)" >&2
  exit 69
}

if [ ! -f "$ENV_FILE" ]; then
  ENV_FILE="$JOSINT_DIR/.env"
fi

case "$ENV_FILE" in
  /*) ;;
  *) echo "HT_LEAD_ENV_FILE must resolve to an absolute path" >&2; exit 64 ;;
esac
mkdir -p data reports-daily logs backups

set -- run \
  --direction "$DAILY_DIRECTION" \
  --provider fixed \
  --fixed-sources config/fixed-sources.json \
  --source-packs config/source-packs.json \
  --source-state-db data/fixed-sources.sqlite \
  --fact-db data/facts.sqlite \
  --runtime-db data/runtime.sqlite \
  --relationship-db data/relationships.sqlite \
  --budget-db data/search-budget.sqlite \
  --feishu-state-db data/feishu-projection.sqlite \
  --audit-db data/audit.sqlite \
  --ops-metrics-db data/ops-metrics.sqlite \
  --env-file "$ENV_FILE" \
  --josint-db "$JOSINT_DIR/data/jobs.sqlite" \
  --output-dir reports-daily \
  --minimum-score 0 \
  --top 20 \
  --metaso-verify-limit 3 \
  --metaso-daily-point-budget 30 \
  --metaso-provider-daily-limit 500

if [ -f config/suppressions.json ]; then
  set -- "$@" --suppressions config/suppressions.json
fi

"$PYTHON_BIN" scripts/run_lead_radar_v2.py "$@"

status=$?
talent_draft_status=0
if [ "$status" -eq 0 ] || [ "$status" -eq 2 ]; then
  "$PYTHON_BIN" scripts/generate_talent_pool_drafts.py \
    --direction "$DAILY_DIRECTION" \
    --generator openclaw \
    --report-dir reports-daily \
    --output-dir reports-daily/talent-pool \
    --state-db data/talent-pool.sqlite \
    || talent_draft_status=$?
fi

"$PYTHON_BIN" scripts/run_lead_radar_v2.py monitor \
  --runtime-db data/runtime.sqlite \
  --source-health-db data/fixed-sources.sqlite \
  --ops-metrics-db data/ops-metrics.sqlite \
  --budget-db data/search-budget.sqlite \
  > reports-daily/health-latest.json 2>&1 || true

notification_status=0
"$PYTHON_BIN" scripts/send_daily_feishu_summary.py \
  --direction "$DAILY_DIRECTION" \
  --task-exit-code "$status" \
  --report-dir reports-daily \
  --state-db data/feishu-notifications.sqlite \
  --env-file "$ENV_FILE" \
  --fallback-env-file "$JOSINT_DIR/.env" \
  --talent-state-db data/talent-pool.sqlite \
  --talent-draft-exit-code "$talent_draft_status" \
  || notification_status=$?

if [ "$status" -eq 0 ] || [ "$status" -eq 2 ]; then
  if [ "$notification_status" -ne 0 ]; then
    echo "Lead Rader completed, but Feishu summary notification failed." >&2
    exit "$notification_status"
  fi
  if [ "$talent_draft_status" -ne 0 ]; then
    echo "Lead Rader completed, but talent-pool draft generation failed." >&2
    exit "$talent_draft_status"
  fi
  exit 0
fi
exit "$status"
