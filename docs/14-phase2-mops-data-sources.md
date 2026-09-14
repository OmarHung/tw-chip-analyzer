# 14 · Phase 2 MOPS 資料來源、時點與 Shadow 特徵

> 狀態：2026-09-14 已完成基礎建設並啟用 forward 排程。此階段 **shadow-only**：只收資料、建立研究特徵與做 OOS 驗證，不讀寫正式 `feature_daily`／`signal_snapshot`，也不改 Chip Score、action 或任何正式權重。

## 1. 範圍與啟用門檻

本階段收集三類 MOPS 訊號：

- 董監、經理人與大股東月持股變化。
- 內部人設質比例及其月變化。
- 內部人持股轉讓事前申報中的市場交易預定股數。

這些資料目前只寫入四張 Phase 2 raw／coverage／shadow 表。即使 retrospective 結果顯著，也不能直接加入正式評分；必須累積 `point_in_time_safe` 的跨 regime forward OOS，通過本文件 §7 的門檻後另案決定方向與保守權重。Phase 1 的 §28 成功標準仍未達成，MOPS 不得被描述為已驗證 alpha。

### 1.1 Connector 共通規則

實際 URL 與 request payload 集中在 `app/connectors/mops.py`。TPEx 使用專案封裝的 TWCA 中繼憑證鏈；不得在呼叫端自行關閉 TLS 驗證。

Connector 將錯誤分為：

- `MopsTransientError`：逾時、連線中斷、5xx、截斷 JSON；依 config 線性 backoff 重試。
- `MopsBlockedError`：403 或安全性阻擋頁；不重試，避免加重封鎖。
- `MopsHttpError`：其他非暫時性 HTTP／資料形狀錯誤。

所有 timeout、retry、backoff、throttle 與排程時間都在 `config/thresholds.yaml:mops`，不得硬編碼。

## 2. 持股與質押資料契約

### 2.1 資料來源

| 來源 | TWSE | TPEx | 能否回補 | 用途 |
|---|---|---|---|---|
| OpenAPI | `t187ap11_L` | `mopsfin_t187ap11_O` | 否，只提供最新一期 | 每日 forward 累積的主要來源 |
| MOPS 網頁 | `ajax_stapap1`，`TYPEK=sii` | `ajax_stapap1`，`TYPEK=otc` | 是，逐公司逐月 | retrospective 研究與缺口補齊 |

### 2.2 原始列與去重

一列代表「某公司、某月份、某來源版本、某職稱、某姓名的一列官方揭露」。`(職稱, 姓名)` 不是唯一鍵：法人董事可能一人多席，也可能存在同名不同人，因此另存來源順序 `row_seq`，忠實保存原始列並維持重匯冪等。

建特徵時，同一姓名多職稱可能重複揭露相同股數，聚合會以姓名去重並取已知最大值；這是避免重複累加，不代表原始資料列可以刪除。名單進出可能只是改選，月變化只比較前後月都存在的 continuing holders。

同月若有多個來源／修正版，只使用 `available_at <= as_of` 中最晚可用的一整組快照，不跨版本混列；時間相同時優先 OpenAPI，因其有官方出表日期。

### 2.3 日期與揭露時點

- `data_date`：資料月份的月底。
- `report_date`：OpenAPI 的出表日期；網頁無出表日期時存推定揭露日。
- `available_at`：該資料在歷史時點最早可用的保守時間，不等於抓取時間。
- `observed_at`：本系統第一次看見該列的時間，只用於 provenance，不可取代 `available_at`。

持股最早可用時間取下列兩者較晚：

1. 資料月次月第 `mops.insider_holding.available_day_of_next_month` 日 `available_hour`；目前為次月 21 日 08:00（台北）。
2. OpenAPI `report_date + publish_lag_days` 的 `available_hour`；目前 lag 1 日。

網頁回補沒有可信的原始出表日期，只採規則日，且一律標記為 backfill；它可能包含事後修正版，不可進 honest OOS。

### 2.4 網頁版本與解析安全

`ajax_stapap1` 的回應頁必須核對公司代號、上市／上櫃身分與查詢年月。忙碌頁、空白頁、公司身分不符或無法證明結果的頁面均視為 parser error，不寫 coverage。只有能明確證明的官方「該月無資料」頁可寫 `row_count=0`。

## 3. 轉讓申報資料契約

### 3.1 資料來源

| 來源 | TWSE | TPEx | 能否回補 | 用途 |
|---|---|---|---|---|
| OpenAPI | `t187ap12_L` | `mopsfin_t187ap12_O` | 否，只提供最新一日 | forward 來源與欄位交叉核對 |
| MOPS 網頁 | `ajax_t56sb12`，`report=SY` | `ajax_t56sb12`，`report=OY` | 是，逐市場逐日 | 完整申報與事後註記 |

### 3.2 轉讓方式分類

轉讓方式只在可明確判斷時分類：

- `market`：一般交易、盤後定價、鉅額逐筆或鉅額配對。
- `gift`、`trust`、`private`：贈與、信託、洽特定人。
- 其餘或混合且無法確定者為 `unknown`，不可強制歸入市場賣出。

