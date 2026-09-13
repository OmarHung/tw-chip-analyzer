# SOP：線上資料拉回本機重建，再推回線上

**用途**：線上主機只有 1 vCPU，全量重建（`rebuild_signals`）會佔滿 CPU 十幾分鐘、拖慢網站。
改在本機（多核心）重建，只把結果表推回線上。

**適用時機**：改了 `weights.*`、`features.*`、`feature_builder` / `analysis` 評分邏輯後的全量重建。
只改決策門檻（`entry_filter.*`、`risk.*`、`signal.*`）**不需要重建**。

**會動到的線上資料**：只覆寫 `feature_daily`、`signal_snapshot`、`market_daily` 三張表。
原始資料（行情、法人、融資、借券、TDCC、逐筆、公司行動）不動。

> 以下以 Docker 部署為例（`<專案目錄>` 換成線上實際路徑）。systemd 部署把
> `docker compose exec -T db` 拿掉、`-U twchip` 視連線方式調整即可。

---

## 0. 事前檢查（兩邊都做）

| 檢查 | 指令 | 必須 |
|---|---|---|
| 程式版本一致 | 兩邊 `git log --oneline -1` | **完全相同的 commit**。不同版本算出的分數推回線上會混用兩套算法 |
| config 一致 | 兩邊 `git diff --stat config/` | 都沒有未 commit 的門檻修改 |
| 線上沒有工作在跑 | 系統頁回補區狀態為閒置，或 `curl -s localhost:8000/api/ops/status` 看 `job.state` | 不是 `running` |
| 避開 EOD | 排程為平日 14:30（`config/thresholds.yaml` `schedule.eod`） | 整個流程**不要跨過 EOD 時段**（見「例外處理」） |

---

## 1. 線上匯出完整 DB

```bash
ssh <線上主機>
cd <專案目錄>
docker compose exec -T db pg_dump -U twchip -Fc twchip > /tmp/twchip_prod.dump
ls -lh /tmp/twchip_prod.dump
```

- **不可排除 `raw_tick`**：`feature_builder` 用逐筆算盤中特徵，少了它本機重建出來全部沒有 intraday 成分，推回去會洗掉線上的盤中分數。
- `raw_tick` 上千萬列，檔案會很大；`-Fc` 已壓縮。磁碟不夠時改輸出到有空間的路徑。

## 2. 拉回本機

```bash
scp <線上主機>:/tmp/twchip_prod.dump ~/Downloads/
ssh <線上主機> rm /tmp/twchip_prod.dump     # 線上不留副本
```

## 3. 還原到本機獨立資料庫

```bash
cd ~/tw_chip_analyzer
dropdb --if-exists twchip_prod
createdb twchip_prod
pg_restore --no-owner --no-privileges -j 8 -d twchip_prod ~/Downloads/twchip_prod.dump
```

- `--no-owner --no-privileges`：線上角色是 `twchip`、本機是 `omar`，不加會一路報權限錯誤。
- `-j 8`：平行還原（本機 12 核）。
- 線上 PG 16 產生的 dump，本機 PG 17 的 `pg_restore` 讀得了。

升級 schema（線上已是同版時是 no-op，跑了無害）：

```bash
DATABASE_URL=postgresql+psycopg://omar@localhost:5432/twchip_prod \
  APP_ENV=dev .venv/bin/alembic upgrade head
```

## 4. 本機重建

```bash
DATABASE_URL=postgresql+psycopg://omar@localhost:5432/twchip_prod \
  APP_ENV=dev .venv/bin/python -m scripts.rebuild_signals
```

- 用環境變數指定 DB，**不必改 `.env`**，也不會碰到本機原本的 `twchip`。
- 只跑腳本、不啟動 API，所以本機的 EOD 排程不會觸發、不耗 Shioaji 配額。
- 參數與線上預設一致（`--min-lookback 20`）。只重建部分日期時加 `--start` / `--end`。

### 本機驗收（推回前必做）

```bash
DATABASE_URL=postgresql+psycopg://omar@localhost:5432/twchip_prod \
  APP_ENV=dev .venv/bin/python -m scripts.score_monotonicity
```

```sql
-- psql twchip_prod
-- (a) 每日訊號筆數沒有異常掉落
select data_date, count(*) from signal_snapshot group by 1 order by 1 desc limit 10;

-- (b) 有逐筆的日子 intraday 成分仍在（查無任何一列 = raw_tick 沒還原到，停止！）
select data_date, count(*) filter (where cvd_z is not null) as with_intraday
from feature_daily group by 1
having count(*) filter (where cvd_z is not null) > 0
order by 1 desc limit 5;
```

## 5. 匯出結果表並推回線上

### 5-1 本機產生匯入檔（純 SQL，含清空與匯入，單一交易）

