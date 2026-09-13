# BUG 修正複查後修改建議書（2026-09-13）

## 1. 文件用途

本文件供 coding agent 直接執行，處理 `docs/09-bug-audit-2026-09-13.md` 第一輪修正後仍存在的部分修正、交互問題與新發現。

目前第一輪 15 項 BUG 加 1 項安全風險的狀態：

- 10 項符合預期。
- 6 項只完成部分修正。
- 沒有任何原項目完全未處理。

本輪優先修正會影響 Chip Score、BUY gate、REDUCE 與驗證可信度的問題。完成前先不要做最終全量 `rebuild_signals`，否則評分語意變更後仍要重建第二次。

## 2. 不得違反的範圍

### 必守

- Phase 1 只分析、不下單。
- Chip Score 與進場時機分離。
- 市場狀態未知、鎖死漲停未知或 RR 無可靠目標時，不得默認放行 BUY。
- 缺資料必須保留為未知，不得用中性 0 或前一日資料冒充。
- 不同資料成分組合不得直接混在同一百分位排名。
- 不得把 CVD level／z-score 冒充 CVD slope，也不得把橫斷面正值冒充時間序列 rising。
- 沒有五檔委買／委賣量時，不得宣稱計算出 OBI。
- 所有可調門檻及視窗必須進入 `config/thresholds.yaml`。
- 不得依現有單一 regime 樣本調權重後宣稱策略有效。
- 公司行動還原、交易成本、`available_at` 與 Newey–West 規則不得退回舊行為。

### 本輪不做

- 不接自動下單。
- 不新增 ML。
- 不新增真正 realtime order book；真 OBI 留待有五檔量資料時處理。
- 不調高 SBL 或 industry 權重。
- 不因本輪修正宣稱 Chip Score 已具預測力。
- 不順手重構無關頁面或清理既有 lint 問題。

## 3. Phase 0：允許沿用的現有 API 與模式

coding agent 開工前應先讀完以下文件：

- `docs/03-algorithms-scoring.md`：CVD、OBI、分項權重與缺成分重分配。
- `docs/04-decision-risk.md`：Entry Filter、RR、持倉出場與 Distribution Warning。
- `docs/06-backtest-validation.md`：回測、look-ahead、測試要求。
- `docs/07-ops-config.md`：所有 threshold config 化。
- `docs/08-roadmap-delivery.md §30`：交付格式。
- `docs/09-bug-audit-2026-09-13.md`：原始問題與驗收意圖。
- `docs/10-bug-fix-todo-2026-09-13.md`：第一輪修正後的部署與待辦說明。

可直接沿用的既有模式：

- `app.repositories.market.load_market_context(session, as_of)`：大盤上下文入口，但本輪需改成只接受目標日資料。
- `AnalysisService.score_features()`：決定 active component 的單一入口。
- `ChipScorer.score(..., active_components=...)`：缺頂層成分時排除並重分配權重。
- `analyze_market()`：依資料成分組合做橫斷面百分位的單一入口。
- `analysis._opt()`：保留 `None` 語意，不把未知改成 0。
- `Thresholds.get()`：讀取 YAML 巢狀設定。
- `app.backtest.metrics.newey_west_t()`：前瞻驗證的統計口徑。
- `app.backtest.runner.load_bars()`：公司行動還原後的回測價格口徑。
- `app.repositories.upsert.upsert_many()`：所有批次 upsert 的共用入口。

禁止自行發明另一套 scanner、score、price adjustment 或 forward return 實作。

## 4. Phase 1：修正市場資料新鮮度

### 問題

`daily.run()` 在 TAIEX 抓取失敗時會繼續建立特徵與訊號，但：

- `build_market_daily(target)` 會因缺少當日 TAIEX 而略過。
- `load_market_context(session, target)` 使用 `data_date <= target`，會取到前一交易日或更舊的 MarketDaily。
- `AnalysisService.score_features()` 仍固定啟用 market 成分。
- Entry Filter 會把舊市場分數當成今日市場狀態。

這可能在大盤當日急跌且 TAIEX 來源剛好失敗時，用昨天的偏多狀態放行 BUY。

### 實作要求

修改：

