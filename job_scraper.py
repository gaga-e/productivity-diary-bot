"""
Multi-source job scraper.

Sources:
  - jobspy (free, open-source) -> LinkedIn + Indeed. Best-effort: these get
    rate-limited/blocked by bot detection sometimes. We never let a failure
    here kill the whole search.
  - Adzuna (free official API) -> reliable.
  - Jooble (free official API) -> reliable.
  - RemoteOK (free public JSON API) -> reliable.

All sources are fetched CONCURRENTLY via a thread pool (they're all blocking
HTTP calls) with a shared timeout, then merged, deduped, filtered to the
lookback window, sorted newest-first, and capped at the result limit.
"""

import hashlib
import logging
import re
import shlex
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

import config as cfg

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Query parsing: "/job python backend --loc=\"United Kingdom\"" -> phrase + loc
# --------------------------------------------------------------------------
def parse_job_query(raw_args: str):
    """
    Parses the free-text portion of /job into (keywords, location).
    Supports:
      /job python backend developer
      /job "senior backend engineer"
      /job python backend --loc="United Kingdom"
    Everything not consumed by --loc= is treated as ONE search phrase
    (best practice for these APIs — a single well-formed phrase beats
    OR-ing loose keywords together).
    """
    if not raw_args or not raw_args.strip():
        return None, cfg.DEFAULT_LOCATION

    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        # unbalanced quotes etc. - fall back to naive split
        tokens = raw_args.split()

    location = cfg.DEFAULT_LOCATION
    keyword_tokens = []
    for tok in tokens:
        m = re.match(r"^--loc=(.*)$", tok)
        if m:
            location = m.group(1).strip() or cfg.DEFAULT_LOCATION
        else:
            keyword_tokens.append(tok)

    keywords = " ".join(keyword_tokens).strip()
    return (keywords or None), location


# --------------------------------------------------------------------------
# Per-source fetchers. Each returns a list of RAW dicts (source-specific shape).
# Each MUST be safe to run in a thread and MUST raise on failure (caller catches).
# --------------------------------------------------------------------------
def fetch_jobspy_jobs(keywords: str, location: str, hours: int):
    from jobspy import scrape_jobs  # imported lazily; heavy dependency

    hours_old = hours if hours else 24
    df = scrape_jobs(
        site_name=cfg.JOBSPY_SITE_NAMES,
        search_term=keywords,
        location=location,
        results_wanted=cfg.JOB_RESULT_LIMIT,
        hours_old=hours_old,
        country_indeed="USA",
    )
    return df.to_dict("records") if df is not None and not df.empty else []


