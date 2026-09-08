# 交接說明 — 公司行動還原的兩個後續：成交量還原 ＋ 還原值可觀測端點

> **✅ 已完成（2026-09-09）**。以下正文保留為當時的分析與陷阱紀錄，**§3/§8 的實作建議已被實作結果取代，勿再照做**；差異見文末 §9「完成紀錄」。

> 產生日期：2026-09-09 · 最新 commit：`8bcbb8b` · alembic head：`4b75aa8390d8` · 測試：137 passed · 線上已回補 corporate_action 至 2026-09-08

給接手的新對話。**先讀 `CLAUDE.md`**（尤其前端設計約束、鐵則 4/5/6/8）。本文件只聚焦這兩個後續任務；公司行動還原的「已完成」部分請看程式碼與近 4 個 commit（`fe69dd8`、`6e4580a`、`e1be588`、`8bcbb8b`）。

---

## 1. 任務目標

上一段工作已把**除權除息（TWT49U）＋ 面額變更/拆股（TWTB8U）＋ 減資（TWTAUU）** 的價格斷點用「後復權」修好（feature 的 MA/ATR/日報酬/close_vs_ma20 與 backtest 報酬皆已還原）。剩兩個後續：

- **任務 A — 成交量還原**：目前只還原「價」、未還原「量」。拆股/配股/減資會改變流通股數，使**歷史成交量與現量尺度不一**，污染 `avg_vol20`（20 日均量），而 `avg_vol20` 是法人/融資/借券強度正規化的分母（見 `feature_builder.build_features`）。目標：對「有股數變動」的事件，把歷史量也依因子調整，讓 `avg_vol20` 連續。
- **任務 B — 還原值可觀測端點**：還原後的 `ma20/atr14/close_vs_ma20` 只存在 `feature_daily`，沒有 API 逐日輸出；`chart` 端點回的是原始 `daily_price`；AVOID 股的 `/analysis` 不輸出這些欄位。導致像 **6949 沛爾生醫\*-創（面額 1:20 變更，1490→74.5）** 這種 AVOID 股，無法從 API 直接核對還原效果。目標：開一個可依日期查 `feature_daily` 還原值的端點，讓還原效果可被人工核對。

完成後：任務 A 讓拆股股的量比/正規化正確；任務 B 讓「還原是否生效」可被直接看到（例如 6949 於 09-08 的 `close_vs_ma20` 應 ≈ 0，而非未還原時的 ≈ -94%）。

---

## 2. 目前系統狀態（精簡）

- 公司行動 ingestion 與還原：見 `app/services/price_adjust.py`（後復權純函式）、`app/repositories/corporate_actions.py`（`load_actions`）、`app/services/feature_builder.py::_price_features`（價還原套用點）、`app/backtest/runner.py::load_bars`（backtest bar 還原）、`app/importers/twse.py`（`parse_ex_dividend` / `parse_resume_reference`）、`app/connectors/twse.py`（`fetch_ex_dividend` / `fetch_par_change` / `fetch_capital_reduction`）。
- 資料表：`corporate_action`（`app/db/models/market.py::CorporateAction`），欄位 `kind`（權/息/權息/面額/減資）、`prev_close`、`reference_price`、`adj_factor`（=參考價/前收）。
- 線上（vultr, docker compose）**已回補 `corporate_action` 全視窗 2026-06-01~09-08、並用還原價重建 feature_daily**；每日 EOD 已接三來源 fail-soft，往後自動累積。
- `/system` 頁「資料涵蓋」已含 `corporate_action` 一列。
- 還原慣例（**關鍵**）：**後復權** = 最新一根維持原始價（factor=1），較早 bar 乘上「其後所有事件因子的累積」；bar 日 `d` 的因子 = ∏ `adj_factor`(ex_date > d)。減資 `adj_factor>1`（價漲）同樣正確。

---

## 3. 實作計畫（建議）

### 任務 A：成交量還原
1. **難點先決策（見 §8）**：`adj_factor`（=參考價/前收）對 **面額變更/減資是純股數變動 → 量因子 = 1/adj_factor**（乾淨）；但 **除權息混了現金股利（不改股數）與配股（改股數）**，光靠 `adj_factor` 無法分離出「股數變動比例」。純除息（`kind='息'`）根本不該調量。
2. 在 `corporate_actions.load_actions` 一併帶回 `kind`（目前只回 `(date, factor)`），或新增一個載入量因子的函式。
3. 在 `feature_builder._price_features`：對 `vol`（算 `avg_vol20` 用）套用「量因子」的後復權（歷史量 × 累積量因子）。**注意 `vwap = 當日 turnover/volume` 是同日比值，不要動**。
4. 對 `kind='息'` 不調量；`面額`/`減資` 用 `1/adj_factor`；`權`/`權息` 依 §8 的決策處理。
5. backtest 端目前不使用量（`load_bars` 只取 OHLC），可不動；若日後 backtest 用量再說。

