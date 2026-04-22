"""
bot.py — Telegram pharmacy inventory bot.
Includes interactive YES/NO learning flow for unknown items.
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

# ── State management ──────────────────────────────────────────────────
# Stores pending learning requests per user
# { user_id: {"original_name": str, "strength": str, "form": str} }
pending_learning: dict = {}


# ── Learning reply handler ────────────────────────────────────────────

async def handle_learning_reply(
    user_id: int,
    text: str,
    update: Update,
) -> bool:
    """
    Handle YES/NO reply for alias learning.
    Returns True if message was consumed as a learning reply.
    """
    if user_id not in pending_learning:
        return False

    pending = pending_learning[user_id]
    original_name = pending["original_name"]
    txt = text.strip()

    # YES <correct_name>
    if txt.upper().startswith("YES"):
        parts = txt.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text(
                "⚠️ اكتب اسم الصنف الصح بعد YES\n"
                "مثال: YES ADWIFLAM"
            )
            return True  # consumed but waiting

        correct_name = parts[1].strip().upper()
        try:
            from dictionary_loader import learn_alias
            learn_alias(original_name, correct_name)
            await update.message.reply_text(
                f"✅ تم الحفظ\n"
                f"'{original_name}' → '{correct_name}'\n\n"
                f"المرة الجاية هيتعرف عليه تلقائي."
            )
        except Exception as e:
            logger.error(f"learn_alias failed: {e}")
            await update.message.reply_text("❌ حصل خطأ أثناء الحفظ")

        del pending_learning[user_id]
        return True

    # NO
    if txt.upper() == "NO":
        await update.message.reply_text(
            f"👍 تمام — '{original_name}' هيفضل في الـ unknown log."
        )
        del pending_learning[user_id]
        return True

    # Unrecognized reply while waiting
    await update.message.reply_text(
        f"❓ لسه مستني ردك على:\n"
        f"'{original_name}'\n\n"
        f"اكتب:\n"
        f"YES <اسم الصنف الصح>\n"
        f"أو\n"
        f"NO"
    )
    return True  # consumed


# ── Main handler ──────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    user_id = msg.from_user.id if msg.from_user else 0

    # Text message
    if msg.text:
        # First check if waiting for learning reply
        consumed = await handle_learning_reply(user_id, msg.text, update)
        if consumed:
            return
        # Otherwise ping
        await msg.reply_text("✅ البوت شغال\nابعت ملف PDF للمعالجة.")
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

    # Block if still waiting for learning reply
    if user_id in pending_learning:
        p = pending_learning[user_id]
        await msg.reply_text(
            f"⏳ لسه مستني ردك على:\n'{p['original_name']}'\n\n"
            f"اكتب YES <اسم صح> أو NO الأول."
        )
        return

    # Download
    await msg.reply_text("📥 جاري تحميل الملف...")
    try:
        file_obj  = await context.bot.get_file(msg.document.file_id)
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
        wb, ws        = load_excel()
        matcher, db_items = build_matcher()
    except Exception as e:
        logger.error(f"Setup failed: {e}", exc_info=True)
        await msg.reply_text("❌ خطأ في تحميل قاعدة البيانات")
        return

    row_lookup = {i["name"].upper(): i for i in db_items}

    # Process items
    stats = {"exact": 0, "fuzzy": 0, "new": 0, "errors": 0, "no_movement": 0}
    fuzzy_details = []
    new_items_for_learning = []   # collect NEW items for learning prompt

    for item in items:
        received = float(item.get("received") or 0)
        issued   = float(item.get("issued")   or 0)
        if received == 0 and issued == 0:
            stats["no_movement"] += 1
            continue

        try:
            result     = matcher.match(item)
            match_type = result.match_type

            matched = None
            if result.matched_item:
                m_name = result.matched_item.get("name", "").upper()
                matched = row_lookup.get(m_name)
                if not matched:
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
                matched_name = (result.matched_item.get("name", "?")
                                if result.matched_item else "?")
                fuzzy_details.append(
                    f"  • {item.get('name','')} {item.get('strength','')}\n"
                    f"    → {matched_name} ({result.confidence_score:.0%})"
                )

            elif match_type == "new":
                new_items_for_learning.append(item)

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

    # ── Summary ────────────────────────────────────────────────────────
    total_processed = stats["exact"] + stats["fuzzy"] + stats["new"]
    lines = [
        "✅ تمت المعالجة",
        f"📦 إجمالي الأصناف: {len(items)}",
        f"   بدون حركة:      {stats['no_movement']}",
        f"   تمت معالجتها:   {total_processed}",
        "",
        f"🟢 متطابق تماماً:  {stats['exact']}",
        f"🟡 متطابق جزئياً: {stats['fuzzy']}",
        f"🔴 أصناف جديدة:   {stats['new']}",
    ]

    if stats["errors"]:
        lines.append(f"⚠️ أخطاء: {stats['errors']}")

    if fuzzy_details:
        lines.append("\n🟡 يحتاج مراجعة:")
        lines.extend(fuzzy_details[:5])
        if len(fuzzy_details) > 5:
            lines.append(f"  ... و {len(fuzzy_details)-5} أكتر")

    if new_items_for_learning:
        lines.append("\n🔴 أصناف جديدة:")
        for it in new_items_for_learning[:5]:
            lines.append(f"  • {it.get('name','')} {it.get('form','')}")
        if len(new_items_for_learning) > 5:
            lines.append(f"  ... و {len(new_items_for_learning)-5} أكتر")

    await msg.reply_text("\n".join(lines))

    # ── Learning prompt: ask about FIRST unknown only ──────────────────
    if new_items_for_learning and user_id not in pending_learning:
        first_new = new_items_for_learning[0]
        name      = str(first_new.get("name",     "") or "").upper()
        form      = str(first_new.get("form",     "") or
                        first_new.get("uom",      "") or "")
        strength  = str(first_new.get("strength", "") or "")

        pending_learning[user_id] = {
            "original_name": name,
            "strength":      strength,
            "form":          form,
        }

        learn_msg = (
            f"❓ صنف جديد اتسجل:\n"
            f"الاسم:    {name}\n"
            f"الشكل:    {form or '—'}\n"
            f"القوة:    {strength or '—'}\n\n"
            f"هل هو اسم تاني لصنف موجود؟\n"
            f"اكتب:\n"
            f"YES <الاسم الصح>\n"
            f"أو\n"
            f"NO"
        )
        await msg.reply_text(learn_msg)


# ── Main ──────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(
        MessageHandler(filters.TEXT | filters.Document.ALL, handle_message)
    )
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
