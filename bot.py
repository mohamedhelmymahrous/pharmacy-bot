import os
import logging

from telegram import Update
from parser import extract_items_from_pdf
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
        if not msg.document.file_name.lower().endswith(".pdf"):
            await msg.reply_text("ابعت PDF بس ❌")
            return

        file_obj = await context.bot.get_file(msg.document.file_id)
        pdf_bytes = bytes(await file_obj.download_as_bytearray())

        await msg.reply_text("جاري قراءة الملف 📄...")

        try:
            items = extract_items_from_pdf(pdf_bytes)

            if not items:
                await msg.reply_text("مش لاقي بيانات ❌")
                return

            preview = "\n".join([
                f"- {i.get('name','UNKNOWN')} {i.get('strength','')} {i.get('form','')}"
                for i in items[:5]
            ])

            await msg.reply_text(
                f"تمت القراءة ✅\n\n"
                f"عدد الأصناف: {len(items)}\n\n"
                f"{preview}"
            )

        except Exception as e:
            await msg.reply_text(f"حصل خطأ: {str(e)}")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    logger.info("bot shaghal!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
