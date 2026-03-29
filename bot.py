import os
import json
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import pdfplumber
import io
import random

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_KEY     = os.environ.get("GEMINI_KEY", "")
ADMIN_CHAT_ID  = int(os.environ.get("ADMIN_CHAT_ID", "0"))
CLIENTS_FILE   = "clients.json"

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-1.5-pro")

def load_clients():
    if os.path.exists(CLIENTS_FILE):
        with open(CLIENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_clients(clients):
    with open(CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(clients, f, ensure_ascii=False, indent=2)

def is_active(chat_id):
    if str(chat_id) == str(ADMIN_CHAT_ID):
        return True
    return load_clients().get(str(chat_id), {}).get("active", False)

pending_files   = {}
pending_answers = {}

def extract_pdf_text(data: bytes) -> str:
    text = ""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text

def excel_to_text(data: bytes, max_rows=300) -> str:
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    out = []
    for name in wb.sheetnames:
        ws = wb[name]
        out.append(f"\n=== شيت: {name} ===")
        for i, row in enumerate(ws.iter_rows(values_only=True), 1):
            if i > max_rows:
                break
            vals = [str(v) if v is not None else "" for v in row]
            if any(v.strip() for v in vals):
                out.append(" | ".join(vals))
    return "\n".join(out)

async def call_gemini(prompt: str) -> str:
    response = await asyncio.to_thread(model.generate_content, prompt)
    return response.text

def parse_json_safe(text: str):
    import re
    text = text.strip()
    m = re.search(r'[\[{]', text)
    if not m:
        return None
    start = m.start()
    end = max(text.rfind("]"), text.rfind("}"))
    if end == -1:
        return None
    try:
        return json.loads(text[start:end+1])
    except:
        return None

async def classify_files(files):
    summaries = []
    for ftype, data, fname in files:
        preview = extract_pdf_text(data)[:600] if ftype == "pdf" else excel_to_text(data, max_rows=10)[:600]
        summaries.append(f"ملف: {fname} ({ftype})\nمحتوى:\n{preview}\n---")

    prompt = f"""صنّف كل ملف في واحدة من الفئات:
- inventory_prev: شيت جرد سابق (Excel أدوية ورصيد)
- pdf_incoming: PDF وارد (أدوية واردة)
- pdf_dispensed: PDF منصرف (أدوية منصرفة)
- patients_sheet: شيت مرضى (Excel أسماء مرضى)
- kpi_sheet: شيت مؤشرات سنوي (Excel شيتات كتير)

الملفات:
{chr(10).join(summaries)}

أرجع JSON فقط:
[{{"filename": "...", "category": "..."}}, ...]"""

    result = await call_gemini(prompt)
    return parse_json_safe(result)

async def build_inventory(prev_data: bytes, incoming_text: str, dispensed_text: str, month: str) -> bytes:
    prev_text = excel_to_text(prev_data, max_rows=500)

    prompt = f"""أنت صيدلاني خبير. ابني شيت جرد شهر {month}.

شيت الجرد السابق:
{prev_text[:4000]}

بيانات الوارد:
{incoming_text[:2000]}

بيانات المنصرف:
{dispensed_text[:2000]}

القواعد:
- رصيد أول الشهر = المتبقي من الشهر السابق
- الوارد = من ملف الوارد (0 لو مش موجود)
- المجموع = رصيد أول الشهر + الوارد
- المنصرف = من ملف المنصرف (0 لو مش موجود)
- المتبقي = المجموع - المنصرف
- طابق أسماء الأدوية بذكاء

أرجع JSON فقط:
[{{"اسم_الصنف": "...", "رصيد_اول_الشهر": 0, "الوارد": 0, "المجموع": 0, "المنصرف": 0, "المتبقي": 0}}, ...]"""

    result = await call_gemini(prompt)
    rows = parse_json_safe(result) or []

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = month
    ws.sheet_view.rightToLeft = True

    headers = ["اسم الصنف", "رصيد أول الشهر", "الوارد", "المجموع", "المنصرف", "المتبقي"]
    hdr_fill = PatternFill("solid", fgColor="1B3A6B")
    hdr_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    bd = Border(*[Side(style="thin", color="CCCCCC")]*4)

    for col, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.fill = hdr_fill
        c.font = hdr_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = bd

    ws.row_dimensions[1].height = 30
    for i, w in enumerate([35,18,12,12,12,12], 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    for r, row in enumerate(rows, 2):
        rf = PatternFill("solid", fgColor="F8F9FA" if r%2==0 else "FFFFFF")
        vals = [row.get("اسم_الصنف",""), row.get("رصيد_اول_الشهر",0),
                row.get("الوارد",0), row.get("المجموع",0),
                row.get("المنصرف",0), row.get("المتبقي",0)]
        for col, val in enumerate(vals, 1):
            c = ws.cell(row=r, column=col, value=val)
            c.fill = rf
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(horizontal="center" if col>1 else "right", vertical="center")
            c.border = bd
        if isinstance(vals[5], (int,float)) and vals[5] < 0:
            ws.cell(row=r, column=6).font = Font(name="Arial", size=10, color="C0392B", bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

async def fill_kpi_sheet(kpi_data: bytes, patients_data: bytes, month: str):
    patients_text = excel_to_text(patients_data, max_rows=300)

    wb_p = openpyxl.load_workbook(io.BytesIO(patients_data), data_only=True)
    ws_p = wb_p.active
    all_patients = []
    for row in ws_p.iter_rows(min_row=2, values_only=True):
        name = row[0]
        mid  = row[1] if len(row) > 1 else None
        if name and str(name).strip():
            all_patients.append((str(name).strip(), str(mid).strip() if mid else ""))

    sample1 = random.sample(all_patients, min(30, len(all_patients)))
    sample2 = random.sample(all_patients, min(30, len(all_patients)))

    stats_prompt = f"""من شيت المرضى ده استخرج الأرقام:
{patients_text[:3000]}

أرجع JSON فقط:
{{
  "total_prescriptions": 0,
  "ab_prescriptions": 0,
  "inappropriate_ab": 0,
  "ab_protocol_adherence": 0,
  "current_medication_count": 0,
  "appropriateness_count": 0,
  "counselling_count": 0,
  "interventions_count": 0
}}"""

    stats_result = await call_gemini(stats_prompt)
    stats = parse_json_safe(stats_result) or {}

    total  = stats.get("total_prescriptions", len(all_patients))
    ab     = stats.get("ab_prescriptions", 0)
    inapp  = stats.get("inappropriate_ab", 0)
    proto  = stats.get("ab_protocol_adherence", ab)
    curmed = stats.get("current_medication_count", total)
    appr   = stats.get("appropriateness_count", total)
    couns  = stats.get("counselling_count", total)
    interv = stats.get("interventions_count", 0)

    months_ar = ["يناير","فبراير","مارس","ابريل","مايو","يونيو",
                 "يوليو","اغسطس","سبتمبر","اكتوبر","نوفمبر","ديسمبر"]
    month_col = 2
    for i, m in enumerate(months_ar, 2):
        if m in month or month in m:
            month_col = i
            break

    wb_kpi = openpyxl.load_workbook(io.BytesIO(kpi_data))

    def write_month(sheet_name, numerator, denominator=None, row_num=3, row_total=4):
        if sheet_name not in wb_kpi.sheetnames:
            return
        ws = wb_kpi[sheet_name]
        ws.cell(row=row_num, column=month_col, value=numerator)
        if denominator is not None:
            ws.cell(row=row_total, column=month_col, value=denominator)

    write_month("AB orders",             ab,    total)
    write_month("AB inappropriate use",  inapp, ab)
    write_month("AB protocols adherence",proto, ab)
    write_month("current medication",    curmed,total)
    write_month("appropriatness ",       appr,  total)
    write_month("councelling",           couns, total)
    write_month("cost savings",          interv)

    if "اسماء المرضي" in wb_kpi.sheetnames:
        ws_names = wb_kpi["اسماء المرضي"]
        for i, (name, mid) in enumerate(sample1, 5):
            ws_names.cell(row=i, column=1, value=i-4)
            ws_names.cell(row=i, column=2, value=name)
            ws_names.cell(row=i, column=3, value=mid)
            for col in range(4, 8):
                ws_names.cell(row=i, column=col, value=1)

    if "شيت الاعطاء " in wb_kpi.sheetnames:
        ws_give = wb_kpi["شيت الاعطاء "]
        for i, (name, mid) in enumerate(sample2, 5):
            ws_give.cell(row=i, column=1, value=i-4)
            ws_give.cell(row=i, column=2, value=name)
            ws_give.cell(row=i, column=3, value=mid)
            ws_give.cell(row=i, column=4, value=1)

    buf = io.BytesIO()
    wb_kpi.save(buf)

    questions = f"""✅ ملأت اللي أقدر عليه تلقائياً!

محتاج منك الأرقام دي لشهر {month}:

1️⃣ التخزين السليم:
   أدوية الثلاجة / الإجمالي
   أدوية الضوء / الإجمالي
   LASA شكل / الإجمالي
   LASA نطق / الإجمالي

2️⃣ High Alert & High Concentration:
   High Alert / الإجمالي
   High Concentration / الإجمالي
   مراجعة ثنائية / إجمالي الوصفات

3️⃣ الرواكد / الإجمالي
4️⃣ النواقص / الإجمالي
5️⃣ كروت التعريف / الإجمالي
6️⃣ عدد الأخطاء الدوائية
7️⃣ عدد Near Miss
8️⃣ عدد الآثار العكسية
9️⃣ عدد التدخلات الدوائية
🔟 التوفير بالجنيه

ابعتهم بالترتيب هكذا (مثال):
12/12 | 20/20 | 15/15 | 8/8 | 16/16 | 1/1 | 117/117 | 0/220 | 9/220 | 195/195 | 3 | 3 | 0 | 0 | 0"""

    return buf.getvalue(), questions

async def complete_kpi(kpi_data: bytes, answers_text: str, month: str) -> bytes:
    parts = [p.strip() for p in answers_text.replace("\n","|").split("|") if p.strip()]

    def get_pair(idx):
        if idx < len(parts) and "/" in parts[idx]:
            a, b = parts[idx].split("/", 1)
            try:
                return int(a.strip()), int(b.strip())
            except:
                return 0, 0
        return 0, 0

    def get_single(idx):
        if idx < len(parts):
            try:
                return int(parts[idx].strip())
            except:
                return 0
        return 0

    fridge_ok,  fridge_tot  = get_pair(0)
    light_ok,   light_tot   = get_pair(1)
    lasa_s_ok,  lasa_s_tot  = get_pair(2)
    lasa_n_ok,  lasa_n_tot  = get_pair(3)
    ha_ok,      ha_tot      = get_pair(4)
    hc_ok,      hc_tot      = get_pair(5)
    double_ok,  double_tot  = get_pair(6)
    stale_n,    stale_tot   = get_pair(7)
    short_n,    short_tot   = get_pair(8)
    cards_ok,   cards_tot   = get_pair(9)
    errors                  = get_single(10)
    near_miss               = get_single(11)
    adv_events              = get_single(12)
    interventions           = get_single(13)
    savings                 = get_single(14)

    months_ar = ["يناير","فبراير","مارس","ابريل","مايو","يونيو",
                 "يوليو","اغسطس","سبتمبر","اكتوبر","نوفمبر","ديسمبر"]
    month_col = 2
    for i, m in enumerate(months_ar, 2):
        if m in month or month in m:
            month_col = i
            break

    wb = openpyxl.load_workbook(io.BytesIO(kpi_data))

    def w(sheet, row, val):
        if sheet in wb.sheetnames:
            wb[sheet].cell(row=row, column=month_col, value=val)

    if "التخزين السليم للادوية " in wb.sheetnames:
        ws = wb["التخزين السليم للادوية "]
        base = (month_col - 2) * 7 + 2
        for i, (v, t) in enumerate([(fridge_ok,fridge_tot),(light_ok,light_tot),(lasa_s_ok,lasa_s_tot),(lasa_n_ok,lasa_n_tot)]):
            ws.cell(row=5, column=base+i, value=v)
            ws.cell(row=7, column=base+i, value=t)

    if "ادوية عالية الخطورة والتركيز" in wb.sheetnames:
        ws = wb["ادوية عالية الخطورة والتركيز"]
        base = (month_col - 2) * 5 + 2
        ws.cell(row=5, column=base,   value=ha_ok)
        ws.cell(row=5, column=base+1, value=hc_ok)
        ws.cell(row=5, column=base+2, value=double_ok)
        ws.cell(row=7, column=base,   value=ha_tot)
        ws.cell(row=7, column=base+1, value=hc_tot)
        ws.cell(row=7, column=base+2, value=double_tot)

    if "HA & HC اثناء الاعطاء" in wb.sheetnames:
        ws = wb["HA & HC اثناء الاعطاء"]
        ws.cell(row=3, column=month_col, value=double_ok)
        ws.cell(row=4, column=month_col, value=double_tot)

    if "نسبة الرواكد والنواقص" in wb.sheetnames:
        ws = wb["نسبة الرواكد والنواقص"]
        ws.cell(row=3,  column=month_col, value=stale_n)
        ws.cell(row=4,  column=month_col, value=stale_tot)
        ws.cell(row=27, column=month_col, value=short_n)
        ws.cell(row=28, column=month_col, value=short_tot)

    if "كروت التعريف" in wb.sheetnames:
        ws = wb["كروت التعريف"]
        ws.cell(row=3, column=month_col, value=cards_ok)
        ws.cell(row=4, column=month_col, value=cards_tot)

    w("medication error",  3, errors)
    if "near miss" in wb.sheetnames:
        wb["near miss"].cell(row=3, column=month_col, value=near_miss)
        wb["near miss"].cell(row=4, column=month_col, value=errors)
    w("الاثار العكسية", 3, adv_events)
    if "cost savings" in wb.sheetnames:
        wb["cost savings"].cell(row=3, column=month_col, value=interventions)
        wb["cost savings"].cell(row=4, column=month_col, value=savings)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# ── MESSAGE HANDLER ───────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg     = update.message
    if not msg:
        return

    # ── ADMIN COMMANDS ──
    if chat_id == ADMIN_CHAT_ID and msg.text:
        text = msg.text.strip()

        if text.startswith("تأكيد "):
            name = text[6:].strip()
            clients = load_clients()
            for cid, data in clients.items():
                if data.get("name") == name:
                    clients[cid]["active"] = True
                    save_clients(clients)
                    await context.bot.send_message(int(cid), f"✅ تم تأكيد اشتراكك يا {name}!\nالبوت شغّال معاك 🎉")
                    await msg.reply_text(f"✅ تم تفعيل {name}")
                    return
            await msg.reply_text(f"❌ مش لاقي {name}")
            return

        if text.startswith("رفض "):
            name = text[5:].strip()
            clients = load_clients()
            for cid, data in clients.items():
                if data.get("name") == name:
                    await context.bot.send_message(int(cid), "⚠️ لم يتم التحقق من الدفع، تواصل مع الإدارة")
                    await msg.reply_text(f"تم إبلاغ {name}")
                    return
            return

        if text.startswith("وقف "):
            name = text[5:].strip()
            clients = load_clients()
            for cid, data in clients.items():
                if data.get("name") == name:
                    clients[cid]["active"] = False
                    save_clients(clients)
                    await context.bot.send_message(int(cid), "⛔ تم إيقاف اشتراكك. للتجديد تواصل مع الإدارة.")
                    await msg.reply_text(f"✅ تم إيقاف {name}")
                    return

        if text.startswith("فعل "):
            name = text[5:].strip()
            clients = load_clients()
            for cid, data in clients.items():
                if data.get("name") == name:
                    clients[cid]["active"] = True
                    save_clients(clients)
                    await msg.reply_text(f"✅ تم تفعيل {name}")
                    return

        if text == "عملاء":
            clients = load_clients()
            if not clients:
                await msg.reply_text("مفيش عملاء لحد دلوقتي.")
                return
            lines = ["📋 قائمة العملاء:\n"]
            for cid, data in clients.items():
                status = "✅ فعّال" if data.get("active") else "⛔ موقوف"
                lines.append(f"{data.get('name','؟')} — {status} — ID: {cid}")
            await msg.reply_text("\n".join(lines))
            return

        if text.startswith("اضف عميل"):
            parts = text.replace("اضف عميل","").strip().split()
            if len(parts) >= 2:
                name, cid = parts[0], parts[1]
                clients = load_clients()
                clients[cid] = {"name": name, "active": True, "joined": str(datetime.now().date())}
                save_clients(clients)
                await msg.reply_text(f"✅ تم إضافة {name}")
                try:
                    await context.bot.send_message(int(cid), f"أهلاً يا {name}! 👋\nتم تفعيل اشتراكك في بوت الصيدلية.")
                except:
                    pass
            else:
                await msg.reply_text("الصيغة: اضف عميل [الاسم] [chat_id]")
            return

        if text == "ابعت فواتير":
            clients = load_clients()
            count = 0
            for cid, data in clients.items():
                try:
                    await context.bot.send_message(int(cid),
                        f"مرحباً د. {data.get('name','')} 👋\n\n"
                        f"اشتراك شهر {datetime.now().strftime('%B')} = 299 جنيه\n\n"
                        f"برجاء السداد وإرسال صورة الإيصال ✅")
                    count += 1
                except:
                    pass
            await msg.reply_text(f"✅ تم إرسال الفواتير لـ {count} عميل")
            return

        if text == "/start":
            await msg.reply_text(
                "👨‍💼 لوحة تحكم المدير\n\n"
                "الأوامر:\n"
                "• عملاء\n"
                "• اضف عميل [اسم] [id]\n"
                "• وقف [اسم]\n"
                "• فعل [اسم]\n"
                "• تأكيد [اسم]\n"
                "• رفض [اسم]\n"
                "• ابعت فواتير"
            )
            return

    # ── NOT ACTIVE ──
    if not is_active(chat_id):
        await msg.reply_text("⛔ اشتراكك غير فعّال.\nللاشتراك تواصل مع الإدارة.")
        return

    cid_str = str(chat_id)

    # ── PAYMENT PHOTO ──
    if msg.photo and chat_id != ADMIN_CHAT_ID:
        clients = load_clients()
        client_name = clients.get(cid_str, {}).get("name", str(chat_id))
        await msg.reply_text("📨 تم استلام صورة الإيصال، جاري المراجعة...")
        await context.bot.forward_message(ADMIN_CHAT_ID, chat_id, msg.message_id)
        await context.bot.send_message(ADMIN_CHAT_ID,
            f"💰 {client_name} بعت إيصال دفع\n\n"
            f"للتأكيد: تأكيد {client_name}\n"
            f"للرفض: رفض {client_name}")
        return

    # ── DOCUMENTS ──
    if msg.document:
        if cid_str not in pending_files:
            pending_files[cid_str] = []

        doc   = msg.document
        fname = doc.file_name or "file"
        fext  = fname.split(".")[-1].lower()
        ftype = "pdf" if fext == "pdf" else "excel"

        file_obj = await context.bot.get_file(doc.file_id)
        data = bytes(await file_obj.download_as_bytearray())
        pending_files[cid_str].append((ftype, data, fname))

        count = len(pending_files[cid_str])
        files = pending_files[cid_str]

        pdfs   = [(t,d,n) for t,d,n in files if t=="pdf"]
        excels = [(t,d,n) for t,d,n in files if t=="excel"]

        # ── الحالة 1: 2 ملفات Excel (مرضى + مؤشرات) بدون PDF ──
        if count >= 2 and len(pdfs) == 0 and len(excels) >= 2:
            await msg.reply_text("✅ استلمت ملفات المرضى والمؤشرات!\nجاري التحليل... ⏳")
            files = pending_files.pop(cid_str)
            excels = [(t,d,n) for t,d,n in files if t=="excel"]
            try:
                classifications = await classify_files(files)
                file_map = {}
                if classifications:
                    for item in classifications:
                        for ftype2, data2, fname2 in files:
                            if fname2 == item.get("filename",""):
                                file_map[item.get("category","")] = (ftype2, data2, fname2)

                patients = file_map.get("patients_sheet", excels[0] if len(excels)>0 else None)
                kpi      = file_map.get("kpi_sheet",      excels[1] if len(excels)>1 else None)

                if kpi and patients:
                    month = datetime.now().strftime("%B %Y")
                    await context.bot.send_message(chat_id, "📋 بنملأ شيت المؤشرات...")
                    kpi_partial, questions = await fill_kpi_sheet(kpi[1], patients[1], month)
                    pending_answers[cid_str] = {"kpi_data": kpi_partial, "month": month}
                    await context.bot.send_message(chat_id, questions)
                else:
                    await context.bot.send_message(chat_id, "❌ مش قادر أحدد الملفات. تأكد إنك بعتت شيت المرضى وشيت المؤشرات.")
            except Exception as e:
                logger.error(f"Error: {e}")
                await context.bot.send_message(chat_id, f"❌ حصل خطأ: {str(e)}")
            return

        # ── الحالة 2: 3 ملفات (جرد + وارد + منصرف) ──
        if count >= 3 and len(pdfs) >= 2 and len(excels) >= 1:
            await msg.reply_text("✅ استلمت ملفات الجرد!\nجاري بناء شيت الجرد... ⏳")
            files = pending_files.pop(cid_str)
            pdfs   = [(t,d,n) for t,d,n in files if t=="pdf"]
            excels = [(t,d,n) for t,d,n in files if t=="excel"]
            try:
                classifications = await classify_files(files)
                file_map = {}
                if classifications:
                    for item in classifications:
                        for ftype2, data2, fname2 in files:
                            if fname2 == item.get("filename",""):
                                file_map[item.get("category","")] = (ftype2, data2, fname2)

                inv_prev = file_map.get("inventory_prev", excels[0] if len(excels)>0 else None)
                pdf_in   = file_map.get("pdf_incoming",  pdfs[0]   if len(pdfs)>0   else None)
                pdf_dis  = file_map.get("pdf_dispensed", pdfs[1]   if len(pdfs)>1   else None)

                month = datetime.now().strftime("%B %Y")

                if inv_prev and pdf_in and pdf_dis:
                    inc_text = extract_pdf_text(pdf_in[1])
                    dis_text = extract_pdf_text(pdf_dis[1])
                    inv_xlsx = await build_inventory(inv_prev[1], inc_text, dis_text, month)
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=io.BytesIO(inv_xlsx),
                        filename=f"جرد_{month}.xlsx",
                        caption=f"✅ شيت جرد {month} جاهز!"
                    )
                else:
                    await context.bot.send_message(chat_id, "❌ مش قادر أحدد الملفات. تأكد من الملفات الـ 3.")
            except Exception as e:
                logger.error(f"Error: {e}")
                await context.bot.send_message(chat_id, f"❌ حصل خطأ: {str(e)}")
            return

        # ── الحالة 3: 5 ملفات (جرد + وارد + منصرف + مرضى + مؤشرات) ──
        if count >= 5:
            await msg.reply_text("✅ استلمت كل الملفات!\nجاري التحليل... ⏳\n(هياخد دقيقتين تقريباً)")
            files = pending_files.pop(cid_str)
            pdfs   = [(t,d,n) for t,d,n in files if t=="pdf"]
            excels = [(t,d,n) for t,d,n in files if t=="excel"]
            try:
                classifications = await classify_files(files)
                file_map = {}
                if classifications:
                    for item in classifications:
                        for ftype2, data2, fname2 in files:
                            if fname2 == item.get("filename",""):
                                file_map[item.get("category","")] = (ftype2, data2, fname2)

                inv_prev = file_map.get("inventory_prev", excels[0] if len(excels)>0 else None)
                pdf_in   = file_map.get("pdf_incoming",  pdfs[0]   if len(pdfs)>0   else None)
                pdf_dis  = file_map.get("pdf_dispensed", pdfs[1]   if len(pdfs)>1   else None)
                patients = file_map.get("patients_sheet",excels[1] if len(excels)>1 else None)
                kpi      = file_map.get("kpi_sheet",     excels[2] if len(excels)>2 else None)

                month = datetime.now().strftime("%B %Y")

                if inv_prev and pdf_in and pdf_dis:
                    await context.bot.send_message(chat_id, "📊 بنبني شيت الجرد...")
                    inc_text = extract_pdf_text(pdf_in[1])
                    dis_text = extract_pdf_text(pdf_dis[1])
                    inv_xlsx = await build_inventory(inv_prev[1], inc_text, dis_text, month)
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=io.BytesIO(inv_xlsx),
                        filename=f"جرد_{month}.xlsx",
                        caption=f"✅ شيت جرد {month} جاهز!"
                    )

                if kpi and patients:
                    await context.bot.send_message(chat_id, "📋 بنملأ شيت المؤشرات...")
                    kpi_partial, questions = await fill_kpi_sheet(kpi[1], patients[1], month)
                    pending_answers[cid_str] = {"kpi_data": kpi_partial, "month": month}
                    await context.bot.send_message(chat_id, questions)

            except Exception as e:
                logger.error(f"Error: {e}")
                await context.bot.send_message(chat_id, f"❌ حصل خطأ: {str(e)}\nجرب تبعت الملفات تاني.")
            return

        # لسه مستني ملفات
        await msg.reply_text(
            f"📎 استلمت {count} ملف...\n\n"
            f"ابعتلي:\n"
            f"• 3 ملفات للجرد بس (جرد + وارد PDF + منصرف PDF)\n"
            f"• 2 ملفات للمؤشرات بس (مرضى + مؤشرات)\n"
            f"• 5 ملفات للاتنين مع بعض"
        )
        return

    # ── TEXT ──
    if msg.text:
        text = msg.text.strip()

        if cid_str in pending_answers and "/" in text:
            await msg.reply_text("⏳ جاري استكمال المؤشرات...")
            try:
                data  = pending_answers.pop(cid_str)
                month = data["month"]
                kpi_complete = await complete_kpi(data["kpi_data"], text, month)
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=io.BytesIO(kpi_complete),
                    filename=f"مؤشرات_{month}.xlsx",
                    caption=f"✅ شيت مؤشرات {month} مكتمل! جاهز للرفع 🎉"
                )
            except Exception as e:
                logger.error(f"KPI error: {e}")
                await msg.reply_text(f"❌ خطأ: {str(e)}")
            return

        if text == "/start":
            clients = load_clients()
            if cid_str not in clients and chat_id != ADMIN_CHAT_ID:
                clients[cid_str] = {"name": f"مستخدم_{cid_str}", "active": False, "joined": str(datetime.now().date())}
                save_clients(clients)
                await context.bot.send_message(ADMIN_CHAT_ID,
                    f"🆕 مستخدم جديد!\nID: {chat_id}\n\nللتفعيل: اضف عميل [الاسم] {chat_id}")
            await msg.reply_text("أهلاً! 👋\nطلبك اتبعت للإدارة.\nهيتواصلوا معاك قريباً.")
            return

        await msg.reply_text(
            "📎 ابعتلي الملفات:\n\n"
            "للجرد بس (3 ملفات):\n"
            "1️⃣ شيت الجرد السابق (Excel)\n"
            "2️⃣ PDF الوارد\n"
            "3️⃣ PDF المنصرف\n\n"
            "للمؤشرات بس (2 ملفات):\n"
            "1️⃣ شيت بيانات المرضى (Excel)\n"
            "2️⃣ شيت المؤشرات السنوي (Excel)\n\n"
            "للاتنين مع بعض (5 ملفات):\n"
            "ابعت الـ 5 مع بعض"
        )

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    logger.info("✅ البوت شغّال!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
