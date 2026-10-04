"""Dictated text → prescription lines (13 §13.3). Speech recognition is bad at English drug names inside Arabic speech,
so every spoken line is fuzzy-matched (rapidfuzz WRatio) against the catalog's trade/generic names and Arabic
aliases. Nothing here saves a line: the doctor always picks/confirms (13 §13.4)."""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from apps.core.arabic import normalize_arabic

from .models import Drug

THRESHOLD = 80
MAX_PREFIX_WORDS = 3
TOP_N = 3

# Spoken Egyptian/Modern Standard numbers → digits (keys are normalize_arabic()'d).
_NUMBER_WORDS = {
    "واحد": "1", "واحده": "1", "اتنين": "2", "اثنين": "2", "تلاته": "3", "ثلاثه": "3", "تلات": "3",
    "اربعه": "4", "اربع": "4", "خمسه": "5", "خمس": "5", "سته": "6", "ست": "6", "سبعه": "7", "سبع": "7",
    "تمانيه": "8", "ثمانيه": "8", "تمن": "8", "تسعه": "9", "تسع": "9", "عشره": "10", "عشر": "10",
    "حداشر": "11", "احدعشر": "11", "اتناشر": "12", "اثناعشر": "12", "خمستاشر": "15", "عشرين": "20",
    "تلاتين": "30", "ثلاثين": "30", "اربعين": "40", "خمسين": "50", "ميه": "100", "مية": "100", "ميتين": "200",
    "اربعميه": "400", "خمسميه": "500", "الف": "1000", "نص": "½", "نصف": "½", "ربع": "¼",
}  # fmt: skip
_COMMANDS = re.compile(r"\s*(?:سطر جديد|سطر جديده)\s*")
_SPLIT = re.compile(r"[،,\n؛;]+")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
# A spoken strength right after the drug name ("اوجمنتين واحد جرام") belongs to the drug, not to the instructions.
_LEADING_STRENGTH = re.compile(
    r"^[\d.,]+\s*(?:جرامات|جرام|جم|مللي|ملجم|مجم|ملي|مل|gm|mg|ml|iu|g)(?=\s|$)\s*", re.IGNORECASE
)


def normalize(text: str) -> str:
    return normalize_arabic((text or "").translate(_ARABIC_DIGITS).lower())


def words_to_numbers(text: str) -> str:
    """ "كل اتناشر ساعة" → "كل 12 ساعة"; also handles the و-prefix ("ونص" → "و½")."""
    out = []
    for word in (text or "").translate(_ARABIC_DIGITS).split():
        norm = normalize_arabic(word)
        if norm in _NUMBER_WORDS:
            out.append(_NUMBER_WORDS[norm])
        elif norm.startswith("و") and norm[1:] in _NUMBER_WORDS:
            out.append("و" + _NUMBER_WORDS[norm[1:]])
        else:
            out.append(word)
    return " ".join(out)


def split_lines(text: str) -> list[str]:
    """One prescription line per "،" / "سطر جديد" / newline (a long pause arrives from the browser as a new line)."""
    text = _COMMANDS.sub("\n", text or "")
    return [line.strip() for line in _SPLIT.split(text) if line.strip()]


@dataclass
class Candidate:
    drug: Drug
    score: float
    spoken: str  # the words that matched (learned as an alias when the doctor picks this drug)


@dataclass
class LineMatch:
    text: str
    candidates: list = field(default_factory=list)
    instructions: str = ""  # the rest of the line, numbers converted


def _names(drug):
    names = [normalize(drug.name), normalize(drug.generic_name)]
    names += [normalize(a) for a in drug.aliases_ar or []]
    return [n for n in names if n]


def _strip_strength(instructions: str, drug: Drug) -> str:
    """Drop "1 جرام" always, and a bare number only when it's the drug's own strength ("بروفين 400")."""
    text = _LEADING_STRENGTH.sub("", instructions).strip()
    first, _sep, rest = text.partition(" ")
    if first.replace(".", "").isdigit() and first in re.findall(r"[\d.]+", f"{drug.name} {drug.strength}"):
        return rest.strip()
    return text


def match_line(drugs, line: str) -> LineMatch:
    words = line.split()
    result = LineMatch(text=line)
    if not words:
        return result
    best = {}  # drug.pk → (score, prefix_len, Candidate)
    for n in range(1, min(MAX_PREFIX_WORDS, len(words)) + 1):
        prefix = normalize(" ".join(words[:n]))
        for drug in drugs:
            score = max(fuzz.WRatio(prefix, name) for name in _names(drug))
            if score < THRESHOLD:
                continue
            key = (score, n)
            if drug.pk not in best or key > best[drug.pk][:2]:
                best[drug.pk] = (score, n, Candidate(drug=drug, score=round(score, 1), spoken=" ".join(words[:n])))
    ranked = sorted(best.values(), key=lambda t: (t[0], t[1], t[2].drug.usage_count), reverse=True)
    result.candidates = [c for _s, _n, c in ranked[:TOP_N]]
    rest = words[ranked[0][1] :] if ranked else words
    result.instructions = _strip_strength(words_to_numbers(" ".join(rest)), ranked[0][2].drug) if ranked else ""
    return result


def match_text(org, text: str) -> list[LineMatch]:
    drugs = list(Drug.objects.filter(organization=org, is_active=True))
    return [match_line(drugs, line) for line in split_lines(text)]


def learn_alias(drug: Drug, spoken: str) -> bool:
    """The first time the doctor picks a drug for a spoken form, remember it (13 §13.3 step 5)."""
    spoken = normalize(spoken)
    if not spoken or len(spoken) < 3 or not re.search(r"[؀-ۿ]", spoken):
        return False
    if spoken in {normalize(a) for a in drug.aliases_ar or []}:
        return False
    drug.aliases_ar = [*(drug.aliases_ar or []), spoken]
    drug.save(update_fields=["aliases_ar", "updated_at"])
    return True
