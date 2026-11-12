"""Cart, split payment and order checks. Uses a fake Paystack client; never reaches the network."""
import hashlib
import hmac
import json
from decimal import Decimal

from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import (APP_PENDING, APP_APPROVED, CartItem, Category, Hostel, Notification, Order, OrderItem,
                    PaystackEvent, Product, University, User, VendorApplication)
from services import paystack
from services.orders import calculate_commission, distribute_commission

app = create_app(TestConfig)
app.config.update(PAYSTACK_SECRET_KEY="test_secret", PAYSTACK_VERIFY_BANK=True, COMMISSION_PERCENT=10, MAX_VENDORS_PER_ORDER=5)
pay_payloads = []
verify_result = {}
resolve_result = {"account_name": ""}
fail_initialize = False
fail_subaccount = False


def fake_initialize(payload):
    if fail_initialize: raise paystack.PaystackError("fake initialize failure")
    pay_payloads.append(payload)
    return {"authorization_url": "https://checkout.paystack.com/test"}


def fake_verify(reference):
    return dict(verify_result)


def fake_resolve(account_number, bank_code):
    return dict(resolve_result)


def fake_subaccount(application):
    if fail_subaccount: raise paystack.PaystackError("fake subaccount failure")
    return {"subaccount_code": "ACCT_TEST_" + application.account_number[-2:]}


paystack.initialize_transaction = fake_initialize
paystack.verify_transaction = fake_verify
paystack.resolve_account = fake_resolve
paystack.create_subaccount = fake_subaccount


def make_user(uni, name, email, role="buyer"):
    user = User(university=uni, name=name, email=email, role=role); user.set_password("password123")
    db.session.add(user); db.session.flush(); return user


with app.app_context():
    db.create_all(); seed_defaults()
    uni = University.query.filter_by(short_name="BINGHAM").one()
    other = University(name="Other Uni", short_name="OTHER", email_domain="other.test"); db.session.add(other); db.session.flush()
    food = Category.query.filter_by(slug="food").one()
    hostel = Hostel.query.filter_by(university_id=uni.id, name="New Hostel (Boys)").one()
    admin = make_user(uni, "Campus Admin", "admin@one.test", "admin")
    other_admin = make_user(other, "Other Admin", "admin@two.test", "admin")
    buyer = make_user(uni, "Buyer One", "buyer@one.test")
    vendor1 = make_user(uni, "Ada Vendor", "ada@one.test", "vendor"); vendor1.photo_key = "ada.webp"
    app1 = VendorApplication(user=vendor1, business_name="Ada Shop", category=food, bio="Fresh campus goods every day.", phone_number="+2348012345678", bank_code="044", bank_name="Access Bank", account_number="1111111111", account_name="Ada Vendor", status=APP_APPROVED, paystack_subaccount_code="ACCT_A")
    db.session.add(app1)
    vendor2 = make_user(uni, "Bola Vendor", "bola@one.test", "vendor"); vendor2.photo_key = "bola.webp"
    app2 = VendorApplication(user=vendor2, business_name="Bola Shop", category=food, bio="Good campus goods every day.", phone_number="+2348098765432", bank_code="058", bank_name="GTBank", account_number="2222222222", account_name="Bola Vendor", status=APP_APPROVED, paystack_subaccount_code="ACCT_B")
    db.session.add(app2)
    foreign_vendor = make_user(other, "Foreign Vendor", "foreign@two.test", "vendor"); foreign_vendor.photo_key="foreign.webp"
    foreign_app = VendorApplication(user=foreign_vendor, business_name="Foreign Shop", category=food, bio="Foreign campus goods every day.", phone_number="+2348012340000", bank_code="044", bank_name="Access Bank", account_number="3333333333", account_name="Foreign Vendor", status=APP_APPROVED, paystack_subaccount_code="ACCT_C")
    own_product = Product(vendor=vendor1, category=food, title="Own product", price=100, stock=3, is_available=True)
    limited = Product(vendor=vendor2, category=food, title="Limited stock", price=1250.50, stock=2, is_available=True)
    service = Product(vendor=vendor2, category=food, title="Repair service", price=500, is_service=True, stock=None, is_available=True)
    foreign_product = Product(vendor=foreign_vendor, category=food, title="Foreign product", price=200, stock=4, is_available=True)
    db.session.add_all([app1, app2, foreign_app, own_product, limited, service, foreign_product]); db.session.commit()
    ids = {p.title: p.id for p in [own_product, limited, service, foreign_product]}
    buyer_id, admin_id, vendor1_id, vendor2_id = buyer.id, admin.id, vendor1.id, vendor2.id
    uni_id, other_uni_id, hostel_id, food_id, app2_id = uni.id, other.id, hostel.id, food.id, app2.id


