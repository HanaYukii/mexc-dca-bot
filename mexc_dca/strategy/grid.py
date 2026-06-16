"""Paired-flip grid.

Each buy that fills becomes a "lot" with its own take-profit sell at
buy_price*(1+profit%). Multiple lots run concurrently (up to max_lots), spaced at
least spacing% apart, so a slide lays a ladder of lots and every lot exits at its
own +profit%% on the bounce -- no stranded lone sell, no clustered buys.

  * one working entry BUY at a time, below market (or below the lowest open lot)
  * on buy fill  -> open a lot, place its paired SELL at +profit%%
  * on sell fill -> book exact profit (sell-buy)*amount, close the lot, free a slot
  * stops buying once max_lots are open (bounds capital in a downtrend)

Only flips lots it opens; pre-existing holdings are left untouched. Profit is
exact per lot (no average-cost needed). State persists to JSON and is reconciled
against the exchange on startup. Dry-run simulates fills against the live ticker.
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
            "lots": [],  # each: {buy_price, amount, sell_order_id, sell_price}
            "buy_order_id": None, "buy_price": None, "buy_amount": None,
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

    # ----- main loop -----
    def run(self) -> None:
        c = self.cfg
        mode = "PAPER (dry-run)" if self.dry else "LIVE"
        log.info(
            "=== Grid paired-flip [%s]: %s, %.2f USDT/lot, entry -%.2f%%, profit +%.2f%%, "
            "spacing %.2f%%, max %d lots, poll %ds ===",
            mode, c.symbol, c.order_usdt, c.buy_offset_pct, c.profit_pct,
            c.spacing_pct, c.max_lots, c.poll_interval_sec,
        )
        log.info("Resuming: %d open lots, buys=%d, sells=%d, realized=%+.4f USDT",
                 len(self.state["lots"]), self.state["buys_filled"],
                 self.state["sells_filled"], self.state["realized_profit"])
        while True:
            try:
                self._manage_buy()
                self._manage_lots()
            except Exception as e:
                log.error("Grid tick error: %s", e)
                self.notifier.send_error(f"Grid error: {e}")
            time.sleep(c.poll_interval_sec)

    # ----- entry buy -----
    def _manage_buy(self) -> None:
        s = self.state
        if s["buy_order_id"] is not None:
            if self._check_buy():            # still resting on the book
                self._recenter_buy_if_stale()
            return
        if len(s["lots"]) >= self.cfg.max_lots:
            return  # at capacity — stop accumulating
        self._place_buy()

    def _recenter_buy_if_stale(self) -> None:
        """If price ran up and left the resting entry buy more than a step below
        where it should sit, cancel and re-place it near the market so the grid
        keeps engaging (mirror of not letting a sell strand). Only moves the buy
        UP toward price; in a dip the buy is already near/above mid and fills."""
        s, sym = self.state, self.cfg.symbol
        mid = self._mid()
        ideal = mid * (1 - self.cfg.buy_offset_pct / 100)
        if s["lots"]:
            lowest = min(l["buy_price"] for l in s["lots"])
            ideal = min(ideal, lowest * (1 - self.cfg.spacing_pct / 100))
        if s["buy_price"] >= ideal * (1 - self.cfg.spacing_pct / 100):
            return  # within one step of ideal — leave it resting
        log.info("Re-centering entry buy: %.6f stale vs ideal %.6f (mid %.6f)",
                 s["buy_price"], ideal, mid)
        if not self.dry:
            try:
                self.ex.cancel_order(s["buy_order_id"], sym)
            except Exception as e:
                log.warning("Re-center cancel failed: %s", e)
                return
        s.update(buy_order_id=None, buy_price=None, buy_amount=None)
        self._save_state()
        self._place_buy()

    def _place_buy(self) -> None:
        s, sym = self.state, self.cfg.symbol
        mid = self._mid()
        entry = mid * (1 - self.cfg.buy_offset_pct / 100)
        if s["lots"]:
            lowest = min(l["buy_price"] for l in s["lots"])
            entry = min(entry, lowest * (1 - self.cfg.spacing_pct / 100))
        price = float(self.ex.price_to_precision(sym, entry))
        amount = float(self.ex.amount_to_precision(sym, self.cfg.order_usdt / price))
        if not self.dry:
            usdt = self.ex.get_usdt_balance()
            if usdt < self.cfg.order_usdt:
                log.warning("Buy idle: USDT %.2f < %.2f", usdt, self.cfg.order_usdt)
                return
            oid = self.ex.create_limit_buy(sym, amount, price)["id"]
        else:
            oid = f"paper-buy-{s['buys_filled']}"
        s.update(buy_order_id=oid, buy_price=price, buy_amount=amount)
        self._save_state()
        log.info("Buy placed: %s @ %.6f x %.8f (mid %.6f, lots=%d, id=%s)",
                 sym, price, amount, mid, len(s["lots"]), oid)

    def _check_buy(self) -> bool:
        """Return True if the buy is still resting (open), False once it has
        filled (opened a lot) or was cancelled (leg cleared)."""
        s, sym = self.state, self.cfg.symbol
        if self.dry:
            if self._mid() > s["buy_price"]:
                return True
            filled, avg = s["buy_amount"], s["buy_price"]
        else:
            try:
                o = self.ex.fetch_order(s["buy_order_id"], sym)
            except Exception as e:
                log.warning("Could not fetch buy order: %s", e)
                return True
            st = o.get("status")
            if st == "canceled":
                log.warning("Buy canceled externally; clearing entry")
                s.update(buy_order_id=None, buy_price=None, buy_amount=None)
                self._save_state()
                return False
            if st != "closed":
                return True
            filled = o.get("filled") or s["buy_amount"]
            avg = o.get("average") or s["buy_price"]

        s["buys_filled"] += 1
        s["lots"].append({"buy_price": avg, "amount": filled,
                          "sell_order_id": None, "sell_price": None})
        self.logger.record(strategy="grid", symbol=sym, side="buy", order_type="limit",
                           order_id=s["buy_order_id"], amount=filled, price=avg,
                           cost=filled * avg, fee=0, status="filled")
        log.info("BUY FILLED: %s @ %.6f x %.8f -> opened lot (%d now open)",
                 sym, avg, filled, len(s["lots"]))
        s.update(buy_order_id=None, buy_price=None, buy_amount=None)
        self._save_state()
        return False

    # ----- lots / paired sells -----
    def _manage_lots(self) -> None:
        s = self.state
        kept = []
        changed = False
        for lot in s["lots"]:
            if self._handle_lot(lot):
                kept.append(lot)
            else:
                changed = True
        if changed:
            s["lots"] = kept
        self._save_state()

    def _handle_lot(self, lot: dict) -> bool:
        """Return True to keep the lot open, False to close it (sell filled)."""
        s, sym = self.state, self.cfg.symbol
        # place the paired sell if it isn't on the book yet
        if lot["sell_order_id"] is None:
            price = float(self.ex.price_to_precision(
                sym, lot["buy_price"] * (1 + self.cfg.profit_pct / 100)))
            amount = float(self.ex.amount_to_precision(sym, lot["amount"]))
            if not self.dry:
                held = self.ex.get_balance(self.base)
                if held < amount:
                    log.warning("Sell idle: %s %.8f < %.8f for lot @ %.6f",
                                self.base, held, amount, lot["buy_price"])
                    return True
                oid = self.ex.create_limit_sell(sym, amount, price)["id"]
            else:
                oid = f"paper-sell-{s['sells_filled']}-{lot['buy_price']}"
            lot["sell_order_id"] = oid
            lot["sell_price"] = price
            self._save_state()
            log.info("Sell placed: %s @ %.6f x %.8f (lot buy %.6f, id=%s)",
                     sym, price, amount, lot["buy_price"], oid)
            return True
        # otherwise check whether it filled
        if self.dry:
            if self._mid() < lot["sell_price"]:
                return True
            filled, avg = lot["amount"], lot["sell_price"]
        else:
            try:
                o = self.ex.fetch_order(lot["sell_order_id"], sym)
            except Exception as e:
                log.warning("Could not fetch sell order: %s", e)
                return True
            st = o.get("status")
            if st == "canceled":
                log.warning("Sell canceled externally; will re-place lot @ %.6f", lot["buy_price"])
                lot["sell_order_id"] = None
                lot["sell_price"] = None
                self._save_state()
                return True
            if st != "closed":
                return True
            filled = o.get("filled") or lot["amount"]
            avg = o.get("average") or lot["sell_price"]

        profit = (avg - lot["buy_price"]) * filled
        s["sells_filled"] += 1
        s["realized_profit"] += profit
        self.logger.record(strategy="grid", symbol=sym, side="sell", order_type="limit",
                           order_id=lot["sell_order_id"], amount=filled, price=avg,
                           proceeds=filled * avg, profit=profit, fee=0, status="filled",
                           cycle=s["sells_filled"])
        self.notifier.send_grid_cycle(sym, profit, s["realized_profit"], s["sells_filled"])
        log.info("SELL FILLED: %s @ %.6f x %.8f -> profit %+.4f USDT (total %+.4f, %d lots left)",
                 sym, avg, filled, profit, s["realized_profit"], len(s["lots"]) - 1)
        return False
