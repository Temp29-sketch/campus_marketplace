"""Absolute URLs for emails."""
from flask import current_app, url_for


def absolute_url(endpoint: str, **values) -> str:
    """Link for an email. Built from PUBLIC_BASE_URL when it's set (required in production),
    because the request's Host header can be forged, which would let an attacker aim a
    victim's verification link at their own domain."""
    base = current_app.config.get("PUBLIC_BASE_URL")
    if base:
        return base.rstrip("/") + url_for(endpoint, **values)
    return url_for(endpoint, _external=True, **values)
