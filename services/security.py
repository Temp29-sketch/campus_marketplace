"""Security headers on every response."""
from urllib.parse import urlparse

from flask import request


def _csp(app) -> str:
    img_sources = ["'self'", "data:"]
    if app.config["STORAGE_BACKEND"] == "s3":
        parsed = urlparse(app.config["S3_PUBLIC_BASE_URL"])
        if parsed.scheme and parsed.netloc:
            img_sources.append(f"{parsed.scheme}://{parsed.netloc}")
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",  # no inline scripts: our JS lives in /static
            "style-src 'self'",
            f"img-src {' '.join(img_sources)}",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def init_security(app) -> None:
    csp = _csp(app)
    production = app.config["ENV_NAME"] == "production"

    @app.after_request
    def add_security_headers(response):
        headers = response.headers
        headers.setdefault("Content-Security-Policy", csp)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if production and request.is_secure:
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
