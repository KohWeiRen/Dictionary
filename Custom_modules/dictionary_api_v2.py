# Custom_modules/dictionary_api_v2.py
from __future__ import annotations
import re
import os
import httpx
from typing import Any, Dict, List, Optional
from important_info.API_loader import env

MW_EN_KEY = os.getenv("MERRIAM_WEBSTER_DICT_API")
MW_ES_KEY = os.getenv("MERRIAM_WEBSTER_SPANISH_DICT_API")

_FLAG = {"en": "🇬🇧", "es": "🇪🇸"}

# -------------------- helpers: MW audio URL + tag cleaner --------------------

def _mw_audio_url(audio: str, language: str = "en") -> str:
    """
    Build MW audio URL per their rules.
    https://dictionaryapi.com/products/json#sec-2.audio
    """
    if audio.startswith("bix"):
        sub = "bix"
    elif audio.startswith("gg"):
        sub = "gg"
    elif not audio[:1].isalpha():
        sub = "number"
    else:
        sub = audio[0]
    locale = "es/me" if language == "es" else "en/us"
    return f"https://media.merriam-webster.com/audio/prons/{locale}/mp3/{sub}/{audio}.mp3"

_TAG_PAT = re.compile(r"\{.*?\}")

_LINK_TOKEN = re.compile(r"\{[a-z0-9_]+\|([^{}|]*)[^{}]*\}")

# MW tokens that stand in for a literal character (must be substituted, not stripped).
_CHAR_TOKENS = {
    "{ldquo}": "“",  # left double quotation mark “
    "{rdquo}": "”",  # right double quotation mark ”
    "{p_br}": " ",        # paragraph/line break
}

def _clean_mw_text(s: str) -> str:
    """
    Make MW 'text' readable by removing/transforming inline tags. MW markup includes:
      {bc}                          -> ": " (bold colon divider)
      {ldquo}/{rdquo}/{p_br}        -> literal characters (substituted, not stripped)
      {it}...{/it}, {wi}...{/wi}    -> paired formatting wrappers (stripped)
      {a_link|word}, {d_link|w|id}, -> cross-reference / link tokens whose FIRST
      {sx|word||}, {dxt|w|id|t}        piped field is the display text (kept)
      {gl|masculine}                -> Spanish gender labels (surfaced separately)
    """
    s = s.replace("{bc}", ": ")
    # Character-bearing tokens carry content; substitute before the blanket strip below.
    for token, char in _CHAR_TOKENS.items():
        s = s.replace(token, char)
    # Any piped token ({tag|TEXT|...}) keeps its first field as the display text.
    # This covers a_link/d_link/i_link/et_link/sx/dxt/mat/... in one pass.
    s = _LINK_TOKEN.sub(r"\1", s)
    # Remove any remaining non-piped tags (e.g. {it}, {/it}, {wi}, {sc}, {/sc}, {phrase}).
    s = _TAG_PAT.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,;:.])", r"\1", s)
    # A leading ": " (from an entry that starts with {bc}) reads badly on its own line
    s = re.sub(r"^:\s*", "", s)
    return s

def _clean_headword(hw: str) -> str:
    """MW marks syllable breaks with '*' (e.g. 'apha*sic'); show a middle dot."""
    return hw.replace("*", "·")

# -------------------- response classification helpers ------------------------

def mw_response_kind(data: Any) -> str:
    """
    Classify a raw MW JSON response:
      'entries'     -> list of dict entries (a real match)
      'suggestions' -> list of strings ("did you mean ...")
      'empty'       -> nothing usable
    """
    if not isinstance(data, list) or not data:
        return "empty"
    if isinstance(data[0], dict):
        return "entries"
    if isinstance(data[0], str):
        return "suggestions"
    return "empty"

def mw_entry_is_offensive(data: Any) -> bool:
    """True if any returned entry is flagged offensive by MW."""
    if not isinstance(data, list):
        return False
    for e in data:
        if isinstance(e, dict) and (e.get("meta") or {}).get("offensive"):
            return True
    return False

