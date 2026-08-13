#!/bin/sh
# 按 PUID/PGID 创建运行用户并降权 —— NAS 上挂载卷的权限适配。
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
DATA_DIR=${DATA_DIR:-/app/data}

# 宿主机上 ./.env 不存在时，Docker 会替你创建一个同名【目录】再挂进来。
# 这个坑的症状是「配置全部失效但没有任何报错」，必须提前喊出来。
if [ -d /app/.env ]; then
  echo "[entrypoint] 错误：/app/.env 是一个目录，不是文件。" >&2
  echo "[entrypoint] 说明宿主机上的 ./.env 在首次启动时并不存在，Docker 自动建了目录。" >&2
  echo "[entrypoint] 修复：docker compose down && rm -rf ./.env && cp .env.example .env" >&2
  echo "[entrypoint] 然后填好配置再 docker compose up -d" >&2
  exit 1
fi
if [ ! -f /app/.env ]; then
  echo "[entrypoint] 警告：/app/.env 不存在，将全部使用默认值（LLM 分析会降级）。" >&2
fi

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
