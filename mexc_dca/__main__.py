from __future__ import annotations

import argparse
import logging
import sys

from .config import CoinConfig, load_config
from .exchange import Exchange
from .logger import TradeLogger
from .notifier import Notifier
from .scheduler import build_scheduler
from .strategy.dca import execute_dca


def run_once(config, symbol: str, amount_usdt: float, timeout_minutes: int = 15) -> None:
    """Execute a single DCA buy immediately."""
    coin = CoinConfig(
        symbol=symbol,
        amount_usdt=amount_usdt,
        schedule="",
        limit_offset_pct=0.1,
        timeout_minutes=timeout_minutes,
    )
    # Override from config if this coin exists
    for c in config.coins:
        if c.symbol.upper() == symbol.upper():
            coin.limit_offset_pct = c.limit_offset_pct
            coin.timeout_minutes = c.timeout_minutes
            coin.withdraw = c.withdraw
            break

    exchange = Exchange(config)
    trade_logger = TradeLogger(config.log_file)
    notifier = Notifier(config.telegram)
    execute_dca(exchange, coin, trade_logger, notifier)


def main() -> None:
    parser = argparse.ArgumentParser(description="MEXC Spot DCA Bot")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--dry-run", action="store_true", help="Simulate orders without placing them")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--once", metavar="SYMBOL", help="Buy once immediately, e.g. --once BTC/USDT")
    parser.add_argument("--amount", type=float, help="USDT amount for --once (default: from config or 5)")
    parser.add_argument("--timeout", type=int, help="Limit order timeout in minutes for --once (default: 15)")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("mexc_dca")

    try:
        config = load_config(args.config, args.env)
    except FileNotFoundError as e:
        log.error("Config error: %s", e)
        sys.exit(1)

    if args.dry_run:
        config.dry_run = True
        log.info("=== DRY RUN MODE ===")

    # Single buy mode
    if args.once:
        amount = args.amount or 5.0
        timeout = args.timeout or 15
        log.info("=== ONE-TIME BUY: %s %.2f USDT (timeout=%dm) ===", args.once, amount, timeout)
        run_once(config, args.once, amount, timeout)
        return

    if not config.coins:
        log.error("No coins configured. Check config.yaml.")
        sys.exit(1)

    log.info("Starting MEXC DCA Bot with %d coin(s)...", len(config.coins))
    for c in config.coins:
        log.info("  %s: %.2f USDT, schedule=%s", c.symbol, c.amount_usdt, c.schedule)

    scheduler = build_scheduler(config)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
