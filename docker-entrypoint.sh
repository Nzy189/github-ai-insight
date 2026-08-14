#!/bin/sh
# 按 PUID/PGID 创建运行用户并降权 —— NAS 上挂载卷的权限适配。
set -e

DATA_DIR=${DATA_DIR:-/app/data}

# 代码热更新：挂载目录里放了 src/ 就优先跑它，否则跑镜像内置的。
#
# GUI 型 NAS 上换镜像意味着删容器重建、重填挂载和端口。而这个项目是纯
# Python、依赖很稳定，把源码放进挂载目录就能做到「覆盖文件 + 重启容器」
# 完成升级，不碰镜像。
#
# 保护：先试导入一次，失败就回退到镜像内置代码 —— 传错文件不会把容器
# 搞成重启循环。注意 src/ 里必须同时带上 templates/，报告模板按
# __file__ 的相对位置查找。依赖变了仍然需要换镜像。
resolve_app_dir() {
  if [ ! -f "$DATA_DIR/src/main.py" ]; then
    echo /app
    return
  fi
  if (cd "$DATA_DIR/src" && python -c "import main" >/dev/null 2>&1); then
    echo "[entrypoint] 使用挂载目录中的代码: $DATA_DIR/src" >&2
    echo "$DATA_DIR/src"
  else
    echo "[entrypoint] 警告：$DATA_DIR/src 中的代码导入失败，回退到镜像内置代码" >&2
    echo "[entrypoint] 依赖是否有变动？依赖变了必须换镜像。" >&2
    echo /app
  fi
}

# PUID/PGID 没显式给的话，沿用挂载目录本身的属主。
# GUI 型 NAS（极空间等）通常不给终端，用户查不到自己的 uid；
# 硬套 1000 会把目录 chown 成一个不属于他们的 uid，之后连从 NAS
# 文件管理器编辑 .env 都做不到 —— 而那正是主要的配置方式。
if [ -z "$PUID" ] || [ -z "$PGID" ]; then
  DETECTED_UID=$(stat -c '%u' "$DATA_DIR" 2>/dev/null || echo "")
  DETECTED_GID=$(stat -c '%g' "$DATA_DIR" 2>/dev/null || echo "")
  if [ -n "$DETECTED_UID" ] && [ -n "$DETECTED_GID" ]; then
    PUID=${PUID:-$DETECTED_UID}
    PGID=${PGID:-$DETECTED_GID}
    echo "[entrypoint] 未指定 PUID/PGID，沿用 $DATA_DIR 的属主 $PUID:$PGID"
  fi
fi
PUID=${PUID:-1000}
PGID=${PGID:-1000}

# 宿主机上 ./.env 不存在时，Docker 会替你创建一个同名【目录】再挂进来。
# 这个坑的症状是「配置全部失效但没有任何报错」，必须提前喊出来。
if [ -d /app/.env ]; then
  echo "[entrypoint] 错误：/app/.env 是一个目录，不是文件。" >&2
  echo "[entrypoint] 说明宿主机上的 ./.env 在首次启动时并不存在，Docker 自动建了目录。" >&2
  echo "[entrypoint] 修复：docker compose down && rm -rf ./.env && cp .env.example .env" >&2
  echo "[entrypoint] 或者改用 GUI 方式：把 .env 放进数据目录（\$DATA_DIR/.env），只挂目录不挂文件。" >&2
  exit 1
fi
# 配置可以来自 /app/.env（compose 挂文件）或数据目录里的 .env / config.env
# （GUI 型 NAS 只能挂目录；config.env 不以点开头，文件管理器里看得见）。
if [ ! -f /app/.env ] && [ ! -f "$DATA_DIR/.env" ] && [ ! -f "$DATA_DIR/config.env" ]; then
  echo "[entrypoint] 警告：没找到配置文件。已查找：" >&2
  echo "[entrypoint]   /app/.env" >&2
  echo "[entrypoint]   $DATA_DIR/.env        （点开头，文件管理器里可能被隐藏）" >&2
  echo "[entrypoint]   $DATA_DIR/config.env  （推荐：不隐藏，随时可编辑）" >&2
  echo "[entrypoint] 将全部使用默认值，LLM 分析会整体降级。" >&2
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

  # 每个目录单独核对属主。只看 $DATA_DIR 是不够的：上面这行 mkdir 以 root
  # 身份运行，新建的子目录属于 root:root，而父目录的属主通常已经对得上 ——
  # 于是递归 chown 被跳过，子目录永远修不了。
  # 症状极具迷惑性：容器启动正常、数据库正常（它在 $DATA_DIR 根下，可写），
  # 一切看起来健康，直到十小时后定时任务写报告时才以 PermissionError 崩掉。
  #
  # 属主已经对上的仍然不动 —— 递归 chown 一个大目录既慢，又会无谓改掉宿主机
  # 上的文件属主（NAS 用户可能因此失去从文件管理器编辑 .env 的权限）。
  for d in "$DATA_DIR" "$DATA_DIR/reports" "$DATA_DIR/archive"; do
    owner=$(stat -c '%u:%g' "$d" 2>/dev/null || echo "")
    [ "$owner" = "$PUID:$PGID" ] && continue
    echo "[entrypoint] 调整属主 $d: $owner -> $PUID:$PGID"
    if [ "$d" = "$DATA_DIR" ]; then
      chown -R "$PUID:$PGID" "$d" 2>/dev/null || true   # 首次挂载：连历史文件一起修
    else
      chown "$PUID:$PGID" "$d" 2>/dev/null || true      # 子目录：单层即可
    fi
  done

  # 属主对了不等于写得进去 —— 只读挂载、ACL、SMB 挂载都能拦住。
  # 这个检查必须发生在启动时：否则要等第一次生成报告才暴露，而那已经是
  # 十小时之后，用户看到的只是「今天没收到报告」。
  # 刻意不 exit：容器退出会变成重启循环，比一条响亮的告警更糟，
  # 而且 HTTP 服务还能继续提供历史报告。
  for d in "$DATA_DIR" "$DATA_DIR/reports" "$DATA_DIR/archive"; do
    if ! gosu "$PUID:$PGID" sh -c "touch '$d/.wprobe' && rm -f '$d/.wprobe'" 2>/dev/null; then
      echo "[entrypoint] ==========================================================" >&2
      echo "[entrypoint] 错误：$d 对 UID=$PUID 不可写。" >&2
      echo "[entrypoint] 定时任务会在写报告时失败，你将收不到任何推送。" >&2
      echo "[entrypoint] 修复：容器终端执行 chown -R $PUID:$PGID $DATA_DIR" >&2
      echo "[entrypoint] 或在 NAS 文件管理器里把数据目录连同子目录的属主改过来。" >&2
      echo "[entrypoint] ==========================================================" >&2
    fi
  done

  echo "[entrypoint] 以 UID=$PUID GID=$PGID 运行: $*"
  cd "$(resolve_app_dir)"
  exec gosu "$PUID:$PGID" "$@"
fi

cd "$(resolve_app_dir)"
exec "$@"
