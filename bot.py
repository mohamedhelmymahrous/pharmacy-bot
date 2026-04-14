import os
import io
import re
import gc
import logging
import tempfile
from difflib import SequenceMatcher

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import pdfplumber
import openpyxl

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# =
# 1. استخراج الأصناف من الـ PDF
# =

_ITEM_RE   = re.compile(r'^\d+\s+\d{3}-\d{5}-(.*?)\s+UOM:\s*(.+)$')
_TOTAL4_RE = re.compile(
    r'^Total\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)'
    r'\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)$'
)
_TOTAL3_RE = re.compile(
    r'^Total\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)$'
)

def _to_num(s):
    return float(s.replace(',', ''))

def extract_items_from_pdf(pdf_bytes):
    items   = []
    current = None
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for raw_line in text.split('\n'):
                line = raw_line.strip()
                m = _ITEM_RE.match(line)
                if m:
                    current = {'name': m.group(1).strip().upper(),
                               'uom':  m.group(2).strip()}
                    continue
                m4 = _TOTAL4_RE.match(line)
                if m4 and current:
                    current.update({'bfw': _to_num(m4.group(1)), 'received': _to_num(m4.group(2)),
                                    'issued': _to_num(m4.group(3)), 'balance': _to_num(m4.group(4))})
                    items.append(current); current = None; continue
                m3 = _TOTAL3_RE.match(line)
                if m3 and current:
                    current.update({'bfw': 0.0, 'received': _to_num(m3.group(1)),
                                    'issued': _to_num(m3.group(2)), 'balance': _to_num(m3.group(3))})
                    items.append(current); current = None
    gc.collect()
    return items


# =
# 2. مطابقة أسماء الأدوية
# =

_GENERIC = {
    'FILM','COATED','ORAL','EXTENDED','RELEASE','MODIFIED','COMP',
    'CHEWABLE','INFUSION','SACHET','POWDER','PATCH','EFFERVESCENT',
    'ENTERIC','SUBLINGUAL','TOPICAL','FORTE','PLUS','MINI','MICRO',
    'NANO','RETARD','DEPOT','LONG','SLOW','INSTANT','RAPID','SOFT',
    'HARD','GELATIN','SUPPOSITORY','ENEMA','PACK','STRIP',
}
_FORMS = {
    'TABLET','TABLETS','TABS','CAPSULE','CAPSULES','CAPS',
    'SYRUP','SUSPENSION','DROPS','CREAM','OINTMENT',
    'INJECTION','AMPOULE','INHALER','SPRAY','LOTION',
    'GEL','SOLUTION','VIAL','CHEW',
}

def _normalize(name):
    return re.sub(r'(\d+)\s+(MG|ML|MCG|IU|G)\b', r'\1\2', name)

def _key_number(name):
    m = re.search(r'(\d+(?:\.\d+)?)(?:MG|ML|MCG|IU|G)(?:/\d+(?:MG|ML))?', name)
    if m: return m.group(1)
    m = re.search(r'\b(\d+)\s*$', name.rstrip())
    if m: return m.group(1)
    return None

def _mwords(name):
    return [w for w in name.split() if len(w) > 3 and w not in _GENERIC and w not in _FORMS]

def _form(name):
    return set(name.split()) & _FORMS

def match_score(pdf_name, excel_name):
    pdf_name   = _normalize(pdf_name)
    excel_name = _normalize(excel_name)
    if pdf_name == excel_name: return 1.0
    excel_mw = _mwords(excel_name)
    pdf_mw   = _mwords(pdf_name)
    base = (any(w in pdf_name for w in excel_mw) or
            any(w in excel_name for w in pdf_mw[:2]))
    if not base:
        return SequenceMatcher(None, pdf_name[:20], excel_name[:20]).ratio()
    score = 0.75
    pn, en = _key_number(pdf_name), _key_number(excel_name)
    if pn and en:
        score += 0.20 if pn == en else -0.40
    pf, ef = _form(pdf_name), _form(excel_name)
    if pf and ef:
        score += 0.10 if pf & ef else -0.40
    elif pf and not ef:
        excel_wc = len([w for w in excel_name.split() if w not in _GENERIC])
        if not (excel_wc <= 2 and not en):
            score -= 0.10
    return max(0.0, min(score, 1.0))


# =
# 3. ملء الإكسيل
# =

