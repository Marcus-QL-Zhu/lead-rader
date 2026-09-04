#!/bin/sh
# Exact-SHA, canonical-origin, CI-gated release activation with atomic rollback.
set -eu
umask 077
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

CANONICAL_REPO="https://github.com/Marcus-QL-Zhu/lead-rader.git"

usage() {
  echo "usage: $0 --sha 40_HEX_SHA --releases-dir ABS --live-link ABS --runtime-dir ABS --env-file ABS --josint-db ABS [--python PYTHON]" >&2
  exit 64
}

SHA=""; RELEASES_DIR=""; LIVE_LINK=""; RUNTIME_DIR=""; ENV_FILE=""; JOSINT_DB=""; PYTHON_BIN="/home/admin/.pyenv/versions/3.11.14/bin/python3"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --sha|--releases-dir|--live-link|--runtime-dir|--env-file|--josint-db|--python)
      [ "$#" -ge 2 ] || usage
      case "$1" in
        --sha) SHA="$2" ;; --releases-dir) RELEASES_DIR="$2" ;;
        --live-link) LIVE_LINK="$2" ;; --runtime-dir) RUNTIME_DIR="$2" ;;
        --env-file) ENV_FILE="$2" ;; --josint-db) JOSINT_DB="$2" ;;
        --python) PYTHON_BIN="$2" ;;
      esac
      shift 2 ;;
    *) usage ;;
  esac
