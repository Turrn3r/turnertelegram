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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

bot_app = None

def get_connect_keyboard(user_id: str):
    """Build inline keyboard with Connect MetaMask button."""
    web_url = os.getenv("PUBLIC_BASE_URL", "https://turnertelegram.fly.dev")
    link_url = f"{web_url}?user_key={user_id}"
    keyboard = [
        [InlineKeyboardButton("🔗 Connect MetaMask Wallet", url=link_url)],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_main_menu_keyboard():
    """Persistent reply keyboard menu."""
    keyboard = [
        [KeyboardButton("🔗 Connect Wallet"), KeyboardButton("💼 My Wallet")],
        [KeyboardButton("❓ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start - show welcome and menu with Connect button."""
    user_id = str(update.effective_user.id)
    web_url = os.getenv("PUBLIC_BASE_URL", "https://turnertelegram.fly.dev")
    link_url = f"{web_url}?user_key={user_id}"

    text = (
        "👋 Welcome to the MetaMask Telegram Bot!\n\n"
        "Connect your MetaMask wallet to link it to this chat.\n\n"
        "👇 Tap the button below to open the Connect page, then:\n"
        "1. Connect MetaMask in your browser\n"
        "2. Click \"Link wallet\" and sign the message\n"
        "3. Come back here and use \"My Wallet\" to confirm\n\n"
        "Your User ID: " + user_id
    )

    await update.message.reply_text(
        text,
        reply_markup=get_connect_keyboard(user_id),
    )
    await update.message.reply_text(
        "Choose an option:",
        reply_markup=get_main_menu_keyboard(),
    )

async def connect_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /connect - send Connect MetaMask button."""
    user_id = str(update.effective_user.id)

    text = (
        "🔗 Connect your MetaMask wallet\n\n"
        "Tap the button below to open the Connect page in your browser.\n"
        "Then connect MetaMask and click \"Link wallet\" to sign."
    )

    await update.message.reply_text(
        text,
        reply_markup=get_connect_keyboard(user_id),
    )

async def wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /wallet - show linked wallet or prompt to connect."""
    user_id = str(update.effective_user.id)
    link_data = get_link(user_id)

    if not link_data:
        text = (
            "❌ No wallet linked yet.\n\n"
            "Use the \"Connect Wallet\" button or /connect to link your MetaMask wallet."
        )
        await update.message.reply_text(
            text,
            reply_markup=get_connect_keyboard(user_id),
        )
    else:
        address, linked_at = link_data
        text = (
            "💼 Your linked wallet\n\n"
            "Address: " + address + "\n"
            "Linked (timestamp): " + str(linked_at)
        )
        await update.message.reply_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help."""
    text = (
        "📖 Menu\n\n"
        "🔗 Connect Wallet – Open link to connect MetaMask\n"
        "💼 My Wallet – View your linked wallet address\n"
        "❓ Help – This message\n\n"
        "Commands: /start /connect /wallet /help"
    )
    await update.message.reply_text(text)

async def menu_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle menu button taps (Connect Wallet, My Wallet, Help)."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if "Connect" in text or "connect" in text:
        await connect_command(update, context)
    elif "My Wallet" in text or "wallet" in text.lower():
        await wallet_command(update, context)
    elif "Help" in text or "help" in text:
        await help_command(update, context)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_app

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if bot_token:
        try:
            bot_app = Application.builder().token(bot_token).build()

            bot_app.add_handler(CommandHandler("start", start_command))
            bot_app.add_handler(CommandHandler("connect", connect_command))
            bot_app.add_handler(CommandHandler("wallet", wallet_command))
            bot_app.add_handler(CommandHandler("help", help_command))
            bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_button_handler))

            webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL")
            if webhook_url:
                webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
                await bot_app.initialize()
                await bot_app.start()
                await bot_app.bot.set_webhook(
                    url=webhook_url,
                    secret_token=webhook_secret if webhook_secret else None
                )
                logger.info("✅ Telegram webhook set to %s", webhook_url)
            else:
                await bot_app.initialize()
                await bot_app.start()
                await bot_app.updater.start_polling(drop_pending_updates=True)
                logger.info("✅ Telegram bot started in polling mode")
        except Exception as e:
            logger.error("❌ Failed to start Telegram bot: %s", e, exc_info=True)
            bot_app = None
    else:
        logger.warning("⚠️ TELEGRAM_BOT_TOKEN not set, Telegram bot disabled")

    yield

    if bot_app:
        try:
            if hasattr(bot_app, 'updater') and bot_app.updater and bot_app.updater.running:
                await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
            logger.info("Telegram bot stopped")
        except Exception as e:
            logger.error("Error stopping bot: %s", e)

app = FastAPI(lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")

@app.get("/healthz")
def healthz():
    return {"ok": True, "bot_status": "running" if bot_app else "disabled"}

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    if not bot_app:
        raise HTTPException(500, "Telegram bot not initialized")

    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if webhook_secret:
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret_header != webhook_secret:
            raise HTTPException(403, "Invalid webhook secret")

    try:
        update_data = await request.json()
        update = Update.de_json(update_data, bot_app.bot)
        await bot_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error("Error processing webhook update: %s", e, exc_info=True)
        raise HTTPException(500, "Error processing update")

class NonceReq(BaseModel):
    user_key: str

class LinkReq(BaseModel):
    user_key: str
    address: str
    signature: str

def build_message(user_key: str, nonce: str) -> str:
    return f"Link this wallet to user: {user_key}\nNonce: {nonce}"

@app.post("/api/nonce")
def api_nonce(req: NonceReq):
    return {"ok": True, "nonce": new_nonce(req.user_key)}

@app.post("/api/link")
async def api_link(req: LinkReq):
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

    if bot_app:
        try:
            await bot_app.bot.send_message(
                chat_id=req.user_key,
                text="✅ Wallet linked successfully!\n\nAddress: " + req.address,
            )
        except Exception as e:
            logger.error("Failed to send Telegram notification: %s", e)

    return {"ok": True, "user_key": req.user_key, "address": req.address}

@app.get("/api/link/{user_key}")
def api_get_link(user_key: str):
    row = get_link(user_key)
    if not row:
        return {"ok": True, "linked": False}
    address, linked_at = row
    return {"ok": True, "linked": True, "address": address, "linked_at": linked_at}
