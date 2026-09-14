"""熱力圖 API（見 docs/05 §16）。

三個端點對應三張圖，共同點是「把既有資料換一種密度更高的呈現」，不新增任何
特徵計算，也不參與評分：

- `/api/heatmap/market`   全市場 treemap：方塊面積＝成交值，顏色＝漲跌／分數／法人
- `/api/heatmap/industry` 產業 × 日期矩陣：每格是該產業當日成分股的**中位數**
- `/api/heatmap/stock/{symbol}` 個股籌碼分項 × 日期矩陣（讀已落地的 signal_snapshot）

兩個刻意的設計：

1. **中位數而非平均**：產業內常有一兩檔漲停／跌停的極端值，平均會被單一個股帶著走，
   看起來像「整個產業在動」。中位數反映的是「這個產業的多數成分股在做什麼」。
2. **成分股數門檻**（`heatmap.industry.min_symbols`）：樣本太少的產業中位數是雜訊，
   寧可留白也不要畫出一個會被誤讀的顏色。與 feature_builder 的 industry_trend 同門檻。

第四張熱力圖（分數 bucket × horizon 的前瞻驗證）不在這裡——它的數字已由
`/api/validation/forward` 供應，前端直接換一種畫法即可，不需要新端點。
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_thresholds
from app.db.session import get_session
from app.services.market_scan import scan_all

router = APIRouter(prefix="/api/heatmap", tags=["heatmap"])


def _conf(*path: str, default=None):
    return get_thresholds().get("heatmap", *path, default=default)


# --------------------------------------------------------------- 全市場 treemap

class MarketCell(BaseModel):
    symbol: str
    name: str
    industry: str | None = None  # None＝MOPS 無產業別（興櫃轉上市初期、F 股等）
    turnover: float              # 方塊面積
    change_pct: float | None = None
    chip_score: float
    institutional: float | None = None
    action: str


class MarketHeatmapResponse(BaseModel):
    as_of: str | None
    total: int    # 當日全市場可評分檔數
    count: int    # 本次回傳（依成交值取前 N）
    covered_turnover: float  # 回傳這些檔佔全市場成交值比例，讓前端能說明「涵蓋 xx%」
    rows: list[MarketCell]


@router.get("/market", response_model=MarketHeatmapResponse)
async def market_heatmap(
    session: AsyncSession = Depends(get_session),
    limit: int | None = Query(None, ge=1, le=3000),
    min_turnover: float | None = Query(None, ge=0),
) -> MarketHeatmapResponse:
    """全市場 treemap 用的扁平列（未分組；產業分組交給前端版面）。

    依成交值由大到小取前 N：treemap 的面積就是成交值，排在後面的檔即使畫出來也
    小於 1px，徒增 DOM 節點。回傳 `covered_turnover` 讓 UI 能誠實標示涵蓋率。
    """
    limit = limit if limit is not None else int(_conf("market", "max_symbols", default=300))
    floor = (
        min_turnover if min_turnover is not None
        else float(_conf("market", "min_turnover", default=0) or 0)
    )
    as_of, rows = await scan_all(session)
    if as_of is None:
        return MarketHeatmapResponse(
            as_of=None, total=0, count=0, covered_turnover=0.0, rows=[]
        )

    total_turnover = sum(r.turnover for r in rows)
    picked = sorted(
        (r for r in rows if r.turnover >= floor),
        key=lambda r: r.turnover,
        reverse=True,
    )[:limit]
    covered = sum(r.turnover for r in picked)
    return MarketHeatmapResponse(
        as_of=str(as_of),
        total=len(rows),
        count=len(picked),
        covered_turnover=round(covered / total_turnover, 4) if total_turnover else 0.0,
        rows=[
            MarketCell(
                symbol=r.symbol, name=r.name, industry=r.industry,
                turnover=r.turnover, change_pct=r.change_pct,
                chip_score=r.chip_score, institutional=r.institutional,
                action=r.action,
            )
            for r in picked
        ],
    )


# ------------------------------------------------------------ 產業 × 日期矩陣

class IndustryCell(BaseModel):
    date: str
    n: int                        # 當日該產業有資料的成分股數
    ret: float | None = None      # 成分股當日漲跌幅中位數
    score: float | None = None    # chip_score 中位數
    inst: float | None = None     # 法人分項中位數


class IndustryRow(BaseModel):
    industry: str
    n_max: int                    # 視窗內最大成分股數（列的代表性）
    cells: list[IndustryCell]     # 只含有資料的日期；缺漏由前端依 dates 對齊留白


class IndustryHeatmapResponse(BaseModel):
    as_of: str | None
    dates: list[str]              # 由舊到新的交易日（非日曆日）
    min_symbols: int
    industries: list[IndustryRow]


# 一次把三個 metric 都聚合出來：切換顏色維度是前端的事，不該為了換個顏色重打 API。
# percentile_cont 會忽略 NULL（某檔當日沒有 chip_score 不影響其他檔的中位數）。
_INDUSTRY_SQL = """
select s.industry,
       f.data_date,
       count(*) as n,
       percentile_cont(0.5) within group (order by f.change_pct) as ret,
       percentile_cont(0.5) within group (order by ss.chip_score) as score,
       percentile_cont(0.5) within group (order by ss.institutional_score) as inst
