# MetaMask Connect + Link (Fly.io)

This repo deploys a FastAPI app to Fly.io with Telegram bot integration:

- Web UI with "Connect MetaMask"
- Optional "Link wallet" flow using a server-issued nonce + signature verification
- Stores user_key -> wallet_address in SQLite (ephemeral storage - no volume required)
- **Telegram bot** with commands: `/start`, `/connect`, `/wallet`, `/help`

## Quick Start - Deploy to Fly.io

### 1. Get Telegram Bot Token

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow instructions
3. Copy your bot token (looks like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Deploy to Fly.io

```bash
# Clone the repo (if you haven't already)
git clone https://github.com/Turrn3r/turnertelegram.git
cd turnertelegram

# Login to Fly.io (if not already logged in)
flyctl auth login

# Create the app (if not already created)
flyctl apps create turnertelegram

# Set required secrets
flyctl secrets set TELEGRAM_BOT_TOKEN=your_bot_token_here -a turnertelegram
flyctl secrets set PUBLIC_BASE_URL=https://turnertelegram.fly.dev -a turnertelegram

# Deploy (no volume needed!)
flyctl deploy -a turnertelegram
```

### 3. Verify Deployment

```bash
# Check logs for bot startup
flyctl logs -a turnertelegram | grep -i telegram

# You should see:
# ✅ Telegram bot started in polling mode
# OR
# ✅ Telegram webhook set to https://...
```

### 4. Test Your Bot

1. Open Telegram and search for your bot (the username you gave it)
2. Send `/start` - you should get a welcome message
3. Send `/connect` - you should get a link to connect MetaMask

## Important Notes

### Database Storage

The database is stored in `/tmp/app.db` (ephemeral storage):
- ✅ **No volume creation needed** - works immediately
- ⚠️ Data resets when the app restarts or redeploys
- For persistent storage, you can add a volume later

### Bot Not Responding?

1. **Check secrets are set:**
   ```bash
   flyctl secrets list -a turnertelegram
   ```
   You should see `TELEGRAM_BOT_TOKEN` and `PUBLIC_BASE_URL`

2. **Check logs:**
   ```bash
   flyctl logs -a turnertelegram
   ```
   Look for:
   - `✅ Telegram bot started in polling mode` (success)
   - `⚠️ TELEGRAM_BOT_TOKEN not set` (missing token)
   - `❌ Failed to start Telegram bot` (error - check the error message)

3. **Restart the app:**
   ```bash
   flyctl apps restart turnertelegram
   ```

### Webhook Mode (Optional - Recommended for Production)

For better performance and reliability, use webhook mode:

```bash
flyctl secrets set TELEGRAM_WEBHOOK_URL=https://turnertelegram.fly.dev/telegram-webhook -a turnertelegram
flyctl secrets set TELEGRAM_WEBHOOK_SECRET=your_random_secret_here -a turnertelegram
flyctl deploy -a turnertelegram
```

## Local Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables (optional for local testing)
export TELEGRAM_BOT_TOKEN=your_token
export PUBLIC_BASE_URL=http://localhost:8080
export DB_PATH=./local.db

# Run the app
uvicorn app.main:app --reload --port 8080
```

## Telegram Bot Commands

- `/start` - Start the bot and get your connection link
- `/connect` - Get link to connect MetaMask wallet
- `/wallet` - View your linked wallet address
- `/help` - Show help message

## How It Works

1. User sends `/start` or `/connect` to the Telegram bot
2. Bot responds with a link containing their Telegram User ID
3. User opens the link in a browser
4. User connects MetaMask and clicks "Link wallet"
5. User signs a message with MetaMask
6. Server verifies the signature and stores the link
7. Bot sends confirmation message to user

## Bot Modes

- **Polling mode** (default): Bot polls Telegram for updates. Works for development and small bots.
- **Webhook mode** (production): Telegram sends updates to your server. More efficient and scalable.

## Files Structure

```
turnertelegram/
├── app/
│   ├── __init__.py      # Package init
│   ├── main.py          # FastAPI app + Telegram bot
│   └── db.py            # SQLite database functions
├── static/
│   ├── index.html       # MetaMask connection UI
│   ├── app.js           # Frontend JavaScript
│   └── style.css        # Styling
├── Dockerfile           # Container definition
├── fly.toml            # Fly.io configuration (no volume required)
├── requirements.txt    # Python dependencies
└── readme.md           # This file
```

## Deployment Checklist

Before deploying, make sure you have:

- [ ] Created the Fly.io app: `flyctl apps create turnertelegram`
- [ ] Set TELEGRAM_BOT_TOKEN secret
- [ ] Set PUBLIC_BASE_URL secret
- [ ] Deployed: `flyctl deploy -a turnertelegram`
- [ ] Verified bot responds: Send `/start` to your bot

**Note:** No volume creation needed! The app uses ephemeral storage in `/tmp`.
