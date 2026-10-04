"""Allergy and duplicate warnings for the prescription builder (03 §3.6).

Matching is deliberately simple and conservative: allergy text vs the drug's trade name, generic name and Arabic
aliases, plus a small table of drug classes ("Penicillin" also matches Amoxicillin, Flucloxacillin...). It is a
safety net, not a clinical decision system — the doctor must still review, and every warning must be acknowledged.
"""

import re

from apps.core.arabic import normalize_arabic

# class keyword (as a doctor would write the allergy, English or Arabic) → generic names it covers
DRUG_CLASSES = {
    "penicillin": ["penicillin", "amoxicillin", "ampicillin", "flucloxacillin", "cloxacillin", "piperacillin",
                   "benzathine", "phenoxymethyl"],
    "بنسلين": ["penicillin", "amoxicillin", "ampicillin", "flucloxacillin", "cloxacillin", "piperacillin",
               "benzathine", "phenoxymethyl"],
    "cephalosporin": ["cef", "ceph"],
    "sulfa": ["sulfamethoxazole", "sulfasalazine", "sulfadiazine"],
    "سلفا": ["sulfamethoxazole", "sulfasalazine", "sulfadiazine"],
    "nsaid": ["ibuprofen", "diclofenac", "naproxen", "ketoprofen", "acetylsalicylic", "aspirin", "mefenamic",
              "celecoxib", "meloxicam"],
    "aspirin": ["acetylsalicylic", "aspirin"],
    "اسبرين": ["acetylsalicylic", "aspirin"],
    "macrolide": ["azithromycin", "clarithromycin", "erythromycin"],
    "quinolone": ["ciprofloxacin", "levofloxacin", "moxifloxacin", "ofloxacin"],
}  # fmt: skip


def _norm(text):
    return normalize_arabic((text or "").lower()).strip()


def allergy_terms(allergy_name):
    """Every lower-case term that an allergy entry should match."""
    name = _norm(allergy_name)
    terms = {name} if len(name) >= 3 else set()
    for keyword, members in DRUG_CLASSES.items():
        if _norm(keyword) in name:
            terms.update(members)
    return terms


def drug_haystack(item):
    parts = [item.drug_name]
    drug = getattr(item, "drug", None)
    if drug is not None:
        parts += [drug.name, drug.generic_name, *drug.aliases_ar]
    return " | ".join(_norm(p) for p in parts if p)


def warnings_for(rx):
    """[{key, kind, text}] — `key` is stable so an acknowledgement survives re-rendering."""
    items = list(rx.items.select_related("drug"))
    out = []
    for allergy in rx.patient.allergies.all():
        terms = allergy_terms(allergy.name)
        for item in items:
            hay = drug_haystack(item)
            if any(t and t in hay for t in terms):
                out.append({
                    "key": f"allergy:{allergy.pk}:{item.pk}",
                    "kind": "allergy",
                    "text": f"المريض عنده حساسية من {allergy.name} — و{item.drug_name} ممكن يكون منها",
                })  # fmt: skip
    seen = {}
    for item in items:
        ident = item.drug_id or re.sub(r"\s+", " ", _norm(item.drug_name))
        if ident in seen:
            out.append({
                "key": f"duplicate:{seen[ident]}:{item.pk}",
                "kind": "duplicate",
                "text": f"{item.drug_name} مكتوب مرتين",
            })  # fmt: skip
        else:
            seen[ident] = item.pk
    return out
