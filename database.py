import sqlite3
import threading
from datetime import datetime, timedelta
from config import DB_PATH
from fpdf import FPDF
import calendar

_lock = threading.Lock()

def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn

def init_db():
    with _lock:
        c = _conn()
        c.executescript('''
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                date TEXT NOT NULL,
                is_done INTEGER DEFAULT 0,
                UNIQUE(name, date)
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                date_added TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                deadline TEXT
            );
            CREATE TABLE IF NOT EXISTS mood (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE,
                score INTEGER,
                emoji TEXT
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS streaks (
                habit_name TEXT PRIMARY KEY,
                current_streak INTEGER DEFAULT 0,
                longest_streak INTEGER DEFAULT 0,
                last_date TEXT
            );
        ''')
        c.close()

def init_today(habit_list):
    with _lock:
        c = _conn()
        today = datetime.now().strftime("%Y-%m-%d")
        for habit in habit_list:
            c.execute('INSERT OR IGNORE INTO habits (name, date) VALUES (?, ?)', (habit, today))
        c.commit()
        c.close()

# ── TO-DO LIST ──────────────────────────────────────────────
def get_today_todos():
    with _lock:
        c = _conn()
        today = datetime.now().strftime("%Y-%m-%d")
        habits = c.execute('SELECT id, name, is_done FROM habits WHERE date = ?', (today,)).fetchall()
        tasks = c.execute(
            "SELECT id, description, status FROM tasks WHERE date_added = ? OR (status = 'pending' AND date_added < ?)",
            (today, today)
        ).fetchall()
        c.close()
    combined = []
    for h in habits:
        combined.append({"id": f"h_{h['id']}", "type": "habit", "name": h['name'].capitalize(), "done": h['is_done']})
    for t in tasks:
        done = 1 if t['status'] == 'done' else 0
        combined.append({"id": f"t_{t['id']}", "type": "task", "name": t['description'], "done": done})
    return combined

def toggle_todo(todo_id):
    with _lock:
        c = _conn()
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            if todo_id.startswith("h_"):
                row_id = int(todo_id.split("_")[1])
                row = c.execute('SELECT name, is_done FROM habits WHERE id = ?', (row_id,)).fetchone()
                if not row: return None
                new_status = 0 if row['is_done'] else 1
                c.execute('UPDATE habits SET is_done = ? WHERE id = ?', (new_status, row_id))
                if new_status:
                    _update_streak_inner(c, row['name'], today)
                c.commit()
                return {"name": row['name'], "done": new_status}
            elif todo_id.startswith("t_"):
                row_id = int(todo_id.split("_")[1])
                row = c.execute('SELECT description, status FROM tasks WHERE id = ?', (row_id,)).fetchone()
                if not row: return None
                new_status = 'pending' if row['status'] == 'done' else 'done'
                c.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, row_id))
                c.commit()
                return {"name": row['description'], "done": 1 if new_status == 'done' else 0}
        finally:
            c.close()

def _update_streak_inner(conn, habit_name, today):
    row = conn.execute('SELECT current_streak, longest_streak, last_date FROM streaks WHERE habit_name = ?', (habit_name,)).fetchone()
    if row:
        last = row['last_date']
        if last:
            delta = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
            new_streak = row['current_streak'] + 1 if delta == 1 else (row['current_streak'] if delta == 0 else 1)
        else:
            new_streak = 1
        conn.execute('UPDATE streaks SET current_streak=?, longest_streak=?, last_date=? WHERE habit_name=?',
                     (new_streak, max(new_streak, row['longest_streak']), today, habit_name))
    else:
        conn.execute('INSERT INTO streaks (habit_name, current_streak, longest_streak, last_date) VALUES (?,1,1,?)',
                     (habit_name, today))

def get_streaks():
    with _lock:
        c = _conn()
        rows = c.execute('SELECT habit_name, current_streak FROM streaks').fetchall()
        c.close()
    return {r['habit_name']: r['current_streak'] for r in rows}

# ── TASKS ───────────────────────────────────────────────────
def add_task(description):
    with _lock:
        c = _conn()
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute('INSERT INTO tasks (description, date_added) VALUES (?, ?)', (description, today))
        c.commit()
        c.close()

def remove_task(row_id):
    with _lock:
        c = _conn()
        c.execute("DELETE FROM tasks WHERE id = ?", (row_id,))
        c.commit()
        c.close()

# ── MOOD ────────────────────────────────────────────────────
def log_mood(score, emoji=""):
    with _lock:
        c = _conn()
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute('INSERT INTO mood (date, score, emoji) VALUES (?,?,?) ON CONFLICT(date) DO UPDATE SET score=excluded.score, emoji=excluded.emoji',
                  (today, score, emoji))
        c.commit()
        c.close()