- `app/models/signal.py`
- `app/repositories/market.py`
- `app/services/analysis.py`
- `app/services/chip/market_score.py`
- `app/services/decision/entry.py`
- `app/services/signal_persist.py`
- `app/services/market_scan.py`
- `app/api/dashboard.py`

建議模型：

1. `MarketContext.market_trend_score` 改為 `float | None`，`None` 明確代表目標日大盤資料未知。
2. `load_market_context()` 只查 `MarketDaily.data_date == as_of`，不可自動向前沿用。
3. `load_market_daily()` 在 dashboard 也只回目標日資料，避免頁面顯示舊 regime 卻標今日 `as_of`。
4. `AnalysisService.score_features()` 僅在當日 `market_trend_score` 有值時啟用 market 成分。
5. `market_score()` 對缺值只提供計算用中性值，但不得把 market 標為 active。
6. `PriceContext.market_trend_score` 改為 `float | None`。
7. Entry Filter 遇到 `None` 時加入 gate reason：`無當日大盤資料，無法排除 Strong Bear`，輸出 WATCH。
8. signal snapshot 的 reason／payload 必須能看出當日 market 缺失，不可靜默降級。
9. `daily.run()` 與 runner 的 `degraded` 狀態維持現有行為。

### 測試

新增或擴充：

- `tests/test_market_score.py`
- `tests/test_daily_failsoft.py`
- `tests/test_decision_gates.py`
- `tests/test_signal_persist.py`
- `tests/test_api.py`

至少涵蓋：

1. DB 只有前一日 MarketDaily，查今日 context 必須得到未知，不得得到前一日分數。
2. 今日 TAIEX 失敗但 OHLCV 成功時，EOD 可降級完成。
3. market 未知時 market 成分不在 `ChipScoreResult.components`。
4. market 未知時，即使 Chip Score、RR、流動性全部達標，也只能 WATCH。
5. dashboard 不得把昨天大盤資料標成今日狀態。
6. 當日 MarketDaily 存在時，現有 Strong Bear 行為不變。

### 驗收

- 全 repo 不得再有「評分或決策用途的 MarketDaily 使用 `<= target` 自動沿用」行為。
- TAIEX 失敗仍能完成 EOD，但當日 BUY 不得因 market unknown 被放行。
- scanner、單股 analysis、dashboard、signal snapshot 對 market availability 的語意一致。

### 禁止做法

- 不得用 `MarketContext()` 的 0 分冒充已知中性市場。
- 不得只在 UI 標示 degraded，後端仍使用舊 MarketDaily。
- 不得在 Entry Filter 看到 `None` 時轉為 0。

## 5. Phase 2：修正 institutional 子項缺漏混排

### 問題

`institutional_score()` 會對缺失子項重分配權重，但 `analyze_market()` 只依頂層 `components` 分組。

例如 TPEx margin 來源失敗時：

- TWSE institutional：法人＋融資＋融券。
- TPEx institutional：只有法人，權重被重新放大。
- 兩者仍使用相同 `{"institutional", ...}` 分組做百分位。

複查中的純函式重現：相同已知因子皆為 `z=+1` 時，完整子項 institutional raw 約為 `0.185`，缺融資／融券後約為 `0.416`。差異來自資料可用性，不是籌碼本身。

### 實作要求

修改：

- `app/services/chip/institutional_score.py`
- `app/services/chip/composite.py`
- `app/services/analysis.py`
- `app/models/signal.py`（若 availability signature 放在結果模型）

建議實作：

1. institutional 計算同時回傳：
   - score value。
   - 實際參與且權重非 0 的子項集合，例如 `{"foreign", "trust", "dealer"}`。
2. `ChipScoreResult` 保留現有頂層 `components` 供 composite 權重使用，另增加不可混淆的 `availability_signature`。
3. signature 至少包含：
   - 頂層 active components。
   - institutional 實際有效子項。
4. `analyze_market()` 的百分位分組 key 改為 `(components, availability_signature)`。
5. SBL 目前權重為 0，不應因有無 SBL 值切出不同組。
6. 不更改 institutional 現行權重數值。

如果 coding agent 判斷 signature 分組會產生大量單檔小組，可採更保守替代方案：必要 institutional 子項不完整時，整個 institutional 成分排除。不可維持目前「重分配後仍與完整組混排」的行為。

### 測試

新增或擴充 `tests/test_feature_windows.py`、`tests/test_scoring.py`：