# -------------------- common definition extractor (EN & ES) ------------------

def _dt_to_text_and_examples(dt: Any, include_gender: bool) -> tuple[str, List[str]]:
    """Turn a sense's 'dt' block into (definition_text, [examples])."""
    text_buf: List[str] = []
    gender_buf: List[str] = []
    examples_buf: List[str] = []

    for piece in dt if isinstance(dt, list) else []:
        if not (isinstance(piece, list) and piece):
            continue
        tag = piece[0]

        if tag == "text" and len(piece) > 1 and isinstance(piece[1], str):
            text_buf.append(_clean_mw_text(piece[1]))

        elif tag == "gl" and include_gender and len(piece) > 1 and isinstance(piece[1], str):
            gender_buf.append(piece[1])

        elif tag == "vis" and len(piece) > 1 and isinstance(piece[1], list):
            for ex in piece[1]:
                if not isinstance(ex, dict):
                    continue
                t = ex.get("t")
                tr = ex.get("tr")
                if isinstance(t, str) and isinstance(tr, str):
                    examples_buf.append(f"{_clean_mw_text(t)} → {_clean_mw_text(tr)}")
                elif isinstance(t, str):
                    examples_buf.append(_clean_mw_text(t))

    text = " ".join(p for p in text_buf if p).strip()
    if gender_buf:
        text = (text + f" ({'; '.join(gender_buf)})").strip()
    return text, examples_buf


def _process_sense(sense: Dict[str, Any], include_gender: bool, out: List[Dict[str, Any]]) -> None:
    text, examples = _dt_to_text_and_examples(sense.get("dt"), include_gender)
    if text or examples:
        out.append({"text": text, "examples": examples})

    # 'sdsense' (also/compare) sometimes holds an extra alt text
    sdsense = sense.get("sdsense")
    if isinstance(sdsense, dict):
        sd_text, _ = _dt_to_text_and_examples(sdsense.get("dt"), include_gender=False)
        if sd_text:
            out.append({"text": sd_text, "examples": []})


def _walk_sense_element(item: Any, include_gender: bool, out: List[Dict[str, Any]]) -> None:
    """Handle one sseq element, recursing into grouping wrappers (pseq/bs)."""
    if not (isinstance(item, list) and len(item) >= 2):
        return
    tag, payload = item[0], item[1]
    if tag in ("sense", "sen") and isinstance(payload, dict):
        _process_sense(payload, include_gender, out)
    elif tag == "bs" and isinstance(payload, dict) and isinstance(payload.get("sense"), dict):
        _process_sense(payload["sense"], include_gender, out)   # binding substitute
    elif tag == "pseq" and isinstance(payload, list):
        for sub in payload:                                     # parenthesized sequence
            _walk_sense_element(sub, include_gender, out)


