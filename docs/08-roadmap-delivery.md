# 08 · Roadmap、開發順序與交付標準

> 對應原始交接文件 §24–31

## 24. ML 不在 Phase 1

**禁止一開始投入：** LSTM、Transformer、PPO、RL。

先建立 rule-based baseline。累積足夠歷史資料後，Phase 4 再用 LightGBM / XGBoost。

Features 可包含：

```text
CVD_Z, LargeTrade_Z, OBI, Absorption, TradeSpeed,
Foreign5D, Trust5D, MarginChange, SBLChange,
LargeHolderChange, RetailHolderChange,
MarketTrend, IndustryTrend, ATR, VolumeRatio
```

Labels：`forward_return_5d`、`mfe_5d`、`mae_5d`、`success`。

## 25. 開發 Phase

- **Phase 1：Daily Chip Scanner** — TWSE、TPEx、TDCC、OHLCV、Institutional、Margin、SBL、PostgreSQL、Feature Engine、Chip Score、Scanner API、Backtest。目標：先證明盤後籌碼模型有效。
- **Phase 2** — 加入 MOPS 內部人 / 大股東 / 質押。
- **Phase 3** — 加入 Shioaji realtime：CVD / Large Trade / OBI / Absorption / Trade Speed。
- **Phase 4** — 加入券商分點與 ML Probability。

## 26. Coding Agent 開發順序

1. 建 FastAPI / SQLAlchemy / Alembic / PostgreSQL / config / logging。
2. 建 DB schema。
3. 實作 TWSE / TPEx / TDCC importer。
4. 建 daily / weekly feature。
5. 建 rule-based Chip Score。
6. 建 Scanner API。
7. 建 Backtest，先驗證 Daily + TDCC。
8. 接 Shioaji realtime。
9. 加 CVD / Large Trade / OBI / Absorption。
10. 建 Intraday Score。
11. 整合 BUY / WATCH / EXIT。
12. 建 Next.js UI。
13. Paper Trading。

> 不要一開始同時實作全部功能。

## 27. MVP Definition of Done

**Data**：上市/上櫃基本資料同步、Daily OHLCV、三大法人、融資融券、借券、TDCC、Shioaji Tick、Shioaji BidAsk。

**Feature**：CVD、Large Trade、OBI、Absorption、Trade Speed、Institutional Z-score、TDCC change、Market trend。

**Signal**：Chip Score、BUY、WATCH、HOLD、REDUCE、EXIT、AVOID。

**Risk**：Entry Zone、Stop Loss、TP1、TP2、RR。

**Backtest**：1D / 3D / 5D / 10D / 20D、MFE / MAE、Transaction Costs、Look-ahead bias checks。

**UI**：Scanner、Stock Detail、Signal Reasons、Price/CVD/Institutional/TDCC 圖表。

## 28. 第一階段成功標準

真正成功條件不是 UI 或功能數量，而是：

> Chip Score 越高，未來報酬應有統計上的單調改善。

理想示意：

```text
Score       5D Avg Return
50-60       +0.3%
60-70       +0.8%
70-80       +1.5%
80-90       +2.6%
90+         +4.1%
```

同時 MAE 不應顯著惡化。若沒有這種現象，優先重新調整 feature / normalization / weight，**不要急著增加新功能**。

## 29. Existing Starter Project

目前已有初版（`tw_chip_analyzer_mvp.zip`，已解壓於 `app/`）。已包含：Shioaji adapter、TWSE connector、TDCC connector、CVD、Large Trade、OBI、Chip Scoring、Risk Engine、Decision Engine。

> Coding Agent 可參考，但**不可視為 production-ready**。

優先補：DB layer、async architecture、reconnect、retry、validation、config、tests、observability。

## 30. 每個 Milestone 的交付格式

Coding Agent 每完成一個 milestone 必須輸出：

1. 已完成項目
2. DB migration
3. API 文件
4. 測試結果
5. 尚未完成項目
6. 技術債
7. 下一步
8. 是否存在 look-ahead bias
9. 是否存在資料缺漏
10. Backtest 結果

> **禁止在沒有 Backtest 證據前聲稱策略有效。**

## 31. 給 Coding Agent 的第一個直接任務

請先實作 **Phase 1：Daily Chip Scanner**。第一個 milestone 只做：

```text
TWSE / TPEx / TDCC
OHLCV / Institutional / Margin / SBL
PostgreSQL / Feature Engine / Chip Score / Scanner API / Backtest
```

**驗收條件：**
- 可以指定日期重建全市場 daily feature
- 可以掃出 score ≥ 指定門檻的股票
- 可以查看 score breakdown 與 reasons
- 可以跑 1/3/5/10/20 日 forward return 回測
- 可以確認無 look-ahead bias
- 可以輸出各 score bucket 的績效比較

> 在此 milestone 驗證前，不要投入 Shioaji realtime、UI 或 ML。
