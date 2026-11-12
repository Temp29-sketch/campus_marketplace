"""Catalog checks: visibility, scoping, filters, search, pagination, listing actions.

Run:  python test_catalog.py     (needs `pip install -r requirements.txt`)
"""
import re
from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import (
    Category, Product, ProductImage, University, User, VendorApplication,
)

app = create_app(TestConfig)
ids = {}


def body(response):
    return response.get_data(as_text=True)


def login(email):
    client = app.test_client()
    assert client.post("/login", data=dict(email=email, password="password123")).status_code == 302
    return client


with app.app_context():
    db.create_all()
    seed_defaults()
    bingham = University.query.filter_by(short_name="BINGHAM").one()
    other = University(name="Other University", short_name="OTHER", email_domain="other.edu.ng")
    db.session.add(other)
    cats = {c.slug: c for c in Category.query.all()}

    admin = User(university=bingham, name="Admin", email="admin@example.com", role="admin")
    admin.set_password("x")
    db.session.add(admin)
    db.session.flush()

    def make_user(uni, name, email, matric):
        user = User(university=uni, name=name, email=email)
        user.set_password("password123")
        if matric:
            user.mark_school_email_verified(email, matric)
        db.session.add(user)
        db.session.flush()
        return user

    def make_vendor(uni, name, email, matric, business, phone, approve=True):
        user = make_user(uni, name, email, matric)
        user.photo_key = "fixture.webp"
        application = VendorApplication(user=user, business_name=business, category=cats["food"],
                                        bio="We sell good things on campus.", phone_number=phone, bank_code="044", bank_name="Access Bank", account_number="1234567890")
        db.session.add(application)
        if approve:
            application.approve(admin)
        return user

    def make_product(vendor, title, category, price, description="", service=False, available=True, photo=True):
        product = Product(vendor=vendor, category=cats[category], title=title, price=price,
                          description=description, is_service=service, is_available=available)
        if photo:
            product.images = [ProductImage(storage_key=f"{title.replace(' ', '_')}.webp", position=0)]
        db.session.add(product)
        db.session.flush()
        ids[title] = product.id
        return product

    ada = make_vendor(bingham, "Ada", "ada@binghamuni.edu.ng", "19/0001", "Ada's Bites", "+2348012345678")
    bob = make_vendor(bingham, "Bob", "bob@binghamuni.edu.ng", "19/0002", "Bob Tech", "+2348099999999", approve=False)
    zed = make_vendor(other, "Zed", "zed@other.edu.ng", "20/1", "Zed Wears", "+2348033333333")
    make_user(bingham, "Eve", "eve@gmail.com", None)  # plain buyer

    # Created FIRST so they are the oldest: the feed is newest-first, 24 per page, and the
    # named listings below must land on page 1.
    for n in range(30):
        make_product(ada, f"Bulk item {n}", "other", 100 + n)
    make_product(ada, "Jollof rice", "food", 1500, "Party jollof, per pack")
    make_product(ada, "Meat pie", "food", 500, "Hot and flaky")
    make_product(ada, "Phone case", "tech", 2000, "Shockproof")
    make_product(ada, "Braiding", "services", 5000, "Home service", service=True, photo=False)
    make_product(ada, "Sold out thing", "other", 100, available=False)
    make_product(bob, "Hidden item", "tech", 900)  # Bob's application is still pending
    make_product(zed, "Other campus item", "fashion", 3000)
    db.session.commit()
    ada_id, bob_id = ada.id, bob.id

anon = app.test_client()
eve = login("eve@gmail.com")
ada_client = login("ada@binghamuni.edu.ng")

# 1. Visibility: only available listings from approved vendors.
page = body(anon.get("/"))
assert 'class="image-frame product-image shimmer"' in page
assert 'class="thumb"' in page and 'opacity-0' not in page and 'image-loaded' not in page
assert 'data-back-button' not in page
assert "Jollof rice" in page and "Meat pie" in page
assert "Sold out thing" not in page, "sold-out items must not appear in the feed"
assert "Hidden item" not in page, "pending vendors' listings must not appear"
assert "Other campus item" in page, "anonymous visitors see every active campus"
chips = re.search(r'<nav class="chips sticky-chips"[^>]*>(.*?)</nav>', page, re.S).group(1)
labels = ["All", "Beauty &amp; Skincare", "Fashion", "Food", "Tech", "Services", "Other"]
assert [chips.index(label) for label in labels] == sorted(chips.index(label) for label in labels)
assert chips.count('<svg class="category-icon"') == 6 and "Other ▾" not in chips and "other-chip" not in chips
print("visibility ok")

# CSS-first image display and password controls use accessible in-field eye markup.
login_html = body(anon.get('/login'))
assert '<div class="password-field"><input id="password" type="password"' in login_html
assert 'class="password-toggle" type="button"' in login_html and 'aria-label="Show password"' in login_html
assert 'eye-off-icon' in login_html
assert '<div class="password-field">' in body(anon.get('/register'))
assert '<div class="password-field">' in body(eve.get('/settings'))
print("image/password markup ok")