1. 完整 institutional 與缺 margin／short 的股票必須進入不同 percentile group。
2. 只有零權重 SBL 的有無差異，不得切成不同組。
3. 同一 availability signature 內仍維持百分位單調與 50 平均附近。
4. 有效 institutional 子項全缺時，頂層 institutional 成分仍須排除。
5. TPEx margin fail-soft 情境不得因缺負權重因子，自動在 TWSE／TPEx 混排中取得優勢。
6. 單檔小組必須有明確退化行為及測試，不得得到假的 0／100 百分位。

### 驗收

- 分數排序不再因 institutional 子項覆蓋率不同而直接混排。
- `components` 仍只代表 composite 的頂層成分，不得把 `institutional:foreign` 當成 composite 權重 key。
- scanner、dashboard、persist、單股 analysis 仍全部共用 `analyze_market()`。

### 禁止做法

- 不得把缺少 margin／short 的值補 0。
- 不得只調低 TPEx 分數作人工補償。
- 不得增加市場別固定加減分。

## 6. Phase 3：修正 intraday／OBI 與 Distribution Warning 語意

### 問題 A：假 OBI

`INTRADAY_SIGNAL_KEYS` 目前把 config 的 `obi` 映射到 `cvd_slope_norm`。現有 `RawTick` 只有 bid／ask 價格，沒有 Bid1~5／Ask1~5 委託量，因此沒有真正 OBI。

結果是 CVD 家族訊號被算兩次：

- `cvd` 權重使用 `net_aggressor`。
- 名稱為 `obi` 的權重實際使用 `cvd_slope_norm`。

### 問題 B：出貨警示使用錯誤欄位

`AnalysisService._exit_ctx()` 目前：

- 用 `cvd_z` 代替 `cvd_slope`。
- 用 `absorption_z > 0` 代替 `buy_absorption_rising`。
- 用橫斷面 `large_trade_delta_z` 的正負代替單股 raw large trade delta 的正負。

這些量的正負語意不同，現在持倉模式已接線，錯誤資料可能真的觸發 REDUCE。

### 實作要求

#### 3A：先停止錯誤的 Distribution Warning

修改：

- `config/thresholds.yaml`
- `app/services/decision/exit.py`
- `app/services/analysis.py`

要求：

1. 新增 `exit.distribution_enabled: false`，預設關閉。
2. `ExitContext` 的 slope／large delta／rising 欄位允許 `None`。
3. Distribution Warning 只有在 enabled 且三個真實訊號都有值時才能判斷。
4. 不得再以 z-score／level 猜測 slope 或 rising。
5. Hard Stop 與 Chip deterioration 的 EXIT／REDUCE 維持啟用。

#### 3B：把假 OBI 正式改名

修改：

- `config/thresholds.yaml`
- `app/models/signal.py`
- `app/db/models/features.py`
- `app/services/orderflow_intraday.py`
- `app/services/feature_builder.py`
- `app/services/chip/intraday_score.py`
- `app/services/analysis.py`
- API schema／frontend label／docs／tests。

要求：

1. `weights.intraday.obi` 改名為 `cvd_slope`。
2. `INTRADAY_SIGNAL_KEYS` 使用 `"cvd_slope": "cvd_slope_norm"`。
3. `IntradayFeatures.obi` 改為語意正確的 `cvd_slope_norm`。
4. `feature_daily.intraday_obi` 建議透過 Alembic rename 為 `cvd_slope_norm`，保留既有歷史值。
5. UI 不得再顯示 OBI，除非未來真的取得五檔委買委賣量。
6. 真 OBI 留在 Phase 3 realtime 待辦，不從 bid／ask price 或 CVD 推導。
7. 本輪只改名與資料契約，不私自調整 0.10 權重；是否保留兩個相關 CVD 因子另走 OOS 驗證。

### 未來重新啟用 Distribution Warning 的必要資料

若日後要開啟，必須新增並明確保存：

- 單股時間序列的 `cvd_slope_norm`，不是橫斷面 `cvd_z`。
- 單股 raw／bounded `large_net`，不是橫斷面 z 的正負。
- 可證明「增加中」的 buy absorption 時序比較，例如後半段相對前半段，而不是 `absorption_z > 0`。

在三者都有定義、測試及至少多日逐筆驗證前，`distribution_enabled` 維持 false。

