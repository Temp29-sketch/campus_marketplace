"""Regression for editing banners whose optional link is empty."""
import io
import re

from PIL import Image

from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import Banner, University, User

app = create_app(TestConfig)


def image_upload():
    stream = io.BytesIO()
    Image.new("RGB", (800, 500), "teal").save(stream, "PNG")
    stream.seek(0)
    return stream


with app.app_context():
    db.create_all()
    seed_defaults()
    university = University.query.filter_by(short_name="BINGHAM").one()
    admin = User(university=university, name="Banner Admin", email="banner-admin@test.example", role="admin")
    admin.set_password("password123")
    db.session.add(admin)
    db.session.commit()

client = app.test_client()
assert client.post("/login", data={"email": "banner-admin@test.example", "password": "password123"}).status_code == 302
created = client.post("/admin/banners/new", data={
    "title": "No link banner", "subtitle": "Optional link", "link_url": "", "is_active": "on",
    "image": (image_upload(), "banner.png"),
}, content_type="multipart/form-data")
assert created.status_code == 302
with app.app_context():
    banner = Banner.query.one()
    banner_id = banner.id
    assert banner.link_url is None

edit_html = client.get(f"/admin/banners/{banner_id}/edit").get_data(as_text=True)
assert "banner-admin.js" in edit_html and client.get("/static/banner-admin.js").status_code == 200
link_tag = re.search(r'<input[^>]*id="link_url"[^>]*>', edit_html).group(0)
assert "required" not in link_tag
assert re.search(r'value="([^"]*)"', link_tag).group(1) == ""

browser_form = {
    "title": "No link banner", "subtitle": "Optional link", "link_url": "",
    "is_active": "on", "fit_mode": "fill", "focus": "center",
}
saved = client.post(f"/admin/banners/{banner_id}/edit", data=browser_form, content_type="multipart/form-data")
assert saved.status_code == 302, saved.get_data(as_text=True)
listing = client.get("/admin/banners").get_data(as_text=True)
assert "Banner saved." in listing
with app.app_context():
    banner = db.session.get(Banner, banner_id)
    assert banner.title == "No link banner" and banner.link_url is None

set_link = client.post(f"/admin/banners/{banner_id}/edit", data={**browser_form, "link_url": "https://example.test/deal", "button_text": ""})
assert set_link.status_code == 302
with app.app_context():
    assert db.session.get(Banner, banner_id).link_url == "https://example.test/deal"
clear_link = client.post(f"/admin/banners/{banner_id}/edit", data=browser_form)
assert clear_link.status_code == 302
with app.app_context():
    assert db.session.get(Banner, banner_id).link_url is None

print("ALL BANNER EDIT CHECKS PASSED")
