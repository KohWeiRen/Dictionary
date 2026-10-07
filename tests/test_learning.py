"""Offline regression coverage for the Pi/Telegram/AI integration."""
import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

TEST_ENV = {
    "DATABASE_URL": "", "RENDER": "", "REQUIRE_DATABASE": "false", "GITHUB_ACTIONS": "",
    "TELEGRAM_API_KEY": "123:test-token", "BOT_OWNER_ID": "123",
    "MERRIAM_WEBSTER_DICT_API": "test", "MERRIAM_WEBSTER_SPANISH_DICT_API": "test",
    "WEBHOOK_SECRET": "test-secret", "GEMINI_API_KEY": "test-secret-api-key",
    "AI_DAILY_LIMIT": "20", "SPANISH_LEVEL": "A1", "LEARNING_START_DATE": "2026-10-07",
}
with patch.dict(os.environ, TEST_ENV):
    import app
    import run_bot
    import get_random_word_daily as daily
    from Custom_modules import dictionary_api_v2 as dictionary, spanish_audio, telegram_bot
    from Custom_modules import gemini_client as gemini, learning_store as store, spanish_lessons as lessons, bot_commands as commands
    from Custom_modules.english_vocabulary import WORDS

TODAY = datetime.date(2026, 10, 7)
LESSON_DATA = {
    "title": "At a cafe", "level": "A1",
    "vocabulary": [{"spanish": "un cafe", "english": "a coffee"}, {"spanish": "por favor", "english": "please"}],
    "grammar": "Use quiero plus a noun for a simple order.",
    "dialogue": [{"spanish": "Quiero un cafe, por favor.", "english": "I'd like a coffee, please."}, {"spanish": "Claro.", "english": "Of course."}],
    "mistake": "Say quiero, not quero.", "exercise": "Ask for a coffee.",
    "answer": "Quiero un cafe, por favor.", "speaking": "Repeat, then change your order.",
}
REPLY_DATA = {
    "reply_es": "Claro.", "translation_en": "Of course.", "correction_es": "Quiero un cafe.",
    "explanation_en": "Use quiero, not quero.", "follow_up_es": "Quieres agua?", "follow_up_en": "Do you want water?",
}


def lesson():
    return lessons.SpanishLesson.model_validate(LESSON_DATA)


def entry(word, lang, audio=None):
    return {"meta": {"id": word, "lang": lang, "offensive": False},
            "hwi": {"hw": word, "prs": [{"sound": {"audio": audio}}] if audio else []},
            "fl": "noun", "shortdef": ["a teaching example"]}


