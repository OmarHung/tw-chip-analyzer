# CLAUDE.md — tw_chip_analyzer

台股籌碼分析與進出場建議系統。整合盤中 order flow、盤後法人/信用/借券、週度 TDCC 集中度，輸出 Chip Score（0~100）與 BUY / WATCH / HOLD / REDUCE / EXIT / AVOID 建議，含進場區間、停損、TP1/TP2、RR 與原因說明。

- 語言 Python 3.12+ · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · asyncio · pandas/numpy/scipy
- DB：PostgreSQL 16（可選 TimescaleDB）· 前端：Next.js 15 + TypeScript
- **目前階段：Phase 1（Daily Chip Scanner），rule-based，尚未接 realtime / UI / ML。**

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

## Milestone 交付格式

每個 milestone 須輸出：已完成 / DB migration / API 文件 / 測試結果 / 未完成 / 技術債 / 下一步 / look-ahead 檢查 / 資料缺漏 / Backtest 結果。詳見 `docs/08-roadmap-delivery.md §30`。

## 現況與目錄

已完成 docs/01–06 全部（骨架、演算法、評分、決策、API、Backtest）、docs/05 §16 Next.js UI，以及 TWSE 真實資料 importer + feature 計算 job（rule-based，含測試）。系統可吃真實台股盤後資料端到端運作。

真實資料涵蓋：OHLCV + institutional + margin（TWSE）+ TDCC 股權分散（holder）+ TAIEX 大盤 regime（market）。四大分項中 institutional / holder / market 皆為真實資料；已對 2026-09-04 全市場（~1080 檔）驗證，分數分層正常（avg 53.4、WATCH 62、AVOID 43），Dashboard 顯示真實 TAIEX/regime/漲跌家數。

**已知限制（重要）**：
- **BUY(75) 在 Phase 1 幾乎不可達**：排除 intraday（35% 權重）後只剩 3 成分，頂部約落在 74（2026-09-04 最高 3045=73.7）。要產生 BUY 需 intraday（Phase 3）或於 config 調降 `signal.buy_score`。屬校準取捨，未擅自更動。
- **TDCC holder 為橫斷面 level proxy**：openapi 僅當週快照，無法算 week-over-week change，暫以「當週大戶/散戶集中度的橫斷面 Z-score」代替；待累積 ≥2 週後改真實 change。available_at 目前設快照日盤後（demo 對齊），生產應 lag 至揭露日。
- **產業趨勢（industry_trend）仍中性**：OHLCV importer 未帶產業別。

尚未做：SBL importer、TPEx connector、真實 TDCC change（需累積多週）、產業別/產業趨勢、Shioaji realtime、走勢圖時序 API。

