"""Phone numbers: stored as entered and in international (E.164) form for matching and messaging."""

import re

from django.conf import settings


def normalize_phone(raw, country_code=None):
    """'0300-1234567', '3001234567' (Excel drops the 0), '+92 300 1234567' -> '+923001234567'.

    Returns None when the value is not a plausible number. National numbers get the default
    country code (PHONE_COUNTRY_CODE, 92 for Pakistan).
    """
    if raw is None:
        return None
    if isinstance(raw, float) and raw.is_integer():
        raw = int(raw)
    text = str(raw).strip()
    if not text:
        return None
    cc = str(country_code or settings.PHONE_COUNTRY_CODE)
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits or re.search(r"[A-Za-z]", text):
        return None
    if plus:
        number = digits
    elif digits.startswith("00"):
        number = digits[2:]
    elif digits.startswith("0"):
        number = cc + digits[1:]
    elif len(digits) <= 10:
        number = cc + digits
    else:
        number = digits
    if not 8 <= len(number) <= 15:
        return None
    if number.startswith("92") and len(number) != 12:  # Pakistan: +92 and 10 digits
        return None
    return "+" + number
