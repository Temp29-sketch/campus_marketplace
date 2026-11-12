"""Wishlist, public navigation, legacy seed, and commission-safe catalog checks."""
from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import APP_APPROVED, Category, Hostel, HostelBlock, Product, University, User, VendorApplication, WishlistItem
from services.hostels import seed_hostels
from sqlalchemy.exc import IntegrityError

app = create_app(TestConfig)

with app.app_context():
    db.create_all()
    seed_defaults()
    uni = University.query.filter_by(short_name="BINGHAM").one()
    girls_old = Hostel.query.filter_by(university_id=uni.id, name="Old Hostel (Girls)").one()
    expected = ["Dorcas", "Abigail", "Esther", "Deborah", "Lois", "Hannah", "Ketura", "Naomi", "Rehab", "Gra", "Shera"]
    assert [b.name for b in HostelBlock.query.filter_by(hostel_id=girls_old.id).order_by(HostelBlock.sort_order)] == expected
    before = HostelBlock.query.filter_by(hostel_id=girls_old.id).count()
    seed_hostels(uni.id)
    assert HostelBlock.query.filter_by(hostel_id=girls_old.id).count() == before

    food = Category.query.filter_by(slug="food").one()
    admin = User(university=uni, name="Campus Admin", email="admin@storefront.test", role="admin")
    admin.set_password("password123")
    buyer = User(university=uni, name="Buyer Name", email="buyer@storefront.test")
    buyer.set_password("password123")
    vendor = User(university=uni, name="Vendor Name", email="vendor@storefront.test", role="vendor")
    vendor.set_password("password123")
    vendor.mark_school_email_verified("vendor@binghamuni.edu.ng", "22/100")
    vendor.photo_key = None
    db.session.add_all([admin, buyer, vendor])
    db.session.flush()
    application = VendorApplication(user=vendor, business_name="Vendor Foods", category=food,
        bio="Fresh food for campus students.", phone_number="+2348012345678", status=APP_APPROVED,
        bank_code="044", bank_name="Access Bank", account_number="1234567890")
    db.session.add(application)
    db.session.flush()
    available = Product(vendor=vendor, category=food, title="Available meal", price=500, stock=5, is_available=True)
    unavailable = Product(vendor=vendor, category=food, title="Unavailable meal", price=600, stock=0, is_available=False)
    removed = Product(vendor=vendor, category=food, title="Removed meal", price=700, is_removed=True)
    db.session.add_all([available, unavailable, removed])
    db.session.flush()
    buyer_id, available_id, unavailable_id, removed_id, university_id = buyer.id, available.id, unavailable.id, removed.id, uni.id
    db.session.add(WishlistItem(user_id=buyer_id, product_id=removed_id))
    db.session.commit()

client = app.test_client()
anon = app.test_client()
def body(response): return response.get_data(as_text=True)
def login(email):
    c = app.test_client()
    assert c.post("/login", data={"email": email, "password": "password123"}).status_code == 302
    return c

# Anonymous wishlist links preserve the target, while protected pages require login.
r = anon.post(f"/wishlist/toggle/{available_id}", headers={"Accept": "application/json"})
assert r.status_code == 401 and r.is_json
r = anon.post(f"/wishlist/toggle/{available_id}")
assert r.status_code == 302 and "/login?next=" in r.headers["Location"]
assert "/wishlist" in r.headers["Location"]
assert client.get("/wishlist").status_code == 302

buyer_client = login("buyer@storefront.test")
r = buyer_client.post(f"/wishlist/toggle/{available_id}", headers={"Accept": "application/json"})
assert r.status_code == 200 and r.json["saved"] is True
with app.app_context():
    assert WishlistItem.query.filter_by(user_id=buyer_id, product_id=available_id).count() == 1
    # The database unique constraint rejects duplicate rows.
    try:
        db.session.add(WishlistItem(user_id=buyer_id, product_id=available_id)); db.session.commit()
    except IntegrityError:
        db.session.rollback()
    else:
        raise AssertionError("wishlist duplicate was accepted")
assert buyer_client.post(f"/wishlist/toggle/{unavailable_id}", headers={"Accept": "application/json"}).json["saved"] is True
assert buyer_client.post(f"/wishlist/toggle/{removed_id}", headers={"Accept": "application/json"}).status_code == 200
wishlist = body(buyer_client.get("/wishlist"))
assert "Available meal" in wishlist and "Unavailable meal" in wishlist and "no longer available" in wishlist.lower()
assert "Removed meal" not in wishlist
assert buyer_client.post(f"/wishlist/toggle/{available_id}", headers={"Accept": "application/json"}).json["saved"] is False
assert buyer_client.get("/wishlist").status_code == 200

# Search and browse routes are shared across the shell; there are no contact shortcuts.
feed = body(anon.get("/"))
assert "Welcome to UniHub" in feed
footer = feed.split('<footer class="site-footer"', 1)[1].split("</footer>", 1)[0]
assert "Cart</a>" not in footer and "Browse" in footer and "Wishlist" in footer
assert feed.index("All") < feed.index("Beauty &amp; Skincare") < feed.index("Fashion") < feed.index("Food") < feed.index("Tech") < feed.index("Services") < feed.index("Other")
assert "Other ▾" not in feed and "other-chip" not in feed
assert "cart-link" in feed and "site-search" in feed and "site-sidebar" in feed
assert "Home" in feed and "Help" in feed and "Log in" in feed
assert "wa.me" not in feed and "tel:" not in feed
for path in ("/", "/login", "/register", "/register/vendor", "/help", "/terms", "/privacy", "/how-it-works", "/seller-guidelines"):
    assert 'id="site-sidebar"' in body(anon.get(path)), path
cart_redirect = anon.get("/cart")
from urllib.parse import parse_qs, urlparse
assert cart_redirect.status_code == 302 and "/login" in cart_redirect.headers["Location"] and parse_qs(urlparse(cart_redirect.headers["Location"]).query)["next"][0].endswith("/cart")
for path in ("/orders", "/wishlist", "/settings"):
    assert 'id="site-sidebar"' in body(buyer_client.get(path)), path
assert "Dorcas" in body(anon.get(f"/api/hostels?university_id={university_id}"))
assert "Block`" in open("static/hostel-fields.js", encoding="utf-8").read()
assert body(anon.get("/terms" )).count("Draft, to be reviewed before launch") >= 1
assert "support@unihub.example" in body(anon.get("/help"))

# Admins have only the admin page/logout in the sidebar and no settings access.
admin_client = login("admin@storefront.test")
admin_shell = body(admin_client.get("/"))
sidebar = admin_shell.split('<aside class="sidebar"', 1)[1].split("</aside>", 1)[0]
assert "Admin dashboard" in sidebar and "Log out" in sidebar
assert "Orders" in sidebar and "Wishlist" not in sidebar and "Account" not in sidebar and "Help" not in sidebar
assert admin_client.get("/settings").status_code == 200
print("wishlist behavior, shell, and hostel seed ok")

print("ALL STOREFRONT CHECKS PASSED")
