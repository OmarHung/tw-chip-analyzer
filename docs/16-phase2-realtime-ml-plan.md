# 16 · Phase 2 Realtime + ML Shadow 規劃

> 狀態：2026-09-14 規劃階段，**尚未動工**。本文件只規劃架構與分工，不含任何 `app/` 程式碼變更。

## 0. 前言與定位

使用者要求「直接做 realtime 跟 ML」。專案現況：Phase 1（rule-based Daily Chip Scanner）本身尚未通過 §28 成功標準——重建後各 horizon IC ≈0、bucket 報酬不單調（見 `CLAUDE.md`「現況」段落尾端）。經確認，realtime 與 ML 兩者都只能作為 **Phase 2 shadow-only 軌道**：

- 不得影響 Phase 1 正式的 `chip_score` / `breakdown` / `action` 輸出。
- 正式 `weights` 不新增任何鍵；`app/services/chip/` 與 `app/services/analysis.py` 不讀 shadow 表。
- 模式比照本專案已完成一次的先例——Phase 2 MOPS shadow-only（`docs/14-phase2-mops-data-sources.md`、`docs/15-phase2-mops-review-fixes-2026-09-14.md`）：raw 表 + shadow feature 表 + OOS 驗證腳本 + 「寫入前後正式輸出一致」的不變量測試。
- 在 rule-based 訊號本身都還沒過 §28 之前，兩軌的任何產出都只能是研究/監控用，不能宣稱「比現有訊號更好」，除非通過與 SBL/MOPS 同等的 OOS 驗證紀律（Newey-West t、train/embargo/test、跨 regime 一致）。

以下 A、B 兩節的現況描述均已直接讀碼確認（非依 `CLAUDE.md` 描述臆測）。

## A. Realtime 串流軌道

### A.1 現況（已讀碼確認）

- `app/connectors/shioaji_market.py`：只有 `fetch_ticks_sync(symbol, date)`，呼叫 Shioaji 同步 API `api.ticks(contract, date=...)` 抓「整天的歷史逐筆」。**沒有任何 callback 訂閱**（無 `api.quote.subscribe`、`on_tick_stk_v1` 等）。也就是說，目前連 callback 本身都不存在——`CLAUDE.md` 鐵則 11 描述的「callback 內禁止 heavy DB/pandas/HTTP/scoring」目前是防禦性文件，沒有對應程式碼可違反、也沒有遵守。
- `app/jobs/import_ticks.py`：對當日 turnover 前 N 檔（`config/thresholds.yaml:intraday_batch`：`max_symbols=200`、`min_turnover`、`throttle_ms=200`），逐檔序列呼叫抓取；每 25 檔查一次 `usage_sync()` 配額，達 `usage_stop_pct=95` 即停止；連續失敗達 `max_consecutive_failures=10` 也停止（疑似 token 過期時別繼續燒配額）。
- `app/services/ticks.py::fetch_ticks`：DB 快取優先（`_cached`），未命中才用 `pg_advisory_xact_lock` 序列化同 symbol-date 併發，抓到後 `delete` 當日舊資料 → `session.add_all(...)` → **單次 `commit()`**。是「一日一批」寫入，不是逐筆 flush，也不是即時串流。
- `app/db/models/intraday.py::RawTick`：`(symbol, data_date, ts, price, volume, bid_price, ask_price, aggressor_side)` + `ix_raw_tick_symbol_date`；讀取時 `order_by(ts, id)` 保證同 ts 決定性排序。
- `app/services/orderflow_intraday.py::compute_orderflow`：純函式，吃 `ticks: list[dict]`，算 CVD/large trade/absorption/trade speed/price efficiency，輸出 -1..1 訊號；用 `INTRADAY_SIGNAL_KEYS` 對照表與 `feature_builder.py` 共用「盤中」語意，避免兩處算法漂移。
- `app/services/feature_builder.py` 用 `RawTick` 算 intraday 分項寫回 `feature_daily`；無逐筆的標的該分項為 NULL，`composite.py` 依 `CHIP_COMPONENTS` 排除。

**結論**：目前是 100% 盤後批次歷史抓取，沒有 realtime callback 串流。A 軌是要「新建」一條 callback 串流路徑，而非升級既有程式碼。

