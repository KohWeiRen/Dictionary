# Update the existing GitHub Actions + Render bot

Keep the existing Telegram bot, GitHub repository and Render web service.
GitHub Actions sends daily English vocabulary + a Gemini Spanish lesson + normal
and slow dialogue audio. Telegram commands go to Render's existing FastAPI
webhook. Both use the same PostgreSQL database for lessons and conversation.

## 1. Create persistent storage

Create a free PostgreSQL project at [Neon](https://console.neon.tech/). Use a
dedicated project/database for this bot and choose a region close to your Render
service. In the project's **Connect** dialog, copy the **pooled PostgreSQL
connection string**, including its SSL query parameters. This is `DATABASE_URL`.
It contains a database password: store it as a secret, never commit it.

An existing PostgreSQL provider also works. You do not need a separate Neon API
key or a manual SQL import. The bot creates its tables automatically. The
database role must be able to create tables and read/write them in its default
schema. The provider's free storage/compute limits still apply.

Render databases are separate resources from web services. Its free PostgreSQL
database expires after 30 days, so it is unsuitable for ongoing lesson history.

## 2. Add two GitHub Actions secrets

Open the repository **Settings → Secrets and variables → Actions → New repository
secret**:

| Secret | Value |
| --- | --- |
| `GEMINI_API_KEY` | Your Google AI Studio key |
| `DATABASE_URL` | The pooled database connection string |

Keep the existing `TELEGRAM_API_KEY`, `BOT_OWNER_ID`,
`MERRIAM_WEBSTER_DICT_API` and `MERRIAM_WEBSTER_SPANISH_DICT_API` secrets.
`BOT_OWNER_ID` is the numeric chat ID that receives daily posts and can practice.

Optional Actions **Variables**:

| Variable | Default |
| --- | --- |
| `GEMINI_MODEL` | `gemini-3.8-flash` |
| `GEMINI_FALLBACK_MODELS` | Unset: tries the remaining models from `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`; use `none` to disable |
| `SPANISH_LEVEL` | `A1` |
| `AI_DAILY_LIMIT` | `20` attempted AI calls per Singapore day |
| `LEARNING_START_DATE` | `2026-10-07`, anchors the English rotation |

Use matching values on Render. `/level` saves a level in the database and
overrides the environment's initial level for subsequent lessons/practice.

## 3. Update the existing Render service

In that service's **Environment**, add the same `GEMINI_API_KEY` and
`DATABASE_URL`, plus `REQUIRE_DATABASE=true`. Keep its existing Telegram,
dictionary and `WEBHOOK_SECRET` settings. The Gemini key itself is not configured
inside source code.

In service settings:

```text
Build command: pip install -r requirements.txt
Start command: uvicorn app:app --host 0.0.0.0 --port $PORT
Health check path: /
Python: 3.13.5 (or another compatible Python 3.13 release)
```

`render.yaml` records these settings as a reference. Update your existing
service; there is no need to import it as a new Blueprint or create another bot.
The application initializes the database at startup and fails clearly if the
cloud database is missing/unreachable. It never falls back to ephemeral SQLite
on Render.

## 4. Publish the code and verify

After configuring both hosts, commit and push the updated code to the branch
used by the daily workflow and Render. GitHub's new **Bot checks** workflow runs
offline checks plus real PostgreSQL integration tests against a disposable CI
database; it uses no real AI or Telegram keys. Deploy the updated revision on
Render using its existing deployment integration or **Manual Deploy → Deploy
latest commit**. This code is prepared locally; creating files does not deploy it.

The Telegram webhook URL and secret stay the same, so no `setWebhook` call is
needed. Do not run `run_bot.py`: polling would remove that webhook.

When Render shows the updated deployment as live:

1. Send `/help` to the bot: the new lesson/practice commands should appear.
2. Send `/lesson`: receive today's generated lesson and two audio tracks.
3. Reply with Spanish text or a voice note: receive a correction and follow-up.
4. Send `/answer`: reveal today's exercise answer without another AI call.
5. In GitHub **Actions → Daily vocabulary and Spanish practice → Run workflow**,
   trigger one test delivery. It reuses today's lesson from the shared database.

The schedule is preserved: `0 22 * * *` targets 06:00 Asia/Singapore. GitHub can
delay scheduled jobs. Daily delivery is recorded in PostgreSQL, so a rerun skips
a completed post. `/word` can still replay it on demand. For an intentional
repeat from a configured terminal, run `python get_random_word_daily.py --force`.

## Optional local verification

Add the same settings to your existing `important_info/.env` (Git-ignored).
Then, with the project's virtual environment active:

```bash
pip install -r requirements.txt
python get_random_word_daily.py --check
python -m Custom_modules.learning_store
python get_random_word_daily.py --preview
```

`--check` checks settings without network calls. The storage command creates/checks
tables without calling Gemini or sending messages. `--preview` generates and
saves a lesson when needed, and prints it without sending Telegram messages.
Local preview and Render will share today's lesson if their database URL matches.

## Expected limits and recovery

For a failed Spanish lesson, run **GitHub Actions → Diagnose Spanish AI → Run
workflow**. It reports `STORAGE OK/FAILED` separately from `GEMINI OK/FAILED`,
uses up to three uncached AI attempts (each counted against the shared daily cap), and sends no
Telegram messages. The equivalent configured-terminal command is:

```bash
python -m Custom_modules.diagnose
```

Gemini errors now include a category and reference ID in Telegram. Find the
matching `gemini_failure` JSON log line in Render for on-demand commands, or in
GitHub Actions for daily jobs. HTTP errors show the provider status and sanitized
reason; validation errors show field paths and constraint types. Prompts and
generated lesson text are not logged. Credentials and URLs in provider error
messages are redacted. Categories distinguish `request` (e.g. HTTP 400), `auth`,
`model`, `quota`, `provider`, `timeout`, `network`, malformed responses and failed
lesson validation. A webhook's HTTP 200 only acknowledges Telegram's update.
It does not mean the background Gemini request succeeded.

Temporary provider failures (including HTTP 503), timeouts, missing models and
invalid lesson output trigger model fallback. The default order is
`gemini-3.8-flash` → `gemini-3.7-flash` → `gemini-3.6-flash`, with one attempt per
model and at most three total. Each supports the same structured lesson request
and currently has free-tier pricing. Authentication, invalid-request and provider
quota errors stop immediately. A successful fallback logs `gemini_fallback_success`
with the selected model and the same reference ID as previous failures. No extra
environment setting is required to activate fallback after deploying this code.

- Render Free still sleeps after idle time. The first command may take about a
  minute to reach the application; the database preserves history through wakeups.
- Gemini generation and speech synthesis require external services. Free AI
  quota is limited. The shared request cap applies across both hosts, including
  failed AI attempts. `/lesson` replay and `/answer` use saved content.
- Audio files are a disposable cache in temporary storage. Text and lesson
  history do not depend on those files surviving a restart; `/listen` regenerates
  normal/slow speech without another Gemini request.
- Repeated Telegram update IDs are suppressed in the database. Background
  processing is best effort: an accepted update can be lost if the Render process
  stops during work. Resend the command in that case. A durable queue is not
  included in this personal bot.
- Delivery recording prevents normal reruns from duplicating a post. A network
  failure after Telegram accepts a message but before its delivery is recorded
  can still cause a duplicate on retry; Telegram sends are not transactional with
  PostgreSQL.

Sources: [Render FastAPI deployment](https://render.com/docs/deploy-fastapi),
[Render Free limitations](https://render.com/docs/free),
[Neon connection instructions](https://neon.com/docs/get-started/connect-neon),
[Gemini free-tier pricing](https://ai.google.dev/gemini-api/docs/pricing).
