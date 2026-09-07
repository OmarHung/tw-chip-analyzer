#!/usr/bin/env bash
#
# 灌歷史:對「今天往前 N 個日曆天」內的每個平日跑一次 EOD(scripts/eod.sh)。
# 冪等(daily 用 upsert),可重複執行;非交易日 daily 偵測無 OHLCV 自動空跑。
#
# 用法(Linux 主機或容器內;需 GNU date):
#   ./scripts/backfill.sh [天數]        # 預設 90(約 64 交易日,足夠 60 日視窗/背離)
#
# Docker:
#   docker compose exec api ./scripts/backfill.sh 90
# 裸機:
#   APP_ENV=prod ./scripts/backfill.sh 90
#
set -u
cd "$(dirname "$0")/.."

DAYS="${1:-90}"

i="$DAYS"
while [ "$i" -ge 0 ]; do
  d=$(date -d "-$i day" +%F)
  if [ "$(date -d "$d" +%u)" -le 5 ]; then   # 1..5 = 週一~週五
    echo "=== EOD $d ==="
    ./scripts/eod.sh "$d" || echo "[warn] $d 失敗,續跑下一天"
  fi
  i=$((i - 1))
done

echo "=== backfill 完成(往前 $DAYS 天)==="
