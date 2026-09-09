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

5/20/60 日視窗與背離需要歷史，補約 60+ 個交易日。用 `scripts/backfill.sh`（對過去 N
天內的每個平日跑一次 EOD，冪等）：

```bash
# 建議在 tmux 內跑(會跑一陣子;斷線也不中斷)
tmux new -s backfill
docker compose exec api ./scripts/backfill.sh 90     # 往前 90 天(約 64 交易日)
# Ctrl-b 再按 d 離開;tmux attach -t backfill 回來看進度
```

> 用 `exec`（進已在跑的 api 容器）比 `run --rm`（每天開新容器、還重跑一次 alembic）快很多。

TDCC openapi 只給當週快照、無法回補，會隨排程逐週累積（≥2 週後才有 week-over-week
變化）。逐筆 tick 同理，每交易日累積。

## 5. 對外存取：主機 nginx 同源代理 + TLS

> **不用公開網域、改用 Tailscale 跳板？** 直接看 [tailscale.md](tailscale.md)（`tailscale serve`
> 自動 HTTPS、免網域免 certbot），略過本節；`NEXT_PUBLIC_API_BASE` 改用 ts.net 名稱。

`api`/`web` 只綁 `127.0.0.1:8000/3000`，對外靠主機 nginx：

```bash
sudo apt update && sudo apt install -y nginx        # 若尚未安裝
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

## 升級流程（SOP）

`git pull` 之後要做哪幾步，取決於改了什麼。**多做無害、少做會留下不一致的資料**。

| 改動範圍 | 需要的步驟 |
|---|---|
| 只改前端 | `docker compose up -d --build web` |
| 改後端程式碼 | `docker compose up -d --build api` |
| 改 `config/thresholds.yaml`（權重／門檻） | 同上——**config 烤在映像內，只 restart 不會生效** |
| 改 `feature_builder` / `analysis` / 評分邏輯 | 上一項 + **全量重建** `rebuild_signals` |
| 新增 migration | 上述 + 起動時 entrypoint 自動 `alembic upgrade head`（不必手動） |
| 新資料源／回補腳本 | **先補資料，最後才重建**（見下方順序鐵則） |

### 順序鐵則

```
抓/補資料  →  重建特徵與分數  →  驗收
```

反過來做，重建會用到還沒補進來的資料，等於白跑一次。回補腳本彼此獨立、都是冪等的，
可以重複執行；`rebuild_signals` 也是冪等的，但**很花時間**（130 個交易日約 6～8 分鐘），
所以放到最後一次做完。

**不要在盤中回補**：TWSE 當日報表在收盤結算前只有半套（部分上市個股、無上櫃、無法人、
無借券），匯進去會讓當日橫斷面失真，盤中量還會污染其後 20 天的 `avg_vol20`。
`backfill_history` / `backfill_sbl` 已用 `availability_for`（盤後 15:00）擋掉，但
其他手動指令仍要自己注意。

### 驗收

```bash
# 1. 容器內實際跑的版本（改 config 後最容易忘記 --build，用這個確認）
docker compose exec -T api python -c \
  "from app.core.config import get_thresholds; print(get_thresholds().weights)"

# 2. 各資料源的涵蓋（天數應一致，最新日期應為最後一個交易日）
docker compose exec -T db psql -U twchip -d twchip -c "
select 'price' t, count(distinct data_date) d, min(data_date), max(data_date) from daily_price
union all select 'sbl',     count(distinct data_date), min(data_date), max(data_date) from sbl_daily
union all select 'feature', count(distinct data_date), min(data_date), max(data_date) from feature_daily
union all select 'signal',  count(distinct data_date), min(data_date), max(data_date) from signal_snapshot;"

# 3. 大盤脈絡有沒有落後（三個日期應該相同）
docker compose exec -T db psql -U twchip -d twchip -c "
select (select max(data_date) from market_index) idx,
       (select max(data_date) from market_daily) mkt,
       (select max(data_date) from daily_price)  px;"

# 4. 當日分數分布（百分位映射下 ≥75 應恰為總數的 25%）
docker compose exec -T db psql -U twchip -d twchip -c "
select data_date, count(*) n, count(*) filter (where chip_score>=75) ge75,
       count(*) filter (where action='BUY') buy
from signal_snapshot where data_date=(select max(data_date) from signal_snapshot)
group by 1;"

# 5. 逐筆覆蓋造成的分數偏差（有逐筆與無逐筆兩組的 ≥75 佔比都應接近 25%）
docker compose exec -T api python -m scripts.diag_intraday_bias
```

第 4 項的 `ge75` 若不是總數的 25%，表示 `signal_snapshot` 是舊版程式產生的，重跑
`rebuild_signals`。第 5 項兩組佔比差很多，表示映像沒更新到分組映射那版。

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

## 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `failed to resolve host 'xxx@db'` | `POSTGRES_PASSWORD` 含 URL 保留字元被塞進 URL。已改用 `PGPASSWORD`，`git pull` 後 `docker compose up -d` 即可。 |
| `password authentication failed for user "twchip"` | **`pgdata` volume 是舊密碼**。`POSTGRES_PASSWORD` 只在 volume 首次初始化時生效；改密碼後舊 volume 不會更新。DB 尚無資料時：`docker compose down -v && docker compose up -d`（重建 volume）。**已有資料**則改用 `ALTER ROLE twchip PASSWORD '...'` 對齊，勿 `-v`。 |
| api 一直 restart | 多半是上述 DB 連線問題。`docker compose logs api` 看 entrypoint 的 alembic 錯誤。 |
| 改了 `.env` 密碼沒生效 | 同上，postgres 密碼綁在 volume；需 `down -v` 重建或 `ALTER ROLE`。 |
