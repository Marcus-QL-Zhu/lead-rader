#!/bin/sh
# Verify that the live symlink, immutable checkout, and marker agree.
set -eu
PYTHONDONTWRITEBYTECODE=1
export PYTHONDONTWRITEBYTECODE

CANONICAL_REPO="https://github.com/Marcus-QL-Zhu/lead-rader.git"

usage() {
  echo "usage: $0 --live-link ABS --releases-dir ABS --runtime-dir ABS --expected-sha 40_HEX_SHA --env-file ABS --josint-db ABS [--python PYTHON]" >&2
  exit 64
}

LIVE_LINK=""
RELEASES_DIR=""
RUNTIME_DIR=""
SHA=""
ENV_FILE=""
JOSINT_DB=""
PYTHON_BIN="/home/admin/.pyenv/versions/3.11.14/bin/python3"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --live-link|--releases-dir|--runtime-dir|--expected-sha|--env-file|--josint-db|--python)
      [ "$#" -ge 2 ] || usage
      case "$1" in
        --live-link) LIVE_LINK="$2" ;;
        --releases-dir) RELEASES_DIR="$2" ;;
        --runtime-dir) RUNTIME_DIR="$2" ;;
        --expected-sha) SHA="$2" ;;
        --env-file) ENV_FILE="$2" ;;
        --josint-db) JOSINT_DB="$2" ;;
        --python) PYTHON_BIN="$2" ;;
      esac
      shift 2 ;;
    *) usage ;;
  esac
done
case "$LIVE_LINK" in /*) ;; *) usage ;; esac
case "$RELEASES_DIR" in /*) ;; *) usage ;; esac
case "$RUNTIME_DIR" in /*) ;; *) usage ;; esac
case "$ENV_FILE" in /*) ;; *) usage ;; esac
case "$JOSINT_DB" in /*) ;; *) usage ;; esac
case "$SHA" in ????????????????????????????????????????) ;; *) usage ;; esac
case "$SHA" in *[!0123456789abcdef]*) usage ;; esac
[ -L "$LIVE_LINK" ] || { echo "live path is not a symlink" >&2; exit 74; }
[ ! -L "$RELEASES_DIR" ] || { echo "release root must not be a symlink" >&2; exit 74; }
[ ! -L "$RUNTIME_DIR" ] || { echo "runtime root must not be a symlink" >&2; exit 74; }
RELEASES_DIR=$(realpath -e -- "$RELEASES_DIR")
RUNTIME_DIR=$(realpath -e -- "$RUNTIME_DIR")
[ "$RELEASES_DIR" != "/" ] && [ "$RUNTIME_DIR" != "/" ] || { echo "release/runtime root cannot be /" >&2; exit 64; }
LIVE_PARENT=$(realpath -e -- "$(dirname -- "$LIVE_LINK")")
LIVE_LINK="$LIVE_PARENT/$(basename -- "$LIVE_LINK")"
RELEASE_DIR=$(readlink -f -- "$LIVE_LINK")
[ "$RELEASE_DIR" = "$RELEASES_DIR/$SHA" ] || { echo "live target escaped exact release" >&2; exit 74; }
[ ! -L "$RELEASES_DIR/$SHA" ] || { echo "release target must not be a symlink" >&2; exit 74; }
[ "$(realpath -e -- "$RELEASES_DIR/$SHA")" = "$RELEASE_DIR" ] || { echo "release target confinement failed" >&2; exit 74; }
[ -d "$RELEASE_DIR/.git" ] || { echo "live target is not a git checkout" >&2; exit 74; }
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
[ "$(safe_git --git-dir="$RELEASE_DIR/.git" remote get-url origin)" = "$CANONICAL_REPO" ] || { echo "live origin is not canonical" >&2; exit 74; }
[ "$(safe_git --git-dir="$RELEASE_DIR/.git" rev-parse HEAD)" = "$SHA" ] || { echo "live git SHA mismatch" >&2; exit 74; }
[ -f "$RELEASE_DIR/evaluation/production-regression-20260818-31/manifest.json" ] \
  && [ ! -L "$RELEASE_DIR/evaluation/production-regression-20260818-31/manifest.json" ] \
  || { echo "live release is missing the frozen regression artifact" >&2; exit 74; }
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
"$PYTHON_BIN" "$SCRIPT_DIR/release_metadata.py" verify --release-dir "$RELEASE_DIR" --sha "$SHA" --releases-dir "$RELEASES_DIR"
"$PYTHON_BIN" "$SCRIPT_DIR/verify_release_tree.py" --release-dir "$RELEASE_DIR" --sha "$SHA" --runtime-dir "$RUNTIME_DIR"
"$PYTHON_BIN" "$SCRIPT_DIR/smoke_release.py" --release-dir "$LIVE_LINK" --expected-realpath "$RELEASE_DIR" --josint-db "$JOSINT_DB" --env-file "$ENV_FILE"
echo "release verified: $SHA"
