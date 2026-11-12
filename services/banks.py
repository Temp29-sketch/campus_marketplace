"""Nigerian bank list with an in-process Paystack cache and static fallback."""
import logging
import time

BANKS = [
    ("Access Bank", "044"), ("GTBank", "058"), ("First Bank", "011"), ("UBA", "033"),
    ("Zenith Bank", "057"), ("Fidelity Bank", "070"), ("Ecobank", "050"), ("Sterling Bank", "232"),
    ("FCMB", "214"), ("Union Bank", "032"), ("Wema Bank", "035"), ("Unity Bank", "215"),
    ("Stanbic IBTC", "221"), ("Polaris Bank", "076"), ("Providus Bank", "101"), ("Keystone Bank", "082"),
    ("Kuda", "50211"), ("OPay", "999992"), ("PalmPay", "999991"), ("Moniepoint", "50515"),
]
BANK_BY_CODE = dict((code, name) for name, code in BANKS)
_cached_banks = None
_cached_until = 0
log = logging.getLogger(__name__)


def get_banks():
    global _cached_banks, _cached_until
    try:
        from flask import current_app
        if not current_app.config.get("PAYSTACK_VERIFY_BANK"):
            return list(BANKS)
    except RuntimeError:
        pass
    now = time.monotonic()
    if _cached_banks is not None and now < _cached_until:
        return _cached_banks
    try:
        from flask import current_app
        if current_app.config.get("PAYSTACK_VERIFY_BANK") and current_app.config.get("PAYSTACK_SECRET_KEY"):
            from services.paystack import list_banks
            data = list_banks()
            rows = data.get("data", data) if isinstance(data, dict) else data
            result = sorted({(str(row["name"]), str(row["code"])) for row in rows if row.get("name") and row.get("code")})
            if result:
                _cached_banks, _cached_until = result, now + 86400
                return result
    except Exception:
        log.info("Paystack bank list unavailable; using local fallback")
    _cached_banks, _cached_until = list(BANKS), now + 3600
    return _cached_banks


def bank_name_for_code(code):
    return dict((value, name) for name, value in get_banks()).get(str(code))

def mask_account(value):
    if not value: return ""
    return "*" * max(0, len(value) - 4) + value[-4:]