def login(email):
    client = app.test_client()
    assert client.post("/login", data={"email": email, "password": "password123"}).status_code == 302
    return client


buyer_client = login("buyer@one.test")
admin_client = login("admin@one.test")
other_admin_client = login("admin@two.test")
vendor1_client = login("ada@one.test")
vendor2_client = login("bola@one.test")
anonymous = app.test_client()

# Cart access, ownership, campus, stock cap and service quantity rules.
assert anonymous.post(f"/cart/add/{ids['Limited stock']}").status_code == 302
assert vendor1_client.post(f"/cart/add/{ids['Own product']}").status_code == 404
assert buyer_client.post(f"/cart/add/{ids['Foreign product']}").status_code == 404
assert buyer_client.post(f"/cart/add/{ids['Limited stock']}").status_code == 302
assert buyer_client.post(f"/cart/add/{ids['Limited stock']}").status_code == 302
with app.app_context():
    item = CartItem.query.filter_by(user_id=buyer_id, product_id=ids["Limited stock"]).one()
    assert item.quantity == 2
assert buyer_client.post(f"/cart/add/{ids['Limited stock']}").status_code == 302
with app.app_context():
    assert CartItem.query.filter_by(user_id=buyer_id, product_id=ids["Limited stock"]).one().quantity == 2
assert buyer_client.post(f"/cart/add/{ids['Repair service']}").status_code == 302
assert buyer_client.post(f"/cart/update/{ids['Repair service']}", data={"quantity": "2"}).status_code == 302
assert "Cart, 3 items" in buyer_client.get("/").get_data(as_text=True)
print("cart ownership, campus and quantity rules ok")

# Exact integer-kobo commission and line allocation for one to three vendors.
assert calculate_commission(10005, 10) == 1001
for count in (1, 2, 3):
    lines = [{"line_total_kobo": 10001 + index} for index in range(count)]
    total = sum(line["line_total_kobo"] for line in lines)
    commission = calculate_commission(total, 10)
    split = distribute_commission(lines, commission)
    assert sum(row["commission_kobo"] for row in split) == commission
    assert sum(row["vendor_share_kobo"] for row in split) + commission == total
print("integer kobo commission and split sums ok")

# Checkout validates delivery, subaccounts, vendor cap, and database prices.
assert "Profile settings" in buyer_client.get("/checkout").get_data(as_text=True)  # no address yet
app.config["PAYSTACK_SECRET_KEY"] = ""
assert "Payments are not configured" in buyer_client.get("/checkout").get_data(as_text=True)
app.config["PAYSTACK_SECRET_KEY"] = "test_secret"
assert buyer_client.post("/checkout").status_code == 400
with app.app_context():
    buyer = db.session.get(User, buyer_id); buyer.hostel_id = hostel_id; buyer.floor = 2; buyer.room_number = "B12"; db.session.commit()
with app.app_context():
    app2 = db.session.get(VendorApplication, app2_id); app2.paystack_subaccount_code = None; db.session.commit()