### 測試

1. `distribution_enabled=false` 時，任何替代 z 值都不得觸發 Distribution Warning。
2. Hard Stop、score<40 EXIT、score<50 REDUCE 行為不變。
3. 全 repo 不得存在 `"obi": "cvd_slope_norm"`。
4. config intraday keys 與 signal mapping 必須完全一致。
5. migration 前後歷史 `intraday_obi` 數值完整保留到新欄位。
6. Unknown aggressor 仍為 0，不得因改名被歸入 BUY／SELL。

### 驗收

- API、DB、config、前端不再把 CVD slope 稱為 OBI。
- 持倉模式不會用橫斷面 z 值偽造出貨警示。
- 不影響硬停損與 Chip Score 門檻式出場。

## 7. Phase 4：修正持倉 TP 與移動停損

### 問題

持倉模式使用：

```python
risk = max(entry_price - stop_loss, 0.0)
```

若使用者把停損上移到成本以上，這是合法的獲利保護，但 risk 會變成 0，TP1／TP2 都退化為成本價。實際重現：成本 100、停損 105、現價 110，輸出 HOLD 且理由為已達 TP1／TP2 100。

### 實作要求

修改：

- `app/services/decision/__init__.py`
- `app/services/decision/exit.py`
- `app/services/analysis.py`
- `app/api/stocks.py`
- `frontend/app/stocks/[symbol]/page.tsx`
- API types／tests。

建議採最小、安全方案：

1. 使用者停損低於成本時，可暫時以 `entry_price - stop_loss` 推導 R 倍 TP。
2. 使用者停損等於或高於成本時，無法從「目前移動停損」重建初始 R：
   - TP1／TP2 回傳 `None`。
   - reason 顯示 `停損已移至成本以上，無初始風險資料，未自動推導 TP`。
3. 若停損高於或等於現價，Hard Stop 仍須立即 EXIT。
4. 不要把 `stop_loss > entry_price` 當成非法輸入；這可能是正確的 trailing stop。
5. 不新增持倉資料庫或自動保存使用者持倉。

另一個較完整但非必要方案，是 API 額外接受 `initial_stop_loss`，讓目前 trailing stop 與初始 R 分離。除非 UI、API、測試能一次完整支援，否則本輪採上面的最小方案。

### 測試

1. `stop < entry < current`：可正常顯示尚未達到的 R 倍 TP。
2. `entry <= stop < current`：HOLD／REDUCE 可成立，但 TP1／TP2 為空且 reason 正確。
3. `stop >= current`：EXIT。
4. 已達 TP1、未達 TP2：只隱藏 TP1。
5. 未提供 stop 時：現有 ATR 預設停損行為不變。

### 驗收

- 不再出現 TP1／TP2 同時退化成成本價的結果。
- trailing stop 高於成本不會被誤判為非法。
- Hard Stop 優先級不變。

## 8. Phase 5：讓 forward report 快取可靠失效

### 問題

`forward_report._cache_key()` 使用最大日期、筆數與公司行動 `created_at`。以下情況內容已改但 key 可能不變：

- `rebuild_signals` 用 upsert 覆寫相同日期與相同筆數。
- 修正既有 DailyPrice 值但沒有新增列。
- 更新既有 CorporateAction，但 `created_at` 不變。
- API 與重建腳本為不同 process，無法靠記憶體 `cache.clear()` 通知。

### 實作要求

修改：

- Alembic migration。
- `app/db/models/features.py`
- `app/db/models/market.py`
- `app/repositories/upsert.py`
- `app/services/forward_report.py`
- 相關 importer／persist 測試。

建議採可靠的資料版本方案：

1. 為 `signal_snapshot`、`daily_price`、`corporate_action` 增加 `updated_at`。
2. 新增列時 `updated_at` 使用 DB `now()`。
3. `upsert_many()` 對具有 `updated_at` 的 model，在 conflict update 時明確設為 DB `now()`；不要只依 ORM `onupdate`，Core upsert 不保證自動觸發。
4. `_cache_key()` 使用三張表的 `max(updated_at)`，並保留 backtest config hash。
5. 不再宣稱 count／max date 能偵測同筆更新。
6. 修正 `evaluated_latest_pending`：目前在先過濾掉 `entry is null` 後才計算，可能漏掉真正等待下一交易日的最新訊號。pending 應在丟棄無 entry 列之前計算，並明確定義是「尚無進場日」或「尚無最短 horizon 出場價」。

