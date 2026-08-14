import random
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
from config import MORNING_MESSAGE_TIME, EVENING_SUMMARY_TIME, RANDOM_REMINDER_COUNT, TIMEZONE, HABITS
import database as db

# ── CASUAL REMINDER PHRASES ─────────────────────────────────
NUDGES = [
    "Hey! 👋 Just a friendly check-in — have you done *{item}* yet?",
    "Quick reminder: *{item}* is still waiting for you! You got this 💪",
    "Don't let *{item}* slip through today! Tap the menu to check it off ✅",
    "Psst... *{item}* — still on the list! A little nudge from me to you 🫶",
    "How's it going? *{item}* is still unchecked. No pressure, just keeping you honest 😄",
    "Hey bestie, *{item}* hasn't been marked yet. Let's get it done! 🚀",
]

_scheduler = None

async def send_morning_message(app, chat_id):
    db.init_today(list(HABITS.keys()))
    todos = db.get_today_todos()
    streaks = db.get_streaks()

    msg = "☀️ *Good morning!* ☀️\n\n"
    if streaks:
        msg += "🔥 *Your Streaks:*\n"
        for h, s in streaks.items():
            msg += f"  • {h.capitalize()}: {s} day{'s' if s != 1 else ''}\n"
        msg += "\n"
    msg += "📋 *Today's To-Do:*\n"
    for t in todos:
        icon = "✅" if t['done'] else "⬜️"
        msg += f"  {icon} {t['name']}\n"
    msg += "\nTap *📋 My To-Do List* to check things off. Let's have a great day! 🫶"
    await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def send_random_reminder(app, chat_id):
    todos = db.get_today_todos()
    pending = [t['name'] for t in todos if not t['done']]
    if not pending: return
    item = random.choice(pending)
    phrase = random.choice(NUDGES).format(item=item)
    await app.bot.send_message(chat_id=chat_id, text=phrase, parse_mode="Markdown")

async def send_evening_summary(app, chat_id):
    mood, notes = db.get_today_logs()
    todos = db.get_today_todos()
    streaks = db.get_streaks()

    done_list = [t['name'] for t in todos if t['done']]
    missed_list = [t['name'] for t in todos if not t['done']]
    total = len(todos)
    done_count = len(done_list)

    msg = "🌙 *End of Day Recap* 🌙\n\n"
    if streaks:
        msg += "🔥 *Your Active Streaks:*\n"
        for h, s in streaks.items():
            if s > 0:
                msg += f"  • {h.capitalize()}: {s} day{'s' if s != 1 else ''}\n"
        msg += "\n"

    if done_count == total and total > 0:
        msg += "🎉 *PERFECT DAY!* You completed everything! 🏆\n\n"
    elif done_count > 0:
        msg += f"📊 *Progress:* {done_count}/{total} completed\n\n"
    else:
        msg += "Tomorrow is a new chance! Don't be too hard on yourself 💙\n\n"

    if done_list:
        msg += "✅ *Completed:*\n"
        for d in done_list: msg += f"  • {d}\n"
    if missed_list:
        msg += "\n⏳ *Missed:*\n"
        for m in missed_list: msg += f"  • {m}\n"
    if mood:
        msg += f"\n🧠 *Mood:* {mood['score']}/5 {mood.get('emoji', '')}\n"
    if notes:
        msg += f"\n📝 You logged {len(notes)} note{'s' if len(notes) != 1 else ''} today.\n"
    msg += "\nRest well tonight! See you tomorrow morning ☀️"
    await app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def send_custom_reminder(app, chat_id, text):
    await app.bot.send_message(chat_id=chat_id, text=f"🔔 *REMINDER:* {text}", parse_mode="Markdown")

def _ensure_scheduler():
    global _scheduler
    if not _scheduler:
        tz = pytz.timezone(TIMEZONE)
        _scheduler = AsyncIOScheduler(timezone=tz)
        try:
            _scheduler.start()
        except RuntimeError:
            pass

def add_one_off_reminder(app, chat_id, minutes, text):
    _ensure_scheduler()
    run_at = datetime.now() + timedelta(minutes=minutes)
    _scheduler.add_job(send_custom_reminder, 'date', run_date=run_at, args=[app, chat_id, text])
    return True

def add_specific_time_reminder(app, chat_id, time_str, text):
    _ensure_scheduler()
    try:
        # Simple parsing for "5pm", "17:00", "5:30 pm"
        time_str = time_str.lower().strip()
        now = datetime.now(pytz.timezone(TIMEZONE))
        
        # Try to parse with common formats
        formats = ["%I%p", "%I:%M%p", "%I %p", "%I:%M %p", "%H:%M"]
        target_time = None
        
        for fmt in formats:
            try:
                # Strip spaces for some formats, keep them for others
                test_str = time_str.replace(" ", "") if " " not in fmt else time_str
                parsed = datetime.strptime(test_str, fmt)
                target_time = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
                break
            except ValueError:
                continue
        
        if not target_time:
            return False
            
        # If the time is in the past, assume they mean tomorrow
        final_time: datetime = target_time
        if final_time < now:
            final_time += timedelta(days=1)
            
        _scheduler.add_job(send_custom_reminder, 'date', run_date=final_time, args=[app, chat_id, text])
        return True
    except Exception as e:
        print(f"Error parsing time: {e}")
        return False

def setup_scheduler(application, chat_id=None):
    _ensure_scheduler()

    if chat_id:
        db.register_user_chat_id(chat_id)

    chat_ids = db.get_all_user_chat_ids()
    if chat_id and chat_id not in chat_ids:
        chat_ids.append(chat_id)

    h_m, m_m = map(int, MORNING_MESSAGE_TIME.split(":"))
    h_e, m_e = map(int, EVENING_SUMMARY_TIME.split(":"))

    for cid in chat_ids:
        # Morning
        _scheduler.add_job(send_morning_message, CronTrigger(hour=h_m, minute=m_m), args=[application, cid], id=f"morning_{cid}", replace_existing=True)

        # Evening
        _scheduler.add_job(send_evening_summary, CronTrigger(hour=h_e, minute=m_e), args=[application, cid], id=f"evening_{cid}", replace_existing=True)

        # Random
        for i in range(RANDOM_REMINDER_COUNT):
            rand_h = random.randint(10, 20)
            rand_m = random.randint(0, 59)
            id_str = f"nudge_{cid}_{i}"
            _scheduler.add_job(send_random_reminder, CronTrigger(hour=rand_h, minute=rand_m), 
                             args=[application, cid], id=id_str, replace_existing=True)

    return _scheduler
