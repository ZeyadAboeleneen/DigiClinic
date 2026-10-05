# ruff: noqa: E501  -- Arabic regex alternatives read best one per line
"""Pull prescription lines out of free conversation (voice dictation while talking to the patient).

The doctor may say: "إزيك يا حاج، متقلقش ده دور برد. هكتبلك اوجمنتين واحد جرام قرص كل اتناشر ساعة بعد الأكل لمدة
أسبوع، وخلي بالك من الأكل، وبنادول اكسترا عند اللزوم". We keep only:
    Augmentin 1g      → قرص كل 12 ساعة بعد الأكل · لمدة أسبوع
    Panadol Extra     → عند اللزوم
and drop everything else. Rule-based (no external AI service, nothing leaves the clinic beyond the browser's speech
recognition): drug names are found anywhere in the text with the catalog's fuzzy index (matching.py), then the dose,
frequency, timing and duration phrases right after each name are picked out with patterns. As always, nothing is saved
until the doctor picks/confirms each line (13 §13.4).

False positives are the main risk with ~25k drug names (an everyday word can resemble a brand), so a near match only
counts when a dose/frequency/duration follows it; an exact (≥ STRONG) match counts on its own; common Egyptian words
never start a name.
"""

import re
from dataclasses import dataclass, field

from .matching import MAX_PREFIX_WORDS, TOP_N, Candidate, _index, normalize, words_to_numbers

STRONG = 93  # this close to a catalog name → accept even without a dose after it
WEAK = 85  # minimum similarity; between WEAK and STRONG a dose/frequency/duration must follow
LOOKAHEAD = 22  # words after a drug name searched for its regimen (cut at the next drug name)
MIN_CHARS = 4  # one-word mentions shorter than this are ignored ("كل", "ده"...)

# Common words of a consultation that must never start a drug-name match.
STOPWORDS = set(
    normalize(w)
    for w in """
    انا انت انتي احنا هو هي هما ده دي دول كده كدا ازيك ازيكم اهلا اهلين سلام عليكم صباح مساء الخير النور
    يا حاج حاجه مدام استاذ دكتور يا باشا معلش متقلقش متخافش ماتقلقش ان شاء الله الحمد لله ربنا يشفيك سلامتك الف
    عشان علشان بس كمان برضه برضو لسه خلاص تمام ماشي طيب اوك حلو جدا خالص شويه شوية كتير قليل اكتر اقل
    هكتبلك هكتب هديلك هتاخد تاخد خد خدي خده خديه اشرب اشربي اكل كل كلي امشي نام نامي ارتاح ارتاحي
    علاج دوا دواء الدوا الروشته روشته التحاليل تحليل اشعه الاشعه الكشف الضغط السكر البرد الكحه الحراره
    في من على عن مع الي الى لحد لغايه بعد قبل لما لو اذا لان وبعدين بعدين يعني طبعا اصلا والله
    هو ايه ازاي ليه امتى فين مين كام ايوه لا مش مفيش فيه عندك عندي حاسس حاسه بيوجعك وجع الم
    الصبح بالليل النهارده بكره امبارح يوم يومين ايام اسبوع شهر ساعه ساعات مره مرتين
    """.split()
)

