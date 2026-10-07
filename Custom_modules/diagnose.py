"""Check storage and model fallback without sending Telegram."""
import os
from important_info import API_loader
from Custom_modules import learning_store as store
from Custom_modules.gemini_client import AIUnavailable, generate_json, model_sequence, _safe
from Custom_modules.spanish_lessons import SYSTEM, SpanishLesson


def main() -> int:
    print("Diagnostic: up to three Gemini attempts; no Telegram messages or lesson/history writes.", flush=True)
    try:
        print("Model order: " + " -> ".join(_safe(model, 100) for model in model_sequence()), flush=True)
    except AIUnavailable as error:
        print(f"CONFIGURATION FAILED: {error}", flush=True)
        return 1
    print("Gemini key: " + ("configured" if os.getenv("GEMINI_API_KEY", "").strip() else "missing"), flush=True)
    try:
        store.initialize()
        level = store.get_level(os.getenv("BOT_OWNER_ID") or "diagnostic")
        print(f"STORAGE OK: {store.storage_backend()} connected; tables ready.", flush=True)
    except store.StoreUnavailable as error:
        print(f"STORAGE FAILED: {error}", flush=True)
        return 1
    # Deliberately bypass the lesson cache to test the actual provider integration.
    prompt = f"""Generate one short {level} conversational Spanish lesson about
introductions. Use 2-3 vocabulary chunks, a brief English grammar explanation,
2-4 translated dialogue lines, a common mistake, an exercise, its model answer,
and a speak-aloud task. Set level to {level}. Keep every dialogue line and its
translation under 120 characters; all Spanish dialogue combined under 450
characters. Keep explanations under 250 characters. This is a connectivity and
schema validation check; follow every field's length guidance."""
    try:
        lesson = generate_json(SYSTEM, prompt, SpanishLesson)
        if lesson.level != level:
            raise AIUnavailable("The response passed validation but returned the wrong lesson level.")
        print("GEMINI OK: authenticated API request succeeded; Spanish lesson passed validation.", flush=True)
        return 0
    except AIUnavailable as error:
        print(f"GEMINI FAILED: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
