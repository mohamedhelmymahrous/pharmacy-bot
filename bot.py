"""
bot.py
------
Telegram bot — receives PDF, processes inventory, updates Excel + JSON.
"""
import os
import logging
import tempfile
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from parser import extract_items_from_pdf
from excel_manager import load_excel, sheet_to_db_list, update_excel, save_excel
from database import build_matcher, sync_from_excel

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", 10))

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN missing from environment!")


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # --- Text ping ---
    if msg.text:
        await msg.reply_text("✅ البوت شغال")
        return

    # --- Document ---
    if not msg.document:
        return

    if not msg.document.file_name.lower().endswith(".pdf"):
        await msg.reply_text("❌ ابعت ملف PDF بس")
        return

    if msg.document.file_size / (1024 * 1024) > MAX_FILE_MB:
        await msg.reply_text(f"❌ الملف أكبر من {MAX_FILE_MB}MB")
        return

    # --- Download PDF ---
    await msg.reply_text("📥 جاري تحميل الملف...")
    try:
        file_obj = await context.bot.get_file(msg.document.file_id)
        pdf_bytes = bytes(await file_obj.download_as_bytearray())
    except Exception as e:
        logger.error(f"Download failed: {e}")
        await msg.reply_text("❌ فشل تحميل الملف")
        return

    # --- Parse PDF ---
    await msg.reply_text("📄 جاري قراءة الملف...")
    try:
        items = extract_items_from_pdf(pdf_bytes)
    except Exception as e:
        logger.error(f"PDF parse failed: {e}", exc_info=True)
        await msg.reply_text("❌ فشل قراءة الملف — تأكد إنه Stock Card Report")
        return

    if not items:
        await msg.reply_text("⚠️ الملف فاضي — مش لاقي أصناف")
        return

    # --- Load Excel + Build Matcher ---
    await msg.reply_text(f"🔄 جاري معالجة {len(items)} صنف...")
    try:
        wb, ws = load_excel()
        matcher = build_matcher()
    except Exception as e:
        logger.error(f"Setup failed: {e}", exc_info=True)
        await msg.reply_text("❌ خطأ في تحميل قاعدة البيانات")
        return

    # --- Process Items ---
    stats = {"exact": 0, "fuzzy": 0, "new": 0, "errors": 0}
    fuzzy_details = []
    new_details   = []

    for item in items:
        try:
            result = matcher.match(item)
            match_type = result.match_type

            update_excel(
                item=item,
                match_type=match_type,
                matched_item_id=result.matched_item_id,
                ws=ws,
            )

            stats[match_type] += 1

            if match_type == "fuzzy":
                fuzzy_details.append(
                    f"  • {item.get('name','')} {item.get('strength','')}\n"
                    f"    → {result.matched_item.get('name','')} "
                    f"({result.confidence_score:.0%})"
                )
            elif match_type == "new":
                new_details.append(
                    f"  • {item.get('name','')} {item.get('strength','')} "
                    f"{item.get('form','')}"
                )

        except Exception as e:
            logger.error(f"Error processing item {item.get('name')}: {e}", exc_info=True)
            stats["errors"] += 1

    # --- Save Excel + Sync JSON ---
    try:
        save_excel(wb)
        sync_from_excel()
    except Exception as e:
        logger.error(f"Save failed: {e}", exc_info=True)
        await msg.reply_text("⚠️ تمت المعالجة لكن فشل الحفظ — حاول تاني")
        return

    # --- Build Summary ---
    lines = [
        f"✅ تمت المعالجة",
        f"📦 إجمالي الأصناف: {len(items)}",
        f"",
        f"🟢 متطابق تماماً:  {stats['exact']}",
        f"🟡 متطابق جزئياً: {stats['fuzzy']}",
        f"🔴 أصناف جديدة:   {stats['new']}",
    ]

    if stats["errors"]:
        lines.append(f"⚠️ أخطاء: {stats['errors']}")

    if fuzzy_details:
        lines.append(f"\n🟡 يحتاج مراجعة:")
        lines.extend(fuzzy_details[:5])  # أول 5 بس
        if len(fuzzy_details) > 5:
            lines.append(f"  ... و {len(fuzzy_details) - 5} أكتر")

    if new_details:
        lines.append(f"\n🔴 أصناف جديدة أُضيفت:")
        lines.extend(new_details[:5])
        if len(new_details) > 5:
            lines.append(f"  ... و {len(new_details) - 5} أكتر")

    await msg.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_message))
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
