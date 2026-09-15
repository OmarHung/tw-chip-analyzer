"""每日「新進 BUY / AVOID」差異與 Telegram 訊息組裝（純讀 signal_snapshot）。

「新進」定義：當日 `signal_snapshot.action` 屬於推播集合，且該檔在回看窗內
**最近一筆**快照的 action 不同（或根本沒有前一筆）。逐檔比較而非只比「前一天」——
某檔昨日無籌碼成分（未落地）、前日是 BUY，今日仍 BUY 就不算新進。

只讀已落地的 signal_snapshot、不重算分數：推播內容與 UI / backtest 同口徑，
也不會因為推播時 config 已被改動而與落地結果不一致。
"""
from __future__ import annotations

import datetime as dt
import html
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.features import SignalSnapshot
from app.db.models.market import Stock

_WEEKDAY_ZH = "一二三四五六日"
# 台股語意：紅＝多／進場，綠＝空／迴避（與 frontend/lib/format.ts dirColor 一致）
_ACTION_STYLE: dict[str, tuple[str, str]] = {
    "BUY": ("🔴", "新進 BUY"),
    "AVOID": ("🟢", "新進 AVOID"),
    "WATCH": ("🟡", "新進 WATCH"),
    "HOLD": ("⚪", "新進 HOLD"),
    "REDUCE": ("🟠", "新進 REDUCE"),
    "EXIT": ("⚫", "新進 EXIT"),
}
# 排序：BUY 高分在前，AVOID 低分在前（最極端的先看）
_ASCENDING_SCORE = {"AVOID", "EXIT", "REDUCE"}


@dataclass(frozen=True)
class AlertItem:
    symbol: str
    name: str
    action: str
    chip_score: float
    prev_action: str | None      # None＝回看窗內沒有前一筆快照
    prev_date: dt.date | None
    close: float | None = None
    change_pct: float | None = None
    turnover: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    risk_reward: float | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class AlertReport:
    target: dt.date
    has_snapshot: bool                    # 當日是否有任何 signal_snapshot（非交易日／未落地＝False）
    has_baseline: bool                    # 回看窗內是否有任何前一筆快照（首日執行＝False）
    items: tuple[AlertItem, ...] = ()     # 新進（已依 action 分組排序，尚未截斷）
    totals: dict[str, int] = field(default_factory=dict)  # 當日各 action 全市場總數（非只新進）

    def by_action(self, action: str) -> list[AlertItem]:
        return [i for i in self.items if i.action == action]


def _f(v: Any) -> float | None:
    if v is None:
        return None
    return float(v) if isinstance(v, (Decimal, int, float)) else None


async def _latest_prev_actions(
    session: AsyncSession, target: dt.date, lookback_days: int
) -> dict[str, tuple[dt.date, str]]:
    """回看窗內每檔最近一筆快照的 (data_date, action)。"""
    since = target - dt.timedelta(days=lookback_days)
    stmt = (
        select(SignalSnapshot.symbol, SignalSnapshot.data_date, SignalSnapshot.action)
        .where(SignalSnapshot.data_date < target, SignalSnapshot.data_date >= since)
        .order_by(SignalSnapshot.symbol, SignalSnapshot.data_date.desc())
    )
    prev: dict[str, tuple[dt.date, str]] = {}
    for symbol, d, action in (await session.execute(stmt)).all():
        if symbol not in prev:  # 已依日期降冪，第一筆即最新
            prev[symbol] = (d, action)
    return prev


def _item_from_row(row: SignalSnapshot, name: str, prev: tuple[dt.date, str] | None) -> AlertItem:
    payload = row.payload or {}
    return AlertItem(
        symbol=row.symbol,
        name=name,
        action=row.action,
        chip_score=float(row.chip_score),
        prev_action=prev[1] if prev else None,
        prev_date=prev[0] if prev else None,
        close=_f(payload.get("close")),
        change_pct=_f(payload.get("change_pct")),
        turnover=_f(payload.get("turnover")),
        entry_low=_f(row.entry_low),
        entry_high=_f(row.entry_high),
        stop_loss=_f(row.stop_loss),
        tp1=_f(row.tp1),
        tp2=_f(row.tp2),
        risk_reward=_f(row.risk_reward),
        reasons=tuple(str(r) for r in (row.reasons or [])),
    )


def _sort_key(item: AlertItem) -> tuple:
    score = item.chip_score if item.action in _ASCENDING_SCORE else -item.chip_score
    return (score, item.symbol)


