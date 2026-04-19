"""
parser.py
---------
Extracts structured inventory data from a Stock Card Report PDF.
Main function:
 extract_items_from_pdf(pdf_bytes) -> list of dicts
Each dict contains:
 code - item code e.g. "270-00539"
 name - base drug name e.g. "ADWIFLAM"
 strength - dosage e.g. "75MG", "1.5MG", "500MG"
 form - dosage form e.g. "TABLET", "SYRUP", "AMPOULE"
 bfw - balance forward (opening stock)
 received - total received this month
 issued - total issued this month
 balance - closing balance
"""
import io
import re
import pdfplumber
# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Known dosage form keywords found in drug names
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
# Matches item header lines:
# e.g. "1 270-00539-ADWIFLAM 75mg Ampoule 3 ml UOM: AMPOULE 3 ML"
# Groups: (1) code XXX-XXXXX (2) full name (3) UOM text
ITEM_LINE_RE = re.compile(
 r"^\d+\s+(\d{3}-\d{5})-(.+?)\s+UOM:\s*(.+)$"
)
# Total line with 4 numbers: BFW Received Issued Balance
TOTAL4_RE = re.compile(
 r"^Total\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)"
 r"\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)$"
)
# Total line with 3 numbers: Received Issued Balance (BFW = 0)
TOTAL3_RE = re.compile(
 r"^Total\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)\s+([\d,]+(?:\.\d+)?)$"
)
# Matches dosage strength: e.g. 75MG, 1.5MG/5ML, 0.5MCG, 70/30 100IU
STRENGTH_RE = re.compile(
 r"(\d+(?:[.,]\d+)?(?:/\d+(?:[.,]\d+)?)?)\s*"
 r"(MG|MCG|ML|IU|I\.U\.|G(?!\w)|GM|%|UNIT)",
 re.IGNORECASE,
)
# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------
def parse_item_name(full_name):
 """
 Splits the full drug description into (name, strength, form).
 Strategy:
 - name: alphabetic words before the first digit or form keyword
 - strength: first dosage number+unit found anywhere in the name
 - form: first FORMS keyword found in the name
 Examples:
 "ADWIFLAM 75mg Ampoule 3 ml" -> ("ADWIFLAM", "75MG", "AMPOULE")
 "ALDOMET 250MG FILM COATED" -> ("ALDOMET", "250MG", None)
 "AMLODIPINE 5MG TABLET" -> ("AMLODIPINE", "5MG", "TABLET")
 "ALKAPRESS TRIO (5/12.5/160)" -> ("ALKAPRESS TRIO (5/12.5/160)", None, None)
 """
 upper = full_name.upper().strip()
 words = upper.split()
 # --- name: words before first digit or form keyword ---
 name_words = []
 for word in words:
 alpha = re.sub(r"[^A-Z]", "", word)
 if re.match(r"^\d", word):
 break
 if alpha in FORMS:
 break
 name_words.append(word)
 name = re.sub(r"[\s\-]+$", "", " ".join(name_words)) or upper
 # --- strength: first number+unit in full name ---
 sm = STRENGTH_RE.search(upper)
 strength = None
 if sm:
 val = sm.group(1).replace(",", ".")
 unit = sm.group(2).upper().rstrip(".")
 strength = f"{val}{unit}"
 # --- form: first FORMS keyword in full name ---
 form = None
 for word in words:
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
 """
 Reads a Stock Card Report PDF and returns a list of items.
 Each item is a dict:
 {
 "code": "270-00539",
 "name": "ADWIFLAM",
 "strength": "75MG",
 "form": "AMPOULE",
 "bfw": 15.0,
 "received": 90.0,
 "issued": 17.0,
 "balance": 88.0,
 }
 How it works (line by line):
 1. If line matches item header -> start new current item
 2. If line matches Total (4 nums) -> save item with BFW+Received+Issued+Balance
 3. If line matches Total (3 nums) -> save item with BFW=0, Received+Issued+Balance
 4. Everything else -> skip
 """
 items = []
 current = None
 with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
 for page in pdf.pages:
 text = page.extract_text()
 if not text:
 continue
 for raw_line in text.split("\n"):
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
 current["bfw"] = _to_num(m4.group(1))
 current["received"] = _to_num(m4.group(2))
 current["issued"] = _to_num(m4.group(3))
 current["balance"] = _to_num(m4.group(4))
 items.append(current)
 current = None
 continue
 # --- Step 3: Total with 3 numbers (BFW was zero) ---
 m3 = TOTAL3_RE.match(line)
 if m3 and current:
 current["bfw"] = 0.0
 current["received"] = _to_num(m3.group(1))
 current["issued"] = _to_num(m3.group(2))
 current["balance"] = _to_num(m3.group(3))
 items.append(current)
 current = None
 return items
