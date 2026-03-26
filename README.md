# 🚀 Continuous Diary: Your Smart Telegram Productivity Partner

A personal, persistent, and intelligent Telegram bot to track your life. Merging habits, tasks, mood, and daily journaling into a beautiful portrait PDF diary.

## ✨ Features

- **✅ Persistent To-Do List**: Habits and tasks stay on your checklist all day—even after they're done. 
- **➕ Multi-line Task Addition**: Just send a list of things to do, and the bot will add them individually.
- **❌ Easy Removal**: A dedicated "Remove Task" button with an interactive list to fix mistakes.
- **🧠 Smart Mood Tracking**: Log your vibe with emojis (1-5 scale) and see them in a yearly grid.
- **📝 Automatic Journaling**: Any random message you send that isn't a task or habit is saved as a note.
- **📖 Portrait PDF Diary**: Generates a professional 2026 diary with:
  - Dark-themed cover page.
  - Day-by-day journal pages with date headers and mood bars.
  - **Yearly Mood Tracker** grid (GitHub style).
  - **Individual Yearly Habit Trackers** for every single habit.
  - Progress bars for your current streaks.
- **⏰ Custom Reminders**: "remind me in 30 mins to gym" or "/remind 1h study".
- **🔄 Manual Recap**: Get your end-of-day summary whenever you want it.

---

## 🛠 Setup Instructions

### 1. Requirements
Ensure you have Python installed, then install the dependencies:
```bash
pip install -r requirements.txt
```

### 2. Environment Variables
1. Copy `.env.example` to a new file named `.env`.
2. Open `.env` and paste your **TELEGRAM_TOKEN** from @BotFather.
3. Your `.env` file is ignored by git to keep your tokens safe! 🔐

### 3. Custom Habits
Open `config.py` to change your daily habits and keywords. The bot uses fuzzy matching, so it understands typos!

### 4. Run the Bot
```bash
python bot.py
```
Open your bot in Telegram and type `/start`.

---

## ☁️ Hosting

### Oracle Cloud (100% Free Forever)
1. Create an **Oracle Cloud Always Free** account.
2. Launch a "Compute Instance" (Ubuntu).
3. SSH into it, clone your repo, and create a `systemd` service for 24/7 uptime.

### Railway / Render
1. Connect your GitHub repo.
2. Add your environment variables in the dashboard.
3. Railway is recommended for 24/7 bots as it doesn't "sleep" like Render's free tier.

---

## 📁 Project Structure
- `bot.py`: Main entry point & command registration.
- `handlers.py`: Button, Command, and NLP logic (The "Brain").
- `database.py`: SQLite storage & PDF Diary generation.
- `scheduler.py`: Morning, Evening, and Custom reminders.
- `nlp.py`: Intent detection and fuzzy matching.
- `config.py`: Habits list and global settings.
