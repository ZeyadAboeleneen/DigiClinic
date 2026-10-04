# ruff: noqa: E501  -- rule table: one rule per line
"""Map the Egyptian drug database's `drug_class` / `route` (e.g. "PENICILLINS.PENICILLIN WITH B-LACTAMASE INHIBITOR",
"ORAL.LIQUID") onto the clinic's category names (apps/prescriptions/specialties.py), so the category buttons fill up
automatically. A drug can get several categories (Augmentin → Antibiotics, Penicillins, Oral Antibiotics...).

Rules are keyword-based on the upper-cased class text; extend RULES when the doctors notice a drug missing from a
button. Cosmetics / personal-care classes get no category (still searchable from "الكل").
"""

import re

from .specialties import SPECIALTIES

# (keywords that must ALL appear in the class — or must NOT, when prefixed with "!", [categories])
RULES: list[tuple[tuple[str, ...], list[str]]] = [
    # --- anti-infectives ---------------------------------------------------------------------
    (("ANTIBIOTIC",), ["Antibiotics", "Systemic Antibiotics"]),
    (("PENICILLIN",), ["Antibiotics", "Penicillins", "Systemic Antibiotics"]),
    (("CEPHALOSPORIN",), ["Antibiotics", "Cephalosporins", "Systemic Antibiotics"]),
    (("MACROLIDE",), ["Antibiotics", "Macrolides", "Systemic Antibiotics"]),
    (("QUINOLONE",), ["Antibiotics", "Fluoroquinolones", "Systemic Antibiotics"]),
    (("TETRACYCLINE",), ["Antibiotics", "Tetracyclines"]),
    (("AMINOGLYCOSIDE",), ["Antibiotics", "Aminoglycosides"]),
    (("CARBAPENEM",), ["Antibiotics", "Carbapenems"]),
    (("GLYCOPEPTIDE",), ["Antibiotics", "Glycopeptides"]),
    (("SULFONAMIDE",), ["Antibiotics"]),
    (("URINARY ANTISEPTIC",), ["UTI medications", "Antibiotics"]),
    (("ANTIFUNGAL",), ["Antifungals"]),
    (("ANTI-FUNGAL",), ["Antifungals"]),
    (("ANTI-VIRAL",), ["Antivirals"]),
    (("ANTIVIRAL",), ["Antivirals"]),
    (("ANTI-RETROVIRAL",), ["Antiretrovirals", "Antivirals"]),
    (("HEPATITIS",), ["Hepatitis medications"]),
    (("ANTHELMINTIC",), ["Deworming medications", "Antiparasitics"]),
    (("ANTI-HELMINTIC",), ["Deworming medications", "Antiparasitics"]),
    (("ANTIPARASITIC",), ["Antiparasitics"]),
    (("AMOEBICIDE",), ["Antiprotozoals"]),
    (("ANTIPROTOZOAL",), ["Antiprotozoals"]),
    (("TUBERCUL",), ["Antituberculosis drugs"]),
    (("SCABICIDE",), ["Scabies medications"]),
    (("PEDICULICIDE",), ["Pediculosis medications"]),
    (("ANTISEPTIC",), ["Antiseptics"]),
    (("VACCINE",), ["Vaccines"]),
    (("IMMUNOGLOBULIN",), ["Immunoglobulins"]),
    # --- pain / inflammation -----------------------------------------------------------------
    (("NSAID",), ["NSAIDs", "Analgesics", "Anti-inflammatory medications", "Pain medications"]),
    (("ANALGESIC",), ["Analgesics", "Pain medications"]),
    (("ANTIPYRETIC",), ["Analgesics / Antipyretics", "Analgesics", "Paracetamol"]),
    (("OPIOID", "!NON OPIOID", "!NON-OPIOID", "!ANTAGONIST"), ["Opioids", "Opioid Analgesics"]),
    (("MUSCLE RELAXANT",), ["Muscle Relaxants"]),
    (("SKELETAL MUSCLE",), ["Muscle Relaxants"]),
    (("ANTI-GOUT",), ["Gout medications", "Urate-lowering drugs"]),
    (("GOUT",), ["Gout medications"]),
    (("OSTEOARTHRITIS",), ["Joint medications"]),
    (("OSTEOPOROSIS",), ["Osteoporosis medications", "Bone-support medications"]),
    (("BISPHOSPHONATE",), ["Bisphosphonates", "Osteoporosis medications"]),
    (("MIGRAINE",), ["Migraine medications"]),
    (("LOCAL ANESTHETIC",), ["Local Anesthetics"]),
    (("ANTI-RHEUMATIC", "!OSTEOARTHRITIS", "!PAIN", "!VASCULAR"), ["DMARDs"]),
    (("DMARD",), ["DMARDs"]),
    # --- cardiovascular ----------------------------------------------------------------------
    (("ANTI-HYPERTENSIVE",), ["Antihypertensives"]),
    (("ANTIHYPERTENSIVE",), ["Antihypertensives"]),
    (("BETA BLOCKER",), ["Beta Blockers", "Antihypertensives"]),
    (("ACE",), ["ACE Inhibitors"]),
    (("ANGIOTENSIN",), ["ARBs"]),
    (("CALCIUM CHANNEL",), ["Calcium Channel Blockers"]),
    (("DIURETIC",), ["Diuretics"]),
    (("ANTI-ISCHEMIC",), ["Antianginals"]),
    (("ANTIANGINAL",), ["Antianginals"]),
    (("NITRATE",), ["Nitrates", "Antianginals"]),
    (("ANTI-ARRHYTHMIC",), ["Antiarrhythmics"]),
    (("ANTIARRHYTHMIC",), ["Antiarrhythmics"]),
    (("HEART FAILURE",), ["Heart Failure medications"]),
    (("CARDIAC GLYCOSIDE",), ["Heart Failure medications"]),
    (("ANTICOAGULANT",), ["Anticoagulants"]),
    (("ANTI-COAGULANT",), ["Anticoagulants"]),
    (("ANTIPLATELET",), ["Antiplatelets"]),
    (("ANTI-PLATELET",), ["Antiplatelets"]),
    (("PLATELET AGGREGATION",), ["Antiplatelets"]),
    (("ANTIHYPERLIPIDEMIC", "!SOMATOSTATIN"), ["Lipid-lowering drugs", "Statins / Lipid-lowering"]),
    (("STATIN", "!SOMATOSTATIN", "!NYSTATIN"), ["Statins", "Statins / Lipid-lowering", "Lipid-lowering drugs"]),
    (("VASODILATOR", "!HAIR"), ["Vasodilators"]),
    (("VENOTONIC",), ["Venous Insufficiency medications"]),
    (("VARICOSE",), ["Venous Insufficiency medications"]),
    (("HEMORRHOID",), ["Venous Insufficiency medications"]),
    (("CEREBRAL CIRCULATORY",), ["Stroke-related medications"]),
    # --- diabetes / endocrine ----------------------------------------------------------------
    (("ANTI-DIABETIC",), ["Diabetes medications", "Antidiabetics"]),
    (("ANTIDIABETIC",), ["Diabetes medications", "Antidiabetics"]),
    (("SULFONYLUREA",), ["Sulfonylureas"]),
    (("DPP-4",), ["DPP-4 Inhibitors"]),
    (("SGLT2",), ["SGLT2 Inhibitors"]),
    (("BIGUANIDE",), ["Biguanides"]),
    (("COMBINED SECRETAGOGUES",), ["Combination Antidiabetics"]),
    (("GLP-1",), ["GLP-1 Receptor Agonists", "GLP-1 medications"]),
    (("INSULIN", "!SENSITIZER", "!SECRETAGOGUE"), ["Insulins", "Insulin", "Diabetes medications"]),
    (("THYROID", "!ANTITHYROID", "!ANTI-THYROID", "!PARATHYROID"), ["Thyroid Hormones", "Thyroid medications"]),
    (("ANTITHYROID",), ["Antithyroid Drugs", "Thyroid medications"]),
    (("ANTI-THYROID",), ["Antithyroid Drugs", "Thyroid medications"]),
    (("GLUCOCORTICOID",), ["Corticosteroids"]),
    (("CORTICOSTEROID",), ["Corticosteroids"]),
    (("GROWTH HORMONE",), ["Growth Hormone"]),
    (("WEIGHT LOSS",), ["Anti-obesity medications"]),
    (("ANTI-OBESITY",), ["Anti-obesity medications"]),
    (("APPETITE",), ["Appetite-related medications"]),
    # --- GI / liver --------------------------------------------------------------------------
    (("PROTON PUMP",), ["PPIs", "GI medications", "GI Protection", "Gastroprotective agents"]),
    (("H2 ANTAGONIST",), ["H2 Blockers", "GI medications", "GI Protection"]),
    (("PEPTIC ULCER",), ["GI medications", "Gastroprotective agents"]),
    (("ANTACID",), ["Antacids", "GI medications"]),
    (("ANTIEMETIC",), ["Antiemetics", "GI medications"]),
    (("ANTI-EMETIC",), ["Antiemetics", "GI medications"]),
    (("PROKINETIC",), ["Prokinetics", "GI medications"]),
    (("ANTISPASMODIC", "!URINARY", "!BLADDER"), ["Antispasmodics", "GI medications"]),
    (("ANTI-SPASMODIC", "!URINARY", "!BLADDER"), ["Antispasmodics", "GI medications"]),
    (("URINARY INCONTINENCE",), ["Urinary Antispasmodics", "Overactive Bladder medications"]),
    (("IRRITABLE BOWEL",), ["IBS medications", "GI medications"]),
    (("LAXATIVE",), ["Laxatives", "Constipation medications", "GI medications"]),
    (("ANTIDIARRH",), ["Antidiarrheals", "GI medications"]),
    (("ANTI-DIARRH",), ["Antidiarrheals", "GI medications"]),
    (("REHYDRATION",), ["ORS / Rehydration", "ORS", "Electrolytes"]),
    (("CARMINATIVE",), ["GI medications"]),
    (("DIGESTIVE ENZYME",), ["Pancreatic Enzymes", "GI medications"]),
    (("PANCREATIC",), ["Pancreatic Enzymes"]),
    (("HEPATOPROTECT",), ["Liver medications"]),
    (("LIVER", "!COD LIVER"), ["Liver medications"]),
    (("BILE",), ["Bile Acid preparations"]),
    (("INFLAMMATORY BOWEL",), ["IBD medications"]),
    # --- respiratory / allergy -----------------------------------------------------------------
    (("ANTI-HISTAMINE", "!H2"), ["Antihistamines", "Allergy medications"]),
    (("ANTIHISTAMINE", "!H2"), ["Antihistamines", "Allergy medications"]),
    (("ANTI-ALLERGY",), ["Allergy medications"]),
    (("BRONCHODILATOR",), ["Bronchodilators", "Respiratory medications"]),
    (("ANTI-ASTHMA",), ["Bronchodilators", "Respiratory medications"]),
    (("LEUKOTRIENE",), ["Leukotriene Antagonists"]),
    (("MUCOLYTIC",), ["Mucolytics", "Respiratory medications"]),
    (("EXPECTORANT",), ["Expectorants", "Respiratory medications"]),
    (("COUGH",), ["Antitussives", "Respiratory medications"]),
    (("ANTITUSSIVE",), ["Antitussives", "Respiratory medications"]),
    (("COLD PRODUCTS",), ["Respiratory medications"]),
    (("NASAL DECONGESTANT",), ["Nasal Decongestants"]),
    (("DECONGESTANT",), ["Nasal Decongestants"]),
    # --- neuro / psych -------------------------------------------------------------------------
    (("ANTI-EPILEPTIC",), ["Antiepileptics", "Anticonvulsants", "Antiepileptics / Anticonvulsants"]),
    (("ANTIEPILEPTIC",), ["Antiepileptics", "Anticonvulsants", "Antiepileptics / Anticonvulsants"]),
    (("ANTICONVULSANT",), ["Anticonvulsants", "Antiepileptics / Anticonvulsants"]),
    (("GABA ANALOG",), ["Neuropathic Pain medications"]),
    (("NEUROPATH",), ["Neuropathic Pain medications"]),
    (("ANTIDEPRESSANT",), ["Antidepressants"]),
    (("ANTIPSYCHOTIC",), ["Antipsychotics"]),
    (("ANXIOLYTIC",), ["Anxiolytics"]),
    (("HYPNOTIC",), ["Sleep medications", "Hypnotics"]),
    (("BENZODIAZEPINE",), ["Benzodiazepines"]),
    (("MOOD STABILIZER",), ["Mood Stabilizers"]),
    (("ADHD",), ["ADHD medications"]),
    (("PARKINSON",), ["Parkinson's medications"]),
    (("ALZHEIMER",), ["Dementia medications"]),
    (("DEMENTIA",), ["Dementia medications"]),
    (("VERTIGO",), ["Vertigo medications"]),
    (("MULTIPLE SCLEROSIS",), ["Multiple Sclerosis medications"]),
    (("SMOKING",), ["Smoking-cessation medications"]),
    (("NEUROTONIC",), ["Vitamins / Neuro supplements"]),
    # --- vitamins / blood ----------------------------------------------------------------------
    (("MULTIVITAMIN",), ["Vitamins & Minerals", "Vitamin Supplements", "Vitamins"]),
    (("VITAMIN D",), ["Vitamin D"]),
    (("VITAMIN B12",), ["Vitamin B12"]),
    (("VITAMIN B",), ["Vitamins / Neuro supplements", "Vitamins"]),
    (("VITAMIN",), ["Vitamins & Minerals", "Vitamin Supplements"]),
    (("CALCIUM SUPPLEMENT",), ["Calcium"]),
    (("MINERAL",), ["Mineral Supplements", "Vitamins & Minerals"]),
    (("MAGNESIUM",), ["Magnesium preparations"]),
    (("IRON",), ["Iron", "Iron preparations", "Hematinics"]),
    (("FOLIC",), ["Folic Acid"]),
    (("HEMATINIC",), ["Hematinics"]),
    (("ERYTHROPOIE",), ["Erythropoietin"]),
    (("HEMOSTATIC",), ["Hemostatic agents"]),
    (("ANTIFIBRINOLYTIC",), ["Antifibrinolytics"]),
    (("PRENATAL",), ["Prenatal Vitamins", "Pregnancy supplements"]),
    (("DIETARY SUPPLEMENT",), ["Nutritional Preparations"]),
    (("NUTRITION",), ["Nutritional Preparations", "Nutrition"]),
    (("ELECTROLYTE",), ["Electrolytes"]),
    (("I.V. SOLUTION",), ["IV Fluids"]),
    (("IV FLUID",), ["IV Fluids"]),
    # --- oncology / immunology -------------------------------------------------------------------
    (("ANTINEOPLASTIC",), ["Chemotherapy", "Oncology"]),
    (("CYTOTOXIC",), ["Chemotherapy", "Oncology"]),
    (("TARGETED",), ["Targeted Therapy", "Oncology"]),
    (("IMMUNOSUPPRESSANT",), ["Immunosuppressants"]),
    (("IMMUNOMODULATOR",), ["Immunomodulators"]),
    (("IMMUNOSTIMULANT",), ["Immunomodulators"]),
    (("COLONY STIMULATING",), ["G-CSF"]),
    (("METHOTREXATE",), ["Methotrexate", "DMARDs"]),
    # --- women / men / urology ---------------------------------------------------------------------
    (("CONTRACEPTIVE",), ["Oral Contraceptives", "Contraceptives", "Hormonal medications"]),
    (("PROGESTERONE",), ["Progesterone", "Hormonal medications"]),
    (("PROGESTIN",), ["Progesterone", "Hormonal medications"]),
    (("PROGESTOGEN",), ["Progesterone", "Hormonal medications"]),
    (("ESTROGEN", "!ANTAGONIST", "!ANTI-ESTROGEN", "!ANTI ESTROGEN", "!RECEPTOR MODULATOR", "!PHYTO"), ["Estrogen", "Hormonal medications", "HRT"]),
    (("OVULATION",), ["Ovulation Induction", "Infertility medications"]),
    (("GONADOTROPHIN",), ["Gonadotropins", "Infertility medications"]),
    (("GONADOTROPIN",), ["Gonadotropins", "Infertility medications"]),
    (("UTEROTONIC",), ["Uterotonics"]),
    (("TOCOLYTIC",), ["Tocolytics"]),
    (("VAGINAL",), ["Vaginal preparations"]),
    (("ERECTILE",), ["Erectile Dysfunction medications", "PDE5 Inhibitors"]),
    (("PDE5",), ["PDE5 Inhibitors", "Erectile Dysfunction medications"]),
    (("PHOSPHODIESTERASE 5",), ["PDE5 Inhibitors", "Erectile Dysfunction medications"]),
    (("PHOSPHODIESTRASE 5",), ["PDE5 Inhibitors", "Erectile Dysfunction medications"]),
    (("PREMATURE EJACULATION",), ["Premature Ejaculation medications"]),
    (("ANDROGEN", "!ANTIANDROGEN", "!ANTI-ANDROGEN", "!ANTI ANDROGEN", "!INHIBITOR", "!ALOPECIA"), ["Testosterone", "Testosterone Replacement", "Hormonal Therapy"]),
    (("ANTIANDROGEN",), ["Hormonal Therapy"]),
    (("ANTI-ANDROGEN",), ["Hormonal Therapy"]),
    (("TESTOSTERONE",), ["Testosterone", "Testosterone Replacement"]),
    (("BENIGN PROSTATIC",), ["BPH medications"]),
    (("PROSTAT",), ["BPH medications"]),
    (("ALPHA BLOCKER",), ["Alpha Blockers", "BPH medications"]),
    (("5-ALPHA",), ["5-alpha Reductase Inhibitors", "BPH medications"]),
    (("OVERACTIVE BLADDER",), ["Overactive Bladder medications", "Urinary Antispasmodics"]),
    (("URINARY ANTISPASMODIC",), ["Urinary Antispasmodics"]),
    (("URINARY ALKALIN",), ["Urinary Alkalinizers"]),
    (("KIDNEY STONE",), ["Kidney Stone medications", "Stone Prevention medications"]),
    (("UROLITHIASIS",), ["Kidney Stone medications"]),
    # --- skin / eye / ENT -----------------------------------------------------------------------------
    (("ANTI-ACNE",), ["Acne medications"]),
    (("ACNE",), ["Acne medications"]),
    (("RETINOID",), ["Retinoids"]),
    (("PSORIASIS",), ["Psoriasis medications"]),
    (("ECZEMA",), ["Eczema medications"]),
    (("ATOPIC",), ["Atopic Dermatitis medications"]),
    (("HAIR LOSS",), ["Hair-loss medications", "Alopecia medications"]),
    (("ALOPECIA",), ["Alopecia medications", "Hair-loss medications"]),
    (("HEALING",), ["Wound-care medications"]),
    (("WOUND",), ["Wound-care medications"]),
    (("BURN",), ["Wound-care medications"]),
    (("ARTIFICIAL TEAR",), ["Artificial Tears", "Eye Lubricants"]),
    (("LUBRICANT",), ["Eye Lubricants"]),
    (("GLAUCOMA",), ["Glaucoma medications"]),
    (("MYDRIATIC",), ["Mydriatics"]),
    (("MIOTIC",), ["Miotics"]),
    (("THROAT",), ["Throat Lozenges", "Throat Antiseptics"]),
    (("LOZENGE",), ["Throat Lozenges"]),
    (("EAR WAX",), ["Wax Removal"]),
    (("CERUMEN",), ["Wax Removal"]),
    # --- spellings / labels seen in the Egyptian data -------------------------------------------
    # The source labels direct factor Xa / thrombin inhibitors (apixaban, rivaroxaban, dabigatran) "ANTIPLATLET.*";
    # clinically they are anticoagulants, never antiplatelets.
    (("ANTIPLATLET", "!FACTOR XA", "!THROMBIN", "!PHOSPHODIESTRASE 5"), ["Antiplatelets"]),
    (("FACTOR XA",), ["Anticoagulants"]),
    (("THROMBIN INHIBITOR",), ["Anticoagulants"]),
    (("ANTI-INFLAMMATORY",), ["Anti-inflammatory medications"]),
    (("CONTRACEPTION",), ["Oral Contraceptives", "Contraceptives"]),
    (("SLEEP AID",), ["Sleep medications"]),
    (("ANTIFLATULENT",), ["GI medications"]),
    (("GIT DISTURBANCES",), ["GI medications"]),
    (("TRAVELERS DIARRHEA",), ["Antidiarrheals", "GI medications"]),
    (("ATTENTION-DEFICIT",), ["ADHD medications"]),
    (("ZINC",), ["Mineral Supplements"]),
    (("OMEGA 3",), ["Vitamin Supplements"]),
    (("SUPPLEMENT",), ["Nutritional Preparations"]),
    (("MILK PRODUCTS",), ["Nutrition", "Nutritional Preparations"]),
    (("DIAPER RASH",), ["Pediatric Dermatology", "Dermatology medications"]),
    (("SCAR",), ["Wound-care medications"]),
    (("MOUTH WASH",), ["Throat Antiseptics"]),
    (("RINGER",), ["IV Fluids", "Electrolytes"]),
    (("NORMAL SALINE",), ["IV Fluids"]),
    (("GLUCOSE SOLUTION",), ["IV Fluids"]),
]  # fmt: skip

