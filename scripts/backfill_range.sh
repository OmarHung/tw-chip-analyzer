#!/usr/bin/env bash
#
# 長區間歷史回補（vultr 主機上執行，走 docker compose）：
#   1. TAIEX 大盤（起始日往前多抓 4 個月，MA60 才有資料）
#   2. 公司行動（除權息/面額/減資區間查詢，同樣往前 4 個月）
#   3. 由舊到新逐交易日跑 app.jobs.daily：上市+上櫃行情/法人/融資券/借券 → 特徵 → 分數
#      （不抓逐筆、不抓 TDCC——不燒 Shioaji 配額，TDCC 只有當週）
#   4. 可選：從 --rebuild-from 起重建既有分數（前段日子當初回看視窗不完整）
#
# 為什麼不用 backfill.sh 或 /system 區間回補：前者每天跑 import_ticks 會燒逐筆配額；
# 後者不抓 TAIEX（舊日子全變「大盤未知→WATCH」）且逐日請求不節流。
#
# 冪等可續跑：完成的日子記在 $STATE/done.txt，重跑自動略過；失敗的記在 failed.txt，
# 不中斷整體。自動避開 EOD 排程時段（16:00、21:00 信用補抓）。
#
# 用法：
#   ./scripts/backfill_range.sh 2026-02-02 2026-02-27                 # 先小段試跑
#   ./scripts/backfill_range.sh 2024-01-02 2026-02-27 --rebuild-from 2026-03-02
#   ./scripts/backfill_range.sh 2024-01-02 2026-02-27 --dry-run       # 只列交易日
#   選項：--sleep 秒（每日間隔，預設 8）、--skip-prep（略過 1、2 步）、--fg（前景執行）
# 進度：tail -f ~/.twchip_backfill/run.log
#
set -u
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SELF")/.." || exit 1

STATE="${HOME}/.twchip_backfill"
LOG="$STATE/run.log"
mkdir -p "$STATE"

usage() { sed -n '/^# 用法/,/^# 進度/p' "$0"; exit 1; }
[ $# -ge 2 ] || usage

START="$1"; END="$2"; shift 2
SLEEP=8
REBUILD_FROM=""
DRY_RUN=0
SKIP_PREP=0
FG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --sleep) SLEEP="$2"; shift 2 ;;
    --rebuild-from) REBUILD_FROM="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; FG=1; shift ;;
    --skip-prep) SKIP_PREP=1; shift ;;
    --fg) FG=1; shift ;;
    *) echo "未知選項：$1"; usage ;;
  esac
done

for d in "$START" "$END" ${REBUILD_FROM:+"$REBUILD_FROM"}; do
  date -d "$d" +%F >/dev/null 2>&1 || { echo "日期格式錯誤：$d（需 GNU date）"; exit 1; }
done

# 預設背景執行（SSH 斷線不中斷），log 寫檔。
if [ "$FG" -eq 0 ] && [ -z "${_TWCHIP_BF_CHILD:-}" ]; then
  args=("$START" "$END" --sleep "$SLEEP")
  [ -n "$REBUILD_FROM" ] && args+=(--rebuild-from "$REBUILD_FROM")
  [ "$SKIP_PREP" -eq 1 ] && args+=(--skip-prep)
  _TWCHIP_BF_CHILD=1 nohup "$SELF" "${args[@]}" >> "$LOG" 2>&1 &
  echo "已在背景啟動（PID $!），進度：tail -f $LOG"
  exit 0
fi

ts() { TZ=Asia/Taipei date '+%F %T'; }
api() { docker compose exec -T api python -m "$@"; }
psql_q() { docker compose exec -T db psql -U twchip -d twchip -Atc "$1"; }

