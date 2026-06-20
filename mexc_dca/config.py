from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class WithdrawConfig:
    enabled: bool = False
    address: str = ""
    network: str = ""
    threshold_usdt: float = 0.0


@dataclass
class CoinConfig:
    symbol: str
    amount_usdt: float
    schedule: str  # cron expression, e.g. "0 9 * * *"
    limit_offset_pct: float = 0.1
    timeout_minutes: int = 5
    withdraw: WithdrawConfig = field(default_factory=WithdrawConfig)


@dataclass
class GridConfig:
    symbol: str = "ETH/USDT"
    order_usdt: float = 50.0
    buy_offset_pct: float = 0.3      # first entry buy this % below market (maker)
    profit_pct: float = 1.0          # each lot sells this % above its buy (maker)
    spacing_pct: float = 1.0         # min gap between consecutive buy lots
    max_lots: int = 6                # max concurrent open lots (caps deployed capital)
    poll_interval_sec: int = 30
    log_file: str = "grid_trades.jsonl"
    state_file: str = "grid_state.json"


@dataclass
class RangeFadeConfig:
    symbol: str = "BTC/USDT"
    order_usdt: float = 25.0
    log_file: str = "rangefade_trades.jsonl"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    weekly_summary_day: int = 1  # 0=Mon ... 6=Sun (APScheduler day_of_week)


@dataclass
class AppConfig:
    api_key: str
    api_secret: str
    coins: list[CoinConfig]
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    grid: GridConfig | None = None
    rangefade: RangeFadeConfig | None = None
    dry_run: bool = False
    log_file: str = "trades.jsonl"


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    load_dotenv(env_path)

    api_key = os.getenv("MEXC_API_KEY", "")
    api_secret = os.getenv("MEXC_API_SECRET", "")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    coins: list[CoinConfig] = []
    for c in raw.get("coins", []):
        wd = c.get("withdraw", {})
        coins.append(
            CoinConfig(
                symbol=c["symbol"],
                amount_usdt=float(c["amount_usdt"]),
                schedule=c["schedule"],
                limit_offset_pct=float(c.get("limit_offset_pct", 0.1)),
                timeout_minutes=int(c.get("timeout_minutes", 5)),
                withdraw=WithdrawConfig(
                    enabled=wd.get("enabled", False),
                    address=wd.get("address", ""),
                    network=wd.get("network", ""),
                    threshold_usdt=float(wd.get("threshold_usdt", 0)),
                ),
            )
        )

    grid = None
    g = raw.get("grid")
    if g:
        grid = GridConfig(
            symbol=g.get("symbol", "ETH/USDT"),
            order_usdt=float(g.get("order_usdt", 50)),
            buy_offset_pct=float(g.get("buy_offset_pct", 0.3)),
            profit_pct=float(g.get("profit_pct", 1.0)),
            spacing_pct=float(g.get("spacing_pct", 1.0)),
            max_lots=int(g.get("max_lots", 6)),
            poll_interval_sec=int(g.get("poll_interval_sec", 30)),
            log_file=g.get("log_file", "grid_trades.jsonl"),
            state_file=g.get("state_file", "grid_state.json"),
        )

    rangefade = None
    rf = raw.get("rangefade")
    if rf:
        rangefade = RangeFadeConfig(
            symbol=rf.get("symbol", "BTC/USDT"),
            order_usdt=float(rf.get("order_usdt", 25)),
            log_file=rf.get("log_file", "rangefade_trades.jsonl"),
        )

    tg_raw = raw.get("telegram", {})
    telegram = TelegramConfig(
        enabled=bool(tg_token and tg_raw.get("chat_id")),
        bot_token=tg_token,
        chat_id=str(tg_raw.get("chat_id", "")),
        weekly_summary_day=int(tg_raw.get("weekly_summary_day", 1)),
    )

    return AppConfig(
        api_key=api_key,
        api_secret=api_secret,
        coins=coins,
        telegram=telegram,
        grid=grid,
        rangefade=rangefade,
        log_file=raw.get("log_file", "trades.jsonl"),
    )