# A few categories are clearer from the active ingredient than from the class label.
INGREDIENT_RULES: list[tuple[str, list[str]]] = [
    ("PARACETAMOL", ["Paracetamol", "Analgesics / Antipyretics", "Analgesics"]),
    ("METHOTREXATE", ["Methotrexate"]),
    ("INSULIN ", ["Insulins", "Insulin", "Diabetes medications"]),
    ("METFORMIN", ["Biguanides"]),
    ("CHOLECALCIFEROL", ["Vitamin D"]),
    ("ALFACALCIDOL", ["Vitamin D"]),
]

# Route-dependent refinements: (route, category already assigned) → extra categories.
ROUTE_EXTRAS: dict[tuple[str, str], list[str]] = {
    ("ORAL.SOLID", "Antibiotics"): ["Oral Antibiotics"],
    ("ORAL.LIQUID", "Antibiotics"): ["Oral Antibiotics", "Pediatric Antibiotics"],
    ("ORAL.LIQUID", "Analgesics / Antipyretics"): ["Pediatric Antipyretics", "Pediatric Analgesics"],
    ("ORAL.LIQUID", "Antihistamines"): ["Pediatric Antihistamines"],
    ("ORAL.LIQUID", "Respiratory medications"): ["Pediatric Respiratory medications"],
    ("ORAL.LIQUID", "GI medications"): ["Pediatric GI medications"],
    ("ORAL.LIQUID", "Vitamins & Minerals"): ["Pediatric Vitamins"],
    ("ORAL.LIQUID", "Antifungals"): ["Pediatric Antifungals"],
    ("ORAL.LIQUID", "Anticonvulsants"): ["Pediatric Anticonvulsants"],
    ("TOPICAL", "Antibiotics"): ["Topical Antibiotics"],
    ("TOPICAL", "Corticosteroids"): ["Topical Corticosteroids", "Pediatric Topical Steroids"],
    ("TOPICAL", "Analgesics"): ["Topical Analgesics"],
    ("TOPICAL", "NSAIDs"): ["Topical Analgesics"],
    ("TOPICAL", "Antifungals"): ["Dermatology medications"],
    ("EYE", "Antibiotics"): ["Antibiotic Eye Drops", "Pediatric Ophthalmic Antibiotics"],
    ("EYE", "Corticosteroids"): ["Steroid Eye Drops"],
    ("EYE", "Antihistamines"): ["Antiallergic Eye Drops"],
    ("EYE", "Allergy medications"): ["Antiallergic Eye Drops"],
    ("EYE", "Antivirals"): ["Ocular Antivirals"],
    ("EYE", "Antifungals"): ["Ocular Antifungals"],
    ("EAR", "Antibiotics"): ["Antibiotic Ear Drops"],
    ("EAR", "Corticosteroids"): ["Steroid Ear Drops"],
    ("EAR", "Antifungals"): ["Antifungal Ear Drops"],
    ("INJECTION", "Antibiotics"): ["Surgical Prophylaxis Antibiotics"],
}