def response(payload, status=200, state="completed"):
    import httpx
    body = {"status": state, "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps(payload)}]}]}
    return httpx.Response(status, json=body, request=httpx.Request("POST", gemini.ENDPOINT))


class IsolatedStore:
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = {**TEST_ENV, "BOT_DATA_DIR": directory.name}
        environment = patch.dict(os.environ, settings)
        environment.start()
        self.addCleanup(environment.stop)
        clock = patch.object(store, "local_today", return_value=TODAY)
        clock.start()
        self.addCleanup(clock.stop)
        spanish_audio.synthesize_spanish.cache_clear()
        self.addCleanup(spanish_audio.synthesize_spanish.cache_clear)


class DictionaryTests(IsolatedStore, unittest.TestCase):
    def test_audio_locales_and_special_directories(self):
        self.assertEqual(dictionary._mw_audio_url("neces04sp", "es"), "https://media.merriam-webster.com/audio/prons/es/me/mp3/n/neces04sp.mp3")
        self.assertIn("/en/us/mp3/n/", dictionary._mw_audio_url("nuance01"))
        for filename, directory in (("bix123", "bix"), ("gg123", "gg"), ("3d000001", "number")):
            self.assertIn(f"/{directory}/{filename}.mp3", dictionary._mw_audio_url(filename, "es"))

    def test_spanish_formatter_skips_english_entry(self):
        output = dictionary.format_mw_entries([entry("media", "en", "media001"), entry("media", "es", "media01sp")], language="es")
        self.assertIn("/es/me/mp3/m/media01sp.mp3", output)
        self.assertNotIn("media001.mp3", output)

    def test_english_only_results_not_taught_as_spanish(self):
        data = [entry("natural", "en")]
        self.assertFalse(dictionary.mw_has_definition(data, "es"))
        self.assertIn("No Spanish entry", dictionary.format_mw_entries(data, language="es"))
        self.assertEqual(dictionary.mw_entries_for_language(["casa", "cosa"], "es"), ["casa", "cosa"])

    def test_fetch_filters_bilingual_response(self):
        result = MagicMock()
        result.json.return_value = [entry("natural", "en"), entry("natural", "es")]
        client = MagicMock()
        client.__enter__.return_value.get.return_value = result
        with patch.object(dictionary.httpx, "Client", return_value=client):
            data = dictionary.fetch_mw_spanish_json("natural")
        self.assertEqual([item["meta"]["lang"] for item in data], ["es"])

    def test_audio_in_later_pronunciation(self):
        data = [entry("hola", "es")]
        data[0]["hwi"]["prs"] = [{"mw": "ola"}, {"sound": {"audio": "hola001sp"}}]
        self.assertIn("hola001sp.mp3", dictionary.mw_audio_url(data, "es"))

    def test_english_rotation_stable_and_useful(self):
        with patch.object(daily, "fetch_mw_english_json", return_value=[]):
            selected = [daily.english_word_block(day)[0] for day in range(1, len(WORDS) + 1)]
            self.assertEqual(len(selected), len(set(selected)))
            self.assertEqual(daily.english_word_block(1), daily.english_word_block(1))
        self.assertFalse({"rosy", "fiddle", "phenotype", "phytoplankton"} & set(selected))

    def test_english_outage_preserves_word_and_examples(self):
        with patch.object(daily, "fetch_mw_english_json", side_effect=OSError("offline")):
            word, output = daily.english_word_block(1)
        self.assertEqual(word, "reticent")
        self.assertIn(WORDS[0].examples[0], output)

    def test_wrong_english_headword_cannot_attach_audio(self):
        with patch.object(daily, "fetch_mw_english_json", return_value=[entry("retire", "en", "retire01")]):
            self.assertNotIn("retire01.mp3", daily.english_word_block(1)[1])


class AIIntegrationTests(IsolatedStore, unittest.TestCase):
    def mock_client(self, result):
        client = MagicMock()
        client.__enter__.return_value.post.return_value = result
        return client

    def test_verified_rest_format_and_header_key(self):
        client = self.mock_client(response(LESSON_DATA))
        with patch.object(gemini.httpx, "Client", return_value=client):
            result = gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
        self.assertEqual(result.title, "At a cafe")
        call = client.__enter__.return_value.post.call_args
        self.assertEqual(call.args[0], gemini.ENDPOINT)
        self.assertNotIn("test-secret-api-key", call.args[0])
        self.assertEqual(call.kwargs["headers"]["x-goog-api-key"], "test-secret-api-key")
        self.assertFalse(call.kwargs["json"]["store"])
        self.assertEqual(call.kwargs["json"]["response_format"]["mime_type"], "application/json")

    def test_quota_error_is_safe_and_not_retried(self):
        client = self.mock_client(response({}, status=429))
        with patch.object(gemini.httpx, "Client", return_value=client), self.assertRaises(gemini.AIUnavailable) as error:
            gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
        self.assertIn("quota", str(error.exception))
        client.__enter__.return_value.post.assert_called_once()
        self.assertNotIn("test-secret-api-key", str(error.exception))

    def test_missing_key_does_not_call_api_or_consume_budget(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch.object(gemini.httpx, "Client") as client, patch.object(store, "reserve_request") as reserve:
            with self.assertRaises(gemini.AIUnavailable):
                gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
        client.assert_not_called()
        reserve.assert_not_called()

    def test_invalid_and_incomplete_ai_output_rejected(self):
        for result in (response({"title": "missing fields"}), response(LESSON_DATA, state="incomplete")):
            with patch.object(gemini.httpx, "Client", return_value=self.mock_client(result)), self.assertRaises(gemini.AIUnavailable):
                gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)

    def test_once_daily_cache_survives_new_connection(self):
        with patch.object(lessons, "generate_json", return_value=lesson()) as generate:
            first = lessons.lesson_for_date(123)
            second = lessons.lesson_for_date(123)
        self.assertEqual(first, second)
        generate.assert_called_once()
        self.assertEqual(lessons.SpanishLesson.model_validate_json(store.cached_lesson(123, TODAY)), first)

    def test_new_lesson_uses_previous_vocabulary_and_practice(self):
        store.save_lesson(123, TODAY - datetime.timedelta(days=1), lesson().model_dump_json())
        store.add_exchange(123, "Quero cafe", "Use quiero")
        with patch.object(lessons, "generate_json", return_value=lesson()) as generate:
            lessons.lesson_for_date(123)
        prompt = generate.call_args.args[1]
        self.assertIn("un cafe", prompt)
        self.assertIn("Quero cafe", prompt)
        self.assertIn("ser for origin", prompt)

    def test_past_replay_does_not_generate_and_missing_date_is_clear(self):
        past = TODAY - datetime.timedelta(days=1)
        with patch.object(lessons, "generate_json") as generate:
            with self.assertRaises(gemini.AIUnavailable):
                lessons.lesson_for_date(123, past)
            store.save_lesson(123, past, lesson().model_dump_json())
            self.assertEqual(lessons.lesson_for_date(123, past), lesson())
        generate.assert_not_called()

    def test_generation_failure_releases_lock_and_does_not_cache(self):
        with patch.object(lessons, "generate_json", side_effect=gemini.AIUnavailable("offline")):
            with self.assertRaises(gemini.AIUnavailable):
                lessons.lesson_for_date(123)
        self.assertIsNone(store.cached_lesson(123, TODAY))
        self.assertTrue(store.claim_lesson(123, TODAY))

    def test_simultaneous_generation_claim_does_not_duplicate_api_call(self):
        store.claim_lesson(123, TODAY)
        with patch.object(lessons, "generate_json") as generate:
            with self.assertRaises(gemini.AIUnavailable):
                lessons.lesson_for_date(123)
        generate.assert_not_called()

    def test_global_daily_cap_and_next_day_reset(self):
        with patch.dict(os.environ, {"AI_DAILY_LIMIT": "2"}):
            store.reserve_request(TODAY)
            store.reserve_request(TODAY)
            with self.assertRaises(gemini.AIUnavailable):
                store.reserve_request(TODAY)
            store.reserve_request(TODAY + datetime.timedelta(days=1))

    def test_concurrent_requests_respect_global_cap(self):
        from concurrent.futures import ThreadPoolExecutor
        def reserve(_):
            try:
                store.reserve_request(TODAY)
                return True
            except gemini.AIUnavailable:
                return False
        with patch.dict(os.environ, {"AI_DAILY_LIMIT": "2"}), ThreadPoolExecutor(max_workers=4) as pool:
            accepted = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(accepted), 2)

    def test_saved_lesson_available_without_key(self):
        store.save_lesson(123, TODAY, lesson().model_dump_json())
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch.object(lessons, "generate_json") as generate:
            self.assertEqual(lessons.lesson_for_date(123), lesson())
        generate.assert_not_called()

    def test_level_persists_and_changes_tutor_context(self):
        store.set_level(123, "A2")
        data = {**LESSON_DATA, "level": "A2"}
        with patch.object(lessons, "generate_json", return_value=lessons.SpanishLesson.model_validate(data)) as generate:
            lessons.lesson_for_date(123)
        self.assertIn('"level": "A2"', generate.call_args.args[1])
        self.assertEqual(store.get_level(123), "A2")

    def test_practice_correction_persists_bounded_context(self):
        reply = lessons.TutorReply.model_validate(REPLY_DATA)
        with patch.object(lessons, "generate_json", return_value=reply):
            for i in range(8):
                lessons.practice_reply(123, f"Quero cafe {i}")
        history = store.conversation(123)
        self.assertEqual(len(history), 12)
        self.assertEqual(history[-2]["text"], "Quero cafe 7")
        self.assertIn("Quiero", history[-1]["text"])
        store.reset_conversation(123)
        self.assertEqual(store.conversation(123), [])

    def test_model_answer_separate_from_initial_lesson(self):
        output = lessons.format_lesson(lesson(), TODAY)
        self.assertNotIn("Model answer", output)
        self.assertIn("/answer 2026-10-07", output)
        self.assertIn(lesson().answer, lessons.format_answer(lesson()))

    def test_voice_uses_inline_ogg_and_saves_transcript_only(self):
        voice_reply = lessons.VoiceTutorReply.model_validate({**REPLY_DATA, "transcript_es": "Quero cafe"})
        with patch.object(lessons, "generate_json", return_value=voice_reply) as generate:
            result = lessons.practice_voice(123, b"fake-ogg-audio")
        content = generate.call_args.args[1]
        self.assertEqual(content[1]["mime_type"], "audio/ogg")
        import base64
        self.assertEqual(base64.b64decode(content[1]["data"]), b"fake-ogg-audio")
        self.assertEqual(store.conversation(123)[0]["text"], "Quero cafe")
        self.assertIn("I heard: Quero cafe", lessons.format_reply(result))
        self.assertFalse(list(store.data_directory().glob("*.ogg")))

    def test_voice_size_checked_before_ai(self):
        with patch.object(lessons, "generate_json") as generate:
            for content in (b"", b"x" * 2_000_001):
                with self.assertRaises(gemini.AIUnavailable):
                    lessons.practice_voice(123, content)
        generate.assert_not_called()

    def test_daily_outage_still_has_english_and_honest_cached_review(self):
        past = TODAY - datetime.timedelta(days=1)
        store.save_lesson(123, past, lesson().model_dump_json())
        with patch.object(daily, "fetch_mw_english_json", return_value=[]), patch.object(daily, "lesson_for_date", side_effect=gemini.AIUnavailable("quota exhausted")):
            bundle = daily.build_daily_bundle(TODAY, 123)
        self.assertIn("reticent", bundle.text)
        self.assertIn("quota exhausted", bundle.text)
        self.assertIn("06 Oct 2026", bundle.text)
        self.assertEqual(bundle.speech, lesson().audio_text)


