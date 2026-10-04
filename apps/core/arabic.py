import re

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي"})


def normalize_arabic(text: str) -> str:
    """Normalize Arabic for search: unify alef forms, ة→ه, ى→ي, strip tatweel and diacritics."""
    if not text:
        return ""
    text = _DIACRITICS.sub("", text).replace(_TATWEEL, "")
    return re.sub(r"\s+", " ", text.translate(_MAP)).strip()
