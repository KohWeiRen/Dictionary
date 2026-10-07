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
