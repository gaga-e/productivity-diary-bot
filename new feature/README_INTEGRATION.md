# Job Search Feature — Integration Guide

## What's here
- `config_job.py` — all settings (API keys, timeouts, cache duration, cooldown). Merge into your `config.py`.
- `job_scraper.py` — the scraping engine. Runs LinkedIn+Indeed (via `jobspy`), RemoteOK, Adzuna, and Jooble **concurrently**, tolerates individual source failures, dedupes on title+company+location, filters to last 24h, returns top 50.
- `job_cache.py` — 15-min result cache (avoids re-scraping identical searches) + per-user 60s cooldown.
- `handlers_job.py` — the `/job` command and pagination button handler.
- `requirements_additions.txt` — new pip packages needed.

## Setup steps

1. **Install dependencies**
   ```bash
   pip install -r requirements_additions.txt
   ```

2. **Get free API keys**
   - Adzuna: sign up at https://developer.adzuna.com/ → get `APP_ID` + `APP_KEY`.
   - Jooble: request a free key at https://jooble.org/api/about (they email it to you).
   - Set them as environment variables on your EC2 instance (or a `.env` file):
     ```bash
     export ADZUNA_APP_ID="..."
     export ADZUNA_APP_KEY="..."
     export ADZUNA_COUNTRY="us"   # or gb, ca, au, de, fr...
     export JOOBLE_API_KEY="..."
     ```
   - No key needed for RemoteOK (public) or jobspy/LinkedIn/Indeed (scraped directly, free).

3. **Copy files into your project** alongside your existing `bot.py`, `handlers.py`, `config.py`.

4. **Register the handlers in `bot.py`**
   ```python
   from telegram.ext import CommandHandler, CallbackQueryHandler
   from handlers_job import cmd_job, btn_job_page

   application.add_handler(CommandHandler("job", cmd_job))
   application.add_handler(CallbackQueryHandler(btn_job_page, pattern=r"^jobpage:"))
   ```

5. **Deploy** on your existing always-on EC2 process (polling or webhook). This is NOT designed for Lambda — the in-memory cache and cooldown tracker in `job_cache.py` need a persistent process to work. If you scale to multiple bot instances later, swap `job_cache.py`'s dict-based storage for Redis (same function signatures).

## Usage
```
/job python backend developer
/job "senior site reliability engineer"
/job python backend --loc="United Kingdom"
```
- Everything after `/job` is treated as ONE search phrase (best practice for these APIs) — not split into separate OR'd keywords.
- Quotes preserve an exact phrase.
- `--loc="..."` sets location; defaults to `Remote`.
- Results: last 24h, top 50, paginated 10 at a time with Prev/Next buttons.
- A cooldown (60s, configurable) prevents rapid repeat searches from tripping LinkedIn/Indeed's bot detection.
- Repeating the exact same search within 15 minutes returns cached results instantly instead of re-scraping.

## Honest limitation (by design, since this is fully free)
LinkedIn and Indeed have no free official API — `jobspy` scrapes them directly, so those two sources will occasionally return fewer results or nothing on a given run if they're rate-limiting that moment. Every result message ends with a status line like:
```
✅ 3/4 sources responded (LinkedIn/Indeed had no results or timed out)
```
so you always know whether a thin result set means "no jobs posted" or "a source had a bad run." Adzuna, Jooble, and RemoteOK are the reliable backbone and essentially never fail.

## Suggested next test before going live
Run once manually with real keys to confirm all 4 source paths work end to end:
```python
from job_scraper import scrape_all_boards
jobs, status = scrape_all_boards("python backend developer", "Remote", hours=24, limit=50)
print(status)
print(len(jobs), "jobs found")
print(jobs[0] if jobs else "none")
```
