from matching import PharmaMatcher

# --- Database (الأصناف الموجودة) ---
database = [
    {
        "id": "DB-001",
        "code": "270-00539",
        "name": "ADWIFLAM",
        "strength": "75MG",
        "form": "AMPOULE",
        "company": "ADWIA",
    },
    {
        "id": "DB-002",
        "name": "AMLODIPINE",
        "strength": "5MG",
        "form": "TABLET",
    },
    {
        "id": "DB-003",
        "name": "AMOXICILLIN",
        "strength": "500MG",
        "form": "CAPSULE",
    },
]

matcher = PharmaMatcher(database)

# --- Test 1: Exact match (اسم مختلف الكتابة بس نفس الدواء) ---
r1 = matcher.match({
    "name": "ADWI FLAM",
    "strength": "75 MG",
    "form": "AMP",
    "company": "ADWIA"
})
print(r1.to_dict())
# match_type: "exact", confidence: ~0.96

# --- Test 2: Fuzzy match (قوة مختلفة شوية) ---
r2 = matcher.match({
    "name": "AMLODIPINE",
    "strength": "10MG",
    "form": "TABS",
})
print(r2.to_dict())
# match_type: "fuzzy", confidence: ~0.75

# --- Test 3: New item (مش موجود) ---
r3 = matcher.match({
    "name": "PARACETAMOL",
    "strength": "500MG",
    "form": "TABLET",
})
print(r3.to_dict())
# match_type: "new", confidence: ~0.15
