"""Settings. Anything environment-specific comes from environment variables.

APP_ENV=production switches on strict start-up checks (ProductionConfig.validate):
the app refuses to boot with an unsafe or incomplete setup instead of failing later
in front of students. See .env.example for every variable.
"""
import os
import tempfile

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DEV_SECRET_KEY = "dev-only-change-me"
APP_NAME = "UniHub"


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///" + os.path.join(BASE_DIR, "unihub.db")
    if url.startswith("postgres://"):  # older hosts hand out this form; SQLAlchemy 2 wants postgresql://
        url = "postgresql://" + url[len("postgres://"):]
    return url


class Config:
    """Development defaults."""

    APP_NAME = APP_NAME
    PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")
    PAYMENTS_MODE = os.environ.get("PAYMENTS_MODE", "paystack").strip().lower()
    PAYSTACK_VERIFY_BANK = _bool("PAYSTACK_VERIFY_BANK", False)
    PAYSTACK_BASE_URL = os.environ.get("PAYSTACK_BASE_URL", "https://api.paystack.co").rstrip("/")
    PAYSTACK_CALLBACK_PATH = os.environ.get("PAYSTACK_CALLBACK_PATH", "/checkout/return")
    COMMISSION_PERCENT = int(os.environ.get("COMMISSION_PERCENT", "10"))
    MAX_VENDORS_PER_ORDER = int(os.environ.get("MAX_VENDORS_PER_ORDER", "5"))
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "support@unihub.example")
    APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Africa/Lagos")

    ENV_NAME = "development"
    SECRET_KEY = os.environ.get("SECRET_KEY") or DEV_SECRET_KEY
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}  # survive idle-connection drops

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_TIME_LIMIT = None  # a long listing form shouldn't expire after an hour

    # Where the site lives publicly. Emailed links are built from this (not from the
    # request's Host header, which an attacker can forge).
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    BEHIND_PROXY = _bool("BEHIND_PROXY", False)  # trust X-Forwarded-For/Proto from ONE proxy

    # Email verification links
    SCHOOL_EMAIL_TOKEN_MAX_AGE = 60 * 60 * 24  # 24 hours

    # Email: "console" prints messages (dev); "smtp" really sends them.
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")
    MAIL_FROM = os.environ.get("MAIL_FROM", "")  # e.g. "UniHub <no-reply@yourdomain.com>"
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)  # STARTTLS (port 587)
    SMTP_USE_SSL = _bool("SMTP_USE_SSL", False)  # implicit TLS (port 465)
    SMTP_TIMEOUT = 10  # seconds, so a slow mail server can't hang a request for long

    # Image uploads
    MAX_CONTENT_LENGTH = 40 * 1024 * 1024  # whole request (up to ~5 phone photos)
    MAX_IMAGE_BYTES = 10 * 1024 * 1024  # per photo
    MAX_IMAGE_PIXELS = 40_000_000  # reject decompression bombs
    MAX_IMAGES_PER_PRODUCT = 5
    IMAGE_MAX_SIDE = 1600  # stored full-size photo, longest side
    THUMB_MAX_SIDE = 480  # feed thumbnail, longest side

    # Photo storage: "local" (a folder) or "s3" (Cloudflare R2, AWS S3, Backblaze B2...)
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(BASE_DIR, "uploads")
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")  # R2: https://<account>.r2.cloudflarestorage.com
    S3_REGION = os.environ.get("S3_REGION", "auto")
    S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "")
    S3_PUBLIC_BASE_URL = os.environ.get("S3_PUBLIC_BASE_URL", "").rstrip("/")  # public URL photos load from

    # Rate limiting (Flask-Limiter). Use a shared store such as redis://... when running
    # more than one worker process, or each worker keeps its own separate counters.
    RATELIMIT_ENABLED = True
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True

    @classmethod
    def validate(cls) -> None:
        """Development skips deployment secrets but validates payment arithmetic limits."""
        if not 0 <= cls.COMMISSION_PERCENT <= 50:
            raise RuntimeError("COMMISSION_PERCENT must be an integer from 0 to 50.")
        if cls.MAX_VENDORS_PER_ORDER < 1:
            raise RuntimeError("MAX_VENDORS_PER_ORDER must be at least 1.")
        if cls.PAYMENTS_MODE not in ("paystack", "demo"):
            raise RuntimeError("PAYMENTS_MODE must be 'paystack' or 'demo'.")