assert "cannot take payments" in buyer_client.get("/checkout").get_data(as_text=True)
with app.app_context():
    db.session.get(VendorApplication, app2_id).paystack_subaccount_code = "ACCT_B"; db.session.commit()
assert buyer_client.post(f"/cart/add/{ids['Own product']}").status_code == 302
app.config["MAX_VENDORS_PER_ORDER"] = 1
assert "at most 1 vendors" in buyer_client.get("/checkout").get_data(as_text=True)
app.config["MAX_VENDORS_PER_ORDER"] = 5
# Service line is from vendor 2; use only it for a clean initialize.
assert buyer_client.post(f"/cart/remove/{ids['Limited stock']}").status_code == 302
assert buyer_client.post(f"/cart/remove/{ids['Own product']}").status_code == 302
before = len(pay_payloads)
checkout_response = buyer_client.post("/checkout", data={"note": "Leave at hostel", "amount": "1"})
assert checkout_response.status_code == 302 and checkout_response.headers["Location"] == "https://checkout.paystack.com/test"
payload = pay_payloads[before]
assert payload["amount"] == 50000 and payload["email"] == "buyer@one.test"
assert payload["metadata"].keys() == {"order_id"}
assert payload["split"] == {"type": "flat", "bearer_type": "account", "subaccounts": [{"subaccount": "ACCT_B", "share": 45000}]}
with app.app_context():
    order = Order.query.filter_by(buyer_id=buyer_id).order_by(Order.id.desc()).first()
    assert order.total_kobo == 50000 and order.commission_kobo == 5000 and order.note == "Leave at hostel"
    order_id, reference = order.id, order.reference
    assert len(reference.removeprefix("UH-")) == 16 and CartItem.query.filter_by(user_id=buyer_id).count() == 1
print("checkout amount and Paystack split payload ok")

# Client-supplied prices do not affect order totals; changing a product price flags the line.
with app.app_context():
    product = db.session.get(Product, ids["Repair service"]); product.price = Decimal("750.00"); db.session.commit()
assert "price changed" in buyer_client.get("/cart").get_data(as_text=True)
assert buyer_client.post("/checkout", data={"amount": "1"}).status_code == 400
assert buyer_client.post(f"/cart/update/{ids['Repair service']}", data={"quantity": "1", "refresh_price": "1"}).status_code == 302
print("checkout ignores client price and requires price review ok")

# Callback trusts server-side verify only; success must match amount/currency.
verify_result.update(status="success", amount=49999, currency="NGN", id=91)
assert buyer_client.get(f"/checkout/return?reference={reference}").status_code == 202
with app.app_context(): assert db.session.get(Order, order_id).status == "pending"
verify_result.update(status="success", amount=50000, currency="NGN", id=91)
assert buyer_client.get(f"/checkout/return?reference={reference}").status_code == 200
with app.app_context():
    order = db.session.get(Order, order_id); assert order.status == "paid" and order.paystack_transaction_id == "91"
    assert CartItem.query.filter_by(user_id=buyer_id, product_id=ids["Repair service"]).count() == 0
assert "Order placed" in buyer_client.get(f"/checkout/return?reference={reference}").get_data(as_text=True)
verify_result.update(status="failed", amount=1, currency="USD")
print("verified callback and idempotent paid return ok")

# Verified failure is the only return page that says the buyer was not charged.
assert buyer_client.post(f"/cart/add/{ids['Repair service']}").status_code == 302
assert buyer_client.post("/checkout").status_code == 302
with app.app_context():
    failed_order = Order.query.filter_by(buyer_id=buyer_id, status="pending").order_by(Order.id.desc()).first()
    failed_reference, failed_id = failed_order.reference, failed_order.id
verify_result.update(status="failed", amount=50000, currency="NGN")
failure_page = buyer_client.get(f"/checkout/return?reference={failed_reference}").get_data(as_text=True)
assert "You were not charged" in failure_page
with app.app_context(): assert db.session.get(Order, failed_id).status == "failed"
assert buyer_client.post(f"/cart/remove/{ids['Repair service']}").status_code == 302
print("failed and amount-mismatch callback cases ok")

