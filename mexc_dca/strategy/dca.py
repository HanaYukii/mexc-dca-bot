from __future__ import annotations

import logging
import time

from ..config import CoinConfig
from ..exchange import Exchange
from ..logger import TradeLogger
from ..notifier import Notifier

log = logging.getLogger(__name__)

POLL_INTERVAL = 60  # seconds


def execute_dca(
    exchange: Exchange,
    coin: CoinConfig,
    trade_logger: TradeLogger,
    notifier: Notifier,
) -> None:
    symbol = coin.symbol
    base_currency = symbol.split("/")[0]
    log.info("=== DCA start: %s, amount=%.2f USDT ===", symbol, coin.amount_usdt)

    # Check USDT balance
    try:
        usdt_balance = exchange.get_usdt_balance()
    except Exception as e:
        msg = f"Failed to fetch balance: {e}"
        log.error(msg)
        notifier.send_error(msg)
        return

    if usdt_balance < coin.amount_usdt:
        msg = f"Insufficient USDT balance: {usdt_balance:.2f} < {coin.amount_usdt:.2f} for {symbol}"
        log.warning(msg)
        notifier.send_error(msg)
        return

    # Fetch current price
    try:
        ticker = exchange.fetch_ticker(symbol)
    except Exception as e:
        msg = f"Failed to fetch ticker for {symbol}: {e}"
        log.error(msg)
        notifier.send_error(msg)
        return

    market_price = ticker["last"]
    limit_price = market_price * (1 - coin.limit_offset_pct / 100)
    amount = coin.amount_usdt / limit_price

    log.info("Market price: %.8f, limit price: %.8f (-%s%%), amount: %.8f",
             market_price, limit_price, coin.limit_offset_pct, amount)

    # Place limit order
    order = None
    order_type = "limit"
    try:
        order = exchange.create_limit_buy(symbol, amount, limit_price)
    except Exception as e:
        msg = f"Failed to place limit order for {symbol}: {e}"
        log.error(msg)
        notifier.send_error(msg)
        return

    order_id = order["id"]

    # Poll for fill
    filled = False
    timeout = coin.timeout_minutes * 60
    elapsed = 0
    while elapsed < timeout:
        time.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
        try:
            status = exchange.fetch_order(order_id, symbol)
        except Exception as e:
            log.warning("Error polling order %s: %s", order_id, e)
            continue
        if status.get("status") == "closed":
            order = status
            filled = True
            log.info("Limit order filled: %s", order_id)
            break
        log.debug("Order %s still open, elapsed=%ds", order_id, elapsed)

    # Fallback to market order
    if not filled:
        log.info("Limit order %s not filled after %dm, cancelling -> market order", order_id, coin.timeout_minutes)
        order_type = "market"
        try:
            exchange.cancel_order(order_id, symbol)
        except Exception as e:
            log.warning("Cancel failed (may already be filled): %s", e)

        try:
            order = exchange.create_market_buy(symbol, coin.amount_usdt)
            filled = True
        except Exception as e:
            msg = f"Market order also failed for {symbol}: {e}"
            log.error(msg)
            notifier.send_error(msg)
            trade_logger.record(
                symbol=symbol, side="buy", order_type=order_type,
                amount=0, price=0, cost=0, fee=0, filled=False, error=str(e),
            )
            return

    # Log trade
    fill_price = order.get("average") or order.get("price") or limit_price
    fill_amount = order.get("filled") or amount
    fee_info = order.get("fee", {})
    fee_cost = fee_info.get("cost", 0) if isinstance(fee_info, dict) else 0

    entry = trade_logger.record(
        symbol=symbol,
        side="buy",
        order_type=order_type,
        amount=fill_amount,
        price=fill_price,
        cost=fill_price * fill_amount if fill_price and fill_amount else coin.amount_usdt,
        fee=fee_cost,
        filled=filled,
    )

    notifier.send_order_filled(entry)

    # Check withdrawal threshold
    if coin.withdraw.enabled and coin.withdraw.address:
        try:
            balance = exchange.get_balance(base_currency)
            threshold_amount = coin.withdraw.threshold_usdt / fill_price if fill_price else 0
            if balance >= threshold_amount and threshold_amount > 0:
                log.info("Withdrawal threshold met: %.8f >= %.8f %s", balance, threshold_amount, base_currency)
                result = exchange.withdraw(
                    base_currency, balance, coin.withdraw.address, coin.withdraw.network,
                )
                txid = result.get("id", "unknown")
                trade_logger.record(
                    symbol=symbol, action="withdraw",
                    amount=balance, address=coin.withdraw.address,
                    network=coin.withdraw.network, txid=txid,
                )
                notifier.send_withdrawal(symbol, balance, coin.withdraw.address, txid)
        except Exception as e:
            msg = f"Withdrawal failed for {base_currency}: {e}"
            log.error(msg)
            notifier.send_error(msg)
