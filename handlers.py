import random
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
import database as db
import nlp
import os
import logging
import httpx
import json
from datetime import datetime
from config import HABITS, GEMINI_API_KEY
from scheduler import setup_scheduler, send_evening_summary, add_one_off_reminder, add_specific_time_reminder
from thefuzz import process, fuzz

logger = logging.getLogger(__name__)

GREETINGS = [
    "Hey love! 💕 Ready to crush it today?",
    "Good to see you! ☀️ Let's make today count.",
    "Welcome back bestie! 🫶 What are we getting done?",
]
MOOD_RESPONSES = {
    1: "I'm sorry you're having a rough one 💙 Be gentle with yourself today.",
    2: "Hang in there, it gets better 🫂 I believe in you.",
    3: "A neutral day is still a day! 🌤 Small wins matter.",
    4: "That's the energy! 🔥 Keep riding that wave.",
    5: "YOU'RE ON TOP OF THE WORLD! 🚀✨ Love to see it!",
}
NOTE_CONFIRMATIONS = ["📝 Saved!", "✍️ Got it!", "💡 Noted!"]
TODO_DONE_REACTIONS = ["Boom! ✅", "Crushed it! 💪", "Nice! ✅🔥"]

# ── KEYBOARDS ───────────────────────────────────────────────
def main_menu():
    return ReplyKeyboardMarkup([
        ['📋 My To-Do List', '➕ Add Task'],
        ['📝 Quick Note', '🧠 Mood Check'],
        ['📖 Export Diary', '🔄 Manual Recap'],
        ['❌ Remove Task', '💡 Help']
    ], resize_keyboard=True)

def todo_keyboard():
    todos = db.get_today_todos()
    if not todos: return None, 0, 0
    kb = []
    for t in todos:
        icon = "✅" if t['done'] else "⬜️"
        kb.append([InlineKeyboardButton(f"{icon}  {t['name']}", callback_data=f"todo_{t['id']}")])
    done_count = sum(1 for t in todos if t['done'])
    return InlineKeyboardMarkup(kb), done_count, len(todos)

def remove_keyboard():
    todos = db.get_today_todos()
    tasks = [t for t in todos if t['type'] == 'task']
    if not tasks: return None
    kb = [[InlineKeyboardButton(f"🗑 {t['name']}", callback_data=f"del_{t['id'].split('_')[1]}")] for t in tasks]
    kb.append([InlineKeyboardButton("🔙 Cancel", callback_data="cancel_del")])
    return InlineKeyboardMarkup(kb)

def mood_keyboard():
    moods = [("😫", "1"), ("😕", "2"), ("😐", "3"), ("🙂", "4"), ("🤩", "5")]
    return InlineKeyboardMarkup([[InlineKeyboardButton(e, callback_data=f"mood_{s}") for e, s in moods]])

# ── COMMANDS ────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.init_db()
    db.init_today(list(HABITS.keys()))
    if not context.bot_data.get('sched'):
        setup_scheduler(context.application, update.effective_chat.id)
        context.bot_data['sched'] = True
    await update.message.reply_text(random.choice(GREETINGS), reply_markup=main_menu())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠 *Quick Guide:*\n\n"
        "• *📋 My To-Do List* - Check off habits/tasks\n"
        "• *➕ Add Task* - Multi-line tasks supported\n"
        "• *❌ Remove Task* - Delete mistake entries\n"
        "• *💼 Job Search* - Use `/job python backend` or `/job \"data engineer\" --loc=\"Remote\"`\n"
        "• *🔄 Manual Recap* - Get your progress now\n"
        "• *Reminders* - Type 'remind me in 30 mins to gym' or '/remind 1h water'\n"
        "• *Notes* - Just send any message to save it!",
        parse_mode="Markdown"
    )

