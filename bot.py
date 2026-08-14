import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from config import TELEGRAM_TOKEN
import handlers
from handlers_job import cmd_job, btn_job_page
import database as db
from scheduler import setup_scheduler
from keep_alive import keep_alive

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def post_init(application):
    db.init_db()
    setup_scheduler(application)
    print("Scheduler initialized on startup! ⏰")

def main():
    db.init_db()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("help", handlers.help_command))
    app.add_handler(CommandHandler("job", cmd_job))
    app.add_handler(CallbackQueryHandler(btn_job_page, pattern=r"^jobpage:"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handlers.handle_message))
    app.add_handler(CallbackQueryHandler(handlers.button_handler))

    print("Bot is live! 🚀")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "edited_message", "callback_query"]
    )

if __name__ == '__main__':
    keep_alive()
    main()
