import os
import logging
from telegram import Update
from pdf_parser import extract_items_from_pdf  # ✅ غيرنا الاسم
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# ✅ validation على التوكن
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN مش موجود في الـ environment variables!")

MAX_FILE_SIZE_MB = 10  # ✅ حد أقصى للملف

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

        # ✅ تحقق من حجم الملف
        file_size_mb = msg.document.file_size / (1024 * 1024)
        if file_size_mb > MAX_FILE_SIZE_MB:
            await msg.reply_text(f"الملف كبير أوي! الحد الأقصى {MAX_FILE_SIZE_MB} MB ❌")
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
            logger.error(f"خطأ في معالجة الملف: {e}", exc_info=True)  # ✅ log تفصيلي
            await msg.reply_text("حصل خطأ أثناء معالجة الملف، حاول تاني ❌")  # ✅ رسالة عامة للمستخدم

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_message))  # ✅ filters محددة
    logger.info("bot shaghal!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
