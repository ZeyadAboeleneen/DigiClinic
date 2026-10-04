import pytest

from apps.core.arabic import normalize_arabic


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("معلقة", "معلقه"),
        ("أكل", "اكل"),
        ("إسفنج", "اسفنج"),
        ("آيس كريم", "ايس كريم"),
        ("شاى", "شاي"),
        ("كـــوب", "كوب"),
        ("مُعَلَّقَة", "معلقه"),
        ("  كوب   ورق ", "كوب ورق"),
    ],
)
def test_normalize(raw, expected):
    assert normalize_arabic(raw) == expected


def test_search_equivalence():
    assert normalize_arabic("معلقه") == normalize_arabic("مَعلقة")
