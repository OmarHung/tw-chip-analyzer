# 04 · 決策引擎：Entry / Risk / Exit

> 對應原始交接文件 §12–14

## 12. Entry Filter

BUY 條件：

```text
ChipScore >= 75
AND RiskReward >= 2
AND 價格不過度偏離 VWAP / MA20
AND Market Regime != Strong Bear
AND 流動性達最低要求
AND 非鎖死漲停等不可合理成交狀況
```

若 Score ≥ 75 但價格過度延伸，輸出 **WATCH**。

實作要點（2026-09-13，docs/09、docs/12）：

- **RR** = (目標 − 進場區上緣) ÷ (進場區上緣 − 停損)。目標取近 `risk.resistance_lookback_bars` 根後復權
  最高價；已突破則用 `進場區上緣 + breakout_target_atr × ATR`（未經 OOS 驗證，原因會加註）；
  無壓力位資料 RR=0。API `risk.rr_basis` = `resistance` / `breakout_atr` / `unavailable`。TP1/TP2 不再拿來反算 RR。
- **Strong Bear** 用原始 `market_trend_score`（不是加權後的市場子分數）。**無當日大盤資料 → WATCH**
  （「無當日大盤資料，無法排除 Strong Bear」）。
- **鎖死漲停**：`feature_daily.is_limit_locked`（漲幅 ≥ `limit_lock_min_change_pct` 且 high=low=close）；
  無法判斷（NULL）視同不可成交。

## 13. Risk Engine

ATR14 使用 **Daily timeframe**。

初版停損：

```text
min(
    entry - 1.5 * ATR,
    recent_swing_low - 0.2 * ATR
)
```

最大停損百分比：6%，config 化。

Take Profit：

```text
TP1 = 2R
TP2 = 3R
R = Entry - StopLoss
```

**禁止固定 +5% / +10%。**

## 14. Exit Logic

持倉上下文由 API 參數帶入（`/analysis?entry_price=&stop_loss=`），系統不保存持倉。

Hard Stop：`price <= stop_loss => EXIT`。

Chip deterioration：

```text
ChipScore < 50 => REDUCE
ChipScore < 40 => EXIT
```

Distribution Warning：

```text
Price new high
AND CVD declining
AND LargeTradeDelta negative
AND BuyAbsorption increasing
```

=> REDUCE。

> **現況：預設關閉**（`exit.distribution_enabled: false`，docs/12 Phase 3A）。需要單股時序 CVD slope、
> 單股 raw 大單淨額、可證明「增加中」的買方吸收三者皆有定義並經多日逐筆驗證後才可開啟；
> 不得以橫斷面 `cvd_z` / `large_trade_delta_z` 正負或 `absorption_z > 0` 替代。

TP1 後可啟用 ATR trailing stop。

持倉 TP：停損低於成本時 TP = 成本 + R 倍數；**停損已移到成本以上（移動停損）時無法還原初始 R，
TP 留空並說明**，不退化成成本價。現價已越過的 TP 留空並加註「現價已達 TPx」。