# EOD 排程（16:00）與信用補抓（21:00）時段暫停，避免與排程同時打外部來源、同時寫庫。
# 兩者都只在週一~五執行（scheduler day_of_week=mon-fri），週末不必讓。
wait_off_peak() {
  while :; do
    [ "$(TZ=Asia/Taipei date +%u)" -ge 6 ] && return
    hm=$((10#$(TZ=Asia/Taipei date +%H%M)))
    if { [ "$hm" -ge 1550 ] && [ "$hm" -lt 1650 ]; } || { [ "$hm" -ge 2050 ] && [ "$hm" -lt 2120 ]; }; then
      echo "[$(ts)] EOD 排程時段，暫停 5 分鐘"
      sleep 300
    else
      return
    fi
  done
}

RETRIES=3
RETRY_SLEEP=30

# daily 對非核心來源 fail-soft（結束碼 0），外部端點偶發回 0 筆也不拋錯——
# 實測 2026-02-09 上櫃行情 0 筆、02-06 上櫃融資券 0 筆，當日橫斷面因此缺整個上櫃。
# 故以「匯入完成」那行的筆數判定：核心來源任一為 0 即視為不完整、重試。
check_complete() {
  local line
  line=$(grep -o '匯入完成.*' "$1" | head -1)
  [ -n "$line" ] || { echo "無匯入完成紀錄"; return 1; }
  local zero=()
  for k in price institutional margin sbl; do
    [ "$(echo "$line" | sed -nE "s/.*[ ：]$k=([0-9]+).*/\1/p")" = "0" ] && zero+=("$k")
  done
  local tpx
  tpx=$(echo "$line" | sed -nE 's/.*tpex=([0-9]+)\/([0-9]+)\/([0-9]+).*/\1 \2 \3/p')
  read -r t_price t_inst t_margin <<< "$tpx"
  [ "${t_price:-0}" = "0" ] && zero+=("tpex行情")
  [ "${t_inst:-0}" = "0" ] && zero+=("tpex法人")
  [ "${t_margin:-0}" = "0" ] && zero+=("tpex融資券")
  if [ ${#zero[@]} -gt 0 ]; then
    echo "0 筆：${zero[*]}"
    return 1
  fi
}

PREP_START=$(date -d "$START -4 month" +%F)
echo "=== [$(ts)] 回補 $START ~ $END（sleep=${SLEEP}s，prep 起點 $PREP_START）==="

if [ "$SKIP_PREP" -eq 0 ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "--- [$(ts)] 1/4 TAIEX ---"
  # --max-days 0：只抓 FMTQIK 並匯入 TAIEX，不抓個股行情（個股由第 3 步逐日補）。
  api scripts.backfill_history --start "$PREP_START" --end "$END" --max-days 0 \
    || { echo "TAIEX 失敗，中止（沒有大盤資料，交易日曆與大盤脈絡都會缺）"; exit 1; }
  echo "--- [$(ts)] 2/4 公司行動 ---"
  api scripts.backfill_corporate_actions "$PREP_START" "$END" \
    || echo "[warn] 公司行動回補有錯，續跑（該段除權息可能未還原，事後可單獨重跑）"
fi

# 交易日曆取自 market_index（TAIEX），避開國定假日；dry-run 且尚未匯入時為空。
DAYS=$(psql_q "select data_date from market_index where data_date between '$START' and '$END' order by 1")
TOTAL=$(echo "$DAYS" | grep -c . || true)
touch "$STATE/done.txt" "$STATE/failed.txt"
DONE_N=0
[ "$TOTAL" -gt 0 ] && DONE_N=$(grep -cxF -f <(echo "$DAYS") "$STATE/done.txt" || true)
echo "交易日 $TOTAL 天，已完成 $DONE_N 天"

if [ "$DRY_RUN" -eq 1 ]; then
  [ "$TOTAL" -eq 0 ] && echo "（market_index 尚無此區間 TAIEX，先不帶 --dry-run 跑一次才會有交易日曆）"
  echo "$DAYS" | head -5; [ "$TOTAL" -gt 5 ] && echo "... 共 $TOTAL 天"
  exit 0
fi

echo "--- [$(ts)] 3/4 逐日匯入 + 特徵 + 分數 ---"
i=0
for d in $DAYS; do
  i=$((i + 1))
  if grep -qxF "$d" "$STATE/done.txt"; then
    continue
  fi
  wait_off_peak
  t0=$(date +%s)
  ok=0
  for attempt in $(seq 1 "$RETRIES"); do
    if api app.jobs.daily "$d" > "$STATE/last_day.log" 2>&1; then
      if reason=$(check_complete "$STATE/last_day.log"); then
        ok=1; break
      fi
    else
      reason="exit≠0：$(tail -1 "$STATE/last_day.log")"
    fi
    echo "[$(ts)] [$i/$TOTAL] $d 第 ${attempt}/${RETRIES} 次不完整（$reason）"
    [ "$attempt" -lt "$RETRIES" ] && sleep $((RETRY_SLEEP * attempt))
  done
  summary=$(grep -o '匯入完成.*' "$STATE/last_day.log" | head -1)
  if [ "$ok" -eq 1 ]; then
    echo "$d" >> "$STATE/done.txt"
    sed -i "/^$d\$/d" "$STATE/failed.txt"
    echo "[$(ts)] [$i/$TOTAL] $d OK ($(( $(date +%s) - t0 ))s) $summary"
  else
    grep -qxF "$d" "$STATE/failed.txt" || echo "$d" >> "$STATE/failed.txt"
    echo "[$(ts)] [$i/$TOTAL] $d FAIL：$reason"
  fi
  sleep "$SLEEP"
done

if [ -n "$REBUILD_FROM" ]; then
  echo "--- [$(ts)] 4/4 重建 $REBUILD_FROM 起的分數 ---"
  wait_off_peak
  api scripts.rebuild_signals --start "$REBUILD_FROM" --min-lookback 0 \
    || echo "[warn] 重建失敗，可單獨重跑：docker compose exec -T api python -m scripts.rebuild_signals --start $REBUILD_FROM --min-lookback 0"
fi

FAILED=$(grep -c . "$STATE/failed.txt" || true)
echo "=== [$(ts)] 完成。失敗 $FAILED 天（見 $STATE/failed.txt；原指令重跑即只補未完成的日子）==="
