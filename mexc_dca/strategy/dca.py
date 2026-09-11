from __future__ import annotations

import logging
from datetime import datetime, timezone

import ccxt

from ..config import CoinConfig
from ..exchange import Exchange
from ..logger import TradeLogger
from ..notifier import Notifier

log = logging.getLogger(__name__)


def _mark_resolved(trade_logger: TradeLogger, symbol: str, order_id: str, reason: str) -> None:
    """Append a marker so this order id is never re-checked by the stale chase."""
    trade_logger.record(
        _quiet=True,
        strategy="dca_resolved",
        symbol=symbol,
        order_id=order_id,
        reason=reason,
    )


def _is_order_not_found(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, ccxt.OrderNotFound) or "does not exist" in text or "-2013" in text


def _chase_stale_orders(
    exchange: Exchange,
    coin: CoinConfig,
    trade_logger: TradeLogger,
    notifier: Notifier,
) -> None:
    """Reprice this strategy's open buys after the configured age.

    Each replacement preserves the stale order's remaining quote value and is
    placed at the current market minus ``limit_offset_pct``.  Other strategies
    use separate logs, so their order ids are never touched.

    Every order id that turns out to be terminal (filled, cancelled, repriced,
    or already purged by the exchange) gets a ``dca_resolved`` marker in the
    trade log and is skipped on later runs, so the check only ever touches
    genuinely open orders instead of re-fetching the whole history every day.
    """
    if coin.chase_after_hours <= 0 or exchange.dry_run:
        return

    cutoff_seconds = coin.chase_after_hours * 3600
    now = datetime.now(timezone.utc)
    resolved: set[str] = set()
    candidates: dict[str, dict] = {}
    for entry in trade_logger.read_all():
        if entry.get("symbol") != coin.symbol:
            continue
        order_id = entry.get("order_id")
        if not order_id or order_id == "dry-run":
            continue
        if entry.get("strategy") == "dca_resolved":
            resolved.add(str(order_id))
            continue
        if entry.get("side") != "buy":
            continue
        if entry.get("strategy") not in (None, "dca", "dca_chase"):
            continue
        timestamp = entry.get("timestamp")
        if not timestamp:
            continue
        try:
            age = (now - datetime.fromisoformat(timestamp)).total_seconds()
        except (TypeError, ValueError):
            continue
        if age >= cutoff_seconds:
            candidates[str(order_id)] = entry

    pruned_missing = pruned_done = repriced = 0
    for order_id, entry in candidates.items():
        if order_id in resolved:
            continue
        try:
            order = exchange.fetch_order(order_id, coin.symbol)
        except Exception as e:
            if _is_order_not_found(e):
                _mark_resolved(trade_logger, coin.symbol, order_id, "not_on_exchange")
                pruned_missing += 1
                log.debug("Stale DCA order %s no longer on exchange; pruned", order_id)
            else:
                log.warning("Could not check stale DCA order %s: %s", order_id, e)
            continue
        status = order.get("status")
        if status != "open":
            _mark_resolved(trade_logger, coin.symbol, order_id, f"status_{status}")
            pruned_done += 1
            continue

        remaining = float(order.get("remaining") or 0)
        old_price = float(order.get("price") or entry.get("price") or 0)
        remaining_usdt = remaining * old_price
        if remaining_usdt <= 0:
            _mark_resolved(trade_logger, coin.symbol, order_id, "nothing_remaining")
            pruned_done += 1
            continue

        try:
            exchange.cancel_order(order_id, coin.symbol)
            market = float(exchange.fetch_ticker(coin.symbol)["last"])
            new_price = float(exchange.price_to_precision(
                coin.symbol, market * (1 - coin.limit_offset_pct / 100)
            ))
            new_amount = float(exchange.amount_to_precision(
                coin.symbol, remaining_usdt / new_price
            ))
            replacement = exchange.create_limit_buy(coin.symbol, new_amount, new_price)
        except Exception as e:
            msg = f"Failed to chase stale DCA order {order_id} for {coin.symbol}: {e}"
            log.error(msg)
            notifier.send_error(msg)
            continue

        trade_logger.record(
            strategy="dca_chase",
            symbol=coin.symbol,
            side="buy",
            order_type="limit",
            order_id=replacement["id"],
            replaces_order_id=order_id,
            amount=new_amount,
            price=new_price,
            cost=remaining_usdt,
            fee=0,
            filled=False,
            status="placed",
        )
        _mark_resolved(trade_logger, coin.symbol, order_id, "repriced")
        repriced += 1
        log.info(
            "Repriced stale DCA order: %s %s -> %s @ %.8f (%.2f USDT, age >= %dh)",
            coin.symbol, order_id, replacement["id"], new_price,
            remaining_usdt, coin.chase_after_hours,
        )

    if pruned_missing or pruned_done or repriced:
        log.info(
            "Stale DCA check %s: repriced=%d, pruned=%d (%d done, %d no longer on exchange) "
            "- pruned ids won't be re-checked",
            coin.symbol, repriced, pruned_done + pruned_missing, pruned_done, pruned_missing,
        )


def execute_dca(
    exchange: Exchange,
    coin: CoinConfig,
    trade_logger: TradeLogger,
    notifier: Notifier,
) -> None:
    symbol = coin.symbol
    base_currency = symbol.split("/")[0]
    log.info("=== DCA start: %s, amount=%.2f USDT ===", symbol, coin.amount_usdt)

    # Reprice this coin's old unfilled DCA orders before placing today's lot.
    _chase_stale_orders(exchange, coin, trade_logger, notifier)

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
        strategy="dca",
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