class ProductionConfig(Config):
    ENV_NAME = "production"
    SESSION_COOKIE_SECURE = True  # cookies only travel over https
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    PREFERRED_URL_SCHEME = "https"
    BEHIND_PROXY = _bool("BEHIND_PROXY", True)  # hosts like Render/Fly/Railway sit behind one proxy
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "smtp")
    PAYSTACK_VERIFY_BANK = _bool("PAYSTACK_VERIFY_BANK", True)

    @classmethod
    def validate(cls) -> None:
        problems = []
        if not cls.PAYSTACK_SECRET_KEY.startswith("sk_live_"):
            problems.append("PAYSTACK_SECRET_KEY must be a live Paystack secret key.")
        if not 0 <= cls.COMMISSION_PERCENT <= 50:
            problems.append("COMMISSION_PERCENT must be an integer from 0 to 50.")
        if cls.MAX_VENDORS_PER_ORDER < 1:
            problems.append("MAX_VENDORS_PER_ORDER must be at least 1.")
        if cls.PAYMENTS_MODE == "demo":
            problems.append("PAYMENTS_MODE=demo is not allowed in production.")
        elif cls.PAYMENTS_MODE != "paystack":
            problems.append("PAYMENTS_MODE must be 'paystack' or 'demo'.")
        if cls.PAYMENTS_MODE == "paystack" and not cls.PAYSTACK_VERIFY_BANK:
            problems.append("PAYSTACK_VERIFY_BANK must be true when PAYMENTS_MODE=paystack in production.")
        if cls.SECRET_KEY == DEV_SECRET_KEY or len(cls.SECRET_KEY) < 32:
            problems.append(
                "SECRET_KEY must be a random value of at least 32 characters. "
                'Generate one: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if not cls.PUBLIC_BASE_URL.startswith("https://"):
            problems.append("PUBLIC_BASE_URL must be your site's https:// address (it is used in emailed links).")
        if cls.SQLALCHEMY_DATABASE_URI.startswith("sqlite") and not _bool("ALLOW_SQLITE"):
            problems.append(
                "DATABASE_URL must point to PostgreSQL. SQLite loses data on most hosts. "
                "(Set ALLOW_SQLITE=1 only if you run on one server with a persistent disk.)"
            )
        if cls.MAIL_BACKEND != "smtp":
            problems.append("MAIL_BACKEND must be 'smtp'. The console backend would print verification links into your logs.")
        else:
            for name in ("SMTP_HOST", "MAIL_FROM"):
                if not getattr(cls, name):
                    problems.append(f"{name} is required for sending email.")
        if cls.STORAGE_BACKEND not in ("local", "s3"):
            problems.append("STORAGE_BACKEND must be 'local' or 's3'.")
        elif cls.STORAGE_BACKEND == "s3":
            for name in ("S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_PUBLIC_BASE_URL"):
                if not getattr(cls, name):
                    problems.append(f"{name} is required when STORAGE_BACKEND=s3.")
        if problems:
            raise RuntimeError(
                "Unsafe or incomplete production settings:\n  - " + "\n  - ".join(problems)
            )


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False
    MAIL_BACKEND = "console"
    STORAGE_BACKEND = "local"
    UPLOAD_FOLDER = tempfile.mkdtemp(prefix="campus_market_uploads_")


def get_config():
    return ProductionConfig if os.environ.get("APP_ENV", "development").strip().lower() == "production" else Config
