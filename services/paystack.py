"""Small, safe Paystack HTTP client implemented with the Python standard library."""
import json
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app


class PaystackError(RuntimeError):
    """A safe, user-displayable Paystack error."""


def request(method, path, payload=None, params=None):
    cfg = current_app.config
    secret = cfg.get("PAYSTACK_SECRET_KEY", "")
    if not secret:
        raise PaystackError("Payments are not configured.")
    url = cfg["PAYSTACK_BASE_URL"].rstrip("/") + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper(), headers={
        "Authorization": f"Bearer {secret}", "Content-Type": "application/json", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            result = json.loads(exc.read().decode("utf-8"))
            message = result.get("message") if isinstance(result, dict) else None
        except Exception:
            message = None
        raise PaystackError(message if isinstance(message, str) and len(message) < 240 else "Paystack could not complete the request.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        raise PaystackError("Paystack is temporarily unavailable. Please try again.") from None
    if not isinstance(result, dict) or result.get("status") is not True:
        message = result.get("message") if isinstance(result, dict) else None
        raise PaystackError(message if isinstance(message, str) and len(message) < 240 else "Paystack could not complete the request.")
    return result.get("data") or {}


def resolve_account(account_number, bank_code):
    return request("GET", "/bank/resolve", params={"account_number": account_number, "bank_code": bank_code})


def create_subaccount(application):
    return request("POST", "/subaccount", {
        "business_name": application.business_name,
        "settlement_bank": application.bank_code,
        "account_number": application.account_number,
        "percentage_charge": 0,
    })


def initialize_transaction(payload):
    return request("POST", "/transaction/initialize", payload)


def verify_transaction(reference):
    return request("GET", "/transaction/verify/" + urllib.parse.quote(reference, safe=""))


def list_banks():
    return request("GET", "/bank", params={"country": "nigeria", "perPage": 500})