# Webhook signature, replay idempotency, stock decrement and shortage/manual-refund notification.
with app.app_context():
    # A second pending order to test inventory contention.
    db.session.add(CartItem(user_id=buyer_id, product_id=ids["Limited stock"], quantity=2, unit_price_kobo=125050))
    db.session.commit()
assert buyer_client.post("/checkout", data={}).status_code == 302
with app.app_context():
    pending = Order.query.filter_by(buyer_id=buyer_id, status="pending").order_by(Order.id.desc()).first()
    shortage_ref, shortage_id, shortage_amount = pending.reference, pending.id, pending.total_kobo
    db.session.get(Product, ids["Limited stock"]).stock = 1; db.session.commit()
event = {"id": "evt-stock", "event": "charge.success", "data": {"reference": shortage_ref, "amount": shortage_amount, "currency": "NGN", "id": 92}}
raw = json.dumps(event, separators=(",", ":")).encode()
signature = hmac.new(b"test_secret", raw, hashlib.sha512).hexdigest()
assert anonymous.post("/webhooks/paystack", data=raw, headers={"x-paystack-signature": "bad"}).status_code == 401
response = anonymous.post("/webhooks/paystack", data=raw, headers={"x-paystack-signature": signature})
assert response.status_code == 200 and anonymous.post("/webhooks/paystack", data=raw, headers={"x-paystack-signature": signature}).status_code == 200
with app.app_context():
    order = db.session.get(Order, shortage_id); assert order.status == "paid"
    assert order.items[0].fulfilment_status == "cancelled"
    assert db.session.get(Product, ids["Limited stock"]).stock == 1
    assert PaystackEvent.query.filter_by(reference=shortage_ref).count() == 1
    assert Notification.query.filter_by(user_id=admin_id, kind="stock_shortage").count() == 1
print("webhook signature, replay, stock shortage and manual refund alert ok")

# Buyer, vendor and campus-admin visibility plus vendor-only status transitions.
assert f"/orders/{shortage_id}" in buyer_client.get("/orders").get_data(as_text=True)
assert buyer_client.get(f"/orders/{shortage_id}").status_code == 200
assert anonymous.get(f"/orders/{shortage_id}").status_code == 302
assert other_admin_client.get("/admin/orders").get_data(as_text=True).find(f"Order #{shortage_id}") == -1
assert "/vendor/orders" in vendor2_client.get("/").get_data(as_text=True)
# A successful, fulfillable vendor-1 order and its status notification.
with app.app_context():
    db.session.add(CartItem(user_id=buyer_id, product_id=ids["Own product"], quantity=1, unit_price_kobo=10000))
    db.session.commit()
assert buyer_client.post("/checkout").status_code == 302
with app.app_context():
    last_order = Order.query.filter_by(buyer_id=buyer_id).order_by(Order.id.desc()).first()
    last_order_id = last_order.id; line_id = last_order.items[0].id
# pay for it through a valid webhook
ev2 = {"id": "evt-order", "event": "charge.success", "data": {"reference": last_order.reference, "amount": last_order.total_kobo, "currency": "NGN", "id": 93}}
raw2 = json.dumps(ev2, separators=(",", ":")).encode(); sig2 = hmac.new(b"test_secret", raw2, hashlib.sha512).hexdigest()
anonymous.post("/webhooks/paystack", data=raw2, headers={"x-paystack-signature": sig2})
with app.app_context(): assert db.session.get(Product, ids["Own product"]).stock == 2
assert vendor1_client.post(f"/vendor/orders/{last_order_id}/items/{line_id}/status", data={"status": "preparing"}).status_code == 302
assert "Buyer One" not in vendor1_client.get("/vendor/orders").get_data(as_text=True) or "Buyer: Buyer" in vendor1_client.get("/vendor/orders").get_data(as_text=True)
assert buyer_client.get("/api/notifications").get_json()["unread_count"] >= 1
assert admin_client.get("/admin/orders").status_code == 200
print("buyer, vendor and campus admin order visibility ok")

