#!/bin/sh
# Public credential boundary: always load the protected file through one FD,
# then execute the fixed non-recursive run_daily_fixed_sources_inner.sh pipeline.
set -eu
umask 077
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE
PATH=/usr/bin:/bin
export PATH
unset LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT LD_DEBUG GCONV_PATH LOCPATH NLSPATH BASH_ENV ENV \
  PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONUSERBASE PYTHONWARNINGS \
  PYTHONBREAKPOINT PYTHONHASHSEED PYTHONSAFEPATH PYTHONPLATLIBDIR PYTHONEXECUTABLE
SCRIPT_PATH=$(/usr/bin/realpath "$0") || exit 64
APP_DIR=$(/usr/bin/dirname "$(/usr/bin/dirname "$SCRIPT_PATH")") || exit 64
ENV_FILE="${HT_LEAD_ENV_FILE:-/home/admin/.openclaw/secrets/lead-radar.env}"
SERVER_PYTHON="/home/admin/.pyenv/versions/3.11.14/bin/python3"

case "$ENV_FILE" in
  /*) ;;
  *) echo "HT_LEAD_ENV_FILE must resolve to an absolute path" >&2; exit 64 ;;
esac

"$SERVER_PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Lead Rader requires Python >= 3.10" >&2
  exit 69
}
exec "$SERVER_PYTHON" "$APP_DIR/deployment/exec_with_runtime_env.py" --env-file "$ENV_FILE"
