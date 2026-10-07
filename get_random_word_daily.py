"""Build a useful English word and a progressive Spanish speaking lesson."""
import argparse
import asyncio
import datetime
import logging
import os
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from Custom_modules.dictionary_api_v2 import fetch_mw_english_json, mw_audio_url
from Custom_modules.english_vocabulary import WORDS
from Custom_modules.spanish_lessons import format_lesson, lesson_for_date, SpanishLesson
from Custom_modules.gemini_client import AIUnavailable
from Custom_modules import learning_store as store
from Custom_modules.telegram_bot import _send_async, send_spanish_audio
from important_info.API_loader import env

logger = logging.getLogger(__name__)

# English catalogue anchor. Spanish progression uses shared lesson history.
DEFAULT_START_DATE = "2026-10-07"
TIMEZONE = ZoneInfo("Asia/Singapore")


def local_today() -> datetime.date:
    return datetime.datetime.now(TIMEZONE).date()


def learning_day(today: datetime.date | None = None) -> int:
    start = datetime.date.fromisoformat(os.getenv("LEARNING_START_DATE") or DEFAULT_START_DATE)
    return max(1, ((today or local_today()) - start).days + 1)


def english_word_block(day: int) -> tuple[str, str]:
    """Rotate curated words; outages must not replace today's word or examples."""
    if day < 1:
        raise ValueError("Learning day must be at least 1.")
    item = WORDS[(day - 1) % len(WORDS)]
    lines = [f"🇬🇧 {item.word}", item.meaning, f"Usage: {item.pattern}", f"Tone: {item.register}"]
    lines.extend(f"• {example}" for example in item.examples)
    lines.append("Your turn: use this word in a sentence about your day.")
    try:
        data = fetch_mw_english_json(item.word, timeout=5)
        # MW can resolve another lemma. Attach pronunciation only for this word.
        entries = [e for e in data if isinstance(e, dict)
                   and (e.get("meta") or {}).get("id", "").split(":")[0].lower() == item.word]
        if entries:
            prs = (entries[0].get("hwi") or {}).get("prs") or []
            pronunciation = next((p.get("mw") or p.get("ipa") for p in prs
                                  if isinstance(p, dict) and (p.get("mw") or p.get("ipa"))), None)
            if pronunciation:
                lines.insert(1, f"🔊 {pronunciation}")
            audio = mw_audio_url(entries, "en")
            if audio:
                lines.append(f"🎧 {audio}")
    except Exception:
        logger.warning("Dictionary pronunciation unavailable for %s", item.word)
    return item.word, "\n".join(lines)


def get_random_en_word(max_tries: int = 25) -> tuple[str, str]:
    """Compatibility entrypoint; selection is now stable for the learning day."""
    return english_word_block(learning_day())


@dataclass(frozen=True)
class DailyPractice:
    text: str
    speech: str | None = None


def build_daily_bundle(today: datetime.date | None = None, chat_id: int | str | None = None) -> DailyPractice:
    today = today or local_today()
    chat_id = chat_id or env("BOT_OWNER_ID")
    _, english = english_word_block(learning_day(today))
    speech = None
    try:
        lesson = lesson_for_date(chat_id, today)
        spanish = format_lesson(lesson, today)
        speech = lesson.audio_text
    except AIUnavailable as exc:
        spanish = f"🇪🇸 {exc}"
        recent = store.recent_lessons(chat_id, today + datetime.timedelta(days=1), limit=1)
        if recent:
            saved = SpanishLesson.model_validate_json(recent[0]["payload"])
            saved_date = datetime.date.fromisoformat(recent[0]["day"])
            spanish += "\n\nReview your saved lesson while AI is unavailable:\n" + format_lesson(saved, saved_date)
            speech = saved.audio_text
    text = "\n".join([
        f"📖 Daily Practice · {today.strftime('%d %b %Y')}", "", english,
        "\n────────────────\n", spanish,
    ])
    if speech:
        text += "\n\n🎧 Spanish dialogue: normal + slow audio follows. Listen, repeat, then answer aloud."
    return DailyPractice(text, speech)


def build_message(today: datetime.date | None = None) -> str:
    return build_daily_bundle(today).text


async def send_daily(today: datetime.date | None = None, force: bool = False) -> None:
    today = today or local_today()
    owner = env("BOT_OWNER_ID")
    if not force and not await asyncio.to_thread(store.claim_delivery, owner, today):
        print("Today's daily post was already sent or is being sent. Use --force to resend.")
        return
    try:
        practice = await asyncio.to_thread(build_daily_bundle, today, owner)
        await _send_async(practice.text, None)
        if practice.speech:
            await send_spanish_audio(practice.speech, None)
        await asyncio.to_thread(store.finish_delivery, owner, today)
    except Exception:
        await asyncio.to_thread(store.finish_delivery, owner, today, False)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Print only; do not send Telegram messages or audio")
    parser.add_argument("--date", type=datetime.date.fromisoformat, help="Replay a saved date (YYYY-MM-DD)")
    parser.add_argument("--check", action="store_true", help="Check configuration without API calls or Telegram messages")
    parser.add_argument("--force", action="store_true", help="Resend a daily post even if already delivered")
    args = parser.parse_args()
    if args.check:
        missing = [key for key in ("TELEGRAM_API_KEY", "BOT_OWNER_ID", "MERRIAM_WEBSTER_DICT_API",
                                   "MERRIAM_WEBSTER_SPANISH_DICT_API", "GEMINI_API_KEY") if not os.getenv(key)]
        if missing:
            parser.exit(1, "Missing settings: " + ", ".join(missing) + "\n")
        try:
            backend = store.storage_backend()
        except store.StoreUnavailable as error:
            parser.exit(1, str(error) + "\n")
        print(f"Required settings are present. Storage: {backend}. Credentials have not been validated remotely.")
        return
    if args.preview:
        print(build_message(args.date))
    else:
        try:
            asyncio.run(send_daily(args.date, args.force))
        except store.StoreUnavailable as error:
            parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
