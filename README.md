# 台股籌碼分析與進出場建議系統

整合盤中 order flow、盤後法人/信用/借券、週度 TDCC 集中度，輸出 **Chip Score（0~100）** 與 **BUY / WATCH / HOLD / REDUCE / EXIT / AVOID** 建議，含進場區間、停損、TP1/TP2、RR 與原因說明。

> 這份 README 只做對外入口。**權威且最新的專案說明在 [`CLAUDE.md`](CLAUDE.md) 與 [`docs/`](docs/README.md)**；本檔不重複維護會過期的細節。

## 目標

整合三個時間尺度：

1. 盤中：Shioaji Tick / BidAsk → CVD、大單、OBI、Absorption、成交速度
2. 盤後：TWSE / TPEx → 外資、投信、自營商、融資融券、借券
3. 每週：TDCC → 大戶/散戶持股級距變化

## 建議規則（摘要，完整定義見 `docs/03`、`docs/04`）

- **BUY**：ChipScore ≥ 75 AND RR ≥ 2 AND 不過度偏離 VWAP/MA20 AND 市場非強空 AND 流動性足 AND 非鎖死漲停
- **WATCH**：Score 65~74，或籌碼佳但時機/RR 未到，等回到 entry zone 或突破確認
- **EXIT**：持倉中 Score < 40、跌破停損、或大戶/法人/盤中資金同時翻空

**核心原則**：不要用「單筆大單 = 大戶」。需盤中 order flow + 盤後法人/信用 + 週度 TDCC 集中度**交叉驗證**。籌碼與時機分離：Chip Score 高 ≠ 可進場。

## 現況（Phase 1，rule-based）

- docs/01–06 全部完成（骨架、演算法、評分、決策、API、Backtest）+ Next.js 前端（`frontend/`）
- 可吃真實台股盤後資料端到端運作：OHLCV + institutional + margin（TWSE + TPEx）+ TWSE SBL + TDCC 股權分散 + TAIEX 大盤 regime
- Chip Score 已採依可用成分／資料簽章分組的當日橫斷面百分位；公司行動價量還原、前瞻驗證與 Newey–West 統計已接線
- Phase 2 MOPS 董監／大股東持股、質押、轉讓申報已開始 forward 累積，但嚴格維持 shadow-only，不進正式分數

> ⚠️ **尚未通過驗證，勿當可用策略**：2026-09-13 修正後的 dev 重建仍顯示各 horizon IC 約 +0.006～+0.008、Chip Score 與未來報酬**無單調性**。**Backtest 是實盤前必要條件**；目前應優先累積跨 regime honest OOS，不再用同一段短樣本反覆調參，詳見 `CLAUDE.md` 與 `docs/06`。

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# 需本機 PostgreSQL（開發庫 twchip），首次建 schema：
alembic upgrade head

# 啟動 API（Swagger 於 /docs）
APP_ENV=dev uvicorn app.main:app --reload
```

每日盤後匯入 + 建特徵（需先匯入約 10 個交易日歷史）：

```bash
APP_ENV=dev python -m app.jobs.daily 2026-09-04            # --tdcc 抓當週 TDCC、--index 抓 TAIEX
APP_ENV=dev python -m app.jobs.import_ticks YYYY-MM-DD      # 批次逐筆（intraday z 進 feature_daily）
```

前端：

```bash
cd frontend && pnpm install && pnpm build && pnpm start   # :3000
```

測試：`python -m pytest`

## 下一步

優先累積跨 regime 的 honest OOS，持續用 `/validation` 與 `scripts/score_monotonicity.py` 檢查分數單調性及 MAE；SBL、產業趨勢與 MOPS 未通過既定門檻前維持零權重／shadow-only。工程面先完成 production 資料涵蓋監控與 UI 人工驗收。只有 §28 成功標準有可靠進展後，才投入 Shioaji realtime、真正五檔 OBI、intraday backtest 與 paper trade；第一階段永遠只分析、不下單。

## 文件導覽

架構 `docs/01` · 資料源/表 `docs/02` · Order Flow/Score `docs/03` · Entry/Risk/Exit `docs/04` · API/UI `docs/05` · Backtest/Look-ahead `docs/06` · 排程/Config `docs/07` · Roadmap/DoD `docs/08` · MOPS 現行規格 `docs/14`／審查修正 `docs/15`（完整索引見 `docs/README.md`）。
