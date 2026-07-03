import random, datetime
from typing import Callable, Optional
from wordfreq import top_n_list
from Custom_modules.dictionary_api_v2 import (
    fetch_mw_english_json,
    fetch_mw_spanish_json,
    fetch_en_example,
    format_mw_entries,
    mw_response_kind,
    mw_entry_is_offensive,
    mw_examples,
    mw_has_definition,
    mw_functional_label,
)

# Functional labels that aren't real vocabulary words (skip for the daily word).
_NON_WORD_LABELS = ("suffix", "prefix", "combining form", "abbreviation", "symbol")
from Custom_modules.telegram_bot import send_text
from important_info.API_loader import env

# ---------------------------------------------------------------------------
# Word-selection strategy
#
# wordfreq ranks words most-common-first. We pick from a *rank band* per
# language instead of the raw rarest tail (which is full of proper nouns,
# foreign borrowings and junk that isn't in the dictionary):
#
#   English -> a rarer band, so the daily word is novel but still real.
#   Spanish -> a common band, so the word is simple and learner-friendly.
#
# Every candidate is then validated against Merriam-Webster: we only accept a
# word if MW returns real entries (not "did you mean" suggestions) and it isn't
# flagged offensive. We also prefer words that come with a real usage example so
# the daily word is always shown in an everyday sentence (Spanish examples come
# from MW; English falls back to dictionaryapi.dev). If the random draws all
# miss, we fall back to a small curated list of words known to be in the
# dictionary.
#
# Novelty guard (English): wordfreq counts inflected forms separately, so a rare
# token like "competes" (rank ~20k) resolves in MW to its common lemma "compete"
# (rank ~3.5k). We therefore require the *displayed headword* to itself be rarer
# than the EN_NOVEL_MIN_RANK most common words, so the word shown is genuinely
# novel. Spanish has no such guard — common lemmas are exactly what a learner
# wants.
# ---------------------------------------------------------------------------

EN_BAND = (12_000, 50_000)   # novel but real English vocabulary
ES_BAND = (120, 2_500)       # common, learner-friendly Spanish
EN_NOVEL_MIN_RANK = 9_000    # displayed English headword must be rarer than this
EN_EXAMPLE_BUDGET = 12       # max dictionaryapi.dev example lookups per run
MAX_ATTEMPT = 25
CHAT_ID = env("BOT_OWNER_ID")

EN_FALLBACK = [
    "ephemeral", "quixotic", "serendipity", "ineffable", "halcyon",
    "mellifluous", "penumbra", "susurrus", "limpid", "nascent",
    "quotidian", "sonorous", "ebullient", "laconic", "petrichor",
]
ES_FALLBACK = [
    "biblioteca", "ventana", "trabajo", "comida", "escuela",
    "ciudad", "camino", "familia", "cocina", "palabra",
    "mañana", "noche", "agua", "libro", "puerta",
]


def _band_candidates(language: str, band: tuple[int, int]) -> list[str]:
    """Return the alphabetic words whose frequency rank falls inside `band`."""
    lo, hi = band
    words = top_n_list(language, n=hi)
    words = [w for w in words if w.isalpha() and len(w) > 2]
    return words[lo:hi]


_EN_COMMON: Optional[set[str]] = None

def _en_common_words() -> set[str]:
    """The EN_NOVEL_MIN_RANK most common English words (built once, lazily)."""
    global _EN_COMMON
    if _EN_COMMON is None:
        _EN_COMMON = set(top_n_list("en", n=EN_NOVEL_MIN_RANK))
    return _EN_COMMON


def _headword_id(data: object) -> str:
    """The base headword MW actually returned (e.g. 'compete' from id 'compete:1')."""
    entry = next((e for e in data if isinstance(e, dict)), {}) if isinstance(data, list) else {}
    hid = (entry.get("meta") or {}).get("id") or ""
    return hid.split(":")[0].strip().lower()


def _fmt(language: str, data: object, example: Optional[str] = None) -> str:
    return format_mw_entries(
        data, language=language, max_defs=4, max_examples=2, fallback_example=example
    )


def _pick_word_block(
    language: str,
    fetch: Callable[[str], object],
    band: tuple[int, int],
    fallback: list[str],
    max_tries: int,
) -> Optional[tuple[str, str]]:
    """
    Try random words from the rank band, then the curated fallback, until one
    resolves to a real (non-offensive) MW entry. Preference order, so the daily
    word is shown in an everyday sentence whenever possible:
      1) a word whose MW entry carries its own usage example;
      2) (English only) a valid word for which dictionaryapi.dev has an example;
      3) any valid word, example or not.
    English candidates whose displayed headword is a common word are skipped
    entirely (novelty guard). Returns (word, formatted_text).
    """
    candidates = _band_candidates(language, band)
    sample = random.sample(candidates, min(max_tries, len(candidates))) if candidates else []
    order = [*sample, *random.sample(fallback, len(fallback))]

    first_valid: Optional[tuple[str, object]] = None
    en_budget = EN_EXAMPLE_BUDGET

    for word in order:
        try:
            data = fetch(word)
        except Exception:
            continue
        if mw_response_kind(data) != "entries" or mw_entry_is_offensive(data):
            continue
        if not mw_has_definition(data, language):
            continue  # variant/inflection with no definition of its own
        if any(bad in mw_functional_label(data) for bad in _NON_WORD_LABELS):
            continue  # a suffix/prefix/abbreviation, not a word

        headword = _headword_id(data)
        if "-" in headword:
            continue  # hyphenated affix headword (e.g. "-ary")
        if language == "en" and headword and headword in _en_common_words():
            continue  # displayed word too common — keep looking for a novel one

        if mw_examples(data, language):
            return word, _fmt(language, data)             # 1) native usage example

        if language == "en" and en_budget > 0:
            en_budget -= 1
            example = fetch_en_example(headword or word)  # 2) borrow everyday example
            if example:
                return word, _fmt("en", data, example)

        if first_valid is None:
            first_valid = (word, data)

    if first_valid is not None:                           # 3) definition only
        word, data = first_valid
        return word, _fmt(language, data)
    return None


def get_random_en_word(max_tries: int = MAX_ATTEMPT) -> Optional[tuple[str, str]]:
    return _pick_word_block("en", fetch_mw_english_json, EN_BAND, EN_FALLBACK, max_tries)


def get_random_es_word(max_tries: int = MAX_ATTEMPT) -> Optional[tuple[str, str]]:
    return _pick_word_block("es", fetch_mw_spanish_json, ES_BAND, ES_FALLBACK, max_tries)


def build_message() -> str:
    today = datetime.datetime.today().strftime("%d %b %Y")
    parts = [f"📖 Daily Words · {today}", ""]

    en = get_random_en_word()
    parts.append(en[1] if en else "🇬🇧 (couldn't fetch an English word today)")

    parts.append("\n────────────────\n")

    es = get_random_es_word()
    parts.append(es[1] if es else "🇪🇸 (no pude encontrar una palabra hoy)")

    return "\n".join(parts)


def main():
    msg = build_message()
    # Standalone script, so send_text() (which uses asyncio.run) is fine:
    send_text(msg, CHAT_ID)


if __name__ == "__main__":
    main()
