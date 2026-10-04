# ruff: noqa: E501, E741  -- data table: one line per sub-specialty, short level names (P/C/O/N) on purpose
"""Drug-category presets per specialty, in the clinic's order (as provided by the clinic, 2026-10-05).

Picking a specialty in Settings → "تصنيفات الأدوية" copies its list into the doctor's own categories, which the doctor
can then hide, reorder or extend. Levels are kept as information (all categories are shown, in this order).
Sub-specialty lists were given without levels: they are all "primary".
"""

P, C, O, N = "primary", "common", "occasional", "not_typical"


def _all(level, *names):
    return [(n, level) for n in names]


SPECIALTIES: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "internal_medicine": ("الباطنة العامة", [
        ("Antihypertensives", P), ("Diabetes medications", P), ("GI medications", P),
        ("Analgesics / Antipyretics", C), ("NSAIDs", C), ("Antibiotics", C), ("Respiratory medications", C),
        ("Antihistamines", C), ("Lipid-lowering drugs", C), ("Vitamins & Minerals", C),
        ("Anticoagulants", O), ("Antiplatelets", O), ("Corticosteroids", O), ("Antiepileptics", O),
        ("Antidepressants", O), ("Antifungals", O), ("Antivirals", O), ("Chemotherapy", N),
    ]),
    "cardiology": ("القلب والأوعية الدموية", [
        ("Antihypertensives", P), ("Beta Blockers", P), ("ACE Inhibitors", P), ("ARBs", P),
        ("Calcium Channel Blockers", P), ("Diuretics", P), ("Heart Failure medications", P), ("Anticoagulants", P),
        ("Antiplatelets", P), ("Statins / Lipid-lowering", P), ("Antianginals", P), ("Antiarrhythmics", P),
        ("Nitrates", C), ("Pulmonary Hypertension drugs", O), ("Electrolytes", C), ("Analgesics", O),
        ("Antibiotics", O), ("Diabetes medications", C), ("GI medications", O),
    ]),
    "neurology": ("المخ والأعصاب", [
        ("Antiepileptics / Anticonvulsants", P), ("Neuropathic Pain medications", P), ("Migraine medications", P),
        ("Parkinson's medications", P), ("Dementia medications", P), ("Vertigo medications", C),
        ("Muscle Relaxants", C), ("Sleep medications", C), ("Multiple Sclerosis medications", P),
        ("Stroke-related medications", P), ("Analgesics", C), ("Corticosteroids", O), ("Antidepressants", C),
        ("Antibiotics", O), ("Vitamins / Neuro supplements", C),
    ]),
    "psychiatry": ("الطب النفسي", [
        ("Antidepressants", P), ("Antipsychotics", P), ("Anxiolytics", P), ("Mood Stabilizers", P),
        ("Benzodiazepines", P), ("ADHD medications", P), ("Sleep medications", C), ("Addiction medications", P),
        ("Smoking-cessation medications", O), ("Anticonvulsants", C), ("Analgesics", O), ("Vitamins", O),
        ("Antibiotics", N),
    ]),
    "pediatrics": ("طب الأطفال", [
        ("Pediatric Antipyretics", P), ("Pediatric Analgesics", P), ("Pediatric Antibiotics", P),
        ("Pediatric Respiratory medications", P), ("Pediatric Antihistamines", P), ("Pediatric GI medications", P),
        ("ORS / Rehydration", C), ("Pediatric Vitamins", C), ("Iron", C), ("Vitamin D", C),
        ("Deworming medications", C), ("Pediatric Antifungals", C), ("Pediatric Anticonvulsants", O),
        ("Corticosteroids", O), ("Pediatric Dermatology", C), ("Antidepressants", O),
    ]),
    "chest": ("الأمراض الصدرية", [
        ("Bronchodilators", P), ("Inhaled Corticosteroids", P), ("LABA", P), ("LAMA", P),
        ("ICS/LABA combinations", P), ("Leukotriene Antagonists", C), ("Mucolytics", C), ("Expectorants", C),
        ("Antitussives", C), ("Antibiotics", C), ("Antihistamines", C), ("Nasal Corticosteroids", O),
        ("Pulmonary Hypertension medications", O), ("Anticoagulants", O), ("Antifungals", O),
    ]),
    "gastro_hepatology": ("الجهاز الهضمي والكبد", [
        ("PPIs", P), ("Antacids", P), ("H2 Blockers", C), ("Gastroprotective agents", P), ("Prokinetics", P),
        ("Antiemetics", P), ("Antispasmodics", P), ("IBS medications", P), ("Laxatives", C), ("Antidiarrheals", C),
        ("ORS", C), ("IBD medications", P), ("Pancreatic Enzymes", O), ("Liver medications", P),
        ("Bile Acid preparations", O), ("Hepatitis medications", P), ("Antibiotics", C), ("Analgesics", O),
    ]),
    "hematology": ("أمراض الدم", [
        ("Iron preparations", P), ("Folic Acid", P), ("Vitamin B12", P), ("Hematinics", P), ("Erythropoietin", P),
        ("Anticoagulants", P), ("Antiplatelets", C), ("Antifibrinolytics", C), ("Hemostatic agents", P),
        ("Coagulation Factors", P), ("Thalassemia medications", P), ("Sickle Cell medications", P),
        ("Immunosuppressants", C), ("Chemotherapy", P), ("Analgesics", C),
    ]),
    "endocrinology": ("الغدد والسكر", [
        ("Biguanides", P), ("Sulfonylureas", P), ("DPP-4 Inhibitors", P), ("SGLT2 Inhibitors", P),
        ("GLP-1 Receptor Agonists", P), ("Insulins", P), ("Combination Antidiabetics", P), ("Thyroid Hormones", P),
        ("Antithyroid Drugs", P), ("Corticosteroids", C), ("Hormone Replacement", P), ("Growth Hormone", P),
        ("Calcium", C), ("Vitamin D", C), ("Osteoporosis medications", C), ("Antihypertensives", C),
        ("Lipid-lowering drugs", C),
    ]),
    "rheumatology": ("الروماتيزم والمناعة", [
        ("NSAIDs", P), ("Corticosteroids", P), ("DMARDs", P), ("Methotrexate", P), ("Biologic DMARDs", P),
        ("Immunosuppressants", P), ("Immunomodulators", P), ("Gout medications", P), ("Urate-lowering drugs", P),
        ("Osteoporosis medications", C), ("Calcium", C), ("Vitamin D", C), ("Analgesics", C),
        ("Muscle Relaxants", C), ("Antibiotics", O),
    ]),
    "orthopedics": ("العظام", [
        ("Analgesics", P), ("NSAIDs", P), ("Muscle Relaxants", C), ("Calcium", P), ("Vitamin D", P),
        ("Osteoporosis medications", P), ("Bisphosphonates", P), ("Joint medications", C), ("Topical Analgesics", C),
        ("Bone-support medications", C), ("Gout medications", O), ("Antibiotics", O), ("Anticoagulants", O),
    ]),
    "dermatology": ("الجلدية", [
        ("Topical Corticosteroids", P), ("Topical Antibiotics", P), ("Antifungals", P), ("Acne medications", P),
        ("Retinoids", P), ("Benzoyl Peroxide", P), ("Antihistamines", P), ("Psoriasis medications", P),
        ("Eczema medications", P), ("Atopic Dermatitis medications", P), ("Scabies medications", C),
        ("Pediculosis medications", C), ("Hair-loss medications", P), ("Alopecia medications", P),
        ("Wound-care medications", C), ("Antiseptics", C), ("Oral Antibiotics", C), ("Antivirals", O),
        ("Systemic Immunosuppressants", O), ("Biologics", O),
    ]),
    "ophthalmology": ("العيون", [
        ("Artificial Tears", P), ("Eye Lubricants", P), ("Antibiotic Eye Drops", P), ("Steroid Eye Drops", P),
        ("Antibiotic + Steroid Drops", C), ("Antiallergic Eye Drops", P), ("Glaucoma medications", P),
        ("Anti-VEGF", P), ("Retinal medications", P), ("Mydriatics", C), ("Miotics", O), ("Ocular Antivirals", O),
        ("Ocular Antifungals", O),
    ]),
    "ent": ("أنف وأذن وحنجرة", [
        ("Nasal Steroids", P), ("Nasal Decongestants", P), ("Antihistamines", P), ("Saline Nasal Preparations", P),
        ("Sinus medications", P), ("Antibiotic Ear Drops", C), ("Wax Removal", C), ("Vertigo medications", P),
        ("Throat Lozenges", C), ("Throat Antiseptics", C), ("Local Anesthetics", O), ("Systemic Antibiotics", C),
        ("Steroid Ear Drops", O), ("Antifungal Ear Drops", O),
    ]),
    "obgyn": ("النساء والتوليد", [
        ("Prenatal Vitamins", P), ("Folic Acid", P), ("Iron", P), ("Calcium", P), ("Vitamin D", C),
        ("Hormonal medications", P), ("Oral Contraceptives", P), ("Progesterone", P), ("Estrogen", C),
        ("Vaginal preparations", P), ("Antifungals", P), ("Antibiotics", C), ("Infertility medications", P),
        ("Ovulation Induction", P), ("Gonadotropins", P), ("Endometriosis medications", P), ("Antiemetics", C),
        ("Uterotonics", P), ("Tocolytics", P), ("HRT", C),
    ]),
    "high_risk_pregnancy": ("الحمل عالي الخطورة / طب الأم والجنين", [
        ("Antihypertensives", P), ("Insulin", P), ("Anticoagulants", P), ("Low-dose Antiplatelet therapy", P),
        ("Iron", P), ("Folic Acid", P), ("Calcium", P), ("Vitamin D", C), ("Corticosteroids", P), ("Tocolytics", P),
        ("Antiemetics", C), ("Thyroid medications", C), ("Pregnancy-safe Antibiotics", C),
        ("Magnesium preparations", C),
    ]),
    "urology": ("المسالك البولية", [
        ("BPH medications", P), ("Alpha Blockers", P), ("5-alpha Reductase Inhibitors", P),
        ("Overactive Bladder medications", P), ("Urinary Antispasmodics", P), ("UTI medications", P),
        ("Antibiotics", P), ("Urinary Alkalinizers", C), ("Kidney Stone medications", P),
        ("Stone Prevention medications", P), ("Erectile Dysfunction medications", C), ("Testosterone", O),
        ("Male Fertility medications", O), ("Analgesics", C),
    ]),
    "andrology": ("أمراض الذكورة", [
        ("PDE5 Inhibitors", P), ("Erectile Dysfunction medications", P), ("Premature Ejaculation medications", P),
        ("Testosterone Replacement", P), ("Male Infertility medications", P), ("Hormonal Therapy", P),
        ("BPH medications", C), ("Antibiotics", C), ("Antifungals", O), ("Analgesics", O),
    ]),
    "general_surgery": ("الجراحة العامة", [
        ("Analgesics", P), ("NSAIDs", P), ("Antibiotics", P), ("Surgical Prophylaxis Antibiotics", P),
        ("Antiseptics", P), ("Wound-care medications", P), ("Topical Antibiotics", C), ("Antiemetics", C),
        ("GI Protection", C), ("Laxatives", C), ("Anticoagulants", C), ("IV Fluids", P), ("Electrolytes", C),
        ("Nutritional Preparations", O),
    ]),
    "neurosurgery": ("جراحة المخ والأعصاب", [
        ("Analgesics", P), ("Anticonvulsants", P), ("Corticosteroids", P), ("Osmotic Diuretics", P),
        ("Antibiotics", P), ("Antiemetics", C), ("Anticoagulants", C), ("Muscle Relaxants", C),
        ("Neuropathic Pain medications", P), ("GI Protection", C),
    ]),
    "vascular_surgery": ("جراحة الأوعية الدموية", [
        ("Antiplatelets", P), ("Anticoagulants", P), ("Statins", P), ("Antihypertensives", P), ("Vasodilators", C),
        ("Venous Insufficiency medications", P), ("Wound-care medications", P), ("Analgesics", C),
        ("Antibiotics", C), ("Antiseptics", C),
    ]),
    "oncology": ("الأورام", [
        ("Chemotherapy", P), ("Targeted Therapy", P), ("Immunotherapy", P), ("Hormonal Therapy", P),
        ("Antiemetics", P), ("Corticosteroids", P), ("Opioid Analgesics", P), ("Pain medications", P),
        ("Bone-modifying agents", C), ("G-CSF", P), ("Erythropoietin", C), ("Anemia medications", C),
        ("Palliative medications", P), ("Antibiotics", C),
    ]),
    "allergy_immunology": ("الحساسية والمناعة", [
        ("Antihistamines", P), ("Corticosteroids", P), ("Leukotriene Antagonists", P), ("Immunosuppressants", P),
        ("Immunomodulators", P), ("Biologics", P), ("Immunoglobulins", P), ("Allergy medications", P),
        ("Anaphylaxis medications", P), ("Antibiotics", O),
    ]),
    "infectious_diseases": ("الأمراض المعدية", [
        ("Penicillins", P), ("Cephalosporins", P), ("Macrolides", P), ("Tetracyclines", P), ("Fluoroquinolones", P),
        ("Aminoglycosides", C), ("Carbapenems", C), ("Glycopeptides", C), ("Antivirals", P), ("Antifungals", P),
        ("Antiparasitics", P), ("Antiprotozoals", P), ("Antituberculosis drugs", P), ("Antiretrovirals", P),
        ("Vaccines", C), ("Immunoglobulins", C),
    ]),
    "pain_medicine": ("طب الألم", [
        ("Paracetamol", P), ("NSAIDs", P), ("Opioids", P), ("Neuropathic Pain medications", P),
        ("Antidepressants for Pain", P), ("Anticonvulsants for Pain", P), ("Muscle Relaxants", C),
        ("Topical Analgesics", C), ("Local Anesthetics", P),
    ]),
    "family_medicine": ("طب الأسرة", [
        ("Analgesics", P), ("Antibiotics", C), ("Antihistamines", P), ("Respiratory medications", P),
        ("GI medications", P), ("Antihypertensives", P), ("Diabetes medications", P), ("Lipid-lowering drugs", P),
        ("Dermatology medications", C), ("Vitamins & Minerals", C), ("Pediatric medications", P),
        ("Women's Health medications", P), ("Contraceptives", C), ("Smoking-cessation medications", O),
        ("Vaccines", P),
    ]),
    "nutrition_obesity": ("التغذية والسمنة", [
        ("Anti-obesity medications", P), ("GLP-1 medications", P), ("Diabetes medications", P),
        ("Vitamin Supplements", P), ("Mineral Supplements", P), ("Iron", C), ("Vitamin D", P), ("Calcium", C),
        ("Nutritional Preparations", P), ("Appetite-related medications", C),
    ]),
    "sports_medicine": ("طب الرياضة", [
        ("NSAIDs", P), ("Analgesics", P), ("Muscle Relaxants", C), ("Topical Analgesics", P),
        ("Anti-inflammatory medications", P), ("Joint medications", C), ("Calcium", C), ("Vitamin D", C),
        ("Bone-health medications", O), ("Antibiotics", O),
    ]),
    "geriatrics": ("طب المسنين", [
        ("Antihypertensives", P), ("Anticoagulants", P), ("Antiplatelets", P), ("Diabetes medications", P),
        ("Lipid-lowering drugs", P), ("Osteoporosis medications", P), ("Analgesics", P), ("Dementia medications", P),
        ("Parkinson's medications", C), ("GI medications", P), ("Constipation medications", C),
        ("Vitamins & Minerals", C),
    ]),
    # --- sub-specialty clinics (lists given without levels) ---------------------------------
    "ped_cardiology": ("قلب أطفال", _all(P, "Pediatric Cardiac", "Antihypertensives", "Antiarrhythmics", "Anticoagulants")),
    "ped_neurology": ("أعصاب أطفال", _all(P, "Pediatric Anticonvulsants", "Neuropathic Pain medications", "Migraine medications")),
    "ped_endocrinology": ("غدد أطفال", _all(P, "Pediatric Diabetes", "Insulin", "Thyroid medications", "Growth Hormone")),
    "ped_nephrology": ("كلى أطفال", _all(P, "Pediatric UTI", "Electrolytes", "Diuretics", "Renal medications")),
    "ped_gastro": ("جهاز هضمي أطفال", _all(P, "Pediatric GI medications", "Antiemetics", "Antidiarrheals", "Laxatives")),
    "ped_chest": ("صدر أطفال", _all(P, "Pediatric Bronchodilators", "Inhaled Corticosteroids", "Antihistamines", "Antibiotics")),
    "ped_rheumatology": ("روماتيزم أطفال", _all(P, "NSAIDs", "Corticosteroids", "DMARDs", "Immunosuppressants")),
    "ped_oncology": ("أورام أطفال", _all(P, "Chemotherapy", "Targeted Therapy", "Immunotherapy", "Supportive Oncology")),
    "neonatology": ("حديثي الولادة", _all(P, "Neonatal Antibiotics", "Nutrition", "Electrolytes", "Respiratory medications")),
    "ped_ophthalmology": ("عيون أطفال", _all(P, "Pediatric Ophthalmic Antibiotics", "Steroid Eye Drops", "Antiallergic Eye Drops", "Eye Lubricants")),
    "ped_dermatology": ("جلدية أطفال", _all(P, "Pediatric Topical Steroids", "Antifungals", "Topical Antibiotics", "Antihistamines")),
    "ped_urology": ("مسالك أطفال", _all(P, "Pediatric UTI", "Antibiotics", "Urinary Antispasmodics", "Kidney Stone medications")),
    "gyn_oncology": ("أورام نساء", _all(P, "Oncology", "Hormonal Therapy", "Antiemetics", "Pain medications")),
    "uro_oncology": ("أورام المسالك", _all(P, "Oncology", "Hormonal Therapy", "Urology medications")),
    "head_neck_oncology": ("أورام الرأس والرقبة", _all(P, "Oncology", "Antibiotics", "Analgesics", "Supportive care")),
    "gi_oncology": ("أورام الجهاز الهضمي", _all(P, "Oncology", "GI medications", "Antiemetics", "Pain medications")),
    "infertility": ("عقم وتأخر الإنجاب", _all(P, "Ovulation Induction", "Gonadotropins", "Hormonal Therapy")),
    "maternal_fetal": ("طب الأم والجنين", _all(P, "Pregnancy-safe Antihypertensives", "Anticoagulants", "Insulin", "Corticosteroids")),
    "high_risk_pregnancy_clinic": ("الحمل عالي الخطورة", _all(P, "Anticoagulants", "Antihypertensives", "Insulin", "Pregnancy supplements")),
    "diabetes_clinic": ("عيادة السكر", _all(P, "Antidiabetics", "Insulin", "GLP-1 Receptor Agonists", "SGLT2 Inhibitors")),
    "thyroid_clinic": ("عيادة الغدة الدرقية", _all(P, "Thyroid Hormones", "Antithyroid Drugs")),
    "obesity_clinic": ("عيادة السمنة", _all(P, "Anti-obesity medications", "GLP-1 medications", "Diabetes medications")),
    "pain_clinic": ("عيادة الألم", _all(P, "Analgesics", "NSAIDs", "Opioids", "Neuropathic Pain medications")),
    "sleep_clinic": ("عيادة النوم", _all(P, "Hypnotics", "Sleep medications", "Psychiatric medications")),
}  # fmt: skip

SPECIALTY_CHOICES = [(key, name) for key, (name, _cats) in SPECIALTIES.items()]