# ── MESSAGE HANDLER ─────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    state = context.user_data.get('state')

    if state == 'noting':
        db.add_note(text); context.user_data['state'] = None
        await update.message.reply_text(random.choice(NOTE_CONFIRMATIONS), reply_markup=main_menu())
        return

    if state == 'adding_task':
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        for l in lines:
            clean = l.lstrip('-•*·0123456789.) ').strip()
            if clean: db.add_task(clean)
        context.user_data['state'] = None
        await update.message.reply_text(f"✅ Added {len(lines)} tasks!", reply_markup=main_menu())
        return

    # Buttons
    if text == "📋 My To-Do List":
        kb, done, total = todo_keyboard()
        if kb:
            await update.message.reply_text(f"📋 *To-Do* — {done}/{total} done", reply_markup=kb, parse_mode="Markdown")
        else:
            await update.message.reply_text("List is empty! 🎉")
        return

    if text == "➕ Add Task":
        context.user_data['state'] = 'adding_task'
        await update.message.reply_text("Type your tasks (one per line):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="cancel_add")]]))
        return

    if text == "❌ Remove Task":
        kb = remove_keyboard()
        if kb:
            await update.message.reply_text("Which task should I remove?", reply_markup=kb)
        else:
            await update.message.reply_text("No tasks to remove! 📋")
        return

    if text == "🔄 Manual Recap":
        await send_evening_summary(context.application, update.effective_chat.id)
        return

    if text == "/recap":
        await send_evening_summary(context.application, update.effective_chat.id)
        return

    if text == "📖 Export Diary":
        await update.message.reply_text("Generating your portrait diary... 📖")
        try:
            pdf = db.export_to_pdf()
            with open(pdf, 'rb') as f: await update.message.reply_document(document=f)
            os.remove(pdf)
        except Exception as e:
            logger.error(e)
            await update.message.reply_text("Failed to export 😕")
        return

    if text == "📝 Quick Note":
        context.user_data['state'] = 'noting'
        await update.message.reply_text("Go ahead, I'm listening... 👂")
        return

    if text == "🧠 Mood Check":
        await update.message.reply_text("How are you feeling right now?", reply_markup=mood_keyboard())
        return

    if text == "💡 Help":
        await help_command(update, context)
        return

    # Better Reminder NLP: 
    # 1. relative (e.g. "text me in 5m", "call me in 1hr", "remind m soon")
    text_lower = text.lower()
    
    if "soon" in text_lower and any(w in text_lower for w in ["remind", "text", "call", "ping"]):
        task = text_lower.replace("remind me", "").replace("remind m", "").replace("text me", "").replace("call me", "").replace("soon", "").strip() or "check in"
        if add_one_off_reminder(context.application, update.effective_chat.id, 15, task):
            await update.message.reply_text(f"⏰ Got it! I'll ping you about *{task}* soon (in ~15 mins).", parse_mode="Markdown")
        return

    rem_match = re.search(r'(?:remind|text|call|ping)\s+m?[e]?\s+(?:to\s+(.+?)\s+)?in\s+(\d+)\s*(min|m|hr|hour|h)', text_lower)
    if rem_match:
        val = int(rem_match.group(2))
        unit = rem_match.group(3)
        mins = val * 60 if 'h' in unit or 'hour' in unit else val
        task = rem_match.group(1).strip() if rem_match.group(1) else (text_lower.split(' to ')[-1] if ' to ' in text_lower else "check in")
        if add_one_off_reminder(context.application, update.effective_chat.id, mins, task):
            await update.message.reply_text(f"⏰ Got it! I'll remind you about *{task}* in {val} {unit}.", parse_mode="Markdown")
        return

    # 2. absolute
    abs_rem_match_1 = re.search(r'(?:remind|text|call|ping)\s+m?[e]?\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)?) (?:to\s+)?(.+)', text_lower)
    abs_rem_match_2 = re.search(r'(?:remind|text|call|ping)\s+m?[e]?\s+(?:to\s+)?(.+?) (?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)?)$', text_lower)
    
    abs_rem_match = abs_rem_match_1 or abs_rem_match_2
    if abs_rem_match:
        if abs_rem_match == abs_rem_match_1:
            time_str = abs_rem_match.group(1).strip()
            task = abs_rem_match.group(2).strip() or "check in"
        else:
            task = abs_rem_match.group(1).strip() or "check in"
            time_str = abs_rem_match.group(2).strip()
            
        if not time_str.endswith('m') and (time_str.endswith('a') or time_str.endswith('p')):
            time_str += 'm' # fix '5p' to '5pm'
            
        if add_specific_time_reminder(context.application, update.effective_chat.id, time_str, task):
            await update.message.reply_text(f"⏰ Noted! I'll remind you to *{task}* at {time_str}.", parse_mode="Markdown")
            return

    intent, data = nlp.get_intent(text)
    if intent == "ADD_TASK":
        task_name = data['task']
        db.add_task(task_name)
        # Get the id of the newly added task (latest one)
        todos = db.get_today_todos()
        new_task = next((t for t in reversed(todos) if t['type'] == 'task' and t['name'] == task_name), None)
        kb = None
        if new_task:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Remove", callback_data=f"del_{new_task['id'].split('_')[1]}")],
                                       [InlineKeyboardButton("✅ Done", callback_data=f"todo_{new_task['id']}") ]])
        await update.message.reply_text(f"✅ Added: {task_name}", reply_markup=kb)
        
    elif intent == "REMOVE_TASK":
        target = data['task']
        todos = db.get_today_todos()
        tasks = [t for t in todos if t['type'] == 'task']
        if not tasks:
            await update.message.reply_text("You don't have any tasks to remove! 📋")
            return
        
        task_names = [t['name'] for t in tasks]
        best_match, score = process.extractOne(target, task_names, scorer=fuzz.token_set_ratio)
        if score > 70:
            found = next(t for t in tasks if t['name'] == best_match)
            db.remove_task(found['id'].split('_')[1])
            await update.message.reply_text(f"🗑 Removed: *{best_match}*", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"I couldn't find a task matching '{target}'. Check your list with 📋")

    elif intent == "HABIT_ACTION":
        habit = data['habit']
        action = data['action']
        todos = db.get_today_todos()
        found_habit = next((h for h in todos if h['type'] == 'habit' and h['name'].lower() == habit.lower()), None)
        if found_habit:
            if action == "DONE":
                if not found_habit['done']:
                    db.toggle_todo(found_habit['id'])
                    await update.message.reply_text(f"{random.choice(TODO_DONE_REACTIONS)} Marked {habit} as done!")
                else:
                    await update.message.reply_text(f"You already finished {habit}! 🏆")
            else: # SKIP
                 await update.message.reply_text(f"Okay, we'll skip {habit} for today. 🌤")
        else:
            await update.message.reply_text(f"I don't see {habit} on your list! 📋")

    elif intent == "LOG_MOOD":
        score = data['score']
        db.log_mood(score)
        await update.message.reply_text(f"Logged {score}/5. {MOOD_RESPONSES.get(score, '')}")

    elif intent == "RECAP":
        await send_evening_summary(context.application, update.effective_chat.id)

    elif intent == "SAVE_NOTE":
        db.add_note(data['content'])
        await update.message.reply_text(random.choice(NOTE_CONFIRMATIONS))

    else:
        # Instead of saving to diary, we respond via LLM or just echo/friendly chat
        # For now, let's use a friendly response if not integrated yet
        chat_response = await get_chat_response(text)
        await update.message.reply_text(chat_response, reply_markup=main_menu())

