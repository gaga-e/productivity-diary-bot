import re
from thefuzz import process, fuzz
from config import HABITS

def get_intent(text):
    text_lower = text.lower().strip()

    # ── REMOVE TASK ─────────────────────────────────────────
    remove_patterns = [
        r'^remove\s+(.+)',
        r'^delete\s+(.+)',
        r'^cancel\s+(.+)',
        r'^del\s+(.+)',
        r'^rm\s+(.+)',
    ]
    for pattern in remove_patterns:
        match = re.match(pattern, text_lower)
        if match:
            task_text = match.group(1).strip()
            if task_text:
                return "REMOVE_TASK", {"task": task_text}

    # ── ADD TASK ────────────────────────────────────────────
    add_patterns = [
        r'^add\s+(.+)',
        r'^todo\s+(.+)',
        r'^task\s+(.+)',
        r'^new task\s+(.+)',
        r'^i need to\s+(.+)',
        r'^remind me to\s+(.+)',
    ]
    for pattern in add_patterns:
        match = re.match(pattern, text_lower)
        if match:
            task_text = match.group(1).strip()
            if task_text:
                return "ADD_TASK", {"task": task_text}

    # ── MOOD (number) ───────────────────────────────────────
    mood_match = re.search(r'(mood|feeling|vibes|score|rate)\s*(\d)', text_lower)
    if mood_match:
        return "LOG_MOOD", {"score": min(int(mood_match.group(2)), 5)}

    # ── MOOD (words) ────────────────────────────────────────
    great_words = ["amazing", "awesome", "great", "fantastic", "wonderful", "incredible", "happy", "thrilled", "blessed", "grateful"]
    good_words = ["good", "fine", "nice", "decent", "vibing", "solid", "cool", "positive"]
    meh_words = ["meh", "alright", "so-so", "neutral", "okay"]
    bad_words = ["bad", "sad", "tired", "exhausted", "low", "drained", "rough", "stressed", "anxious"]
    terrible_words = ["terrible", "awful", "horrible", "worst", "depressed", "miserable", "trash", "broken"]

    mood_triggers = ["feeling", "i feel", "i'm", "im ", "today is", "today was", "day is", "day was"]
    has_mood_trigger = any(t in text_lower for t in mood_triggers)

    if has_mood_trigger:
        for word in terrible_words:
            if word in text_lower:
                return "LOG_MOOD", {"score": 1}
        for word in bad_words:
            if word in text_lower:
                return "LOG_MOOD", {"score": 2}
        for word in meh_words:
            if word in text_lower:
                return "LOG_MOOD", {"score": 3}
        for word in great_words:
            if word in text_lower:
                return "LOG_MOOD", {"score": 5}
        for word in good_words:
            if word in text_lower:
                return "LOG_MOOD", {"score": 4}

    # ── NOTES ───────────────────────────────────────────────
    note_triggers = ["note:", "note ", "rmb ", "rmb:", "remember:", "thought:", "idea:", "journal:"]
    for trigger in note_triggers:
        if text_lower.startswith(trigger):
            content = text[len(trigger):].strip()
            if content:
                return "SAVE_NOTE", {"content": content}

    # ── HABITS ──────────────────────────────────────────────
    skip_words = ["skip", "no ", "not ", "missed", "didn't", "didnt", "nah", "nope", "forgot"]
    done_words = ["done", "did", "complete", "finished", "yes", "yep", "yeah", "checked"]

    for habit, keywords in HABITS.items():
        for kw in keywords:
            if kw in text_lower.split() or kw in text_lower:
                action = "SKIP" if any(s in text_lower for s in skip_words) else "DONE"
                return "HABIT_ACTION", {"habit": habit, "action": action}

    # Fuzzy match
    all_keywords = []
    keyword_to_habit = {}
    for habit, keywords in HABITS.items():
        for kw in keywords:
            all_keywords.append(kw)
            keyword_to_habit[kw] = habit

    if all_keywords:
        best_match, score = process.extractOne(text_lower, all_keywords, scorer=fuzz.token_set_ratio)
        if score > 75:
            habit = keyword_to_habit[best_match]
            action = "SKIP" if any(s in text_lower for s in skip_words) else "DONE"
            return "HABIT_ACTION", {"habit": habit, "action": action}

    # ── RECAP ───────────────────────────────────────────────
    if any(word in text_lower for word in ["recap", "summary", "how did i do", "progress report"]):
        return "RECAP", {}

    return "UNKNOWN", {}
