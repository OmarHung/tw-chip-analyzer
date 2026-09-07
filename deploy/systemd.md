# 用 systemd 部署（裸機）

不走容器，直接在 Ubuntu 主機用 Python venv + systemd 管理服務，nginx 同源代理。

```
瀏覽器 ─HTTPS→ nginx ┬ /      → Next.js  127.0.0.1:3000  (twchip-web.service)
                     └ /api/* → uvicorn  127.0.0.1:8000  (twchip-api.service)
                                   PostgreSQL 16 ◄┘
      systemd timer(週一–五 14:30 Asia/Taipei) → scripts/eod.sh  (twchip-eod.*)
```

相關檔：`systemd/twchip-{api,web,eod}.service`、`systemd/twchip-eod.timer`、
`nginx/twchip.conf`、`env.example`、`install.sh`。佔位符 `__DOMAIN__` /
`/home/twchip/tw_chip_analyzer` 由 `install.sh` 自動替換。

> 與 [Docker 版](docker.md) 二選一。

---

## 1. 系統套件

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip \
  postgresql postgresql-contrib nginx git curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs && sudo npm i -g pnpm
sudo timedatectl set-timezone Asia/Taipei      # 建議（EOD 時間對齊）
```

## 2. 專用帳號 + 取碼 + Python 環境

```bash
sudo useradd -m -s /bin/bash twchip
sudo -iu twchip
git clone <你的 repo> ~/tw_chip_analyzer && cd ~/tw_chip_analyzer
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. PostgreSQL

```bash
sudo -u postgres psql <<SQL
CREATE ROLE twchip LOGIN PASSWORD '換成強密碼';
CREATE DATABASE twchip OWNER twchip;
SQL
```

## 4. `.env`（專案根，權限 600）

```bash
cp deploy/env.example .env
# 編輯 .env：填 DATABASE_URL 密碼，APP_ENV=prod
chmod 600 .env
```

`prod` 與 `dev` 都吃 `DATABASE_URL`，只有 `test` 走 `TEST_DATABASE_URL`（見
`app/core/config.py`）。

## 5. 建表 + 灌初始歷史

```bash
APP_ENV=prod .venv/bin/alembic upgrade head

# 5/20/60 日視窗與背離需歷史。用 backfill.sh 補過去 N 天(約 64 交易日),冪等:
PY=.venv/bin/python APP_ENV=prod ./scripts/backfill.sh 90
```

TDCC openapi 只提供當週快照、無法回補，會隨排程逐週累積（≥2 週後才有 week-over-week
變化）。逐筆 tick 同理，每交易日累積。

## 6. 前端 build（API 網域烤進 bundle）

`NEXT_PUBLIC_API_BASE` 於 build 時決定，須先手動 build：

```bash
cd ~/tw_chip_analyzer/frontend
pnpm install
NEXT_PUBLIC_API_BASE=https://你的網域 pnpm build
```

設**公開網域**（非 127.0.0.1）：瀏覽器同源、SSR 亦可連。每次改網域都要重新 `pnpm build`。

## 7. 一鍵安裝服務

```bash
# 需 sudo。DOMAIN 必填；SCHED=timer(預設) 或 inproc
sudo DOMAIN=chip.example.com APP_DIR=/home/twchip/tw_chip_analyzer SCHED=timer \
  deploy/install.sh
```

`install.sh` 會：替換佔位符 → 佈署 4 個 systemd 單元與 nginx 設定 → 啟用
`twchip-api`/`twchip-web`（+ `twchip-eod.timer` 若 `SCHED=timer`）→ reload nginx。

### 排程二選一（勿同時開，會重複跑 EOD）

- **方案 B `SCHED=timer`（推薦）**：systemd timer 觸發 `eod.sh`；`Persistent=true`
  關機錯過會補跑。**務必**把 `config/thresholds.yaml` 的 `schedule.enabled` 設為
  `false`，避免 API 內建排程也跑一次。
- **方案 A `SCHED=inproc`**：用 API lifespan 內的 APScheduler（`schedule.enabled: true`）。
  最省事，但 API 必須**單 worker**（本專案 unit 已是），且 API 需常駐。

## 8. TLS 憑證

> **不用公開網域、改用 Tailscale 跳板？** 看 [tailscale.md](tailscale.md)（`tailscale serve`
> 自動 HTTPS、免網域免 certbot），略過本節與步驟 7 的 nginx 網域設定；`install.sh` 的
> `DOMAIN` 可隨意填，之後照 tailscale.md 把 nginx 改綁 `127.0.0.1:8080`。

```bash
sudo certbot --nginx -d 你的網域         # 自動加 443 區塊與轉址
```

## 9. 驗證

```bash
sudo systemctl status twchip-api twchip-web
systemctl list-timers | grep twchip                 # 下次 EOD 時間
curl -s https://你的網域/api/dashboard | head -c 300
sudo systemctl start twchip-eod.service && journalctl -u twchip-eod -f  # 手動觸發一次
```

---

## 維運

| 動作 | 指令 |
|---|---|
| 看後端日誌 | `journalctl -u twchip-api -f` |
| 看 EOD 日誌 | `journalctl -u twchip-eod -f` |
| 改門檻 `config/thresholds.yaml` | `sudo systemctl restart twchip-api` |
| 改 `NEXT_PUBLIC_API_BASE`／前端碼 | 重新 `pnpm build` → `sudo systemctl restart twchip-web` |
| 補跑某日 EOD | `APP_ENV=prod ./scripts/eod.sh 2026-09-04` |
| DB migration | `APP_ENV=prod .venv/bin/alembic upgrade head` |
| DB 備份 | `pg_dump -U twchip twchip > backup.sql` |

## 重點與陷阱

1. **單 worker**：`twchip-api` 勿加 `--workers>1`——APScheduler 在 lifespan 內，多
   worker 會重複跑 EOD。要橫向擴充就用方案 B（timer）並把 `schedule.enabled=false`。
2. **同源免 CORS**：後端 `CORSMiddleware` 只放行 `localhost`（見 `app/main.py`）。
   正式環境靠 nginx 同源代理，勿讓瀏覽器直接跨網域打後端；否則需改 CORS 設定。
3. **逐筆非必需**：不填 `SJ_*` 金鑰也能運作，只是 intraday 分項中性——法人買賣超、
   量價背離、主力估算成本等主力功能不受影響。
4. **時區**：EOD 於 14:30 Asia/Taipei。timer 已明寫時區；若用 cron 請確認主機時區。
5. **備份**：`pg_dump twchip` 定期備份；`.env` 含密碼，權限 600、勿進版控。
