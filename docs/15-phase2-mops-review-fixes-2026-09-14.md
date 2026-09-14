# 15 · Phase 2 MOPS 審查修正紀錄（2026-09-14）

## 1. 結論

初版 MOPS shadow 基礎建設完成後，審查發現五類會破壞 TPEx 涵蓋、NULL 語意或 honest OOS 的問題。修正已包含在主功能 commit `60dd6a6`，migration head 為 `d4f7a2c9e1b3`；線上 migration、TWSE／TPEx 單日 smoke、forward provenance 與無歧義案例於 2026-09-14 驗收後，排程由 commit `4e72f35` 開啟。

本文件是修正決策與驗收紀錄；現行完整資料契約以 [docs/14](14-phase2-mops-data-sources.md) 為準。

## 2. 修正一：TPEx canonical market

### 問題

初版部分 MOPS 資料使用 `TPEX`，但股票主檔使用 `TPEx`。回補選股、coverage 與特徵建構均依字串相等連接，造成上櫃資料可能完整匯入卻完全不被使用。

### 修正

- parser／import service 只接受 `TWSE`、`TPEx`。
- migration 將 MOPS raw 與 coverage 的 `TPEX` 正規化為 `TPEx`。
- coverage 已存在等價 `TPEx` 鍵時，先刪除錯拼的重複列再更新，避免唯一鍵衝突。
- downgrade 不把拼法改回錯值。

### 驗收

上櫃股票能匹配轉讓 coverage、建立 shadow 特徵；回補目標包含 TPEx 股票；非 canonical market 直接拒絕。

## 3. 修正二：forward job 主動解析修正申報

### 問題

每日 forward job 不會回頭重抓舊申報日，不能依賴舊頁日後出現的 `superseded_on`。若新申報沒有主動指向舊申報，舊、新兩筆會在視窗內重複計算。

### 修正

- parser 從新申報的異動文字解析 `amends_report_date`。
- 新申報可用後，依 `symbol + 舊申報日 + reporter_role + reporter_name` 找舊列。
- 唯一匹配才取代；零筆或多筆候選將該標的轉讓因子設為 NULL。
- 取代在 method category 過濾前進行，正確處理 market ↔ gift 等方式變更。
- 舊申報日已在研究視窗外時不要求匹配，避免製造假歧義。

### 驗收

涵蓋 market→gift、gift→market、找不到舊申報、多候選、舊申報在視窗外、純 `superseded_on` 回補案例及實際 2442 fixture 的 amendment chain。

## 4. 修正三：嚴格 NULL 傳遞

### 問題

部分已知值加總會把未知分項吃掉，產生看似精確的持股、設質或轉讓比率。

### 修正

- 去重後任一 holder 的 `current_shares` 未知，持股合計為 NULL。
- 持股大於零者任一 `pledged_shares` 未知，設質比例為 NULL。
- continuing holder 任一月份持股未知，月變化為 NULL。
- 轉讓的自有／信託預定股數必須同時已知；官方 total 不一致時為 NULL。
- singleton 橫斷面 z 為 NULL；多檔同值才是有效 `0.0`。

### 驗收

完整資料的既有計算不變；未知、有效零與資料品質異常能明確區分。

## 5. 修正四：coverage scope 與首次觀測 provenance

### 問題

初版 coverage 唯一鍵只有 dataset／market／date，無法記錄逐公司持股頁的成功零筆結果。Raw 與 coverage 也未保存資料是 forward 還是 backfill 首次取得，導致回補資料可能被誤標為誠實 OOS。

### 修正

Migration `d4f7a2c9e1b3`：

1. `mops_fetch_coverage` 新增 `scope_key`，唯一鍵改為 `(dataset, market, data_date, scope_key)`。
2. 轉讓 scope 為 `*`；持股 scope 為股票代號。
3. `insider_holding_monthly`、`insider_transfer_declaration`、`mops_fetch_coverage` 新增 `ingestion_mode`、`observed_at`。
4. `mops_shadow_feature_daily` 新增 `holding_provenance`、`transfer_provenance`。
5. migration 前舊列標成 `unknown`，不能事後猜測為 forward。
6. 重匯只更新業務欄位與最後抓取資訊，不覆寫首次觀測 provenance。

Shadow provenance 分為 `point_in_time_safe`、`backfill_derived`、`mixed`、`unknown`；honest OOS 只收第一類。

### 驗收

- backfill 後再 forward 匯入，不得把首次 backfill 改標 forward。
- holding raw、transfer coverage、申報列與持股分母任一混入 backfill，對應因子不得進 honest OOS。
- parser error、忙碌頁、阻擋頁與年月不符不寫 coverage；可信的零筆頁會寫 coverage 並在下次略過。

## 6. 修正五：排程與 degraded 修復流程

### 問題

MOPS 外部來源不穩定；若整個 job fail-fast，單一市場或單一資料集會阻斷其他可用資料。另一方面，若用歷史 backfill 修補剛發生的 forward 缺口，又會永久污染該視窗 provenance。

### 修正

- 每一來源獨立 fail-soft，結果回報 `ok`、`failed`、`warnings`、`degraded`。
- amendment 歧義和總股數不一致不阻斷其他標的，但必須列 warning 並讓該標的因子為 NULL。
- 排程依 config 啟用，週一至週六 08:10 處理前一日，test env 永不排程。
- 日期依設定時區計算，不依賴主機 local timezone。
- 與 EOD、回補及系統工作共用單飛鎖。
- 某日轉讓網頁 degraded 時，以 `app.jobs.mops <date> --skip-holdings` 盡快補跑，仍標 forward；禁止用 `backfill_mops_transfers` 冒充 forward 修復。

## 7. TPEx 憑證鏈

Vultr 所解析到的部分 TPEx 節點會間歇漏送 TWCA 中繼憑證。專案已加入 `app/connectors/certs/twca_intermediates.pem`，TPEx MOPS OpenAPI 共用 connector 的自訂 SSL context。此檔被明確排除於一般 cert ignore 規則之外；部署與輪替時不得漏帶，並需在憑證到期前更新。

## 8. Migration 與回復語意

- Revision：`d4f7a2c9e1b3`
- Down revision：`c9d2e7f1a3b5`
- 只修改四張 Phase 2 MOPS 表，不碰 Phase 1 表、`stock` 或正式分數資料。
- 新增 NOT NULL provenance 欄時先以 server default 填既有列，隨即移除 default，使 DB 與 ORM 一致；新寫入必須明確給值。
- Downgrade 會刪除逐公司 holding coverage，避免退回舊唯一鍵時衝突；canonical `TPEx` 拼法不回退。

## 9. 測試與不變量

對應測試集中在：

- `tests/test_mops_review_fixes.py`
- `tests/test_mops_features_db.py`
- `tests/test_mops_migration.py`
- `tests/test_mops_research.py`
- `tests/test_mops_parsers.py`

核心不變量：

1. MOPS shadow 資料不改 Phase 1 Chip Score、breakdown、action 或 `data_version`。
2. Backfill 永不標 robust。
3. 無法證明 point-in-time 的值不得進 honest OOS。
4. 未知不得以零或部分加總替代。
5. 排程不代表因子已驗證；正式權重仍不存在。

## 10. 後續

- 持續監控每日兩市場 coverage、degraded、warnings 及 provenance 成長。
- 累積至少 `mops.research.min_honest_test_days` 個有效 test 橫斷面日後才可判斷；跨 regime 證據仍是必要條件。
- 在此之前只執行 shadow research，不新增正式權重，也不宣稱策略有效。

