"""Two-sided single-level grid.

Keeps ONE buy order and ONE sell order working at the same time:
  * BUY  leg: limit buy of order_usdt        at mid*(1 - buy_offset%)  (maker, no fee)
  * SELL leg: limit sell of order_usdt-worth at mid*(1 + profit%)      (maker, no fee)

When a leg fills it is re-placed around the new mid on the next tick, so the bot
continuously buys dips and sells rips, capturing the spread. Inventory is shared:
buy fills add base coin, sell fills remove it, each guarded by the free balance.

Profit uses a running average cost. On first start the existing holdings are
seeded at the current market price, so selling them +profit%% books +profit%% as
realized profit (matches the "sell 1%% above market" intent).

State is persisted to JSON and reconciled against the exchange on startup, so the
loop survives restarts, manual cancels, and the handoff from the old flip logic.
Dry-run simulates fills against the live ticker (paper trading).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from ..config import GridConfig
from ..exchange import Exchange
from ..logger import TradeLogger
from ..notifier import Notifier

log = logging.getLogger(__name__)


class GridFlip:
    def __init__(
        self,
        exchange: Exchange,
        cfg: GridConfig,
        trade_logger: TradeLogger,
        notifier: Notifier,
    ) -> None:
        self.ex = exchange
        self.cfg = cfg
        self.logger = trade_logger
        self.notifier = notifier
        self.dry = exchange.dry_run
        self.base = cfg.symbol.split("/")[0]
        self.state_path = Path(cfg.state_file)
        self.state = self._load_state()

    # ----- state persistence -----
    def _default_state(self) -> dict:
        return {
            "buy_order_id": None, "buy_price": None, "buy_amount": None,
            "sell_order_id": None, "sell_price": None, "sell_amount": None,
            "inv_amount": 0.0, "inv_cost": 0.0, "seeded": False,
            "buys_filled": 0, "sells_filled": 0, "realized_profit": 0.0,
        }

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                base = self._default_state()
                base.update({k: v for k, v in data.items() if k in base})
                return base
            except Exception as e:
                log.warning("Could not read state file (%s), starting fresh", e)
        return self._default_state()

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _mid(self) -> float:
        return self.ex.fetch_ticker(self.cfg.symbol)["last"]

    # ----- startup: seed cost basis + adopt existing orders -----
    def _seed_inventory(self, mid: float) -> None:
        if self.state["seeded"]:
            return
        held = self.ex.get_balance(self.base)  # read-only, works in dry-run too
        self.state.update(inv_amount=held, inv_cost=held * mid, seeded=True)
        self._save_state()
        log.info("Seeded inventory: %.8f %s @ %.6f (cost basis %.2f USDT)",
                 held, self.base, mid, held * mid)

    def _reconcile(self) -> None:
        """Adopt existing open orders so a restart/handoff never double-places.

        Keeps at most one order per side (cancels any extras) and picks the order
        up into state so we manage it instead of placing a fresh duplicate.
        """
        try:
            open_orders = self.ex.fetch_open_orders(self.cfg.symbol)
        except Exception as e:
            log.warning("Reconcile: could not fetch open orders: %s", e)
            return
        buys = [o for o in open_orders if o.get("side") == "buy"]
        sells = [o for o in open_orders if o.get("side") == "sell"]
        for extra in buys[1:] + sells[1:]:
            log.info("Reconcile: cancelling extra %s order %s", extra.get("side"), extra.get("id"))
            try:
                self.ex.cancel_order(extra["id"], self.cfg.symbol)
            except Exception as e:
                log.warning("Reconcile: cancel failed: %s", e)
        if buys:
            o = buys[0]
            self.state.update(buy_order_id=o["id"], buy_price=float(o["price"]),
                              buy_amount=float(o["amount"]))
            log.info("Reconcile: adopted buy %s @ %.6f x %.8f",
                     o["id"], float(o["price"]), float(o["amount"]))
        if sells:
            o = sells[0]
            self.state.update(sell_order_id=o["id"], sell_price=float(o["price"]),
                              sell_amount=float(o["amount"]))
            log.info("Reconcile: adopted sell %s @ %.6f x %.8f",
                     o["id"], float(o["price"]), float(o["amount"]))
        self._save_state()

    # ----- main loop -----
    def run(self) -> None:
        c = self.cfg
        mode = "PAPER (dry-run)" if self.dry else "LIVE"
        log.info(
            "=== Grid 2-sided [%s]: %s, %.2f USDT/order, buy -%.2f%%, sell +%.2f%%, poll %ds ===",
            mode, c.symbol, c.order_usdt, c.buy_offset_pct, c.profit_pct, c.poll_interval_sec,
        )
        self._seed_inventory(self._mid())
        if not self.dry:
            self._reconcile()
        log.info("Resuming: inv=%.8f %s, buys=%d, sells=%d, realized=%+.4f USDT",
                 self.state["inv_amount"], self.base, self.state["buys_filled"],
                 self.state["sells_filled"], self.state["realized_profit"])
        while True:
            try:
                self._handle_buy()
                self._handle_sell()
            except Exception as e:
                log.error("Grid tick error: %s", e)
                self.notifier.send_error(f"Grid error: {e}")
            time.sleep(c.poll_interval_sec)

    # ----- buy leg -----
    def _handle_buy(self) -> None:
        s, sym = self.state, self.cfg.symbol
        if s["buy_order_id"] is None:
            self._place_buy()
            return
        if self.dry:
            if self._mid() > s["buy_price"]:
                return
            filled, avg = s["buy_amount"], s["buy_price"]
        else:
            try:
                o = self.ex.fetch_order(s["buy_order_id"], sym)
            except Exception as e:
                log.warning("Could not fetch buy order: %s", e)
                return
            st = o.get("status")
            if st == "canceled":
                log.warning("Buy canceled externally; clearing leg")
                s.update(buy_order_id=None, buy_price=None, buy_amount=None)
                self._save_state()
                return
            if st != "closed":
                return
            filled = o.get("filled") or s["buy_amount"]
            avg = o.get("average") or s["buy_price"]

        cost = filled * avg
        s["inv_amount"] += filled
        s["inv_cost"] += cost
        s["buys_filled"] += 1
        self.logger.record(strategy="grid", symbol=sym, side="buy", order_type="limit",
                           order_id=s["buy_order_id"], amount=filled, price=avg, cost=cost,
                           fee=0, status="filled")
        log.info("BUY FILLED: %s @ %.6f x %.8f (cost %.4f USDT, inv now %.8f %s)",
                 sym, avg, filled, cost, s["inv_amount"], self.base)
        s.update(buy_order_id=None, buy_price=None, buy_amount=None)
        self._save_state()

    def _place_buy(self) -> None:
        sym, mid = self.cfg.symbol, self._mid()
        price = float(self.ex.price_to_precision(sym, mid * (1 - self.cfg.buy_offset_pct / 100)))
        amount = float(self.ex.amount_to_precision(sym, self.cfg.order_usdt / price))
        if not self.dry:
            usdt = self.ex.get_usdt_balance()
            if usdt < self.cfg.order_usdt:
                log.warning("Buy leg idle: USDT %.2f < %.2f", usdt, self.cfg.order_usdt)
                return
            oid = self.ex.create_limit_buy(sym, amount, price)["id"]
        else:
            oid = "paper-buy"
        self.state.update(buy_order_id=oid, buy_price=price, buy_amount=amount)
        self._save_state()
        log.info("Buy placed: %s @ %.6f x %.8f (mid %.6f, id=%s)", sym, price, amount, mid, oid)

    # ----- sell leg -----
    def _handle_sell(self) -> None:
        s, sym = self.state, self.cfg.symbol
        if s["sell_order_id"] is None:
            self._place_sell()
            return
        if self.dry:
            if self._mid() < s["sell_price"]:
                return
            filled, avg = s["sell_amount"], s["sell_price"]
        else:
            try:
                o = self.ex.fetch_order(s["sell_order_id"], sym)
            except Exception as e:
                log.warning("Could not fetch sell order: %s", e)
                return
            st = o.get("status")
            if st == "canceled":
                log.warning("Sell canceled externally; clearing leg")
                s.update(sell_order_id=None, sell_price=None, sell_amount=None)
                self._save_state()
                return
            if st != "closed":
                return
            filled = o.get("filled") or s["sell_amount"]
            avg = o.get("average") or s["sell_price"]

        proceeds = filled * avg
        avg_cost = (s["inv_cost"] / s["inv_amount"]) if s["inv_amount"] > 1e-12 else avg
        profit = proceeds - filled * avg_cost
        s["inv_amount"] = max(0.0, s["inv_amount"] - filled)
        s["inv_cost"] = max(0.0, s["inv_cost"] - filled * avg_cost)
        s["sells_filled"] += 1
        s["realized_profit"] += profit
        self.logger.record(strategy="grid", symbol=sym, side="sell", order_type="limit",
                           order_id=s["sell_order_id"], amount=filled, price=avg, proceeds=proceeds,
                           profit=profit, fee=0, status="filled", cycle=s["sells_filled"])
        self.notifier.send_grid_cycle(sym, profit, s["realized_profit"], s["sells_filled"])
        log.info("SELL FILLED: %s @ %.6f x %.8f → profit %+.4f USDT (total %+.4f, inv now %.8f %s)",
                 sym, avg, filled, profit, s["realized_profit"], s["inv_amount"], self.base)
        s.update(sell_order_id=None, sell_price=None, sell_amount=None)
        self._save_state()

    def _place_sell(self) -> None:
        sym, mid = self.cfg.symbol, self._mid()
        price = float(self.ex.price_to_precision(sym, mid * (1 + self.cfg.profit_pct / 100)))
        amount = float(self.ex.amount_to_precision(sym, self.cfg.order_usdt / price))
        if not self.dry:
            held = self.ex.get_balance(self.base)
            if held < amount:
                log.warning("Sell leg idle: %s %.8f < %.8f needed", self.base, held, amount)
                return
            oid = self.ex.create_limit_sell(sym, amount, price)["id"]
        else:
            if self.state["inv_amount"] < amount:
                log.warning("Sell leg idle (paper): inv %.8f < %.8f", self.state["inv_amount"], amount)
                return
            oid = "paper-sell"
        self.state.update(sell_order_id=oid, sell_price=price, sell_amount=amount)
        self._save_state()
        log.info("Sell placed: %s @ %.6f x %.8f (mid %.6f, id=%s)", sym, price, amount, mid, oid)
