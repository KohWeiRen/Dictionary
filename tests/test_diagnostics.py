import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch
import httpx
from test_learning import IsolatedStore, LESSON_DATA, response, gemini, lessons, store, lesson
from Custom_modules import diagnose


class GeminiDiagnosticsTests(IsolatedStore, unittest.TestCase):
    def client(self, result=None, error=None):
        client = MagicMock()
        post = client.__enter__.return_value.post
        post.return_value = result
        post.side_effect = error
        return patch.object(gemini.httpx, "Client", return_value=client)

    def test_provider_errors_report_status_reason_and_redact_secrets(self):
        for status, category in ((400, "request"), (401, "auth"), (403, "auth"), (404, "model"), (429, "quota"), (503, "provider")):
            result = httpx.Response(status, json={"error": {"status": "TEST_STATUS",
                "message": "Unsupported schema field; key=test-secret-api-key URL https://example.com/private"}},
                request=httpx.Request("POST", gemini.ENDPOINT))
            with self.client(result), self.assertLogs(gemini.logger, level="ERROR") as captured:
                with self.assertRaises(gemini.AIUnavailable) as error:
                    gemini.generate_json("private prompt", "private answer", lessons.SpanishLesson)
            record = json.loads(captured.records[0].getMessage())
            self.assertEqual(record["http_status"], status)
            self.assertEqual(record["category"], category)
            self.assertIn(str(status), str(error.exception))
            self.assertIn(record["ref"], str(error.exception))
            self.assertIn("Unsupported schema field", record["reason"])
            combined = str(error.exception) + captured.records[0].getMessage()
            for secret in ("test-secret-api-key", "https://example.com/private", "private prompt", "private answer"):
                self.assertNotIn(secret, combined)

    def test_timeout_and_connection_failure_are_distinct(self):
        for error, category in ((httpx.ReadTimeout("private URL"), "timeout"), (httpx.ConnectError("private URL"), "network")):
            with self.client(error=error), self.assertLogs(gemini.logger, level="ERROR"):
                with self.assertRaises(gemini.AIUnavailable) as raised:
                    gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
            self.assertIn(category, str(raised.exception))
            self.assertNotIn("private URL", str(raised.exception))

    def test_validation_reports_field_and_constraint_without_learner_text(self):
        data = {**LESSON_DATA, "title": "private-content" * 20}
        with self.client(response(data)), self.assertLogs(gemini.logger, level="ERROR") as captured:
            with self.assertRaises(gemini.AIUnavailable) as error:
                gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
        self.assertIn("title (string_too_long)", str(error.exception))
        self.assertNotIn("private-content", str(error.exception) + captured.records[0].getMessage())

    def test_invalid_json_empty_output_and_incomplete_status_are_distinct(self):
        malformed = response(LESSON_DATA)
        malformed = httpx.Response(200, content=b"not-json", request=malformed.request)
        for result, category in ((malformed, "response_json"), (response(LESSON_DATA, state="incomplete"), "incomplete"),
                                 (httpx.Response(200, json={"status": "completed", "steps": []}), "empty_output")):
            with self.client(result), self.assertLogs(gemini.logger, level="ERROR"):
                with self.assertRaises(gemini.AIUnavailable) as error:
                    gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
            self.assertIn(category, str(error.exception))

    def test_legacy_outputs_are_parsed_without_changing_validation(self):
        result = httpx.Response(200, json={"status": "completed", "outputs": [{"type": "text", "text": json.dumps(LESSON_DATA)}]})
        with self.client(result):
            self.assertEqual(gemini.generate_json("tutor", "lesson", lessons.SpanishLesson), lesson())

    def test_provider_schema_guides_lengths_and_retains_local_validation(self):
        provider = gemini._provider_schema(lessons.SpanishLesson)
        self.assertNotIn('"maxLength"', json.dumps(provider))
        self.assertNotIn('"minLength"', json.dumps(provider))
        self.assertIn("at most 150", provider["properties"]["title"]["description"])
        self.assertEqual(provider["properties"]["dialogue"]["maxItems"], 4)
        self.assertEqual(lessons.SpanishLesson.model_json_schema()["properties"]["title"]["maxLength"], 150)

    def test_diagnostic_bypasses_lesson_cache_and_sends_no_telegram(self):
        store.save_lesson(123, store.local_today(), lesson().model_dump_json())
        output = io.StringIO()
        with patch.object(diagnose, "generate_json", return_value=lesson()) as call, redirect_stdout(output):
            self.assertEqual(diagnose.main(), 0)
        call.assert_called_once()
        self.assertIn("STORAGE OK", output.getvalue())
        self.assertIn("GEMINI OK", output.getvalue())
        self.assertNotIn("test-secret-api-key", output.getvalue())


