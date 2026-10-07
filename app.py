"""Render's Telegram webhook, sharing saved lessons with GitHub Actions."""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from important_info.API_loader import env
from Custom_modules.bot_commands import dispatch_text, dispatch_voice
from Custom_modules import learning_store as store

WEBHOOK_SECRET = env("WEBHOOK_SECRET")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application):
    # Fail startup clearly if cloud storage is missing or cannot be reached.
    await asyncio.to_thread(store.initialize)
    yield


app = FastAPI(lifespan=lifespan)


async def process_update(update_id, handler, *arguments):
    try:
        await handler(*arguments)
        if update_id is not None:
            await asyncio.to_thread(store.finish_update, update_id)
    except Exception as error:
        logger.warning("Telegram processing failed (%s)", type(error).__name__)
        if update_id is not None:
            try:
                await asyncio.to_thread(store.finish_update, update_id, False)
            except store.StoreUnavailable:
                logger.warning("Could not record Telegram processing failure")


@app.get("/")
async def health():
    return {"ok": True, "storage": store.storage_backend()}


@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if secret != WEBHOOK_SECRET:
        return {"ok": True}
    try:
        update = await request.json()
        if not isinstance(update, dict):
            return {"ok": True}
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = message.get("text")
        handler = None
        if isinstance(chat_id, int) and isinstance(text, str) and text.strip():
            handler, arguments = dispatch_text, (chat_id, text, chat.get("type") == "private")
        elif isinstance(chat_id, int) and isinstance(message.get("voice"), dict):
            voice = message["voice"]
            if isinstance(voice.get("file_id"), str) and isinstance(voice.get("duration"), int):
                handler, arguments = dispatch_voice, (chat_id, voice["file_id"], voice["duration"], voice.get("file_size"))
        if handler:
            update_id = update.get("update_id")
            update_id = update_id if isinstance(update_id, int) else None
            if update_id is not None:
                try:
                    if not await asyncio.to_thread(store.claim_update, update_id):
                        return {"ok": True}
                except store.StoreUnavailable:
                    # Let Telegram retry when storage failed before acceptance.
                    return JSONResponse({"ok": False}, status_code=503)
            background_tasks.add_task(process_update, update_id, handler, *arguments)
    except (ValueError, AttributeError, TypeError):
        pass
    return {"ok": True}