# Admin payout-account setup: approval succeeds despite mismatch or a gateway failure.
with app.app_context():
    uni = db.session.get(University, uni_id); category = db.session.get(Category, food_id)
    applicant = make_user(uni, "New Applicant", "newapp@one.test")
    applicant.mark_school_email_verified("newapp@binghamuni.edu.ng", "24/1"); applicant.photo_key = "new.webp"
    application = VendorApplication(user=applicant, business_name="New Business", category=category, bio="A new campus business with good goods.", phone_number="+2348012345678", bank_code="044", bank_name="Access Bank", account_number="4444444444", account_name="New Applicant", status=APP_PENDING)
    db.session.add(application); db.session.commit(); applicant_app_id = application.id
resolve_result.clear(); resolve_result.update(account_name="New Applicant")
assert admin_client.post(f"/admin/applications/{applicant_app_id}/approve").status_code == 302
with app.app_context():
    approw = db.session.get(VendorApplication, applicant_app_id); assert approw.status == APP_APPROVED and approw.paystack_subaccount_code
with app.app_context():
    applicant2 = make_user(db.session.get(University, uni_id), "Mismatch Applicant", "mismatch@one.test")
    applicant2.mark_school_email_verified("mismatch@binghamuni.edu.ng", "24/2"); applicant2.photo_key = "mismatch.webp"
    mismatch_app = VendorApplication(user=applicant2, business_name="Mismatch Business", category=db.session.get(Category, food_id), bio="A mismatch campus business and goods.", phone_number="+2348012345679", bank_code="044", bank_name="Access Bank", account_number="5555555555", account_name="Different Name", status=APP_PENDING)
    db.session.add(mismatch_app); db.session.commit(); mismatch_id = mismatch_app.id
resolve_result["account_name"] = "Paystack Account Name"
assert admin_client.post(f"/admin/applications/{mismatch_id}/approve").status_code == 302
with app.app_context():
    mismatch_row = db.session.get(VendorApplication, mismatch_id)
    assert mismatch_row.status == APP_APPROVED and mismatch_row.paystack_subaccount_code
with app.app_context():
    failing = make_user(db.session.get(University, uni_id), "Fail Applicant", "fail@one.test")
    failing.mark_school_email_verified("fail@binghamuni.edu.ng", "24/3"); failing.photo_key = "fail.webp"
    fail_app = VendorApplication(user=failing, business_name="Fail Business", category=db.session.get(Category, food_id), bio="A failing campus business and its goods.", phone_number="+2348012345680", bank_code="044", bank_name="Access Bank", account_number="6666666666", account_name="Fail Applicant", status=APP_PENDING)
    db.session.add(fail_app); db.session.commit(); fail_id = fail_app.id
fail_subaccount = True
resolve_result["account_name"] = "Fail Applicant"
assert admin_client.post(f"/admin/applications/{fail_id}/approve").status_code == 302
fail_subaccount = False
with app.app_context():
    fail_row = db.session.get(VendorApplication, fail_id)
    assert fail_row.status == APP_APPROVED and not fail_row.paystack_subaccount_code
print("payout account approval remains available through mismatch and gateway failure")

# Config bounds and public pages never disclose vendor phone numbers.
assert "+2348012345678" not in buyer_client.get(f"/item/{ids['Own product']}").get_data(as_text=True)
class BadCommission(TestConfig): COMMISSION_PERCENT = 51
try:
    BadCommission.validate()
except RuntimeError: pass
else: raise AssertionError("invalid commission accepted")
print("configuration and contact privacy ok")
print("ALL PAYMENT CHECKS PASSED")
