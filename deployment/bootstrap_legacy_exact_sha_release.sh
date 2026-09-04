#!/bin/sh
# One-time, recoverable migration from the legacy real directory to exact-SHA releases.
set -eu
umask 077
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE
CANONICAL_REPO="https://github.com/Marcus-QL-Zhu/lead-rader.git"

usage() {
  echo "usage: $0 --sha 40_HEX_SHA --releases-dir ABS --live-path ABS --runtime-dir ABS --env-file ABS --josint-db ABS [--python PYTHON]" >&2
  exit 64
}

SHA=""; RELEASES_DIR=""; LIVE_PATH=""; RUNTIME_DIR=""; ENV_FILE=""; JOSINT_DB=""
PYTHON_BIN="/home/admin/.pyenv/versions/3.11.14/bin/python3"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --sha|--releases-dir|--live-path|--runtime-dir|--env-file|--josint-db|--python)
      [ "$#" -ge 2 ] || usage
      case "$1" in
        --sha) SHA="$2" ;; --releases-dir) RELEASES_DIR="$2" ;;
        --live-path) LIVE_PATH="$2" ;; --runtime-dir) RUNTIME_DIR="$2" ;;
        --env-file) ENV_FILE="$2" ;; --josint-db) JOSINT_DB="$2" ;;
        --python) PYTHON_BIN="$2" ;;
      esac
      shift 2 ;;
    *) usage ;;
  esac