正式研究因子只加總有效的 `market` 類申報。

### 3.3 冪等鍵與總股數

`row_hash` 只由不會被事後回寫的核心欄位產生，使 OpenAPI 與網頁同一申報能落在同一列；`amendment_note`、`superseded_on`、`unfinished_flag` 等註記不進 hash。OpenAPI 重匯不得洗掉網頁才有的事後註記。

預定轉讓總股數以 `planned_own_shares + planned_trust_shares` 計算，兩者任一未知即為 NULL。若官方 `transfer_shares` 已知但與合計不一致，該標的整個視窗因子為 NULL，不可靜默擇一。

### 3.4 變更申報與 look-ahead

MOPS 會在舊申報頁事後回寫「已於某日申報變更」，不能把今天看到的註記套回歷史：

1. 新申報若帶 `amends_report_date`，只在新申報本身 `available_at <= as_of` 後，才尋找同標的、舊申報日、申報人身分與姓名相同的舊列。
2. 唯一匹配才取代舊申報；找不到或多筆候選均視為歧義，該標的因子為 NULL，避免新舊雙算。
3. 回補頁舊列的 `superseded_on` 也只在該變更日依 T+1 規則可用後才生效。
4. 取代關係必須在轉讓方式分類前處理，因修正版可能把 market 改成 gift，或反向變更。

### 3.5 日期與揭露時點

- `data_date`：申報日期。
- `available_at`：申報日加 `mops.transfer_declaration.available_lag_days`，目前為次日 08:00（台北）。官方沒有可靠盤中申報時點，因此採 T+1 盤前的保守規則。
- `effective_start`／`effective_end`：預定轉讓期間，只保存原始語意，不可拿來提前使用申報。

## 4. 資料表與 provenance

Migration `c9d2e7f1a3b5` 新增四張表；`d4f7a2c9e1b3` 補上 canonical market、coverage scope 與 provenance。

| 表 | 粒度與責任 |
|---|---|
| `insider_holding_monthly` | 公司 × 月 × 來源版本 × 官方列；保存持股、設質與關係人原始值 |
| `insider_transfer_declaration` | 公司 × 申報日 × `row_hash`；保存轉讓方式、股數、期間及修正關係 |
| `mops_fetch_coverage` | dataset × market × data date × scope；即使成功取得 0 筆也記錄 |
| `mops_shadow_feature_daily` | 公司 × 交易日；raw ratio、橫斷面 z 與各因子的 provenance |

市場拼法必須與 `stock.market` 完全一致：`TWSE`／`TPEx`。`TPEX` 是舊 bug，禁止重新引入。

Coverage 的 `scope_key`：

- 轉讓申報一次涵蓋整個市場，使用 `*`。
- 持股網頁逐公司查詢，使用股票代號。

Raw 與 coverage 保存首次觀測：

- `forward`：每日 job 向前累積。
- `backfill`：歷史回補腳本。
- `unknown`：migration 前既有資料，無法證明來源。

衝突重匯不覆寫首次 `ingestion_mode`／`observed_at`。Shadow 特徵依所有必要輸入分類為：

- `point_in_time_safe`：全部輸入首次觀測均為 forward。
- `backfill_derived`：全部為 backfill。
- `mixed`：同時使用 forward 與 backfill。
- `unknown`：缺 provenance、migration 舊資料或沒有足夠輸入。

只有 `point_in_time_safe` 可進 honest forward OOS。

## 5. Look-ahead、coverage 與 NULL 共通規則

- Shadow 特徵的資訊截止點與正式日特徵一致，為目標交易日盤後；所有 raw row 都必須滿足 `available_at <= as_of`。
- 股票母體取自目標日有 `daily_price` 的股票，市場值只接受與 `stock.market` 相同的 `TWSE`／`TPEx`。
- Coverage 是判斷「官方確實零筆」與「根本沒抓到」的必要輸入；缺 coverage 時不能把空集合解讀成 0。
- 缺資料、過舊、視窗不足、coverage 不完整、修正歧義或必要分項未知，一律傳遞 NULL，不以中性 0 或部分加總填補。
- 只有完整視窗的所有市場日都有 coverage，且確實沒有有效 market 類申報時，`transfer_market_sale_ratio=0.0` 才成立。
- 橫斷面樣本不足時保留 NULL；「無法計算」和「計算後恰為零」必須可區分。

## 6. Shadow 特徵

所有特徵遵守 §5 的共同資料契約。

| Raw 特徵 | 定義 |
|---|---|
| `insider_holding_change_pct` | 前後月 continuing holders 持股合計變化率 |
| `insider_pledge_ratio` | 去重後內部人設質股數合計 ÷ 持股合計 |
| `insider_pledge_ratio_change` | 本月設質比例 − 前月設質比例 |
| `major_holder_change_pct` | 職稱以「大股東」開頭且前後月持續存在者的持股變化率 |
| `transfer_market_sale_ratio` | 最近 `window_bars` 個已可用交易日之有效 market 類預定轉讓股數 ÷ 最近可用內部人持股合計 |

