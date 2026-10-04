"""Short Latin customer codes used in quotation numbers (RG-2026/09/24)."""

import re

CODE_RE = re.compile(r"^[A-Z]{2,5}$")

_AR = {
    "ا": "A", "أ": "A", "إ": "E", "آ": "A", "ء": "A", "ؤ": "O", "ئ": "E",
    "ب": "B", "ت": "T", "ث": "TH", "ج": "G", "ح": "H", "خ": "KH", "د": "D", "ذ": "Z",
    "ر": "R", "ز": "Z", "س": "S", "ش": "SH", "ص": "S", "ض": "D", "ط": "T", "ظ": "Z",
    "ع": "A", "غ": "GH", "ف": "F", "ق": "K", "ك": "K", "ل": "L", "م": "M", "ن": "N",
    "ه": "H", "ة": "A", "و": "O", "ي": "I", "ى": "A",
}  # fmt: skip


def _latin(word: str) -> str:
    out = []
    for ch in word:
        if ch.isascii():
            if ch.isalpha():
                out.append(ch.upper())
        else:
            out.append(_AR.get(ch, ""))
    return "".join(out)


def _words(name: str) -> list[str]:
    words = []
    for w in name.split():
        if w.startswith("ال") and len(w) > 3:
            w = w[2:]
        latin = _latin(w)
        if latin:
            words.append(latin)
    return words


def suggest_code(name: str, taken: set[str] | frozenset = frozenset()) -> str:
    """First two letters of the (transliterated) name, e.g. ريجينا → RI, Jaz Hotels → JA.
    Falls back to other letter pairs, then a third letter, to avoid codes already `taken`."""
    words = _words(name) or ["XX"]
    first = words[0]
    candidates = [first[:2]]
    if len(words) > 1:
        candidates.append(first[0] + words[1][0])
    candidates += [first[0] + c for c in first[2:]]
    candidates += [first[:2] + c for c in "".join(words)[2:]]
    candidates += [first[:2] + c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]
    for code in candidates:
        if len(code) >= 2 and CODE_RE.match(code) and code not in taken:
            return code
    return first[:2].ljust(2, "X")
