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
