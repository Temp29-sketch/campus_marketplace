"""Signed, expiring tokens for school-email verification and vendor signup.

Nothing about a school email or matric number is stored in the database until
its link is clicked. That way nobody can "squat" someone else's school email or
matric number just by typing it into a form.
"""
from flask import current_app
from itsdangerous import BadSignature, URLSafeTimedSerializer

_VERIFY_SALT = "verify-school-email"
_SIGNUP_SALT = "vendor-signup"


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _load(salt: str, token: str):
    try:
        return _serializer(salt).loads(token, max_age=current_app.config["SCHOOL_EMAIL_TOKEN_MAX_AGE"])
    except BadSignature:  # SignatureExpired is a subclass
        return None


# --- existing buyer -> vendor upgrade ------------------------------------------
def make_school_email_token(user_id: int, school_email: str, matric_number: str) -> str:
    return _serializer(_VERIFY_SALT).dumps({"uid": user_id, "email": school_email, "matric": matric_number})


def read_school_email_token(token: str):
    """Return the payload dict, or None if the token is invalid or expired."""
    return _load(_VERIFY_SALT, token)


# --- sign up as a vendor from the start (no account exists yet) ------------------
def make_vendor_signup_token(name: str, school_email: str, matric_number: str, university_id: int) -> str:
    return _serializer(_SIGNUP_SALT).dumps(
        {"name": name, "email": school_email, "matric": matric_number, "uni": university_id}
    )


def read_vendor_signup_token(token: str):
    return _load(_SIGNUP_SALT, token)