# ── NOTES ───────────────────────────────────────────────────
def add_note(content):
    with _lock:
        c = _conn()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute('INSERT INTO notes (content, timestamp) VALUES (?, ?)', (content, now))
        c.commit()
        c.close()

# ── DAILY LOGS ──────────────────────────────────────────────
def get_today_logs():
    with _lock:
        c = _conn()
        today = datetime.now().strftime("%Y-%m-%d")
        mood = c.execute('SELECT score, emoji FROM mood WHERE date = ?', (today,)).fetchone()
        notes = c.execute('SELECT content, timestamp FROM notes WHERE timestamp >= ?', (today,)).fetchall()
        c.close()
    return mood, [dict(n) for n in notes] if notes else []

# ══════════════════════════════════════════════════════════════
# PDF DIARY EXPORT (ALL PORTRAIT)
# ══════════════════════════════════════════════════════════════
MOOD_COLORS = {
    1: (220, 53, 69), 2: (253, 126, 20), 3: (255, 193, 7), 4: (40, 167, 69), 5: (0, 123, 255),
}
MOOD_LABELS = {1: "Awful", 2: "Low", 3: "Okay", 4: "Good", 5: "Great"}
HABIT_DONE_COLOR = (40, 167, 69)
HABIT_MISS_COLOR = (230, 230, 230)
NO_DATA_COLOR = (245, 245, 245)

def _draw_year_grid_portrait(pdf, year, data_dict, color_fn, title):
    pdf.add_page('P')
    pdf.set_font("Helvetica", 'B', 16)
    pdf.cell(0, 12, title, ln=True, align='C')
    pdf.ln(3)

    cell = 3.8
    gap = 0.5
    months_lbl = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    days_lbl = ["M","T","W","T","F","S","S"]

    months_per_row = 4
    for row_idx in range(3):
        start_x = 20
        start_y = pdf.get_y() + 8
        
        # Day labels for each row
        pdf.set_font("Helvetica", '', 4)
        for i, lbl in enumerate(days_lbl):
            pdf.set_xy(start_x - 5, start_y + i * (cell + gap))
            pdf.cell(4, cell, lbl, align='R')

        x_offset = start_x
        for m_in_row in range(months_per_row):
            month_idx = row_idx * months_per_row + m_in_row
            if month_idx >= 12: break
            
            month_num = month_idx + 1
            _, days_in_month = calendar.monthrange(year, month_num)
            
            # Print month name tilted or smaller
            pdf.set_font("Helvetica", 'B', 4)
            pdf.set_xy(x_offset, start_y - 4)
            pdf.cell(10, 3, months_lbl[month_idx][:3])

            first_dow = calendar.monthrange(year, month_num)[0]
            col = 0
            row = first_dow

            for day in range(1, days_in_month + 1):
                date_str = f"{year}-{month_num:02d}-{day:02d}"
                val = data_dict.get(date_str)
                cx, cy = x_offset + col * (cell + gap), start_y + row * (cell + gap)

                r, g, b = color_fn(val) if val is not None else NO_DATA_COLOR
                pdf.set_fill_color(r, g, b)
                pdf.rect(cx, cy, cell, cell, 'F')
                row += 1
                if row > 6:
                    row = 0; col += 1
            x_offset += (col + 1) * (cell + gap) + 4
        
        pdf.set_y(start_y + 7 * (cell + gap) + 4) # Move down for next row

