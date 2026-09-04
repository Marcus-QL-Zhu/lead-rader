#!/bin/sh
# Invoked only by run_daily_fixed_sources.sh after descriptor-based env loading.
set -u
umask 077
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE
PATH=/usr/bin:/bin
export PATH
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT LD_DEBUG GCONV_PATH LOCPATH NLSPATH BASH_ENV ENV \
  PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE PYTHONWARNINGS \
  PYTHONBREAKPOINT PYTHONHASHSEED PYTHONSAFEPATH PYTHONPLATLIBDIR PYTHONEXECUTABLE
SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"
PORTFOLIO_WALLCLOCK_SECONDS=1800
PORTFOLIO_KILL_GRACE_SECONDS=15
DRAFT_WALLCLOCK_SECONDS=600
DRAFT_KILL_GRACE_SECONDS=15
FALLBACK_WALLCLOCK_SECONDS=60
FALLBACK_KILL_GRACE_SECONDS=5
FALLBACK_RECORD_WALLCLOCK_SECONDS=10
SCRIPT_PATH=$(/usr/bin/realpath "$0") || exit 64
APP_DIR=$(/usr/bin/dirname "$(/usr/bin/dirname "$SCRIPT_PATH")") || exit 64
JOSINT_DIR="/home/admin/.openclaw/workspace/skills/web-ad-radar"
PYTHON_BIN="$SERVER_PYTHON"
"$PYTHON_BIN" "$APP_DIR/deployment/consume_runtime_capability.py" || exit 64
unset HT_LEAD_RUNTIME_CAPABILITY_FD
OPENCLAW_CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-/home/admin/.openclaw/openclaw.json}"
OPENCLAW_MODELS_PATH="${OPENCLAW_MODELS_PATH:-/home/admin/.openclaw/agents/main/agent/models.json}"
OPENCLAW_BIN="${OPENCLAW_BIN:-/home/admin/.local/share/pnpm/openclaw}"
LEAD_RADAR_LLM_MODEL="${LEAD_RADAR_LLM_MODEL:-minimax/MiniMax-M3}"
LEAD_RADAR_AGGREGATE_STRICT_CLAIMS="${LEAD_RADAR_AGGREGATE_STRICT_CLAIMS:-1}"
LEAD_RADAR_AGGREGATE_CLAIM_CENTRIC_V27="${LEAD_RADAR_AGGREGATE_CLAIM_CENTRIC_V27:-1}"
LEAD_RADAR_AGGREGATE_SOURCE_TIMEOUT_SECONDS="${LEAD_RADAR_AGGREGATE_SOURCE_TIMEOUT_SECONDS:-900}"
LEAD_RADAR_AGGREGATE_LLM_WORKERS="${LEAD_RADAR_AGGREGATE_LLM_WORKERS:-1}"
LEAD_RADAR_AGGREGATE_REUSE_STALE_SEMANTICS="${LEAD_RADAR_AGGREGATE_REUSE_STALE_SEMANTICS:-1}"
LEAD_RADAR_ADAPTIVE_SELECTORS="${LEAD_RADAR_ADAPTIVE_SELECTORS:-0}"
LEAD_RADAR_TALENT_LLM_TIMEOUT_SECONDS="${LEAD_RADAR_TALENT_LLM_TIMEOUT_SECONDS:-90}"
LEAD_RADAR_TALENT_LLM_MAX_COMPLETION_TOKENS="${LEAD_RADAR_TALENT_LLM_MAX_COMPLETION_TOKENS:-8192}"
LEAD_RADAR_TALENT_LLM_THINKING_MODE="${LEAD_RADAR_TALENT_LLM_THINKING_MODE:-disabled}"
export OPENCLAW_CONFIG_PATH OPENCLAW_MODELS_PATH LEAD_RADAR_LLM_MODEL
export LEAD_RADAR_AGGREGATE_STRICT_CLAIMS LEAD_RADAR_AGGREGATE_CLAIM_CENTRIC_V27
export LEAD_RADAR_AGGREGATE_SOURCE_TIMEOUT_SECONDS LEAD_RADAR_AGGREGATE_LLM_WORKERS
export LEAD_RADAR_AGGREGATE_REUSE_STALE_SEMANTICS LEAD_RADAR_ADAPTIVE_SELECTORS
export LEAD_RADAR_TALENT_LLM_TIMEOUT_SECONDS LEAD_RADAR_TALENT_LLM_MAX_COMPLETION_TOKENS
export LEAD_RADAR_TALENT_LLM_THINKING_MODE
DAILY_DIRECTIONS="${HT_LEAD_DAILY_DIRECTIONS:-具身智能|半导体|商业航天|核聚变|脑机接口}"
DAILY_DIRECTION="${HT_LEAD_DAILY_DIRECTION:-硬科技组合}"

