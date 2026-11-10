"""Turn a stored vendor phone number into contact links (pure Python).

Phone numbers are stored normalised as +234XXXXXXXXXX (see blueprints/vendor.py).
"""
import re
from urllib.parse import quote

_NG_MOBILE = re.compile(r"234[789]\d{9}")


def _digits(phone) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    return digits if _NG_MOBILE.fullmatch(digits) else None


def display_phone(phone) -> str:
    """+2348012345678 -> 0801 234 5678 (falls back to the raw value)."""
    digits = _digits(phone)
    if not digits:
        return phone or ""
    local = "0" + digits[3:]
    return f"{local[:4]} {local[4:7]} {local[7:]}"


def tel_url(phone) -> str | None:
    digits = _digits(phone)
    return f"tel:+{digits}" if digits else None


def whatsapp_url(phone, business_name, title, price_text) -> str | None:
    """wa.me link with a prefilled message, so the vendor knows which listing it's about."""
    digits = _digits(phone)
    if not digits:
        return None
    text = f'Hi {business_name}, I saw "{title}" ({price_text}) on Campus Market. Is it still available?'
    return f"https://wa.me/{digits}?text={quote(text)}"
