from __future__ import annotations

import logging
import time
from typing import Any

import ccxt

from .config import AppConfig

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, exponential


class Exchange:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = ccxt.mexc({
            "apiKey": config.api_key,
            "secret": config.api_secret,
            "enableRateLimit": True,
        })
        self.dry_run = config.dry_run

    def _retry(self, fn, *args, **kwargs) -> Any:
        for attempt in range(MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                wait = RETRY_BACKOFF ** (attempt + 1)
                log.warning("Retryable error (attempt %d/%d): %s. Waiting %ds...", attempt + 1, MAX_RETRIES, e, wait)
                if attempt == MAX_RETRIES - 1:
                    raise
                time.sleep(wait)

    def fetch_ticker(self, symbol: str) -> dict:
        return self._retry(self.client.fetch_ticker, symbol)

    def get_usdt_balance(self) -> float:
        balance = self._retry(self.client.fetch_balance)
        return float(balance.get("USDT", {}).get("free", 0))

    def get_balance(self, currency: str) -> float:
        balance = self._retry(self.client.fetch_balance)
        return float(balance.get(currency, {}).get("free", 0))

    def create_limit_buy(self, symbol: str, amount: float, price: float) -> dict:
        log.info("Limit buy: %s amount=%.8f price=%.8f", symbol, amount, price)
        if self.dry_run:
            log.info("[DRY RUN] Would place limit buy")
            return {"id": "dry-run", "status": "open", "price": price, "amount": amount, "filled": 0}
        return self._retry(self.client.create_limit_buy_order, symbol, amount, price)

    def create_market_buy(self, symbol: str, amount_usdt: float) -> dict:
        log.info("Market buy: %s cost=%.4f USDT", symbol, amount_usdt)
        if self.dry_run:
            log.info("[DRY RUN] Would place market buy")
            return {"id": "dry-run", "status": "closed", "cost": amount_usdt, "filled": 0, "average": 0}
        # MEXC market buy: use quoteOrderQty (cost in USDT)
        return self._retry(
            self.client.create_order, symbol, "market", "buy", None,
            None, {"quoteOrderQty": amount_usdt},
        )

    def fetch_order(self, order_id: str, symbol: str) -> dict:
        if self.dry_run:
            return {"id": "dry-run", "status": "closed", "filled": 0}
        return self._retry(self.client.fetch_order, order_id, symbol)

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        log.info("Cancelling order %s for %s", order_id, symbol)
        if self.dry_run:
            return {"id": "dry-run", "status": "canceled"}
        return self._retry(self.client.cancel_order, order_id, symbol)

    def withdraw(self, currency: str, amount: float, address: str, network: str) -> dict:
        log.info("Withdraw %s %.8f to %s (network=%s)", currency, amount, address, network)
        if self.dry_run:
            log.info("[DRY RUN] Would withdraw")
            return {"id": "dry-run-txid"}
        return self._retry(
            self.client.withdraw, currency, amount, address,
            params={"network": network},
        )