case "$APP_DIR" in
  /home/admin/.openclaw/workspace/skills/hardtech-lead-radar) ;;
  /home/admin/.openclaw/workspace/skills/hardtech-lead-radar-releases/*)
    RELEASE_SHA=${APP_DIR##*/}
    case "$RELEASE_SHA" in
      ????????????????????????????????????????) ;;
      *) exit 64 ;;
    esac
    case "$RELEASE_SHA" in *[!0123456789abcdef]*) exit 64 ;; esac ;;
  *) exit 64 ;;
esac
cd "$APP_DIR" || exit 1
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || exit 69
[ -x /usr/bin/timeout ] || {
  echo "Required wall-clock watchdog is unavailable." >&2
  exit 69
}
mkdir -p data reports-daily logs backups

if command -v flock >/dev/null 2>&1; then
  DAILY_LOCK="data/daily-task.lock"
  [ ! -L "$DAILY_LOCK" ] || { echo "Daily task lock must not be a symlink." >&2; exit 74; }
  if [ ! -e "$DAILY_LOCK" ]; then (set -C; : > "$DAILY_LOCK") 2>/dev/null || true; fi
  [ -f "$DAILY_LOCK" ] && [ ! -L "$DAILY_LOCK" ] || { echo "Daily task lock must be a regular file." >&2; exit 74; }
  [ "$(stat -c %h -- "$DAILY_LOCK")" = "1" ] || { echo "Daily task lock must have one hard link." >&2; exit 74; }
  chmod 600 -- "$DAILY_LOCK"
  exec 9<>"$DAILY_LOCK"
  if ! flock -n 9; then
    echo "Another Lead Rader daily task is already running." >&2
    exit 75
  fi
fi

set -- \
  --directions "$DAILY_DIRECTIONS" \
  --portfolio-direction "$DAILY_DIRECTION" \
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
  --josint-db "$JOSINT_DIR/data/jobs.sqlite" \
  --output-dir reports-daily \
  --target-count 20 \
  --metaso-verify-limit 3 \
  --metaso-daily-point-budget 30 \
  --metaso-provider-daily-limit 500

if [ -f config/suppressions.json ]; then set -- "$@" --suppressions config/suppressions.json; fi

/usr/bin/timeout --signal=TERM \
  --kill-after="${PORTFOLIO_KILL_GRACE_SECONDS}s" \
  "${PORTFOLIO_WALLCLOCK_SECONDS}s" \
  "$PYTHON_BIN" scripts/run_daily_hardtech_portfolio.py "$@"
status=$?
talent_draft_status=0
completion_ready=0
if [ "$status" -eq 0 ] || [ "$status" -eq 2 ]; then
  analysis_status="completed"
  "$PYTHON_BIN" scripts/run_lead_radar_v2.py monitor \
    --runtime-db data/runtime.sqlite \
    --source-health-db data/fixed-sources.sqlite \
    --ops-metrics-db data/ops-metrics.sqlite \
    --budget-db data/search-budget.sqlite \
    > reports-daily/health-latest.json 2>&1 || true
  /usr/bin/timeout --signal=TERM \
    --kill-after="${DRAFT_KILL_GRACE_SECONDS}s" \
    "${DRAFT_WALLCLOCK_SECONDS}s" \
    "$PYTHON_BIN" scripts/generate_talent_pool_drafts.py \
    --direction "$DAILY_DIRECTION" \
    --generator direct-llm \
    --report-dir reports-daily \
    --output-dir reports-daily/talent-pool \
    --state-db data/talent-pool.sqlite \
    --analysis-status "$analysis_status" \
    --health-report reports-daily/health-latest.json
  talent_draft_status=$?
  if [ "$talent_draft_status" -eq 0 ] \
    || [ "$talent_draft_status" -eq 71 ] \
    || [ "$talent_draft_status" -eq 72 ]; then
    # The generator's normal non-zero outcomes persist zero/partial completion.
    # --require-report below remains the final fail-closed check.
    completion_ready=1
  elif [ "$talent_draft_status" -eq 124 ] \
    || [ "$talent_draft_status" -eq 137 ]; then
    talent_draft_status=71
    if "$PYTHON_BIN" scripts/generate_talent_pool_drafts.py \
      --direction "$DAILY_DIRECTION" \
      --report-dir reports-daily \
      --output-dir reports-daily/talent-pool \
      --state-db data/talent-pool.sqlite \
      --analysis-status "$analysis_status" \
      --health-report reports-daily/health-latest.json \
      --record-draft-failure \
      --draft-error-class DraftGenerationWallClockTimeout; then
      completion_ready=1
    fi
  fi