模擬配額 500MB 的既有因應是「歷史批次」版本（只抓前 N 檔 + throttle + usage 監看 + 連續失敗停止），這套紀律要延用到 realtime 訂閱，但維度不同（訂閱檔數上限，而非抓取檔數上限；常駐服務而非一次性 job）。

### A.2 目標

把「批次逐筆」以外**新增**一條 callback 串流路徑，只餵 shadow 用途（即時 intraday 監控快照），**不接**業務決策路徑，不寫 `feature_daily` / `signal_snapshot`，不被 `composite.py` / `analyze_market` 讀取。

### A.3 架構：callback → buffer → batch flush → DB

1. **Callback 內只做**：型別轉換（Shioaji tick → dict，比照 `fetch_ticks_sync` 現有的 ts/side 轉換邏輯）+ append 到 thread-safe 佇列。**不可**在 callback 內做 DB 寫入、pandas 運算、HTTP 呼叫、或呼叫 `compute_orderflow` 之類的 scoring（鐵則 11）。
2. **背景 flush**：獨立 asyncio task 或 thread，每 100~500ms 或攢滿 100~1000 rows 觸發一次批次寫入（沿用鐵則 11 的門檻，也與現有 `fetch_ticks` 的「一次 `add_all` + 一次 `commit`」精神一致，只是頻率從「一天一次」變成「每 100~500ms 一次」）。
3. 需要冪等保護（unique constraint 或 upsert-ignore）：斷線重連可能造成 Shioaji 重送同一批 tick。
4. `usage_sync()` 複用做配額監看，但語意要從「批次工作達標即停止整個 job」改成「常駐服務達標即主動降級（退訂部分/全部標的）」。

### A.4 配額與範圍控制

- Simulation 帳號配額 500MB，**不可**整天掛全市場訂閱。訂閱範圍建議：當日 watchlist top-N，複用 `intraday_batch` 用 turnover 排序的邏輯，但需注意 chicken-egg 問題——開盤前決定訂閱清單只能用**前一交易日**的 `DailyPrice.turnover`（當日盤中 turnover 尚未產生），或改用使用者手動指定的固定 watchlist。
- 訂閱時段限制在台股盤中（09:00–13:30），收盤後主動 unsubscribe，避免整天空耗配額。
- 配額監看達 `usage_stop_pct` 時，行為需與批次版不同：批次是「停止整個 job」，常駐串流應是「降級（退訂部分標的，保留核心監控）」而非直接關閉整個服務。
- 斷線重連：`reset_api()` 已存在（丟棄快取 session，下次呼叫重新登入），可複用，但需新增重連 backoff 策略；重連期間必然漏 tick，須接受資料有缺口並記錄缺口區間，**不可**假裝資料連續。

### A.5 與 EOD job / 單飛鎖的關係

- `app/jobs/runner.py::_busy` 是「同時只允許一個回補/EOD」的單飛旗標，鎖粒度是整批一次性工作。Realtime 常駐串流是「長時間運行的背景服務」，語意不同，**不應該**佔用同一把鎖——否則整個交易日的排程 EOD、手動回補都會被常駐串流卡住。
- 但兩者都會碰 Shioaji session（`_api` 全域單例 + `_lock`）：streaming callback 與批次同步呼叫（`asyncio.to_thread` 包住的 `fetch_ticks_sync`）若同時對同一個 session 發呼叫，需先確認 Shioaji SDK 本身的 thread-safety（官方文件通常建議一個 session 對應一種用途）。**此點目前無法從讀碼確認，列入 D 節風險**。
- 若 realtime shadow 沿用 `RawTick` 表：批次 `fetch_ticks` 的「先 delete 當日再 insert」邏輯會把 streaming 寫入的當日資料整批清掉重寫，互相踐踏。**建議 realtime shadow 用新表**，不寫 `RawTick`，也更符合「shadow 不動正式路徑」的精神。

### A.6 資料表／schema（只說明用途，不寫 migration）