# Inhaled corticosteroids / combinations are recognisable from the class text + route together.
_STEROID = ("CORTICOSTEROID", "GLUCOCORTICOID")
_ASTHMA = ("BRONCHODILATOR", "ASTHMA", "INHAL")
SPECIAL = [
    # Sprays: asthma inhalers vs nasal sprays look alike in the source data.
    (
        lambda cls, route: route == "SPRAY" and any(s in cls for s in _STEROID) and any(a in cls for a in _ASTHMA),
        ["Inhaled Corticosteroids"],
    ),
    (
        lambda cls, route: route == "SPRAY" and any(s in cls for s in _STEROID) and not any(a in cls for a in _ASTHMA),
        ["Nasal Steroids", "Nasal Corticosteroids"],
    ),
    (
        lambda cls, route: "NASAL" in cls and ("WASH" in cls or "CARE" in cls or "SALINE" in cls),
        ["Saline Nasal Preparations"],
    ),
    (
        lambda cls, route: "ARTIFICIAL" in cls or (route == "EYE" and "LUBRIC" in cls),
        ["Artificial Tears", "Eye Lubricants"],
    ),
]

KNOWN = {name for _label, items in SPECIALTIES.values() for name, _lvl in items}

# "ACE" must be a whole word (not "SURFACE", "PEACE"...).
_WORD_ONLY = {"ACE", "IRON", "BURN", "BILE", "LIVER", "ACNE", "SCAR", "ZINC"}


