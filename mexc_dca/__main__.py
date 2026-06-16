from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import CoinConfig, load_config
from .exchange import Exchange
from .logger import TradeLogger
from .notifier import Notifier
from .scheduler import build_scheduler
from .strategy.dca import execute_dca
from .strategy.grid import GridFlip


def show_stats(config) -> None:
    """Display portfolio stats with average cost, current price, and P&L."""
    trade_logger = TradeLogger(config.log_file)
    exchange = Exchange(config)

    stats = trade_logger.compute_stats()
    if not stats:
        print("\nNo trades found.\n")
        return

    print("\n" + "=" * 70)
    print("  MEXC DCA Portfolio Stats")
    print("=" * 70)

    total_cost_all = 0.0
    total_value_all = 0.0

    for symbol, s in sorted(stats.items()):
        base = symbol.split("/")[0]
        try:
            ticker = exchange.fetch_ticker(symbol)
            current_price = ticker.get("last", 0.0)
        except Exception:
            current_price = 0.0

        current_value = s["total_amount"] * current_price
        pnl = current_value - s["total_cost"]
        pnl_pct = (pnl / s["total_cost"] * 100) if s["total_cost"] > 0 else 0.0

        total_cost_all += s["total_cost"]
        total_value_all += current_value

        print(f"\n  {symbol}")
        print(f"  {'─' * 40}")
        print(f"  Buys:          {s['buy_count']}")
        print(f"  Total Amount:  {s['total_amount']:.8f} {base}")
        print(f"  Total Cost:    {s['total_cost']:.2f} USDT")
        print(f"  Avg Price:     {s['avg_price']:.2f} USDT")
        if current_price > 0:
            print(f"  Current Price: {current_price:.2f} USDT")
            print(f"  Current Value: {current_value:.2f} USDT")
            sign = "+" if pnl >= 0 else ""
            print(f"  P&L:           {sign}{pnl:.2f} USDT ({sign}{pnl_pct:.1f}%)")
        print(f"  First Buy:     {s['first_buy'][:10]}")
        print(f"  Last Buy:      {s['last_buy'][:10]}")

    if len(stats) > 1 and total_cost_all > 0:
        total_pnl = total_value_all - total_cost_all
        total_pnl_pct = (total_pnl / total_cost_all * 100)
        sign = "+" if total_pnl >= 0 else ""
        print(f"\n  {'=' * 40}")
        print(f"  TOTAL COST:    {total_cost_all:.2f} USDT")
        print(f"  TOTAL VALUE:   {total_value_all:.2f} USDT")
        print(f"  TOTAL P&L:     {sign}{total_pnl:.2f} USDT ({sign}{total_pnl_pct:.1f}%)")

    print("\n" + "=" * 70 + "\n")


def run_grid(config) -> None:
    """Run the single-slot flip grid strategy (continuous loop)."""
    if not config.grid:
        raise SystemExit("No [grid] section in config.yaml")
    exchange = Exchange(config)
    trade_logger = TradeLogger(config.grid.log_file)
    notifier = Notifier(config.telegram)
    GridFlip(exchange, config.grid, trade_logger, notifier).run()


def show_grid_stats(config) -> None:
    """Show realized profit and live state of the two-sided grid."""
    if not config.grid:
        raise SystemExit("No [grid] section in config.yaml")
    trades = TradeLogger(config.grid.log_file).read_all()
    sells = [t for t in trades if t.get("side") == "sell" and t.get("strategy") == "grid"]
    buys = [t for t in trades if t.get("side") == "buy" and t.get("strategy") == "grid"]
    total = sum(t.get("profit", 0.0) for t in sells)

    print("\n" + "=" * 56)
    print("  Grid (paired-flip) Stats")
    print("=" * 56)
    print(f"  Buys filled:      {len(buys)}")
    print(f"  Sells filled:     {len(sells)}")
    print(f"  Realized profit:  {total:+.4f} USDT")
    if sells:
        wins = [t for t in sells if t.get("profit", 0) > 0]
        print(f"  Avg per sell:     {total / len(sells):+.4f} USDT")
        print(f"  Win rate:         {len(wins)}/{len(sells)}")
        print(f"  First sell:       {sells[0].get('timestamp', '')[:19]}")
        print(f"  Last sell:        {sells[-1].get('timestamp', '')[:19]}")

    state_path = Path(config.grid.state_file)
    if state_path.exists():
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            st = {}
        lots = st.get("lots") or []
        deployed = sum(l.get("buy_price", 0) * l.get("amount", 0) for l in lots)
        print(f"\n  Open lots:        {len(lots)}/{config.grid.max_lots}  (~{deployed:.2f} USDT deployed)")
        for l in sorted(lots, key=lambda x: x.get("buy_price", 0), reverse=True):
            tgt = l.get("sell_price") or l.get("buy_price", 0) * (1 + config.grid.profit_pct / 100)
            armed = "sell armed" if l.get("sell_order_id") else "sell pending"
            print(f"    lot buy {l.get('buy_price', 0):.2f} -> sell {tgt:.2f}  ({armed})")
        if st.get("buy_order_id"):
            print(f"  Working BUY:      {st.get('buy_amount') or 0:.8f} @ {st.get('buy_price') or 0:.6f}")
        else:
            print("  Working BUY:      (none)")
    print("=" * 56 + "\n")


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
    parser.add_argument("--stats", action="store_true", help="Show portfolio stats (avg price, total cost, P&L)")
    parser.add_argument("--grid", action="store_true", help="Run the single-slot flip grid strategy (loop)")
    parser.add_argument("--grid-stats", action="store_true", help="Show grid realized profit stats")
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

    # Stats mode
    if args.stats:
        show_stats(config)
        return

    # Grid stats
    if args.grid_stats:
        show_grid_stats(config)
        return

    # Grid strategy (continuous loop)
    if args.grid:
        run_grid(config)
        return

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
