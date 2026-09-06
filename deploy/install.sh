#!/usr/bin/env bash
#
# 安裝 systemd 單元 + nginx 設定到 Ubuntu 主機。
# 先完成 deploy/README.md 的步驟 1~5(套件/venv/DB/.env/建表灌史)再跑這支。
#
# 用法(需 sudo):
#   sudo DOMAIN=chip.example.com \
#        APP_DIR=/home/twchip/tw_chip_analyzer \
#        SCHED=timer \
#        deploy/install.sh
#
# 環境變數:
#   DOMAIN  必填,公開網域(供前端 build 與 nginx)
#   APP_DIR 專案路徑(預設 /home/twchip/tw_chip_analyzer)
#   SVC_USER 服務帳號(預設 twchip)
#   SCHED   排程方式:timer(systemd timer,預設) | inproc(用 API 內建 APScheduler)
#
set -euo pipefail

: "${DOMAIN:?請設 DOMAIN=你的網域}"
APP_DIR="${APP_DIR:-/home/twchip/tw_chip_analyzer}"
SVC_USER="${SVC_USER:-twchip}"
SCHED="${SCHED:-timer}"
SRC="$APP_DIR/deploy"

echo "== 套用設定 DOMAIN=$DOMAIN APP_DIR=$APP_DIR USER=$SVC_USER SCHED=$SCHED =="

render() {  # $1 來源 $2 目標
  sed -e "s#__DOMAIN__#$DOMAIN#g" \
      -e "s#/home/twchip/tw_chip_analyzer#$APP_DIR#g" \
      -e "s#^User=twchip#User=$SVC_USER#g" \
      -e "s#^Group=twchip#Group=$SVC_USER#g" \
      "$1" > "$2"
}

# --- systemd 單元 ---
render "$SRC/systemd/twchip-api.service" /etc/systemd/system/twchip-api.service
render "$SRC/systemd/twchip-web.service" /etc/systemd/system/twchip-web.service
render "$SRC/systemd/twchip-eod.service" /etc/systemd/system/twchip-eod.service
render "$SRC/systemd/twchip-eod.timer"   /etc/systemd/system/twchip-eod.timer

# --- nginx ---
render "$SRC/nginx/twchip.conf" /etc/nginx/sites-available/twchip
ln -sf /etc/nginx/sites-available/twchip /etc/nginx/sites-enabled/twchip

systemctl daemon-reload
systemctl enable --now twchip-api twchip-web

if [ "$SCHED" = "timer" ]; then
  echo "== 排程:systemd timer(記得把 config/thresholds.yaml 的 schedule.enabled 設 false)=="
  systemctl enable --now twchip-eod.timer
else
  echo "== 排程:API 內建 APScheduler(schedule.enabled 保持 true;不啟用 timer)=="
  systemctl disable --now twchip-eod.timer 2>/dev/null || true
fi

nginx -t && systemctl reload nginx
echo "== 完成。檢查: systemctl status twchip-api twchip-web；systemctl list-timers | grep twchip =="
