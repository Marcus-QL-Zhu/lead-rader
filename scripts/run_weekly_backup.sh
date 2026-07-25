#!/bin/sh
set -eu

umask 077

APP_DIR="${HT_LEAD_APP_DIR:-/home/admin/.openclaw/workspace/skills/hardtech-lead-radar}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

case "$APP_DIR" in
  /*/hardtech-lead-radar) ;;
  *)
    echo "HT_LEAD_APP_DIR must be an absolute hardtech-lead-radar directory" >&2
    exit 64
    ;;
esac

cd "$APP_DIR"
mkdir -p "$APP_DIR/backups"

"$PYTHON_BIN" scripts/run_lead_radar_v2.py backup \
  --backup-dir "$APP_DIR/backups" \
  --databases \
  "$APP_DIR/data/fixed-sources.sqlite" \
  "$APP_DIR/data/facts.sqlite" \
  "$APP_DIR/data/runtime.sqlite" \
  "$APP_DIR/data/relationships.sqlite" \
  "$APP_DIR/data/search-budget.sqlite" \
  "$APP_DIR/data/feishu-projection.sqlite" \
  "$APP_DIR/data/audit.sqlite" \
  "$APP_DIR/data/ops-metrics.sqlite"