# 2. Scoping: signed-in students see their own campus only.
assert "Other campus item" not in body(eve.get("/"))
print("campus scoping ok")

# 3. Category filters; Services lives in the ordered category row.
page = body(anon.get("/?category=tech"))
assert "Phone case" in page and "Jollof rice" not in page
assert "Jollof rice" in body(anon.get("/?category=nope"))
page = body(anon.get("/?category=services"))
assert "Braiding" in page and "Jollof rice" not in page
print("filters ok")

# 4. Search: title, description, business name; all words must match; wildcards are literal.
assert "Jollof rice" in body(anon.get("/", query_string={"q": "JOLLOF"}))          # case-insensitive
assert "Jollof rice" in body(anon.get("/", query_string={"q": "party"}))           # description
assert "Jollof rice" in body(anon.get("/", query_string={"q": "ada"}))             # business name
page = body(anon.get("/", query_string={"q": "jollof pie"}))
assert "Jollof rice" not in page and "Meat pie" not in page                        # AND, not OR
for wildcard in ("%", "_", "100%"):
    assert "No listings match" in body(anon.get("/", query_string={"q": wildcard})), wildcard
print("search ok")

# 5. Sorting.
page = body(anon.get("/?category=food&sort=price_asc"))
assert page.index("Meat pie") < page.index("Jollof rice")
page = body(anon.get("/?category=food&sort=price_desc"))
assert page.index("Jollof rice") < page.index("Meat pie")
assert anon.get("/?sort=garbage").status_code == 200
print("sorting ok")

# 6. Partial responses (used by live search) are fragments, not full pages.
fragment = body(anon.get("/?partial=1&q=jollof"))
assert "Jollof rice" in fragment and "<html" not in fragment and "Add to cart" in fragment
print("partial ok")

# 7. Pagination: 24 per page.
assert body(anon.get("/?q=bulk")).count('data-product-id="') == 24
assert body(anon.get("/?q=bulk&page=2")).count('data-product-id="') == 6
assert anon.get("/?q=bulk&page=99").status_code == 200 and anon.get("/?page=-3").status_code == 200
print("pagination ok")

# 8. Direct contact shortcuts are absent from cards and details to keep purchases on-platform.
assert "2348012345678" not in body(anon.get("/")) and "0801 234 5678" not in body(anon.get("/"))
detail = body(anon.get(f"/item/{ids['Jollof rice']}"))
assert "Add to cart" in detail and "wa.me" not in detail and "0801 234 5678" not in detail
detail = body(eve.get(f"/item/{ids['Jollof rice']}"))
assert "Add to cart" in detail and "Save" in detail and "wa.me" not in detail and "0801 234 5678" not in detail
assert "wa.me" not in body(eve.get("/"))
json_toggle = eve.post(f"/wishlist/toggle/{ids['Jollof rice']}", headers={"Accept": "application/json"})
assert json_toggle.status_code == 200 and json_toggle.get_json() == {"saved": True}
json_toggle = eve.post(f"/wishlist/toggle/{ids['Jollof rice']}", headers={"Accept": "application/json"})
assert json_toggle.status_code == 200 and json_toggle.get_json() == {"saved": False}
assert anon.post(f"/wishlist/toggle/{ids['Jollof rice']}", headers={"Accept": "application/json"}).status_code == 401
redirect_toggle = eve.post(f"/wishlist/toggle/{ids['Jollof rice']}")
assert redirect_toggle.status_code == 302 and redirect_toggle.location.endswith(f"#product-{ids['Jollof rice']}")
cart_result = eve.post(f"/cart/add/{ids['Jollof rice']}", headers={"Accept": "application/json"})
assert cart_result.status_code == 200 and cart_result.get_json()['cart_count'] == 1
owner_detail = body(ada_client.get(f"/item/{ids['Jollof rice']}"))
assert "your listing" in owner_detail.lower() and "wa.me" not in owner_detail
print("listing action rules ok")

# 9. Detail pages: 404 for hidden, other-campus and unknown items; sold out remains viewable.
assert anon.get(f"/item/{ids['Hidden item']}").status_code == 404
assert eve.get(f"/item/{ids['Other campus item']}").status_code == 404
assert anon.get("/item/999999").status_code == 404
sold_out = eve.get(f"/item/{ids['Sold out thing']}")
assert sold_out.status_code == 200 and "Sold out" in body(sold_out) and "Add to cart" not in body(sold_out)
print("detail pages ok")

# 10. Storefronts.
store = anon.get(f"/store/{ada_id}")
assert store.status_code == 200 and "Jollof rice" in body(store) and "Verified" in body(store)
assert "Sold out thing" not in body(store)
assert anon.get(f"/store/{bob_id}").status_code == 404, "pending vendors have no storefront"
assert anon.get("/store/999999").status_code == 404
print("storefronts ok")

print("ALL CATALOG CHECKS PASSED")
