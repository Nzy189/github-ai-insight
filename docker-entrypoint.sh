#!/bin/sh
# 按 PUID/PGID 创建运行用户并降权 —— NAS 上挂载卷的权限适配。
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
DATA_DIR=${DATA_DIR:-/app/data}

if [ "$(id -u)" = "0" ]; then
  if ! getent group appgroup >/dev/null 2>&1; then
    groupadd -o -g "$PGID" appgroup 2>/dev/null || addgroup -g "$PGID" appgroup 2>/dev/null || true
  fi
  if ! id appuser >/dev/null 2>&1; then
    useradd -o -u "$PUID" -g "$PGID" -M -s /sbin/nologin appuser 2>/dev/null || \
      adduser -u "$PUID" -G appgroup -H -D appuser 2>/dev/null || true
  fi

  mkdir -p "$DATA_DIR/reports" "$DATA_DIR/archive"
  chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || true

  echo "[entrypoint] 以 UID=$PUID GID=$PGID 运行: $*"
  exec gosu "$PUID:$PGID" "$@"
fi

exec "$@"