class DeliveryTests(IsolatedStore, unittest.IsolatedAsyncioTestCase):
    async def test_send_chunks_under_telegram_limit(self):
        bot = AsyncMock()
        bot.__aenter__.return_value = bot
        with patch.object(telegram_bot, "Bot", return_value=bot):
            await telegram_bot._send_async("x" * 5000, 123)
        self.assertEqual([len(call.kwargs["text"]) for call in bot.send_message.call_args_list], [4096, 904])

    async def test_normal_and_slow_audio_uploads(self):
        bot = AsyncMock()
        bot.__aenter__.return_value = bot
        with patch.object(telegram_bot, "Bot", return_value=bot), patch.object(spanish_audio, "synthesize_spanish", return_value=b"mp3") as synth:
            await telegram_bot.send_spanish_audio("Hola.", 123)
        self.assertEqual([call.args[1] for call in synth.call_args_list], [False, True])
        self.assertEqual(bot.send_audio.await_count, 2)

    async def test_audio_failure_gives_retry(self):
        bot = AsyncMock()
        bot.__aenter__.return_value = bot
        with patch.object(telegram_bot, "Bot", return_value=bot), patch.object(spanish_audio, "synthesize_spanish", side_effect=OSError("offline")):
            await telegram_bot.send_spanish_audio("Hola.", 123)
        bot.send_audio.assert_not_called()
        self.assertIn("/listen", bot.send_message.call_args.kwargs["text"])

    async def test_daily_sends_cached_bundle_audio_after_text(self):
        events = []
        async def text(value, chat):
            events.append("text")
        async def audio(value, chat):
            events.append("audio")
            self.assertEqual(value, lesson().audio_text)
        with patch.object(daily, "build_daily_bundle", return_value=daily.DailyPractice("lesson", lesson().audio_text)), patch.object(daily, "_send_async", side_effect=text), patch.object(daily, "send_spanish_audio", side_effect=audio):
            await daily.send_daily(TODAY)
        self.assertEqual(events, ["text", "audio"])

    async def test_lesson_replay_and_answer_do_not_generate_new_content(self):
        store.save_lesson(123, TODAY, lesson().model_dump_json())
        with patch.object(commands, "_send_async", new_callable=AsyncMock) as send, patch.object(commands, "send_spanish_audio", new_callable=AsyncMock) as audio, patch.object(lessons, "generate_json") as generate:
            await commands.dispatch_text(123, "/lesson@DictionaryBot 2026-10-07")
            audio.assert_awaited_once_with(lesson().audio_text, 123)
            audio.reset_mock()
            await commands.dispatch_text(123, "/answer")
            self.assertIn(lesson().answer, send.call_args.args[0])
            audio.assert_not_called()
        generate.assert_not_called()

    async def test_free_text_corrects_and_continues_in_owner_chat(self):
        with patch.object(commands, "_send_async", new_callable=AsyncMock) as send, patch.object(commands, "send_spanish_audio", new_callable=AsyncMock), patch.object(commands, "practice_reply", return_value=lessons.TutorReply.model_validate(REPLY_DATA)) as reply:
            await commands.dispatch_text(123, "Quero cafe")
        reply.assert_called_once_with(123, "Quero cafe", False)
        self.assertIn("Try: Quiero", send.call_args.args[0])

    async def test_other_chat_cannot_consume_ai_quota(self):
        with patch.object(commands, "_send_async", new_callable=AsyncMock), patch.object(commands, "practice_reply") as reply, patch.object(commands, "lesson_for_date") as generate:
            await commands.dispatch_text(456, "/practice cafe")
            await commands.dispatch_text(456, "/lesson")
        reply.assert_not_called()
        generate.assert_not_called()

    async def test_invalid_date_and_long_audio_are_explained(self):
        with patch.object(commands, "_send_async", new_callable=AsyncMock) as send, patch.object(commands, "send_spanish_audio", new_callable=AsyncMock) as audio:
            await commands.dispatch_text(123, "/lesson 8")
            self.assertIn("YYYY-MM-DD", send.call_args.args[0])
            await commands.dispatch_text(123, "/listen " + "x" * 601)
            self.assertIn("600", send.call_args.args[0])
        audio.assert_not_called()

    async def test_pi_polling_uses_shared_handler(self):
        update = MagicMock()
        update.effective_chat.id = 123
        update.effective_chat.type = "private"
        update.effective_message.text = "/lesson"
        with patch.object(run_bot, "dispatch_text", new_callable=AsyncMock) as dispatch:
            await run_bot.handle_text(update, MagicMock())
        dispatch.assert_awaited_once_with(123, "/lesson", True)

    async def test_voice_download_and_correction_delivery(self):
        bot = AsyncMock()
        bot.__aenter__.return_value = bot
        voice = AsyncMock()
        voice.file_size = 1234
        voice.download_as_bytearray.return_value = bytearray(b"ogg")
        bot.get_file.return_value = voice
        reply = lessons.VoiceTutorReply.model_validate({**REPLY_DATA, "transcript_es": "Quero cafe"})
        with patch.object(commands, "Bot", return_value=bot), patch.object(commands, "practice_voice", return_value=reply) as practice, patch.object(commands, "_send_async", new_callable=AsyncMock) as send, patch.object(commands, "send_spanish_audio", new_callable=AsyncMock) as audio:
            await commands.dispatch_voice(123, "file-id", 12, 1234)
        practice.assert_called_once_with(123, b"ogg")
        self.assertIn("I heard: Quero cafe", send.call_args.args[0])
        audio.assert_awaited_once_with(reply.audio_text, 123)

    async def test_voice_owner_and_duration_limits_before_download(self):
        with patch.object(commands, "Bot") as bot, patch.object(commands, "_send_async", new_callable=AsyncMock):
            await commands.dispatch_voice(456, "file-id", 12, 1234)
            await commands.dispatch_voice(123, "file-id", 61, 1234)
            await commands.dispatch_voice(123, "file-id", 12, 2_000_001)
        bot.assert_not_called()

    async def test_polling_voice_routes_to_shared_handler(self):
        update = MagicMock()
        update.effective_chat.id = 123
        update.effective_message.voice.file_id = "file-id"
        update.effective_message.voice.duration = 12
        update.effective_message.voice.file_size = 1234
        with patch.object(run_bot, "dispatch_voice", new_callable=AsyncMock) as dispatch:
            await run_bot.handle_voice(update, MagicMock())
        dispatch.assert_awaited_once_with(123, "file-id", 12, 1234)


