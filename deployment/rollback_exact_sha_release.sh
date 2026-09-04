#!/bin/sh
# Atomically select a previously deployed, fully re-smoked exact-SHA release.
set -eu
umask 077
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

CANONICAL_REPO="https://github.com/Marcus-QL-Zhu/lead-rader.git"
usage() {
  echo "usage: $0 --live-link ABS --releases-dir ABS --runtime-dir ABS --sha 40_HEX_SHA --env-file ABS --josint-db ABS [--python PYTHON]" >&2
  exit 64
}

LIVE_LINK=""; RELEASES_DIR=""; RUNTIME_DIR=""; SHA=""; ENV_FILE=""; JOSINT_DB=""
PYTHON_BIN="/home/admin/.pyenv/versions/3.11.14/bin/python3"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --live-link|--releases-dir|--runtime-dir|--sha|--env-file|--josint-db|--python)
      [ "$#" -ge 2 ] || usage
      case "$1" in
        --live-link) LIVE_LINK="$2" ;; --releases-dir) RELEASES_DIR="$2" ;;
        --runtime-dir) RUNTIME_DIR="$2" ;; --sha) SHA="$2" ;;
        --env-file) ENV_FILE="$2" ;; --josint-db) JOSINT_DB="$2" ;;
        --python) PYTHON_BIN="$2" ;;
      esac
      shift 2 ;;
    *) usage ;;
  esac
