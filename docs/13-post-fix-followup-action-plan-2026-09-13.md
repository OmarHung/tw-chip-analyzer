# BUG 修正複查後續修改建議書（2026-09-13）

## 1. 文件用途

本文件接續 `docs/12-post-fix-review-action-plan-2026-09-13.md`，只處理該輪修改完成後複查出的三項遺留問題，供 Coding Agent 直接實作。

上一輪主要修正已符合預期：

- 只使用目標日 MarketDaily，未知大盤會排除 market 成分並阻擋 BUY。
- institutional 依有效子項 availability signature 分組。
- 假 OBI 已改名為 CVD slope，錯誤的 Distribution Warning 已停用。
- 移動停損不再產生假的成本價 TP。
- validation 快取已加入三張來源表的 `updated_at`。
- 固定視窗已移入 `config/thresholds.yaml`。

本文件不重做上述項目，也不調整因子權重、Chip Score 門檻或交易策略參數。

## 2. 優先順序與結論

| 優先 | 問題 | 影響 | 是否影響分數資料 |
|---|---|---|---|
| P1 | 單一逐筆標的的 undefined 橫斷面 z 被補成 0 | 把不可計算誤標為中性，錯誤啟用 intraday 成分 | 只影響當日有效逐筆標的少於 2 檔的日期 |
| P2 | `updated_at` migration 允許 NULL，但 ORM 宣告 NOT NULL | DB schema 與 metadata 漂移，NULL 可能讓快取漏偵測 | 不改分數，只修 schema 約束 |
| P3 | Forward Report cache hash 漏掉 `validation.min_ic_names` | 設定變更後可能繼續回傳舊 IC 統計 | 不改 snapshot，只影響報告快取 |

建議在下一次正式全量重建前完成 P1～P3。若已完成全量重建，P1 只需確認是否存在「有效逐筆標的少於 2 檔」的歷史日期，再決定是否局部重建。

## 3. 不得違反的範圍

### 必守

1. 無法計算的橫斷面統計必須保留 `NULL`，不可補成中性 0。
2. `0` 只能代表「有足夠樣本且結果確實為中性」。
3. 缺資料的成分必須排除並重分配權重。
4. DB migration 與 SQLAlchemy metadata 的 nullable 語意必須一致。
5. Validation cache key 必須涵蓋所有會改變報告內容的資料版本與設定。
6. 不改既有權重，不依目前樣本調參，不宣稱策略有效。
7. 不引入 realtime 或自動下單。

### 本輪不做

- 不重新啟用 Distribution Warning。
- 不實作真正五檔 OBI。
- 不調整 RR、壓力位回看根數或 breakout policy。
- 不修改 institutional availability grouping。
- 不處理與這三項無關的 UI 或重構。

## 4. Phase 0：既有文件與允許沿用的模式

實作前先核對：

- `docs/03-algorithms-scoring.md §9–10`
  - 橫斷面 normalization 才能跨股票比較。
  - 缺資料成分排除，不以中性 0 灌水。
- `docs/06-backtest-validation.md §17.1`
  - Validation 報告、cache、pending 與 Newey–West 統計口徑。
- `docs/07-ops-config.md`
  - `validation.min_ic_names` 是正式 config。
- `docs/08-roadmap-delivery.md §30`
  - 交付必須說明 migration、測試、look-ahead、資料缺漏與 backtest。

允許直接沿用的程式模式：

- `app/services/feature_builder.py::_zscore_map`
  - 樣本少於 2 檔回 `{}`。
  - 兩檔以上但全部同值時，才會得到有效 `z=0`。
- `app/services/analysis.py::AnalysisService.score_features`
  - `cvd_z` 與 `large_trade_delta_z` 都是 `None` 時，不啟用 intraday 成分。
- `app/db/models/mixins.py::UpdatedAtMixin`
  - ORM 的單一真相為 non-null timezone-aware datetime。
- `app/repositories/upsert.py::upsert_many`
  - PostgreSQL Core upsert 明確使用 `func.now()` 更新 `updated_at`。
- `app/services/forward_report.py::_min_ic_names`
  - 已有正式 config reader，cache key 應使用同一份解析後數值。

禁止的反模式：

- 在 `_zscore_map(...).get()` 後使用 `0.0` fallback。
- 只改 ORM、不修實際 migration schema。
- 重寫已推到共享分支的 migration revision。
- 用 `Base.metadata.create_all()` 測試 migration 是否正確。
- cache hash 只包含 `backtest`，卻漏掉其他實際讀取的報告設定。

## 5. Phase 1：修正單一逐筆標的的假中性 Z-score

### 問題證據

`app/services/feature_builder.py::_zscore_map()` 已正確定義：橫斷面樣本少於 2 檔時回空 dict，代表 z 無法定義。

但 `_intraday_signals()` 目前使用：

```python
"cvd_z": z["cvd"].get(sym, 0.0)
```

同樣模式也出現在 `large_trade_delta_z`、`absorption_z`、`trade_speed_z`、`price_efficiency_z`。因此只有一檔 RawTick 時，空 dict 又被轉成 0，該檔會被誤認為具有有效 intraday 橫斷面資料。

