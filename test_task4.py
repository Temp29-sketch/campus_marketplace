"""Task 4 regressions: media, demo checkout, reset, admin accounts and delivery."""
import hashlib
import io
from pathlib import Path
import re
from datetime import timedelta

from PIL import Image
from app import create_app, seed_defaults
from config import TestConfig, ProductionConfig
from extensions import db
from models import (APP_APPROVED, APP_PENDING, AdminAuditLog, Banner, Category, Hostel, Notification,
                    Order, OrderItem, PasswordResetToken, Product, ProductImage, University, User, VendorApplication, utcnow)
from services import paystack
from services.orders import is_confirmed


paystack_calls = []
def raises(*args, **kwargs):
    paystack_calls.append("called")
    raise AssertionError("Paystack must not be called in mock mode")


paystack.resolve_account = raises
paystack.create_subaccount = raises
paystack.list_banks = raises


class DemoConfig(TestConfig):
    PAYMENTS_MODE = "demo"
    PAYSTACK_VERIFY_BANK = False
    PAYSTACK_SECRET_KEY = ""


app = create_app(DemoConfig)
app.config.update(PAYMENTS_MODE="demo", PAYSTACK_VERIFY_BANK=False, PAYSTACK_SECRET_KEY="")


def image_file():
    data = io.BytesIO(); Image.new("RGB", (80, 60), "teal").save(data, "PNG"); data.seek(0)
    return data


def login(email, password="password123"):
    client = app.test_client()
    response = client.post("/login", data={"email": email, "password": password})
    assert response.status_code == 302, (email, response.status_code)
    return client


with app.app_context():
    db.create_all(); seed_defaults()
    campus = University.query.filter_by(short_name="BINGHAM").one()
    other = University(name="Other Campus", short_name="OTHER", email_domain="other.test"); db.session.add(other); db.session.flush()
    category = Category.query.filter_by(slug="food").one()
    hostel = Hostel.query.filter_by(university_id=campus.id, name="New Hostel (Boys)").one()
    admin = User(university=campus, name="Admin", email="admin@campus.test", role="admin"); admin.set_password("password123")
    other_admin = User(university=other, name="Other Admin", email="admin@other.test", role="admin"); other_admin.set_password("password123")
    buyer = User(university=campus, name="Buyer", email="buyer@campus.test", role="buyer", hostel_id=hostel.id, floor=2, room_number="B12"); buyer.set_password("password123")
    applicant = User(university=campus, name="Demo Vendor", email="vendor@campus.test", role="buyer"); applicant.set_password("password123")
    applicant.mark_school_email_verified("vendor@binghamuni.edu.ng", "24/001")
    db.session.add_all([admin, other_admin, buyer, applicant]); db.session.flush()
    db.session.commit()
    ids = {"campus": campus.id, "other": other.id, "category": category.id, "hostel": hostel.id,
           "admin": admin.id, "buyer": buyer.id, "vendor": applicant.id}

admin_client = login("admin@campus.test")
error_page = admin_client.get("/admin/does-not-exist").get_data(as_text=True)
assert 'aria-label="Go back"' in error_page and 'data-fallback="/"' in error_page
vendor_client = login("vendor@campus.test")
apply_response = vendor_client.post("/become-vendor/apply", data={
    "business_name": "Demo Shop", "category_id": str(ids["category"]),
    "bio": "Fresh campus products made available every day.", "phone_number": "08012345678",
    "bank_code": "044", "account_name": "Demo Vendor", "account_number": "0123456789", "own_account": "on",
    "profile_photo": (image_file(), "vendor.png")
}, content_type="multipart/form-data")
assert apply_response.status_code == 302
with app.app_context(): application_id = VendorApplication.query.filter_by(user_id=ids["vendor"]).one().id
assert admin_client.post(f"/admin/applications/{application_id}/approve").status_code == 302
with app.app_context():
    application = db.session.get(VendorApplication, application_id)
    assert application.status == APP_APPROVED and application.paystack_subaccount_code is None