done
case "$SHA" in ????????????????????????????????????????) ;; *) usage ;; esac
case "$SHA" in *[!0123456789abcdef]*) usage ;; esac
for VALUE in "$RELEASES_DIR" "$LIVE_PATH" "$RUNTIME_DIR" "$ENV_FILE" "$JOSINT_DB"; do
  case "$VALUE" in /*) ;; *) usage ;; esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
"$PYTHON_BIN" "$SCRIPT_DIR/verify_github_ci.py" "$SHA"
"$PYTHON_BIN" "$SCRIPT_DIR/validate_runtime_env.py" "$ENV_FILE"

mkdir -p -- "$RELEASES_DIR" "$RUNTIME_DIR"
[ ! -L "$RELEASES_DIR" ] && [ ! -L "$RUNTIME_DIR" ] || { echo "release/runtime root must not be a symlink" >&2; exit 74; }
RELEASES_DIR=$(realpath -e -- "$RELEASES_DIR")
RUNTIME_DIR=$(realpath -e -- "$RUNTIME_DIR")
[ "$RELEASES_DIR" != "/" ] && [ "$RUNTIME_DIR" != "/" ] || exit 64
LIVE_PARENT=$(realpath -e -- "$(dirname -- "$LIVE_PATH")")
LIVE_PATH="$LIVE_PARENT/$(basename -- "$LIVE_PATH")"

# The migration/backup implementation itself must be the requested canonical
# commit, not an ad-hoc server copy.  This is checked before it is trusted to
# certify the legacy backup.
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
[ -d "$PROJECT_ROOT/.git" ] && [ ! -L "$PROJECT_ROOT/.git" ] || { echo "bootstrap tool must be a real Git checkout" >&2; exit 74; }
[ "$(safe_git --git-dir="$PROJECT_ROOT/.git" remote get-url origin)" = "$CANONICAL_REPO" ] || { echo "bootstrap tool origin is not canonical" >&2; exit 74; }
[ "$(safe_git --git-dir="$PROJECT_ROOT/.git" rev-parse HEAD)" = "$SHA" ] || { echo "bootstrap tool does not match requested SHA" >&2; exit 74; }
[ -f "$PROJECT_ROOT/evaluation/production-regression-20260818-31/manifest.json" ] || { echo "bootstrap requires final artifact commit B" >&2; exit 74; }
"$PYTHON_BIN" "$SCRIPT_DIR/verify_release_tree.py" \
  --release-dir "$PROJECT_ROOT" --sha "$SHA" --runtime-dir "$RUNTIME_DIR" \
  || { echo "bootstrap tool checkout failed exact-tree verification" >&2; exit 74; }

# Lock order is identical to normal deploy/rollback: release transaction first,
# then the daily-task inode.  No legacy source/state is inspected before both
# locks are held; opening the known lock pathname is the only legacy access.
command -v flock >/dev/null 2>&1 || { echo "flock is required for bootstrap" >&2; exit 69; }
TRANSACTION_LOCK="$RUNTIME_DIR/.release-transaction.lock"
[ ! -L "$TRANSACTION_LOCK" ] || { echo "release transaction lock must not be a symlink" >&2; exit 74; }
if [ ! -e "$TRANSACTION_LOCK" ]; then (set -C; : > "$TRANSACTION_LOCK") 2>/dev/null || true; fi
[ -f "$TRANSACTION_LOCK" ] && [ ! -L "$TRANSACTION_LOCK" ] || { echo "release transaction lock must be a regular file" >&2; exit 74; }
[ "$(stat -c %h -- "$TRANSACTION_LOCK")" = "1" ] || { echo "release transaction lock must have one hard link" >&2; exit 74; }
chmod 600 -- "$TRANSACTION_LOCK"
exec 9<>"$TRANSACTION_LOCK"
flock -n 9 || { echo "another release transaction is active" >&2; exit 75; }

[ -d "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] || { echo "bootstrap requires a legacy real live directory" >&2; exit 74; }
[ "$(realpath -e -- "$LIVE_PATH")" = "$LIVE_PATH" ] || { echo "legacy live directory escaped its parent" >&2; exit 74; }
[ -d "$LIVE_PATH/data" ] && [ ! -L "$LIVE_PATH/data" ] || { echo "legacy data must be a real directory" >&2; exit 74; }
LEGACY_DAILY_LOCK="$LIVE_PATH/data/daily-task.lock"
[ ! -L "$LEGACY_DAILY_LOCK" ] || { echo "legacy daily task lock must not be a symlink" >&2; exit 74; }
if [ ! -e "$LEGACY_DAILY_LOCK" ]; then (set -C; : > "$LEGACY_DAILY_LOCK") 2>/dev/null || true; fi
[ -f "$LEGACY_DAILY_LOCK" ] && [ ! -L "$LEGACY_DAILY_LOCK" ] || { echo "legacy daily task lock must be a regular file" >&2; exit 74; }
[ "$(stat -c %h -- "$LEGACY_DAILY_LOCK")" = "1" ] || { echo "legacy daily task lock must have one hard link" >&2; exit 74; }
chmod 600 -- "$LEGACY_DAILY_LOCK"
exec 8<>"$LEGACY_DAILY_LOCK"
flock -n 8 || { echo "daily task is active" >&2; exit 75; }

[ ! -e "$RUNTIME_DIR/.previous_release_target" ] && [ ! -L "$RUNTIME_DIR/.previous_release_target" ] || { echo "bootstrap runtime already has release metadata" >&2; exit 74; }
TARGET_RELEASE="$RELEASES_DIR/$SHA"
[ ! -L "$TARGET_RELEASE" ] || { echo "bootstrap target must not be a symlink" >&2; exit 74; }
if [ -e "$TARGET_RELEASE" ]; then
  [ -d "$TARGET_RELEASE" ] && [ "$(realpath -e -- "$TARGET_RELEASE")" = "$TARGET_RELEASE" ] || { echo "bootstrap target escaped release root" >&2; exit 74; }
fi
[ ! -e "$TARGET_RELEASE/.deployed_git_sha" ] && [ ! -L "$TARGET_RELEASE/.deployed_git_sha" ] \
  && [ ! -e "$TARGET_RELEASE/.release-manifest.json" ] && [ ! -L "$TARGET_RELEASE/.release-manifest.json" ] \
  || { echo "bootstrap target already has release metadata" >&2; exit 74; }
for NAME in data logs backups reports-daily; do
  [ ! -e "$RUNTIME_DIR/$NAME" ] && [ ! -L "$RUNTIME_DIR/$NAME" ] || { echo "bootstrap runtime state already exists: $NAME" >&2; exit 74; }
done

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TRANSACTION_DIR="$RUNTIME_DIR/.bootstrap-transaction-$STAMP-$$"
ARCHIVE_ROOT="$RUNTIME_DIR/legacy-source-archives"
ARCHIVE_DIR="$ARCHIVE_ROOT/$STAMP-$SHA"
mkdir -- "$TRANSACTION_DIR"
mkdir -- "$TRANSACTION_DIR/verified-backups"
chmod 700 -- "$TRANSACTION_DIR" "$TRANSACTION_DIR/verified-backups"
[ ! -e "$ARCHIVE_ROOT" ] || { [ -d "$ARCHIVE_ROOT" ] && [ ! -L "$ARCHIVE_ROOT" ] || { echo "legacy archive root is unsafe" >&2; exit 74; }; }
if [ ! -e "$ARCHIVE_ROOT" ]; then mkdir -- "$ARCHIVE_ROOT"; fi
[ ! -e "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ] || { echo "legacy archive target already exists" >&2; exit 74; }

[ -f "$PROJECT_ROOT/scripts/run_lead_radar_v2.py" ] && [ ! -L "$PROJECT_ROOT/scripts/run_lead_radar_v2.py" ] || { echo "bootstrap backup entry point is missing" >&2; exit 74; }
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/run_lead_radar_v2.py" backup \
  --git-sha "$SHA" \
  --backup-dir "$TRANSACTION_DIR/verified-backups" \
  --discover-data-dir "$LIVE_PATH/data" \
  --databases \
    "$LIVE_PATH/data/fixed-sources.sqlite" \
    "$LIVE_PATH/data/facts.sqlite" \
    "$LIVE_PATH/data/runtime.sqlite" \
    "$LIVE_PATH/data/relationships.sqlite" \
    "$LIVE_PATH/data/search-budget.sqlite" \
    "$LIVE_PATH/data/feishu-projection.sqlite" \
    "$LIVE_PATH/data/audit.sqlite" \
    "$LIVE_PATH/data/ops-metrics.sqlite" \
    "$LIVE_PATH/data/talent-pool.sqlite" \
    "$LIVE_PATH/data/feishu-notifications.sqlite" \
  --manifests \
    "$LIVE_PATH/config/fixed-sources.json" \
    "$LIVE_PATH/config/source-packs.json" \
    "$LIVE_PATH/config/openclaw-report-cron.json" \
  || { echo "legacy bootstrap backup gate failed" >&2; exit 74; }
set -- "$TRANSACTION_DIR"/verified-backups/production-predeploy-*
[ "$#" -eq 1 ] && [ -d "$1" ] && [ ! -L "$1" ] || { echo "bootstrap backup set is ambiguous" >&2; exit 74; }
BOOTSTRAP_BACKUP_SET="$1"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/run_lead_radar_v2.py" verify-backup --manifest "$BOOTSTRAP_BACKUP_SET/manifest.json" \
  || { echo "legacy bootstrap backup verification failed" >&2; exit 74; }

MIGRATED_NAMES="$TRANSACTION_DIR/migrated-state-names"
: > "$MIGRATED_NAMES"
chmod 600 -- "$MIGRATED_NAMES"
MIGRATION_STARTED=0
ARCHIVED=0
BOOTSTRAP_COMMITTED=0

restore_legacy() {
  if [ -L "$LIVE_PATH" ]; then
    SELECTED=$(readlink -f -- "$LIVE_PATH" 2>/dev/null || true)
    case "$SELECTED" in "$RELEASES_DIR/$SHA") rm -f -- "$LIVE_PATH" ;; *) return 1 ;; esac
  elif [ -e "$LIVE_PATH" ]; then
    [ -d "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] && [ ! -e "$ARCHIVE_DIR" ] || return 1
  fi
  rm -f -- "$TARGET_RELEASE/.deployed_git_sha" "$TARGET_RELEASE/.release-manifest.json"
  rm -f -- "$RUNTIME_DIR/.previous_release_target"
  if [ -d "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ]; then
    [ ! -e "$LIVE_PATH" ] && [ ! -L "$LIVE_PATH" ] || return 1
    [ -d "$ARCHIVE_DIR" ] && [ ! -L "$ARCHIVE_DIR" ] || return 1
    mv -T -- "$ARCHIVE_DIR" "$LIVE_PATH" || return 1
    ARCHIVED=0
  fi
  if [ "$MIGRATION_STARTED" -eq 1 ]; then
    while IFS= read -r NAME; do
      [ -n "$NAME" ] || continue
      case "$NAME" in data|logs|backups|reports*) ;; *) return 1 ;; esac
      if [ -d "$RUNTIME_DIR/$NAME" ] && [ ! -L "$RUNTIME_DIR/$NAME" ]; then
        [ ! -e "$LIVE_PATH/$NAME" ] && [ ! -L "$LIVE_PATH/$NAME" ] || return 1
        mv -T -- "$RUNTIME_DIR/$NAME" "$LIVE_PATH/$NAME" || return 1
      else
        [ -d "$LIVE_PATH/$NAME" ] && [ ! -L "$LIVE_PATH/$NAME" ] || return 1
      fi
    done < "$MIGRATED_NAMES"
  fi
}

bootstrap_exit() {
  CODE="$1"
  trap - EXIT HUP INT TERM
  if [ "$BOOTSTRAP_COMMITTED" -eq 0 ] && [ "$MIGRATION_STARTED" -eq 1 ]; then
    restore_legacy || { echo "CRITICAL: bootstrap failed and legacy layout could not be restored" >&2; exit 75; }
  fi
  exit "$CODE"
}
trap 'bootstrap_exit $?' EXIT
trap 'bootstrap_exit 129' HUP
trap 'bootstrap_exit 130' INT
trap 'bootstrap_exit 143' TERM

for SOURCE in "$LIVE_PATH/data" "$LIVE_PATH/logs" "$LIVE_PATH/backups" "$LIVE_PATH"/reports*; do
  [ -e "$SOURCE" ] || continue
  [ -d "$SOURCE" ] && [ ! -L "$SOURCE" ] || { echo "legacy state must be a real directory" >&2; exit 74; }
  NAME=$(basename -- "$SOURCE")
  case "$NAME" in data|logs|backups|reports*) ;; *) echo "unexpected legacy state name" >&2; exit 74 ;; esac
  case "$NAME" in *[!A-Za-z0-9._-]*) echo "legacy state name is not portable" >&2; exit 74 ;; esac
  [ ! -e "$RUNTIME_DIR/$NAME" ] && [ ! -L "$RUNTIME_DIR/$NAME" ] || { echo "runtime state collision: $NAME" >&2; exit 74; }
  printf '%s\n' "$NAME" >> "$MIGRATED_NAMES"
  MIGRATION_STARTED=1
  mv -T -- "$SOURCE" "$RUNTIME_DIR/$NAME"
done
for REQUIRED in data logs backups reports-daily; do
  [ -d "$RUNTIME_DIR/$REQUIRED" ] && [ ! -L "$RUNTIME_DIR/$REQUIRED" ] || { echo "legacy state is missing required directory: $REQUIRED" >&2; exit 74; }
done

ARCHIVED=1
mv -T -- "$LIVE_PATH" "$ARCHIVE_DIR"
BACKUP_DEST="$RUNTIME_DIR/backups/$(basename -- "$BOOTSTRAP_BACKUP_SET")"
[ ! -e "$BACKUP_DEST" ] && [ ! -L "$BACKUP_DEST" ] || { echo "bootstrap backup destination collision" >&2; exit 74; }
mv -T -- "$BOOTSTRAP_BACKUP_SET" "$BACKUP_DEST"
"$PYTHON_BIN" "$PROJECT_ROOT/scripts/run_lead_radar_v2.py" verify-backup --manifest "$BACKUP_DEST/manifest.json" \
  || { echo "moved bootstrap backup failed verification" >&2; exit 74; }

HT_RELEASE_LOCKS_HELD=1
export HT_RELEASE_LOCKS_HELD
/bin/sh "$SCRIPT_DIR/deploy_exact_sha_release.sh" \
  --sha "$SHA" --releases-dir "$RELEASES_DIR" --live-link "$LIVE_PATH" \
  --runtime-dir "$RUNTIME_DIR" --env-file "$ENV_FILE" --josint-db "$JOSINT_DB" \
  --python "$PYTHON_BIN"
/bin/sh "$SCRIPT_DIR/verify_exact_sha_release.sh" \
  --expected-sha "$SHA" --releases-dir "$RELEASES_DIR" --live-link "$LIVE_PATH" \
  --runtime-dir "$RUNTIME_DIR" --env-file "$ENV_FILE" --josint-db "$JOSINT_DB" \
  --python "$PYTHON_BIN"

BOOTSTRAP_COMMITTED=1
trap - EXIT HUP INT TERM
printf 'bootstrapped %s; legacy source archived at %s\n' "$SHA" "$ARCHIVE_DIR"
