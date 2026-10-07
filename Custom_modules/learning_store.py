"""Shared PostgreSQL history, with SQLite for local development."""
from contextlib import contextmanager
import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from zoneinfo import ZoneInfo
from Custom_modules.gemini_client import AIUnavailable

TIMEZONE = ZoneInfo("Asia/Singapore")
_initialized = set()
_schema_lock = threading.Lock()


class StoreUnavailable(AIUnavailable):
    """Safe storage error without driver messages or credentials."""


def storage_backend() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise StoreUnavailable("DATABASE_URL must be a PostgreSQL connection string.")
        return "postgresql"
    if os.getenv("REQUIRE_DATABASE", "").lower() == "true" or os.getenv("RENDER", "").lower() == "true":
        raise StoreUnavailable("Shared lesson storage isn't configured. Set DATABASE_URL on Render and in GitHub Actions secrets.")
    return "sqlite"


def local_today() -> datetime.date:
    return datetime.datetime.now(TIMEZONE).date()


def data_directory() -> Path:
    if os.getenv("BOT_DATA_DIR"):
        return Path(os.environ["BOT_DATA_DIR"])
    if os.getenv("DATABASE_URL") or os.getenv("RENDER") or os.getenv("GITHUB_ACTIONS"):
        return Path(tempfile.gettempdir()) / "dictionary-bot"
    return Path(__file__).resolve().parents[1] / "data"


class _PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        # Only authored SQL is translated; all learner text is bound separately.
        return self.connection.execute(sql.replace("?", "%s"), parameters)


def _schema(db, postgres=False):
    history_id = "BIGSERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY"
    statements = [
        "CREATE TABLE IF NOT EXISTS lessons (chat TEXT NOT NULL, day TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (chat, day))",
        "CREATE TABLE IF NOT EXISTS profiles (chat TEXT PRIMARY KEY, level TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS history (id {history_id}, chat TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS history_chat_id_idx ON history (chat, id)",
        "CREATE TABLE IF NOT EXISTS requests (day TEXT PRIMARY KEY, count INTEGER NOT NULL)",
        "CREATE TABLE IF NOT EXISTS generation_locks (chat TEXT NOT NULL, day TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, PRIMARY KEY (chat, day))",
        "CREATE TABLE IF NOT EXISTS deliveries (chat TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL, PRIMARY KEY (chat, day))",
        "CREATE TABLE IF NOT EXISTS telegram_updates (id BIGINT PRIMARY KEY, status TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL)",
    ]
    for statement in statements:
        db.execute(statement)


@contextmanager
def database():
    backend = storage_backend()
    connection = None
    driver_errors = (sqlite3.Error, OSError)
    try:
        if backend == "postgresql":
            import psycopg
            from psycopg.rows import dict_row
            driver_errors = (*driver_errors, psycopg.Error)
            url = os.environ["DATABASE_URL"].strip()
            connection = psycopg.connect(url, connect_timeout=15, row_factory=dict_row, prepare_threshold=None)
            db = _PostgresConnection(connection)
            db.execute("SET LOCAL statement_timeout = '20000ms'")
            with _schema_lock:
                if url not in _initialized:
                    db.execute("SELECT pg_advisory_xact_lock(742190613)")
                    _schema(db, postgres=True)
                    connection.commit()
                    _initialized.add(url)
                    db.execute("SET LOCAL statement_timeout = '20000ms'")
        else:
            directory = data_directory()
            directory.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(directory / "learning.sqlite3", timeout=15)
            connection.row_factory = sqlite3.Row
            db = connection
            _schema(db)
        yield db
        connection.commit()
    except ImportError:
        raise StoreUnavailable("PostgreSQL support isn't installed. Reinstall requirements.txt and redeploy.") from None
    except driver_errors:
        raise StoreUnavailable("Learning storage is unavailable. Check DATABASE_URL and the database service, then retry.") from None
    finally:
        if connection is not None:
            connection.close()


def initialize() -> None:
    with database() as db:
        db.execute("SELECT 1")


def cached_lesson(chat: int | str, day: datetime.date) -> str | None:
    with database() as db:
        row = db.execute("SELECT payload FROM lessons WHERE chat=? AND day=?", (str(chat), day.isoformat())).fetchone()
        return row["payload"] if row else None


def save_lesson(chat: int | str, day: datetime.date, payload: str) -> None:
    with database() as db:
        db.execute("INSERT INTO lessons VALUES (?, ?, ?) ON CONFLICT (chat, day) DO NOTHING", (str(chat), day.isoformat(), payload))


def recent_lessons(chat: int | str, before: datetime.date, limit: int = 7) -> list[dict]:
    with database() as db:
        rows = db.execute("SELECT day, payload FROM lessons WHERE chat=? AND day<? ORDER BY day DESC LIMIT ?",
                          (str(chat), before.isoformat(), limit)).fetchall()
        return [dict(row) for row in rows]


def lesson_count(chat: int | str, before: datetime.date) -> int:
    with database() as db:
        return db.execute("SELECT COUNT(*) AS total FROM lessons WHERE chat=? AND day<?", (str(chat), before.isoformat())).fetchone()["total"]


