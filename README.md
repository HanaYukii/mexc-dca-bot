# MEXC Spot DCA Bot

MEXC 現貨定投機器人，使用 ccxt 接 MEXC API，支援多幣種定投、限價單掛單、自動提幣、Telegram 通知。

## Features

- 多幣種定投，每個幣獨立設定金額與頻率（cron 表達式）
- 限價單掛單（低於市價 0.1%-0.3%，享 maker 零手續費），超時自動改市價單
- 累積到門檻後自動提幣到冷錢包
- Telegram 通知：成交、錯誤、每週摘要
- JSON Lines 格式交易日誌
- Dry-run 模式，可安全測試

## Setup

### 1. Install dependencies

```bash
cd mexc-dca-bot
poetry install
```

### 2. Configure

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Edit `config.yaml` to set your coins, amounts, schedules, and withdrawal addresses.

### 3. Get Telegram Chat ID

1. Create a bot via [@BotFather](https://t.me/BotFather)
2. Send a message to your bot
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat_id`
4. Fill in `.env` and `config.yaml`

### 4. Run

```bash
# Normal mode
poetry run mexc-dca

# Dry-run (no real orders)
poetry run mexc-dca --dry-run

# Debug logging
poetry run mexc-dca --dry-run --log-level DEBUG
```

Or run as a module:

```bash
poetry run python -m mexc_dca --dry-run
```

## Config Reference

### config.yaml

| Field | Description |
|---|---|
| `coins[].symbol` | Trading pair, e.g. `BTC/USDT` |
| `coins[].amount_usdt` | USDT amount per buy |
| `coins[].schedule` | Cron expression (min hour day month weekday) |
| `coins[].limit_offset_pct` | Limit price offset below market (%) |
| `coins[].timeout_minutes` | Cancel limit order after N minutes |
| `coins[].withdraw.enabled` | Enable auto-withdrawal |
| `coins[].withdraw.address` | Withdrawal destination |
| `coins[].withdraw.network` | Network (BTC, ETH, TRC20, etc.) |
| `coins[].withdraw.threshold_usdt` | Min accumulated value to trigger withdrawal |
| `telegram.chat_id` | Telegram chat ID for notifications |
| `telegram.weekly_summary_day` | Day of week for summary (0=Mon) |

### .env

| Variable | Description |
|---|---|
| `MEXC_API_KEY` | MEXC API key |
| `MEXC_API_SECRET` | MEXC API secret |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |

## Trade Log

Trades are logged to `trades.jsonl` (one JSON object per line):

```json
{"timestamp":"2025-01-01T09:00:00+00:00","symbol":"BTC/USDT","side":"buy","order_type":"limit","amount":0.00052,"price":96000.0,"cost":50.0,"fee":0,"filled":true}
```

## Architecture

```
mexc_dca/
├── config.py       # Config loading (YAML + .env)
├── exchange.py     # ccxt MEXC wrapper with retry
├── strategy/
│   └── dca.py      # DCA buy logic (limit -> market fallback)
├── notifier.py     # Telegram notifications
├── logger.py       # JSON trade logger
├── scheduler.py    # APScheduler job setup
└── __main__.py     # CLI entry point
```

To add a new strategy, create a new file in `strategy/` and register it in `scheduler.py`.
