"""Checks that need no database: production config, email, photo storage, links, headers.

Run:  python test_services.py
"""
import io
import os
import smtplib

from flask import Flask
from PIL import Image
from werkzeug.datastructures import FileStorage

import config
import services.mail as mail
from config import Config, ProductionConfig, TestConfig
from services.images import ImageError
from services.links import absolute_url
from services.mail import EmailError, notify, send_email
from services.security import init_security
from services.storage import delete_image_files, image_url, save_image, thumb_key, thumb_url


def make_app(config_class, name="t"):
    app = Flask(name)
    app.config.from_object(config_class)
    app.add_url_rule("/media/<path:filename>", "media", lambda filename: "")
    app.add_url_rule("/x/<token>", "x", lambda token: "")
    return app


def png_upload(name="a.png", size=(400, 300)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "red").save(buffer, "PNG")
    return FileStorage(stream=io.BytesIO(buffer.getvalue()), filename=name)


# 1. Production config refuses unsafe or incomplete settings -------------------------------
class Good(ProductionConfig):
    SECRET_KEY = "k" * 40
    PUBLIC_BASE_URL = "https://campus.example"
    SQLALCHEMY_DATABASE_URI = "postgresql://u:p@h/db"
    MAIL_BACKEND = "smtp"
    SMTP_HOST = "smtp.example"
    MAIL_FROM = "UniHub <no-reply@campus.example>"
    STORAGE_BACKEND = "s3"
    S3_BUCKET = "bucket"
    S3_ACCESS_KEY_ID = "id"
    S3_SECRET_ACCESS_KEY = "secret"
    S3_PUBLIC_BASE_URL = "https://cdn.example"


Good.validate()
Config.validate()  # development accepts anything
bad_settings = {
    "SECRET_KEY": ("dev-only-change-me", "SECRET_KEY"),
    "SECRET_KEY ": ("too-short", "SECRET_KEY"),
    "PUBLIC_BASE_URL": ("http://campus.example", "PUBLIC_BASE_URL"),
    "PUBLIC_BASE_URL ": ("", "PUBLIC_BASE_URL"),
    "SQLALCHEMY_DATABASE_URI": ("sqlite:///x.db", "DATABASE_URL"),
    "MAIL_BACKEND": ("console", "MAIL_BACKEND"),
    "SMTP_HOST": ("", "SMTP_HOST"),
    "MAIL_FROM": ("", "MAIL_FROM"),
    "STORAGE_BACKEND": ("ftp", "STORAGE_BACKEND"),
    "S3_BUCKET": ("", "S3_BUCKET"),
    "S3_PUBLIC_BASE_URL": ("", "S3_PUBLIC_BASE_URL"),
}
for attr, (value, expected) in bad_settings.items():
    Bad = type("Bad", (Good,), {attr.strip(): value})
    try:
        Bad.validate()
    except RuntimeError as exc:
        assert expected in str(exc), (attr, str(exc))
    else:
        raise SystemExit(f"FAIL: production accepted bad {attr.strip()}={value!r}")
os.environ["ALLOW_SQLITE"] = "1"
type("Sqlite", (Good,), {"SQLALCHEMY_DATABASE_URI": "sqlite:///x.db"}).validate()
del os.environ["ALLOW_SQLITE"]

os.environ["DATABASE_URL"] = "postgres://u:p@h:5432/db"
assert config._database_url() == "postgresql://u:p@h:5432/db"
del os.environ["DATABASE_URL"]
assert config._database_url().startswith("sqlite:///")
os.environ["APP_ENV"] = "Production"
assert config.get_config() is ProductionConfig
os.environ["APP_ENV"] = "development"
assert config.get_config() is Config
del os.environ["APP_ENV"]
assert ProductionConfig.SESSION_COOKIE_SECURE and ProductionConfig.PREFERRED_URL_SCHEME == "https"
assert TestConfig.RATELIMIT_ENABLED is False
print("production config ok")


# 2. Email: SMTP delivery, failures, header injection, outbox ---------------------------------
class SmtpConfig(Config):
    TESTING = False
    MAIL_BACKEND = "smtp"
    SMTP_HOST = "smtp.example"
    SMTP_PORT = 587
    SMTP_USERNAME = "user"
    SMTP_PASSWORD = "pw"
    MAIL_FROM = "UniHub <no-reply@campus.example>"
    PUBLIC_BASE_URL = "https://campus.example"


