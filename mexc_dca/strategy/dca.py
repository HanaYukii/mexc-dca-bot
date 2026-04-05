from __future__ import annotations

import logging

from ..config import CoinConfig
from ..exchange import Exchange
from ..logger import TradeLogger
from ..notifier import Notifier

log = logging.getLogger(__name__)


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

    # Place limit order and leave it (fire-and-forget)
    try:
        order = exchange.create_limit_buy(symbol, amount, limit_price)
    except Exception as e:
        msg = f"Failed to place limit order for {symbol}: {e}"
        log.error(msg)
        notifier.send_error(msg)
        return

    order_id = order["id"]

    # Log the placed order
    entry = trade_logger.record(
        symbol=symbol,
        side="buy",
        order_type="limit",
        order_id=order_id,
        amount=amount,
        price=limit_price,
        cost=coin.amount_usdt,
        fee=0,
        filled=False,
        status="placed",
    )

    notifier.send_order_placed(entry)
    log.info("Limit order placed: %s (id=%s), price=%.8f, amount=%.8f",
             symbol, order_id, limit_price, amount)

    # Check withdrawal threshold
    if coin.withdraw.enabled and coin.withdraw.address:
        try:
            balance = exchange.get_balance(base_currency)
            threshold_amount = coin.withdraw.threshold_usdt / market_price if market_price else 0
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