### 任務 B：還原值可觀測端點
1. 新增 `GET /api/stocks/{symbol}/features?date=YYYY-MM-DD`（預設最新），回 `feature_daily` 的 `close/ma20/atr14/vwap/close_vs_ma20_pct/close_vs_vwap_pct/change_pct`（皆已是還原值）。
2. 重用 `app/repositories/features.py::FeatureDailyRepository`（已有 `get_latest`/`get_on_or_before`/`list_on_date`；可能要加 `get_on_date(symbol, date)`）。
3. 端點放 `app/api/stocks.py`（`_resolve_date` 已有，處理 date 預設）；型別加到 `frontend/lib/api.ts`（若要前端用）。
4.（選用）前端個股頁顯示還原後 MA/ATR，或 `/system` 加一個「還原核對」小工具。

---

## 4. 要重用的既有元件

| 需求 | 直接用 |
|---|---|
| 後復權計算 | `app/services/price_adjust.py::back_adjust` / `cumulative_factors` |
| 載入某股公司行動 | `app/repositories/corporate_actions.py::load_actions`（回 `{symbol:[(ex_date,factor)]}`；任務 A 需擴充帶 `kind`） |
| 價/量特徵計算點 | `app/services/feature_builder.py::_price_features`（量還原改這裡） |
| 讀 feature_daily | `app/repositories/features.py::FeatureDailyRepository` |
| 事件型別判斷 | `CorporateAction.kind`（權/息/權息/面額/減資） |
| 冪等 upsert | `app/repositories/upsert.py::upsert_many` |
| 型別化前端 client | `frontend/lib/api.ts`（`NEXT_PUBLIC_API_BASE`） |

---

## 5. 必知陷阱（本次對話才知道的坑）

- **TWSE 端點怪癖**：TWT49U 在 `/exchangeReport/TWT49U`（`/rwd/zh/afterTrading/TWT49U` 會回 404 HTML）；TWTB8U 在 `/rwd/zh/change/TWTB8U`；TWTAUU 在 `/rwd/zh/reducation/TWTAUU`（TWSE 拼字 "reducation"）。**日期參數是 `startDate`/`endDate`（西元 YYYYMMDD），`date` 參數無效會回「當日」**。三者歷史區間都可查（**與 SBL 不同**，SBL 歷史抓不回）。
- **日期格式不一**：TWT49U 的「資料日期」是 `115年09月09日`（CJK，用 `parse_roc_cjk_date`）；TWTB8U/TWTAUU 的「恢復買賣日期」是 `115/09/07`（斜線，用 `parse_roc_date`）。
- **量因子 ≠ 價因子**：除權息混現金股利與配股，`adj_factor` 不能直接當量因子（見 §3 A / §8）。這是任務 A 的核心陷阱，別直接 `1/adj_factor` 套到所有 kind。
- **觀測限制**：還原值只在 `feature_daily`；`chart` API 回原始 `daily_price`（設計上保留原始價，別改成還原）；AVOID 股的 `/analysis` 不輸出 MA/ATR（entry/risk 為 null）——這正是任務 B 要補的。
- **6949 是最佳驗證案例**：面額 1:20 變更（恢復買賣日 09-07，前收 1490→參考 74.5，factor 0.05）。未還原時 09-08 的 `close_vs_ma20` ≈ -94%（MA20 被拆股前 ~1200 拉爆）；還原後應 ≈ 0。1563 巧新（減資 09-07）是已驗證的正例（BUY、ATR/停損合理）。
- **前端**：台股**紅漲綠跌**（與美股相反，見 `lib/format.ts`）、**無斜體**；dev HMR 在沙箱會卡 hydration，驗證用 `pnpm build && pnpm start`。
- **測試不需 migration**：`conftest.py` 每 test 由 `Base.metadata` 重建 schema，新增 model 只要有 import 進 metadata（`app/db/models/__init__.py`）就會被建；但**上 prod 一定要 `alembic revision --autogenerate` + `upgrade head`**。
- **DB 連線池已修**：`/api/ops/status` 的 coverage 已改背景快取（`app/api/ops.py`，stale-while-revalidate），別把重查詢搬回請求路徑（會重演 QueuePool 耗盡）。
- **range 回補很重**：全市場 × ~70 平日約 70 分；只驗單股改動不需要，跑測試即可。

---

## 6. 環境與執行

- 依賴/DB/啟動一律見 `CLAUDE.md`「環境與指令」。重點：
  - 測試：`APP_ENV=test python -m pytest -q`（本機 venv：`.venv/bin/python`）
  - 起 API：`APP_ENV=dev uvicorn app.main:app --reload`；前端 `cd frontend && pnpm dev`
  - migration：`APP_ENV=dev alembic revision --autogenerate -m "..."` → `alembic upgrade head`
  - 探 TWSE 欄位（不打壞 DB）：直接用 `httpx` 打端點看 `fields`/`data`（本次即如此驗證形狀）
