"""Run Telegram commands on a Pi using outbound-only long polling."""
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

from important_info.API_loader import env
from Custom_modules.bot_commands import dispatch_text, dispatch_voice


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_message and update.effective_message.text:
        await dispatch_text(update.effective_chat.id, update.effective_message.text,
                            update.effective_chat.type == "private")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_message and update.effective_message.voice:
        voice = update.effective_message.voice
        await dispatch_voice(update.effective_chat.id, voice.file_id, voice.duration, voice.file_size)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Provider exception bodies and request URLs can contain credentials.
    logging.getLogger(__name__).warning("Telegram update failed (%s)", type(context.error).__name__)


def build_application():
    application = ApplicationBuilder().token(env("TELEGRAM_API_KEY")).build()
    application.add_handler(MessageHandler(filters.TEXT, handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_error_handler(error_handler)
    return application


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    # run_polling removes an existing webhook before receiving updates.
    build_application().run_polling(drop_pending_updates=False, allowed_updates=["message", "channel_post"])
