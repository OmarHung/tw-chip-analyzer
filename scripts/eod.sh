#!/usr/bin/env bash
#
# 每日盤後(End-Of-Day)完整流程 orchestration。
#
# 串起:行情/法人/融資匯入 + TAIEX + 特徵 → 逐筆(Shioaji simulation)
#       → 重建特徵(含 intraday order flow)+ composite 分數落地 signal_snapshot。
# 這是「餵養 Gate 1 驗證(每日分數時間序列)+ Gate 3 realtime(逐筆累積)」的機制。
#
# 用法:
#   ./scripts/eod.sh              # 今天
#   ./scripts/eod.sh 2026-09-04   # 指定交易日
#
# 非交易日:daily.py 偵測當日無 OHLCV 會自動略過,安全空跑。
#
# 排程(交易日盤後,例:週一~週五 14:30;國定假日自動空跑):
#   30 14 * * 1-5 cd /Users/omar/tw_chip_analyzer && APP_ENV=dev ./scripts/eod.sh >> /tmp/eod.log 2>&1
#
set -euo pipefail
cd "$(dirname "$0")/.."

export APP_ENV="${APP_ENV:-dev}"
DATE="${1:-$(date +%F)}"
PY="${PY:-.venv/bin/python}"

echo "=== EOD $DATE (APP_ENV=$APP_ENV) ==="

# 1) 行情 + 法人 + 融資 + 當週 TDCC(冪等,跨週自動累積) + TAIEX + 特徵
#    --skip-signals:此時尚無逐筆,分數留待步驟 3 一次算成四維,避免重複落地。
$PY -m app.jobs.daily "$DATE" --tdcc --index --skip-signals

# 2) 逐筆(Shioaji simulation);無金鑰/非交易日/失敗時不中斷整體流程。
$PY -m app.jobs.import_ticks "$DATE" || echo "[warn] import_ticks 失敗或無逐筆,intraday 將為中性"

# 3) 重建特徵(有逐筆則含 intraday z)+ composite 分數落地 signal_snapshot
$PY -m app.jobs.daily "$DATE" --skip-import

echo "=== EOD $DATE done ==="