else
  analysis_error_class="PortfolioRunFailed"
  if [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; then
    analysis_error_class="PortfolioWallClockTimeout"
  fi
  "$PYTHON_BIN" scripts/run_lead_radar_v2.py monitor \
    --runtime-db data/runtime.sqlite \
    --source-health-db data/fixed-sources.sqlite \
    --ops-metrics-db data/ops-metrics.sqlite \
    --budget-db data/search-budget.sqlite \
    > reports-daily/health-latest.json 2>&1 || true
  if "$PYTHON_BIN" scripts/generate_talent_pool_drafts.py \
    --direction "$DAILY_DIRECTION" \
    --report-dir reports-daily \
    --output-dir reports-daily/talent-pool \
    --state-db data/talent-pool.sqlite \
    --record-analysis-failure \
    --analysis-error-class "$analysis_error_class"; then
    completion_ready=1
  else
    talent_draft_status=$?
  fi
fi

notification_status=0
openclaw_hook_status=0
if [ "$completion_ready" -eq 1 ]; then
  if [ ! -x "$OPENCLAW_BIN" ]; then
    echo "OpenClaw binary is not executable: $OPENCLAW_BIN" >&2
    if "$PYTHON_BIN" scripts/openclaw_daily_report.py \
      --state-db data/talent-pool.sqlite \
      record-hook-preflight-failure \
      --error-class OpenClawBinaryUnavailable; then
      openclaw_hook_status=69
    else
      openclaw_hook_status=73
    fi
  else
    "$PYTHON_BIN" scripts/openclaw_daily_report.py \
      --state-db data/talent-pool.sqlite \
      wake --source completion-hook --openclaw-bin "$OPENCLAW_BIN" \
      --sessions-file /home/admin/.openclaw/agents/main/sessions/sessions.json \
      --require-report \
      || openclaw_hook_status=$?
  fi
fi

if [ "$openclaw_hook_status" -ne 0 ] || [ "$completion_ready" -ne 1 ]; then
  /usr/bin/timeout --signal=TERM \
    --kill-after="${FALLBACK_KILL_GRACE_SECONDS}s" \
    "${FALLBACK_WALLCLOCK_SECONDS}s" \
    "$PYTHON_BIN" scripts/send_daily_feishu_summary.py \
    --direction "$DAILY_DIRECTION" \
    --task-exit-code "$status" \
    --report-dir reports-daily \
    --state-db data/feishu-notifications.sqlite \
    --talent-state-db data/talent-pool.sqlite \
    --talent-draft-exit-code "$talent_draft_status" \
    --talent-completion-ready "$completion_ready" \
    || notification_status=$?
  if [ "$notification_status" -eq 124 ] || [ "$notification_status" -eq 137 ]; then
    # The notifier cannot update SQLite after the outer watchdog kills it.
    # Record the bounded class in a fresh process and a separate ledger channel;
    # this leaves the existing openclaw_hook failure intact for reconciliation.
    /usr/bin/timeout --signal=TERM --kill-after=2s \
      "${FALLBACK_RECORD_WALLCLOCK_SECONDS}s" \
      "$PYTHON_BIN" scripts/send_daily_feishu_summary.py \
      --direction "$DAILY_DIRECTION" \
      --task-exit-code "$status" \
      --report-dir reports-daily \
      --state-db data/feishu-notifications.sqlite \
      --talent-state-db data/talent-pool.sqlite \
      --talent-draft-exit-code "$talent_draft_status" \
      --talent-completion-ready "$completion_ready" \
      --record-fallback-failure FeishuFallbackWallClockTimeout \
      >/dev/null 2>&1 || true
  fi
fi
if [ "$status" -eq 0 ] || [ "$status" -eq 2 ]; then
  if [ "$notification_status" -ne 0 ]; then
    echo "Lead Rader completed, but its fallback Feishu notification failed." >&2
    exit "$notification_status"
  fi
  if [ "$talent_draft_status" -ne 0 ]; then
    echo "Lead Rader completed, but talent-pool draft generation failed." >&2
    exit "$talent_draft_status"
  fi
  exit 0
fi
exit "$status"
