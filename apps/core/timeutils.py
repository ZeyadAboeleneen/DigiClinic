"""Cairo-local scheduling times. Stored in the DB as UTC (USE_TZ=True); computed here in
Africa/Cairo via zoneinfo, so DST transitions are handled automatically (no manual offset math).
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

CAIRO = ZoneInfo("Africa/Cairo")

# Egyptian week order: 0=Saturday ... 6=Friday.
WEEKDAY_LABELS = ("السبت", "الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة")


def weekday_egypt(d: date) -> int:
    """Python's date.weekday() is Monday=0..Sunday=6; convert to the Egyptian Saturday=0..Friday=6 order."""
    return (d.weekday() + 2) % 7


def local_dt(d: date, t: time, tz: ZoneInfo = CAIRO) -> datetime:
    """A timezone-aware datetime for `d`+`t` in `tz`, DST-correct for that specific date."""
    return datetime.combine(d, t, tzinfo=tz)


def to_local(dt: datetime, tz: ZoneInfo = CAIRO) -> datetime:
    return dt.astimezone(tz)
