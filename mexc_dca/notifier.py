from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import TelegramConfig

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, config: TelegramConfig):
        self.config = config
        self._bot = None
        if config.enabled:
            try:
                from telegram import Bot
                self._bot = Bot(token=config.bot_token)
            except Exception as e:
                log.warning("Failed to init Telegram bot: %s", e)

    def _send(self, text: str) -> None:
        if not self._bot:
            log.debug("Telegram disabled, skipping: %s", text[:80])
            return
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                self._bot.send_message(chat_id=self.config.chat_id, text=text, parse_mode="HTML")
            )
            loop.close()
        except Exception as e:
            log.error("Telegram send failed: %s", e)

    def send_order_placed(self, entry: dict[str, Any]) -> None:
        text = (
            f"<b>[Limit] {entry.get('symbol')} Order Placed</b>\n"
            f"Amount: {entry.get('amount', 0):.8f}\n"
            f"Price: {entry.get('price', 0):.8f}\n"
            f"Cost: {entry.get('cost', 0):.4f} USDT"
        )
        self._send(text)

    def send_withdrawal(self, symbol: str, amount: float, address: str, txid: str) -> None:
        text = (
            f"<b>Withdrawal: {symbol}</b>\n"
            f"Amount: {amount:.8f}\n"
            f"To: <code>{address[:12]}...{address[-6:]}</code>\n"
            f"TxID: <code>{txid}</code>"
        )
        self._send(text)

    def send_error(self, message: str) -> None:
        text = f"<b>Error</b>\n<pre>{message[:500]}</pre>"
        self._send(text)

    def send_weekly_summary(self, trades: list[dict]) -> None:
        if not trades:
            self._send("<b>Weekly DCA Summary</b>\nNo trades this week.")
            return

        total_cost = sum(t.get("cost", 0) for t in trades if t.get("side") == "buy")
        symbols = {}
        for t in trades:
            if t.get("side") != "buy":
                continue
            s = t.get("symbol", "?")
            if s not in symbols:
                symbols[s] = {"cost": 0, "count": 0}
            symbols[s]["cost"] += t.get("cost", 0)
            symbols[s]["count"] += 1

        lines = [f"<b>Weekly DCA Summary</b>", f"Total spent: {total_cost:.2f} USDT", ""]
        for s, info in symbols.items():
            lines.append(f"  {s}: {info['cost']:.2f} USDT ({info['count']} buys)")

        self._send("\n".join(lines))