assert paystack_calls == []
listing_response = vendor_client.post("/vendor/products/new", data={
    "title": "Demo apples", "description": "Fresh apples for campus buyers.", "price": "1500",
    "category_id": str(ids["category"]), "stock": "2", "images": (image_file(), "apples.png")
}, content_type="multipart/form-data")
assert listing_response.status_code == 302
with app.app_context(): product = Product.query.filter_by(vendor_id=ids["vendor"]).one(); product_id = product.id
buyer_client = login("buyer@campus.test")
assert buyer_client.post(f"/cart/add/{product_id}").status_code == 302
assert buyer_client.get("/checkout").status_code == 200
checkout_response = buyer_client.post("/checkout", data={"delivery_phone": "08012345678", "note": "Call me"})
assert checkout_response.status_code == 302 and "/checkout/demo/" in checkout_response.location
reference = checkout_response.location.rsplit("/", 1)[-1]
assert "DEMO PAYMENT (no real money)" in buyer_client.get(checkout_response.location).get_data(as_text=True)
assert buyer_client.post(f"/checkout/demo/{reference}/success").status_code == 302
with app.app_context():
    order = Order.query.filter_by(reference=reference).one(); item = order.items[0]
    assert order.status == "paid" and order.delivery_phone == "+2348012345678"
    assert db.session.get(Product, product_id).stock == 1
    assert Notification.query.filter(Notification.user_id.in_([ids["vendor"], ids["buyer"]])).count() >= 2
    order_id, item_id = order.id, item.id
assert "Demo payment" in buyer_client.get(f"/orders/{order_id}").get_data(as_text=True)
assert "Delivery phone" not in buyer_client.get(f"/item/{product_id}").get_data(as_text=True)
assert "Delivery phone: +2348012345678" in vendor_client.get("/vendor/orders").get_data(as_text=True)
assert "Delivery phone: +2348012345678" in admin_client.get("/admin/orders").get_data(as_text=True)

# Vendor progresses the line; buyer confirms. The 48-hour helper auto-confirms old deliveries.
for target in ("preparing", "ready", "delivered"):
    assert vendor_client.post(f"/vendor/orders/{order_id}/items/{item_id}/status", data={"status": target}).status_code == 302
with app.app_context(): assert db.session.get(OrderItem, item_id).delivered_at is not None
assert buyer_client.post(f"/orders/items/{item_id}/confirm").status_code == 302
with app.app_context():
    delivery_item = db.session.get(OrderItem, item_id); assert delivery_item.buyer_confirmed_at is not None and is_confirmed(delivery_item)
    delivery_item.buyer_confirmed_at = None; delivery_item.delivered_at = utcnow() - timedelta(hours=49); db.session.commit()
    assert is_confirmed(delivery_item)
assert "automatically confirmed" in buyer_client.get(f"/orders/{order_id}").get_data(as_text=True)
assert admin_client.post(f"/admin/orders/{order_id}/items/{item_id}/status", data={"status": "cancelled"}).status_code == 302
with app.app_context(): assert AdminAuditLog.query.filter_by(action="order_item_status_override").count() == 1

# Demo failure leaves stock untouched; demo routes are hidden in Paystack mode.
assert buyer_client.post(f"/cart/add/{product_id}").status_code == 302
failure_checkout = buyer_client.post("/checkout")
failure_ref = failure_checkout.location.rsplit("/", 1)[-1]
assert buyer_client.post(f"/checkout/demo/{failure_ref}/failed").status_code == 302
with app.app_context():
    failed = Order.query.filter_by(reference=failure_ref).one()
    assert failed.status == "failed" and db.session.get(Product, product_id).stock == 1
app.config["PAYMENTS_MODE"] = "paystack"
assert buyer_client.get(f"/checkout/demo/{reference}").status_code == 404
app.config["PAYMENTS_MODE"] = "demo"
print("demo approval, checkout, success/failure, stock and order notifications ok")

