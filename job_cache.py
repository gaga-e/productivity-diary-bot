"""
Lightweight in-memory cache + cooldown tracker.
Fine for a single always-on process (EC2). If you ever run multiple bot
processes, swap this dict for Redis with the same get/set interface.
"""

import time
import threading

import config as cfg

_lock = threading.Lock()
_search_cache = {}   # (keywords, location) -> (expires_at, jobs, status)
_last_search_at = {}  # chat_id -> timestamp


def cache_key(keywords: str, location: str) -> tuple:
    return (keywords.lower().strip(), location.lower().strip())


def get_cached_search(keywords: str, location: str):
    key = cache_key(keywords, location)
    with _lock:
        entry = _search_cache.get(key)
        if not entry:
            return None
        expires_at, jobs, status = entry
        if time.monotonic() > expires_at:
            del _search_cache[key]
            return None
        return jobs, status


def cache_search_results(keywords: str, location: str, jobs: list, status: dict):
    key = cache_key(keywords, location)
    expires_at = time.monotonic() + cfg.JOB_SEARCH_CACHE_MINUTES * 60
    with _lock:
        _search_cache[key] = (expires_at, jobs, status)


def seconds_until_next_allowed(chat_id) -> float:
    """Returns 0 if the user can search now, else seconds remaining on cooldown."""
    with _lock:
        last = _last_search_at.get(chat_id)
    if last is None:
        return 0
    elapsed = time.monotonic() - last
    remaining = cfg.JOB_SEARCH_COOLDOWN_SECONDS - elapsed
    return max(0, remaining)


def mark_search_started(chat_id):
    with _lock:
        _last_search_at[chat_id] = time.monotonic()


# --- results cache for pagination (so "Next page" doesn't re-run the search) ---
_results_by_search_id = {}  # search_id -> (expires_at, jobs)


def store_results_for_paging(search_id: str, jobs: list):
    expires_at = time.monotonic() + cfg.JOB_SEARCH_CACHE_MINUTES * 60
    with _lock:
        _results_by_search_id[search_id] = (expires_at, jobs)


def get_results_for_paging(search_id: str):
    with _lock:
        entry = _results_by_search_id.get(search_id)
        if not entry:
            return None
        expires_at, jobs = entry
        if time.monotonic() > expires_at:
            del _results_by_search_id[search_id]
            return None
        return jobs
