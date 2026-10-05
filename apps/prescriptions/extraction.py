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
WEAK = 85
WEAK_LOOKAHEAD = 8  # a weak (not near-exact) name only counts if a dose/frequency/duration follows this closely
FILLERS = {
    "بس",
    "يعني",
    "كده",
    "كدا",
    "ده",
    "دي",
    "وده",
    "ودي",
    "دا",
}  # skipped inside a regimen  # minimum similarity; between WEAK and STRONG a dose/frequency/duration must follow
LOOKAHEAD = 45  # words after a drug name searched for its regimen (cut at the next drug name)
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
    مايه مايّه ميه مية مياه المايه الميه سوايل عصير عصاير شاي قهوه لبن اكل الاكل حاجه حاجات
    اللي اللى دا دى ودي وده تيجي تيجى تاخديه تاخدها تاخده تستخدمي تستخدميه تستخدمه استخدم او ولا
    الصبح بالليل النهارده بكره امبارح يوم يومين ايام اسبوع شهر ساعه ساعات مره مرتين
    """.split()
)

DOSE_UNITS = (
    r"قرص|اقراص|حبايه|حبايات|حبه|حبتين|كبسوله|كبسولات|معلقه|معالق|معلقتين|نقطه|نقط|نقطتين|بخه|بختين|بخات|"
    r"حقنه|حقن|امبول|امبوله|كيس|اكياس|ظرف|اظرف|مل|سم|سي سي|cc|ml|وحده|وحدات|لبوسه|لبوس|فوار|لزقه|دهان|طبقه رقيقه"
)
NUM = r"(?:\d+(?:[.,]\d+)?|½|¼|و½|نص|ونص|ربع)"
PATTERNS = [
    # dose: "قرص", "½ قرص", "2 معلقه", "10 مل"
    ("dose", re.compile(rf"(?:{NUM}\s*)?(?:{DOSE_UNITS})(?:\s*{NUM})?")),
    # frequency
    ("freq", re.compile(r"كل\s*\d+\s*(?:ساعات|ساعه|ايام|يوم)")),
    ("freq", re.compile(r"(?:مره|مرتين|\d+\s*مرات)?\s*كل يوم(?:\s*بالليل|\s*الصبح)?|يوم بعد يوم|يوم اه ويوم لا")),
    ("freq", re.compile(r"(?:مره|مرتين|\d+\s*مرات|\d+\s*مره)\s*(?:(?:واحده|1)\s*)?(?:يوميا|في اليوم|فاليوم|في الاسبوع|اسبوعيا|بالليل|الصبح)?")),
    ("freq", re.compile(r"طول اليوم|بعد (?:الشاور|الحمام|الاستحمام|الدش)")),
    ("freq", re.compile(r"عند اللزوم|عند الحاجه|قبل النوم|الصبح وبالليل|الصبح والليل")),
    # timing
    ("timing", re.compile(r"(?:قبل|بعد|مع)\s*(?:الاكل|الفطار|الفطور|الغدا|الغداء|العشا|العشاء|الوجبات|الاكل)(?:\s*ب(?:نص|ربع)\s*ساعه|\s*ب\d+\s*دقيقه)?|علي الريق|على الريق")),
    # duration
    ("duration", re.compile(r"(?:لمده|مده)\s*(?:\d+\s*(?:ايام|يوم|اسابيع|اسبوع|شهور|شهر)|يومين|يوم|اسبوعين|اسبوع|شهرين|شهر)")),
    ("duration", re.compile(r"\d+\s*(?:ايام|اسابيع|شهور)")),
    # "لمدة" is often misheard ("ولماضه", "لمدت"): any short ل-word before a duration counts
    ("duration", re.compile(r"و?ل\w{1,4}\s*(?:اسبوعين|اسبوع|شهرين|شهر|يومين)")),
    ("duration", re.compile(r"(?:اسبوعين|شهرين|يومين|تلت اسابيع)")),
    ("duration", re.compile(r"(?:اسبوعين|اسبوع|شهرين|شهر)\s*(?:كاملين|كامل)")),
    ("duration", re.compile(r"باستمرار|علي طول|على طول|بشكل مستمر|مدي الحياه|لحد ما (?:العلبه |الدوا |الكورس )?(?:تخلص|يخلص)|لحد الزياره الجايه|لحد ما نشوفك")),
]  # fmt: skip
# Display polish for normalized phrases.
PRETTY = [
    ("الاكل", "الأكل"), ("يوميا", "يوميًا"), ("اسبوعيا", "أسبوعيًا"), ("معلقه", "معلقة"), ("حبايه", "حباية"),
    ("كبسوله", "كبسولة"), ("نقطه", "نقطة"), ("حقنه", "حقنة"), ("لمده", "لمدة"), ("اسبوع", "أسبوع"), ("مره", "مرة"), ("مرة 1", "مرة واحدة"),
    ("ساعه", "ساعة"), ("علي الريق", "على الريق"), ("ايام", "أيام"), ("اقراص", "أقراص"), ("اكياس", "أكياس"),
]  # fmt: skip

_PUNCT = re.compile(r"[^\w½¼\s]", re.UNICODE)

# English product names said in Arabic ("صابونه اي ون" = A.ONE SOAP): letters, number words and common name words.
SPOKEN_EN = {normalize(k): v for k, v in {
    "اي": "a", "ايه": "a", "بي": "b", "سي": "c", "دي": "d", "اف": "f", "جي": "g", "اتش": "h", "جاي": "j",
    "كي": "k", "ال": "l", "ام": "m", "ان": "n", "او": "o", "كيو": "q", "ار": "r", "اس": "s", "تي": "t", "يو": "u",
    "دبليو": "w", "اكس": "x", "واي": "y", "زد": "z", "زي": "z",
    "ون": "one", "وان": "one", "تو": "two", "تري": "three", "ثري": "three", "فور": "four", "فايف": "five",
    "بلس": "plus", "اكسترا": "extra", "فورت": "forte", "فورتي": "forte", "ماكس": "max", "كيدز": "kids",
    "بيبي": "baby", "جونيور": "junior", "ميني": "mini", "دوبل": "double", "سوبر": "super", "نايت": "night",
    "داي": "day", "كولد": "cold", "فلو": "flu", "كير": "care", "هير": "hair", "سكين": "skin",
}.items()}  # fmt: skip
# Letters Arabic speech recognition confuses with each other → one "sound" letter.
_SOUNDS = str.maketrans("رصطضذظقحثغ", "لستدززكهسخ")
SOUND_PENALTY = 7  # a 100% sound match = 93 (still "strong"), a weaker one needs a dose after it


def _sound(text: str) -> str:
    return text.translate(_SOUNDS)


def _sound_heads(idx, n: int) -> list[str]:
    cache = idx.__dict__.setdefault("sound_heads", {})
    if n not in cache:
        cache[n] = [_sound(h) for h in idx.heads[n]]
    return cache[n]


VARIANT_WORDS = {"plus", "extra", "forte", "xr", "sr", "cr", "max", "duo", "co", "mr", "xl", "la"}
DIGIT_WORDS = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five"}


def _glue(words: list[str]) -> str:
    """["a", "1", "d"] → "a1 d": a letter followed by a number is written together in names (A1 CREAM)."""
    out: list[str] = []
    for w in words:
        if out and w.isdigit() and out[-1].isalpha() and len(out[-1]) == 1:
            out[-1] += w
        else:
            out.append(w)
    return " ".join(out)


# Product type words → the English word in catalog names; said next to a name they pick the right product.
TYPES = {normalize(k): v for k, v in {
    "صابونه": "soap", "صابون": "soap", "كريم": "cream", "شامبو": "shampoo", "لوشن": "lotion", "جل": "gel",
    "مرهم": "oint", "شراب": "syrup", "سيرب": "syrup", "نقط": "drops", "نقطه": "drops", "بخاخ": "spray",
    "لبوس": "supp", "حقن": "amp", "حقنه": "amp", "فوار": "eff", "بودره": "powder", "غسول": "wash", "اقراص": "tab",
    "كبسول": "cap", "كبسولات": "cap", "لبوسه": "supp", "معجون": "paste", "سبراي": "spray",
}.items()}  # fmt: skip
# Name modifiers and strength units never start a drug name ("اكسترا" alone is not a drug; "جرام" is a strength).
MODIFIERS = {normalize(w) for w in "اكسترا بلس فورت فورتي ريتارد اس ار اكس ال".split()}
UNITS = {
    normalize(w)
    for w in "جرام جم جرامات مجم ملجم مللي ملي مل مليجرام ميليجرام ميلي ميكرو ميكروجرام وحده وحدات mg gm g ml mcg iu".split()
}
COUNTED = {normalize(w) for w in "مل ml مللي ملي سم cc نقطه نقط وحده وحدات".split()}  # number + these = a dose
# Said right before a name, these mean the drug is being stopped / not prescribed ("بلاش البنادول", "وقفي الكونكور").
NEGATIONS = {
    normalize(w)
    for w in """
    مش بلاش وقف وقفي اوقف اوقفي بطل بطلي متاخدش ماتاخدش متاخديش ماتاخديش متخدش شيل شيلي الغي لغي بدل
    ممنوع متستخدميش متستخدمش متكملش متكمليش كفايه