- `realtime_tick_shadow`（暫名）：欄位比照 `RawTick`（symbol, data_date, ts, price, volume, bid, ask, side）+ `received_at`（callback 收到時間，供延遲監控）。與 `RawTick` 分表，避免正式 intraday 分項的資料來源混淆。
- `realtime_stream_gap`（選用）：記錄斷線重連造成的時間缺口（symbol, data_date, gap_start, gap_end, reason），供之後 shadow feature 判斷「這段時間資料不完整」。
- 現階段**不建議**把 realtime tick 併入 `feature_builder.py` 算的正式 `cvd_z` 等 intraday 分項——那是正式評分路徑，shadow 資料要接必須先走跟 MOPS 一樣的「shadow 表 + OOS」流程（銜接 B 軌 §B.3 的討論）。

### A.7 明確界線

此軌道**不得**觸發自動下單（鐵則 1），純資料蒐集與監控；不得寫 `feature_daily` / `signal_snapshot`；不得被 `composite.py` / `analyze_market` 讀取。

## B. ML shadow 軌道

### B.1 現況（已讀碼確認）

- 完全 rule-based：`app/services/chip/composite.py::ChipScorer.score` 加權合成 -1..1 訊號 → `app/services/analysis.py::analyze_market` 兩段式橫斷面百分位映射成 0~100 分。目前**零 ML 基礎設施**。
- **既有可直接借用的資產**：`app/db/models/features.py::SignalSnapshot.payload` 已是「完整 feature 快照，供回測與**未來 ML**」（欄位註解原文），透過 `app/services/signal_persist.py::_feature_payload(fd)` 產生（排除 id/symbol/data_date/available_at/created_at/updated_at），且該表自帶 `AvailabilityMixin` 的 `available_at`。這是現成、已驗證過 look-ahead 安全性的 ML 特徵來源，**不需要重新從 raw 表兜特徵**。
- OOS 驗證紀律已有兩份可直接抄的範本：`scripts/sbl_factor_oos.py`、`scripts/mops_factor_oos.py`，共用：
  - `app/backtest/metrics.py::newey_west_t`（HAC t 值，lag = horizon − 1，修正連續交易日 forward return 重疊造成的自相關高估）。
  - `app/backtest/engine.py::market_calendar`（排除停牌復牌日）。
  - train（前半）/ embargo（`max(horizons)` 交易日）/ test（後半）三段式切分。
  - regime 分層（`market_trend_score > 0.3` 切多頭/非多頭）驗證方向一致性。
  - retrospective（全部資料，僅供參考）與 honest OOS（僅 look-ahead safe 的子集，才能判定 robust）分開報告——MOPS 用 provenance（`point_in_time_safe`/`backfill_derived`/`mixed`）做這個切分。

### B.2 特徵來源決策

- **優先用 `SignalSnapshot.payload` / `feature_daily`**：已是 look-ahead safe 的每日快照，不必等 A 軌就緒。
- **是否引入 realtime 軌道特徵**：第一階段**不建議**。理由：
  1. Realtime 軌道本身尚未累積、也還沒驗證資料完整性/缺口處理（見 A.4 斷線缺口問題）。
  2. 混入會讓 ML shadow 同時背負「模型本身」與「新特徵品質」兩個未驗證維度，難以歸因訊號來源。
  3. Realtime tick 沒有現成「當日盤後快照」的 available_at 等價物；要餵進訓練集必須先定義「盤中某時間點的特徵在當天可否被同日回看使用」的口徑，這本身是一個待設計的子問題，不宜與 v1 綁在一起。
  - 建議 ML shadow v1 只用既有每日特徵做研究；realtime 特徵留待 A 軌穩定後，作為 v2 的候選輸入（見 §C Track B3）。

### B.3 模型選型建議

現有驗證腳本的有效 test 橫斷面日通常只有 20~35 天（樣本量 N≈100~130 交易日），這是小樣本 setting：

- **建議 baseline：正則化線性模型（Ridge/Lasso）**。可解釋（係數即因子方向）、訓練快、不易過擬合，且與現有 rule-based composite 的「加權線性合成」哲學一致，方便做「ML 版合成 vs 手調權重」的直接對比。
- **建議第二候選：淺層 GBM**（如 LightGBM，`max_depth` 2~3、強正則化、早停）。能抓非線性/交互作用，但優先用於「特徵重要性研究」（提示既有 factor 誰更有潛力），而非直接拿來產生 shadow score。
- **明確排除：深度學習**（MLP/LSTM/Transformer）。理由：①樣本量（幾十個有效橫斷面日）不足以支撐；②可解釋性差，不利於歸因；③當 base rule-based 訊號本身 IC≈0 時，複雜模型更容易學到噪聲而非訊號——過擬合風險超過潛在收益，這正是 `common/coding-style.md` KISS 原則在這個資料量級下的具體應用。
- 輸出：ML shadow 分數不是「新的 chip_score」，而是獨立的 `ml_shadow_score`（比照 `MopsShadowFeatureDaily` 的設計存入 `ml_shadow_feature_daily`），同樣不進 `weights`。

