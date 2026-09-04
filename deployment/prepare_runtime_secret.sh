#!/bin/sh
# Create the production secret boundary without accepting secrets in argv/stdin.
set -eu
umask 077

SECRETS_DIR="/home/admin/.openclaw/secrets"
ENV_FILE="$SECRETS_DIR/lead-radar.env"

[ "$(/usr/bin/id -un)" = "admin" ] && [ "$(/usr/bin/id -gn)" = "admin" ] || {
  echo "run as the admin service account" >&2
  exit 77
}
[ ! -L "$SECRETS_DIR" ] || {
  echo "secrets directory must not be a symlink" >&2
  exit 74
}
if [ -e "$SECRETS_DIR" ]; then
  [ -d "$SECRETS_DIR" ] || {
    echo "secrets path must be a directory" >&2
    exit 74
  }
else
  mkdir -m 0700 -- "$SECRETS_DIR"
fi
chmod 0700 -- "$SECRETS_DIR"
[ "$(stat -c %U:%G -- "$SECRETS_DIR")" = "admin:admin" ] || {
  echo "secrets directory must be owned by admin:admin" >&2
  exit 74
}

[ ! -L "$ENV_FILE" ] || {
  echo "runtime env must not be a symlink" >&2
  exit 74
}
if [ -e "$ENV_FILE" ]; then
  [ -f "$ENV_FILE" ] || {
    echo "runtime env must be a regular file" >&2
    exit 74
  }
  [ "$(stat -c %h -- "$ENV_FILE")" = "1" ] || {
    echo "runtime env must have exactly one hard link" >&2
    exit 74
  }
else
  : > "$ENV_FILE"
fi
chmod 0600 -- "$ENV_FILE"
[ "$(stat -c %h -- "$ENV_FILE")" = "1" ] || exit 74
[ "$(stat -c %U:%G -- "$ENV_FILE")" = "admin:admin" ] || {
  echo "runtime env must be owned by admin:admin" >&2
  exit 74
}
echo "protected runtime env is ready; populate it out of band and run the auth smoke"