# Admin buyer listing/edit/scope/suspension and audit log.
assert "Registered buyers" in admin_client.get("/admin").get_data(as_text=True)
assert admin_client.get("/admin/buyers").status_code == 200
other_admin_client = login("admin@other.test")
assert other_admin_client.get(f"/admin/buyers/{ids['buyer']}").status_code == 404
assert buyer_client.get("/admin/buyers").status_code == 403
form = {"name": "Buyer Updated", "email": "buyer-new@campus.test", "phone_number": "08012345678",
        "hostel_id": str(ids["hostel"]), "hostel_block_id": "", "floor": "2", "room_number": "C13"}
assert admin_client.post(f"/admin/buyers/{ids['buyer']}", data=form).status_code == 200
with app.app_context():
    buyer = db.session.get(User, ids["buyer"]); assert buyer.email == form["email"] and buyer.name == form["name"]
    assert AdminAuditLog.query.filter_by(action="buyer_edited").count() >= 1
duplicate_response = admin_client.post(f"/admin/buyers/{ids['buyer']}", data={**form, "email": "admin@campus.test"})
assert duplicate_response.status_code == 200 and "already in use" in duplicate_response.get_data(as_text=True)
assert admin_client.post(f"/admin/buyers/{ids['buyer']}/status", data={"active": "0"}).status_code == 302
blocked = app.test_client().post("/login", data={"email": form["email"], "password": "password123"})
assert blocked.status_code == 200 and b"This account has been suspended" in blocked.data
assert admin_client.post(f"/admin/buyers/{ids['buyer']}/status", data={"active": "1"}).status_code == 302
assert login(form["email"]).get("/settings").status_code == 200
print("admin buyer scope, edits, audit, suspend and reactivation ok")

# Password reset: same response for known/unknown addresses, hash-only, GET-safe, one use and expiry.
reset_client = app.test_client()
known_response = reset_client.post("/forgot-password", data={"email": "vendor@campus.test"})
unknown_response = reset_client.post("/forgot-password", data={"email": "missing@campus.test"})
assert "If that email has an account, we've sent a reset link." in known_response.get_data(as_text=True)
assert "If that email has an account, we've sent a reset link." in unknown_response.get_data(as_text=True)
first_reset_link = re.search(r"http[^\s]+/reset-password/(\S+)", app.extensions["outbox"][-1]["body"]).group(1)
reset_client.post("/forgot-password", data={"email": "vendor@campus.test"})
reset_link = re.search(r"http[^\s]+/reset-password/(\S+)", app.extensions["outbox"][-1]["body"]).group(1)
token_hash = hashlib.sha256(reset_link.encode()).hexdigest()
first_hash = hashlib.sha256(first_reset_link.encode()).hexdigest()
with app.app_context():
    reset_row = PasswordResetToken.query.filter_by(token_hash=token_hash).one()
    assert reset_row.token_hash != reset_link and reset_row.used_at is None
    assert PasswordResetToken.query.filter_by(token_hash=first_hash).one().used_at is not None
assert "Request a new link" in reset_client.get(f"/reset-password/{first_reset_link}").get_data(as_text=True)
assert reset_client.get(f"/reset-password/{reset_link}").status_code == 200
with app.app_context(): assert PasswordResetToken.query.filter_by(token_hash=token_hash).one().used_at is None
assert reset_client.post(f"/reset-password/{reset_link}", data={"password": "newpassword123", "confirm_password": "newpassword123"}).status_code == 302
assert reset_client.post("/login", data={"email": "vendor@campus.test", "password": "password123"}).status_code == 200
assert reset_client.post("/login", data={"email": "vendor@campus.test", "password": "newpassword123"}).status_code == 302
with app.app_context():
    expired_token = "expired-test-token"
    db.session.add(PasswordResetToken(user_id=ids["admin"], token_hash=hashlib.sha256(expired_token.encode()).hexdigest(), expires_at=utcnow()-timedelta(hours=1)))
    db.session.commit()