若不希望新增三欄，可採 config 化短 TTL 作為較弱替代，但必須在文件清楚標示最多可能 stale 幾秒；不接受永久只靠 row count 的方案。

### 測試

1. signal snapshot 同 key upsert、筆數與日期不變，cache key 必須改變。
2. DailyPrice 同 key修正價格，cache key 必須改變。
3. CorporateAction 同 key修正 factor，cache key 必須改變。
4. 不重啟 API，也能讀到重建後的新 validation 結果。
5. 新增價格列仍會正常失效。
6. 最新訊號沒有下一交易日價格時，pending 必須增加。

### 驗收

- `docs/11-local-rebuild-sop.md` 中的快取說明與實際機制一致。
- API restart 可保留為部署保險，但不再是得到正確結果的必要條件。

## 9. Phase 6：完成 threshold config 化

### 問題

目前仍有策略／統計視窗硬編碼：

- `app/services/feature_builder.py`：SBL 20、法人 5、MA20、ATR14 所需列數、產業最低 5 檔、swing low 10 根。
- `app/services/forward_report.py`：每日 IC 最低 20 檔。
- `app/services/market_score.py`：MA20／MA60、斜率 5、波動 20 等視窗。

把數字搬到模組常數不算 config 化。

### 實作要求

在 `config/thresholds.yaml` 增加清楚命名的設定，例如：

```yaml
features:
  flow_window_bars: 5
  ma_bars: 20
  atr_period: 14
  swing_low_bars: 10
  sbl_lookback_bars: 20
  industry_min_members: 5

validation:
  min_ic_names: 20

market_regime:
  ma_short_bars: 20
  ma_long_bars: 60
  slope_lookback_bars: 5
  volatility_bars: 20
```

要求：

1. 實作以 `Thresholds.get()` 讀取。
2. ATR 所需最小 bar 數由 `atr_period + 1` 推導，不另設重複門檻。
3. `LOTS_TO_SHARES=1000` 這類市場單位常數不是 threshold，可保留。
4. config 缺鍵時允許使用與現況相同的預設值，但正式 YAML 必須完整列出。
5. 更新 `docs/07-ops-config.md`。

### 測試

- 用測試 Thresholds 覆寫各視窗，證明函式實際讀 config，而不是仍使用模組常數。
- `rg` 檢查不可再找到代表上述門檻的硬編碼模組常數。
- 預設 config 下結果應與修正前一致。

## 10. RR 修正的後續研究，不混入本輪 BUG patch

目前非突破股票的 RR 已改用壓力位，原本「用 TP1 反算 RR」的恆等式問題已解除。

仍待驗證：

- `resistance_lookback_bars=60` 是否把目標放得太遠。
- 突破分支使用 `breakout_target_atr=4.0` 是否仍過度樂觀。
- 近 20 日壓力、近 60 日壓力與 ATR projection 對實際 MFE 的關係。

本輪只要求：

1. API／reason 能看出 RR 使用的是 resistance 還是 ATR projection。
2. config 註明 breakout target 仍未經 OOS 驗證。
3. 不在這次修 BUG 時根據現有 130 日樣本挑出最好參數。
4. 完成其他評分修正並重建後，再跑敏感度報告。

若產品要求「RR 必須完全來自獨立價格結構」，則在 breakout target 未驗證前應將 breakout 標的保守輸出 WATCH；這屬產品決策，不要由 coding agent 靜默選擇。

## 11. 建議 commit 分批

### Commit A

```text
fix(scoring): require same-day market context and availability-aware groups
```

包含 Phase 1、Phase 2。這一批會改變 Chip Score 與 BUY 結果。

### Commit B

```text
fix(decision): make intraday and position-exit semantics truthful
```

包含 Phase 3、Phase 4 與必要 migration。不得混入因子調權重。

### Commit C

```text
fix(validation): invalidate report cache on same-row updates
```

包含 Phase 5、pending 定義與 migration。

### Commit D

```text
refactor(config): move remaining strategy windows to thresholds yaml
```

包含 Phase 6；預設值下不得改變數值行為。

### Commit E

