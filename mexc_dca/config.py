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
        log_file=raw.get("log_file", "trades.jsonl"),
    )