calls = []


class FakeSMTP:
    fail = False

    def __init__(self, host, port, timeout=None, context=None):
        calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        calls.append(("quit",))

    def starttls(self, context=None):
        calls.append(("starttls", context is not None))

    def login(self, user, password):
        calls.append(("login", user, password))

    def send_message(self, message):
        if FakeSMTP.fail:
            raise smtplib.SMTPRecipientsRefused({})
        calls.append(("send", message))


real_smtp, real_smtp_ssl = smtplib.SMTP, smtplib.SMTP_SSL
smtplib.SMTP = smtplib.SMTP_SSL = FakeSMTP
try:
    app = make_app(SmtpConfig)
    with app.app_context():
        send_email("student@binghamuni.edu.ng", "Confirm your email", "Click: https://campus.example/x/abc")
        assert [c[0] for c in calls] == ["connect", "starttls", "login", "send", "quit"], calls
        assert calls[0][1:] == ("smtp.example", 587, 10) and calls[2] == ("login", "user", "pw")
        message = calls[3][1]
        assert message["To"] == "student@binghamuni.edu.ng" and message["Subject"] == "Confirm your email"
        assert "no-reply@campus.example" in message["From"] and "@campus.example" in message["Message-ID"]
        assert "https://campus.example/x/abc" in message.get_content()
        assert "outbox" not in app.extensions, "the outbox must not grow in production"

        FakeSMTP.fail = True
        try:
            send_email("a@b.co", "s", "b")
            raise SystemExit("FAIL: SMTP failure was swallowed")
        except EmailError:
            pass
        assert notify("a@b.co", "s", "b") is False, "notify() must never raise"
        FakeSMTP.fail = False
        assert notify("a@b.co", "s", "b") is True

        try:
            send_email("a@b.co\nBcc: victim@x.co", "s", "b")
            raise SystemExit("FAIL: header injection accepted")
        except EmailError:
            pass
        try:
            send_email("a@b.co", "Hi\r\nBcc: victim@x.co", "b")
            raise SystemExit("FAIL: header injection accepted")
        except EmailError:
            pass

    calls.clear()
    app = make_app(type("Ssl", (SmtpConfig,), {"SMTP_USE_SSL": True, "SMTP_PORT": 465}))
    with app.app_context():
        send_email("a@b.co", "s", "b")
        assert "starttls" not in [c[0] for c in calls] and calls[0][2] == 465
finally:
    smtplib.SMTP, smtplib.SMTP_SSL = real_smtp, real_smtp_ssl

app = make_app(TestConfig)  # TESTING: messages are captured, nothing is sent
with app.app_context():
    send_email("a@b.co", "Hello", "Body")
    assert app.extensions["outbox"][-1] == {"to": "a@b.co", "subject": "Hello", "body": "Body"}
app = make_app(type("Weird", (TestConfig,), {"MAIL_BACKEND": "carrier-pigeon"}))
with app.app_context():
    try:
        send_email("a@b.co", "s", "b")
        raise SystemExit("FAIL: unknown backend accepted")
    except EmailError:
        pass
print("email ok")


# 3. Photo storage: local folder ---------------------------------------------------------------
app = make_app(TestConfig)
with app.test_request_context():
    key = save_image(png_upload())
    folder = app.config["UPLOAD_FOLDER"]
    assert key.endswith(".webp") and os.path.exists(os.path.join(folder, key))
    assert os.path.exists(os.path.join(folder, thumb_key(key)))
    assert image_url(key) == f"/media/{key}" and thumb_url(key) == f"/media/{thumb_key(key)}"
    delete_image_files([key])
    assert not os.path.exists(os.path.join(folder, key)) and not os.path.exists(os.path.join(folder, thumb_key(key)))
    delete_image_files([key])  # deleting twice is harmless
    before = set(os.listdir(folder))
    try:
        save_image(FileStorage(stream=io.BytesIO(b"nope"), filename="x.png"))
        raise SystemExit("FAIL: bad image accepted")
    except ImageError:
        pass
    assert set(os.listdir(folder)) == before
