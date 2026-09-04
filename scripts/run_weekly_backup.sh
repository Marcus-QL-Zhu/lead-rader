#!/bin/sh
set -eu

umask 077

APP_DIR="${HT_LEAD_APP_DIR:-/home/admin/.openclaw/workspace/skills/hardtech-lead-radar}"
PYTHON_BIN="${PYTHON_BIN:-/home/admin/.pyenv/versions/3.11.14/bin/python3}"

case "$APP_DIR" in
  /*/hardtech-lead-radar) ;;
  *)
    echo "HT_LEAD_APP_DIR must be an absolute hardtech-lead-radar directory" >&2
    exit 64
    ;;
esac

cd "$APP_DIR"
mkdir -p "$APP_DIR/backups"
GIT_SHA=$(git -C "$APP_DIR" rev-parse HEAD)
case "$GIT_SHA" in
  ????????????????????????????????????????) ;;
  *) echo "unable to resolve exact deployment commit" >&2; exit 74 ;;
esac
case "$GIT_SHA" in *[!0123456789abcdef]*) echo "invalid deployment commit" >&2; exit 74 ;; esac
DATA_DIR=$(readlink -f -- "$APP_DIR/data")
[ -d "$DATA_DIR" ] && [ ! -L "$DATA_DIR" ] || { echo "runtime data directory is invalid" >&2; exit 74; }

"$PYTHON_BIN" scripts/run_lead_radar_v2.py backup \
  --git-sha "$GIT_SHA" \
  --backup-dir "$APP_DIR/backups" \
  --discover-data-dir "$DATA_DIR" \
  --databases \
  "$APP_DIR/data/fixed-sources.sqlite" \
  "$APP_DIR/data/facts.sqlite" \
  "$APP_DIR/data/runtime.sqlite" \
  "$APP_DIR/data/search-budget.sqlite" \
  "$APP_DIR/data/feishu-projection.sqlite" \
  "$APP_DIR/data/audit.sqlite" \
  "$APP_DIR/data/ops-metrics.sqlite" \
  "$APP_DIR/data/talent-pool.sqlite" \
  "$APP_DIR/data/feishu-notifications.sqlite" \
  --manifests \
  "$APP_DIR/config/fixed-sources.json" \
  "$APP_DIR/config/source-packs.json" \
  "$APP_DIR/config/openclaw-report-cron.json"
