# CLAUDE.md — tw_chip_analyzer

台股籌碼分析與進出場建議系統。整合盤中 order flow、盤後法人/信用/借券、週度 TDCC 集中度，輸出 Chip Score（0~100）與 BUY / WATCH / HOLD / REDUCE / EXIT / AVOID 建議，含進場區間、停損、TP1/TP2、RR 與原因說明。

- 語言 Python 3.12+ · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · asyncio · pandas/numpy/scipy
- DB：PostgreSQL 16（可選 TimescaleDB）· 前端：Next.js 16 + TypeScript
- **目前階段：Phase 1（Daily Chip Scanner），rule-based，尚未接 realtime / ML。**

> 這份檔案只放「從程式碼讀不出來的意圖與約束」。目錄／函式層級的細節請用 CodeGraph（`codegraph explore "..."`）或直接讀碼，別依賴此處的描述——它不保證與現況同步。

## 專案不變量（鐵則，任何實作都不得違反）

1. **只分析、不下單**：第一階段禁止直接串自動下單。
2. **籌碼與時機分離**：Chip Score 高 ≠ 可進場；價格過度延伸 / RR < 2 一律輸出 WATCH。BUY 必須通過 Entry Filter（`ChipScore≥75 AND RR≥2 AND 不過度偏離 VWAP/MA20 AND Market≠Strong Bear AND 流動性足 AND 非鎖死漲停`）。
3. **不把單一訊號當大戶證據**：大單 / OBI / 外資買超 / TDCC 增加，單一皆不可當結論。需多個獨立訊號 + 價格結構 + 風險報酬交叉驗證。
4. **所有 threshold 必須 config 化**：signal / risk / large_trade / tdcc 級距等，禁止硬編碼（含排程時間、交易成本）。見 `docs/07-ops-config.md`。
5. **禁止 magic number**：Large Trade 用 rolling quantile（≥500 樣本）非固定 100 張；停損用 ATR 非固定 %；TP 用 R 倍數非固定 +5%/+10%。
6. **跨股票不可直接比張數**：一律先做 normalization（Z-score / percentile / turnover / 量比）。
7. **Aggressor UNKNOWN 不可強制歸類**（1=BUY / -1=SELL / 0=UNKNOWN）。
8. **Look-ahead bias 防護**：每筆資料存 `data_date` + `available_at`，backtest 依 `available_at` 判斷可用性。禁止提前使用法人/TDCC/當日收盤資料。
9. **Backtest 是實盤前必要條件**：未有 backtest 證據前，禁止聲稱策略有效。含交易成本，禁止 0 成本回測。
10. **自營商必須拆兩類**（自行買賣 / 避險）。
11. **Realtime 效能**：Shioaji callback 內禁止 heavy DB / pandas / HTTP / scoring；Tick 寫入必須 batch（100~1000 rows 或 100~500ms flush），禁止每 tick commit。

## 成功標準（第一階段）

不是功能數量，而是：**Chip Score 越高，未來報酬有統計上單調改善，且 MAE 不惡化。** 若無此現象，優先調整 feature / normalization / weight，不要加新功能。

Milestone 交付格式（已完成 / migration / API / 測試 / 技術債 / 下一步 / look-ahead 檢查 / 資料缺漏 / backtest…）見 `docs/08-roadmap-delivery.md §30`。

## 現況

已完成 docs/01–06 全部（骨架、演算法、評分、決策、API、Backtest）、docs/05 §16 Next.js UI，可吃真實台股盤後資料端到端運作。真實資料涵蓋 OHLCV + institutional + margin（TWSE）+ TDCC 股權分散 + TAIEX 大盤 regime；四大分項中 institutional / holder / market 為真實資料。已對 2026-09-04 全市場（~1080 檔）驗證分層正常。intraday 分項已可併入全市場 composite（有逐筆的標的走四維，無者三維排除）。

**已知限制 / 待辦（會影響決策，讀不出來的部分）**：
- **BUY(75) 仍偏難達，且補齊 intraday 未使其更易達**：intraday 6 個成分已全數接線（absorption / trade_speed / price_efficiency 於 2026-09-06 由當日 tick 算出橫斷面 z，見 `orderflow_intraday._bar_absorption/_bar_trade_speed` 與 `orderflow/price_efficiency.py`）。**經驗發現**：補齊後 2026-09-04 全市場最高 chip_score 由 73.7 **降至 71.0**——新三成分對當時頂部標的偏中性/偏弱，反而稀釋，故「補齊 intraday 能更易出 BUY」的原假設不成立。要更易出 BUY 只剩於 config 調降 `signal.buy_score`——屬校準取捨，勿擅自更動。intraday 是否真有 alpha 仍待累積多日 tick 後回測驗證。
- **TDCC holder 為橫斷面 level proxy**：openapi 僅當週快照，無法算 week-over-week change，暫以當週集中度橫斷面 Z-score 代替；累積 ≥2 週後改真實 change。`available_at` 現設快照日盤後（demo 對齊），生產應 lag 至揭露日。
- **industry_trend 仍中性**：OHLCV importer 未帶產業別。
- 尚未做：SBL importer、TPEx connector、真實 TDCC change、產業別/趨勢、Shioaji realtime、走勢圖時序 API、intraday 併入 backtest 驗證單調性（需先累積多日 tick）。

