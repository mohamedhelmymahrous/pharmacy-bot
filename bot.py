import os
import logging

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    if msg.text:
        await msg.reply_text("bot shaghal ok")

    elif msg.document:
        await msg.reply_text("file wesil - processing gai")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    logger.info("bot shaghal!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
