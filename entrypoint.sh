#!/bin/sh
# Run as PUID:PGID (linuxserver.io convention) so deletions happen with the
# same identity Syncthing uses on the media directory.
set -e

PUID="${PUID:-}"
PGID="${PGID:-}"

if [ "$(id -u)" = "0" ] && [ -n "$PUID" ]; then
  PGID="${PGID:-$PUID}"
  if ! getent group "$PGID" >/dev/null 2>&1; then
    addgroup -g "$PGID" app >/dev/null 2>&1 || true
  fi
  if ! getent passwd "$PUID" >/dev/null 2>&1; then
    adduser -D -H -u "$PUID" -G "$(getent group "$PGID" | cut -d: -f1)" app >/dev/null 2>&1 || true
  fi
  exec su-exec "$PUID:$PGID" "$@"
fi

exec "$@"