```text
docs: update post-fix verification and rebuild instructions
```

更新：

- `CLAUDE.md` 現況與限制。
- `docs/03-algorithms-scoring.md`。
- `docs/04-decision-risk.md`。
- `docs/06-backtest-validation.md`。
- `docs/07-ops-config.md`。
- `docs/10-bug-fix-todo-2026-09-13.md`。
- `docs/11-local-rebuild-sop.md`。

## 12. 最終驗證順序

coding agent 必須依序執行：

```bash
.venv/bin/python -m compileall -q app scripts tests
.venv/bin/python -m pip check
.venv/bin/alembic heads
APP_ENV=test .venv/bin/python -m pytest
cd frontend && pnpm exec tsc --noEmit
cd frontend && pnpm lint
```

再做以下整合驗證：

1. 建立只有前一日 MarketDaily 的今日 feature，確認今日不得沿用前日 regime。
2. 模擬 TAIEX 失敗，確認 EOD 顯示 degraded、訊號可產生，但 BUY 被 market unknown gate 擋下。
3. 模擬 TPEx margin 失敗，確認 TPEx 與完整 institutional 股票不混在同一 percentile group。
4. 使用會觸發舊 Distribution Warning 的資料，確認預設不再錯誤 REDUCE。
5. 成本 100、停損 105、現價 110，確認不再產生 TP1=TP2=100。
6. 同筆 signal snapshot 覆寫不同分數，確認不重啟 API 也能看到新 validation 結果。
7. 執行 migration upgrade，確認舊 intraday slope 值與三張表 updated_at 正常。
8. 執行 migration downgrade／upgrade smoke test。
9. `git diff --check` 必須通過。

前端既有 7 errors／1 warning 若仍存在，可在交付中列為既有技術債；本輪不得增加新的 lint error。

## 13. 重建與回測時點

### 修正完成前

- 不執行最終全量 `rebuild_signals`。
- 可以使用小型 fixture／測試庫驗證行為。

### Commit A～D 全部完成並部署 migration 後

依 `docs/11-local-rebuild-sop.md`：

1. 確認本機與線上 commit、config 完全相同。
2. 全量重建 `feature_daily`、`market_daily`、`signal_snapshot`。
3. 重啟 API 作為部署保險。
4. 跑 `scripts.score_monotonicity`。
5. 核對 `/validation` 的 IC、NW t、bucket return 與 MAE。
6. 把結果寫回 `CLAUDE.md`，不得只記 BUY 數量。

注意：歷史重建仍屬樣本內結果。誠實 OOS 證據從修正部署後每日新產生的 snapshot 開始累積。

## 14. Coding Agent 交付格式

最終回報必須列出：

1. 已完成項目，逐項對應本文件 Phase／驗收條件。
2. 新增 migration revision 與 upgrade／downgrade 結果。
3. API／schema／frontend 型別變更。
4. 新增及修改測試。
5. 完整 pytest、TypeScript、lint 結果。
6. 是否改變 Chip Score、BUY／WATCH、REDUCE／EXIT 行為。
7. look-ahead 檢查結果。
8. 缺資料與降級模式檢查結果。
9. 是否需要全量重建。
10. 尚未完成、需要人決定或需要 OOS 驗證的項目。

## 15. 可直接交給 Coding Agent 的任務文字

```text
請依 docs/12-post-fix-review-action-plan-2026-09-13.md 分 Phase 修正，先完整閱讀文件中
Phase 0 指定的專案文件與現行程式。

優先完成：
1. 同日 MarketDaily 與 market unknown WATCH gate。
2. institutional 子項 availability signature 分組。
3. 關閉使用錯誤替代欄位的 Distribution Warning，將假 OBI 正式改名為 CVD slope。
4. 修正 trailing stop 高於成本時 TP 退化。
5. forward report 對同筆更新可靠失效並修正 pending。
6. 剩餘策略／統計視窗 config 化。

嚴禁用前日市場資料、0 或 z-score 冒充未知／slope／rising；嚴禁沒有五檔量卻稱 OBI；
嚴禁在這次修 BUG 時依現有樣本調權重或宣稱策略有效。

每一批先寫失敗測試再修正，依文件建議拆 commit。全部完成、migration 與測試通過後，
先回報行為差異，不要自動執行線上全量重建或部署。
```