assert reset_client.get(f"/reset-password/{expired_token}").status_code == 200
assert "Request a new link" in reset_client.get(f"/reset-password/{expired_token}").get_data(as_text=True)
reset_client.post("/forgot-password", data={"email": "admin@campus.test"})
admin_reset_link = re.search(r"http[^\s]+/reset-password/(\S+)", next(message for message in reversed(app.extensions["outbox"]) if "/reset-password/" in message["body"] and "admin@campus.test" in message["to"])["body"]).group(1)
assert reset_client.get(f"/reset-password/{admin_reset_link}").status_code == 200
assert reset_client.post(f"/reset-password/{admin_reset_link}", data={"password": "admin-new-pass", "confirm_password": "admin-new-pass"}).status_code == 302
assert reset_client.post("/login", data={"email": "admin@campus.test", "password": "admin-new-pass"}).status_code == 302
print("password reset enumeration, hash storage, expiry and single use ok")

# Admin settings allow name/password and confirmed-only email changes.
admin_settings = login("admin@campus.test", "admin-new-pass")
assert admin_settings.get("/settings").status_code == 200
wrong_password = admin_settings.post("/settings/password", data={"current_password": "wrong-password", "new_password": "otherpassword", "confirm_password": "otherpassword"}, follow_redirects=True)
assert "Current password is incorrect" in wrong_password.get_data(as_text=True)
assert admin_settings.post("/settings/email", data={"email": "new-admin@campus.test", "current_password": "wrong-password"}).status_code == 302
assert admin_settings.post("/settings/email", data={"email": "buyer-new@campus.test", "current_password": "admin-new-pass"}, follow_redirects=True).status_code == 200
with app.app_context(): assert db.session.get(User, ids["admin"]).pending_email is None
assert admin_settings.post("/settings/email", data={"email": "new-admin@campus.test", "current_password": "admin-new-pass"}).status_code == 302
with app.app_context():
    admin = db.session.get(User, ids["admin"]); assert admin.email == "admin@campus.test" and admin.pending_email == "new-admin@campus.test"
confirm_message = next(message for message in reversed(app.extensions["outbox"]) if "/settings/confirm-email/" in message["body"])
confirm_token = re.search(r"http[^\s]+/settings/confirm-email/(\S+)", confirm_message["body"]).group(1)
assert admin_settings.get(f"/settings/confirm-email/{confirm_token}").status_code == 302
with app.app_context(): assert db.session.get(User, ids["admin"]).email == "new-admin@campus.test"
print("admin settings confirmation flow ok")

class UnsafeDemo(ProductionConfig):
    PAYMENTS_MODE = "demo"
    PAYSTACK_SECRET_KEY = "sk_live_test"
    PAYSTACK_VERIFY_BANK = True
try: UnsafeDemo.validate()
except RuntimeError as error: assert "PAYMENTS_MODE=demo" in str(error)
else: raise AssertionError("production accepted demo payment mode")
class UnsafeBank(ProductionConfig):
    PAYMENTS_MODE = "paystack"
    PAYSTACK_SECRET_KEY = "sk_live_test"
    PAYSTACK_VERIFY_BANK = False
try: UnsafeBank.validate()
except RuntimeError as error: assert "PAYSTACK_VERIFY_BANK" in str(error)
else: raise AssertionError("production accepted disabled bank verification")
css = Path("static/style.css").read_text(encoding="utf-8")
assert "--footer-bg: #0b2623" in css and "color: #eef6f4" in css and "#a9c9c3" in css and "#2dd4bf" in css
print("production payment configuration guard ok")

class LimitedConfig(TestConfig):
    RATELIMIT_ENABLED = True
rate_app = create_app(LimitedConfig)
with rate_app.app_context():
    db.create_all(); seed_defaults()
rate_client = rate_app.test_client()
rate_responses = [rate_client.post("/forgot-password", data={"email": "not-present@campus.test"}) for _ in range(6)]
assert [response.status_code for response in rate_responses] == [200, 200, 200, 200, 200, 429]
print("password reset IP/email rate limit ok")
print("ALL TASK 4 CHECKS PASSED")