def _extract_senses(sseq: Any, include_gender: bool = True) -> List[Dict[str, Any]]:
    """
    Walk 'def' -> 'sseq' blocks and collect senses as structured dicts:
      {"text": "<definition>", "examples": ["<ex> → <translation>", ...]}
    Senses may be direct ('sense'), truncated ('sen'), or nested inside grouping
    wrappers ('bs' binding substitute, 'pseq' parenthesized sequence). For Spanish,
    'dt' can include ["gl", "masculine"] gender labels and bilingual
    ["vis", [{"t": ..., "tr": ...}]] examples; English is usually text + examples.
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(sseq, list):
        return out

    for block in sseq:
        if not isinstance(block, list):
            continue
        for item in block:
            _walk_sense_element(item, include_gender, out)

    # Deduplicate by definition text, preserving order
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for s in out:
        key = s["text"]
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        uniq.append(s)
    return uniq

# -------------------- fetchers (return RAW JSON) -----------------------------

def fetch_mw_english_json(word: str, timeout: float = 15) -> Any:
    key = MW_EN_KEY or env("MERRIAM_WEBSTER_DICT_API")
    url = f"https://www.dictionaryapi.com/api/v3/references/collegiate/json/{word}?key={key}"
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()

def fetch_mw_spanish_json(word: str, timeout: float = 15) -> Any:
    key = MW_ES_KEY or env("MERRIAM_WEBSTER_SPANISH_DICT_API")
    url = f"https://www.dictionaryapi.com/api/v3/references/spanish/json/{word}?key={key}"
    with httpx.Client(timeout=timeout) as client:
        r = client.get(url)
        r.raise_for_status()
        return mw_entries_for_language(r.json(), "es")

def fetch_en_example(word: str, timeout: float = 10) -> Optional[str]:
    """
    Fetch a real usage example for an English word from the free dictionaryapi.dev,
    used as a fallback when Merriam-Webster's Collegiate entry has no example
    sentence. Returns the first example found, or None.
    """
    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for meaning in entry.get("meanings", []) or []:
            for d in meaning.get("definitions", []) or []:
                ex = d.get("example") if isinstance(d, dict) else None
                if isinstance(ex, str) and ex.strip():
                    return ex.strip()
    return None

# -------------------- examples helper ----------------------------------------

def mw_entries_for_language(data: Any, language: str) -> Any:
    """The Spanish endpoint is bidirectional; never teach its English entries."""
    if mw_response_kind(data) != "entries":
        return data  # preserve spelling suggestions
    return [
        e for e in data if isinstance(e, dict)
        and ((e.get("meta") or {}).get("lang") == "es" if language == "es"
             else (e.get("meta") or {}).get("lang", "en") == "en")
    ]


def _first_entry(data: Any, language: Optional[str] = None) -> Dict[str, Any]:
    if language:
        data = mw_entries_for_language(data, language)
    if not isinstance(data, list):
        return {}
    return next((e for e in data if isinstance(e, dict)), {})


def mw_audio_url(data: Any, language: str) -> Optional[str]:
    """Find the first available recording, including later pronunciations."""
    entry = _first_entry(data, language)
    for pronunciation in (entry.get("hwi") or {}).get("prs") or []:
        if not isinstance(pronunciation, dict):
            continue
        audio = (pronunciation.get("sound") or {}).get("audio")
        if audio:
            return _mw_audio_url(audio, language)
    return None

def mw_examples(data: Any, language: str) -> List[str]:
    """Return every usage example found in the first MW entry (may be empty)."""
    if mw_response_kind(data) != "entries":
        return []
    entry = _first_entry(data, language)
    defs = entry.get("def") or []
    if not (defs and isinstance(defs[0], dict)):
        return []
    senses = _extract_senses(defs[0].get("sseq"), include_gender=(language == "es"))
    examples: List[str] = []
    for s in senses:
        examples.extend(s["examples"])
    return examples

def mw_functional_label(data: Any, language: Optional[str] = None) -> str:
    """The part of speech / functional label (fl) of the first entry, lowercased."""
    return (_first_entry(data, language).get("fl") or "").lower()

def mw_has_definition(data: Any, language: str) -> bool:
    """True if the first entry carries a real definition (sseq senses or shortdef)."""
    if mw_response_kind(data) != "entries":
        return False
    entry = _first_entry(data, language)
    defs = entry.get("def") or []
    if defs and isinstance(defs[0], dict):
        if _extract_senses(defs[0].get("sseq"), include_gender=(language == "es")):
            return True
    return any(isinstance(x, str) and x.strip() for x in (entry.get("shortdef") or []))

def _extract_cxs(entry: Dict[str, Any]) -> List[str]:
    """
    Cognate cross-references, used by variant/inflected entries that have no
    definition of their own, e.g. 'chiefly British spelling of emphasize'.
    """
    out: List[str] = []
    for cx in entry.get("cxs", []) or []:
        if not isinstance(cx, dict):
            continue
        label = _clean_mw_text(cx.get("cxl", "") or "")
        targets = [
            _clean_mw_text(t.get("cxt", ""))
            for t in (cx.get("cxtis") or [])
            if isinstance(t, dict) and t.get("cxt")
        ]
        text = " ".join(p for p in [label, ", ".join(targets)] if p).strip()
        if text:
            out.append(text)
    return out

# -------------------- formatting ---------------------------------------------

def format_suggestions(data: Any, *, language: str, limit: int = 8) -> str:
    """Friendly 'did you mean' message for a near-miss lookup."""
    flag = _FLAG.get(language, "")
    suggestions = [s for s in data if isinstance(s, str)][:limit]
    if not suggestions:
        return f"{flag} No entry found — and no close matches.".strip()
    body = "\n".join(f"  • {s}" for s in suggestions)
    return f"{flag} No exact match. Did you mean:\n{body}".strip()

def format_mw_entries(
    data: Any,
    *,
    language: str,
    max_defs: int = 6,
    max_examples: int = 2,
    show_audio: bool = True,
    fallback_example: Optional[str] = None,
) -> str:
    """
    Render the first dictionary entry from MW JSON as a clean, readable block for
    either language='en' (Collegiate) or language='es' (Spanish-English).
    Suggestions (near-misses) and empty responses are rendered gracefully.

    If the entry carries no usage example of its own, `fallback_example` (e.g.
    an example sentence sourced elsewhere) is shown so the word always appears
    in context.
    """
    data = mw_entries_for_language(data, language)
    kind = mw_response_kind(data)
    if kind == "empty":
        return f"{_FLAG.get(language, '')} No {('Spanish' if language == 'es' else 'English')} entry found.".strip()
    if kind == "suggestions":
        return format_suggestions(data, language=language)

    entry = _first_entry(data)
    meta = entry.get("meta", {}) or {}
    hwi = entry.get("hwi", {}) or {}

    headword = _clean_headword(hwi.get("hw", "") or meta.get("id", "") or "-")
    pos = entry.get("fl")
    offensive = bool(meta.get("offensive", False))

    ipa = None
    audio_url = mw_audio_url(data, language)
    prs = hwi.get("prs") or []
    if prs and isinstance(prs[0], dict):
        ipa = prs[0].get("mw")

    # Definitions: prefer rich def.sseq, fall back to shortdef
    senses: List[Dict[str, Any]] = []
    defs = entry.get("def") or []
    if defs and isinstance(defs[0], dict):
        senses = _extract_senses(defs[0].get("sseq"), include_gender=(language == "es"))
    if not senses:
        senses = [{"text": str(x), "examples": []} for x in (entry.get("shortdef") or []) if isinstance(x, str)]
    if not senses:
        # Variant/inflected entries with no definition of their own: show the
        # cross-reference (e.g. "chiefly British spelling of emphasize").
        senses = [{"text": f"→ {c}", "examples": []} for c in _extract_cxs(entry)]

    # Build output
    flag = _FLAG.get(language, "")
    title = f"{flag} {headword}".strip()
    if pos:
        title += f" · {pos}"
    if offensive:
        title += "  ⚠️ offensive"

    lines: List[str] = [title]
    if ipa:
        lines.append(f"🔊 {ipa}")

    rendered_example = False
    if senses:
        for i, s in enumerate(senses[:max_defs], start=1):
            lines.append(f"{i}. {s['text']}" if s["text"] else f"{i}.")
            for ex in s["examples"][:max_examples]:
                lines.append(f"   • {ex}")
                rendered_example = True
    else:
        lines.append("(no definition text available)")

    if not rendered_example and fallback_example:
        lines.append(f"📝 e.g. {fallback_example}")

    if show_audio and audio_url:
        lines.append(f"🎧 {audio_url}")

    return "\n".join(lines)

def main():
    data = fetch_mw_english_json(word="prodigious")
    print(format_mw_entries(data, language="en"))

if __name__ == "__main__":
    main()
