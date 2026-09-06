#!/usr/bin/env bash
#
# 一鍵啟動開發環境:後端 FastAPI(uvicorn)+ 前端 Next.js。
#
# 後端跑在 :8099(前端 lib/api.ts 預設連此埠),前端跑在 :3000。
# Ctrl+C 會一併關閉兩者。
#
# 用法:
#   ./scripts/dev.sh              # 後端 --reload + 前端 next dev(HMR)
#   MODE=prod ./scripts/dev.sh    # 前端改走 build + start(HMR 在某些環境會卡 hydration 時用)
#
# 可調環境變數:
#   BACKEND_PORT(預設 8099)、APP_ENV(預設 dev)、PY(預設 .venv/bin/python)
#
set -euo pipefail
cd "$(dirname "$0")/.."

export APP_ENV="${APP_ENV:-dev}"
BACKEND_PORT="${BACKEND_PORT:-8099}"
PY="${PY:-.venv/bin/python}"
MODE="${MODE:-dev}"
API_BASE="http://127.0.0.1:${BACKEND_PORT}"

# --- 前置檢查 ---
if [ ! -x "$PY" ]; then
  echo "✗ 找不到 Python venv:$PY" >&2
  echo "  請先建立:python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if ! "$PY" -c "import uvicorn" 2>/dev/null; then
  echo "✗ venv 內未安裝 uvicorn,請執行 .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if ! command -v pnpm >/dev/null 2>&1; then
  echo "✗ 找不到 pnpm,請先安裝(npm i -g pnpm)" >&2
  exit 1
fi
if [ ! -d frontend/node_modules ]; then
  echo "== 首次啟動:安裝前端相依 =="
  (cd frontend && pnpm install)
fi

# --- 同時關閉子行程 ---
pids=()
cleanup() {
  trap - INT TERM EXIT
  echo ""
  echo "== 收工,關閉前後端 =="
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- 後端 ---
echo "== 後端  ${API_BASE}  (APP_ENV=${APP_ENV}, Swagger ${API_BASE}/docs) =="
"$PY" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT" &
pids+=($!)

# --- 前端 ---
echo "== 前端  http://localhost:3000  (MODE=${MODE}) =="
if [ "$MODE" = "prod" ]; then
  (cd frontend && NEXT_PUBLIC_API_BASE="$API_BASE" pnpm build \
    && NEXT_PUBLIC_API_BASE="$API_BASE" pnpm start) &
else
  (cd frontend && NEXT_PUBLIC_API_BASE="$API_BASE" pnpm dev) &
fi
pids+=($!)

echo "== 已啟動,按 Ctrl+C 結束 =="
wait
