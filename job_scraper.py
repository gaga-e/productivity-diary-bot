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
    import pandas as pd
    from jobspy import scrape_jobs  # imported lazily; heavy dependency

    hours_old = hours if hours else 24
    is_remote_search = bool(location and "remote" in location.lower())

    results_list = []
    # Query across UK, South Africa, and USA for broader EMEA/Africa & Global Remote coverage
    countries = ["UK", "South Africa", "USA"] if is_remote_search else ["USA"]
    per_country_limit = max(10, cfg.JOB_RESULT_LIMIT // len(countries))

    for ctry in countries:
        try:
            df = scrape_jobs(
                site_name=cfg.JOBSPY_SITE_NAMES,
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
            logger.warning("JobSpy fetch failed for country %s: %s", ctry, e)

    return results_list


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
        "results_per_page": cfg.JOB_RESULT_LIMIT,
        "what": keywords,
        "where": location,
        "max_days_old": max_days_old,
    }
    country = getattr(cfg, "ADZUNA_COUNTRY", "gb")
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def fetch_jooble_jobs(keywords: str, location: str, hours: int):
    if not cfg.JOOBLE_API_KEY:
        raise RuntimeError("Jooble not configured (missing API key)")

    payload = {
        "keywords": keywords,
        "location": location,
        "page": 1,
    }
    url = f"https://jooble.org/api/{cfg.JOOBLE_API_KEY}"
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


def fetch_wellfound_jobs(keywords: str, hours: int):
    """Fetches startup & tech job listings from Wellfound (AngelList)."""
    try:
        import tls_client
        from bs4 import BeautifulSoup

        session = tls_client.Session(client_identifier="chrome_120")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = "https://wellfound.com/jobs"
        resp = session.get(url, headers=headers)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        jobs = []
        terms = [t.lower() for t in keywords.split()] if keywords else []
        for a in soup.find_all('a', href=re.compile(r'/jobs/\d+')):
            title = a.get_text(strip=True)
            link = "https://wellfound.com" + a['href'] if a['href'].startswith('/') else a['href']
            if terms and not any(t in title.lower() for t in terms):
                continue
            jobs.append({
                "title": title,
                "company": "Wellfound Startup",
                "location": "Remote / Global",
                "link": link,
                "source": "wellfound",
                "date_posted": datetime.now(timezone.utc).isoformat()
            })
        return jobs
    except Exception as e:
        logger.warning("Wellfound fetch failed: %s", e)
        return []


def fetch_justremote_jobs(keywords: str, hours: int):
    """Fetches remote tech & design jobs from JustRemote.co."""
    try:
        from bs4 import BeautifulSoup

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = "https://justremote.co/remote-jobs"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        jobs = []
        terms = [t.lower() for t in keywords.split()] if keywords else []
        for a in soup.find_all('a', href=re.compile(r'/remote-jobs/')):
            href = a['href']
            title = a.get_text(strip=True)
            if 'new' in href or len(title) < 3:
                continue
            if terms and not any(t in title.lower() for t in terms):
                continue
            full_url = "https://justremote.co" + href if href.startswith('/') else href
            jobs.append({
                "title": title,
                "company": "JustRemote Partner",
                "location": "Remote / Worldwide",
                "link": full_url,
                "source": "justremote",
                "date_posted": datetime.now(timezone.utc).isoformat()
            })
        return jobs
    except Exception as e:
        logger.warning("JustRemote fetch failed: %s", e)
        return []


def fetch_builtin_jobs(keywords: str, hours: int):
    """Fetches tech jobs from BuiltIn.com."""
    try:
        from bs4 import BeautifulSoup
        import tls_client

        session = tls_client.Session(client_identifier="chrome_120")
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = f"https://builtin.com/jobs?q={keywords}"
        resp = session.get(url, headers=headers)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        jobs = []
        for card in soup.find_all('div', attrs={'data-id': 'job-card'}) or soup.find_all('h2'):
            a = card.find('a', href=True) if hasattr(card, 'find') else None
            if not a:
                continue
            title = a.get_text(strip=True)
            link = "https://builtin.com" + a['href'] if a['href'].startswith('/') else a['href']
            jobs.append({
                "title": title,
                "company": "BuiltIn Tech",
                "location": "Remote / Global",
                "link": link,
                "source": "builtin",
                "date_posted": datetime.now(timezone.utc).isoformat()
            })
        return jobs
    except Exception as e:
        logger.warning("BuiltIn fetch failed: %s", e)
        return []


def fetch_careerhound_jobs(keywords: str, hours: int):
    """Fetches curated tech jobs from CareerHound.io."""
    try:
        from bs4 import BeautifulSoup

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        url = "https://careerhound.io"
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        jobs = []
        terms = [t.lower() for t in keywords.split()] if keywords else []
        for a in soup.find_all('a', href=True):
            href = a['href']
            title = a.get_text(strip=True)
            if len(title) > 3 and any(k in href.lower() for k in ['/job', '/career', '/position']):
                if terms and not any(t in title.lower() for t in terms):
                    continue
                full_url = "https://careerhound.io" + href if href.startswith('/') else href
                jobs.append({
                    "title": title,
                    "company": "CareerHound Startup",
                    "location": "Remote / Global",
                    "link": full_url,
                    "source": "careerhound",
                    "date_posted": datetime.now(timezone.utc).isoformat()
                })
        return jobs
    except Exception as e:
        logger.warning("CareerHound fetch failed: %s", e)
        return []


# --------------------------------------------------------------------------
# Normalization: every source -> {title, company, location, link, source, date_posted}
# --------------------------------------------------------------------------
def _to_iso(dt) -> str:
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
    if source in ("wellfound", "justremote", "builtin", "careerhound"):
        return {
            "title": str(raw.get("title") or "").strip(),
            "company": str(raw.get("company") or "Tech Company").strip(),
            "location": str(raw.get("location") or "Remote").strip(),
            "link": raw.get("link") or "",
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
# Regional Classification
# --------------------------------------------------------------------------
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
    "us-based only", "united states only", "must be located in the us",
    ", us", ", usa", "united states", ", ca", ", ny", ", tx", ", fl", ", wa", ", ma",
    ", il", ", ga", ", nc", ", co", ", va", ", nj", ", pa", ", oh", ", mi", ", az",
    ", tn", ", mo", ", md", ", ut", ", mn", ", wi", ", sc", ", in", ", al", ", nv"
]


def _is_us_restricted(job: dict) -> bool:
    text = f"{job.get('title', '')} {job.get('location', '')}".lower()
    # Worldwide/Global/EMEA/Africa roles are not US-restricted
    if any(global_kw in text for global_kw in ["worldwide", "global", "anywhere", "work from anywhere", "emea", "africa"]):
        return False
    return any(req in text for req in US_ONLY_RESTRICTIONS)


PREFERRED_BOARDS = ["wellfound", "justremote", "builtin", "careerhound", "remoteok"]


def _region_score(job: dict) -> int:
    """
    Ranks jobs by regional relevance and board quality:
      4: Preferred Startup/Remote Boards (Wellfound, JustRemote, BuiltIn, CareerHound, RemoteOK)
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

    if src in PREFERRED_BOARDS:
        return 4

    if any(w in haystack for w in WORLDWIDE_REMOTE_KEYWORDS):
        return 3

    if any(k in haystack for k in EMEA_AFRICA_KEYWORDS):
        return 2

    if _is_remote(job):
        return 1

    return 0


SOURCES = ["wellfound", "justremote", "builtin", "careerhound", "linkedin_indeed", "remoteok", "adzuna", "jooble"]


def _run_source(name: str, keywords: str, location: str, hours: int):
    if name == "wellfound":
        raw = fetch_wellfound_jobs(keywords, hours)
        return [normalize_job(r, "wellfound") for r in raw]
    if name == "justremote":
        raw = fetch_justremote_jobs(keywords, hours)
        return [normalize_job(r, "justremote") for r in raw]
    if name == "builtin":
        raw = fetch_builtin_jobs(keywords, hours)
        return [normalize_job(r, "builtin") for r in raw]
    if name == "careerhound":
        raw = fetch_careerhound_jobs(keywords, hours)
        return [normalize_job(r, "careerhound") for r in raw]
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


def _is_remote(job: dict) -> bool:
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
        try:
            for future in as_completed(futures, timeout=cfg.JOB_SEARCH_TIMEOUT_SECONDS):
                name = futures[future]
                try:
                    jobs = future.result(timeout=max(0.1, deadline - time.monotonic()))
                    results.extend(jobs)
                    status[name] = "ok"
                except Exception as e:
                    logger.warning("Source %s failed: %s", name, e)
                    status[name] = f"failed: {e}"
        except TimeoutError:
            logger.warning("Job search reached total timeout (%ds); returning partial results.", cfg.JOB_SEARCH_TIMEOUT_SECONDS)
            for future, name in futures.items():
                if future.done() and status[name] == "timeout":
                    try:
                        jobs = future.result()
                        results.extend(jobs)
                        status[name] = "ok"
                    except Exception as e:
                        status[name] = f"failed: {e}"

    results = dedupe_jobs(results)

    # Completely filter out US-restricted roles unless the user explicitly searched for US location
    is_us_requested = bool(location and any(us in location.lower() for us in ["united states", "usa", " us"]))
    if not is_us_requested:
        results = [j for j in results if not _is_us_restricted(j)]

    # Sort remaining jobs by Region Score (3 > 2 > 1), then by date_posted newest-first
    results.sort(key=lambda j: (_region_score(j), j["date_posted"]), reverse=True)
    return results[:limit], status
