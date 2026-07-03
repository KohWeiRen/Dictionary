# Dictionary Bot

A Telegram bot that

- sends a **daily pair of words** — a *novel* English word and a *simple* Spanish
  word (for language learning), each with a definition and a real usage example, and
- answers **on-demand lookups** in English and Spanish.

All definitions come from **Merriam-Webster** (Collegiate for English, Spanish-English
for Spanish). English usage examples fall back to [dictionaryapi.dev](https://dictionaryapi.dev)
when Merriam-Webster has none.

## Bot commands

| Command | What it does |
| --- | --- |
| `/en <word>` | English definition + pronunciation, audio and an example |
| `/es <palabra>` | Spanish → English definition with bilingual examples |
| `/word` (or `/daily`) | Today's daily English + Spanish words, on demand |
| `/help` (or `/start`) | Show the command list |
| `/dict <word>` | Alias for `/en` |

Near-misses return a short "did you mean …" list instead of an error.

## How the daily word is chosen

`wordfreq` ranks words most-common-first. Instead of the raw rarest tail (which is
full of proper nouns and junk that isn't in the dictionary), we pick from a **rank
band** per language:

- **English** → a rarer band (`EN_BAND`), so the word is *novel* but real.
- **Spanish** → a common band (`ES_BAND`), so the word is *simple* and learner-friendly.

Each candidate is validated against Merriam-Webster and only accepted if it:

1. returns real entries (not "did you mean" suggestions),
2. isn't flagged offensive,
3. has an actual definition (not a bare variant/inflection), and
4. isn't a suffix/prefix/abbreviation/symbol.

For **English** the *displayed headword* must itself be uncommon
(`EN_NOVEL_MIN_RANK`) — otherwise a rare inflection like `competes` would resolve to
the common lemma `compete`. Words that come with a usage example are preferred so the
daily word is shown in an everyday sentence. If every random draw misses, a small
curated fallback list guarantees a real word.

Tuning knobs live at the top of [get_random_word_daily.py](get_random_word_daily.py).

## Project layout

| File | Purpose |
| --- | --- |
| [get_random_word_daily.py](get_random_word_daily.py) | Daily-word picker + message builder (run by GitHub Actions) |
| [app.py](app.py) | FastAPI Telegram webhook (the interactive commands) |
| [Custom_modules/dictionary_api_v2.py](Custom_modules/dictionary_api_v2.py) | Merriam-Webster fetchers, parsing and formatting |
| [Custom_modules/telegram_bot.py](Custom_modules/telegram_bot.py) | Telegram send/bot helpers |
| [important_info/API_loader.py](important_info/API_loader.py) | Loads secrets from `important_info/.env` |
| [.github/workflows/get_random_word_daily.yml](.github/workflows/get_random_word_daily.yml) | Daily cron that sends the word pair |

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create `important_info/.env` (loaded by `API_loader`):
   ```env
   TELEGRAM_API_KEY=...
   BOT_OWNER_ID=...                        # numeric chat id the daily word is sent to
   MERRIAM_WEBSTER_DICT_API=...            # Collegiate (English) key
   MERRIAM_WEBSTER_SPANISH_DICT_API=...    # Spanish-English key
   WEBHOOK_SECRET=...                      # path secret for the webhook (see below)
   ```
   The same values must be set as **GitHub Actions secrets** for the daily workflow.

## Running

- **Daily word** (also runs automatically via GitHub Actions):
  ```bash
  python get_random_word_daily.py
  ```
- **Webhook server** (for the interactive commands):
  ```bash
  uvicorn app:app --host 0.0.0.0 --port 8000
  ```
  Then point Telegram at it (the `WEBHOOK_SECRET` is part of the URL path):
  ```bash
  curl "https://api.telegram.org/bot<TELEGRAM_API_KEY>/setWebhook?url=https://<your-host>/telegram/<WEBHOOK_SECRET>"
  ```