def fill_excel(template_bytes, pdf_items):
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp.write(template_bytes)
        tmp_path = tmp.name
    try:
        wb = openpyxl.load_workbook(tmp_path)
        ws = wb['Sheet1']
        matched = 0
        unmatched = 0
        for row_idx in range(3, ws.max_row + 1):
            cell_name = ws.cell(row=row_idx, column=2).value
            if not cell_name or not str(cell_name).strip():
                continue
            excel_upper = str(cell_name).strip().upper()
            best_score, best_item = 0.0, None
            for item in pdf_items:
                s = match_score(item['name'], excel_upper)
                if s > best_score:
                    best_score = s; best_item = item
            if best_score >= 0.75 and best_item:
                r = row_idx
                ws.cell(row=r, column=5).value  = int(best_item['bfw'])
                ws.cell(row=r, column=6).value  = int(best_item['received']) if best_item['received'] else None
                ws.cell(row=r, column=10).value = int(best_item['issued'])   if best_item['issued']   else None
                if not ws.cell(row=r, column=9).value:
                    ws.cell(row=r, column=9).value  = f"=E{r}+F{r}+G{r}+H{r}"
                if not ws.cell(row=r, column=11).value:
                    ws.cell(row=r, column=11).value = f"=I{r}-J{r}"
                matched += 1
            else:
                unmatched += 1
        wb.save(tmp_path)
        wb.close()
        with open(tmp_path, 'rb') as f:
            result = f.read()
        return result, matched, unmatched
    finally:
        os.unlink(tmp_path)
        gc.collect()


# =
# 4. البوت
# =

pending = {}  # {chat_id: {'pdf': bytes, 'excel': bytes}}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg     = update.message
    if not msg:
        return

    # = رسائل نصية =
    if msg.text:
        if msg.text.strip() == '/start':
            await msg.reply_text(
                "أهلاً! \n\n"
                "ابعتلي ملفين:\n"
                "1 Stock Card Report (PDF)\n"
                "2 شيت الجرد template (Excel)\n\n"
                "مش مهم ترتيبهم "
            )
        else:
            await msg.reply_text(
                " ابعتلي الملفين:\n"
                "1 Stock Card Report (PDF)\n"
                "2 شيت الجرد template (Excel)"
            )
        return

    # = ملفات =
    if msg.document:
        doc   = msg.document
        fname = (doc.file_name or "").lower()
        fext  = fname.split('.')[-1]

        file_obj = await context.bot.get_file(doc.file_id)
        data     = bytes(await file_obj.download_as_bytearray())

        cid = str(chat_id)
        if cid not in pending:
            pending[cid] = {}

        if fext == 'pdf':
            pending[cid]['pdf'] = data
            # لو الإكسيل موجود بالفعل  ابدأ
            if 'excel' not in pending[cid]:
                await msg.reply_text(" استلمت الـ PDF\nابعتلي شيت الإكسيل template")
        elif fext in ('xlsx', 'xls'):
            pending[cid]['excel'] = data
            # لو الـ PDF موجود بالفعل  ابدأ
            if 'pdf' not in pending[cid]:
                await msg.reply_text(" استلمت الإكسيل\nابعتلي الـ PDF")
        else:
            await msg.reply_text(" بعتلي PDF أو Excel بس")
            return

        # لو الاتنين وصلوا  شغّل
        if 'pdf' in pending[cid] and 'excel' in pending[cid]:
            files          = pending.pop(cid)
            pdf_bytes      = files['pdf']
            template_bytes = files['excel']

            await msg.reply_text(" جاري المعالجة...")

            try:
                pdf_items = extract_items_from_pdf(pdf_bytes)

                if not pdf_items:
                    await msg.reply_text(
                        " مش قادر أقرأ الـ PDF\n"
                        "تأكد إنه Stock Card Report صح"
                    )
                    return

                excel_bytes, matched, unmatched = fill_excel(template_bytes, pdf_items)

                await context.bot.send_document(
                    chat_id=chat_id,
                    document=io.BytesIO(excel_bytes),
                    filename="جرد.xlsx",
                    caption=(
                        f" جرد جاهز!\n\n"
                        f" أصناف في الـ PDF: {len(pdf_items)}\n"
                        f" تم ملء: {matched} صنف\n"
                        f" صفر حركة: {unmatched} صنف"
                    )
                )

            except Exception as e:
                logger.error(f"Error: {e}")
                await msg.reply_text(f" حصل خطأ: {str(e)}")
            finally:
                gc.collect()


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    logger.info(" البوت شغال!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