done
for VALUE in "$LIVE_LINK" "$RELEASES_DIR" "$RUNTIME_DIR" "$ENV_FILE" "$JOSINT_DB"; do case "$VALUE" in /*) ;; *) usage ;; esac; done
case "$SHA" in ????????????????????????????????????????) ;; *) usage ;; esac
case "$SHA" in *[!0123456789abcdef]*) usage ;; esac
[ ! -L "$RELEASES_DIR" ] && [ ! -L "$RUNTIME_DIR" ] || { echo "release/runtime root must not be a symlink" >&2; exit 74; }
RELEASES_DIR=$(realpath -e -- "$RELEASES_DIR")
RUNTIME_DIR=$(realpath -e -- "$RUNTIME_DIR")
[ "$RELEASES_DIR" != "/" ] && [ "$RUNTIME_DIR" != "/" ] || exit 64

command -v flock >/dev/null 2>&1 || { echo "flock is required for release transactions" >&2; exit 69; }
TRANSACTION_LOCK="$RUNTIME_DIR/.release-transaction.lock"
[ ! -L "$TRANSACTION_LOCK" ] || { echo "release transaction lock must not be a symlink" >&2; exit 74; }
if [ ! -e "$TRANSACTION_LOCK" ]; then
  (set -C; : > "$TRANSACTION_LOCK") 2>/dev/null || true
fi
[ -f "$TRANSACTION_LOCK" ] && [ ! -L "$TRANSACTION_LOCK" ] || { echo "release transaction lock must be a regular file" >&2; exit 74; }
[ "$(stat -c %h -- "$TRANSACTION_LOCK")" = "1" ] || { echo "release transaction lock must have one hard link" >&2; exit 74; }
chmod 600 -- "$TRANSACTION_LOCK"
exec 9<>"$TRANSACTION_LOCK"
if ! flock -n 9; then
  echo "another release transaction is active" >&2
  exit 75
fi

[ -d "$RUNTIME_DIR/data" ] && [ ! -L "$RUNTIME_DIR/data" ] || { echo "runtime data must be a real directory" >&2; exit 74; }
[ "$(realpath -e -- "$RUNTIME_DIR/data")" = "$RUNTIME_DIR/data" ] || { echo "runtime data escaped runtime root" >&2; exit 74; }
DAILY_LOCK="$RUNTIME_DIR/data/daily-task.lock"
[ ! -L "$DAILY_LOCK" ] || { echo "daily task lock must not be a symlink" >&2; exit 74; }
if [ ! -e "$DAILY_LOCK" ]; then (set -C; : > "$DAILY_LOCK") 2>/dev/null || true; fi
[ -f "$DAILY_LOCK" ] && [ ! -L "$DAILY_LOCK" ] || { echo "daily task lock must be a regular file" >&2; exit 74; }
[ "$(stat -c %h -- "$DAILY_LOCK")" = "1" ] || { echo "daily task lock must have one hard link" >&2; exit 74; }
chmod 600 -- "$DAILY_LOCK"
exec 8<>"$DAILY_LOCK"
if ! flock -n 8; then
  echo "daily task is active" >&2
  exit 75
fi

[ -L "$LIVE_LINK" ] || { echo "rollback requires an existing live symlink" >&2; exit 74; }
LIVE_PARENT=$(realpath -e -- "$(dirname -- "$LIVE_LINK")")
LIVE_LINK="$LIVE_PARENT/$(basename -- "$LIVE_LINK")"
TARGET="$RELEASES_DIR/$SHA"
[ ! -L "$TARGET" ] && [ "$(realpath -e -- "$TARGET")" = "$TARGET" ] || { echo "rollback target confinement failed" >&2; exit 74; }
CURRENT=$(readlink -f -- "$LIVE_LINK")
case "$CURRENT" in "$RELEASES_DIR"/*) ;; *) echo "current live release escaped root" >&2; exit 74 ;; esac
CURRENT_SHA=$(basename -- "$CURRENT")
case "$CURRENT_SHA" in ????????????????????????????????????????) ;; *) echo "current release is not exact-SHA" >&2; exit 74 ;; esac
case "$CURRENT_SHA" in *[!0123456789abcdef]*) echo "current release is not exact-SHA" >&2; exit 74 ;; esac
[ "$CURRENT" = "$RELEASES_DIR/$CURRENT_SHA" ] || exit 74

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
safe_git() {
  GIT_NO_REPLACE_OBJECTS=1 git --no-replace-objects -c protocol.file.allow=never -c protocol.ext.allow=never \
    -c http.followRedirects=false -c core.hooksPath=/dev/null \
    -c core.fsmonitor=false "$@"
}
GIT_CONFIG_NOSYSTEM=1; GIT_CONFIG_GLOBAL=/dev/null; GIT_ALLOW_PROTOCOL=https
export GIT_CONFIG_NOSYSTEM GIT_CONFIG_GLOBAL GIT_ALLOW_PROTOCOL
unset GIT_CONFIG GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_DIR GIT_WORK_TREE \
  GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
  GIT_INDEX_FILE GIT_SSH GIT_SSH_COMMAND GIT_PROXY_COMMAND GIT_EXTERNAL_DIFF

verify_deployed_release() {
  CHECKOUT="$1"; CHECKOUT_SHA="$2"
  [ ! -L "$CHECKOUT" ] && [ -d "$CHECKOUT/.git" ] || return 1
  [ "$(safe_git --git-dir="$CHECKOUT/.git" remote get-url origin)" = "$CANONICAL_REPO" ] || return 1
  [ "$(safe_git --git-dir="$CHECKOUT/.git" rev-parse HEAD)" = "$CHECKOUT_SHA" ] || return 1
  [ -f "$CHECKOUT/evaluation/production-regression-20260818-31/manifest.json" ] \
    && [ ! -L "$CHECKOUT/evaluation/production-regression-20260818-31/manifest.json" ] \
    || return 1
  "$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" verify \
    --release-dir "$CHECKOUT" --sha "$CHECKOUT_SHA" --releases-dir "$RELEASES_DIR" || return 1
  "$PYTHON_BIN" "$SCRIPT_DIR/verify_release_tree.py" \
    --release-dir "$CHECKOUT" --sha "$CHECKOUT_SHA" --runtime-dir "$RUNTIME_DIR" || return 1
  "$PYTHON_BIN" "$SCRIPT_DIR/smoke_release.py" \
    --release-dir "$CHECKOUT" --expected-realpath "$CHECKOUT" \
    --josint-db "$JOSINT_DB" --env-file "$ENV_FILE" || return 1
}

"$PYTHON_BIN" "$SCRIPT_DIR/validate_runtime_env.py" "$ENV_FILE"
verify_deployed_release "$CURRENT" "$CURRENT_SHA" || { echo "current release failed pre-rollback validation" >&2; exit 74; }
verify_deployed_release "$TARGET" "$SHA" || { echo "rollback target failed pre-activation validation" >&2; exit 74; }
if [ "$CURRENT" = "$TARGET" ]; then echo "already selected $SHA"; exit 0; fi

[ -f "$CURRENT/scripts/run_lead_radar_v2.py" ] && [ ! -L "$CURRENT/scripts/run_lead_radar_v2.py" ] || { echo "production backup entry point is missing from current release" >&2; exit 74; }
"$PYTHON_BIN" "$CURRENT/scripts/run_lead_radar_v2.py" backup \
  --git-sha "$SHA" \
  --backup-dir "$RUNTIME_DIR/backups" \
  --discover-data-dir "$RUNTIME_DIR/data" \
  --databases \
    "$RUNTIME_DIR/data/fixed-sources.sqlite" \
    "$RUNTIME_DIR/data/facts.sqlite" \
    "$RUNTIME_DIR/data/runtime.sqlite" \
    "$RUNTIME_DIR/data/relationships.sqlite" \
    "$RUNTIME_DIR/data/search-budget.sqlite" \
    "$RUNTIME_DIR/data/feishu-projection.sqlite" \
    "$RUNTIME_DIR/data/audit.sqlite" \
    "$RUNTIME_DIR/data/ops-metrics.sqlite" \
    "$RUNTIME_DIR/data/talent-pool.sqlite" \
    "$RUNTIME_DIR/data/feishu-notifications.sqlite" \
  --manifests \
    "$CURRENT/config/fixed-sources.json" \
    "$CURRENT/config/source-packs.json" \
    "$CURRENT/config/openclaw-report-cron.json" \
  || { echo "production rollback backup gate failed; release was not changed" >&2; exit 74; }

LINK_TMP="$LIVE_PARENT/.$(basename -- "$LIVE_LINK").rollback.$$"
RESTORE_TMP="$LIVE_PARENT/.$(basename -- "$LIVE_LINK").restore.$$"
PREVIOUS_POINTER="$RUNTIME_DIR/.previous_release_target"
POINTER_BACKUP="$RUNTIME_DIR/.previous_release_target.backup.$$"
POINTER_TMP="$RUNTIME_DIR/.previous_release_target.next.$$"
ACTIVATION_IN_PROGRESS=0
POINTER_CHANGED=0
cleanup_rollback_temps() {
  rm -f -- "$LINK_TMP" "$RESTORE_TMP" "$POINTER_TMP" "$POINTER_BACKUP"
}
restore_rollback() {
  mv -Tf -- "$RESTORE_TMP" "$LIVE_LINK" || return 1
  if [ "$POINTER_CHANGED" -eq 1 ]; then
    rm -f -- "$PREVIOUS_POINTER"
    if [ -e "$POINTER_BACKUP" ]; then mv -f -- "$POINTER_BACKUP" "$PREVIOUS_POINTER"; fi
    POINTER_CHANGED=0
  fi
  verify_deployed_release "$CURRENT" "$CURRENT_SHA" || return 1
  cleanup_rollback_temps
  ACTIVATION_IN_PROGRESS=0
}
rollback_exit() {
  CODE="$1"; trap - EXIT HUP INT TERM
  if [ "$ACTIVATION_IN_PROGRESS" -eq 1 ]; then
    restore_rollback || { cleanup_rollback_temps; exit 75; }
  else
    cleanup_rollback_temps
  fi
  exit "$CODE"
}
trap 'rollback_exit $?' EXIT
trap 'rollback_exit 129' HUP
trap 'rollback_exit 130' INT
trap 'rollback_exit 143' TERM

"$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" verify-pointer --pointer "$PREVIOUS_POINTER" --releases-dir "$RELEASES_DIR" || exit 74
if [ -e "$PREVIOUS_POINTER" ]; then cp -p -- "$PREVIOUS_POINTER" "$POINTER_BACKUP"; fi
"$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" write-pointer --pointer "$POINTER_TMP" --release "$CURRENT"
ln -s -- "$TARGET" "$LINK_TMP"
ln -s -- "$CURRENT" "$RESTORE_TMP"
ACTIVATION_IN_PROGRESS=1
if ! mv -Tf -- "$LINK_TMP" "$LIVE_LINK"; then
  ACTIVATION_IN_PROGRESS=0
  rm -f -- "$LINK_TMP" "$RESTORE_TMP"
  echo "rollback activation failed; current release remains selected" >&2
  exit 74
fi
if ! verify_deployed_release "$TARGET" "$SHA"; then
  if ! restore_rollback; then
    echo "CRITICAL: rollback smoke failed and original release could not be restored" >&2
    exit 75
  fi
  echo "rollback post-activation smoke failed; original release restored" >&2
  exit 74
fi
POINTER_CHANGED=1
if ! mv -f -- "$POINTER_TMP" "$PREVIOUS_POINTER" \
  || ! "$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" verify-pointer --pointer "$PREVIOUS_POINTER" --releases-dir "$RELEASES_DIR"; then
  restore_rollback || exit 75
  echo "rollback pointer activation failed; original release restored" >&2
  exit 74
fi
ACTIVATION_IN_PROGRESS=0
cleanup_rollback_temps
trap - EXIT HUP INT TERM
echo "rolled back to $SHA"
