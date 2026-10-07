"""Sentence speech synthesis, separate from lesson authoring and dictionary audio."""
from functools import lru_cache
from io import BytesIO
import os
import hashlib

MAX_SPEECH_CHARS = 600


@lru_cache(maxsize=128)
def synthesize_spanish(text: str, slow: bool = False) -> bytes:
    text = text.strip()
    if not text or len(text) > MAX_SPEECH_CHARS:
        raise ValueError(f"Spanish audio needs 1–{MAX_SPEECH_CHARS} characters.")
    from Custom_modules.learning_store import data_directory
    locale = os.getenv("SPANISH_TTS_TLD") or "com.mx"
    digest = hashlib.sha256(f"v1:{locale}:{slow}:{text}".encode("utf-8")).hexdigest()
    directory = data_directory() / "audio"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.mp3"
    if target.exists():
        return target.read_bytes()
    from gtts import gTTS
    output = BytesIO()
    gTTS(text=text, lang="es", tld=locale,
         slow=slow, lang_check=False, timeout=(5, 15)).write_to_fp(output)
    content = output.getvalue()
    # Atomic replacement so cron and polling never read half-written audio.
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as temporary:
        temporary.write(content)
        temporary_path = temporary.name
    try:
        os.replace(temporary_path, target)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
    return content
