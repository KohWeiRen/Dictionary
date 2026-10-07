"""One command handler for both FastAPI webhooks and Pi long polling."""
import asyncio
import datetime
import logging
from telegram import Bot

from important_info.API_loader import env
from Custom_modules import learning_store as store
from Custom_modules.dictionary_api_v2 import fetch_mw_english_json, fetch_mw_spanish_json, fetch_en_example, format_mw_entries, mw_response_kind, mw_examples
from Custom_modules.gemini_client import AIUnavailable
from Custom_modules.spanish_audio import MAX_SPEECH_CHARS
from Custom_modules.spanish_lessons import lesson_for_date, format_lesson, format_answer, practice_reply, practice_voice, format_reply, SpanishLesson
from Custom_modules.telegram_bot import _send_async, send_spanish_audio

logger = logging.getLogger(__name__)
HELP = """📖 Dictionary Bot

/word or /daily — daily English vocabulary + AI Spanish lesson and audio
/lesson [YYYY-MM-DD] — today's lesson or replay a saved date
/answer [YYYY-MM-DD] — reveal the saved lesson's model answer
/practice [topic] — start a Spanish conversation, e.g. ordering coffee
/reply <Spanish> — correct your answer and continue the conversation
/listen [Spanish text] — normal + slow audio (today's dialogue if omitted)
/level [A1|A2|B1|B2] — see or change your Spanish level
/reset — reset the conversation
/en <word> or /dict <word> — English dictionary lookup
/es <palabra> — Spanish dictionary lookup and pronunciation
/help — show commands

In the owner's private chat, you can also send Spanish text to practice.
Send a short voice note to practice speaking and get a transcript + correction.
"""


def lookup_english(word: str) -> str:
    try:
        data = fetch_mw_english_json(word)
        example = fetch_en_example(word) if mw_response_kind(data) == "entries" and not mw_examples(data, "en") else None
        return format_mw_entries(data, language="en", max_defs=8, max_examples=2, fallback_example=example)
    except Exception:
        return f"Couldn't reach the dictionary for “{word}”. Try again in a moment."


def lookup_spanish(word: str) -> str:
    try:
        data = fetch_mw_spanish_json(word)
        result = format_mw_entries(data, language="es", max_defs=8, max_examples=2)
        if mw_response_kind(data) == "entries" and len(word) <= MAX_SPEECH_CHARS:
            result += f"\n/listen {word} — hear normal + slow Spanish"
        return result
    except Exception:
        return f"Couldn't reach the dictionary for “{word}”. Try again in a moment."


def saved_lesson(chat_id: int, day: datetime.date) -> SpanishLesson:
    payload = store.cached_lesson(chat_id, day)
    if not payload:
        raise AIUnavailable("No saved lesson for that date. Get today's lesson with /lesson first.")
    return SpanishLesson.model_validate_json(payload)


def command_date(arg: str) -> datetime.date:
    if not arg:
        return store.local_today()
    try:
        return datetime.date.fromisoformat(arg)
    except ValueError:
        raise AIUnavailable("Use a saved date in YYYY-MM-DD format, or omit the date for today.") from None


async def dispatch_text(chat_id: int, text: str, private: bool = True) -> None:
    text = text.strip()
    if not text:
        return
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().split("@", 1)[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    # A personal bot: keep its limited AI quota for the configured owner chat.
    owner = str(chat_id) == env("BOT_OWNER_ID")
    if cmd in ("/start", "/help"):
        await _send_async(HELP, chat_id)
        return
    if cmd in ("/en", "/dict", "/es"):
        if not arg:
            await _send_async(f"Usage: {cmd} <word>", chat_id)
            return
        fn = lookup_spanish if cmd == "/es" else lookup_english
        await _send_async(await asyncio.to_thread(fn, arg), chat_id)
        return
    if not owner:
        await _send_async("Spanish practice is available in the owner's chat. Use /en or /es for dictionary lookups.", chat_id)
        return
    speech = None
    try:
        if cmd in ("/word", "/daily"):
            from get_random_word_daily import build_daily_bundle
            bundle = await asyncio.to_thread(build_daily_bundle, None, chat_id)
            output, speech = bundle.text, bundle.speech
        elif cmd == "/lesson":
            day = command_date(arg)
            lesson = await asyncio.to_thread(lesson_for_date, chat_id, day)
            output, speech = format_lesson(lesson, day), lesson.audio_text
        elif cmd == "/answer":
            lesson = await asyncio.to_thread(saved_lesson, chat_id, command_date(arg))
            output = format_answer(lesson)
        elif cmd == "/listen":
            if not arg:
                lesson = await asyncio.to_thread(saved_lesson, chat_id, store.local_today())
                arg = lesson.audio_text
            if len(arg) > MAX_SPEECH_CHARS:
                raise AIUnavailable(f"Use /listen with at most {MAX_SPEECH_CHARS} characters of Spanish.")
            await send_spanish_audio(arg, chat_id)
            return
        elif cmd == "/level":
            if arg:
                if arg.upper() not in ("A1", "A2", "B1", "B2"):
                    raise AIUnavailable("Usage: /level A1, A2, B1 or B2. A1 is beginner.")
                await asyncio.to_thread(store.set_level, chat_id, arg.upper())
                output = f"Spanish level set to {arg.upper()}. New practice uses it now; tomorrow's lesson will use it too."
            else:
                output = "Your Spanish level: " + await asyncio.to_thread(store.get_level, chat_id)
        elif cmd == "/reset":
            await asyncio.to_thread(store.reset_conversation, chat_id)
            output = "Conversation reset. Use /practice <topic> to begin again. Your saved lessons remain available."
        elif cmd in ("/practice", "/reply") or (not cmd.startswith("/") and private):
            start = cmd == "/practice"
            if start:
                arg = arg or "an everyday conversation using today's vocabulary"
            elif cmd == "/reply" and not arg:
                raise AIUnavailable("Usage: /reply <your Spanish answer>")
            elif cmd != "/reply":
                arg = text
            reply = await asyncio.to_thread(practice_reply, chat_id, arg, start)
            output, speech = format_reply(reply), reply.audio_text
        else:
            output = HELP
    except AIUnavailable as exc:
        output = str(exc)
    except Exception as exc:
        logger.warning("Bot command failed (%s)", type(exc).__name__)
        output = "Something went wrong. Please try again in a moment."
    await _send_async(output, chat_id)
    if speech:
        await send_spanish_audio(speech, chat_id)


async def dispatch_voice(chat_id: int, file_id: str, duration: int, file_size: int | None = None) -> None:
    if str(chat_id) != env("BOT_OWNER_ID"):
        await _send_async("Spanish voice practice is available in the owner's chat.", chat_id)
        return
    if duration > 60 or (file_size is not None and file_size > 2_000_000):
        await _send_async("Send a voice note up to 60 seconds and under 2 MB.", chat_id)
        return
    try:
        async with Bot(env("TELEGRAM_API_KEY")) as bot:
            voice = await bot.get_file(file_id)
            if voice.file_size is not None and voice.file_size > 2_000_000:
                raise AIUnavailable("Send a voice note under 2 MB.")
            content = bytes(await voice.download_as_bytearray())
        reply = await asyncio.to_thread(practice_voice, chat_id, content)
        await _send_async(format_reply(reply), chat_id)
        await send_spanish_audio(reply.audio_text, chat_id)
    except AIUnavailable as exc:
        await _send_async(str(exc), chat_id)
    except Exception as exc:
        logger.warning("Voice practice failed (%s)", type(exc).__name__)
        await _send_async("Couldn't process that voice note. Try again with a short recording.", chat_id)
