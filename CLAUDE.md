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

- `app/` 為 starter（`tw_chip_analyzer_mvp.zip` 解壓）：已含 Shioaji adapter、TWSE/TDCC connector、CVD、Large Trade、OBI、Chip Scoring、Risk、Decision。**不可視為 production-ready。** 優先補：DB layer、async、reconnect/retry、validation、config、tests、observability。
  - 現有檔案：`app/main.py`、`app/connectors/{twse,tdcc,shioaji_stream}.py`、`app/services/{scoring,orderflow,decision,risk}.py`、`app/models/signal.py`
- 完整建議目錄結構見 `docs/01-overview-architecture.md §5`。

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
