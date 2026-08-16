"""
Telegram handler for /startups and /founders command.

Sends newly launched startups, founder handles, and contact emails directly as permanent text messages.
"""

import html
import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes

from startups_scraper import get_new_startups_and_founders

logger = logging.getLogger(__name__)


def _format_startup(s: dict) -> str:
    name = html.escape(s.get("name") or "Untitled Startup")
    pitch = html.escape(s.get("pitch") or "New product launch")
    founder = html.escape(s.get("founder") or "Founder")
    email = html.escape(s.get("email") or "hello@startup.com")
    link = s.get("link") or ""
    src = html.escape(s.get("source") or "Web")

    lines = [
        f"🚀 <b>{name}</b>",
        f"💡 <i>{pitch}</i>",
        f"👤 <b>Founder/Maker:</b> {founder}",
        f"📧 <b>Contact Email:</b> <code>{email}</code>",
    ]
    if link:
        lines.append(f"🌐 <a href=\"{html.escape(link)}\">Visit Website ({src})</a>")

    return "\n".join(lines)


async def cmd_startups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    status_msg = await update.message.reply_text(
        "🔍 Fetching newly launched startups and founder contact emails from Product Hunt & Show HN...",
        parse_mode="HTML"
    )

    loop = asyncio.get_event_loop()
    try:
        startups = await loop.run_in_executor(None, get_new_startups_and_founders, 10)
    except Exception as e:
        logger.exception("Failed to fetch startups")
        await status_msg.edit_text(f"⚠️ Error fetching startups: {html.escape(str(e))}")
        return

    if not startups:
        await status_msg.edit_text("😕 No new startup launches found right now. Please try again shortly!")
        return

    total = len(startups)
    await status_msg.edit_text(
        f"🔥 <b>Found {total} New Startups & Founders</b>\n"
        f"<i>Sending founder contact details to your chat below...</i>",
        parse_mode="HTML"
    )

    # Group 5 startups per message for super clean reading
    chunk_size = 5
    total_parts = max(1, (total - 1) // chunk_size + 1)

    for i in range(total_parts):
        start = i * chunk_size
        chunk = startups[start : start + chunk_size]
        header = f"🚀 <b>New Startups & Founder Contacts</b> — <i>Part {i + 1}/{total_parts}</i>"
        body = "\n\n".join(
            f"<b>#{start + idx + 1}</b> {_format_startup(s)}"
            for idx, s in enumerate(chunk)
        )
        text = f"{header}\n\n{body}"

        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await asyncio.sleep(0.3)