""".split()
}
# Said right after a name: the doctor corrects themself ("كونكور... لا قصدي كونكور بلس") → the first name is dropped.
CORRECTIONS = {normalize(w) for w in "قصدي اقصد غلط لالا".split()}
_CLITICS = ("وال", "بال", "فال", "لل", "ال", "و")  # "والاوجمنتين" → "اوجمنتين"
_SOFT_CLITICS = ("ب", "ف", "ك", "و", "ل")  # tried as well, never instead ("بنادول" starts with ب)
_GLUED = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")  # "كونكور5" / "500mg" → split
_COMPOUND = re.compile(r"\b([1-9]00)\s+و([1-9]\d?)\b")  # "200 و50" (ميتين وخمسين) → 250


def _prepare(transcript: str) -> str:
    text = _PUNCT.sub(" ", words_to_numbers(transcript or ""))
    text = _COMPOUND.sub(lambda m: str(int(m[1]) + int(m[2])), text)
    return _GLUED.sub(" ", text)


def _variants(token: str) -> set[str]:
    """Spellings of one spoken word to try against the catalog: as heard, without و/ال, without a soft prefix."""
    out = {token, _bare(token)}
    for p in _SOFT_CLITICS:
        if token.startswith(p) and len(token) - len(p) >= MIN_CHARS:
            out.add(token[len(p) :])
    return out


# Arabic speech vs an English catalog name with no usable Arabic alias: compare consonant "skeletons" in one
# alphabet ("اموكسيسيلين" ≈ "amoxicillin"). Weakest evidence of all, so it only counts with a dose after it.
_EN_DIGRAPHS = [("ph", "f"), ("th", "t"), ("sh", "S"), ("ch", "k"), ("ck", "k"), ("qu", "k"), ("x", "ks"),
                ("ce", "se"), ("ci", "si"), ("cy", "sy")]  # fmt: skip
_EN_SKEL = str.maketrans({"p": "b", "c": "k", "q": "k", "v": "f", "z": "s", "j": "g", **dict.fromkeys("aeiouyw", "")})
_AR_SKEL = str.maketrans({
    "ب": "b", "پ": "b", "ت": "t", "ط": "t", "ث": "s", "س": "s", "ص": "s", "ز": "s", "ذ": "s", "ظ": "s", "د": "d",
    "ض": "d", "ك": "k", "ق": "k", "ج": "g", "غ": "g", "ف": "f", "ڤ": "f", "ل": "l", "ر": "r", "م": "m", "ن": "n",
    "ه": "h", "ح": "h", "خ": "k", "ش": "S", **dict.fromkeys("اويىءئؤعة", ""),
})  # fmt: skip
SKELETON_MIN = 5  # consonants; shorter skeletons collide with too many names
SKELETON_CUTOFF = 92
SKELETON_PENALTY = 8  # → at most 92: never "strong" on its own


def _skeleton_en(word: str) -> str:
    word = re.sub(r"[^a-z]", "", word.lower())
    for a, b in _EN_DIGRAPHS:
        word = word.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", word.translate(_EN_SKEL))


def _skeleton_ar(word: str) -> str:
    return re.sub(r"(.)\1+", r"\1", word.translate(_AR_SKEL))


def _skeleton_heads(idx) -> tuple[list[str], list[int]]:
    """Skeletons of the first word of every English name in the index (cached on the index)."""
    if "skeletons" not in idx.__dict__:
        heads, owners = [], []
        for h, pk in zip(idx.heads[1], idx.owners[1], strict=True):
            if h.isascii():
                sk = _skeleton_en(h)
                if len(sk) >= SKELETON_MIN:
                    heads.append(sk)
                    owners.append(pk)
        idx.__dict__["skeletons"] = (heads, owners)
    return idx.__dict__["skeletons"]


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


def _join(phrases: list[str]) -> str:
    """Said twice ("مرة كل يوم ... مرة كل يوم") → written once; a phrase inside a longer one is dropped too."""
    kept: list[str] = []
    for p in phrases:
        if any(p in k for k in kept):
            continue
        kept = [k for k in kept if k not in p] + [p]
    return " ".join(sorted(kept, key=phrases.index))


def regimen(words: list[str]) -> tuple[str, str]:
    """(instructions, duration) found in these (normalized, numbers-converted) words, in spoken order."""
    text = " ".join(w for w in words if w not in FILLERS)
    found = []  # (start, kind, phrase)
    taken = []
    for kind, pattern in PATTERNS:
        for m in _whole_words(pattern).finditer(text):
            span = m.span()
            if m.group().strip() and not any(span[0] < e and s < span[1] for s, e in taken):
                taken.append(span)
                found.append((span[0], kind, " ".join(m.group().split())))
    found.sort()
    instructions = _join([_pretty(p) for _s, kind, p in found if kind != "duration"])
    durations = [_pretty(p) for _s, kind, p in found if kind == "duration"]
    duration = durations[0] if durations else ""
    # a misheard "لمدة" ("ولماضه") → shown as "لمدة"; "باستمرار" / "لحد ما العلبة تخلص" stay as said
    duration = re.sub(r"^و?ل\w{1,4}\s+(?=(?:اسبوع|شهر|يوم))", "", duration)
    if duration and re.match(r"\d|اسبوع|أسبوع|شهر|يوم", duration):
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

    tokens = _prepare(transcript).split()
    norm = [normalize(t) for t in tokens]
    bare = [_bare(t) for t in norm]  # for matching names only (regimen patterns use `norm`)
    blocked = STOPWORDS | MODIFIERS | UNITS | set(TYPES)
    if not norm:
        return []
    idx = _index(org)

    # 1) every 1–3 word window that could be a drug name → its best catalog matches
    windows = {}  # (start, n) → list[(score, pk)]
    for n in range(1, MAX_PREFIX_WORDS + 1):
        starts = [
            i for i in range(len(norm) - n + 1)
            if norm[i] not in blocked and bare[i] not in blocked and not _is_number(norm[i])
            and not any(t in STOPWORDS or t in UNITS for t in norm[i + 1 : i + n])
            and not any(_is_number(t) or t in UNITS for t in norm[i + 1 : i + n])
            and len("".join(bare[i : i + n])) >= MIN_CHARS
        ]  # fmt: skip
        if not idx.heads[n]:
            continue
        owners = idx.owners[n]
        # each window in its spellings: the first word with/without a prefix (و، ال، ب، ف...)
        qs = [(i, " ".join([v, *bare[i + 1 : i + n]])) for i in starts for v in _variants(norm[i])]
        qs = [(i, q) for i, q in qs if len(q.replace(" ", "")) >= MIN_CHARS]
        queries = [q for _i, q in qs]
        scores = process.cdist(queries, idx.heads[n], scorer=fuzz.ratio, score_cutoff=WEAK, dtype=np.uint8,
                               workers=-1) if queries else []  # fmt: skip
        # letters speech recognition mixes up (الكول/الكور): a match on the "sound" scores a bit lower than an exact one
        phon = process.cdist([_sound(q) for q in queries], _sound_heads(idx, n), scorer=fuzz.ratio,
                             score_cutoff=STRONG, dtype=np.uint8, workers=-1) if queries else []  # fmt: skip
        found: dict[int, dict] = {}
        for row, prow, (i, _q) in zip(scores, phon, qs, strict=True):
            best = found.setdefault(i, {})
            for h in np.nonzero(row)[0]:
                best[owners[h]] = max(best.get(owners[h], 0), int(row[h]))
            for h in np.nonzero(prow)[0]:
                best[owners[h]] = max(best.get(owners[h], 0), int(prow[h]) - SOUND_PENALTY)
        if n == 2 and idx.heads[1]:  # a name heard as two words ("اوج منتين") vs the one-word name
            joined = [(i, bare[i] + bare[i + 1]) for i in starts]
            rows = process.cdist([q for _i, q in joined], idx.heads[1], scorer=fuzz.ratio, score_cutoff=STRONG,
                                 dtype=np.uint8, workers=-1) if joined else []  # fmt: skip
            for row, (i, _q) in zip(rows, joined, strict=True):
                best = found.setdefault(i, {})
                for h in np.nonzero(row)[0]:
                    pk = idx.owners[1][h]
                    best[pk] = max(best.get(pk, 0), int(row[h]) - 2)
        if n == 1:  # Arabic speech vs English-only names, by consonant skeleton
            sk_heads, sk_owners = _skeleton_heads(idx)
            sk = [(i, _skeleton_ar(v)) for i in starts for v in _variants(norm[i]) if not v.isascii()]
            sk = [(i, q) for i, q in sk if len(q) >= SKELETON_MIN]
            rows = process.cdist([q for _i, q in sk], sk_heads, scorer=fuzz.ratio, score_cutoff=SKELETON_CUTOFF,
                                 dtype=np.uint8, workers=-1) if sk and sk_heads else []  # fmt: skip
            for row, (i, _q) in zip(rows, sk, strict=True):
                best = found.setdefault(i, {})
                for h in np.nonzero(row)[0]:
                    pk = sk_owners[h]
                    best[pk] = max(best.get(pk, 0), int(row[h]) - SKELETON_PENALTY)
        for i, best in found.items():
            if best:
                windows[(i, n)] = sorted(((s, pk) for pk, s in best.items()), reverse=True)

        # the same window heard as spoken English ("اي ون" / "اي 1" → "a one" / "a1"), when every word is a known
        # spoken word; letters alone ("دي او") are too common in speech to count
        latin = []  # (start, query)
        for i in range(len(norm) - n + 1):
            ws = norm[i : i + n]
            if not all(w in SPOKEN_EN or _is_number(w) for w in ws) or _is_number(ws[0]):
                continue
            words = [SPOKEN_EN.get(w, w) for w in ws]
            for q in {" ".join(DIGIT_WORDS.get(w, w) for w in words), _glue(words)}:
                if any(len(w) >= 3 or (re.search(r"\d", w) and re.search(r"[a-z]", w)) for w in q.split()):
                    latin.append((i, q))
        for i, q in latin:
            m = len(q.split())
            if not idx.heads.get(m):
                continue
            row = process.cdist([q], idx.heads[m], scorer=fuzz.ratio, score_cutoff=STRONG, dtype=np.uint8,
                                workers=-1)[0]  # fmt: skip
            best = dict((pk, sc) for sc, pk in windows.get((i, n), []))
            for h in np.nonzero(row)[0]:
                pk = idx.owners[m][h]
                best[pk] = max(best.get(pk, 0), int(row[h]))
            if best:
                windows[(i, n)] = sorted(((sc, pk) for pk, sc in best.items()), reverse=True)

    # 2) choose non-overlapping mentions: strongest first, longer spoken names win ties; weak ones need a regimen
    chosen = []
    used = set()
    for (i, n), hits in sorted(windows.items(), key=lambda kv: (-kv[1][0][0], -kv[0][1], kv[0][0])):
        span = set(range(i, i + n))
        if span & used:
            continue
        top_score = hits[0][0]
        if top_score < STRONG:
            ins, dur = regimen(norm[i + n : i + n + WEAK_LOOKAHEAD])
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
        if any(w in NEGATIONS for w in norm[max(0, i - 2) : i]):  # "بلاش البنادول": stopped, not prescribed
            continue
        if k + 1 < len(chosen) and any(w in CORRECTIONS for w in norm[i + n : min(end, i + n + 4)]):
            continue  # "كونكور... لا قصدي كونكور بلس": only the corrected name counts
        j = i + n
        strength = None
        while j < end and (norm[j] in MODIFIERS or norm[j] in UNITS or (_is_number(norm[j]) and strength is None)):
            if _is_number(norm[j]):
                if j + 1 < end and norm[j + 1] in COUNTED:
                    break  # "شراب 5 مل" is the dose, not the strength
                big = float(norm[j].replace(",", ".")) >= 20  # "بروفين 250 معلقة": 250 is a strength, not a dose
                if (j + 1 < end and norm[j + 1] in UNITS) or big or _number_in_names(norm[j], hits, names):
                    strength = norm[j]
                else:
                    break
            j += 1
        tail = norm[j : min(end, j + LOOKAHEAD)]
        ins, dur = regimen(tail)
        if k == 0 and not (ins and dur):  # "خدي مرتين في اليوم من البروفين": dose said before the first name
            b_ins, b_dur = regimen(norm[max(0, i - 12) : i])
            ins, dur = ins or b_ins, dur or b_dur

        said_en = {SPOKEN_EN.get(w, w) for w in norm[i:j]}  # "الكور" alone → ALKOR, not ALKOR PLUS
        kinds = {TYPES[w] for w in norm[max(0, i - 2) : i] + norm[j : j + 3] if w in TYPES}  # "صابونه اي ون"

        def rank(sp, strength=strength, kinds=kinds, said_en=said_en):
            score, pk = sp
            name = names.get(pk, "").lower()
            has = bool(strength) and strength in re.findall(r"\d+(?:\.\d+)?", name)
            unsaid = any(w in VARIANT_WORDS and w not in said_en for w in re.split(r"[^a-z]+", name))
            return (-score, not any(k in name for k in kinds), not has, unsaid, -idx.usage[pk], idx.name_len[pk])

        ranked = sorted(hits, key=rank)[:TOP_N]
        spoken = " ".join(tokens[i:j])
        said = " ".join(tokens[i : min(end, j + LOOKAHEAD)])
        top = ranked[0][1]
        if top in by_drug:  # "اوجمنتين ... والاوجمنتين ده كمل عليه": fill what's missing, keep one line
            prev = by_drug[top]
            prev.instructions = _join([p for p in (prev.instructions, ins) if p])
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
