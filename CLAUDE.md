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

已完成 docs/01–06 全部（骨架、演算法、評分、決策、API、Backtest）、docs/05 §16 Next.js UI，可吃真實台股盤後資料端到端運作。真實資料涵蓋 OHLCV + institutional + margin（**TWSE + TPEx 上櫃**）+ **SBL 借券（TWT93U）** + TDCC 股權分散 + TAIEX 大盤 regime。intraday 分項可併入 composite（有逐筆走四維，無者三維排除）。UI 有 總覽/選股/主力背離/驗證(/validation 前瞻報告)/系統(/system 配額·涵蓋·回補) 五頁。

**chip_score 已改橫斷面百分位映射（2026-09-08，重大行為變更）**：`scoring.mapping: percentile`（config 可切回 linear）。分數 = 當日全市場 composite_raw 排名百分位（0~100 均勻分布），修復「z 合成回歸 50、天花板 ~64、高分 bucket 永無樣本」的結構缺陷；rank-preserving 不改 IC。**語意**：75 分 = 當日前 25%，BUY 門檻從「幾乎不可達」變「常態可達」（Entry Filter 其餘關卡仍在）。四呼叫點（scanner/dashboard/persist/單股 analysis）共用 `analysis.analyze_market` 兩段式；單股 `/analysis` 會載入當日全市場一起算。

**公司行動還原（價 + 量）已完成**：`corporate_action` 收 除權息 TWT49U／面額變更 TWTB8U／減資 TWTAUU／**除權息預告 TWT48U**，以「後復權」還原。**價因子 `adj_factor`（參考價/前收）與量因子 `share_factor`（1 舊股→幾新股）是兩件事，不可互推**——除權息把現金股利與配股混在同一個 `adj_factor` 裡（已實證：無償配股 7.1% 的 2442 比值為 1.0、純現增的 6533 卻是 1.032），故配股率只能取自 TWT48U 的「無償配股率」（`share_factor = 1 + 無償配股率`）；面額/減資才可用 `1/adj_factor`。價因子套 close/high/low，量因子只套 `avg_vol20`（法人/融資/借券強度的分母），**`vwap` 是同日 turnover/volume 比值，一律用原始量**。還原值可用 `GET /api/stocks/{symbol}/features?date=` 核對（個股頁「還原後價格結構」卡）。

**已知限制 / 待辦（會影響決策，讀不出來的部分）**：
- **歷史除權息的配股率補不回來**：TWT48U 是預告表，只回「未來尚未執行」的事件（區間參數無效）——只能靠每日 EOD 往前累積。（註：SBL TWT93U 歷史其實可回補，勿再以它類比。）故 2026-09-09 以前的 `權`/`權息` 事件 `share_factor` 為 NULL（只還原價、不還原量）；`面額`/`減資` 不受影響（用 `1/adj_factor`，精確且可回補）。TPEx 的公司行動則整個尚未接。
- **§28 成功標準仍未達成**：乾淨重算後各 horizon IC 全 ≈0（bucket 平坦）。歷次因子挖掘結論=病根是「單一 5 個月 regime 樣本」，非程式；勿再於現有資料挖因子（詳見 memory chip-score-backtest-finding）。累積跨 regime 資料後用 `/validation` 頁與 `scripts/score_monotonicity.py` 重驗。
- **SBL 特徵已接線但權重刻意為 0**：`sbl_change_z` 已入 feature_daily，config `weights.institutional.sbl_change: 0.0`（原設計 -0.10）——紀律：未經 OOS 驗證不進分數；待借券累積 ≥2 個月跑 `scripts/sbl_factor_oos.py` 驗證後再啟用。融券 short_change 同理維持原 config，勿依 in-sample 調整。
- **TDCC holder：視窗內 ≥2 週快照自動切真實 change**（feature_builder），1 週時 level proxy；`available_at` 現設快照日盤後（demo 對齊），生產應 lag 至揭露日。
- **industry_trend 仍中性**：importer 未帶產業別。
- 尚未做：TPEx 的 SBL、產業別/趨勢、Shioaji realtime、intraday 併入 backtest 驗證單調性（需累積多日 tick；Shioaji simulation 配額僅 500MB，backfill 逐筆會燒穿，逐筆只靠每日 EOD 累積）。

## 頂層地圖

細節用 CodeGraph 或讀碼；完整建議結構見 `docs/01-overview-architecture.md §5`。

- `app/core/`：`config.py`（env 用 pydantic-settings；門檻用 `config/thresholds.yaml`）、`logging.py`
- `app/db/`：`models/`（10 張表，`mixins.py` 含 look-ahead `data_date`/`available_at`）；`intraday.py` 有 `RawTick`
- `app/services/orderflow/`：aggressor / cvd / large_trade / obi / absorption / trade_speed（純函式）
- `app/services/chip/`：intraday / institutional / holder / market 分項 + `composite.py`（config 驅動權重 + 缺成分重分配）
- `app/services/decision/`：`risk` / `entry` / `exit` + `decide()`
- `app/services/`：`feature_builder.py`（原始表→`feature_daily`，兩段正規化，look-ahead 只用 `data_date<=target`）、`market_score.py`、`normalize.py`（含 `cross_sectional_percentile`）、`analysis.py`（含 `analyze_market` 橫斷面兩段式）、`market_scan.py`、`flow_scan.py`、`forward_report.py`（前瞻驗證）、`price_adjust.py`（後復權純函式，價/量共用）、`orderflow_intraday.py`、`ticks.py`、`signal_persist.py`
- `app/api/`：`stocks`（`/analysis`、`/chart`、`/ticks`、`/orderflow`、`/flows`、`/features` 還原值核對）、`scanner`（含 `/divergence`）、`dashboard`、`ops`（`/status`、`/backfill`）、`validation`（`/forward`）；CORS 允許任意 localhost 埠（見 `main.py`）
- `app/backtest/`：`costs`（禁 0 成本）、`forward_returns`、`metrics`、`engine`（look-ahead 安全）、`runner`
- `app/connectors/`：`twse`（含 SBL TWT93U、公司行動 TWT49U/TWT48U/TWTB8U/TWTAUU）/ `tpex`（上櫃；憑證缺 SKI，關 strict X509）/ `tdcc` / `yahoo`（圖表用）/ `shioaji_market`（逐筆 ticks，`simulation=True` 單例；金鑰無 production 權限但模擬可取真實行情）
- `app/importers/`：TWSE/TPEx/TDCC parser + `service.py`（冪等 upsert）；`app/repositories/upsert.py` 用 PG `on_conflict`
- `app/jobs/`：`daily.py`（抓取→匯入 TWSE+TPEx+SBL→建特徵→大盤脈絡）、`import_ticks.py`（批次逐筆）、`scheduler.py`（APScheduler EOD）、`runner.py`（手動回補，與 EOD 共用單飛鎖）
- `frontend/`：Next.js 16 + TS + Tailwind v4。設計約束見下節。UI 規格見 `docs/05-api-ui.md §16`。
- `tests/`：144 passed。`tests/fixtures/` 有 TWSE/TPEx 真實回應切片供 parser 測試不打網路。

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
