"""Cloud storage and webhook behavior; PostgreSQL tests use a dedicated CI DB."""
import os
from urllib.parse import urlparse
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import test_learning as _learning
from test_learning import IsolatedStore, TODAY, store, app, daily


class CloudStorageTests(IsolatedStore, unittest.TestCase):
    def test_cloud_requires_shared_database(self):
        for settings in ({"RENDER": "true"}, {"REQUIRE_DATABASE": "true"}):
            with patch.dict(os.environ, settings), self.assertRaises(store.StoreUnavailable):
                store.initialize()

    def test_invalid_url_cannot_fall_back_to_local_storage(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///local.db"}), self.assertRaises(store.StoreUnavailable):
            store.initialize()

    def test_postgres_error_does_not_expose_credentials(self):
        import psycopg
        url = "postgresql://user:private-password@missing.invalid/dictionary"
        with patch.dict(os.environ, {"DATABASE_URL": url}), patch.object(psycopg, "connect", side_effect=psycopg.OperationalError(url)):
            with self.assertRaises(store.StoreUnavailable) as error:
                store.initialize()
        self.assertNotIn("private-password", str(error.exception))

    def test_pooler_connection_and_bound_parameters(self):
        import psycopg
        url = "postgresql://user:password@test.invalid/dictionary"
        connection = MagicMock()
        store._initialized.discard(url)
        with patch.dict(os.environ, {"DATABASE_URL": url}), patch.object(psycopg, "connect", return_value=connection) as connect:
            store.save_lesson(123, TODAY, "learner's question? %s")
        self.assertIsNone(connect.call_args.kwargs["prepare_threshold"])
        insert = next(call for call in connection.execute.call_args_list if call.args[0].startswith("INSERT INTO lessons"))
        self.assertNotIn("learner's", insert.args[0])
        self.assertEqual(insert.args[1][-1], "learner's question? %s")
        connection.close.assert_called_once()

    def test_delivery_record_shared_and_failed_delivery_can_retry(self):
        self.assertTrue(store.claim_delivery(123, TODAY))
        self.assertFalse(store.claim_delivery(123, TODAY))
        store.finish_delivery(123, TODAY, False)
        self.assertTrue(store.claim_delivery(123, TODAY))
        store.finish_delivery(123, TODAY)
        self.assertFalse(store.claim_delivery(123, TODAY))
        self.assertTrue(store.claim_delivery(456, TODAY))

    def test_telegram_retries_do_not_repeat_completed_update(self):
        self.assertTrue(store.claim_update(10))
        self.assertFalse(store.claim_update(10))
        store.finish_update(10, False)
        self.assertTrue(store.claim_update(10))
        store.finish_update(10)
        self.assertFalse(store.claim_update(10))

    def test_webhook_duplicate_update_dispatches_once(self):
        from fastapi.testclient import TestClient
        body = {"update_id": 11, "message": {"chat": {"id": 123, "type": "private"}, "text": "/lesson"}}
        with patch.object(app, "dispatch_text", new_callable=AsyncMock) as dispatch, TestClient(app.app) as client:
            self.assertEqual(client.post("/telegram/test-secret", json=body).status_code, 200)
            self.assertEqual(client.post("/telegram/test-secret", json=body).status_code, 200)
        dispatch.assert_awaited_once()

    def test_storage_outage_before_acceptance_requests_telegram_retry(self):
        from fastapi.testclient import TestClient
        body = {"update_id": 12, "message": {"chat": {"id": 123}, "text": "/lesson"}}
        with patch.object(store, "claim_update", side_effect=store.StoreUnavailable("storage down")), patch.object(app, "dispatch_text", new_callable=AsyncMock) as dispatch:
            self.assertEqual(TestClient(app.app).post("/telegram/test-secret", json=body).status_code, 503)
        dispatch.assert_not_called()


class CloudDeliveryTests(IsolatedStore, unittest.IsolatedAsyncioTestCase):
    async def test_rerunning_daily_job_does_not_send_duplicate(self):
        with patch.object(daily, "build_daily_bundle", return_value=daily.DailyPractice("lesson")), patch.object(daily, "_send_async", new_callable=AsyncMock) as send:
            await daily.send_daily(TODAY)
            await daily.send_daily(TODAY)
            send.assert_awaited_once()
            await daily.send_daily(TODAY, force=True)
            self.assertEqual(send.await_count, 2)

    async def test_daily_send_failure_releases_delivery_for_retry(self):
        with patch.object(daily, "build_daily_bundle", return_value=daily.DailyPractice("lesson")), patch.object(daily, "_send_async", new_callable=AsyncMock, side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                await daily.send_daily(TODAY)
        self.assertTrue(store.claim_delivery(123, TODAY))


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "PostgreSQL integration runs in CI")
class PostgresIntegrationTests(_learning.AIIntegrationTests):
    """Run the same lesson/cache/history/concurrency scenarios on real PostgreSQL."""
    def setUp(self):
        url = os.environ["TEST_DATABASE_URL"]
        if urlparse(url).path != "/dictionary_test":
            self.fail("Integration tests require the dedicated dictionary_test database.")
        super().setUp()
        settings = patch.dict(os.environ, {"DATABASE_URL": url, "REQUIRE_DATABASE": "true"})
        settings.start()
        self.addCleanup(settings.stop)
        store.initialize()
        with store.database() as db:
            for table in ("lessons", "profiles", "history", "requests", "generation_locks", "deliveries", "telegram_updates"):
                db.execute(f"DELETE FROM {table}")

    def test_postgres_delivery_and_update_records(self):
        CloudStorageTests.test_delivery_record_shared_and_failed_delivery_can_retry(self)
        CloudStorageTests.test_telegram_retries_do_not_repeat_completed_update(self)

    def test_cached_lesson_read_by_a_fresh_process(self):
        import subprocess
        import sys
        import tempfile
        store.save_lesson(123, TODAY, _learning.lesson().model_dump_json())
        # A disposable local directory must not affect the shared lesson.
        with tempfile.TemporaryDirectory() as empty_directory:
            result = subprocess.run([sys.executable, "-c",
                "import datetime; from Custom_modules.spanish_lessons import lesson_for_date; "
                "print(lesson_for_date(123, datetime.date(2026, 10, 7)).title)"],
                env={**os.environ, "BOT_DATA_DIR": empty_directory, "GEMINI_API_KEY": ""},
                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, "Fresh-process PostgreSQL replay failed")
        self.assertEqual(result.stdout.strip(), "At a cafe")