### B.4 驗證紀律（重用不重造）

- 產出腳本 `scripts/ml_shadow_oos.py`，結構比照 `scripts/mops_factor_oos.py`：train 階段 fit 模型，embargo 之後的 test 階段**只 predict 不再 fit**，對 test 集算 daily rank IC、樸素 t、`newey_west_t`。
- **Look-ahead safe 的切分規則（本規劃過程中發現的、A/B 兩軌都未直接點出但對 ML 軌特別關鍵的坑）**：訓練特徵不可直接查詢「當前」`feature_daily` 表的值——該表會被 `rebuild_signals` 之類的操作**覆寫**（`UpdatedAtMixin` 的「同筆覆寫」語意；`CLAUDE.md` 現況多次提到「部署後需全量重建」、百分位映射改動、§09 修正等）。用當前值訓練，等於用「事後才知道的修正結果」（新的 config、新的公司行動還原值）當作「當時」的特徵，是隱性 look-ahead。**必須用歷史快照**——即 `SignalSnapshot.payload`，並核對其 `config_version` 欄位，確認訓練樣本的 `available_at` 與「產生該快照時的規則版本」一致。
- Train/test 間留 `max(horizons)` embargo（比照既有腳本的 `EMBARGO=20`），避免 forward return 重疊洩漏。
- Regime 分層驗證（仿 SBL 的 `market_trend_score>0.3` 切分），確保不是單一 regime 的產物——CLAUDE.md 已記載過一次「先前誤判樣本全是多頭」的教訓，此紀律务必延續。
- **Enable 門檻比照 SBL/MOPS 先例**：NW `|t| > 2`、train/test 同方向、跨 regime 方向一致、有效 test 橫斷面日不少於門檻（比照 MOPS 的 `min_honest_test_days=20`）。通過仍只代表「可以提出是否接入評分的獨立變更案」，腳本本身**不得自動改權重**；第一次啟用要給保守權重（非設計權重全額），且這個決定不該在驗證腳本裡自動做，只能是人工審查後的另案。
- Retrospective 與 honest OOS 報告必須分開呈現（比照 MOPS §7.1），因為 IC≈0 的 base rate 下 ML 很容易「看起來」有效但其實是 in-sample 噪聲。

## C. 分工建議

### Track A（Realtime）

| # | 任務 | 涉及檔案 | 驗收標準 | 依賴 | 預估 |
|---|---|---|---|---|---|
| A1 | Shioaji streaming connector 骨架：新增 callback 訂閱/退訂函式，只做訂閱管理與登入複用，不含 buffer/flush | `app/connectors/shioaji_market.py` | 手動跑腳本，盤中訂閱 1~3 檔，log 可見 tick；既有測試全綠 | 無 | 0.5~1 天（SDK callback 行為是最大不確定性來源） |
| A2 | In-memory buffer + batch flush + 新 shadow 表 | 新增 `app/db/models/realtime.py`、`app/services/realtime_stream.py` | 模擬高頻 callback 灌假資料，flush 頻率符合 config、無逐筆 commit（可斷言 commit 次數遠小於 tick 數） | A1 | 1~1.5 天 |
| A3 | 配額/範圍/重連治理 + 與 EOD 的關係釐清 | `app/services/realtime_stream.py`、`config/thresholds.yaml` 新增 `realtime_stream` 節、`app/jobs/scheduler.py`（是否需掛載常駐服務待確認） | 模擬配額超標、斷線兩種場景行為符合規劃，不阻塞既有 EOD/回補 | A2 | 1 天 |

### Track B（ML shadow）

