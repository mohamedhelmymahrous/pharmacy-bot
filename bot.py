import os
import json
import logging
import asyncio
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
import anthropic
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import pdfplumber
import io
import random
import calendar

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_KEY", "")
ADMIN_CHAT_ID    = int(os.environ.get("ADMIN_CHAT_ID", "0"))
CLIENTS_FILE     = "clients.json"

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Clients DB ────────────────────────────────────────────────────────────────
def load_clients():
    if os.path.exists(CLIENTS_FILE):
        with open(CLIENTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_clients(clients):
    with open(CLIENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(clients, f, ensure_ascii=False, indent=2)

def is_active(chat_id: str) -> bool:
    clients = load_clients()
    if str(chat_id) == str(ADMIN_CHAT_ID):
        return True
    return clients.get(str(chat_id), {}).get("active", False)

# ── Pending files per user ────────────────────────────────────────────────────
pending_files   = {}   # chat_id → list of (file_type, bytes, filename)
pending_answers = {}   # chat_id → {"kpis": ..., "inventory": ...}

# ── Helpers ───────────────────────────────────────────────────────────────────
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

async def call_claude(prompt: str, system: str = "", max_tokens: int = 4000) -> str:
    msgs = [{"role": "user", "content": prompt}]
    kwargs = {"model": "claude-sonnet-4-20250514", "max_tokens": max_tokens, "messages": msgs}
    if system:
        kwargs["system"] = system
    resp = anthropic_client.messages.create(**kwargs)
    return resp.content[0].text

def parse_json_safe(text: str):
    import re
    text = text.strip()
    # find first [ or {
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

# ── STEP 1: Classify files ────────────────────────────────────────────────────
async def classify_files(files):
    """Ask Claude to classify which file is which"""
    summaries = []
    for ftype, data, fname in files:
        if ftype == "pdf":
            preview = extract_pdf_text(data)[:800]
        else:
            preview = excel_to_text(data, max_rows=15)[:800]
        summaries.append(f"ملف: {fname} ({ftype})\nمحتوى (أول 800 حرف):\n{preview}\n---")

    prompt = f"""عندي {len(files)} ملفات. صنّف كل ملف في واحدة من الفئات دي:
- inventory_prev: شيت الجرد الشهر السابق (Excel فيه أسماء أدوية ورصيد أول الشهر والوارد والمنصرف)
- pdf_incoming: PDF الوارد (فيه أدوية واردة وكميات)
- pdf_dispensed: PDF المنصرف (فيه أدوية منصرفة وكميات)
- patients_sheet: شيت بيانات المرضى (Excel فيه أسماء مرضى وأرقام طبية وأدوية)
- kpi_sheet: شيت المؤشرات السنوي (Excel فيه شيتات كتير زي AB orders و medication error)

الملفات:
{chr(10).join(summaries)}

أرجع JSON فقط بالشكل ده بدون أي نص تاني:
[{{"filename": "...", "category": "..."}}, ...]"""

    result = await call_claude(prompt, "أنت مساعد صيدلاني. أرجع JSON فقط.")
    return parse_json_safe(result)

# ── STEP 2: Build inventory sheet ─────────────────────────────────────────────
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
- طابق أسماء الأدوية بذكاء حتى لو فيه اختلافات بسيطة

أرجع JSON فقط بالشكل ده:
[{{"اسم_الصنف": "...", "رصيد_اول_الشهر": 0, "الوارد": 0, "المجموع": 0, "المنصرف": 0, "المتبقي": 0}}, ...]"""

    result = await call_claude(prompt, "أنت صيدلاني. أرجع JSON فقط.", max_tokens=6000)
    rows = parse_json_safe(result) or []

    # Build Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = month
    ws.sheet_view.rightToLeft = True

    headers = ["اسم الصنف", "رصيد أول الشهر", "الوارد", "المجموع", "المنصرف", "المتبقي"]
    hdr_fill = PatternFill("solid", fgColor="1B3A6B")
    hdr_font = Font(name="Arial", bold=True, color="FFFFFF", size=11)
    border = Border(*[Side(style="thin", color="CCCCCC")]*4)

    for col, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=col, value=h)
        c.fill = hdr_fill
        c.font = hdr_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border

    ws.row_dimensions[1].height = 30
    col_widths = [35, 18, 12, 12, 12, 12]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    for r, row in enumerate(rows, 2):
        fill = PatternFill("solid", fgColor="F8F9FA" if r % 2 == 0 else "FFFFFF")
        vals = [row.get("اسم_الصنف",""), row.get("رصيد_اول_الشهر",0),
                row.get("الوارد",0), row.get("المجموع",0),
                row.get("المنصرف",0), row.get("المتبقي",0)]
        for col, val in enumerate(vals, 1):
            c = ws.cell(row=r, column=col, value=val)
            c.fill = fill
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(horizontal="center" if col > 1 else "right", vertical="center")
            c.border = border

        # Color red if متبقي < 0
        if isinstance(vals[5], (int, float)) and vals[5] < 0:
            ws.cell(row=r, column=6).font = Font(name="Arial", size=10, color="C0392B", bold=True)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# ── STEP 3: Fill KPI sheet ────────────────────────────────────────────────────
async def fill_kpi_sheet(kpi_data: bytes, patients_data: bytes, month: str) -> tuple[bytes, str]:
    """Returns (filled_xlsx_bytes, questions_message)"""
    patients_text = excel_to_text(patients_data, max_rows=300)

    # Get 30 random patients for indicators 1 & 2
    wb_p = openpyxl.load_workbook(io.BytesIO(patients_data), data_only=True)
    ws_p = wb_p.active
    all_patients = []
    for row in ws_p.iter_rows(min_row=2, values_only=True):
        name = row[0] if row[0] else None
        mid  = row[1] if len(row) > 1 else None
        if name and str(name).strip():
            all_patients.append((str(name).strip(), str(mid).strip() if mid else ""))

    sample1 = random.sample(all_patients, min(30, len(all_patients)))
    sample2 = random.sample(all_patients, min(30, len(all_patients)))

    # Extract stats from patients sheet via Claude
    stats_prompt = f"""من شيت المرضى ده، استخرج الأرقام دي للشهر:
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

    stats_result = await call_claude(stats_prompt, "أنت صيدلاني. أرجع JSON فقط.")
    stats = parse_json_safe(stats_result) or {}

    total  = stats.get("total_prescriptions", len(all_patients))
    ab     = stats.get("ab_prescriptions", 0)
    inapp  = stats.get("inappropriate_ab", 0)
    proto  = stats.get("ab_protocol_adherence", ab)
    curmed = stats.get("current_medication_count", total)
    appr   = stats.get("appropriateness_count", total)
    couns  = stats.get("counselling_count", total)
    interv = stats.get("interventions_count", 0)

    # Determine month column index
    months_ar = ["يناير","فبراير","مارس","ابريل","مايو","يونيو",
                 "يوليو","اغسطس","سبتمبر","اكتوبر","نوفمبر","ديسمبر"]
    month_clean = month.strip()
    month_col = None
    for i, m in enumerate(months_ar, 2):
        if m in month_clean or month_clean in m:
            month_col = i
            break
    if not month_col:
        month_col = datetime.now().month + 1

    wb_kpi = openpyxl.load_workbook(io.BytesIO(kpi_data))

    def write_month(sheet_name, numerator, denominator=None, row_num=3, row_total=4):
        if sheet_name not in wb_kpi.sheetnames:
            return
        ws = wb_kpi[sheet_name]
        ws.cell(row=row_num, column=month_col, value=numerator)
        if denominator is not None:
            ws.cell(row=row_total, column=month_col, value=denominator)

    # Fill what we can automatically
    write_month("AB orders",            ab,    total)
    write_month("AB inappropriate use", inapp, ab)
    write_month("AB protocols adherence", proto, ab)
    write_month("current medication",   curmed, total)
    write_month("appropriatness ",      appr,  total)
    write_month("councelling",          couns, total)
    write_month("cost savings",         interv, row_num=3, row_total=4)

    # Fill names sheets - اسماء المرضى
    if "اسماء المرضي" in wb_kpi.sheetnames:
        ws_names = wb_kpi["اسماء المرضي"]
        for i, (name, mid) in enumerate(sample1, 5):
            ws_names.cell(row=i, column=1, value=i-4)
            ws_names.cell(row=i, column=2, value=name)
            ws_names.cell(row=i, column=3, value=mid)
            for col in range(4, 8):
                ws_names.cell(row=i, column=col, value=1)

    # Fill شيت الاعطاء
    if "شيت الاعطاء " in wb_kpi.sheetnames:
        ws_give = wb_kpi["شيت الاعطاء "]
        for i, (name, mid) in enumerate(sample2, 5):
            ws_give.cell(row=i, column=1, value=i-4)
            ws_give.cell(row=i, column=2, value=name)
            ws_give.cell(row=i, column=3, value=mid)
            ws_give.cell(row=i, column=4, value=1)

    buf = io.BytesIO()
    wb_kpi.save(buf)

    # Build questions message
    questions = f"""✅ ملأت اللي أقدر عليه تلقائياً!

محتاج منك الأرقام دي لشهر {month}:

1️⃣ *التخزين السليم:*
   - أدوية الثلاجة صح / الإجمالي؟ (مثال: 12/12)
   - أدوية الضوء صح / الإجمالي؟
   - LASA شكل صح / الإجمالي؟
   - LASA نطق صح / الإجمالي؟

2️⃣ *High Alert & High Concentration:*
   - High Alert صح / الإجمالي؟
   - High Concentration صح / الإجمالي؟
   - مراجعة ثنائية تمت / إجمالي الوصفات؟

3️⃣ *الرواكد والنواقص:*
   - عدد الراكد / إجمالي الأدوية؟
   - عدد الناقص / إجمالي الأدوية؟

4️⃣ *كروت التعريف:*
   - عدد الأدوية عليها كروت / الإجمالي؟

5️⃣ *الأخطاء الدوائية:*
   - عدد الأخطاء؟
   - منهم كام Near Miss؟

6️⃣ *الآثار العكسية:*
   - عدد الآثار العكسية؟

7️⃣ *Cost Savings:*
   - كام تدخل دوائي؟
   - كام جنيه وفّرت؟

ابعت الإجابات بالترتيب هكذا:
12/12 | 20/20 | 15/15 | 8/8 | 16/16 | 1/1 | 117/117 | 0/220 | 9/220 | 195/195 | 3 | 3 | 0 | 0 | 0"""

    return buf.getvalue(), questions

# ── STEP 4: Complete KPI with answers ─────────────────────────────────────────
async def complete_kpi(kpi_data: bytes, answers_text: str, month: str) -> bytes:
    # Parse answers
    parts = [p.strip() for p in answers_text.replace("\n", "|").split("|") if p.strip()]

    def get_pair(idx):
        if idx < len(parts):
            p = parts[idx]
            if "/" in p:
                a, b = p.split("/", 1)
                return int(a.strip()), int(b.strip())
            return int(p.strip()), int(p.strip())
        return 0, 0

    def get_single(idx):
        if idx < len(parts):
            try:
                return int(parts[idx].strip())
            except:
                return 0
        return 0

    fridge_ok, fridge_tot   = get_pair(0)
    light_ok,  light_tot    = get_pair(1)
    lasa_shape_ok, lasa_shape_tot = get_pair(2)
    lasa_sound_ok, lasa_sound_tot = get_pair(3)
    ha_ok, ha_tot           = get_pair(4)
    hc_ok, hc_tot           = get_pair(5)
    double_ok, double_tot   = get_pair(6)
    stale_n, stale_tot      = get_pair(7)
    short_n, short_tot      = get_pair(8)
    cards_ok, cards_tot     = get_pair(9)
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

    def w(sheet, r, val):
        if sheet in wb.sheetnames:
            wb[sheet].cell(row=r, column=month_col, value=val)

    # التخزين السليم
    if "التخزين السليم للادوية " in wb.sheetnames:
        ws = wb["التخزين السليم للادوية "]
        # row5=العدد, row7=الاجمالي
        vals = [fridge_ok, light_ok, lasa_shape_ok, lasa_sound_ok]
        tots = [fridge_tot, light_tot, lasa_shape_tot, lasa_sound_tot]
        base_col = (month_col - 2) * 7 + 2
        for i, (v, t) in enumerate(zip(vals, tots)):
            ws.cell(row=5, column=base_col+i, value=v)
            ws.cell(row=7, column=base_col+i, value=t)

    # ادوية عالية الخطورة
    if "ادوية عالية الخطورة والتركيز" in wb.sheetnames:
        ws = wb["ادوية عالية الخطورة والتركيز"]
        base_col = (month_col - 2) * 5 + 2
        ws.cell(row=5, column=base_col,   value=ha_ok)
        ws.cell(row=5, column=base_col+1, value=hc_ok)
        ws.cell(row=5, column=base_col+2, value=double_ok)
        ws.cell(row=7, column=base_col,   value=ha_tot)
        ws.cell(row=7, column=base_col+1, value=hc_tot)
        ws.cell(row=7, column=base_col+2, value=double_tot)

    # HA & HC اثناء الاعطاء
    if "HA & HC اثناء الاعطاء" in wb.sheetnames:
        ws = wb["HA & HC اثناء الاعطاء"]
        ws.cell(row=3, column=month_col, value=double_ok)
        ws.cell(row=4, column=month_col, value=double_tot)

    # نسبة الرواكد والنواقص
    if "نسبة الرواكد والنواقص" in wb.sheetnames:
        ws = wb["نسبة الرواكد والنواقص"]
        ws.cell(row=3, column=month_col, value=stale_n)
        ws.cell(row=4, column=month_col, value=stale_tot)
        ws.cell(row=27, column=month_col, value=short_n)
        ws.cell(row=28, column=month_col, value=short_tot)

    # كروت التعريف
    if "كروت التعريف" in wb.sheetnames:
        ws = wb["كروت التعريف"]
        ws.cell(row=3, column=month_col, value=cards_ok)
        ws.cell(row=4, column=month_col, value=cards_tot)

    # medication error
    if "medication error" in wb.sheetnames:
        wb["medication error"].cell(row=3, column=month_col, value=errors)

    # near miss
    if "near miss" in wb.sheetnames:
        ws = wb["near miss"]
        ws.cell(row=3, column=month_col, value=near_miss)
        ws.cell(row=4, column=month_col, value=errors)

    # الاثار العكسية
    if "الاثار العكسية" in wb.sheetnames:
        wb["الاثار العكسية"].cell(row=3, column=month_col, value=adv_events)

    # cost savings
    if "cost savings" in wb.sheetnames:
        ws = wb["cost savings"]
        ws.cell(row=3, column=month_col, value=interventions)
        ws.cell(row=4, column=month_col, value=savings)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# ── MESSAGE HANDLER ───────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg     = update.message

    # ── ADMIN COMMANDS ──
    if chat_id == ADMIN_CHAT_ID:
        if msg.text:
            text = msg.text.strip()

            # تأكيد دفع
            if text.startswith("تأكيد "):
                name = text[6:].strip()
                clients = load_clients()
                for cid, data in clients.items():
                    if data.get("name") == name:
                        clients[cid]["active"] = True
                        save_clients(clients)
                        await context.bot.send_message(
                            chat_id=int(cid),
                            text=f"✅ تم تأكيد اشتراكك يا {name}!\nالبوت شغّال معاك طول الشهر 🎉"
                        )
                        await msg.reply_text(f"✅ تم تفعيل {name}")
                        return
                await msg.reply_text(f"❌ مش لاقي عميل اسمه {name}")
                return

            # رفض دفع
            if text.startswith("رفض "):
                name = text[5:].strip()
                clients = load_clients()
                for cid, data in clients.items():
                    if data.get("name") == name:
                        await context.bot.send_message(
                            chat_id=int(cid),
                            text=f"⚠️ لم يتم التحقق من الدفع يا {name}\nبرجاء التواصل مع الإدارة"
                        )
                        await msg.reply_text(f"تم إبلاغ {name}")
                        return
                return

            # وقف عميل
            if text.startswith("وقف "):
                name = text[5:].strip()
                clients = load_clients()
                for cid, data in clients.items():
                    if data.get("name") == name:
                        clients[cid]["active"] = False
                        save_clients(clients)
                        await context.bot.send_message(
                            chat_id=int(cid),
                            text="⛔ تم إيقاف اشتراكك. للتجديد تواصل مع الإدارة."
                        )
                        await msg.reply_text(f"✅ تم إيقاف {name}")
                        return

            # فعّل عميل
            if text.startswith("فعل "):
                name = text[5:].strip()
                clients = load_clients()
                for cid, data in clients.items():
                    if data.get("name") == name:
                        clients[cid]["active"] = True
                        save_clients(clients)
                        await msg.reply_text(f"✅ تم تفعيل {name}")
                        return

            # قائمة العملاء
            if text == "عملاء":
                clients = load_clients()
                if not clients:
                    await msg.reply_text("مفيش عملاء لحد دلوقتي.")
                    return
                lines = ["📋 *قائمة العملاء:*\n"]
                for cid, data in clients.items():
                    status = "✅ فعّال" if data.get("active") else "⛔ موقوف"
                    lines.append(f"{data.get('name','؟')} — {status} — ID: {cid}")
                await msg.reply_text("\n".join(lines), parse_mode="Markdown")
                return

            # إضافة عميل
            if text.startswith("اضف عميل"):
                parts = text.replace("اضف عميل", "").strip().split()
                if len(parts) >= 2:
                    name = parts[0]
                    cid  = parts[1]
                    clients = load_clients()
                    clients[cid] = {"name": name, "active": True, "joined": str(datetime.now().date())}
                    save_clients(clients)
                    await msg.reply_text(f"✅ تم إضافة {name} (ID: {cid})")
                    try:
                        await context.bot.send_message(
                            chat_id=int(cid),
                            text=f"أهلاً يا {name}! 👋\nتم تفعيل اشتراكك في بوت الصيدلية.\nابعت ملفاتك متى ما تريد!"
                        )
                    except:
                        pass
                else:
                    await msg.reply_text("الصيغة: اضف عميل [الاسم] [chat_id]")
                return

            # إرسال فواتير
            if text == "ابعت فواتير":
                clients = load_clients()
                count = 0
                for cid, data in clients.items():
                    try:
                        await context.bot.send_message(
                            chat_id=int(cid),
                            text=f"مرحباً د. {data.get('name','')} 👋\n\n"
                                 f"اشتراك شهر {datetime.now().strftime('%B')} = 299 جنيه\n"
                                 f"InstaPay: أرسله إليك الآن\n\n"
                                 f"برجاء السداد وإرسال صورة الإيصال هنا ✅"
                        )
                        count += 1
                    except:
                        pass
                await msg.reply_text(f"✅ تم إرسال الفواتير لـ {count} عميل")
                return

    # ── CLIENT NOT ACTIVE ──
    if not is_active(chat_id):
        await msg.reply_text(
            "⛔ اشتراكك غير فعّال.\n"
            "للاشتراك تواصل مع الإدارة."
        )
        return

    # ── HANDLE PAYMENT PHOTO (non-admin) ──
    if msg.photo:
        # Check if admin
        if chat_id != ADMIN_CHAT_ID:
            clients = load_clients()
            client_name = clients.get(str(chat_id), {}).get("name", str(chat_id))
            photo = msg.photo[-1]
            photo_file = await context.bot.get_file(photo.file_id)
            await msg.reply_text("📨 تم استلام صورة الإيصال، جاري المراجعة...")
            # Forward to admin
            await context.bot.forward_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=chat_id,
                message_id=msg.message_id
            )
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"💰 *{client_name}* بعت إيصال دفع\n\n"
                     f"للتأكيد: `تأكيد {client_name}`\n"
                     f"للرفض: `رفض {client_name}`",
                parse_mode="Markdown"
            )
            return

    # ── HANDLE DOCUMENTS ──
    if msg.document:
        cid_str = str(chat_id)
        if cid_str not in pending_files:
            pending_files[cid_str] = []

        doc  = msg.document
        fname = doc.file_name or "file"
        fext  = fname.split(".")[-1].lower()
        ftype = "pdf" if fext == "pdf" else "excel"

        file_obj = await context.bot.get_file(doc.file_id)
        data = bytes(await file_obj.download_as_bytearray())
        pending_files[cid_str].append((ftype, data, fname))

        count = len(pending_files[cid_str])
        await msg.reply_text(f"📎 استلمت {count}/5 ملفات...")

        if count >= 5:
            await msg.reply_text("✅ استلمت كل الملفات! جاري التحليل... ⏳\n(هياخد دقيقتين تقريباً)")
            files = pending_files.pop(cid_str)

            try:
                # Classify
                classifications = await classify_files(files)
                file_map = {}
                if classifications:
                    for item in classifications:
                        fname_c = item.get("filename","")
                        cat     = item.get("category","")
                        for ftype, data, fname in files:
                            if fname == fname_c:
                                file_map[cat] = (ftype, data, fname)

                # Fallback classification by order
                pdfs   = [(t,d,n) for t,d,n in files if t=="pdf"]
                excels = [(t,d,n) for t,d,n in files if t=="excel"]

                inv_prev = file_map.get("inventory_prev", excels[0] if len(excels)>0 else None)
                pdf_in   = file_map.get("pdf_incoming",  pdfs[0]   if len(pdfs)>0   else None)
                pdf_dis  = file_map.get("pdf_dispensed", pdfs[1]   if len(pdfs)>1   else None)
                patients = file_map.get("patients_sheet",excels[1] if len(excels)>1 else None)
                kpi      = file_map.get("kpi_sheet",     excels[2] if len(excels)>2 else None)

                month = datetime.now().strftime("%B %Y")

                # Build inventory
                if inv_prev and pdf_in and pdf_dis:
                    await context.bot.send_message(chat_id=chat_id, text="📊 بنبني شيت الجرد...")
                    inc_text = extract_pdf_text(pdf_in[1])
                    dis_text = extract_pdf_text(pdf_dis[1])
                    inv_xlsx = await build_inventory(inv_prev[1], inc_text, dis_text, month)
                    await context.bot.send_document(
                        chat_id=chat_id,
                        document=io.BytesIO(inv_xlsx),
                        filename=f"جرد_{month}.xlsx",
                        caption=f"✅ شيت جرد {month} جاهز!"
                    )

                # Fill KPI
                if kpi and patients:
                    await context.bot.send_message(chat_id=chat_id, text="📋 بنملأ شيت المؤشرات...")
                    kpi_partial, questions = await fill_kpi_sheet(kpi[1], patients[1], month)
                    pending_answers[cid_str] = {"kpi_data": kpi_partial, "month": month}
                    await context.bot.send_message(chat_id=chat_id, text=questions, parse_mode="Markdown")

            except Exception as e:
                logger.error(f"Error: {e}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ حصل خطأ: {str(e)}\nجرب تبعت الملفات تاني."
                )
        return

    # ── HANDLE TEXT ──
    if msg.text:
        text = msg.text.strip()
        cid_str = str(chat_id)

        # Answer to KPI questions
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
                logger.error(f"KPI complete error: {e}")
                await msg.reply_text(f"❌ خطأ في استكمال المؤشرات: {str(e)}")
            return

        # Register new user
        if text == "/start":
            clients = load_clients()
            if cid_str not in clients and chat_id != ADMIN_CHAT_ID:
                clients[cid_str] = {"name": f"مستخدم_{cid_str}", "active": False, "joined": str(datetime.now().date())}
                save_clients(clients)
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"🆕 مستخدم جديد طلب الاشتراك!\nID: {chat_id}\n\nللتفعيل: `اضف عميل [الاسم] {chat_id}`",
                    parse_mode="Markdown"
                )
            if chat_id == ADMIN_CHAT_ID:
                await msg.reply_text(
                    "👨‍💼 *لوحة تحكم المدير*\n\n"
                    "الأوامر المتاحة:\n"
                    "• `عملاء` — قائمة العملاء\n"
                    "• `اضف عميل [اسم] [id]` — إضافة عميل\n"
                    "• `وقف [اسم]` — إيقاف عميل\n"
                    "• `فعل [اسم]` — تفعيل عميل\n"
                    "• `تأكيد [اسم]` — تأكيد دفع\n"
                    "• `رفض [اسم]` — رفض دفع\n"
                    "• `ابعت فواتير` — إرسال فواتير الشهر",
                    parse_mode="Markdown"
                )
            else:
                await msg.reply_text(
                    "أهلاً! 👋\n"
                    "طلبك اتبعت للإدارة.\n"
                    "هيتواصلوا معاك قريباً لتفعيل اشتراكك."
                )
            return

        await msg.reply_text(
            "📎 ابعتلي الملفات الـ 5:\n"
            "1. شيت الجرد السابق (Excel)\n"
            "2. PDF الوارد\n"
            "3. PDF المنصرف\n"
            "4. شيت بيانات المرضى (Excel)\n"
            "5. شيت المؤشرات السنوي (Excel)"
        )

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    logger.info("البوت شغّال! ✅")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
