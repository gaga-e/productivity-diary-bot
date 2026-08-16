"""
=============================================================================
 Standalone Multi-Source Job Scraper
=============================================================================
 Features:
   - Scrapes LinkedIn, Indeed, RemoteOK, Adzuna, and Jooble concurrently.
   - Remote-First Prioritization: Remote/WFH jobs automatically rank at the top.
   - Deduplication: Eliminates duplicate postings across multiple job boards.
   - Robust Error & Timeout Handling: Never crashes if one site is slow/blocked.
   - Date Formatting: Includes exact/relative date posted for each listing.

 Requirements:
   pip install requests python-jobspy pandas beautifulsoup4 markdownify tls-client

 Usage as a CLI script:
   python send_to_friend_job_scraper.py "product designer"
   python send_to_friend_job_scraper.py "backend engineer" --loc="United Kingdom" --hours=72

 Usage in code:
   from send_to_friend_job_scraper import scrape_all_boards
   jobs, status = scrape_all_boards("product designer", location="Remote")
=============================================================================
"""

import os
import re
import sys
import json
import shlex
import time
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Defaults
DEFAULT_LOCATION = "Remote"
DEFAULT_LOOKBACK_HOURS = 168  # 7 days
DEFAULT_RESULT_LIMIT = 50
DEFAULT_TIMEOUT_SECONDS = 45
JOBSPY_SITE_NAMES = ["linkedin", "indeed"]
SOURCES = ["linkedin_indeed", "remoteok", "adzuna", "jooble"]


# --------------------------------------------------------------------------
# Query Parsing
# --------------------------------------------------------------------------
def parse_job_query(raw_args: str):
    """
    Parses a search string into (keywords, location).
    Supports:
      "python backend developer"
      "senior backend engineer" --loc="United Kingdom"
    """
    if not raw_args or not raw_args.strip():
        return None, DEFAULT_LOCATION

    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        tokens = raw_args.split()

    location = DEFAULT_LOCATION
    keyword_tokens = []
    for tok in tokens:
        m = re.match(r"^--loc=(.*)$", tok)
        if m:
            location = m.group(1).strip() or DEFAULT_LOCATION
        else:
            keyword_tokens.append(tok)

    keywords = " ".join(keyword_tokens).strip()
    return (keywords or None), location