純函式重現：

```text
zmap = {}
stored_cvd_z = 0.0
```

### 實作要求

修改 `app/services/feature_builder.py::_intraday_signals()`：

1. 所有橫斷面 z 欄位使用無預設值的 `.get(sym)`：

```python
"cvd_z": z["cvd"].get(sym),
"large_trade_delta_z": z["large_trade_delta"].get(sym),
"absorption_z": z["absorption"].get(sym),
"trade_speed_z": z["trade_speed"].get(sym),
"price_efficiency_z": z["price_efficiency"].get(sym),
```

2. `cvd_slope_norm` 是單股自身已正規化的有界訊號，不是橫斷面 z，可保留現有來源值。
3. 當日只有一檔逐筆標的時，五個 z-derived 欄位都應為 `None`。
4. 沿用 `AnalysisService.score_features()` 現有判斷：`cvd_z`、`large_trade_delta_z` 都是 `None` 時排除整個 intraday 成分。
5. 不要為了保留 `cvd_slope_norm` 而強制啟用 intraday。
6. 不修改 intraday 權重或 percentile mapping。

### 測試

新增至 `tests/test_intraday_composite.py` 或獨立測試檔：

1. 當日只有一個 symbol 有 RawTick：五個 z-derived 欄位全部為 `None`。
2. 該 FeatureDaily 經 `AnalysisService.score_features()` 後，`intraday` 不在 `components`。
3. 當日有兩個 symbol 且原始訊號完全相同時，z 是有效的 `0.0`，不是 `None`，且 intraday 可以啟用。
4. UNKNOWN aggressor 仍維持 0 side，不得因本修正被歸為 BUY 或 SELL。
5. 多檔正常逐筆資料的既有 z 排序與分數保持不變。

### 驗收

- `_intraday_signals()` 中沒有任何 z-derived 欄位使用 `.get(sym, 0.0)`。
- singleton 與「兩檔同值」清楚區分：前者 NULL，後者有效 0。
- 不影響無逐筆與正常多檔逐筆日的既有行為。

## 6. Phase 2：統一 `updated_at` 的 DB 與 ORM nullable 語意

### 問題證據

`app/db/models/mixins.py::UpdatedAtMixin` 使用非 Optional 的 `Mapped[datetime]`，SQLAlchemy metadata 因此把三張表的 `updated_at` 判定為 `nullable=False`。

但 `a7d0e4f2b6c8_updated_at_for_cache_invalidation.py` 建欄位時使用 `nullable=True`：

- `signal_snapshot`
- `daily_price`
- `corporate_action`

這會造成 schema drift；若資料被直接寫入 NULL，`max(updated_at)` 也無法偵測該筆內容版本。

### Migration 策略

`a7d0e4f2b6c8` 已提交並推到共享 `main`，本輪一律新增 corrective migration，不重寫舊 revision。

Upgrade 順序：

1. 對三張表補齊既有 NULL：

```sql
UPDATE <table> SET updated_at = now() WHERE updated_at IS NULL;
```

2. 對三張表設定 NOT NULL：

```python
op.alter_column(
    table,
    "updated_at",
    existing_type=sa.DateTime(timezone=True),
    nullable=False,
)
```

Downgrade 只把 nullable 還原為 `True`，不需要把任何值改回 NULL。

### 測試與驗證

1. `alembic heads` 只能有一個 head。
2. 對實際由 Alembic upgrade 建出的測試 schema 使用 SQLAlchemy inspector，確認三張表 `updated_at.nullable == False`。
3. 直接插入明確 `updated_at=NULL` 必須被 DB 拒絕。
4. 不傳 `updated_at` 的正常 insert 仍由 server default 寫入時間。
5. `upsert_many()` conflict update 後 `updated_at` 仍會改變。
6. 執行 downgrade／upgrade smoke test，確認資料與 index 保留。
7. 執行 `alembic check`，不得再出現這三欄的 nullable diff。

只用 `Base.metadata.create_all()` 建表會直接依 ORM 生成 NOT NULL，無法證明 migration 正確，因此不可把它當本項驗收證據。

### 驗收

- Production schema、test migration schema 與 ORM metadata 三者一致。
- 三張表既有資料沒有 NULL `updated_at`。
- 原本依 `max(updated_at)` 的 validation cache invalidation 行為不退化。

## 7. Phase 3：把所有 Validation 報告設定納入 cache key

### 問題證據

`build_forward_report()` 會讀取 `thresholds.backtest` 與 `validation.min_ic_names`，但目前 `_cache_key(session, bt)` 只 hash `backtest`。若 process 內 reload 設定後只改 `min_ic_names`，資料表版本與 cache key 都不變，可能直接回傳使用舊門檻計算的 IC 報告。

### 實作要求

修改 `app/services/forward_report.py`：

1. 建立單一 report-config payload，至少包含：

```python
{
    "backtest": thresholds.backtest,
    "validation": {
        "min_ic_names": min_names,
    },
}
```

2. `_cache_key()` 對完整 payload 做 deterministic hash：

```python
json.dumps(report_config, sort_keys=True, default=str)
```

