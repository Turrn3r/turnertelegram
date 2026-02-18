# MetaMask Connect + Link (Fly.io)

This repo deploys a FastAPI app to Fly.io with Telegram bot integration:

- Web UI with "Connect MetaMask"
- Optional "Link wallet" flow using a server-issued nonce + signature verification
- Stores user_key -> wallet_address in SQLite on a Fly volume
- **Telegram bot** with commands: `/start`, `/connect`, `/wallet`, `/help`

## Setup

1. Get a Telegram bot token from [@BotFather](https://t.me/BotFather)
2. Set Fly.io secrets:
   ```bash
   flyctl secrets set TELEGRAM_BOT_TOKEN=your_bot_token
   flyctl secrets set PUBLIC_BASE_URL=https://turnertelegram.fly.dev
   # Optional: For webhook mode (recommended for production)
   flyctl secrets set TELEGRAM_WEBHOOK_URL=https://turnertelegram.fly.dev/telegram-webhook
   flyctl secrets set TELEGRAM_WEBHOOK_SECRET=your_secret_token
   ```
3. Deploy:
   ```bash
   flyctl deploy
   ```

## Local run

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=your_token  # Optional for local testing
uvicorn app.main:app --reload --port 8080
```

## Telegram Bot Commands

- `/start` - Start the bot and get your connection link
- `/connect` - Get link to connect MetaMask wallet
- `/wallet` - View your linked wallet address
- `/help` - Show help message

## Bot Modes

- **Polling mode** (default): Bot polls Telegram for updates. Works for development.
- **Webhook mode** (production): Set `TELEGRAM_WEBHOOK_URL` and `TELEGRAM_WEBHOOK_SECRET` for better performance and reliability.