print("local storage ok")


# 4. Photo storage: S3-compatible bucket (R2 / S3 / B2) ---------------------------------------------
class FakeS3:
    def __init__(self):
        self.puts, self.deletes, self.fail_put_on, self.fail_deletes = [], [], None, False

    def put_object(self, **kw):
        if self.fail_put_on is not None and len(self.puts) == self.fail_put_on:
            raise OSError("network down")
        self.puts.append(kw)

    def delete_object(self, **kw):
        if self.fail_deletes:
            raise OSError("network down")
        self.deletes.append(kw)


S3Config = type("S3", (TestConfig,), {"STORAGE_BACKEND": "s3", "S3_BUCKET": "photos",
                                      "S3_PUBLIC_BASE_URL": "https://cdn.example/"})
app = make_app(S3Config)
fake = app.extensions["s3_client"] = FakeS3()
with app.test_request_context():
    key = save_image(png_upload())
    assert [p["Key"] for p in fake.puts] == [key, thumb_key(key)]
    assert all(p["Bucket"] == "photos" and p["ContentType"] == "image/webp" for p in fake.puts)
    assert all("immutable" in p["CacheControl"] for p in fake.puts)
    assert Image.open(io.BytesIO(fake.puts[0]["Body"])).format == "WEBP"
    assert max(Image.open(io.BytesIO(fake.puts[1]["Body"])).size) <= 480
    assert image_url(key) == f"https://cdn.example/{key}" and thumb_url(key) == f"https://cdn.example/{thumb_key(key)}"
    assert not os.listdir(app.config["UPLOAD_FOLDER"]), "S3 mode must not write local files"

    delete_image_files([key])
    assert sorted(d["Key"] for d in fake.deletes) == sorted([key, thumb_key(key)])

    fake.puts.clear(); fake.deletes.clear(); fake.fail_put_on = 1  # thumbnail upload fails
    try:
        save_image(png_upload())
        raise SystemExit("FAIL: upload failure was swallowed")
    except OSError:
        pass
    assert len(fake.deletes) == 1 and fake.deletes[0]["Key"] == fake.puts[0]["Key"], "half-uploaded photo must be cleaned up"

    fake.fail_put_on = None; fake.puts.clear()
    try:
        save_image(FileStorage(stream=io.BytesIO(b"nope"), filename="x.png"))
    except ImageError:
        pass
    assert fake.puts == [], "invalid images must never reach the bucket"

    fake.fail_deletes = True
    delete_image_files(["abc.webp"])  # cleanup failures are logged, not raised
print("s3 storage ok")


# 5. Emailed links come from PUBLIC_BASE_URL, not the (forgeable) Host header ------------------------
app = make_app(type("Pub", (TestConfig,), {"PUBLIC_BASE_URL": "https://campus.example"}))
with app.test_request_context("/", base_url="http://evil.example"):
    assert absolute_url("x", token="t") == "https://campus.example/x/t"
app = make_app(TestConfig)
with app.test_request_context("/", base_url="http://localhost:5000"):
    assert absolute_url("x", token="t") == "http://localhost:5000/x/t"
print("links ok")


# 6. Security headers ---------------------------------------------------------------------------------
app = make_app(Good)
app.add_url_rule("/", "home", lambda: "hi")
init_security(app)
client = app.test_client()
secure = client.get("/", base_url="https://campus.example")
h = secure.headers
assert "script-src 'self'" in h["Content-Security-Policy"] and "frame-ancestors 'none'" in h["Content-Security-Policy"]
assert "img-src 'self' data: https://cdn.example" in h["Content-Security-Policy"] and "unsafe-inline" not in h["Content-Security-Policy"]
assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
assert h["Strict-Transport-Security"].startswith("max-age=")
assert "Strict-Transport-Security" not in client.get("/", base_url="http://campus.example").headers
dev = make_app(TestConfig)
dev.add_url_rule("/", "home", lambda: "hi")
init_security(dev)
dev_headers = dev.test_client().get("/", base_url="https://campus.example").headers
assert "Strict-Transport-Security" not in dev_headers and "https://cdn" not in dev_headers["Content-Security-Policy"]
print("security headers ok")

print("ALL SERVICE CHECKS PASSED")