```bash
{
  echo "TRUNCATE feature_daily, signal_snapshot, market_daily;"
  pg_dump --data-only --no-owner --no-privileges \
    -t feature_daily -t signal_snapshot -t market_daily twchip_prod \
  | grep -v '^SET transaction_timeout'
  echo "SELECT setval(pg_get_serial_sequence('feature_daily','id'),   coalesce(max(id),1)) FROM feature_daily;"
  echo "SELECT setval(pg_get_serial_sequence('signal_snapshot','id'), coalesce(max(id),1)) FROM signal_snapshot;"
  echo "SELECT setval(pg_get_serial_sequence('market_daily','id'),    coalesce(max(id),1)) FROM market_daily;"
} | gzip > ~/Downloads/rebuilt.sql.gz
ls -lh ~/Downloads/rebuilt.sql.gz
```

為什麼這樣做：

- **用純 SQL 而非 `-Fc`**：本機 `pg_dump` 17 的 custom 格式，線上 `pg_restore` 16 **讀不了**。
- **`grep -v transaction_timeout`**：PG 17 的 dump 會帶 `SET transaction_timeout`，PG 16 不認得；單一交易中任何錯誤都會讓整批回滾。
- **TRUNCATE 與匯入同一交易**：中途失敗整批回滾，線上不會出現「表被清空但沒匯入」的半套狀態。
- **setval**：修正自增序號，避免之後 EOD 寫入時主鍵衝突。

### 5-2 推上線並匯入

```bash
scp ~/Downloads/rebuilt.sql.gz <線上主機>:/tmp/
ssh <線上主機>
cd <專案目錄>

# 再確認一次：沒有工作在跑、還沒到 EOD
curl -s localhost:8000/api/ops/status | grep -o '"state":"[a-z]*"'

gunzip -c /tmp/rebuilt.sql.gz \
  | docker compose exec -T db psql -U twchip twchip \
      --single-transaction -v ON_ERROR_STOP=1
```

- 結尾沒有 `ERROR` 即為成功；有錯誤時整批已回滾，線上資料維持原狀。
- 匯入期間三張表被鎖住，網站相關頁面會短暫等待（COPY 很快，通常數十秒內）。

### 5-3 重啟 API（部署保險，非必要）

```bash
docker compose restart api
rm /tmp/rebuilt.sql.gz
```

驗證頁快取依三表 `max(updated_at)` 自動失效，同筆覆寫也偵測得到（docs/12 Phase 5）；
背離掃描與總覽的記憶體快取以日期為 key，重啟可確保一次清乾淨，但不再是得到正確結果的必要條件。

## 6. 線上驗收

```bash
docker compose exec -T db psql -U twchip twchip -c \
  "select max(data_date), count(*) from signal_snapshot;"
```

- 筆數、最新日期與本機第 4 步一致
- `/validation` 頁：IC / NW t 與本機跑出來的一致
- 總覽、選股頁：正常顯示

## 7. 收尾

```bash
dropdb twchip_prod                    # 本機副本（含完整線上資料），用完刪除
rm ~/Downloads/twchip_prod.dump ~/Downloads/rebuilt.sql.gz
```

---

## 例外處理

| 狀況 | 處理 |
|---|---|
| 流程途中線上跑了 EOD（多了一天本機沒有的資料） | 匯入會把那天的結果清掉。匯入後在線上補跑當天：`docker compose exec api python -m app.jobs.daily YYYY-MM-DD --skip-import` |
| 第 0 步發現 commit 不同 | 先讓兩邊同版（線上 `git pull && docker compose up -d --build`），再從第 1 步開始 |
| 第 4 步驗收 (b) 查無資料 | 第 1 步的 dump 沒含 `raw_tick` 資料，重做第 1 步 |
| 5-2 出現 `ERROR` | 已整批回滾，線上不受影響；把錯誤訊息貼出來排查 |
| 想回到推回前的狀態 | 第 1 步的 dump 就是推回前的完整備份（第 7 步前別刪）。只還原三張表時，要在**線上容器內**用 PG 16 的 `pg_restore --data-only -t feature_daily -t signal_snapshot -t market_daily`，執行前先 TRUNCATE 這三張表 |

## 注意

- 本機的 `twchip_prod` 與 dump 檔是**線上資料的完整副本**，用完即刪。
- 本流程不需要、也不應該在本機啟動 API（`./scripts/dev.sh`）連 `twchip_prod`。若要啟動測試，先把 `config/thresholds.yaml` 的 `schedule.enabled` 暫改 `false`（不要 commit），避免本機 EOD 耗用線上共用的 Shioaji 配額。
- 重建後的歷史 snapshot 屬「樣本內回測」（算法是看過這段資料後修的）；誠實的前瞻 OOS 從重建之後每日 EOD 新寫入的資料開始累積。
- 目前 `rebuild_signals` 是逐日依序執行；本機多核心的加速（`--workers`）尚未實作。
- 線上也可從系統頁「工作 → 重建特徵與分數」按鈕觸發（子行程、nice 降優先權），但 1 vCPU 期間網站會變慢；
  大規模重建仍建議走本 SOP。