def export_to_pdf():
    with _lock:
        c = _conn()
        all_moods = c.execute('SELECT * FROM mood ORDER BY date ASC').fetchall()
        all_notes = c.execute('SELECT * FROM notes ORDER BY timestamp ASC').fetchall()
        all_habits = c.execute('SELECT * FROM habits ORDER BY date ASC, name ASC').fetchall()
        all_tasks = c.execute('SELECT * FROM tasks ORDER BY date_added ASC').fetchall()
        streaks = c.execute('SELECT * FROM streaks').fetchall()
        c.close()

    year = datetime.now().year
    days = {}
    for h in all_habits:
        d = h['date']
        if d not in days: days[d] = {"habits": [], "mood": None, "notes": [], "tasks": []}
        days[d]["habits"].append({"name": h['name'], "done": h['is_done']})
    for m in all_moods:
        d = m['date']
        if d not in days: days[d] = {"habits": [], "mood": None, "notes": [], "tasks": []}
        days[d]["mood"] = {"score": m['score'], "emoji": m['emoji'] or ""}
    for n in all_notes:
        ts = n['timestamp']
        d = ts[:10] if ts else ""
        if d:
            if d not in days: days[d] = {"habits": [], "mood": None, "notes": [], "tasks": []}
            days[d]["notes"].append({"content": n['content'], "time": ts[11:16]})
    for t in all_tasks:
        d = t['date_added']
        if d:
            if d not in days: days[d] = {"habits": [], "mood": None, "notes": [], "tasks": []}
            days[d]["tasks"].append({"name": t['description'], "done": t['status'] == 'done'})

    sorted_dates = sorted(days.keys())
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # 1. COVER PAGE
    pdf.add_page('P')
    pdf.set_fill_color(25, 25, 35)
    pdf.rect(0, 0, 210, 297, 'F')
    pdf.set_text_color(240, 240, 250)
    pdf.set_font("Helvetica", 'B', 32)
    pdf.ln(100); pdf.cell(0, 20, "My Continuous Diary", ln=True, align='C')
    pdf.set_font("Helvetica", '', 14)
    pdf.cell(0, 10, f"A daily record of growth - {year}", ln=True, align='C')
    pdf.set_text_color(0, 0, 0)

    # 2. DAY-BY-DAY PAGES
    for date_str in sorted_dates:
        day_data = days[date_str]
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        pdf.add_page('P')
        pdf.set_fill_color(245, 245, 250)
        pdf.rect(0, 0, 210, 42, 'F')
        pdf.set_font("Helvetica", 'B', 22); pdf.ln(8)
        pdf.cell(0, 12, dt.strftime("%A"), ln=True, align='C')
        pdf.set_font("Helvetica", '', 13); pdf.cell(0, 8, dt.strftime("%B %d, %Y"), ln=True, align='C')
        pdf.ln(12)

        mood = day_data.get("mood")
        if mood:
            r, g, b = MOOD_COLORS.get(mood['score'], (200, 200, 200))
            pdf.set_fill_color(r, g, b); pdf.rect(15, pdf.get_y(), 180, 12, 'F')
            pdf.set_text_color(255, 255, 255); pdf.set_font("Helvetica", 'B', 11)
            pdf.cell(0, 12, f"  Mood: {mood['emoji']} {mood['score']}/5 - {MOOD_LABELS.get(mood['score'], '')}", ln=True)
            pdf.set_text_color(0, 0, 0); pdf.ln(5)

        habits = day_data.get("habits", [])
        if habits:
            pdf.set_font("Helvetica", 'B', 12); pdf.cell(0, 10, "Daily Habits", ln=True)
            pdf.set_font("Helvetica", '', 10)
            for h in habits: pdf.cell(0, 7, f"    [{'X' if h['done'] else ' '}] {h['name'].capitalize()}", ln=True)
            pdf.ln(3)

        tasks = day_data.get("tasks", [])
        if tasks:
            pdf.set_font("Helvetica", 'B', 12); pdf.cell(0, 10, "Tasks", ln=True)
            pdf.set_font("Helvetica", '', 10)
            for t in tasks: pdf.cell(0, 7, f"    [{'X' if t['done'] else ' '}] {t['name']}", ln=True)
            pdf.ln(3)

        notes = day_data.get("notes", [])
        if notes:
            pdf.set_font("Helvetica", 'B', 12); pdf.cell(0, 10, "Journal", ln=True)
            pdf.set_font("Helvetica", '', 10)
            for n in notes: pdf.multi_cell(0, 6, f"    [{n['time']}] {n['content']}"); pdf.ln(1)

    # 3. TRACKERS & STREAKS (AFTER DAILY PAGES)
    _draw_year_grid_portrait(pdf, year, {m['date']: m['score'] for m in all_moods}, lambda v: MOOD_COLORS.get(v, NO_DATA_COLOR), f"Mood Tracker - {year}")
    
    habit_names = sorted(set(h['name'] for h in all_habits))
    for name in habit_names:
        h_data = {h['date']: h['is_done'] for h in all_habits if h['name'] == name}
        _draw_year_grid_portrait(pdf, year, h_data, lambda v: (HABIT_DONE_COLOR if v else HABIT_MISS_COLOR), f"Habit: {name.capitalize()} - {year}")

    if streaks:
        pdf.add_page('P')
        pdf.set_font("Helvetica", 'B', 20); pdf.cell(0, 15, "Current Streaks", ln=True, align='C'); pdf.ln(5)
        for s in streaks:
            pdf.set_font("Helvetica", 'B', 12); pdf.cell(55, 10, f"  {s['habit_name'].capitalize()}")
            bar_w = min(s['current_streak'] * 5, 100)
            pdf.set_fill_color(*HABIT_DONE_COLOR); pdf.rect(pdf.get_x(), pdf.get_y() + 2, bar_w, 6, 'F')
            pdf.cell(bar_w + 5, 10, ""); pdf.cell(0, 10, f"{s['current_streak']} days (Best: {s['longest_streak']})", ln=True)

    filename = f"Diary_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
    pdf.output(filename)
    return filename