DOSE_UNITS = (
    r"قرص|اقراص|حبايه|حبايات|حبه|حبتين|كبسوله|كبسولات|معلقه|معالق|معلقتين|نقطه|نقط|نقطتين|بخه|بختين|بخات|"
    r"حقنه|حقن|امبول|امبوله|كيس|اكياس|ظرف|اظرف|مل|سم|لبوسه|لبوس|فوار|لزقه|دهان"
)
NUM = r"(?:\d+(?:[.,]\d+)?|½|¼|و½|نص|ونص|ربع)"
PATTERNS = [
    # dose: "قرص", "½ قرص", "2 معلقه", "10 مل"
    ("dose", re.compile(rf"(?:{NUM}\s*)?(?:{DOSE_UNITS})(?:\s*{NUM})?")),
    # frequency
    ("freq", re.compile(r"كل\s*\d+\s*(?:ساعات|ساعه|ايام|يوم)")),
    ("freq", re.compile(r"(?:مره|مرتين|\d+\s*مرات|\d+\s*مره)\s*(?:(?:واحده|1)\s*)?(?:يوميا|في اليوم|فاليوم|في الاسبوع|اسبوعيا|بالليل|الصبح)?")),
    ("freq", re.compile(r"عند اللزوم|عند الحاجه|قبل النوم|الصبح وبالليل|الصبح والليل")),
    # timing
    ("timing", re.compile(r"(?:قبل|بعد|مع)\s*(?:الاكل|الفطار|الفطور|الغدا|الغداء|العشا|العشاء|الوجبات|الاكل)|علي الريق|على الريق")),
    # duration
    ("duration", re.compile(r"(?:لمده|مده)\s*(?:\d+\s*(?:ايام|يوم|اسابيع|اسبوع|شهور|شهر)|يومين|يوم|اسبوعين|اسبوع|شهرين|شهر)")),
    ("duration", re.compile(r"\d+\s*(?:ايام|اسابيع|شهور)")),
    ("duration", re.compile(r"(?:اسبوعين|اسبوع|شهرين|شهر)\s*(?:كاملين|كامل)")),
]  # fmt: skip
# Display polish for normalized phrases.
PRETTY = [
    ("الاكل", "الأكل"), ("يوميا", "يوميًا"), ("اسبوعيا", "أسبوعيًا"), ("معلقه", "معلقة"), ("حبايه", "حباية"),
    ("كبسوله", "كبسولة"), ("نقطه", "نقطة"), ("حقنه", "حقنة"), ("لمده", "لمدة"), ("اسبوع", "أسبوع"), ("مره", "مرة"), ("مرة 1", "مرة واحدة"),
    ("ساعه", "ساعة"), ("علي الريق", "على الريق"), ("ايام", "أيام"), ("اقراص", "أقراص"), ("اكياس", "أكياس"),
]  # fmt: skip

_PUNCT = re.compile(r"[^\w½¼\s]", re.UNICODE)
# Name modifiers and strength units never start a drug name ("اكسترا" alone is not a drug; "جرام" is a strength).
MODIFIERS = {normalize(w) for w in "اكسترا بلس فورت فورتي ريتارد اس ار اكس ال".split()}
UNITS = {normalize(w) for w in "جرام جم جرامات مجم ملجم مللي ملي مل مليجرام ميكرو وحده وحدات".split()}
_CLITICS = ("وال", "بال", "فال", "لل", "ال", "و")  # "والاوجمنتين" → "اوجمنتين"


def _bare(token: str) -> str:
    for p in _CLITICS:
        if token.startswith(p) and len(token) - len(p) >= MIN_CHARS:
            return token[len(p) :]
    return token


def _is_number(token: str) -> bool:
    return token.replace(".", "").replace(",", "").isdigit()


@dataclass
class Extracted:
    text: str  # what was said for this drug (name + regimen words), for the doctor to see
    candidates: list = field(default_factory=list)  # Candidate (top 3)
    instructions: str = ""  # dose + frequency + timing
    duration: str = ""


def _whole_words(pattern: re.Pattern) -> re.Pattern:
    """Match only whole words: "مل" must not be found inside "كمل"."""
    return _WHOLE.setdefault(pattern.pattern, re.compile(rf"(?<!\w)(?:{pattern.pattern})(?!\w)"))


_WHOLE: dict[str, re.Pattern] = {}


def _pretty(phrase: str) -> str:
    for a, b in PRETTY:
        phrase = re.sub(rf"(?<!\w){a}(?!\w)", b, phrase)
    return phrase


def regimen(words: list[str]) -> tuple[str, str]:
    """(instructions, duration) found in these (normalized, numbers-converted) words, in spoken order."""
    text = " ".join(words)
    found = []  # (start, kind, phrase)
    taken = []
    for kind, pattern in PATTERNS:
        for m in _whole_words(pattern).finditer(text):
            span = m.span()
            if m.group().strip() and not any(span[0] < e and s < span[1] for s, e in taken):
                taken.append(span)
                found.append((span[0], kind, " ".join(m.group().split())))
    found.sort()
    instructions = " ".join(_pretty(p) for _s, kind, p in found if kind != "duration")
    durations = [_pretty(p) for _s, kind, p in found if kind == "duration"]
    duration = durations[0] if durations else ""
    if duration and not duration.startswith("لمدة"):
        duration = f"لمدة {duration}"
    return instructions, duration


def idx_names(idx, pks) -> dict:
    from .models import Drug

    return dict(Drug.objects.filter(pk__in=pks).values_list("pk", "name"))


def _number_in_names(number: str, hits, names) -> bool:
    return any(number in re.findall(r"\d+(?:\.\d+)?", names.get(pk, "")) for _s, pk in hits)