3. 實際計算值與 cache payload 必須來自同一個 `min_names`。
4. 更新 `tests/test_forward_cache.py` 內 `_cache_key()` 的呼叫介面。
5. 保留三張表的 `max(updated_at)` 與 row count；不要退回只看日期或只清 process memory cache。

### 測試

1. 相同資料、相同 backtest config、`min_ic_names=20` 與 `10` 的 cache key 必須不同。
2. 先產生並快取報告，再只修改／reload `validation.min_ic_names`，下一次結果必須重算。
3. 只改 SignalSnapshot、DailyPrice、CorporateAction 內容的既有失效測試繼續通過。
4. Backtest costs、horizons、entry price、bucket 任一設定改變仍會失效。
5. 相同資料與完整設定時仍命中 cache。

### 驗收

- Cache key 涵蓋報告實際讀取的全部設定。
- 不需要重啟 API 就能在 reload 後取得新 IC 統計。
- `docs/06-backtest-validation.md` 的快取說明更新為「報告設定 hash」，不要只寫 backtest hash。

## 8. 建議 Commit 拆分

### Commit A

```text
fix(features): preserve undefined singleton intraday z-scores
```

只包含 Phase 1 與測試。

### Commit B

```text
fix(db): enforce updated_at not-null constraints
```

只包含 corrective migration 與 migration/schema 測試。

### Commit C

```text
fix(validation): include all report settings in cache key
```

只包含 Phase 3、測試與必要文件更新。

禁止把權重、RR、UI 美化或無關 refactor 混入上述 commit。

## 9. 最終驗證順序

Coding Agent 完成後依序執行：

```bash
git diff --check
python -m compileall -q app scripts tests
python -m pytest -q tests/test_intraday_composite.py
python -m pytest -q tests/test_forward_cache.py
python -m pytest
alembic heads
alembic upgrade head
alembic check
```

Migration smoke test 需在可回復的測試 DB 執行：

```bash
alembic downgrade a7d0e4f2b6c8
alembic upgrade head
```

然後檢查：

```sql
SELECT
  (SELECT count(*) FROM signal_snapshot WHERE updated_at IS NULL) AS snapshot_nulls,
  (SELECT count(*) FROM daily_price WHERE updated_at IS NULL) AS price_nulls,
  (SELECT count(*) FROM corporate_action WHERE updated_at IS NULL) AS action_nulls;
```

三個結果都必須為 0。

前端沒有預期行為變更；仍建議執行：

```bash
cd frontend
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

## 10. 重建與部署判斷

### 若尚未進行上一輪全量重建

1. 先完成本文件三個 commit。
2. 套用 corrective migration。
3. 跑完整測試與 migration smoke test。
4. 再依 `docs/11-local-rebuild-sop.md` 執行正式全量重建。

### 若已完成上一輪全量重建

1. P2、P3 不需要重建 feature 或 signal。
2. 查出每天有 RawTick 的 distinct symbol 數：

```sql
SELECT data_date, count(DISTINCT symbol) AS symbols
FROM raw_tick
GROUP BY data_date
HAVING count(DISTINCT symbol) < 2
ORDER BY data_date;
```

3. 沒有結果：P1 不需要歷史重建。
4. 有結果：只局部重建這些日期的 `feature_daily` 與 `signal_snapshot`；重建後再次確認 validation cache 自動失效。

部署或重建都屬外部狀態變更，Coding Agent 完成程式與測試後應先回報，不得自行操作 production。

## 11. Coding Agent 交付格式

完成後必須回報：

1. 已完成項目與 commit。
2. 新 migration revision、down revision 與 upgrade／downgrade 結果。
3. 三張表 `updated_at IS NULL` 的查詢結果。
4. Singleton intraday 與兩檔同值案例的測試結果。
5. Cache config invalidation 測試結果。
6. 完整 pytest、Alembic、lint、TypeScript 與 build 結果。
7. 是否需要局部或全量重建，以及判斷用 SQL 結果。
8. Look-ahead bias 檢查。
9. 資料缺漏與尚未完成事項。
10. Backtest／validation 是否重跑；若沒有，明確寫「未重跑，不宣稱策略有效」。

## 12. 可直接交給 Coding Agent 的任務文字

```text
請依 docs/13-post-fix-followup-action-plan-2026-09-13.md 實作三項後續修正：

1. singleton intraday 的橫斷面 z 保留 NULL，不得用 0 冒充中性；兩檔以上同值才是有效 0。
2. 新增 corrective Alembic migration，把 signal_snapshot、daily_price、corporate_action.updated_at
   補值後設為 NOT NULL；不要重寫已推送的 a7d0e4f2b6c8。
3. Forward Report cache hash 納入完整 report config，至少包含 backtest 與
   validation.min_ic_names。

每一項先補能失敗的測試再修正，依文件拆成三個 commit。不得修改權重、RR、breakout policy、
percentile mapping 或其他無關程式。完整 pytest、migration upgrade/downgrade、alembic check、lint、
tsc 與 build 通過後，依 docs/08 §30 格式回報。先不要自行部署 production 或執行全量重建。
```