class ModelFallbackTests(IsolatedStore, unittest.TestCase):
    def setUp(self):
        super().setUp()
        settings = patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": ""})
        settings.start()
        self.addCleanup(settings.stop)

    def run_responses(self, results):
        client = MagicMock()
        post = client.__enter__.return_value.post
        post.side_effect = results
        return patch.object(gemini.httpx, "Client", return_value=client), post

    def attempted_models(self, post):
        return [call.kwargs["json"]["model"] for call in post.call_args_list]

    def request_count(self):
        with store.database() as db:
            return db.execute("SELECT count FROM requests WHERE day=?", (store.local_today().isoformat(),)).fetchone()["count"]

    def test_overload_falls_back_and_returns_valid_lesson(self):
        context, post = self.run_responses([response({}, status=503), response(LESSON_DATA)])
        with context, self.assertLogs(gemini.logger, level="WARNING") as captured:
            result = gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
        self.assertEqual(result, lesson())
        self.assertEqual(self.attempted_models(post), ["gemini-3.8-flash", "gemini-3.7-flash"])
        self.assertEqual(self.request_count(), 2)
        records = [json.loads(item.getMessage()) for item in captured.records]
        self.assertEqual(records[-1]["event"], "gemini_fallback_success")
        self.assertEqual(records[0]["ref"], records[-1]["ref"])

    def test_success_on_primary_uses_one_attempt(self):
        context, post = self.run_responses([response(LESSON_DATA)])
        with context:
            self.assertEqual(gemini.generate_json("tutor", "lesson", lessons.SpanishLesson), lesson())
        self.assertEqual(self.attempted_models(post), ["gemini-3.8-flash"])
        self.assertEqual(self.request_count(), 1)

    def test_http_200_with_invalid_lesson_does_not_end_fallback(self):
        context, post = self.run_responses([response({"title": "missing fields"}), response(LESSON_DATA)])
        with context, self.assertLogs(gemini.logger, level="WARNING"):
            self.assertEqual(gemini.generate_json("tutor", "lesson", lessons.SpanishLesson), lesson())
        self.assertEqual(post.call_count, 2)

    def test_all_models_fail_without_looping_or_repeating(self):
        context, post = self.run_responses([response({}, status=503)] * 3)
        with context, self.assertLogs(gemini.logger, level="ERROR"):
            with self.assertRaises(gemini.AIUnavailable) as error:
                gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
        self.assertEqual(self.attempted_models(post), list(gemini.FREE_TIER_MODELS))
        self.assertIn("All 3", str(error.exception))
        self.assertIn("HTTP 503", str(error.exception))
        self.assertEqual(self.request_count(), 3)

    def test_key_quota_and_bad_request_errors_stop_without_fallback(self):
        for status in (400, 401, 403, 429):
            context, post = self.run_responses([response({}, status=status)])
            with context, self.assertLogs(gemini.logger, level="ERROR"), self.assertRaises(gemini.AIUnavailable):
                gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
            self.assertEqual(post.call_count, 1)

    def test_bot_daily_cap_is_checked_for_every_fallback_attempt(self):
        context, post = self.run_responses([response({}, status=503)] * 3)
        with patch.dict(os.environ, {"AI_DAILY_LIMIT": "2"}), context, self.assertLogs(gemini.logger, level="ERROR"):
            with self.assertRaises(gemini.AIUnavailable) as error:
                gemini.generate_json("tutor", "lesson", lessons.SpanishLesson)
        self.assertIn("Today's AI request limit", str(error.exception))
        self.assertEqual(post.call_count, 2)
        self.assertEqual(self.request_count(), 2)

    def test_missing_model_and_timeout_try_next_model(self):
        for first in (response({}, status=404), httpx.ReadTimeout("timeout")):
            context, post = self.run_responses([first, response(LESSON_DATA)])
            with context, self.assertLogs(gemini.logger, level="WARNING"):
                self.assertEqual(gemini.generate_json("tutor", "lesson", lessons.SpanishLesson), lesson())
            self.assertEqual(post.call_count, 2)

    def test_configuration_deduplicates_and_limits_attempts(self):
        with patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "gemini-3.8-flash,gemini-3.7-flash,gemini-3.7-flash,gemini-3.6-flash"}):
            self.assertEqual(gemini.model_sequence(), gemini.FREE_TIER_MODELS)
        with patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "none"}):
            self.assertEqual(gemini.model_sequence(), ("gemini-3.8-flash",))
        with patch.dict(os.environ, {"GEMINI_FALLBACK_MODELS": "unverified-model"}):
            with self.assertRaises(gemini.AIUnavailable):
                gemini.model_sequence()