實際結構：
- `app/core/`：`config.py`（env 用 pydantic-settings；門檻用 `config/thresholds.yaml`）、`logging.py`
- `app/db/`：`base.py`、`session.py`（async engine）、`models/`（10 張表，`mixins.py` 含 look-ahead `data_date`/`available_at`）
- `alembic/`：migration（初始 schema 已套用）
- `app/services/orderflow/`：aggressor / cvd / large_trade / obi / absorption / trade_speed（純函式）
- `app/services/chip/`：intraday / institutional / holder / market 分項 + `composite.py`（config 驅動權重 + 缺成分權重重分配）
- `app/services/decision/`：`risk.py` / `entry.py` / `exit.py` + `__init__.decide()` 整合
- `app/services/{normalize,analysis}.py`：正規化工具、FeatureDaily→分析結果
- `app/api/`：`stocks.py`（`GET /api/stocks/{symbol}/analysis`）、`scanner.py`（`GET /api/scanner`）、`dashboard.py`（`GET /api/dashboard`）、`schemas.py`；CORS 允許 :3000（見 `main.py`）
- `app/services/market_scan.py`：scanner 與 dashboard 共用的全市場掃描
- `frontend/`：Next.js 16 + TS + Tailwind v4（App Router）。「Terminal Luxe」設計：字體 Fraunces(display)/JetBrains Mono(數字)/Noto Sans TC(中文)，招牌琥珀金，深炭黑底 + 噪點；**台股語意紅漲綠跌**（`lib/format.ts` 的 `dirColor`/`Change`）。頁面：`app/page.tsx`(總覽，TAIEX 大盤列+統計+Top10)、`app/scanner/page.tsx`(client，含股名/漲跌幅)、`app/stocks/[symbol]/page.tsx`(詳情，ScoreRing)；components：Nav/Card/ActionBadge/ScoreBar+ScoreRing/Change。`lib/api.ts` 型別化 client（`NEXT_PUBLIC_API_BASE`；.env.local 目前 :8000）。走勢 K 線待時序 API
- `app/backtest/`：`costs.py`（成本模型，禁 0 成本）、`forward_returns.py`（1/3/5/10/20D + MFE/MAE）、`metrics.py`（win/PF/expectancy/DD/Sharpe/Sortino）、`engine.py`（look-ahead 安全進場 + score bucket/threshold 聚合）、`runner.py`（DB-backed）
- `app/connectors/`：`twse.py`（`fetch_ohlcv` MI_INDEX、`fetch_institutional` T86、`fetch_margin` MI_MARGN）、`tdcc.py`（openapi 1-5，當週全市場）；`shioaji_stream.py` 仍為 starter
- `app/importers/`：`base.py`（TWSE 數字/日期解析、`is_stock_symbol`、`availability_for`）、`twse.py`（parser，欄位以標題名定位；MI_MARGN 用固定位置）、`tdcc.py`（代號需 strip 尾隨空白；級距→retail/medium/large/super_large 依 config 門檻）、`service.py`（冪等 upsert，FK stub 保護；`import_tdcc`）
- `app/repositories/upsert.py`：PostgreSQL `on_conflict` 冪等 upsert（do_update / do_nothing）
- `app/services/feature_builder.py`：原始表 → `feature_daily`。兩段正規化（個股 5 日淨額/20 日均量 → 市場橫斷面 Z-score），look-ahead 只用 `data_date<=target`
- `app/services/market_score.py`：TAIEX（MA20/MA60/斜率/波動）+ 全市場漲跌家數 → `market_daily.market_trend_score`（config `market_regime`）
- `app/repositories/market.py`：`load_market_context`（→ MarketContext）、`load_market_daily`
- `app/jobs/daily.py`：CLI「抓取→匯入→建特徵→大盤脈絡」，`APP_ENV=dev python -m app.jobs.daily YYYY-MM-DD [--tdcc] [--index]`
- `tests/fixtures/`：TWSE 真實回應切片（T86/MI_INDEX/MI_MARGN），供 parser 測試不打網路
- `app/models/signal.py`：領域 dataclasses（features / Action / SignalResult）
- `scripts/`：`seed_dev.py`（API 示範資料）、`backtest_demo.py`（合成行情跑回測，輸出 bucket 表）
- `tests/`：80 passed（DB roundtrip、order flow、scoring、decision、API 整合、backtest、importer、TDCC、feature builder、market score）
- 完整建議目錄結構見 `docs/01-overview-architecture.md §5`。

## 環境與指令

- DB：本機 PostgreSQL，開發庫 `twchip`、測試庫 `twchip_test`（角色 `omar`，見 `.env`）。測試以 `APP_ENV=test` 走測試庫，`conftest.py` 每個 test 重建 schema。
- 安裝：`pip install -r requirements.txt`
- Migration：`alembic upgrade head`（新增 model 後 `alembic revision --autogenerate -m "..."`）
- 測試：`python -m pytest`
- 啟動：`APP_ENV=dev uvicorn app.main:app --reload`；Swagger 於 `/docs`
- Seed 開發資料：`APP_ENV=dev python -m scripts.seed_dev`
- Backtest 示範：`APP_ENV=dev python -m scripts.backtest_demo`（輸出各 score bucket × 5D 績效）
- 前端：`cd frontend && pnpm install && pnpm dev`（:3000）。注意 dev 模式 HMR websocket 在本沙箱會失敗而卡住 client hydration；驗證請用 `pnpm build && pnpm start`。
- 每日盤後匯入 + 建特徵：`APP_ENV=dev python -m app.jobs.daily 2026-09-04`（需先匯入約 10 個交易日歷史，feature 的 5 日/20 日視窗才有意義）；`--tdcc` 抓當週 TDCC 股權分散；`--index` 抓近 4 個月 TAIEX 並建大盤脈絡（MA60 需足夠歷史）。

## 文件導覽（`docs/`）

規格已依主題拆分，實作前先讀對應章節（索引：`docs/README.md`）：

- 目標 / 架構 / 目錄 → `docs/01-overview-architecture.md`
- 資料來源 / 資料表 / Retention → `docs/02-data-and-schema.md`
- Order Flow 演算法 / Normalization / Score / Signal 分級 → `docs/03-algorithms-scoring.md`
- Entry / Risk / Exit → `docs/04-decision-risk.md`
- API / UI → `docs/05-api-ui.md`
- Backtest / Look-ahead / Testing / Paper Trading → `docs/06-backtest-validation.md`
- 排程 / Config → `docs/07-ops-config.md`
- Roadmap / 開發順序 / MVP DoD / 第一任務 → `docs/08-roadmap-delivery.md`

原始完整交接文件：`tw_stock_chip_analysis_coding_agent_handoff.md`（單一來源存檔）。

## 執行

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```
