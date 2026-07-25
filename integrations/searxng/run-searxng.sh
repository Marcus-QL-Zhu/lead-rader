#!/bin/sh
set -eu

SKILL_DIR=$(CDPATH= cd -- $(dirname $0)/.. && pwd)
docker stop searxng 2>/dev/null || true
docker rm searxng 2>/dev/null || true
docker run --restart always --network host --name searxng -d \
  -e GRANIAN_HOST=127.0.0.1 \
  -v $SKILL_DIR/config/:/etc/searxng:Z \
  searxng/searxng:latest
