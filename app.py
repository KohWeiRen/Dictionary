# app.py
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

from important_info.API_loader import env
from Custom_modules.telegram_bot import _send_async as send_text_async  # await this
from Custom_modules.dictionary_api_v2 import (
    fetch_mw_english_json,
    fetch_mw_spanish_json,
    fetch_en_example,
    format_mw_entries,
    mw_response_kind,
    mw_examples,
)

WEBHOOK_SECRET = env("WEBHOOK_SECRET")

app = FastAPI()

HELP = (
    "📖 Dictionary Bot\n"
    "\n"
    "/en <word> — English definition (Merriam-Webster) + example\n"
    "/es <palabra> — Spanish → English + example\n"
    "/word — today's daily English + Spanish words\n"
    "/help — show this message\n"
    "\n"
    "Examples:\n"
    "/en prodigious\n"
    "/es biblioteca\n"
    "(/dict <word> also works, same as /en)"
)


@app.get("/")
async def health():
    return {"ok": True}


async def send_chunks(chat_id: int, text: str) -> None:
    """Split long messages so Telegram accepts them (4096-char limit)."""
    if not text:
        return
    limit = 4096
    for i in range(0, len(text), limit):
        await send_text_async(text[i:i + limit], chat_id)


def lookup_english(word: str) -> str:
    """Blocking MW English lookup (+ example fallback). Run off the event loop."""
    try:
        data = fetch_mw_english_json(word)
    except Exception:
        return f"Couldn't reach the dictionary for “{word}”. Try again in a moment."
    example = None
    if mw_response_kind(data) == "entries" and not mw_examples(data, "en"):
        example = fetch_en_example(word)
    return format_mw_entries(
        data, language="en", max_defs=8, max_examples=2, fallback_example=example
    )


def lookup_spanish(word: str) -> str:
    """Blocking MW Spanish lookup. Run off the event loop."""
    try:
        data = fetch_mw_spanish_json(word)
    except Exception:
        return f"Couldn't reach the dictionary for “{word}”. Try again in a moment."
    return format_mw_entries(data, language="es", max_defs=8, max_examples=2)


def daily_words() -> str:
    """Build today's daily English + Spanish message. Run off the event loop."""
    from get_random_word_daily import build_message  # lazy: avoids loading wordfreq at startup
    return build_message()


async def _run_and_send(chat_id: int, fn, *args) -> None:
    """Do the (blocking) dictionary work in a threadpool, then send the reply.

    Run as a background task so the webhook returns 200 immediately — otherwise a
    slow lookup makes Telegram time out and resend the update (duplicate replies).
    Any failure is caught so the user still gets a reply instead of silence.
    """
    try:
        out = await run_in_threadpool(fn, *args)
    except Exception:
        out = "Something went wrong. Please try again in a moment."
    try:
        await send_chunks(chat_id, out)
    except Exception:
        pass  # nothing more we can do if Telegram is unreachable


@app.post("/telegram/{secret}")
async def telegram_webhook(secret: str, request: Request, background_tasks: BackgroundTasks):
    if secret != WEBHOOK_SECRET:
        return {"ok": True}

    # Always answer 200 so a bad/duplicate update never makes Telegram retry.
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}
    if not isinstance(update, dict):
        return {"ok": True}

    message = update.get("message") or update.get("channel_post") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    print("UPDATE TEXT:", text)

    if not chat_id or not text:
        return {"ok": True}

    parts = text.split()
    # normalize "/en@BotName" -> "/en"
    cmd = parts[0].lower().split("@", 1)[0]
    arg = " ".join(parts[1:]).strip()

    try:
        if cmd in ("/start", "/help"):
            await send_chunks(chat_id, HELP)

        elif cmd in ("/en", "/dict"):
            if not arg:
                await send_chunks(chat_id, "Usage: /en <word>")
            else:
                background_tasks.add_task(_run_and_send, chat_id, lookup_english, arg)

        elif cmd == "/es":
            if not arg:
                await send_chunks(chat_id, "Usage: /es <palabra>")
            else:
                background_tasks.add_task(_run_and_send, chat_id, lookup_spanish, arg)

        elif cmd in ("/word", "/daily"):
            background_tasks.add_task(_run_and_send, chat_id, daily_words)

        else:
            await send_chunks(chat_id, HELP)
    except Exception:
        pass  # never surface a 500 to Telegram (it would redeliver the update)

    return {"ok": True}