# ── BUTTON HANDLER ──────────────────────────────────────────
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    data = query.data

    if data.startswith("todo_"):
        db.toggle_todo(data.replace("todo_", ""))
        kb, done, total = todo_keyboard()
        if kb: await query.edit_message_text(f"📋 *To-Do* — {done}/{total} done", reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("del_"):
        db.remove_task(data.replace("del_", ""))
        kb = remove_keyboard()
        if kb: await query.edit_message_text("Task removed. Anything else?", reply_markup=kb)
        else: await query.edit_message_text("All tasks removed! 📋")

    elif data == "cancel_del":
        await query.edit_message_text("Cancelled removal.")

    elif data == "cancel_add":
        context.user_data['state'] = None
        await query.edit_message_text("Cancelled adding tasks.")

    elif data.startswith("mood_"):
        score = int(data.split("_")[1])
        db.log_mood(score)
        await query.edit_message_text(f"Mood logged: {score}/5 ✨")
async def get_chat_response(text):
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
        return "I hear you! ✨ (P.S. To make me smarter, please add your free Gemini API key in `config.py`!)"
    
    # Using v1 stable endpoint
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": f"System: You are a helpful, supportive, sassy productivity assistant. User: {text}"}]}]
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            data = resp.json()
            
            if "candidates" in data and data["candidates"]:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            
            # If 404 or flash fails, try gemini-1.5-pro as fallback
            if "error" in data:
                logger.error(f"Gemini API Error (Flash): {data['error'].get('message', 'Unknown Error')}")
                # Fallback URL
                fb_url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}"
                resp = await client.post(fb_url, headers=headers, json=payload)
                fb_data = resp.json()
                if "candidates" in fb_data and fb_data["candidates"]:
                    return fb_data["candidates"][0]["content"]["parts"][0]["text"]
            
            logger.error(f"Gemini API Full Failure: {data}")
            return "I hear you! ✨ How can I help you stay on track with your goals today?"
            
    except Exception as e:
        logger.error(f"Gemini Exception: {e}")
        return "I hear you! ✨ Let's stay focused. Anything else for your to-do list?"

