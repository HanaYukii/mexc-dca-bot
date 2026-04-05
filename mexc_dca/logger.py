from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class TradeLogger:
    def __init__(self, log_file: str = "trades.jsonl"):
        self.path = Path(log_file)

    def record(self, **fields: Any) -> dict:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        log.info("Trade logged: %s %s @ %s", fields.get("symbol"), fields.get("side"), fields.get("price"))
        return entry

    def read_recent(self, days: int = 7) -> list[dict]:
        if not self.path.exists():
            return []
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        results = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                if ts >= cutoff:
                    results.append(entry)
        return results

    def read_all(self) -> list[dict]:
        """Read all trade records."""
        if not self.path.exists():
            return []
        results = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return results

    def compute_stats(self, trades: list[dict] | None = None) -> dict[str, dict]:
        """Compute per-symbol stats: total amount, total cost, avg price, buy count."""
        if trades is None:
            trades = self.read_all()

        stats: dict[str, dict] = {}
        for t in trades:
            if t.get("side") != "buy":
                continue
            symbol = t.get("symbol", "")
            if not symbol:
                continue

            if symbol not in stats:
                stats[symbol] = {
                    "total_amount": 0.0,
                    "total_cost": 0.0,
                    "buy_count": 0,
                    "first_buy": t.get("timestamp", ""),
                    "last_buy": t.get("timestamp", ""),
                }

            s = stats[symbol]
            s["total_amount"] += t.get("amount", 0.0)
            s["total_cost"] += t.get("cost", 0.0)
            s["buy_count"] += 1
            s["last_buy"] = t.get("timestamp", "")

        # Calculate average price
        for s in stats.values():
            if s["total_amount"] > 0:
                s["avg_price"] = s["total_cost"] / s["total_amount"]
            else:
                s["avg_price"] = 0.0

        return stats
