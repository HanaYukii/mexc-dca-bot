"""24h range-fade — a second 'DCA' that fires once a day.

Each run places, fire-and-forget (orders are NOT tracked or cancelled, they rest
until filled, exactly like the DCA bot):
  * a maker BUY  at the 24h low
  * a maker SELL at the 24h high   (only if enough base coin is held)

Balance guards mean it simply skips a side when funds are already tied up in
resting orders, so it self-limits without any cancel logic. Uses its own order
ids only in the log, so it never touches DCA's orders on the same symbol.
"""
from __future__ import annotations

import logging

from ..config import RangeFadeConfig
from ..exchange import Exchange
from ..logger import TradeLogger
from ..notifier import Notifier

log = logging.getLogger(__name__)


def execute_rangefade(
    exchange: Exchange,
    cfg: RangeFadeConfig,
    trade_logger: TradeLogger,
    notifier: Notifier,
) -> None:
    symbol = cfg.symbol
    base = symbol.split("/")[0]

    try:
        t = exchange.fetch_ticker(symbol)
    except Exception as e:
        log.error("Range-fade: fetch_ticker failed: %s", e)
        notifier.send_error(f"Range-fade fetch failed: {e}")
        return

    high, low, last = t.get("high"), t.get("low"), t.get("last")
    if not high or not low:
        log.error("Range-fade: ticker missing 24h high/low (high=%s low=%s)", high, low)
        return
    log.info("=== Range-fade %s: 24h high=%.4f low=%.4f last=%.4f ===", symbol, high, low, last)

    # ----- BUY at the 24h low -----
    buy_price = float(exchange.price_to_precision(symbol, low))
    buy_amount = float(exchange.amount_to_precision(symbol, cfg.order_usdt / buy_price))
    try:
        usdt = exchange.get_usdt_balance()
        if usdt < cfg.order_usdt:
            log.warning("Skip BUY: USDT %.2f < %.2f (funds tied up in resting orders?)",
                        usdt, cfg.order_usdt)
        else:
            o = exchange.create_limit_buy(symbol, buy_amount, buy_price)
            trade_logger.record(strategy="rangefade", symbol=symbol, side="buy",
                                order_type="limit", order_id=o.get("id"), amount=buy_amount,
                                price=buy_price, cost=cfg.order_usdt, status="placed")
            log.info("BUY placed @ %.4f x %.8f (24h low)", buy_price, buy_amount)
    except Exception as e:
        log.error("Range-fade BUY failed: %s", e)
        notifier.send_error(f"Range-fade BUY failed: {e}")

    # ----- SELL at the 24h high (only if we hold enough base coin) -----
    sell_price = float(exchange.price_to_precision(symbol, high))
    sell_amount = float(exchange.amount_to_precision(symbol, cfg.order_usdt / sell_price))
    try:
        held = exchange.get_balance(base)
        if held < sell_amount:
            log.warning("Skip SELL: %s %.8f < %.8f needed (not enough %s held)",
                        base, held, sell_amount, base)
        else:
            o = exchange.create_limit_sell(symbol, sell_amount, sell_price)
            trade_logger.record(strategy="rangefade", symbol=symbol, side="sell",
                                order_type="limit", order_id=o.get("id"), amount=sell_amount,
                                price=sell_price, proceeds=cfg.order_usdt, status="placed")
            log.info("SELL placed @ %.4f x %.8f (24h high)", sell_price, sell_amount)
    except Exception as e:
        log.error("Range-fade SELL failed: %s", e)
        notifier.send_error(f"Range-fade SELL failed: {e}")