def get_level(chat: int | str) -> str:
    with database() as db:
        row = db.execute("SELECT level FROM profiles WHERE chat=?", (str(chat),)).fetchone()
        level = row["level"] if row else (os.getenv("SPANISH_LEVEL") or "A1").upper()
        if level not in ("A1", "A2", "B1", "B2"):
            raise ValueError("SPANISH_LEVEL must be A1, A2, B1 or B2.")
        return level


def set_level(chat: int | str, level: str) -> None:
    if level not in ("A1", "A2", "B1", "B2"):
        raise ValueError("Choose A1, A2, B1 or B2.")
    with database() as db:
        db.execute("INSERT INTO profiles VALUES (?, ?) ON CONFLICT (chat) DO UPDATE SET level=excluded.level", (str(chat), level))


def reserve_request(day: datetime.date | None = None) -> None:
    """Atomic global cap across Render and scheduled jobs; replay costs nothing."""
    limit = int(os.getenv("AI_DAILY_LIMIT") or "20")
    if limit < 1:
        raise ValueError("AI_DAILY_LIMIT must be positive.")
    day = day or local_today()
    with database() as db:
        row = db.execute("""INSERT INTO requests VALUES (?, 1)
            ON CONFLICT (day) DO UPDATE SET count=requests.count+1
            WHERE requests.count < ? RETURNING count""", (day.isoformat(), limit)).fetchone()
        if row is None:
            raise AIUnavailable("Today's AI request limit is reached. Saved lessons and pronunciation are still available.")


def claim_lesson(chat: int | str, day: datetime.date) -> bool:
    now = time.time()
    with database() as db:
        row = db.execute("""INSERT INTO generation_locks VALUES (?, ?, ?)
            ON CONFLICT (chat, day) DO UPDATE SET expires=excluded.expires
            WHERE generation_locks.expires < ? RETURNING chat""", (str(chat), day.isoformat(), now + 300, now)).fetchone()
        return row is not None


def release_lesson(chat: int | str, day: datetime.date) -> None:
    with database() as db:
        db.execute("DELETE FROM generation_locks WHERE chat=? AND day=?", (str(chat), day.isoformat()))


def conversation(chat: int | str) -> list[dict]:
    with database() as db:
        rows = db.execute("SELECT role, text FROM history WHERE chat=? ORDER BY id DESC LIMIT 12", (str(chat),)).fetchall()
        return [dict(row) for row in reversed(rows)]


def add_exchange(chat: int | str, user: str, assistant: str) -> None:
    with database() as db:
        if isinstance(db, _PostgresConnection):
            db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (str(chat),))
        db.execute("INSERT INTO history (chat, role, text) VALUES (?, ?, ?), (?, ?, ?)",
                   (str(chat), "user", user, str(chat), "assistant", assistant))
        db.execute("DELETE FROM history WHERE chat=? AND id NOT IN (SELECT id FROM history WHERE chat=? ORDER BY id DESC LIMIT 12)",
                   (str(chat), str(chat)))


def reset_conversation(chat: int | str) -> None:
    with database() as db:
        if isinstance(db, _PostgresConnection):
            db.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (str(chat),))
        db.execute("DELETE FROM history WHERE chat=?", (str(chat),))


def claim_delivery(chat: int | str, day: datetime.date) -> bool:
    now = time.time()
    with database() as db:
        row = db.execute("""INSERT INTO deliveries VALUES (?, ?, 'sending', ?)
            ON CONFLICT (chat, day) DO UPDATE SET status='sending', expires=excluded.expires
            WHERE deliveries.status != 'sent' AND deliveries.expires < ? RETURNING chat""",
                         (str(chat), day.isoformat(), now + 600, now)).fetchone()
        return row is not None


def finish_delivery(chat: int | str, day: datetime.date, success=True) -> None:
    with database() as db:
        db.execute("UPDATE deliveries SET status=?, expires=0 WHERE chat=? AND day=?",
                   ("sent" if success else "failed", str(chat), day.isoformat()))


def claim_update(update_id: int) -> bool:
    now = time.time()
    with database() as db:
        db.execute("DELETE FROM telegram_updates WHERE expires < ? AND status='done'", (now - 7 * 86400,))
        row = db.execute("""INSERT INTO telegram_updates VALUES (?, 'processing', ?)
            ON CONFLICT (id) DO UPDATE SET status='processing', expires=excluded.expires
            WHERE telegram_updates.status != 'done' AND telegram_updates.expires < ? RETURNING id""",
                         (update_id, now + 300, now)).fetchone()
        return row is not None


def finish_update(update_id: int, success=True) -> None:
    with database() as db:
        db.execute("UPDATE telegram_updates SET status=?, expires=? WHERE id=?",
                   ("done" if success else "failed", time.time() if success else 0, update_id))


if __name__ == "__main__":
    from important_info import API_loader
    try:
        initialize()
        print(f"Learning storage ready ({storage_backend()}). No AI calls or Telegram messages sent.")
    except StoreUnavailable as error:
        raise SystemExit(str(error)) from None
