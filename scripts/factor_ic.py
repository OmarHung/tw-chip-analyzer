"""單因子 IC 分解:每個原始 feature / 分項分數各自對 forward return 的預測力。

判別病因:
- 若某些成分有顯著 IC(|t|>2)但 chip_score 整體 IC≈0 → normalization/weight
  稀釋了有效訊號,調權重可救。
- 若所有成分 IC 都≈0 → 訊號內容本身在此期間無 alpha,調權重救不了,得換/加 feature。

IC = 逐日橫斷面 Spearman(factor vs forward net return),跨日平均。
ICIR = mean/std;t = ICIR*sqrt(天數)。|t|>2 視為顯著。IC 符號代表方向。

用法:APP_ENV=dev python -m scripts.factor_ic
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.backtest import BacktestEngine
from app.backtest.engine import BacktestSignal
from app.backtest.runner import load_bars
from app.db.session import get_sessionmaker

# feature_daily 原始 z 特徵(排除恆為 0 的 sbl_change_z / holder_count_change_z)
FEAT_COLS = [
    "foreign_5d_z", "trust_5d_z", "dealer_5d_z",
    "margin_balance_change_z", "short_balance_change_z",
    "large_holder_ratio_change_z", "retail_holder_ratio_change_z",
    "cvd_z", "large_trade_delta_z", "intraday_obi",
]
# signal_snapshot 分項分數 + 總分
SCORE_COLS = [
    "intraday_score", "institutional_score", "holder_score",
    "market_score", "chip_score",
]


async def _amain() -> None:
    sm = get_sessionmaker()
    engine = BacktestEngine()
    horizons = engine.horizons

    async with sm() as s:
        feat = pd.DataFrame(
            (await s.execute(text(
                f"select symbol, data_date, {', '.join(FEAT_COLS)} from feature_daily"
            ))).all(),
            columns=["symbol", "data_date", *FEAT_COLS],
        )
        score = pd.DataFrame(
            (await s.execute(text(
                f"select symbol, data_date, {', '.join(SCORE_COLS)} from signal_snapshot"
            ))).all(),
            columns=["symbol", "data_date", *SCORE_COLS],
        )
        # 只保留有落地分數的 (symbol,date)(即回看視窗足夠的交易日)
        df = score.merge(feat, on=["symbol", "data_date"], how="inner")
        symbols = sorted(df["symbol"].unique())
        prices = await load_bars(s, symbols)

    # 每列算 forward net return(重用 engine 的 look-ahead 安全進場)
    fwd = {h: [] for h in horizons}
    for sym, d in zip(df["symbol"], df["data_date"]):
        bars = prices.get(sym)
        oc = engine.evaluate_signal(BacktestSignal(sym, d, 0.0), list(bars)) if bars else None
        for h in horizons:
            hr = oc.forward.horizons.get(h) if oc else None
            fwd[h].append(hr.net_return if hr else np.nan)
    for h in horizons:
        df[f"ret{h}"] = fwd[h]

    for c in [*FEAT_COLS, *SCORE_COLS]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    print(f"評估列數={len(df)}  交易日={df['data_date'].nunique()}\n")
    print("=== 單因子逐日橫斷面 IC(mean IC;* = |t|>2 顯著)===")
    print(f"{'factor':<28}" + "".join(f"{f'{h}D':>10}" for h in horizons))

    def ic_stats(factor: str, h: int):
        ics = []
        for _, g in df.groupby("data_date"):
            sub = g[[factor, f"ret{h}"]].dropna()
            if len(sub) >= 20 and sub[factor].nunique() > 1:
                ic = np.corrcoef(sub[factor].rank(), sub[f"ret{h}"].rank())[0, 1]
                if not np.isnan(ic):
                    ics.append(ic)
        a = np.array(ics)
        if a.size < 2:
            return None, None
        std = a.std(ddof=1)
        icir = a.mean() / std if std > 0 else 0.0
        return a.mean(), icir * np.sqrt(a.size)

    for c in [*FEAT_COLS, *SCORE_COLS]:
        cells = []
        for h in horizons:
            ic, t = ic_stats(c, h)
            if ic is None:
                cells.append(f"{'-':>10}")
            else:
                mark = "*" if abs(t) > 2 else " "
                cells.append(f"{ic:>+9.4f}{mark}")
        tag = "  [分項]" if c in SCORE_COLS else ""
        print(f"{c:<28}" + "".join(cells) + tag)


if __name__ == "__main__":
    asyncio.run(_amain())