class SpeechAndWebhookTests(IsolatedStore, unittest.TestCase):
    def test_tts_disk_cache_survives_memory_cache_reset(self):
        with patch("gtts.gTTS") as tts:
            tts.return_value.write_to_fp.side_effect = lambda stream: stream.write(b"test-mp3")
            self.assertEqual(spanish_audio.synthesize_spanish("Hola."), b"test-mp3")
            spanish_audio.synthesize_spanish.cache_clear()
            self.assertEqual(spanish_audio.synthesize_spanish("Hola."), b"test-mp3")
            tts.assert_called_once()
            spanish_audio.synthesize_spanish("Hola.", True)
            self.assertEqual(tts.call_count, 2)
        self.assertEqual(len(list((Path(os.environ["BOT_DATA_DIR"]) / "audio").glob("*.mp3"))), 2)
        with self.assertRaises(ValueError):
            spanish_audio.synthesize_spanish("x" * 601)

    def test_webhook_schedules_shared_dispatch_and_checks_secret(self):
        from fastapi.testclient import TestClient
        client = TestClient(app.app)
        body = {"message": {"chat": {"id": 123, "type": "private"}, "text": "/practice cafe"}}
        with patch.object(app, "dispatch_text", new_callable=AsyncMock) as dispatch:
            self.assertEqual(client.post("/telegram/test-secret", json=body).status_code, 200)
            dispatch.assert_awaited_once_with(123, "/practice cafe", True)
            dispatch.reset_mock()
            client.post("/telegram/wrong", json=body)
            dispatch.assert_not_called()
            client.post("/telegram/test-secret", json={"message": []})
            dispatch.assert_not_called()

    def test_webhook_voice_routes_to_shared_handler(self):
        from fastapi.testclient import TestClient
        body = {"message": {"chat": {"id": 123}, "voice": {"file_id": "file-id", "duration": 12, "file_size": 1234}}}
        with patch.object(app, "dispatch_voice", new_callable=AsyncMock) as dispatch:
            TestClient(app.app).post("/telegram/test-secret", json=body)
        dispatch.assert_awaited_once_with(123, "file-id", 12, 1234)


if __name__ == "__main__":
    unittest.main()
