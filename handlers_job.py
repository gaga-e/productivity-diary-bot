"""
Telegram handlers for on-demand job search.

  /job <keywords> [--loc="City, Country"]

Wire into your bot.py with:
    from handlers_job import cmd_job, btn_job_page
    application.add_handler(CommandHandler("job", cmd_job))
    application.add_handler(CallbackQueryHandler(btn_job_page, pattern=r"^jobpage:"))
"""

import asyncio
import html
import logging
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import config as cfg
import job_cache as cache
from job_scraper import parse_job_query, scrape_all_boards

logger = logging.getLogger(__name__)

SOURCE_LABELS = {
    "linkedin_indeed": "LinkedIn/Indeed",
    "remoteok": "RemoteOK",
    "adzuna": "Adzuna",
    "jooble": "Jooble",
}


def _status_summary(status: dict) -> str:
    ok = sum(1 for v in status.values() if v == "ok")
    total = len(status)
    failing = [SOURCE_LABELS.get(k, k) for k, v in status.items() if v != "ok"]
    line = f"✅ {ok}/{total} sources responded"
    if failing:
        line += f" ({', '.join(failing)} had no results or timed out)"
    return html.escape(line)


def _format_job(j: dict) -> str:
    title = html.escape(j.get("title") or "Untitled")
    company = html.escape(j.get("company") or "Unknown company")
    loc = html.escape(j.get("location") or "Unspecified")
    link = j.get("link") or ""
    src = html.escape(j.get("source") or "web")
    if link:
        return f"💼 <b>{title}</b>\n🏢 {company} — {loc}\n🔗 <a href=\"{html.escape(link)}\">{src}</a>"
    return f"💼 <b>{title}</b>\n🏢 {company} — {loc}\n({src}, no link available)"


def _build_page_text(jobs: list, page: int, page_size: int, header: str = "") -> str:
    start = page * page_size
    chunk = jobs[start:start + page_size]
    body = "\n\n".join(_format_job(j) for j in chunk)
    parts = [p for p in (header, body) if p]
    return "\n\n".join(parts) if parts else "No jobs on this page."


def _build_keyboard(search_id: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"jobpage:{search_id}:{page - 1}"))
    if page < total_pages - 1:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"jobpage:{search_id}:{page + 1}"))
    return InlineKeyboardMarkup([buttons]) if buttons else None


async def cmd_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    raw_args = " ".join(context.args) if context.args else ""

    keywords, location = parse_job_query(raw_args)
    if not keywords:
        await update.message.reply_text(
            'Usage: /job <role or keywords> [--loc="City, Country"]\n'
            'Example: /job senior backend engineer --loc="Remote"\n'
            'Tip: put quotes around an exact phrase, e.g. /job "site reliability engineer"'
        )
        return

    cooldown = cache.seconds_until_next_allowed(chat_id)
    if cooldown > 0:
        await update.message.reply_text(
            f"⏳ Please wait {int(cooldown)}s before searching again (keeps us from getting rate-limited)."
        )
        return

    escaped_keywords = html.escape(keywords)
    escaped_loc = html.escape(location)
    status_msg = await update.message.reply_text(
        f'🔍 Searching LinkedIn, Indeed, RemoteOK, Adzuna, and Jooble for "<b>{escaped_keywords}</b>" '
        f'({escaped_loc}, last {cfg.JOB_LOOKBACK_HOURS}h)...',
        parse_mode="HTML"
    )
    cache.mark_search_started(chat_id)
    asyncio.create_task(_run_search_and_respond(update, context, keywords, location, status_msg))


async def _run_search_and_respond(update, context, keywords, location, status_msg):
    chat_id = update.effective_chat.id

    cached = cache.get_cached_search(keywords, location)
    if cached:
        jobs, status = cached
        await status_msg.edit_text("✅ Found cached results from the last 15 minutes, sending...")
    else:
        loop = asyncio.get_event_loop()
        try:
            jobs, status = await loop.run_in_executor(
                None, scrape_all_boards, keywords, location, cfg.JOB_LOOKBACK_HOURS, cfg.JOB_RESULT_LIMIT
            )
            cache.cache_search_results(keywords, location, jobs, status)
        except Exception as e:
            logger.exception("Job search failed")
            await status_msg.edit_text(f"⚠️ Search failed unexpectedly: {html.escape(str(e))}")
            return

    import database as db
    unseen_jobs = db.filter_unseen_jobs(chat_id, jobs)
    if unseen_jobs:
        db.mark_jobs_seen(chat_id, unseen_jobs)
        jobs = unseen_jobs
    else:
        jobs = []

    escaped_keywords = html.escape(keywords)
    escaped_loc = html.escape(location)

    if not jobs:
        await status_msg.edit_text(
            f'😕 No new/unseen jobs found for "<b>{escaped_keywords}</b>".\n<i>(All recent listings have already been shown to you!)</i>\n\n{_status_summary(status)}',
            parse_mode="HTML"
        )
        return

    search_id = uuid.uuid4().hex[:10]
    cache.store_results_for_paging(search_id, jobs)

    header = f'Found {len(jobs)} jobs for "<b>{escaped_keywords}</b>" ({escaped_loc})\n{_status_summary(status)}'
    await status_msg.edit_text(f"✅ Found {len(jobs)} jobs for \"{escaped_keywords}\"")

    total_pages = max(1, (len(jobs) - 1) // cfg.JOB_RESULTS_PER_PAGE + 1)
    text = _build_page_text(jobs, 0, cfg.JOB_RESULTS_PER_PAGE, header=header)
    keyboard = _build_keyboard(search_id, 0, total_pages)

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


async def btn_job_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, search_id, page_str = query.data.split(":")
        page = int(page_str)
    except (ValueError, AttributeError):
        await query.answer("This search has expired, please run /job again.", show_alert=True)
        return

    jobs = cache.get_results_for_paging(search_id)
    if jobs is None:
        await query.edit_message_text("⚠️ This search has expired (15 min limit). Please run /job again.")
        return

    total_pages = max(1, (len(jobs) - 1) // cfg.JOB_RESULTS_PER_PAGE + 1)
    page = max(0, min(page, total_pages - 1))
    text = _build_page_text(jobs, page, cfg.JOB_RESULTS_PER_PAGE)
    keyboard = _build_keyboard(search_id, page, total_pages)

    await query.edit_message_text(
        text=text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True
    )