最近持股月距目標日超過 `max_stale_months`（目前 2）時，持股相關特徵全部 NULL。轉讓視窗取市場交易日曆，少於完整 `window_bars` 或任一日缺對應市場 coverage 時為 NULL。

每個 raw 特徵另做當日全市場橫斷面 z-score，寫入同名 `*_z` 欄。有效樣本少於兩檔時 z 為 NULL；兩檔以上且值相同才是有效中性 `0.0`。

`app/services/analysis.py`、`app/services/chip/` 與正式 `weights` 不讀以上欄位。相關測試必須持續保證 MOPS 資料寫入前後的 Chip Score、breakdown 與 action 完全一致。

## 7. OOS 驗證規則

### 7.1 Provenance 分段

Retrospective research 可使用全部 shadow 資料，但只列統計、永不判定 robust。Honest forward OOS 只保留該因子 provenance 為 `point_in_time_safe` 的列；`backfill_derived`、`mixed`、`unknown` 全部排除，並在 honest 日期上重新切 train／embargo／test。

```bash
APP_ENV=dev python -m scripts.mops_factor_oos
```

報告包含：

- 1／3／5／10／20D 淨 forward return，進場為訊號後市場次一交易日 open，含正式交易成本。
- 前半 train、`max(horizons)` 交易日 embargo、後半 test。
- 每日橫斷面 Spearman rank IC、樸素 t 與 Newey–West t（lag = horizon − 1）。
- 多頭／非多頭 regime、coverage、缺漏、來源組成與 survivorship。
- 對 Phase 1 `chip_score` 排名殘差化後的增量 IC。
- retrospective 與 honest forward OOS 分開報告。

只有 honest OOS 同時符合以下條件才可標 `robust ✓`：

1. train 與 test IC 同方向。
2. test Newey–West `|t| > 2`。
3. test 有效橫斷面日不少於 `mops.research.min_honest_test_days`，目前為 20。

通過仍只代表可以提出「是否接入評分」的獨立變更案；本腳本不得自動改權重。Retrospective、backfill、mixed 或 unknown 資料永不標 robust。

## 8. 匯入、排程與回補

每日 forward job：

```bash
APP_ENV=dev python -m app.jobs.mops YYYY-MM-DD
```

流程為：上市／上櫃最新持股 OpenAPI → 指定日上市／上櫃轉讓網頁與最新 OpenAPI → 當日 shadow 特徵。單一來源失敗不阻斷其餘來源，但結果必須列入 `degraded`；修正歧義與總股數不一致列入 warnings。

排程目前啟用，週一至週六 08:10（`schedule.timezone`）處理前一日，並與 EOD、回補及系統工作面板共用單飛鎖。若某日只有轉讓網頁 degraded，應盡快執行：

```bash
APP_ENV=prod python -m app.jobs.mops YYYY-MM-DD --skip-holdings
```

這仍屬 forward 修復。不要用歷史 transfer backfill 代替，否則該視窗 provenance 會變成 backfill／mixed，不能進 honest OOS。

選擇性歷史回補：

```bash
APP_ENV=dev python -m scripts.backfill_mops_holdings --top 300 --months 12
APP_ENV=dev python -m scripts.backfill_mops_holdings --symbols 2330,5386 --months 3
APP_ENV=dev python -m scripts.backfill_mops_transfers --start 2026-03-01 --end 2026-09-11
APP_ENV=dev python -m scripts.rebuild_mops_features --start 2026-03-01 --end 2026-09-11
```

持股回補依最近成交值選前 N 檔，逐公司逐月，預設節流；成功的零筆頁也會記 coverage，失敗頁不記，重跑時會再請求。轉讓回補依 `daily_price` 交易日曆逐市場逐日執行；只要仍有失敗即以非零狀態結束，避免把涵蓋缺口藏起來。

## 9. 已知限制與操作檢查

- OpenAPI 無歷史參數，forward 資料只能從排程啟用日起累積。
- 持股網頁回補可能含事後更正，因此只能做 retrospective research。
- 轉讓申報可能被事後回寫；只有本文件 §3.4 的時點規則可避免提前取代。
- MOPS／TPEx 端點可能忙碌、截斷或重置；不能把抓取失敗當成零筆。
- TPEx 節點偶爾漏送 TWCA 中繼憑證；部署需保留 `app/connectors/certs/twca_intermediates.pem` 並在到期前更新。
- MOPS 仍是 Phase 2 shadow 資料，不能用來迴避 Phase 1 §28 尚未達成的事實。

每日／每週應檢查 `job_run`、MOPS job 的 `degraded`／warnings、兩市場 coverage、新增的 `point_in_time_safe` 日期數，以及 shadow 特徵非 NULL 涵蓋率。

## 10. 測試範圍

MOPS 測試涵蓋 fixture parser、日期與 NULL 語意、來源冪等、修正版時點、coverage、provenance、shadow-only 不變量、research 統計、排程 config、單一 Alembic head 及 upgrade／downgrade。測試不得連真實 MOPS 網路。
