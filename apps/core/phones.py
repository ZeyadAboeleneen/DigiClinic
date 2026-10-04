import phonenumbers
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

DEFAULT_REGION = "EG"


def to_e164(raw: str, region: str = DEFAULT_REGION) -> str:
    """Normalize a phone number to E.164 (`+201060777030`). Accepts local Egyptian forms,
    numbers missing their leading zero (as Excel stores them), Arabic-Indic digits and spaces/dashes."""
    if raw is None:
        raise ValidationError(_("رقم التليفون فاضي."))
    text = str(raw).strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
    if text.endswith(".0"):  # float coming from a spreadsheet cell
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit() or ch == "+")
    if region == "EG" and not digits.startswith(("+", "0")) and len(digits) == 10 and digits[0] == "1":
        digits = "0" + digits
    try:
        parsed = phonenumbers.parse(digits, region)
    except phonenumbers.NumberParseException as e:
        raise ValidationError(_("رقم التليفون ده مش صحيح: %(v)s"), params={"v": raw}) from e
    if not phonenumbers.is_valid_number(parsed):
        raise ValidationError(_("رقم التليفون ده مش صحيح: %(v)s"), params={"v": raw})
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def to_local(e164: str) -> str:
    """Display form: `01060777030` for Egyptian numbers, international format otherwise."""
    try:
        parsed = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return e164
    if parsed.country_code == 20:
        return "0" + str(parsed.national_number)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)


def mask(e164: str) -> str:
    """For logs: `+20106****030`."""
    return e164[:6] + "****" + e164[-3:] if len(e164) > 9 else "****"