async def load_action_changes(
    session: AsyncSession,
    target: dt.date,
    actions: tuple[str, ...] | list[str],
    *,
    lookback_days: int,
    min_turnover: float = 0.0,
) -> AlertReport:
    """算出 target 當日「新進」actions 的清單；不送出、不改資料。"""
    wanted = tuple(actions)
    stmt = (
        select(SignalSnapshot, Stock.name)
        .join(Stock, Stock.symbol == SignalSnapshot.symbol)
        .where(SignalSnapshot.data_date == target)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return AlertReport(target=target, has_snapshot=False, has_baseline=False)

    prev = await _latest_prev_actions(session, target, lookback_days)
    totals: dict[str, int] = {a: 0 for a in wanted}
    items: list[AlertItem] = []
    for snap, name in rows:
        if snap.action not in wanted:
            continue
        totals[snap.action] += 1
        p = prev.get(snap.symbol)
        if p is not None and p[1] == snap.action:
            continue  # 延續，不是新進
        item = _item_from_row(snap, name, p)
        if min_turnover > 0 and item.turnover is not None and item.turnover < min_turnover:
            continue
        items.append(item)
    # 依 actions 宣告順序分組，組內依分數極端度排序
    order = {a: i for i, a in enumerate(wanted)}
    items.sort(key=lambda i: (order[i.action], _sort_key(i)))
    return AlertReport(
        target=target, has_snapshot=True, has_baseline=bool(prev),
        items=tuple(items), totals=totals,
    )


# ------------------------------------------------------------------ 訊息組裝（HTML）

def _num(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if digits else f"{v:.0f}"


def _pct(v: float | None) -> str:
    if v is None:
        return ""
    sign = "+" if v > 0 else ""
    return f"（{sign}{v * 100:.1f}%）"


def _item_lines(item: AlertItem, max_reasons: int) -> list[str]:
    head = f"<b>{html.escape(item.symbol)} {html.escape(item.name)}</b>"
    if item.close is not None:
        head += f"  {_num(item.close)}{_pct(item.change_pct)}"
    meta = [f"分數 {item.chip_score:.0f}"]
    if item.risk_reward is not None:
        meta.append(f"RR {_num(item.risk_reward, 1)}")
    meta.append(f"前 {item.prev_action}" if item.prev_action else "前 無快照")
    lines = [head, "  " + " ・ ".join(meta)]
    if item.entry_low is not None and item.entry_high is not None:
        plan = [f"進場 {_num(item.entry_low)}–{_num(item.entry_high)}"]
        if item.stop_loss is not None:
            plan.append(f"停損 {_num(item.stop_loss)}")
        if item.tp1 is not None:
            tp = f"TP1 {_num(item.tp1)}"
            if item.tp2 is not None:
                tp += f" ／ TP2 {_num(item.tp2)}"
            plan.append(tp)
        lines.append("  " + " ・ ".join(plan))
    for r in item.reasons[:max_reasons]:
        lines.append(f"  ↳ {html.escape(r)}")
    return lines


def build_message_blocks(
    report: AlertReport,
    actions: tuple[str, ...] | list[str],
    *,
    max_items: int,
    max_reasons: int,
    note: str | None = None,
) -> list[str]:
    """回傳「不可再拆」的段落清單（表頭 + 每檔一段），供 split_messages 分段。"""
    t = report.target
    title = f"<b>籌碼日報 {t.isoformat()}（{_WEEKDAY_ZH[t.weekday()]}）</b>"
    summary = " ／ ".join(
        f"新進 {a} {len(report.by_action(a))} 檔" for a in actions
    )
    totals = "、".join(f"{a} {report.totals.get(a, 0)}" for a in actions)
    blocks = [f"{title}\n{summary}\n全市場：{totals}"]
    if note:
        blocks.append(f"⚠️ {html.escape(note)}")
    if not report.has_baseline:
        blocks.append("ℹ️ 回看窗內無前一筆快照（首次執行），今日全部視為新進。")
    for action in actions:
        group = report.by_action(action)
        icon, label = _ACTION_STYLE.get(action, ("•", f"新進 {action}"))
        blocks.append(f"\n{icon} <b>{label}</b>（{len(group)}）")
        if not group:
            blocks.append("  無")
            continue
        for item in group[:max_items]:
            blocks.append("\n".join(_item_lines(item, max_reasons)))
        if len(group) > max_items:
            blocks.append(f"  …另 {len(group) - max_items} 檔未列")
    return blocks


def split_messages(blocks: list[str], max_chars: int) -> list[str]:
    """把段落依上限合併成多則訊息；單一段落超長時硬切（極端情況，不應發生）。"""
    messages: list[str] = []
    current = ""
    for block in blocks:
        while len(block) > max_chars:
            if current:
                messages.append(current)
                current = ""
            messages.append(block[:max_chars])
            block = block[max_chars:]
        candidate = f"{current}\n{block}" if current else block
        if len(candidate) > max_chars:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def format_telegram_messages(
    report: AlertReport,
    actions: tuple[str, ...] | list[str],
    *,
    max_items: int,
    max_reasons: int,
    max_chars: int,
    note: str | None = None,
) -> list[str]:
    blocks = build_message_blocks(
        report, actions, max_items=max_items, max_reasons=max_reasons, note=note
    )
    return split_messages(blocks, max_chars)
