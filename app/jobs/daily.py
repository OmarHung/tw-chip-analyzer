"""每日盤後 job：抓取 → 匯入 → 建特徵（見 docs/07 §19）。

用法：
  APP_ENV=dev python -m app.jobs.daily 2025-09-03
  APP_ENV=dev python -m app.jobs.daily 2025-09-03 --skip-import
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt

from app.connectors import tdcc as tdcc_conn
from app.connectors import tpex as tpex_conn
from app.connectors import twse as twse_conn
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.importers.service import (
    import_capital_reduction,
    import_capital_reduction_forecast,
    import_company_profiles,
    import_ex_dividend,
    import_ex_rights_forecast,
    import_index,
    import_institutional,
    import_margin,
    import_ohlcv,
    import_par_change,
    import_sbl,
    import_tdcc,
    import_tpex_capital_reduction,
    import_tpex_company_profiles,
    import_tpex_ex_dividend,
    import_tpex_institutional,
    import_tpex_margin,
    import_tpex_ohlcv,
    import_tpex_par_change,
)
from app.services.feature_builder import build_features
from app.services.market_score import build_market_daily
from app.services.signal_persist import persist_signals

logger = get_logger("jobs.daily")


def _month_starts(target: dt.date, months: int) -> list[dt.date]:
    """target 當月起往前 months 個月的每月 1 號（供 FMTQIK 逐月抓取）。"""
    out, y, m = [], target.year, target.month
    for _ in range(months):
        out.append(dt.date(y, m, 1))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


class SourceReport:
    """各資料源的抓取/匯入結果(docs/09 BUG-07/08):非核心來源失敗不中止 EOD,
    但必須可觀測——回傳給排程/回補狀態,而不是只寫一行 warning。"""

    def __init__(self) -> None:
        self.sources: dict[str, dict] = {}

    def ok(self, label: str, rows: int) -> None:
        self.sources[label] = {"ok": True, "rows": rows, "error": None}

    def fail(self, label: str, error: Exception) -> None:
        logger.warning("資料源失敗（%s），當日該成分降級：%s", label, error)
        self.sources[label] = {"ok": False, "rows": 0, "error": str(error)}

    def rows(self, label: str) -> int:
        return self.sources.get(label, {}).get("rows", 0)

    def summary(self) -> dict:
        return {
            "sources": self.sources,
            "degraded": [k for k, v in self.sources.items() if not v["ok"]],
        }


async def run(
    target: dt.date,
    do_import: bool = True,
    do_features: bool = True,
    do_signals: bool = True,
    do_tdcc: bool = False,
    do_index: bool = False,
) -> dict:
    """回傳 {sources: {來源: {ok, rows, error}}, degraded: [失敗來源]}。

    核心 TWSE 行情/法人/融資失敗仍直接拋錯(無核心資料不該產生訊號);
    其餘來源各自 fail-soft,缺則該成分在 composite 中排除或維持前值。
    """
    sm = get_sessionmaker()
    report = SourceReport()

    if do_index:
        # 抓近 4 個月 TAIEX（確保 MA60 有足夠歷史），再算大盤脈絡。
        logger.info("抓取 TAIEX 指數（近 4 個月）...")
        try:
            total = 0
            for mstart in _month_starts(target, 4):
                raw = await twse_conn.fetch_index_month(mstart)
                async with sm() as s:
                    total += await import_index(s, raw)
            report.ok("TAIEX", total)
            logger.info("TAIEX 匯入完成：%d 日", total)
        except Exception as e:  # noqa: BLE001 — 缺則大盤脈絡沿用既有資料或略過
            report.fail("TAIEX", e)

    if do_tdcc:
        # TDCC openapi 只有當週快照（無日期參數）。
        logger.info("抓取 TDCC 股權分散（當週）...")
        try:
            records = await tdcc_conn.fetch_shareholder_distribution()
            async with sm() as s:
                nw, ns = await import_tdcc(s, records)
            report.ok("TDCC", ns)
            logger.info("TDCC 匯入完成：weekly=%d summary=%d", nw, ns)
        except Exception as e:  # noqa: BLE001 — 缺則 holder 用前一週快照或排除
            report.fail("TDCC", e)

    if do_import:
        logger.info("抓取 TWSE 盤後資料 %s ...", target)
        ohlcv = await twse_conn.fetch_ohlcv(target)
        inst = await twse_conn.fetch_institutional(target)
        margin = await twse_conn.fetch_margin(target)
        async with sm() as s:
            n_price = await import_ohlcv(s, ohlcv, target)
            n_inst = await import_institutional(s, inst, target)
            n_margin = await import_margin(s, margin, target)
        report.ok("TWSE 行情", n_price)
        report.ok("TWSE 法人", n_inst)
        report.ok("TWSE 融資券", n_margin)
        # 借券 SBL（TWT93U）：新資料源，失敗不影響核心匯入。
        n_sbl = 0
        try:
            sbl = await twse_conn.fetch_sbl(target)
            async with sm() as s:
                n_sbl = await import_sbl(s, sbl, target)
            report.ok("SBL 借券", n_sbl)
        except Exception as e:  # noqa: BLE001 — SBL 非必要，缺則該子項 NULL
            report.fail("SBL 借券", e)
        # 公司行動還原因子：TWSE 除權息(TWT49U)+面額變更(TWTB8U)+減資(TWTAUU)+兩張預告表
        # (TWT48U 補配股率、TWTAVU 補減資換股率→量因子,只回未來)；
        # TPEx 除權息(exDailyQ,同表即有配股率)
        # +面額變更(pvChgRslt)+減資(revivt)。
        # 各自 fail-soft，缺則該日該類無還原。
        n_ca = 0
        for label, fetch, imp in (
            ("除權息 TWT49U", twse_conn.fetch_ex_dividend, import_ex_dividend),
            ("面額變更 TWTB8U", twse_conn.fetch_par_change, import_par_change),
            ("減資預告 TWTAVU", twse_conn.fetch_capital_reduction_forecast,
             import_capital_reduction_forecast),
            ("減資 TWTAUU", twse_conn.fetch_capital_reduction, import_capital_reduction),
            ("除權息預告 TWT48U", twse_conn.fetch_ex_rights_forecast,
             import_ex_rights_forecast),
            ("TPEx 除權息 exDailyQ", tpex_conn.fetch_ex_dividend,
             import_tpex_ex_dividend),
            ("TPEx 面額變更 pvChgRslt", tpex_conn.fetch_par_change,
             import_tpex_par_change),
            ("TPEx 減資 revivt", tpex_conn.fetch_capital_reduction,
             import_tpex_capital_reduction),
        ):
            try:
                raw_ca = await fetch(target)
                async with sm() as s:
                    n = await imp(s, raw_ca)
                n_ca += n
                report.ok(label, n)
            except Exception as e:  # noqa: BLE001 — 公司行動非必要，缺則該類無還原
                report.fail(label, e)
        # 公司基本資料（產業別 → industry_trend 分組、已發行股數）：靜態資料，
        # 每日刷新一次即可；失敗只代表產業別維持前值。
        n_prof = 0
        for label, fetch, imp in (
            ("上市基本資料 t187ap03_L", twse_conn.fetch_company_profiles,
             import_company_profiles),
            ("上櫃基本資料 mopsfin_t187ap03_O", tpex_conn.fetch_company_profiles,
             import_tpex_company_profiles),
        ):
            try:
                raw_p = await fetch()
                async with sm() as s:
                    n = await imp(s, raw_p)
                n_prof += n
                report.ok(label, n)
            except Exception as e:  # noqa: BLE001 — 缺則產業別維持前值
                report.fail(label, e)
        # TPEx 上櫃（行情/法人/融資券）：三個端點各自 fail-soft、各自落庫——
        # 融資端點失敗不可連帶丟掉已抓到的行情，否則整個上櫃從當日橫斷面消失。
        for label, fetch, imp in (
            ("TPEx 行情", tpex_conn.fetch_ohlcv, import_tpex_ohlcv),
            ("TPEx 法人", tpex_conn.fetch_institutional, import_tpex_institutional),
            ("TPEx 融資券", tpex_conn.fetch_margin, import_tpex_margin),
        ):
            try:
                raw_tpx = await fetch(target)
                async with sm() as s:
                    report.ok(label, await imp(s, raw_tpx, target))
            except Exception as e:  # noqa: BLE001 — 缺則該類僅上市參與
                report.fail(label, e)
        n_tpx = report.rows("TPEx 行情")
        n_tpx_inst = report.rows("TPEx 法人")
        n_tpx_margin = report.rows("TPEx 融資券")
        logger.info(
            "匯入完成：price=%d institutional=%d margin=%d sbl=%d ca=%d "
            "profile=%d tpex=%d/%d/%d",
            n_price, n_inst, n_margin, n_sbl, n_ca, n_prof,
            n_tpx, n_tpx_inst, n_tpx_margin,
        )
        if n_price + n_tpx == 0:
            logger.warning("當日無 OHLCV（可能非交易日），略過建特徵。")
            return report.summary()

    if do_features:
        async with sm() as s:
            n = await build_features(s, target)
        logger.info("特徵建立完成：feature_daily=%d", n)
        async with sm() as s:
            ok = await build_market_daily(s, target)
        logger.info("大盤脈絡：%s", "已建立" if ok else "略過（無 TAIEX 或非交易日）")

    if do_signals:
        # composite 分數落地(供 backtest / ML / 追蹤);前置為 feature_daily 已建。
        async with sm() as s:
            n_sig = await persist_signals(s, target)
        logger.info("訊號落地：signal_snapshot=%d", n_sig)

    result = report.summary()
    if result["degraded"]:
        logger.warning("EOD %s 降級完成，失敗來源：%s", target, "、".join(result["degraded"]))
    return result


def main() -> None:
    p = argparse.ArgumentParser(description="每日盤後 import + feature job")
    p.add_argument("date", help="交易日 YYYY-MM-DD")
    p.add_argument("--skip-import", action="store_true")
    p.add_argument("--skip-features", action="store_true")
    p.add_argument("--skip-signals", action="store_true", help="不落地 signal_snapshot")
    p.add_argument("--tdcc", action="store_true", help="同時抓取當週 TDCC 股權分散")
    p.add_argument("--index", action="store_true", help="抓取 TAIEX 並建大盤脈絡")
    args = p.parse_args()
    target = dt.date.fromisoformat(args.date)
    asyncio.run(
        run(
            target,
            do_import=not args.skip_import,
            do_features=not args.skip_features,
            do_signals=not args.skip_signals,
            do_tdcc=args.tdcc,
            do_index=args.index,
        )
    )


if __name__ == "__main__":
    main()