from feature_daily f
join stock s on s.symbol = f.symbol
left join signal_snapshot ss
       on ss.symbol = f.symbol and ss.data_date = f.data_date
where s.industry is not null and f.data_date = any(:dates)
group by s.industry, f.data_date
having count(*) >= :min_symbols
"""


@router.get("/industry", response_model=IndustryHeatmapResponse)
async def industry_heatmap(
    session: AsyncSession = Depends(get_session),
    days: int | None = Query(None, ge=2),
    min_symbols: int | None = Query(None, ge=1),
) -> IndustryHeatmapResponse:
    """產業輪動矩陣：每格 = 該產業當日成分股的中位數（報酬／分數／法人）。

    橫軸取「最近 N 個有資料的交易日」而非日曆日回推——否則連假會在圖上留下
    整排空白，看起來像資料缺漏。
    """
    max_days = int(_conf("industry", "max_days", default=120))
    n_days = min(days if days is not None else int(_conf("industry", "days", default=20)), max_days)
    floor = (
        min_symbols if min_symbols is not None
        else int(_conf("industry", "min_symbols", default=5))
    )

    dates: list[dt.date] = [
        r[0] for r in (await session.execute(text(
            "select distinct data_date from feature_daily"
            " order by data_date desc limit :n"
        ), {"n": n_days})).all()
    ]
    if not dates:
        return IndustryHeatmapResponse(
            as_of=None, dates=[], min_symbols=floor, industries=[]
        )
    dates.reverse()  # 由舊到新，與前端由左至右的時間軸一致

    rows = (await session.execute(
        text(_INDUSTRY_SQL), {"dates": dates, "min_symbols": floor}
    )).all()

    by_industry: dict[str, list[IndustryCell]] = {}
    for industry, data_date, n, ret, score, inst in rows:
        by_industry.setdefault(industry, []).append(IndustryCell(
            date=str(data_date), n=int(n),
            ret=float(ret) if ret is not None else None,
            score=float(score) if score is not None else None,
            inst=float(inst) if inst is not None else None,
        ))

    latest = str(dates[-1])
    out: list[IndustryRow] = []
    for industry, cells in by_industry.items():
        cells.sort(key=lambda c: c.date)
        out.append(IndustryRow(
            industry=industry,
            n_max=max(c.n for c in cells),
            cells=cells,
        ))
    # 依最新一日的報酬排序（無當日資料者沉底）：最上面幾列就是今天在動的產業
    def _latest_ret(row: IndustryRow) -> tuple[int, float]:
        cell = next((c for c in row.cells if c.date == latest), None)
        if cell is None or cell.ret is None:
            return (1, 0.0)
        return (0, -cell.ret)

    out.sort(key=_latest_ret)
    return IndustryHeatmapResponse(
        as_of=latest, dates=[str(d) for d in dates], min_symbols=floor, industries=out
    )


# --------------------------------------------------- 個股籌碼分項 × 日期矩陣

class StockScoreCell(BaseModel):
    date: str
    chip_score: float
    intraday: float | None = None
    institutional: float | None = None
    holder: float | None = None
    market: float | None = None
    action: str | None = None


class StockHeatmapResponse(BaseModel):
    symbol: str
    days: int
    cells: list[StockScoreCell]  # 由舊到新


@router.get("/stock/{symbol}", response_model=StockHeatmapResponse)
async def stock_heatmap(
    symbol: str,
    session: AsyncSession = Depends(get_session),
    days: int | None = Query(None, ge=2, le=250),
) -> StockHeatmapResponse:
    """單一標的的分項演變。讀已落地的 signal_snapshot（不重算）。

    NULL 分項（如當日沒有逐筆、沒有 TDCC）保持 NULL 往上傳——前端必須把
    「中性 50 分」和「沒有這個成分」畫成兩種樣子，否則會讀成「這天籌碼普通」。
    """
    n_days = days if days is not None else int(_conf("stock", "days", default=60))
    rows = (await session.execute(text(
        "select data_date, chip_score, intraday_score, institutional_score,"
        "       holder_score, market_score, action"
        " from signal_snapshot where symbol = :symbol"
        " order by data_date desc limit :n"
    ), {"symbol": symbol, "n": n_days})).all()

    cells = [
        StockScoreCell(
            date=str(r[0]), chip_score=float(r[1]),
            intraday=float(r[2]) if r[2] is not None else None,
            institutional=float(r[3]) if r[3] is not None else None,
            holder=float(r[4]) if r[4] is not None else None,
            market=float(r[5]) if r[5] is not None else None,
            action=r[6],
        )
        for r in reversed(rows)
    ]
    return StockHeatmapResponse(symbol=symbol, days=n_days, cells=cells)
