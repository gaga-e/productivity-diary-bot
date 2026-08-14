import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- BOT CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- GEMINI SETTINGS ---
# Get a free API key from Google AI Studio: https://aistudio.google.com/
GEMINI_API_KEY = "AIzaSyBPFgOeax_MCu8GmCN2BtKrUxqKWy49Rzk"

# --- PERSONAL SETTINGS ---
TIMEZONE = os.getenv("TIMEZONE", "Africa/Lagos")  # Change to your timezone (e.g., "Europe/London", "America/New_York")

# DAILY HABITS
# These are things you want to track every single day.
# DAILY HABITS
# These are things you want to track every single day.
HABITS = {
    "Pray": ["pray", "prayer", "prayed"],
    "Read Bible": ["bible", "read bible", "scripture", "devotional"],
    "Meditate": ["meditate", "meditation", "mindfulness", "zen"],
    "Write": ["write", "writing", "journal", "wrote"],
    "Exercise": ["exercise", "gym", "workout", "training", "lift", "run", "jog"],
    "Post On Tiktok": ["tiktok", "tt", "post tiktok", "posted tiktok", "tik tok"],
    "Duolingo French": ["duolingo", "duo", "french lesson", "duolingo french"],
    "Practice For Interview": ["interview", "practice interview", "mock interview", "interview prep"],
    "Apply For Opps": ["opps", "opportunities", "apply opps", "applications"],
    "Do Something New": ["something new", "new thing", "try new", "new experience"],
    "Podcast": ["podcast", "pod", "listen podcast", "listened"],
    "Read Book": ["read book", "reading", "read", "book"],
    "Apply Jobs 5": ["apply jobs", "job apps", "job applications", "apply 5", "jobs"],
    "Chess Learn": ["chess", "chess learn", "play chess", "chess puzzle"],
    "Learn A New French Phrase": ["french phrase", "new phrase", "learn french", "phrase"],
}

# --- SCHEDULING ---
MORNING_MESSAGE_TIME = "06:00"  # 24h format
EVENING_SUMMARY_TIME = "21:00"  # 24h format
RANDOM_REMINDER_COUNT = 3      # How many random nudges per day

# --- DATABASE ---
MONGODB_URI = os.getenv("MONGODB_URI")
MONGO_DB_NAME = "productivity_bot"
DB_PATH = "productivity_bot.db"  # Kept for local fallback/legacy if needed

# --- JOB SEARCH SETTINGS ---
ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
ADZUNA_COUNTRY = os.getenv("ADZUNA_COUNTRY", "us")
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")

DEFAULT_LOCATION = "Remote"
JOB_LOOKBACK_HOURS = 24
JOB_RESULT_LIMIT = 50
JOB_RESULTS_PER_PAGE = 10
JOB_SEARCH_TIMEOUT_SECONDS = 45
JOB_SEARCH_CACHE_MINUTES = 15
JOB_SEARCH_COOLDOWN_SECONDS = 60
JOBSPY_SITE_NAMES = ["linkedin", "indeed"]
