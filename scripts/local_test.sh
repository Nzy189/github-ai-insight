#!/usr/bin/env bash
# ============================================================
# 本地一键验证 (macOS / Linux / NAS SSH)
#   ./scripts/local_test.sh          完整验证
#   ./scripts/local_test.sh --serve  验证完再起 HTTP 服务
# 不需要任何 API Key，不发起任何网络请求。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8080}"
SERVE=0
[ "${1:-}" = "--serve" ] && SERVE=1

step() { printf '\n\033[36m[%s] %s\033[0m\n' "$1" "$2"; }

step 1 "安装依赖"
python3 -m pip install -q -r requirements-dev.txt

step 2 "配置自检"
python3 main.py --show-config

step 3 "单元测试"
python3 -m pytest -q

step 4 "Mock 全链路（抓取 → 分析 → 打分 → 报告 → 归档 → 推送预览）"
rm -rf ./data-local
python3 main.py --now --mock

step 5 "去重验证（第二次执行应换一个项目）"
python3 main.py --now --mock | grep -F "胜出"

step 6 "数据库记录"
python3 main.py --list --data-dir ./data-local

step 7 "产物清单"
find ./data-local -type f | sort

printf '\n\033[32m✅ 本地验证全部通过\033[0m\n'
printf '   最新报告: %s\n' "$(ls -t ./data-local/reports/*.html | head -1)"

if [ "$SERVE" = "1" ]; then
  printf '\n\033[33m启动报告服务: http://localhost:%s/reports  (Ctrl+C 停止)\033[0m\n' "$PORT"
  python3 main.py --serve --data-dir ./data-local --port "$PORT"
else
  printf '   在浏览器中查看: python3 main.py --serve --data-dir ./data-local\n'
fi