| # | 任務 | 涉及檔案 | 驗收標準 | 依賴 | 預估 |
|---|---|---|---|---|---|
| B1 | 離線 ML 研究腳本（**不依賴 realtime，可與 A 軌完全並行**） | 新增 `scripts/ml_shadow_research.py` | 讀 `SignalSnapshot.payload` 歷史資料，跑通 Ridge/淺 GBM，輸出 retrospective IC 報告（明確標「僅研究用，非 honest OOS」），複用 `newey_west_t` | 無 | 1~1.5 天 |
| B2 | Honest OOS 框架 + shadow 表 | 新增 `app/db/models/ml_shadow.py`、`scripts/ml_shadow_oos.py` | train/embargo/test 切分、regime 分層、robust 判定門檻正確運作；有測試保證新表寫入不影響 `chip_score`/breakdown/action（仿 MOPS 不變量測試） | B1 | 1.5~2 天 |
| B3 | （可選、明確排在 A 軌穩定之後）realtime shadow 特徵併入 ML 訓練集的可行性研究：先定義「盤中特徵的 available_at 口徑」 | 待定 | 產出設計備忘，暫不實作 | A2/A3 完成且已累積數月資料 | 0.5 天（僅設計） |

### 依賴與並行

- A1 → A2 → A3 為串行依賴（可同一 worker 或不同 worker 交接）。
- B1 完全獨立，可與 A 軌同時開工，且**建議優先做**——成本最低，最快驗證「這條路有沒有戲」（比照 SBL/MOPS 先例，IC 很可能又接近 0）。
- B2 依賴 B1 的特徵準備程式碼，不依賴 A 軌。
- B3 明確依賴 A 軌完成，是兩軌真正的交匯點，建議排在最後、且先決定要不要做。

### 建議實作順序

**B1 →（A1 與 B2 並行）→ A2 → A3 →（視 B1/B2 結果決定要不要做 B3）**

理由：B1 成本最低、最快拿到「這個方向值不值得投入」的訊號；A 軌的技術不確定性集中在 A1（SDK callback 行為），越早驗證越好、不需要等 B 軌；B2 可以在 B1 出結果後立刻接上，不必等 A 軌完成。

## D. 風險與待確認事項

1. **Shioaji 模擬帳號 callback 是否真的可用、SDK thread-safety 未知**——目前連接器只驗證過同步 `api.ticks()` 歷史查詢，從未驗證過 callback 訂閱在 simulation 模式下的實際行為（延遲、斷線頻率、單一 session 能否同時處理 callback 與既有同步呼叫）。這是 A 軌最大的技術不確定性，建議 A1 定位為 spike，容許方案中途改變。**需要使用者確認**：是否真的要連上 Shioaji 即時行情（vs. 先做 B 軌驗證有沒有戲）。
2. **配額經濟性**——500MB 配額若常駐整個盤中（09:00–13:30，4.5 小時）訂閱，消耗速度可能遠高於「收盤後批次抓全量」。**需要使用者拍板**：能接受多快燒完配額、要不要先設「每週只測幾天」的節流策略，以及模擬帳號額度是否夠長時間測試。
3. **ML 訓練資料的時間窗口與版本邊界**——`feature_daily`/`SignalSnapshot` 歷史上經歷過多次規則變更（百分位映射改動 2026-09-08、§09 修正 2026-09-13 等），訓練集若跨越這些邊界，label 語意不一致。**需要使用者決定**：只用最近一次全量重建後的資料（樣本量可能不夠 B.4 門檻），還是接受跨版本但把 `config_version` 當必要分層變量——這是統計取捨而非純工程問題。
4. **A 軌基建是否值得現在做**——若 B1 離線研究顯示既有 rule-based 特徵測不出顯著 alpha（延續 SBL/MOPS 的既有結論模式），在沒有強 prior 的情況下先做昂貴的 realtime streaming 基建，ROI 存疑。建議把 B1 結果當成「要不要做 A2/A3」的軟性檢查點，而非無條件往下做。
5. **常駐服務的生命週期模式尚未設計**——現有 `app/jobs/scheduler.py`/`runner.py` 都是「一次性任務」模型（單飛鎖、開始跑到結束），沒有「長時間運行的背景服務」先例。A3 需要新設計啟停掛載點（FastAPI lifespan？獨立 process？），不是現有模式的直接複製，需要架構決策而非照抄現有 job 模式。
