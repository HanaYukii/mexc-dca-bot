"""Slow asset rotation (e.g. ETH -> BTC).

Fires once a day and places, FIRE-AND-FORGET (no tracking/cancel, like the DCA
and range-fade bots):
  * a maker SELL of order_usdt of `sell_symbol` at +offset% above market
  * a maker BUY  of order_usdt of `buy_symbol`  at -offset% below market

Net effect over time: rotate out of sell_symbol into buy_symbol, capturing a
small maker edge on each leg. The two legs are independent (sell -> USDT, USDT
-> buy); they fill whenever price ticks the small offset, so most days both go
through. Balance guards skip a leg when funds are short — e.g. the SELL only
places if enough FREE (unstaked) sell-coin is held, so it naturally pauses once
the free balance is exhausted (top up by unstaking more).
"""
from __future__ import annotations

import logging

from ..config import RotateConfig
from ..exchange import Exchange
from ..logger import TradeLogger
from ..notifier import Notifier

log = logging.getLogger(__name__)


def execute_rotate(
    exchange: Exchange,
    cfg: RotateConfig,
    trade_logger: TradeLogger,
    notifier: Notifier,
) -> None:
    # ----- SELL sell_symbol at +offset% -----
    sell_base = cfg.sell_symbol.split("/")[0]
    try:
        last = exchange.fetch_ticker(cfg.sell_symbol)["last"]
        price = float(exchange.price_to_precision(cfg.sell_symbol, last * (1 + cfg.offset_pct / 100)))
        amount = float(exchange.amount_to_precision(cfg.sell_symbol, cfg.order_usdt / price))
        held = exchange.get_balance(sell_base)
        if held < amount:
            log.warning("Rotate SELL skip: free %s %.8f < %.8f needed (unstake more to continue)",
                        sell_base, held, amount)
        else:
            o = exchange.create_limit_sell(cfg.sell_symbol, amount, price)
            trade_logger.record(strategy="rotate", symbol=cfg.sell_symbol, side="sell",
                                order_type="limit", order_id=o.get("id"), amount=amount,
                                price=price, proceeds=cfg.order_usdt, status="placed")
            log.info("SELL %s @ %.4f x %.8f (+%.2f%%, market %.4f)",
                     cfg.sell_symbol, price, amount, cfg.offset_pct, last)
    except Exception as e:
        log.error("Rotate SELL failed: %s", e)
        notifier.send_error(f"Rotate SELL failed: {e}")

    # ----- BUY buy_symbol at -offset% -----
    try:
        last = exchange.fetch_ticker(cfg.buy_symbol)["last"]
        price = float(exchange.price_to_precision(cfg.buy_symbol, last * (1 - cfg.offset_pct / 100)))
        amount = float(exchange.amount_to_precision(cfg.buy_symbol, cfg.order_usdt / price))
        usdt = exchange.get_usdt_balance()
        if usdt < cfg.order_usdt:
            log.warning("Rotate BUY skip: USDT %.2f < %.2f", usdt, cfg.order_usdt)
        else:
            o = exchange.create_limit_buy(cfg.buy_symbol, amount, price)
            trade_logger.record(strategy="rotate", symbol=cfg.buy_symbol, side="buy",
                                order_type="limit", order_id=o.get("id"), amount=amount,
                                price=price, cost=cfg.order_usdt, status="placed")
            log.info("BUY %s @ %.4f x %.8f (-%.2f%%, market %.4f)",
                     cfg.buy_symbol, price, amount, cfg.offset_pct, last)
    except Exception as e:
        log.error("Rotate BUY failed: %s", e)
        notifier.send_error(f"Rotate BUY failed: {e}")
