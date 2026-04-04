from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import AppConfig
from .exchange import Exchange
from .logger import TradeLogger
from .notifier import Notifier
from .strategy.dca import execute_dca

log = logging.getLogger(__name__)


def _parse_cron(expr: str) -> dict:
    """Parse '0 9 * * *' into CronTrigger kwargs."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expr}")
    return {
        "minute": parts[0],
        "hour": parts[1],
        "day": parts[2],
        "month": parts[3],
        "day_of_week": parts[4],
    }


def build_scheduler(config: AppConfig) -> BlockingScheduler:
    scheduler = BlockingScheduler()
    exchange = Exchange(config)
    trade_logger = TradeLogger(config.log_file)
    notifier = Notifier(config.telegram)

    for coin in config.coins:
        cron_kwargs = _parse_cron(coin.schedule)
        job_id = f"dca_{coin.symbol.replace('/', '_')}"

        scheduler.add_job(
            execute_dca,
            trigger=CronTrigger(**cron_kwargs),
            id=job_id,
            name=f"DCA {coin.symbol}",
            args=[exchange, coin, trade_logger, notifier],
            replace_existing=True,
            misfire_grace_time=300,
        )
        log.info("Scheduled job %s: %s @ %s", job_id, coin.symbol, coin.schedule)

    # Weekly summary
    if config.telegram.enabled:
        scheduler.add_job(
            _send_weekly_summary,
            trigger=CronTrigger(
                day_of_week=str(config.telegram.weekly_summary_day),
                hour="9",
                minute="0",
            ),
            id="weekly_summary",
            name="Weekly Summary",
            args=[trade_logger, notifier],
            replace_existing=True,
        )
        log.info("Scheduled weekly summary on day_of_week=%d", config.telegram.weekly_summary_day)

    return scheduler


def _send_weekly_summary(trade_logger: TradeLogger, notifier: Notifier) -> None:
    trades = trade_logger.read_recent(days=7)
    notifier.send_weekly_summary(trades)