## 頂層地圖

細節用 CodeGraph 或讀碼；完整建議結構見 `docs/01-overview-architecture.md §5`。

- `app/core/`：`config.py`（env 用 pydantic-settings；門檻用 `config/thresholds.yaml`）、`logging.py`
- `app/db/`：`models/`（10 張表，`mixins.py` 含 look-ahead `data_date`/`available_at`）；`intraday.py` 有 `RawTick`
- `app/services/orderflow/`：aggressor / cvd / large_trade / obi / absorption / trade_speed（純函式）
- `app/services/chip/`：intraday / institutional / holder / market 分項 + `composite.py`（config 驅動權重 + 缺成分重分配）
- `app/services/decision/`：`risk` / `entry` / `exit` + `decide()`
- `app/services/`：`feature_builder.py`（原始表→`feature_daily`，兩段正規化，look-ahead 只用 `data_date<=target`）、`market_score.py`、`normalize.py`、`analysis.py`、`market_scan.py`、`orderflow_intraday.py`、`ticks.py`
- `app/api/`：`stocks`（`/analysis`、`/chart`、`/ticks`、`/orderflow`）、`scanner`、`dashboard`；CORS 允許任意 localhost 埠（見 `main.py`）
- `app/backtest/`：`costs`（禁 0 成本）、`forward_returns`、`metrics`、`engine`（look-ahead 安全）、`runner`
- `app/connectors/`：`twse` / `tdcc` / `yahoo`（圖表用）/ `shioaji_market`（逐筆 ticks，`simulation=True` 單例；金鑰無 production 權限但模擬可取真實行情）
- `app/importers/`：TWSE/TDCC parser + `service.py`（冪等 upsert）；`app/repositories/upsert.py` 用 PG `on_conflict`
- `app/jobs/`：`daily.py`（抓取→匯入→建特徵→大盤脈絡）、`import_ticks.py`（批次逐筆）
- `frontend/`：Next.js 16 + TS + Tailwind v4。設計約束見下節。UI 規格見 `docs/05-api-ui.md §16`。
- `tests/`：88 passed。`tests/fixtures/` 有 TWSE 真實回應切片供 parser 測試不打網路。

**逐筆特別注意**：Shioaji tick ts 為 ns，以 UTC 解讀即台北牆鐘（用 `utcfromtimestamp`）。批次逐筆要先跑 `import_ticks` 再跑 `daily --skip-import`，intraday z 才會進 `feature_daily`。

**前端設計約束（改前端必守）**：**台股語意紅漲綠跌**（與美股相反，見 `lib/format.ts` 的 `dirColor`/`Change`）、**無斜體**。「Terminal Luxe」風格：Fraunces(display)/JetBrains Mono(數字)/Noto Sans TC(中文)、招牌琥珀金、深炭黑底。`lib/api.ts` 為型別化 client（`NEXT_PUBLIC_API_BASE`）。

## 環境與指令

- DB：本機 PostgreSQL，開發庫 `twchip`、測試庫 `twchip_test`（角色 `omar`，見 `.env`）。測試以 `APP_ENV=test` 走測試庫，`conftest.py` 每 test 重建 schema。
- 安裝：`pip install -r requirements.txt`
- Migration：`alembic upgrade head`（新增 model 後 `alembic revision --autogenerate -m "..."`）
- 測試：`python -m pytest`
- 啟動：`APP_ENV=dev uvicorn app.main:app --reload`；Swagger 於 `/docs`
- Seed / Backtest 示範：`python -m scripts.seed_dev`、`python -m scripts.backtest_demo`（前綴 `APP_ENV=dev`）
- 每日盤後匯入 + 建特徵：`APP_ENV=dev python -m app.jobs.daily 2026-09-04`（需先匯入約 10 個交易日歷史，5/20 日視窗才有意義）；`--tdcc` 抓當週 TDCC；`--index` 抓近 4 個月 TAIEX 建大盤脈絡。批次逐筆：`APP_ENV=dev python -m app.jobs.import_ticks YYYY-MM-DD [--max-symbols N]`。
- 前端：`cd frontend && pnpm install && pnpm dev`（:3000）。dev 模式 HMR websocket 在本沙箱會卡住 hydration；驗證用 `pnpm build && pnpm start`。

## 文件導覽（`docs/`，索引 `docs/README.md`）

- 架構/目錄 → `01` · 資料源/表/Retention → `02` · Order Flow/Normalization/Score/Signal → `03`
- Entry/Risk/Exit → `04` · API/UI → `05` · Backtest/Look-ahead/Testing → `06` · 排程/Config → `07` · Roadmap/DoD → `08`

原始完整交接文件：`tw_stock_chip_analysis_coding_agent_handoff.md`（單一來源存檔）。
