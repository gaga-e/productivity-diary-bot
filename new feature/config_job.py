"""
Job search configuration.
Copy these values into your main config.py (or `from config_job import *`).
"""

import os

# --- Free API keys (sign up, both have generous free tiers) ---
# Adzuna: https://developer.adzuna.com/  (free, ~250 calls/month on the basic tier)
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.getenv("ADZUNA_COUNTRY", "us")  # us, gb, ca, au, de, fr, etc.

# Jooble: https://jooble.org/api/about  (free, request an API key by email)
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")

# --- Search defaults ---
DEFAULT_LOCATION = "Remote"
JOB_LOOKBACK_HOURS = 24
JOB_RESULT_LIMIT = 50
JOB_RESULTS_PER_PAGE = 10

# How long we wait for all sources combined before giving up on the slow ones
JOB_SEARCH_TIMEOUT_SECONDS = 45

# Cache identical searches for this long so re-runs are instant and free
JOB_SEARCH_CACHE_MINUTES = 15

# Per-user cooldown between searches (seconds) to avoid hammering LinkedIn/Indeed
JOB_SEARCH_COOLDOWN_SECONDS = 60

# jobspy sources to attempt (LinkedIn + Indeed are best-effort / may fail or return 0)
JOBSPY_SITE_NAMES = ["linkedin", "indeed"]
