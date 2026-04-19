"""
parser.py
---------
Extracts structured inventory data from a Stock Card Report PDF.
"""
import io
import re
import pdfplumber

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FORMS = {
    "TABLET", "TABLETS", "TABS", "TAB",
    "CAPSULE", "CAPSULES", "CAPS", "CAP",
    "SYRUP", "SUSPENSION", "DROPS", "SOLUTION",
    "AMPOULE", "AMPOULES", "AMP",
    "VIAL", "VIALS",
    "CREAM", "OINTMENT", "GEL", "LOTION",
    "INHALER", "SPRAY", "SACHET",
    "INJECTION", "INFUSION",
    "BOTTLE", "CARTRIDGE", "FLEXPEN", "PEN",
    "TUBE", "BOX",
}

ITEM_LINE_RE = re.compile(
    r"^\d+\s+(\d{3}-\d{5})-(.+?)\s+UOM:\s*(.+)$"
)

TOTAL4_RE = re.compile(
    r"^Total\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)"
    r"\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)$"
)

TOTAL3_RE = re.compile(
    r"^Total\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)$"
)

STRENGTH_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)?)\s*"
    r"(MG|MCG|ML|IU|I\.U\.|G(?!\w)|GM|%|UNIT)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------
def parse_item_name(full_name):
    upper = full_name.upper().strip()
    words = upper.split()

    # --- name: words before first digit or form keyword ---
    name_words = []
    for word in words:                          # ✅ الـ for عندها body دلوقتي
        alpha = re.sub(r"[^A-Z]", "", word)
        if re.match(r"^\d", word):
            break
        if alpha in FORMS:
            break
        name_words.append(word)

    name = re.sub(r"[\s\-]+$", "", " ".join(name_words)) or upper

    # --- strength ---
    sm = STRENGTH_RE.search(upper)
    strength = None
    if sm:
        val = sm.group(1).replace(",", ".")
        unit = sm.group(2).upper().rstrip(".")
        strength = f"{val}{unit}"

    # --- form ---
    form = None
    for word in words:                          # ✅ نفس المشكلة هنا كمان متصلحة
        alpha = re.sub(r"[^A-Z]", "", word.upper())
        if alpha in FORMS:
            form = alpha
            break

    return name, strength, form

# ---------------------------------------------------------------------------
# Number helper
# ---------------------------------------------------------------------------
def _to_num(s):
    return float(s.replace(",", ""))

# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------
def extract_items_from_pdf(pdf_bytes):
    items = []
    current = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:                  # ✅ indentation صح
            text = page.extract_text()
            if not text:
                continue

            for raw_line in text.split("\n"):   # ✅ indentation صح
                line = raw_line.strip()
                if not line:
                    continue

                # --- Step 1: item header ---
                m = ITEM_LINE_RE.match(line)
                if m:
                    code = m.group(1)
                    full_name = m.group(2).strip()
                    name, strength, form = parse_item_name(full_name)
                    current = {
                        "code": code,
                        "name": name,
                        "strength": strength,
                        "form": form,
                    }
                    continue

                # --- Step 2: Total with 4 numbers ---
                m4 = TOTAL4_RE.match(line)
                if m4 and current:
                    current["bfw"]      = _to_num(m4.group(1))
                    current["received"] = _to_num(m4.group(2))
                    current["issued"]   = _to_num(m4.group(3))
                    current["balance"]  = _to_num(m4.group(4))
                    items.append(current)
                    current = None
                    continue

                # --- Step 3: Total with 3 numbers ---
                m3 = TOTAL3_RE.match(line)
                if m3 and current:
                    current["bfw"]      = 0.0
                    current["received"] = _to_num(m3.group(1))
                    current["issued"]   = _to_num(m3.group(2))
                    current["balance"]  = _to_num(m3.group(3))
                    items.append(current)
                    current = None

    return items