def _has(cls: str, word: str) -> bool:
    if word in _WORD_ONLY:
        return re.search(rf"(^|[^A-Z]){re.escape(word)}([^A-Z]|$)", cls) is not None
    return word in cls


def categories_for(drug_class: str, route: str, scientific_name: str = "") -> list[str]:
    cls = (drug_class or "").upper()
    route = (route or "").upper()
    if "ANTIDOTE" in cls:
        return []  # naloxone, flumazenil, vitamin K... — named after what they reverse, so keywords would mislead
    ingredients = (scientific_name or "").upper()
    found: list[str] = []
    for keywords, cats in RULES:
        if all((not _has(cls, k[1:])) if k.startswith("!") else _has(cls, k) for k in keywords):
            found += cats
    for ingredient, cats in INGREDIENT_RULES:
        if ingredient in ingredients and not (ingredient == "SODIUM CHLORIDE" and route not in ("SPRAY", "NASAL")):
            found += cats
    for test, cats in SPECIAL:
        if test(cls, route):
            found += cats
    for (r, base), extra in ROUTE_EXTRAS.items():
        if route == r and base in found:
            found += extra
    seen, out = set(), []
    for c in found:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


ROUTE_FORMS = {
    "ORAL.SOLID": "قرص",
    "ORAL.LIQUID": "شراب",
    "INJECTION": "حقن",
    "TOPICAL": "كريم",
    "EFF": "فوار",
    "SPRAY": "بخاخ",
    "EYE": "نقط عين",
    "EAR": "نقط أذن",
    "VAGINAL": "لبوس مهبلي",
    "RECTAL": "لبوس",
    "MOUTH": "للفم",
}