def fetch_remoteok_jobs(keywords: str, hours: int):
    resp = requests.get(
        "https://remoteok.com/api",
        headers={"User-Agent": "Mozilla/5.0 (job-alert-bot)"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return []
    data = data[1:]  # first element is a legal/metadata blob, not a job

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


def fetch_adzuna_jobs(keywords: str, location: str, hours: int):
    if not cfg.ADZUNA_APP_ID or not cfg.ADZUNA_APP_KEY:
        raise RuntimeError("Adzuna not configured (missing APP_ID/APP_KEY)")

    max_days_old = max(1, round(hours / 24))
    params = {
        "app_id": cfg.ADZUNA_APP_ID,
        "app_key": cfg.ADZUNA_APP_KEY,
        "what": keywords,
        "max_days_old": max_days_old,
        "results_per_page": cfg.JOB_RESULT_LIMIT,
        "sort_by": "date",
    }
    if location and location.lower() != "remote":
        params["where"] = location

    url = f"https://api.adzuna.com/v1/api/jobs/{cfg.ADZUNA_COUNTRY}/search/1"
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def fetch_jooble_jobs(keywords: str, location: str, hours: int):
    if not cfg.JOOBLE_API_KEY:
        raise RuntimeError("Jooble not configured (missing API key)")

    url = f"https://jooble.org/api/{cfg.JOOBLE_API_KEY}"
    payload = {"keywords": keywords, "location": "" if location.lower() == "remote" else location}
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])

    # Jooble doesn't support a lookback filter server-side; filter client-side
    # using the "updated" field where present, best-effort.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for j in jobs:
        updated = j.get("updated")
        if updated:
            try:
                posted = datetime.strptime(updated, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if posted < cutoff:
                    continue
            except ValueError:
                pass  # unparseable date -> keep it, don't drop good results over formatting
        out.append(j)
    return out


# --------------------------------------------------------------------------
# Normalization: every source -> {title, company, location, link, source, date_posted}
# --------------------------------------------------------------------------
def _to_iso(dt) -> str:
    if dt is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(dt, (int, float)):
        return datetime.fromtimestamp(dt, tz=timezone.utc).isoformat()
    if isinstance(dt, str):
        return dt
    try:
        return dt.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def normalize_job(raw: dict, source: str) -> dict:
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
    """
    Dedup key is title+company+location, NOT the link — apply links carry
    tracking query params that differ between scrapes of the same posting
    and would otherwise defeat dedup.
    """
    key = f"{job['title'].lower().strip()}|{job['company'].lower().strip()}|{job['location'].lower().strip()}"
    return hashlib.md5(key.encode()).hexdigest()


def dedupe_jobs(jobs: list) -> list:
    seen = set()
    out = []
    for j in jobs:
        h = job_hash(j)
        if h not in seen:
            seen.add(h)
            out.append(j)
    return out


# --------------------------------------------------------------------------
# Orchestration: run every source concurrently, tolerate individual failures,
# return (jobs, source_status) so callers can show a "4/5 sources responded" line.
# --------------------------------------------------------------------------
SOURCES = ["linkedin_indeed", "remoteok", "adzuna", "jooble"]


def _run_source(name: str, keywords: str, location: str, hours: int):
    if name == "linkedin_indeed":
        raw = fetch_jobspy_jobs(keywords, location, hours)
        return [normalize_job(r, r.get("site", "linkedin")) for r in raw]
    if name == "remoteok":
        raw = fetch_remoteok_jobs(keywords, hours)
        return [normalize_job(r, "remoteok") for r in raw]
    if name == "adzuna":
        raw = fetch_adzuna_jobs(keywords, location, hours)
        return [normalize_job(r, "adzuna") for r in raw]
    if name == "jooble":
        raw = fetch_jooble_jobs(keywords, location, hours)
        return [normalize_job(r, "jooble") for r in raw]
    raise ValueError(name)


def scrape_all_boards(keywords: str, location: str = None, hours: int = None, limit: int = None):
    """
    Returns (jobs: list[dict], status: dict[source] -> "ok" | "failed: <reason>" | "timeout")
    Never raises — a bad source just gets marked failed and excluded.
    """
    location = location or cfg.DEFAULT_LOCATION
    hours = hours or cfg.JOB_LOOKBACK_HOURS
    limit = limit or cfg.JOB_RESULT_LIMIT

    results = []
    status = {name: "timeout" for name in SOURCES}

    with ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = {
            executor.submit(_run_source, name, keywords, location, hours): name
            for name in SOURCES
        }
        deadline = time.monotonic() + cfg.JOB_SEARCH_TIMEOUT_SECONDS
        for future in as_completed(futures, timeout=cfg.JOB_SEARCH_TIMEOUT_SECONDS):
            name = futures[future]
            try:
                jobs = future.result(timeout=max(0, deadline - time.monotonic()))
                results.extend(jobs)
                status[name] = "ok"
            except Exception as e:
                logger.warning("Source %s failed: %s", name, e)
                status[name] = f"failed: {e}"

    results = dedupe_jobs(results)
    results.sort(key=lambda j: j["date_posted"], reverse=True)
    return results[:limit], status
