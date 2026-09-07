#!/usr/bin/env bash
#
# 生產資料升級一鍵版:背景執行 prod_data_upgrade(TPEx 回填 + 全量重建)。
# 避免長指令貼上斷行踩雷(nohup/redirect 都在腳本內處理好)。
#
# 用法(vultr 主機上):
#   ./scripts/upgrade_prod.sh              # 兩階段都跑
#   ./scripts/upgrade_prod.sh --skip-tpex  # 只重建
# 進度:tail -f /tmp/prod_upgrade.log
#
set -u
cd "$(dirname "$0")/.."

LOG=/tmp/prod_upgrade.log
nohup docker compose exec -T api python -m scripts.prod_data_upgrade "$@" > "$LOG" 2>&1 &
echo "已在背景啟動(PID $!),進度:tail -f $LOG"