- **線上部署（vultr）**：`git pull` → `docker compose exec -T api alembic upgrade head`（有新 migration 才需）→ `docker compose up -d --build api web`。線上網址走 tailscale：`https://vultr.tail663489.ts.net`（`/system` 可觸發回補、看涵蓋）。
- 驗證還原（任務完成後）：對 6949／1563 打新端點（任務 B）或跑針對性測試比對還原前後值。

---

## 7. 驗收條件

- [ ] 測試全綠（新增對應單元測試；目前基線 137 passed）
- [ ] **任務 A**：拆股/配股/減資股的 `avg_vol20` 不再被跨事件的股數變動污染；純除息不調量；look-ahead 不破（只用 `data_date<=target`，見 `feature_builder` 既有約束）
- [ ] **任務 B**：新端點可回某股某日的還原後 `ma20/atr14/close_vs_ma20`；**用 6949（面額變更）核對 09-08 `close_vs_ma20` ≈ 0**（未還原會是 ≈ -94%）
- [ ] 若動 model → 產 migration 並在 prod `upgrade head`
- [ ] 交付格式對照 `docs/08-roadmap-delivery.md §30`（已完成/migration/API/測試/技術債/下一步/look-ahead 檢查/資料缺漏/backtest）

---

## 8. 待決策（接手時可先問使用者）

1. **除權息的量因子怎麼算**（任務 A 核心）：
   - (a) 只對 `面額`/`減資`/純 `權` 調量（用 `1/adj_factor`），`權息`/`息` 暫不調 —— 最簡、避免把現金股利誤當股數變動。**（建議先這樣）**
   - (b) 抓 TWT49U 的「權值/息值」拆分或另找「配股率」欄位，精確算配股的股數變動比例 —— 最正確、工程較大。
2. **任務 B 端點形式**：新開 `/features` 端點（乾淨、可查歷史）vs 直接把 price_context 塞進 `/analysis`（連 AVOID 也輸出）。建議前者。
3. **是否也在前端呈現**還原值（個股頁或 /system 核對工具），或後端端點即可。

---

## 9. 完成紀錄（2026-09-09）— 與上文建議的差異

**§8-1 的兩個選項都沒採用**。使用者選「精算」後，我先實作 (b) 的變體 `share_factor = max(減除股利參考價 / 除權息參考價, 1)`，出貨前拿 TWT48U 的權威「無償配股率」在 16 筆重疊資料上交叉驗證，**證明該推導在兩個方向都錯**：無償配股 7.1% 的 2442 算出 1.0（漏抓）、純現金增資的 6533 算出 1.032（誤判）。故改為**新增第四個資料源 TWT48U 除權除息預告表**，直接取「無償配股率」欄位算 `share_factor = 1 + 無償配股率`（以 `參考價 = (前收 − 現金股利)/(1 + 無償配股率)` 在 5 檔上驗到 tick 精度）。

- 新欄位 `corporate_action.share_factor`（migration `62ea86a2d817`），與 `adj_factor` 分離。`parse_ex_dividend` **刻意不輸出 `share_factor` 鍵**，TWT48U 的 importer 則以 `upsert_many(update_columns=["share_factor"])` 只寫該欄——兩來源寫同一列而互不覆蓋。
- `load_actions` 未擴充帶 `kind`，改為新增 `load_factors()` 一次查詢回 `(price_map, share_map)`；`share_factor` 為 1.0 或 NULL 的事件不進量因子 map。
- **代價（已寫進 `CLAUDE.md`）**：TWT48U 只回未來事件，**歷史除權息的配股率補不回來**（性質同 SBL），僅能靠每日 EOD 累積。面額/減資仍用 `1/adj_factor`，精確且可回補。
- **§7 驗收條件的 6949「`close_vs_ma20` ≈ 0」是估算，實測為 +10.98%，且這才是對的**：還原後序列 39.50 → 74.50 → 60.40 確實站在 MA20（54.42）之上；關鍵是 −93.76% 的假斷點消失（ma20 967.33→54.42、atr14 168.26→4.84、avg_vol20 2.43M→7.66M）。另驗 1563 減資量 ×0.80、2442 權息量 ×1.0711（＝1+無償配股率）。
- 任務 B 依 §8-2/§8-3 建議：新開 `GET /api/stocks/{symbol}/features?date=`（**只查該日、不 fallback**，才能誠實核對），前端個股頁加「還原後價格結構」卡（fail-soft，缺特徵不顯示不掛頁）。
- 測試 137 → 144 passed。
