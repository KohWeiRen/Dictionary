from typing import Optional,Final
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

import asyncio, os
import logging
from io import BytesIO
from important_info.API_loader import env

logger = logging.getLogger(__name__)

BOT_USERNAME : Final = "@Recapoman_bot" 
OWNER_ID : Final = int(os.getenv("BOT_OWNER_ID") or "0")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat:
        await context.bot.send_message(chat_id=chat.id, text="Hello! I am your Dictionary Bot.")
    

async def shutdown_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    
    if not user or user.id != OWNER_ID:
        if msg:
            await msg.reply_text("You are not authorized to shut down the bot.")
        return
    if msg:
        await msg.reply_text("Shutting down the bot...")
        
    context.application.stop()

def handle_response(text : str) -> str:
    text = text.lower()
    if "hello" in text or "hi" in text:
        return "Hello! How can I assist you today?"
    if "help" in text:
        return "You can use commands like /start, /help, and /info to interact with me."
    return "I'm sorry, I didn't understand that. Type /help for assistance."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat  = update.effective_chat
    
    if not msg or not chat or not msg.text:
        return

    message_type = chat.type  # "private", "group", "supergroup", or "channel"
    text = msg.text
    
    print(f"User ({chat.id}) in {message_type} sent: {text}")
    
    if message_type in ("group", "supergroup"):
        if BOT_USERNAME in text:
            new_text : str = text.replace(BOT_USERNAME, "").strip()
            response : str = handle_response(new_text)
        else:
            return
    else:
        response : str = handle_response(text)
    
    print("Bot response:", response)
    await update.message.reply_text(response)
    
async def error_handler(update: Optional[Update], context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"Update {update} caused error: {context.error}")
    
def build_bot() -> ApplicationBuilder:
    print("starting bot...")
    app = ApplicationBuilder().token(env("TELEGRAM_API_KEY")).build()
    
    # commands
    app.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE | filters.ChatType.GROUPS | filters.ChatType.CHANNEL))
    app.add_handler(CommandHandler("shutdown", shutdown_bot, filters=filters.User(user_id=OWNER_ID)))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None))

    app.add_error_handler(error_handler)
    
    return app

def send_text(text: str, chat: str | None = None) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # no running loop -> OK to use asyncio.run
        asyncio.run(_send_async(text, chat))
    else:
        # already in an event loop (FastAPI/Uvicorn) -> create task
        loop.create_task(_send_async(text, chat))


async def _send_async(text: str, chat: str | None):
    token = env("TELEGRAM_API_KEY")
    chat = chat or env("BOT_OWNER_ID")  # fallback
    async with Bot(token) as bot:
        # If chat looks like "@something", resolve to numeric ID first
        if isinstance(chat, str) and chat.startswith("@"):
            chat_id = (await bot.get_chat(chat)).id
        else:
            chat_id = int(chat)
        for start in range(0, len(text), 4096):
            await bot.send_message(chat_id=chat_id, text=text[start:start + 4096], disable_web_page_preview=True)


async def send_spanish_audio(text: str, chat: int | str | None) -> None:
    """Upload normal and slow MP3s as Telegram players; leave text usable on failure."""
    from Custom_modules.spanish_audio import synthesize_spanish
    token = env("TELEGRAM_API_KEY")
    chat = chat or env("BOT_OWNER_ID")
    async with Bot(token) as bot:
        chat_id = (await bot.get_chat(chat)).id if isinstance(chat, str) and chat.startswith("@") else int(chat)
        for slow, label in ((False, "normal speed"), (True, "slow practice")):
            try:
                content = await asyncio.to_thread(synthesize_spanish, text, slow)
                stream = BytesIO(content)
                stream.name = "spanish-slow.mp3" if slow else "spanish-normal.mp3"
                await bot.send_audio(chat_id=chat_id, audio=stream,
                                     title=f"Spanish · {label}", performer="Dictionary Bot",
                                     caption=f"🇪🇸 {label}\n{text}", read_timeout=30, write_timeout=30)
            except Exception as exc:
                logger.warning("Spanish audio unavailable (%s)", type(exc).__name__)
                await bot.send_message(chat_id=chat_id,
                                       text=f"Spanish audio ({label}) is temporarily unavailable. Retry with /listen, or /lesson YYYY-MM-DD for a saved lesson.")
                break
    
def run_bot(app) -> None:
    print("running bot...")
    app.run_polling()
    print("bot stopped.")
