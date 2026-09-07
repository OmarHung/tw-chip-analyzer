# 用 Docker 部署（推薦快速上手）

`docker compose` 起三個服務：`db`（PostgreSQL 16）、`api`（FastAPI，含內建 EOD 排程）、
`web`（Next.js）。對外由**主機 nginx** 反向代理做同源與 TLS（免 CORS）。

```
瀏覽器 ─HTTPS→ 主機 nginx ┬ /      → web  容器 127.0.0.1:3000
                          └ /api/* → api  容器 127.0.0.1:8000
                                        db 容器(pgdata volume) ◄┘
              api 容器內建 APScheduler → 週一–五 14:30(Asia/Taipei) 自動 EOD
```

相關檔：根目錄 `Dockerfile`、`docker-entrypoint.sh`（啟動前自動 `alembic upgrade head`）、
`frontend/Dockerfile`、`docker-compose.yml`、`deploy/docker/env.example`。

> 與 [systemd 版](systemd.md) 二選一。Docker 較省事、環境隔離好；systemd 較貼近主機、無容器負擔。

---

## 1. 安裝 Docker

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 nginx
sudo usermod -aG docker $USER && newgrp docker
```

## 2. 取碼 + 環境變數

```bash
git clone <你的 repo> tw_chip_analyzer && cd tw_chip_analyzer
cp deploy/docker/env.example .env        # compose 自動讀根目錄 .env
chmod 600 .env
# 編輯 .env：
#   POSTGRES_PASSWORD=強密碼
#   NEXT_PUBLIC_API_BASE=https://你的網域     # 公開網域，build 時烤入前端
```

`.env` 已被 gitignore，不會進版控。`NEXT_PUBLIC_API_BASE` 一定要設**公開網域**
（非 127.0.0.1）：瀏覽器同源、SSR 亦可連。改網域要重新 `docker compose build web`。

## 3. build + 啟動

```bash
docker compose up -d --build          # entrypoint 會自動跑 alembic upgrade head
docker compose logs -f api            # 看啟動與 EOD 日誌
```

> **Apple Silicon** 上 build 請加 `DOCKER_DEFAULT_PLATFORM=linux/amd64`——`shioaji`
> 只有 amd64 wheel。正式主機一般是 amd64，無此問題。

## 4. 灌初始歷史

5/20/60 日視窗與背離需要歷史，補約 60+ 個交易日：

```bash
for d in $(python3 - <<'PY'
import datetime as dt
d = dt.date(2026, 6, 1)
while d <= dt.date.today():
    if d.weekday() < 5:
        print(d)
    d += dt.timedelta(days=1)
PY
); do docker compose run --rm api ./scripts/eod.sh "$d"; done
```

TDCC openapi 只給當週快照、無法回補，會隨排程逐週累積（≥2 週後才有 week-over-week
變化）。逐筆 tick 同理，每交易日累積。

## 5. 主機 nginx 同源代理 + TLS

`api`/`web` 只綁 `127.0.0.1:8000/3000`，對外靠主機 nginx：

```bash
sudo cp deploy/nginx/twchip.conf /etc/nginx/sites-available/twchip
sudo sed -i 's/__DOMAIN__/你的網域/g' /etc/nginx/sites-available/twchip
sudo ln -sf /etc/nginx/sites-available/twchip /etc/nginx/sites-enabled/twchip
sudo nginx -t && sudo systemctl reload nginx
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的網域         # 自動補 443/轉址
```

## 6. 驗證

```bash
docker compose ps
curl -s https://你的網域/api/dashboard | head -c 300
docker compose run --rm api ./scripts/eod.sh 2026-09-04   # 手動觸發一次 EOD
```

---

## 維運

| 動作 | 指令 |
|---|---|
| 看日誌 | `docker compose logs -f api` |
| 停 / 起 | `docker compose down` / `docker compose up -d` |
| 更新程式 | `git pull && docker compose up -d --build` |
| 改門檻 `config/thresholds.yaml` | `docker compose restart api`（config 走 bind？預設烤在映像內，改碼需 rebuild） |
| 改網域／前端 | 改 `.env` → `docker compose up -d --build web` |
| 補跑某日 EOD | `docker compose run --rm api ./scripts/eod.sh 2026-09-04` |
| DB 備份 | `docker compose exec db pg_dump -U twchip twchip > backup.sql` |
| DB 還原 | `docker compose exec -T db psql -U twchip twchip < backup.sql` |

> 註：`config/thresholds.yaml` 與程式碼一起 COPY 進映像。要改門檻，最簡單是改檔後
> `docker compose up -d --build api`；或在 compose 為 `api` 掛 bind mount
> `./config:/app/config` 讓改檔免 rebuild（重啟即生效）。

## 重點與陷阱

1. **EOD 排程**：`api` 容器內建 APScheduler（`config/thresholds.yaml` 的
   `schedule.enabled: true`），單容器＝單實例，每交易日 14:30（Asia/Taipei）自動跑，
   無重複跑問題，不需額外 cron 容器。
2. **同源免 CORS**：後端 `CORSMiddleware` 只放行 `localhost`（見 `app/main.py`）。
   靠主機 nginx 同源代理，勿讓瀏覽器跨網域直打後端。
3. **逐筆非必需**：不填 `SJ_*` 也能運作，只是 intraday 分項中性——法人買賣超、
   量價背離、主力估算成本等功能不受影響。
4. **資料持久化**：Postgres 在 named volume `pgdata`；`docker compose down` 保留、
   `down -v` 才會刪。務必定期 `pg_dump` 備份。
5. **shioaji 平台**：僅 amd64 wheel，跨平台 build 記得 `DOCKER_DEFAULT_PLATFORM=linux/amd64`。
