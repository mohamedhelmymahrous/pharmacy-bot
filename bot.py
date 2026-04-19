"""
bot.py
------
Telegram bot — receives Stock Card Report PDF,
matches items against Excel inventory, updates Excel + JSON.
"""
import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from pdf_parser import extract_items_from_pdf
from excel_manager import load_excel, update_excel, save_excel
from database import build_matcher, sync_from_excel

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
MAX_FILE_MB    = int(os.environ.get("MAX_FILE_MB", 10))

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN missing from environment!")


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # Text ping
    if msg.text:
        await msg.reply_text("✅ البوت شغال")
        return

    if not msg.document:
        return

    # Validate file
    if not msg.document.file_name.lower().endswith(".pdf"):
        await msg.reply_text("❌ ابعت ملف PDF بس")
        return

    if msg.document.file_size / (1024 * 1024) > MAX_FILE_MB:
        await msg.reply_text(f"❌ الملف أكبر من {MAX_FILE_MB}MB")
        return

    # Download
    await msg.reply_text("📥 جاري تحميل الملف...")
    try:
        file_obj = await context.bot.get_file(msg.document.file_id)
        pdf_bytes = bytes(await file_obj.download_as_bytearray())
    except Exception as e:
        logger.error(f"Download failed: {e}")
        await msg.reply_text("❌ فشل تحميل الملف")
        return

    # Parse PDF
    await msg.reply_text("📄 جاري قراءة الملف...")
    try:
        items = extract_items_from_pdf(pdf_bytes)
    except Exception as e:
        logger.error(f"Parse failed: {e}", exc_info=True)
        await msg.reply_text("❌ فشل قراءة الملف — تأكد إنه Stock Card Report")
        return

    if not items:
        await msg.reply_text("⚠️ الملف فاضي — مش لاقي أصناف")
        return

    # Load Excel + build matcher
    await msg.reply_text(f"🔄 جاري معالجة {len(items)} صنف...")
try:
    wb, ws = load_excel()
    matcher, db_items = build_matcher()

    # 🔍 CHECK DATABASE FROM EXCEL
    print("🔵 DB SIZE:", len(db_items))
    print("🔵 SAMPLE ITEM:", db_items[0] if db_items else "EMPTY")

except Exception as e:
    logger.error(f"Setup failed: {e}", exc_info=True)
    await msg.reply_text("❌ خطأ في تحميل قاعدة البيانات")
    return

    # Build lookup: id → item with _row
    row_lookup = {i["name"].upper(): i for i in db_items}

    # Process items
    stats = {"exact": 0, "fuzzy": 0, "new": 0, "errors": 0, "no_movement": 0}
    fuzzy_details = []
    new_details   = []

    for item in items:
        # Skip items with no movement at all
        received = float(item.get("received") or 0)
        issued   = float(item.get("issued")   or 0)
        if received == 0 and issued == 0:
            stats["no_movement"] += 1
            continue

        try:
            result = matcher.match(item)
            match_type = result.match_type

            # Get matched_item with _row info
            matched = None
            if result.matched_item:
                # Find the db_item that has _row
                m_name = result.matched_item.get("name", "").upper()
                matched = row_lookup.get(m_name)
                if not matched:
                    # fallback: search by name similarity
                    for db_i in db_items:
                        if db_i["name"].upper() == m_name:
                            matched = db_i
                            break

            update_excel(
                item=item,
                match_type=match_type,
                matched_item=matched,
                ws=ws,
            )

            stats[match_type] += 1

            if match_type == "fuzzy":
                fuzzy_details.append(
                    f"  • {item.get('name','')} {item.get('strength','')}\n"
                    f"    → {result.matched_item.get('name','') if result.matched_item else '?'} "
                    f"({result.confidence_score:.0%})"
                )
            elif match_type == "new":
                new_details.append(
                    f"  • {item.get('name','')} {item.get('form','')}"
                )

        except Exception as e:
            logger.error(f"Error on {item.get('name')}: {e}", exc_info=True)
            stats["errors"] += 1

    # Save Excel + sync JSON
    try:
        save_excel(wb)
        sync_from_excel()
    except Exception as e:
        logger.error(f"Save failed: {e}", exc_info=True)
        await msg.reply_text("⚠️ تمت المعالجة لكن فشل الحفظ")
        return

    # Build summary
    total_processed = stats["exact"] + stats["fuzzy"] + stats["new"]
    lines = [
        "✅ تمت المعالجة",
        f"📦 إجمالي الأصناف: {len(items)}",
        f"   منها بدون حركة: {stats['no_movement']}",
        f"   تمت معالجتها:   {total_processed}",
        "",
        f"🟢 متطابق تماماً:  {stats['exact']}",
        f"🟡 متطابق جزئياً: {stats['fuzzy']}",
        f"🔴 أصناف جديدة:   {stats['new']}",
    ]

    if stats["errors"]:
        lines.append(f"⚠️ أخطاء: {stats['errors']}")

    if fuzzy_details:
        lines.append(f"\n🟡 يحتاج مراجعة:")
        lines.extend(fuzzy_details[:5])
        if len(fuzzy_details) > 5:
            lines.append(f"  ... و {len(fuzzy_details)-5} أكتر")

    if new_details:
        lines.append(f"\n🔴 أصناف جديدة أُضيفت:")
        lines.extend(new_details[:5])
        if len(new_details) > 5:
            lines.append(f"  ... و {len(new_details)-5} أكتر")

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