# --------------------------------------------------------------------------
# Per-Source Fetchers
# --------------------------------------------------------------------------
def fetch_jobspy_jobs(keywords: str, location: str, hours: int, limit: int = DEFAULT_RESULT_LIMIT):
    """Scrapes LinkedIn and Indeed using python-jobspy."""
    try:
        import pandas as pd
        from jobspy import scrape_jobs

        hours_old = hours if hours else 24
        is_remote_search = bool(location and "remote" in location.lower())

        results_list = []
        countries = ["UK", "South Africa", "USA"] if is_remote_search else ["USA"]
        per_country_limit = max(10, limit // len(countries))

        for ctry in countries:
            try:
                df = scrape_jobs(
                    site_name=JOBSPY_SITE_NAMES,
                    search_term=keywords,
                    location=location if not is_remote_search else None,
                    is_remote=is_remote_search,
                    results_wanted=per_country_limit,
                    hours_old=hours_old,
                    country_indeed=ctry,
                )
                if df is not None and not df.empty:
                    df = df.where(pd.notnull(df), None)
                    results_list.extend(df.to_dict("records"))
            except Exception as e:
                logger.warning("JobSpy scraper error for country %s: %s", ctry, e)

        return results_list
    except ImportError:
        logger.warning("python-jobspy is not installed. Run: pip install python-jobspy")
        raise RuntimeError("Missing python-jobspy dependency")
    except Exception as e:
        logger.warning("JobSpy scraper error: %s", e)
        raise e


def fetch_remoteok_jobs(keywords: str, hours: int):
    """Fetches remote jobs from RemoteOK public JSON API."""
    resp = requests.get(
        "https://remoteok.com/api",
        headers={"User-Agent": "Mozilla/5.0 (JobScraperScript/1.0)"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return []
    data = data[1:]  # skip legal header

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    terms = [t.lower() for t in keywords.split()] if keywords else []
    out = []
    for job in data:
        epoch = job.get("epoch")
        if epoch:
            posted = datetime.fromtimestamp(epoch, tz=timezone.utc)
            if posted < cutoff:
                continue
        haystack = f"{job.get('position', '')} {job.get('company', '')} {' '.join(job.get('tags', []))}".lower()
        if terms and not any(t in haystack for t in terms):
            continue
        out.append(job)
    return out


def fetch_adzuna_jobs(keywords: str, location: str, hours: int, limit: int = DEFAULT_RESULT_LIMIT):
    """Fetches jobs from Adzuna API (requires ADZUNA_APP_ID & ADZUNA_APP_KEY in env)."""
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("Adzuna not configured (missing ADZUNA_APP_ID / ADZUNA_APP_KEY env vars)")

    max_days_old = max(1, round(hours / 24))
    country = os.getenv("ADZUNA_COUNTRY", "us")
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": limit,
        "what": keywords,
        "where": location,
        "max_days_old": max_days_old,
    }
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def fetch_jooble_jobs(keywords: str, location: str, hours: int):
    """Fetches jobs from Jooble API (requires JOOBLE_API_KEY in env)."""
    api_key = os.getenv("JOOBLE_API_KEY")
    if not api_key:
        raise RuntimeError("Jooble not configured (missing JOOBLE_API_KEY env var)")

    payload = {"keywords": keywords, "location": location, "page": 1}
    url = f"https://jooble.org/api/{api_key}"
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    jobs = data.get("jobs", [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for j in jobs:
        updated_str = j.get("updated")
        if updated_str:
            try:
                dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                if dt < cutoff:
                    continue
            except Exception:
                pass
        out.append(j)
    return out


# --------------------------------------------------------------------------
# Normalization & Helpers
# --------------------------------------------------------------------------
def _to_iso(dt) -> str:
    """Converts raw date input into ISO formatted string safely."""
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        import math
        import pandas as pd
        if pd.isna(dt) or (isinstance(dt, float) and math.isnan(dt)):
            return datetime.now(timezone.utc).isoformat()
    except Exception:
        pass
    if isinstance(dt, (int, float)):
        try:
            return datetime.fromtimestamp(dt, tz=timezone.utc).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()
    if isinstance(dt, str):
        return dt
    try:
        return dt.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def format_date_posted(date_str: str) -> str:
    """Formats ISO date string into human readable date (e.g. 'Aug 14, 2026')."""
    if not date_str:
        return "Recent"
    try:
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%b %d, %Y")
        return str(date_str)[:10]
    except Exception:
        return str(date_str)[:10] if len(str(date_str)) >= 10 else "Recent"


def normalize_job(raw: dict, source: str) -> dict:
    """Normalizes job data into a unified dictionary structure."""
    if source in ("linkedin", "indeed"):
        return {
            "title": str(raw.get("title") or "").strip(),
            "company": str(raw.get("company") or "").strip(),
            "location": str(raw.get("location") or "").strip(),
            "link": raw.get("job_url") or raw.get("job_url_direct") or "",
            "source": source,
            "date_posted": _to_iso(raw.get("date_posted")),
        }
    if source == "remoteok":
        return {
            "title": str(raw.get("position") or "").strip(),
            "company": str(raw.get("company") or "").strip(),
            "location": str(raw.get("location") or "Remote").strip(),
            "link": raw.get("url") or f"https://remoteok.com/remote-jobs/{raw.get('id', '')}",
            "source": "remoteok",
            "date_posted": _to_iso(raw.get("epoch")),
        }
    if source == "adzuna":
        return {
            "title": str(raw.get("title") or "").strip(),
            "company": str((raw.get("company") or {}).get("display_name") or "").strip(),
            "location": str((raw.get("location") or {}).get("display_name") or "").strip(),
            "link": raw.get("redirect_url") or "",
            "source": "adzuna",
            "date_posted": _to_iso(raw.get("created")),
        }
    if source == "jooble":
        return {
            "title": str(raw.get("title") or "").strip(),
            "company": str(raw.get("company") or "").strip(),
            "location": str(raw.get("location") or "").strip(),
            "link": raw.get("link") or "",
            "source": "jooble",
            "date_posted": _to_iso(raw.get("updated")),
        }
    raise ValueError(f"Unknown source: {source}")


def job_hash(job: dict) -> str:
    """Generates unique hash based on title, company, and location for deduplication."""
    key = f"{job['title'].lower().strip()}|{job['company'].lower().strip()}|{job['location'].lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def dedupe_jobs(jobs: list) -> list:
    """Removes duplicate job listings."""
    seen = set()
    out = []
    for j in jobs:
        h = job_hash(j)
        if h not in seen:
                    seen.add(h)
            out.append(j)
    return out


EMEA_AFRICA_KEYWORDS = [
    "emea", "africa", "uk", "united kingdom", "london", "england", "south africa",
    "nigeria", "lagos", "kenya", "nairobi", "egypt", "cairo", "ghana", "accra",
    "germany", "berlin", "france", "paris", "netherlands", "amsterdam", "spain",
    "madrid", "barcelona", "uae", "dubai", "united arab emirates", "saudi",
    "ireland", "dublin", "switzerland", "zurich", "poland", "sweden", "stockholm",
    "estonia", "portugal", "lisbon", "europe"
]

WORLDWIDE_REMOTE_KEYWORDS = [
    "worldwide", "anywhere", "global", "remote - worldwide", "remote (worldwide)",
    "work from anywhere", "remoteok", "emea", "africa"
]

US_ONLY_RESTRICTIONS = [
    "us only", "usa only", "us citizens", "must reside in us", "must be in us",
    "us-based only", "united states only", "must be located in the us"
]


def _is_us_restricted(job: dict) -> bool:
    text = f"{job.get('title', '')} {job.get('location', '')}".lower()
    return any(req in text for req in US_ONLY_RESTRICTIONS)


def _region_score(job: dict) -> int:
    """
    Ranks jobs by regional relevance:
      3: Fully Remote Worldwide / Global / EMEA / Africa
      2: Specific EMEA & Africa location/region
      1: General Remote / Unspecified
      0: Restricted US-Only local/onsite role
    """
    loc = (job.get("location") or "").lower()
    title = (job.get("title") or "").lower()
    src = (job.get("source") or "").lower()
    haystack = f"{title} {loc} {src}"

    if _is_us_restricted(job):
        return 0

    if any(w in haystack for w in WORLDWIDE_REMOTE_KEYWORDS):
        return 3

    if any(k in haystack for k in EMEA_AFRICA_KEYWORDS):
        return 2

    if _is_remote(job):
        return 1

    return 0


def _is_remote(job: dict) -> bool:
    """Checks if a job is remote or work-from-home."""
    loc = (job.get("location") or "").lower()
    title = (job.get("title") or "").lower()
    source = (job.get("source") or "").lower()
    return (
        "remote" in loc or
        "worldwide" in loc or
        "anywhere" in loc or
        "work from home" in loc or
        "wfh" in loc or
        "telecommute" in loc or
        "remote" in title or
        source == "remoteok"
    )


# --------------------------------------------------------------------------
# Main Orchestrator
# --------------------------------------------------------------------------
def _run_source(name: str, keywords: str, location: str, hours: int, limit: int):
    if name == "linkedin_indeed":
        raw = fetch_jobspy_jobs(keywords, location, hours, limit)
        return [normalize_job(r, r.get("site", "linkedin")) for r in raw]
    if name == "remoteok":
        raw = fetch_remoteok_jobs(keywords, hours)
        return [normalize_job(r, "remoteok") for r in raw]
    if name == "adzuna":
        raw = fetch_adzuna_jobs(keywords, location, hours, limit)
        return [normalize_job(r, "adzuna") for r in raw]
    if name == "jooble":
        raw = fetch_jooble_jobs(keywords, location, hours)
        return [normalize_job(r, "jooble") for r in raw]
    raise ValueError(name)


def scrape_all_boards(keywords: str, location: str = None, hours: int = None, limit: int = None, timeout: int = None):
    """
    Main function to scrape all job boards concurrently.

    Parameters:
      keywords (str): Job title or keywords (e.g. "Product Designer")
      location (str): Target location (default: "Remote")
      hours (int): Lookback window in hours (default: 168 = 7 days)
      limit (int): Maximum number of results to return (default: 50)
      timeout (int): Overall search timeout in seconds (default: 45)

    Returns:
      (jobs: list[dict], status: dict)
    """
    location = location or DEFAULT_LOCATION
    hours = hours or DEFAULT_LOOKBACK_HOURS
    limit = limit or DEFAULT_RESULT_LIMIT
    timeout = timeout or DEFAULT_TIMEOUT_SECONDS

    results = []
    status = {name: "timeout" for name in SOURCES}

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = {
            executor.submit(_run_source, name, keywords, location, hours, limit): name
            for name in SOURCES
        }
        deadline = time.monotonic() + timeout
        try:
            for future in as_completed(futures, timeout=timeout):
                name = futures[future]
                try:
                    jobs = future.result(timeout=max(0.1, deadline - time.monotonic()))
                    results.extend(jobs)
                    status[name] = "ok"
                except Exception as e:
                    logger.warning("Source %s failed/skipped: %s", name, e)
                    status[name] = f"failed: {e}"
        except TimeoutError:
            logger.warning("Search reached timeout (%ds); returning partial results.", timeout)
            for future, name in futures.items():
                if future.done() and status[name] == "timeout":
                    try:
                        jobs = future.result()
                        results.extend(jobs)
                        status[name] = "ok"
                    except Exception as e:
                        status[name] = f"failed: {e}"

    # Deduplicate and sort: EMEA / Africa / Worldwide Remote first (3 > 2 > 1 > 0), then newest-first
    results = dedupe_jobs(results)
    results.sort(key=lambda j: (_region_score(j), j["date_posted"]), reverse=True)
    return results[:limit], status


# --------------------------------------------------------------------------
# CLI Entry Point
# --------------------------------------------------------------------------
if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if len(sys.argv) < 2:
        print("Usage: python send_to_friend_job_scraper.py \"<keywords>\" [--loc=\"<location>\"] [--hours=<hours>]")
        print("Example: python send_to_friend_job_scraper.py \"product designer\" --loc=\"Remote\"")
        sys.exit(1)

    raw_input = " ".join(sys.argv[1:])
    keywords, location = parse_job_query(raw_input)

    print(f"\n[SEARCH] Searching live job boards for: '{keywords}' | Location: '{location}'...\n")
    start_time = time.time()
    jobs, status = scrape_all_boards(keywords, location=location)
    elapsed = round(time.time() - start_time, 2)

    print(f"=== BOARD RESPONSES ({elapsed}s) ===")
    for source, st in status.items():
        icon = "[OK]" if st == "ok" else "[WARN]"
        print(f"  {icon} {source}: {st}")

    print(f"\n=== FOUND {len(jobs)} TOTAL JOBS (Remote-Prioritized & Deduped) ===")
    for idx, j in enumerate(jobs, 1):
        formatted_date = format_date_posted(j.get('date_posted'))
        print(f"{idx}. {j.get('title')} at {j.get('company')} ({j.get('location')})")
        print(f"   [POSTED] Date: {formatted_date} | Source: {j.get('source')}")
        print(f"   [LINK] {j.get('link')}\n")
