from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import time
import os
import asyncio
from contextlib import asynccontextmanager

from eth_account import Account
from eth_account.messages import encode_defunct

from .db import new_nonce, get_nonce, clear_nonce, save_link, get_link

# Telegram bot imports
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Global bot application
bot_app = None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user_id = str(update.effective_user.id)
    web_url = os.getenv("PUBLIC_BASE_URL", "https://turnertelegram.fly.dev")
    link_url = f"{web_url}?user_key={user_id}"
    
    await update.message.reply_text(
        f"👋 Welcome to the MetaMask Telegram Bot!\n\n"
        f"Your Telegram User ID: `{user_id}`\n\n"
        f"To connect your MetaMask wallet:\n"
        f"1. Click the link below\n"
        f"2. Connect MetaMask\n"
        f"3. Click 'Link wallet' to sign and link\n\n"
        f"{link_url}\n\n"
        f"Commands:\n"
        f"/connect - Get connection link\n"
        f"/wallet - View your linked wallet\n"
        f"/help - Show help",
        parse_mode='Markdown'
    )

async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /connect command"""
    user_id = str(update.effective_user.id)
    web_url = os.getenv("PUBLIC_BASE_URL", "https://turnertelegram.fly.dev")
    link_url = f"{web_url}?user_key={user_id}"
    
    await update.message.reply_text(
        f"🔗 Connect your MetaMask wallet:\n\n"
        f"1. Open this link: {link_url}\n"
        f"2. Connect MetaMask\n"
        f"3. Click 'Link wallet' to sign\n\n"
        f"Your User ID: `{user_id}`",
        parse_mode='Markdown'
    )

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /wallet command"""
    user_id = str(update.effective_user.id)
    link_data = get_link(user_id)
    
    if not link_data:
        await update.message.reply_text(
            "❌ No wallet linked.\n\n"
            "Use /connect to link your MetaMask wallet.",
            parse_mode='Markdown'
        )
    else:
        address, linked_at = link_data
        await update.message.reply_text(
            f"💼 Your linked wallet:\n\n"
            f"Address: `{address}`\n"
            f"Linked: <t:{linked_at}:R>",
            parse_mode='Markdown'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await update.message.reply_text(
        "📖 Available Commands:\n\n"
        "/start - Start the bot\n"
        "/connect - Get link to connect MetaMask\n"
        "/wallet - View your linked wallet\n"
        "/help - Show this help",
        parse_mode='Markdown'
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    global bot_app
    
    # Startup: Initialize Telegram bot
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if bot_token:
        bot_app = Application.builder().token(bot_token).build()
        
        # Add handlers
        bot_app.add_handler(CommandHandler("start", start_command))
        bot_app.add_handler(CommandHandler("connect", connect_command))
        bot_app.add_handler(CommandHandler("wallet", wallet_command))
        bot_app.add_handler(CommandHandler("help", help_command))
        
        # Start bot (webhook mode if WEBHOOK_URL is set, else polling)
        webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
        if webhook_url:
            webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
            await bot_app.bot.set_webhook(
                url=webhook_url,
                secret_token=webhook_secret if webhook_secret else None
            )
            logger.info(f"Telegram webhook set to {webhook_url}")
        else:
            # Start polling in background
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling()
            logger.info("Telegram bot started in polling mode")
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not set, Telegram bot disabled")
    
    yield
    
    # Shutdown: Stop Telegram bot
    if bot_app:
        if bot_app.updater.running:
            await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        logger.info("Telegram bot stopped")

app = FastAPI(lifespan=lifespan)

# Serve static UI
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")

@app.get("/healthz")
def healthz():
    return {"ok": True}

# Telegram webhook endpoint (if using webhook mode)
@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    """Handle Telegram webhook updates"""
    if not bot_app:
        raise HTTPException(500, "Telegram bot not initialized")
    
    # Verify webhook secret if set
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if webhook_secret:
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret_header != webhook_secret:
            raise HTTPException(403, "Invalid webhook secret")
    
    update_data = await request.json()
    update = Update.de_json(update_data, bot_app.bot)
    await bot_app.process_update(update)
    return {"ok": True}

# ---- Wallet link flow (signature verification) ----

class NonceReq(BaseModel):
    user_key: str  # could be telegram_user_id, email, username, etc.

class LinkReq(BaseModel):
    user_key: str
    address: str
    signature: str

def build_message(user_key: str, nonce: str) -> str:
    # Simple, clear message. You can upgrade this to SIWE later.
    return f"Link this wallet to user: {user_key}\nNonce: {nonce}"

@app.post("/api/nonce")
def api_nonce(req: NonceReq):
    return {"ok": True, "nonce": new_nonce(req.user_key)}

@app.post("/api/link")
def api_link(req: LinkReq):
    row = get_nonce(req.user_key)
    if not row:
        raise HTTPException(400, "missing_nonce")
    nonce, created_at = row
    if int(time.time()) - int(created_at) > 10 * 60:
        raise HTTPException(400, "nonce_expired")

    msg = build_message(req.user_key, nonce)
    recovered = Account.recover_message(encode_defunct(text=msg), signature=req.signature)

    if recovered.lower() != req.address.lower():
        raise HTTPException(400, "bad_signature")

    save_link(req.user_key, req.address)
    clear_nonce(req.user_key)
    
    # Notify user via Telegram if bot is available
    if bot_app:
        try:
            asyncio.create_task(
                bot_app.bot.send_message(
                    chat_id=req.user_key,
                    text=f"✅ Wallet linked successfully!\n\nAddress: `{req.address}`",
                    parse_mode='Markdown'
                )
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
    
    return {"ok": True, "user_key": req.user_key, "address": req.address}

@app.get("/api/link/{user_key}")
def api_get_link(user_key: str):
    row = get_link(user_key)
    if not row:
        return {"ok": True, "linked": False}
    address, linked_at = row
    return {"ok": True, "linked": True, "address": address, "linked_at": linked_at}