done
case "$SHA" in ????????????????????????????????????????) ;; *) echo "sha must be exactly 40 lowercase hexadecimal characters" >&2; exit 64 ;; esac
case "$SHA" in *[!0123456789abcdef]*) echo "sha must be lowercase hexadecimal" >&2; exit 64 ;; esac
for VALUE in "$RELEASES_DIR" "$LIVE_LINK" "$RUNTIME_DIR" "$ENV_FILE" "$JOSINT_DB"; do
  case "$VALUE" in /*) ;; *) echo "all deployment paths must be absolute" >&2; exit 64 ;; esac
done

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$PYTHON_BIN" "$SCRIPT_DIR/verify_github_ci.py" "$SHA"
"$PYTHON_BIN" "$SCRIPT_DIR/validate_runtime_env.py" "$ENV_FILE"

mkdir -p -- "$RELEASES_DIR" "$RUNTIME_DIR"
[ ! -L "$RELEASES_DIR" ] || { echo "release root must not be a symlink" >&2; exit 74; }
[ ! -L "$RUNTIME_DIR" ] || { echo "runtime root must not be a symlink" >&2; exit 74; }
RELEASES_DIR=$(realpath -e -- "$RELEASES_DIR")
RUNTIME_DIR=$(realpath -e -- "$RUNTIME_DIR")
[ "$RELEASES_DIR" != "/" ] && [ "$RUNTIME_DIR" != "/" ] || { echo "release/runtime root cannot be /" >&2; exit 64; }

# Deploy and rollback share one transaction lock in the external runtime root.
# It is intentionally acquired before the live selector is inspected and the
# descriptor stays open until activation commits or every rollback cleanup has
# completed.  A second operator receives a deterministic, safe refusal rather
# than racing a successful transaction back to its former release.
command -v flock >/dev/null 2>&1 || { echo "flock is required for release transactions" >&2; exit 69; }
TRANSACTION_LOCK="$RUNTIME_DIR/.release-transaction.lock"
[ ! -L "$TRANSACTION_LOCK" ] || { echo "release transaction lock must not be a symlink" >&2; exit 74; }
if [ ! -e "$TRANSACTION_LOCK" ]; then
  (set -C; : > "$TRANSACTION_LOCK") 2>/dev/null || true
fi
[ -f "$TRANSACTION_LOCK" ] && [ ! -L "$TRANSACTION_LOCK" ] || { echo "release transaction lock must be a regular file" >&2; exit 74; }
[ "$(stat -c %h -- "$TRANSACTION_LOCK")" = "1" ] || { echo "release transaction lock must have one hard link" >&2; exit 74; }
chmod 600 -- "$TRANSACTION_LOCK"
if [ "${HT_RELEASE_LOCKS_HELD:-0}" = "1" ]; then
  [ -e "/proc/$$/fd/9" ] && [ "$(readlink -f -- "/proc/$$/fd/9")" = "$TRANSACTION_LOCK" ] || { echo "inherited release lock descriptor is invalid" >&2; exit 74; }
else
  exec 9<>"$TRANSACTION_LOCK"
fi
flock -n 9 || { echo "another release transaction is active" >&2; exit 75; }

# The daily lock lives in external state so the cron launcher and every release
# operation serialize on the same inode.  No runtime data is inspected until
# after the release transaction lock is held.
if [ ! -e "$RUNTIME_DIR/data" ]; then mkdir -- "$RUNTIME_DIR/data"; fi
[ -d "$RUNTIME_DIR/data" ] && [ ! -L "$RUNTIME_DIR/data" ] || { echo "runtime data must be a real directory" >&2; exit 74; }
[ "$(realpath -e -- "$RUNTIME_DIR/data")" = "$RUNTIME_DIR/data" ] || { echo "runtime data escaped runtime root" >&2; exit 74; }
DAILY_LOCK="$RUNTIME_DIR/data/daily-task.lock"
[ ! -L "$DAILY_LOCK" ] || { echo "daily task lock must not be a symlink" >&2; exit 74; }
if [ ! -e "$DAILY_LOCK" ]; then (set -C; : > "$DAILY_LOCK") 2>/dev/null || true; fi
[ -f "$DAILY_LOCK" ] && [ ! -L "$DAILY_LOCK" ] || { echo "daily task lock must be a regular file" >&2; exit 74; }
[ "$(stat -c %h -- "$DAILY_LOCK")" = "1" ] || { echo "daily task lock must have one hard link" >&2; exit 74; }
chmod 600 -- "$DAILY_LOCK"
if [ "${HT_RELEASE_LOCKS_HELD:-0}" = "1" ]; then
  [ -e "/proc/$$/fd/8" ] && [ "$(readlink -f -- "/proc/$$/fd/8")" = "$DAILY_LOCK" ] || { echo "inherited daily lock descriptor is invalid" >&2; exit 74; }
else
  exec 8<>"$DAILY_LOCK"
fi
flock -n 8 || { echo "daily task is active" >&2; exit 75; }
unset HT_RELEASE_LOCKS_HELD

LIVE_PARENT=$(realpath -e -- "$(dirname -- "$LIVE_LINK")")
LIVE_LINK="$LIVE_PARENT/$(basename -- "$LIVE_LINK")"
RELEASE_DIR="$RELEASES_DIR/$SHA"
STAGING_DIR="$RELEASES_DIR/.incoming-$SHA-$$"
STAGING_ACTIVE=0
case "$RELEASE_DIR" in "$RELEASES_DIR"/*) ;; *) echo "release target escaped release root" >&2; exit 74 ;; esac

cleanup_staging() {
  if [ "$STAGING_ACTIVE" -eq 1 ] && [ -d "$STAGING_DIR" ] && [ ! -L "$STAGING_DIR" ]; then
    rm -r -- "$STAGING_DIR"
  fi
}
staging_exit() {
  CODE="$1"; trap - EXIT HUP INT TERM; cleanup_staging; exit "$CODE"
}
trap 'staging_exit $?' EXIT
trap 'staging_exit 129' HUP
trap 'staging_exit 130' INT
trap 'staging_exit 143' TERM

safe_git() {
  GIT_NO_REPLACE_OBJECTS=1 git --no-replace-objects -c protocol.file.allow=never -c protocol.ext.allow=never \
    -c http.followRedirects=false -c core.hooksPath=/dev/null \
    -c core.fsmonitor=false "$@"
}
GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null
GIT_ALLOW_PROTOCOL=https
export GIT_CONFIG_NOSYSTEM GIT_CONFIG_GLOBAL GIT_ALLOW_PROTOCOL
unset GIT_CONFIG GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_DIR GIT_WORK_TREE \
  GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES \
  GIT_INDEX_FILE GIT_SSH GIT_SSH_COMMAND GIT_PROXY_COMMAND GIT_EXTERNAL_DIFF
if [ -L "$RELEASE_DIR" ]; then
  echo "release target must not be a symlink" >&2
  exit 74
elif [ -e "$RELEASE_DIR" ]; then
  [ "$(realpath -e -- "$RELEASE_DIR")" = "$RELEASE_DIR" ] || { echo "release target escaped release root" >&2; exit 74; }
  [ -d "$RELEASE_DIR/.git" ] || { echo "existing release is not a checkout" >&2; exit 74; }
  ORIGIN=$(safe_git --git-dir="$RELEASE_DIR/.git" remote get-url origin)
  [ "$ORIGIN" = "$CANONICAL_REPO" ] || { echo "existing release origin is not canonical" >&2; exit 74; }
  [ "$(safe_git --git-dir="$RELEASE_DIR/.git" rev-parse HEAD)" = "$SHA" ] || { echo "existing release HEAD mismatch" >&2; exit 74; }
  "$PYTHON_BIN" "$SCRIPT_DIR/verify_release_tree.py" \
    --release-dir "$RELEASE_DIR" --sha "$SHA" --runtime-dir "$RUNTIME_DIR"
else
  [ ! -e "$STAGING_DIR" ] && [ ! -L "$STAGING_DIR" ] || { echo "staging path collision" >&2; exit 74; }
  STAGING_ACTIVE=1
  safe_git clone --no-checkout "$CANONICAL_REPO" "$STAGING_DIR"
  safe_git -C "$STAGING_DIR" fetch --depth=1 "$CANONICAL_REPO" "$SHA"
  safe_git -C "$STAGING_DIR" checkout --detach "$SHA"
  [ "$(safe_git --git-dir="$STAGING_DIR/.git" remote get-url origin)" = "$CANONICAL_REPO" ] || exit 74
  [ "$(safe_git --git-dir="$STAGING_DIR/.git" rev-parse HEAD)" = "$SHA" ] || exit 74
  "$PYTHON_BIN" "$SCRIPT_DIR/verify_release_tree.py" \
    --release-dir "$STAGING_DIR" --sha "$SHA" --runtime-dir "$RUNTIME_DIR"
  mv -T -- "$STAGING_DIR" "$RELEASE_DIR"
  STAGING_ACTIVE=0
fi
trap - EXIT HUP INT TERM
[ ! -L "$RELEASE_DIR" ] && [ "$(realpath -e -- "$RELEASE_DIR")" = "$RELEASE_DIR" ] || { echo "release checkout escaped release root" >&2; exit 74; }
[ "$(safe_git --git-dir="$RELEASE_DIR/.git" remote get-url origin)" = "$CANONICAL_REPO" ] || { echo "checked-out origin is not canonical" >&2; exit 74; }
[ "$(safe_git --git-dir="$RELEASE_DIR/.git" rev-parse HEAD)" = "$SHA" ] || { echo "checked-out SHA mismatch" >&2; exit 74; }
[ -f "$RELEASE_DIR/evaluation/production-regression-20260818-31/manifest.json" ] \
  && [ ! -L "$RELEASE_DIR/evaluation/production-regression-20260818-31/manifest.json" ] \
  || { echo "source commit A is not deployable; frozen artifact commit B is required" >&2; exit 74; }

for NAME in data logs backups reports-daily; do
  STATE_CHILD="$RUNTIME_DIR/$NAME"
  [ ! -L "$STATE_CHILD" ] || { echo "runtime state child must not be a symlink: $NAME" >&2; exit 74; }
  if [ -e "$STATE_CHILD" ]; then
    [ -d "$STATE_CHILD" ] || { echo "runtime state child must be a directory: $NAME" >&2; exit 74; }
    CHILD_REAL=$(realpath -e -- "$STATE_CHILD")
    case "$CHILD_REAL" in "$RUNTIME_DIR"/*) ;; *) echo "runtime state escaped root: $NAME" >&2; exit 74 ;; esac
  else
    mkdir -- "$STATE_CHILD"
  fi
  safe_git --git-dir="$RELEASE_DIR/.git" ls-tree -r --name-only "$SHA" -- "$NAME" | grep . >/dev/null 2>&1 && { echo "runtime path is tracked: $NAME" >&2; exit 74; }
  TARGET="$RELEASE_DIR/$NAME"
  if [ -L "$TARGET" ]; then
    [ "$(readlink -f -- "$TARGET")" = "$STATE_CHILD" ] || { echo "release runtime link target mismatch: $NAME" >&2; exit 74; }
    rm -f -- "$TARGET"
  elif [ -e "$TARGET" ]; then
    echo "refusing to replace existing non-symlink runtime path: $NAME" >&2
    exit 74
  fi
  ln -s -- "$STATE_CHILD" "$TARGET"
done

verify_release_tree() {
  "$PYTHON_BIN" "$SCRIPT_DIR/verify_release_tree.py" \
    --release-dir "$RELEASE_DIR" --sha "$SHA" --runtime-dir "$RUNTIME_DIR"
}

verify_checkout_identity() {
  CHECKOUT="$1"
  CHECKOUT_SHA="$2"
  [ ! -L "$CHECKOUT" ] && [ -d "$CHECKOUT/.git" ] || return 1
  [ "$(safe_git --git-dir="$CHECKOUT/.git" remote get-url origin)" = "$CANONICAL_REPO" ] || return 1
  [ "$(safe_git --git-dir="$CHECKOUT/.git" rev-parse HEAD)" = "$CHECKOUT_SHA" ] || return 1
}

verify_deployed_release() {
  CHECKOUT="$1"
  CHECKOUT_SHA="$2"
  verify_checkout_identity "$CHECKOUT" "$CHECKOUT_SHA" || return 1
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

verify_release_tree
"$PYTHON_BIN" "$SCRIPT_DIR/smoke_release.py" --release-dir "$RELEASE_DIR" --josint-db "$JOSINT_DB" --env-file "$ENV_FILE"
verify_release_tree

PREVIOUS_TARGET=""
if [ -e "$LIVE_LINK" ] && [ ! -L "$LIVE_LINK" ]; then echo "live path must be a symlink" >&2; exit 74; fi
if [ -L "$LIVE_LINK" ]; then
  PREVIOUS_TARGET=$(readlink -f -- "$LIVE_LINK")
  case "$PREVIOUS_TARGET" in "$RELEASES_DIR"/*) ;; *) echo "previous live target escaped release root" >&2; exit 74 ;; esac
  PREVIOUS_SHA=$(basename -- "$PREVIOUS_TARGET")
  case "$PREVIOUS_SHA" in ????????????????????????????????????????) ;; *) echo "previous live target is not an exact SHA release" >&2; exit 74 ;; esac
  case "$PREVIOUS_SHA" in *[!0123456789abcdef]*) echo "previous live target is not an exact SHA release" >&2; exit 74 ;; esac
  [ "$PREVIOUS_TARGET" = "$RELEASES_DIR/$PREVIOUS_SHA" ] || { echo "previous live target path mismatch" >&2; exit 74; }
  verify_deployed_release "$PREVIOUS_TARGET" "$PREVIOUS_SHA" || { echo "previous live release failed full validation" >&2; exit 74; }
fi
if [ "$PREVIOUS_TARGET" = "$RELEASE_DIR" ]; then
  printf 'already active %s\n' "$SHA"
  exit 0
fi
if [ -e "$RELEASE_DIR/.deployed_git_sha" ] || [ -e "$RELEASE_DIR/.release-manifest.json" ]; then
  echo "historical deployed release must be selected with rollback script" >&2
  exit 74
fi

BACKUP_MANIFEST_ROOT="$RELEASE_DIR"
if [ -n "$PREVIOUS_TARGET" ]; then BACKUP_MANIFEST_ROOT="$PREVIOUS_TARGET"; fi
[ -f "$RELEASE_DIR/scripts/run_lead_radar_v2.py" ] && [ ! -L "$RELEASE_DIR/scripts/run_lead_radar_v2.py" ] || { echo "production backup entry point is missing" >&2; exit 74; }
"$PYTHON_BIN" "$RELEASE_DIR/scripts/run_lead_radar_v2.py" backup \
  --git-sha "$SHA" \
  --backup-dir "$RUNTIME_DIR/backups" \
  --discover-data-dir "$RUNTIME_DIR/data" \
  --databases \
    "$RUNTIME_DIR/data/fixed-sources.sqlite" \
    "$RUNTIME_DIR/data/facts.sqlite" \
    "$RUNTIME_DIR/data/runtime.sqlite" \
    "$RUNTIME_DIR/data/search-budget.sqlite" \
    "$RUNTIME_DIR/data/feishu-projection.sqlite" \
    "$RUNTIME_DIR/data/audit.sqlite" \
    "$RUNTIME_DIR/data/ops-metrics.sqlite" \
    "$RUNTIME_DIR/data/talent-pool.sqlite" \
    "$RUNTIME_DIR/data/feishu-notifications.sqlite" \
  --manifests \
    "$BACKUP_MANIFEST_ROOT/config/fixed-sources.json" \
    "$BACKUP_MANIFEST_ROOT/config/source-packs.json" \
    "$BACKUP_MANIFEST_ROOT/config/openclaw-report-cron.json" \
  || { echo "production backup gate failed; release was not activated" >&2; exit 74; }

LINK_TMP="$LIVE_PARENT/.$(basename -- "$LIVE_LINK").next.$$"
ROLLBACK_TMP="$LIVE_PARENT/.$(basename -- "$LIVE_LINK").previous.$$"
PREVIOUS_POINTER="$RUNTIME_DIR/.previous_release_target"
PREVIOUS_POINTER_BACKUP="$RUNTIME_DIR/.previous_release_target.backup.$$"
MARKER_TMP="$RELEASE_DIR/.deployed_git_sha.next.$$"
MANIFEST_TMP="$RELEASE_DIR/.release-manifest.json.next.$$"
PREVIOUS_TMP="$RUNTIME_DIR/.previous_release_target.next.$$"
POINTER_CHANGED=0
ACTIVATION_IN_PROGRESS=0

cleanup_activation_temps() {
  rm -f -- "$LINK_TMP" "$ROLLBACK_TMP" "$MARKER_TMP" "$MANIFEST_TMP" \
    "$PREVIOUS_TMP" "$PREVIOUS_POINTER_BACKUP"
}

rollback_activation() {
  rm -f -- "$RELEASE_DIR/.deployed_git_sha" "$RELEASE_DIR/.release-manifest.json"
  if [ "$POINTER_CHANGED" -eq 1 ]; then
    rm -f -- "$PREVIOUS_POINTER"
    if [ -f "$PREVIOUS_POINTER_BACKUP" ] && [ ! -L "$PREVIOUS_POINTER_BACKUP" ]; then
      mv -f -- "$PREVIOUS_POINTER_BACKUP" "$PREVIOUS_POINTER" || return 1
    else
      rm -f -- "$PREVIOUS_POINTER_BACKUP" || return 1
    fi
    POINTER_CHANGED=0
  fi
  if [ -n "$PREVIOUS_TARGET" ]; then
    if ! mv -Tf -- "$ROLLBACK_TMP" "$LIVE_LINK"; then
      return 1
    fi
    [ -L "$LIVE_LINK" ] || return 1
    [ "$(readlink -f -- "$LIVE_LINK")" = "$PREVIOUS_TARGET" ] || return 1
    verify_deployed_release "$PREVIOUS_TARGET" "$PREVIOUS_SHA" || return 1
  else
    rm -f -- "$LIVE_LINK" "$ROLLBACK_TMP" || return 1
    [ ! -e "$LIVE_LINK" ] && [ ! -L "$LIVE_LINK" ] || return 1
  fi
  cleanup_activation_temps
  ACTIVATION_IN_PROGRESS=0
}

activation_exit() {
  CODE="$1"
  trap - EXIT HUP INT TERM
  if [ "$ACTIVATION_IN_PROGRESS" -eq 1 ]; then
    rollback_activation || {
      cleanup_activation_temps
      echo "CRITICAL: interrupted activation could not restore prior state" >&2
      exit 75
    }
  else
    cleanup_activation_temps
  fi
  exit "$CODE"
}
trap 'activation_exit $?' EXIT
trap 'activation_exit 129' HUP
trap 'activation_exit 130' INT
trap 'activation_exit 143' TERM

if [ -n "$PREVIOUS_TARGET" ]; then
  ln -s -- "$PREVIOUS_TARGET" "$ROLLBACK_TMP"
fi

ln -s -- "$RELEASE_DIR" "$LINK_TMP"
ACTIVATION_IN_PROGRESS=1
if ! mv -Tf -- "$LINK_TMP" "$LIVE_LINK"; then
  ACTIVATION_IN_PROGRESS=0
  rm -f -- "$LINK_TMP" "$ROLLBACK_TMP"
  echo "release activation failed; previous release remains selected" >&2
  exit 74
fi
if ! "$PYTHON_BIN" "$SCRIPT_DIR/smoke_release.py" --release-dir "$LIVE_LINK" --expected-realpath "$RELEASE_DIR" --josint-db "$JOSINT_DB" --env-file "$ENV_FILE" \
  || ! verify_release_tree; then
  if ! rollback_activation; then
    echo "CRITICAL: post-activation smoke failed and previous release could not be restored" >&2
    exit 75
  fi
  echo "post-activation smoke failed; previous release restored" >&2
  exit 74
fi

if ! "$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" write \
  --marker "$MARKER_TMP" --manifest "$MANIFEST_TMP" \
  --previous-file "$PREVIOUS_TMP" --sha "$SHA" \
  --previous-release "$PREVIOUS_TARGET"; then
  rm -f -- "$MARKER_TMP" "$MANIFEST_TMP" "$PREVIOUS_TMP"
  if ! rollback_activation; then
    echo "CRITICAL: deployment metadata failed and previous release could not be restored" >&2
    exit 75
  fi
  echo "deployment metadata failed; previous release restored" >&2
  exit 74
fi
if [ -e "$PREVIOUS_POINTER" ] || [ -L "$PREVIOUS_POINTER" ]; then
  "$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" verify-pointer --pointer "$PREVIOUS_POINTER" --releases-dir "$RELEASES_DIR" || { rollback_activation || exit 75; exit 74; }
  [ -f "$PREVIOUS_POINTER" ] && [ ! -L "$PREVIOUS_POINTER" ] || { echo "previous pointer must be a regular file" >&2; rollback_activation || exit 75; exit 74; }
  [ "$(stat -c %h -- "$PREVIOUS_POINTER")" = "1" ] || { echo "previous pointer must have one hard link" >&2; rollback_activation || exit 75; exit 74; }
  cp -p -- "$PREVIOUS_POINTER" "$PREVIOUS_POINTER_BACKUP" || { rollback_activation || exit 75; exit 74; }
fi
POINTER_CHANGED=1
if ! mv -f -- "$MARKER_TMP" "$RELEASE_DIR/.deployed_git_sha" \
  || ! mv -f -- "$MANIFEST_TMP" "$RELEASE_DIR/.release-manifest.json" \
  || ! mv -f -- "$PREVIOUS_TMP" "$RUNTIME_DIR/.previous_release_target"; then
  rm -f -- "$MARKER_TMP" "$MANIFEST_TMP" "$PREVIOUS_TMP"
  if ! rollback_activation; then
    echo "CRITICAL: metadata activation failed and previous release could not be restored" >&2
    exit 75
  fi
  echo "deployment metadata activation failed; previous release restored" >&2
  exit 74
fi
if ! "$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" verify \
  --release-dir "$RELEASE_DIR" --sha "$SHA" --releases-dir "$RELEASES_DIR" \
  || ! "$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" verify-pointer \
    --pointer "$PREVIOUS_POINTER" --releases-dir "$RELEASES_DIR" \
  || ! verify_release_tree; then
  if ! rollback_activation; then
    echo "CRITICAL: committed metadata failed validation and previous release could not be restored" >&2
    exit 75
  fi
  echo "committed deployment metadata failed validation; previous release restored" >&2
  exit 74
fi
ACTIVATION_IN_PROGRESS=0
cleanup_activation_temps
trap - EXIT HUP INT TERM
printf 'deployed %s; previous=%s\n' "$SHA" "${PREVIOUS_TARGET:-none}"
