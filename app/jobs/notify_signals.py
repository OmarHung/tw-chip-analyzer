"""每日推播：把 target 當日「新進 BUY / AVOID」推到 Telegram。

由排程器在 EOD 落地 signal_snapshot 後呼叫（scheduler._run_eod_once），也可手動：
  APP_ENV=dev python -m app.jobs.notify_signals 2026-09-15            # 送出
  APP_ENV=dev python -m app.jobs.notify_signals 2026-09-15 --dry-run  # 只印訊息不送

設定：/settings 頁「Telegram 推播」（DB）> .env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID >
config notify.telegram（鐵則 4）。
推播失敗只記 log、回傳 status，絕不讓 EOD 失敗。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo

from app.connectors.telegram import TelegramError
from app.core.config import get_thresholds
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.services.notify_settings import resolve_telegram
from app.services.signal_alerts import AlertReport, format_telegram_messages, load_action_changes

logger = get_logger("jobs.notify_signals")


def _local_today() -> dt.date:
    tz = get_thresholds().schedule.get("timezone", "Asia/Taipei")
    return dt.datetime.now(ZoneInfo(tz)).date()


def _summary(report: AlertReport, actions: tuple[str, ...]) -> dict:
    return {a: len(report.by_action(a)) for a in actions}


async def run(
    target: dt.date,
    *,
    dry_run: bool = False,
    note: str | None = None,
) -> dict:
    """回傳 {status, new: {action: n}, messages: n, ...}。

    status：sent / dry_run / disabled / unconfigured / no_snapshot / error。
    `note` 會以 ⚠️ 前置到訊息（例：EOD 資料不完整仍推送時說明原因）。
    設定來源：/settings 頁（DB）> .env > YAML，見 app.services.notify_settings。
    """
    sm = get_sessionmaker()
    async with sm() as s:
        conf = await resolve_telegram(s)
        if not dry_run:
            if not conf.enabled:
                return {"status": "disabled"}
            if not conf.configured:
                logger.warning("Telegram 推播已啟用但未設定 Bot token / Chat ID，略過推播")
                return {"status": "unconfigured"}
        report = await load_action_changes(
            s, target, conf.actions,
            lookback_days=conf.lookback_days, min_turnover=conf.min_turnover,
        )
    if not report.has_snapshot:
        logger.info("%s 無 signal_snapshot（非交易日或尚未落地），不推播", target)
        return {"status": "no_snapshot"}

    messages = format_telegram_messages(
        report, conf.actions,
        max_items=conf.max_items, max_reasons=conf.max_reasons,
        max_chars=conf.max_message_chars, note=note,
    )
    new = _summary(report, conf.actions)
    if dry_run:
        for i, m in enumerate(messages, 1):
            print(f"--- 訊息 {i}/{len(messages)}（{len(m)} 字）---\n{m}\n")
        return {"status": "dry_run", "new": new, "messages": len(messages),
                "enabled": conf.enabled, "configured": conf.configured}

    client = conf.client()
    sent = 0
    try:
        for m in messages:
            await client.send_message(m)
            sent += 1
    except TelegramError as e:
        logger.warning("Telegram 推播失敗（已送 %d/%d 則）：%s", sent, len(messages), e)
        return {"status": "error", "error": str(e), "new": new,
                "messages": len(messages), "sent": sent}
    logger.info("Telegram 推播完成 %s：%s，共 %d 則", target, new, sent)
    return {"status": "sent", "new": new, "messages": len(messages), "sent": sent}


def main() -> None:
    p = argparse.ArgumentParser(description="推播當日新進 BUY / AVOID 到 Telegram")
    p.add_argument("date", nargs="?", help="YYYY-MM-DD；省略＝設定時區的今天")
    p.add_argument("--dry-run", action="store_true", help="只印出訊息，不送")
    args = p.parse_args()
    target = dt.date.fromisoformat(args.date) if args.date else _local_today()
    result = asyncio.run(run(target, dry_run=args.dry_run))
    print(result)
    if result.get("status") == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
