# 02 · 資料來源與資料庫

> 對應原始交接文件 §6、§7、§20

## 6. 資料來源與責任

### Shioaji
用途：Tick、BidAsk 五檔、盤中 order flow。
> 注意：只能知道市場行為，**不能辨認成交者身分**。

### TWSE / TPEx
用途：OHLCV、三大法人、融資融券、借券。

### TAIFEX 期交所
用途：台指期（TX）每日行情 → `futures_daily`。端點 `cht/3/futDataDown`（POST，Big5 CSV，
支援日期區間、歷史可回補，回補用 `scripts/backfill_futures.py`）。
**只供概覽頁的大盤脈絡展示，不是評分成分**；缺當日只是少一格，不影響 chip_score。

### TDCC
用途：股權分散、大戶/散戶持股比例變化。

### MOPS（Phase 2）
董監持股、大股東、質押、持股轉讓。
> 端點查證、`available_at` 規則、表結構與 shadow feature 見 `docs/14-phase2-mops-data-sources.md`（shadow-only，不進正式分數）。

### 券商分點（Phase 3）
Phase 3 才加入。**不可假設存在免費完整歷史 API。**

## 7. 主要資料表

### raw_tick

```sql
CREATE TABLE raw_tick (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(16) NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    price NUMERIC(14,4) NOT NULL,
    volume INTEGER NOT NULL,
    bid1_price NUMERIC(14,4),
    ask1_price NUMERIC(14,4),
    aggressor_side SMALLINT,
    trade_value NUMERIC(18,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_raw_tick_symbol_ts ON raw_tick(symbol, ts DESC);
```

`aggressor_side`：1=BUY、-1=SELL、0=UNKNOWN。

### raw_orderbook
保存 Bid1~Bid5 / Ask1~Ask5 的價量快照。Production 不建議永久保留全部 snapshot。

### feature_1m

```sql
CREATE TABLE feature_1m (
    symbol VARCHAR(16) NOT NULL,
    bucket TIMESTAMPTZ NOT NULL,
    open NUMERIC(14,4),
    high NUMERIC(14,4),
    low NUMERIC(14,4),
    close NUMERIC(14,4),
    volume BIGINT,
    buy_volume BIGINT,
    sell_volume BIGINT,
    delta BIGINT,
    cvd BIGINT,
    trade_count INTEGER,
    avg_trade_size NUMERIC(16,4),
    large_buy_volume BIGINT,
    large_sell_volume BIGINT,
    large_trade_delta BIGINT,
    obi NUMERIC(10,6),
    sell_absorption_score NUMERIC(10,6),
    buy_absorption_score NUMERIC(10,6),
    trade_speed NUMERIC(16,6),
    vwap NUMERIC(14,4),
    PRIMARY KEY(symbol, bucket)
);
```

### institutional_daily
保存外資、投信、自營商自行買賣、自營商避險。**自營商兩類必須拆開。**

### margin_daily
保存融資買進/賣出/餘額與融券賣出/回補/餘額。

### sbl_daily
保存借券賣出、還券、餘額。

### tdcc_weekly
保存原始級距。

### tdcc_summary_weekly
聚合：retail_ratio、medium_ratio、large_ratio、super_large_ratio、holder_count。

> 第一版級距：Retail ≤ 50 張；Large ≥ 400 張；Super Large ≥ 1000 張。**所有級距必須 config 化。**

### futures_daily
台指期每日行情（一個交易日多個 `contract_month`）。兩個取用約定：

- **只存日盤**（期交所「一般」時段）。「盤後」夜盤跨到隔日凌晨才收，EOD 當下抓到的是
  半截資料；價差契約（到期月份形如 `202609/202610`）報的是價差不是點位，一律排除。
- **主力月份＝當日成交量最大者**，不是到期月份最小者：結算日當天近月量已萎縮、結算價 0，
  拿它當「台指期收盤」會失真（`repositories.market.load_futures_front_month`）。
- `change_pct` 取自期交所欄位（存成小數）而非自算——換月時前一交易日的同月份收盤才是
  正確基準，自算會在換月日給出假跳空。

### signal_snapshot
每個訊號都保存完整 feature payload，供回測與未來 ML 使用。

## 20. Tick 寫入策略

**禁止每 Tick 一次 transaction commit。**

使用 batch：
- 100~1000 rows
- 或 100~500ms flush
- 可用 COPY / bulk insert

### Retention

```text
raw_tick       30~90 days
raw_orderbook   7~30 days
feature_1m      永久
daily           永久
weekly          永久
```