def extract(org, transcript: str) -> list[Extracted]:
    import numpy as np
    from rapidfuzz import fuzz, process

    raw = _PUNCT.sub(" ", words_to_numbers(transcript or ""))
    tokens = raw.split()
    norm = [normalize(t) for t in tokens]
    bare = [_bare(t) for t in norm]  # for matching names only (regimen patterns use `norm`)
    blocked = STOPWORDS | MODIFIERS | UNITS
    if not norm:
        return []
    idx = _index(org)

    # 1) every 1–3 word window that could be a drug name → its best catalog matches
    windows = {}  # (start, n) → list[(score, pk)]
    for n in range(1, MAX_PREFIX_WORDS + 1):
        starts = [
            i for i in range(len(norm) - n + 1)
            if norm[i] not in blocked and bare[i] not in blocked and not _is_number(norm[i])
            and not any(_is_number(t) or t in UNITS for t in norm[i + 1 : i + n])
            and len("".join(bare[i : i + n])) >= MIN_CHARS
        ]  # fmt: skip
        if not starts or not idx.heads[n]:
            continue
        queries = [" ".join(bare[i : i + n]) for i in starts]
        scores = process.cdist(queries, idx.heads[n], scorer=fuzz.ratio, score_cutoff=WEAK, dtype=np.uint8,
                               workers=-1)  # fmt: skip
        owners = idx.owners[n]
        for row, i in zip(scores, starts, strict=True):
            hits = np.nonzero(row)[0]
            if len(hits):
                best = {}
                for h in hits:
                    pk = owners[h]
                    best[pk] = max(best.get(pk, 0), int(row[h]))
                windows[(i, n)] = sorted(((s, pk) for pk, s in best.items()), reverse=True)

    # 2) choose non-overlapping mentions: strongest first, longer spoken names win ties; weak ones need a regimen
    chosen = []
    used = set()
    for (i, n), hits in sorted(windows.items(), key=lambda kv: (-kv[1][0][0], -kv[0][1], kv[0][0])):
        span = set(range(i, i + n))
        if span & used:
            continue
        top_score = hits[0][0]
        if top_score < STRONG:
            ins, dur = regimen(norm[i + n : i + n + LOOKAHEAD])
            if not (ins or dur):
                continue
        chosen.append((i, n, hits))
        used |= span
    chosen.sort()

    # 3) each mention's regimen = the words up to the next mention. A number (+ unit) or a modifier right after the
    #    name is its strength ("كونكور 5", "اوجمنتين 1 جرام", "بنادول اكسترا"): it stays with the name and picks the
    #    matching variant. The same drug said twice anywhere becomes one line.
    names = idx_names(idx, {pk for _i, _n, hits in chosen for _s, pk in hits})
    out: list[Extracted] = []
    by_drug: dict[int, Extracted] = {}
    for k, (i, n, hits) in enumerate(chosen):
        end = chosen[k + 1][0] if k + 1 < len(chosen) else len(norm)
        j = i + n
        strength = None
        while j < end and (norm[j] in MODIFIERS or norm[j] in UNITS or (_is_number(norm[j]) and strength is None)):
            if _is_number(norm[j]):
                if (j + 1 < end and norm[j + 1] in UNITS) or _number_in_names(norm[j], hits, names):
                    strength = norm[j]
                else:
                    break
            j += 1
        tail = norm[j : min(end, j + LOOKAHEAD)]
        ins, dur = regimen(tail)

        def rank(sp, strength=strength):
            score, pk = sp
            has = bool(strength) and strength in re.findall(r"\d+(?:\.\d+)?", names.get(pk, ""))
            return (-score, not has, -idx.usage[pk], idx.name_len[pk])

        ranked = sorted(hits, key=rank)[:TOP_N]
        spoken = " ".join(tokens[i:j])
        said = " ".join(tokens[i : min(end, j + LOOKAHEAD)])
        top = ranked[0][1]
        if top in by_drug:  # "اوجمنتين ... والاوجمنتين ده كمل عليه": fill what's missing, keep one line
            prev = by_drug[top]
            prev.instructions = prev.instructions or ins
            prev.duration = prev.duration or dur
            prev.text = f"{prev.text} … {said}"
            continue
        item = Extracted(text=said, candidates=[(pk, s, spoken) for s, pk in ranked], instructions=ins, duration=dur)
        out.append(item)
        by_drug[top] = item

    # 4) load the drugs for display
    from .models import Drug

    drugs = Drug.objects.in_bulk({pk for e in out for pk, _s, _sp in e.candidates})
    for e in out:
        e.candidates = [Candidate(drug=drugs[pk], score=s, spoken=sp) for pk, s, sp in e.candidates if pk in drugs]
    return [e for e in out if e.candidates]
