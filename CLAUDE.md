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

已完成 docs/01–05 骨架與核心邏輯，以及 docs/06 的 Backtest 引擎（rule-based，含測試）。尚未做：資料 importer（TWSE/TPEx/TDCC 真實抓取與清洗）、feature 計算 job、Shioaji realtime、UI。

實際結構：
- `app/core/`：`config.py`（env 用 pydantic-settings；門檻用 `config/thresholds.yaml`）、`logging.py`
- `app/db/`：`base.py`、`session.py`（async engine）、`models/`（10 張表，`mixins.py` 含 look-ahead `data_date`/`available_at`）
- `alembic/`：migration（初始 schema 已套用）
- `app/services/orderflow/`：aggressor / cvd / large_trade / obi / absorption / trade_speed（純函式）
- `app/services/chip/`：intraday / institutional / holder / market 分項 + `composite.py`（config 驅動權重 + 缺成分權重重分配）
- `app/services/decision/`：`risk.py` / `entry.py` / `exit.py` + `__init__.decide()` 整合
- `app/services/{normalize,analysis}.py`：正規化工具、FeatureDaily→分析結果
- `app/api/`：`stocks.py`（`GET /api/stocks/{symbol}/analysis`）、`scanner.py`（`GET /api/scanner`）、`schemas.py`
- `app/backtest/`：`costs.py`（成本模型，禁 0 成本）、`forward_returns.py`（1/3/5/10/20D + MFE/MAE）、`metrics.py`（win/PF/expectancy/DD/Sharpe/Sortino）、`engine.py`（look-ahead 安全進場 + score bucket/threshold 聚合）、`runner.py`（DB-backed）
- `app/connectors/{twse,tdcc,shioaji_stream}.py`：starter connector（**尚未整合進 importer/DB，待補**）
- `app/models/signal.py`：領域 dataclasses（features / Action / SignalResult）
- `scripts/`：`seed_dev.py`（API 示範資料）、`backtest_demo.py`（合成行情跑回測，輸出 bucket 表）
- `tests/`：59 passed（DB roundtrip、order flow、scoring、decision、API 整合、backtest）
- 完整建議目錄結構見 `docs/01-overview-architecture.md §5`。

## 環境與指令

- DB：本機 PostgreSQL，開發庫 `twchip`、測試庫 `twchip_test`（角色 `omar`，見 `.env`）。測試以 `APP_ENV=test` 走測試庫，`conftest.py` 每個 test 重建 schema。
- 安裝：`pip install -r requirements.txt`
- Migration：`alembic upgrade head`（新增 model 後 `alembic revision --autogenerate -m "..."`）
- 測試：`python -m pytest`
- 啟動：`APP_ENV=dev uvicorn app.main:app --reload`；Swagger 於 `/docs`
- Seed 開發資料：`APP_ENV=dev python -m scripts.seed_dev`
- Backtest 示範：`APP_ENV=dev python -m scripts.backtest_demo`（輸出各 score bucket × 5D 績效）

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
