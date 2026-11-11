"""Reports and moderation checks.

Run:  python test_moderation.py     (needs `pip install -r requirements.txt`)
"""
from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import (
    Category, Product, Report, University, User, VendorApplication,
)

app = create_app(TestConfig)
outbox = app.extensions.setdefault("outbox", [])
ids = {}


def body(response):
    return response.get_data(as_text=True)


def login(email):
    client = app.test_client()
    assert client.post("/login", data=dict(email=email, password="password123")).status_code == 302, email
    return client


def reports_for(title=None, status=None):
    with app.app_context():
        query = Report.query
        if title:
            query = query.join(Product).filter(Product.title == title)
        if status:
            query = query.filter(Report.status == status)
        return query.count()


with app.app_context():
    db.create_all()
    seed_defaults()
    bingham = University.query.filter_by(short_name="BINGHAM").one()
    other = University(name="Other University", short_name="OTHER", email_domain="other.edu.ng")
    db.session.add(other)
    cat = Category.query.filter_by(slug="food").one()

    def make_user(uni, name, email, matric=None, role="buyer"):
        user = User(university=uni, name=name, email=email, role=role)
        user.set_password("password123")
        if matric:
            user.mark_school_email_verified(email, matric)
        db.session.add(user)
        db.session.flush()
        return user

    admin = make_user(bingham, "Admin", "admin@example.com", role="admin")
    admin2 = make_user(other, "Admin Two", "admin2@example.com", role="admin")

    def make_vendor(uni, name, email, matric, business):
        user = make_user(uni, name, email, matric)
        user.photo_key = "fixture.webp"
        application = VendorApplication(user=user, business_name=business, category=cat,
                                        bio="We sell good things on campus.", phone_number="+2348012345678", bank_code="044", bank_name="Access Bank", account_number="1234567890")
        db.session.add(application)
        application.approve(admin)
        return user

    def make_product(vendor, title):
        product = Product(vendor=vendor, category=cat, title=title, price=1000, description="Nice")
        db.session.add(product)
        db.session.flush()
        ids[title] = product.id

    ada = make_vendor(bingham, "Ada", "ada@binghamuni.edu.ng", "19/0001", "Ada's Bites")
    bob = make_vendor(bingham, "Bob", "bob@binghamuni.edu.ng", "19/0002", "Bob Tech")
    zed = make_vendor(other, "Zed", "zed@other.edu.ng", "20/1", "Zed Wears")
    make_user(bingham, "Eve", "eve@gmail.com")
    fay = make_user(bingham, "Fay", "fay@gmail.com")
    make_user(other, "Gus", "gus@gmail.com")
    for vendor, title in [(ada, "Jollof rice"), (ada, "Meat pie"), (bob, "Bob phone"), (zed, "Zed shirt")]:
        make_product(vendor, title)
    db.session.commit()
    ada_id, bob_id, fay_id = ada.id, bob.id, fay.id

anon = app.test_client()
eve, fay_client = login("eve@gmail.com"), login("fay@gmail.com")
ada_client, bob_client = login("ada@binghamuni.edu.ng"), login("bob@binghamuni.edu.ng")
admin_client, admin2_client = login("admin@example.com"), login("admin2@example.com")
jollof, pie, phone, zshirt = (ids[t] for t in ("Jollof rice", "Meat pie", "Bob phone", "Zed shirt"))

# 1. Reporting requires a login; the form loads for students.
r = anon.get(f"/item/{jollof}/report")
assert r.status_code == 302 and "/login" in r.headers["Location"]
assert eve.get(f"/item/{jollof}/report").status_code == 200
assert "Report this listing" in body(eve.get(f"/item/{jollof}"))
assert "Log in to report" in body(anon.get(f"/item/{jollof}"))
assert "Report this listing" not in body(ada_client.get(f"/item/{jollof}")), "owners can't report themselves"
print("report form access ok")

# 2. Validation, success, duplicates, own listing.
assert eve.post(f"/item/{jollof}/report", data={}).status_code == 200
assert eve.post(f"/item/{jollof}/report", data=dict(reason="nonsense")).status_code == 200
assert eve.post(f"/item/{jollof}/report", data=dict(reason="other", details="bad")).status_code == 200
assert reports_for() == 0
r = eve.post(f"/item/{jollof}/report", data=dict(reason="scam", details="Asked me to pay first"))
assert r.status_code == 302 and reports_for("Jollof rice", "open") == 1
assert eve.post(f"/item/{jollof}/report", data=dict(reason="scam")).status_code == 302
assert reports_for("Jollof rice") == 1, "same student can't stack open reports on one listing"
assert ada_client.post(f"/item/{jollof}/report", data=dict(reason="scam")).status_code == 302
assert reports_for("Jollof rice") == 1, "vendors can't report their own listing"
assert eve.get(f"/item/{zshirt}/report").status_code == 404, "other-campus listings are out of reach"
print("report submission ok")

# 3. Daily limit.
with app.app_context():
    for _ in range(10):
        db.session.add(Report(product_id=pie, reporter_id=fay_id, reason="scam", status="dismissed"))
    db.session.commit()
before = reports_for()
assert fay_client.post(f"/item/{phone}/report", data=dict(reason="scam")).status_code == 200
assert reports_for() == before, "11th report in a day must be refused"
with app.app_context():  # remove the filler so Fay can report normally again below
    Report.query.filter_by(reporter_id=fay_id, status="dismissed").delete()
    db.session.commit()
print("daily limit ok")

# 4. Admin queue: only admins, only their campus.
assert anon.get("/admin/reports").status_code == 302
assert eve.get("/admin/reports").status_code == 403
page = body(admin_client.get("/admin/reports"))
assert "Jollof rice" in page and "Looks like a scam" in page and "Asked me to pay first" in page and "Eve" in page
assert "Jollof rice" not in body(admin2_client.get("/admin/reports?status=all"))
with app.app_context():
    jollof_report = Report.query.join(Product).filter(Product.title == "Jollof rice").one().id
assert admin2_client.post(f"/admin/reports/{jollof_report}/dismiss").status_code == 404
assert eve.post(f"/admin/reports/{jollof_report}/dismiss").status_code == 403
print("queue access ok")

# 5. Dismiss leaves the listing up and can't be repeated.
assert admin_client.post(f"/admin/reports/{jollof_report}/dismiss", data=dict(note="Looks fine")).status_code == 302
with app.app_context():
    report = db.session.get(Report, jollof_report)
    assert report.status == "dismissed" and report.resolution_note == "Looks fine" and report.resolved_by.name == "Admin"
assert "Jollof rice" in body(anon.get("/"))
assert admin_client.post(f"/admin/reports/{jollof_report}/remove-listing").status_code == 302
with app.app_context():
    assert db.session.get(Product, jollof).is_removed is False, "a handled report can't be re-used to remove a listing"
print("dismiss ok")

# 6. Remove listing: hidden everywhere, vendor emailed, all open reports closed, vendor can't undo it.
assert eve.post(f"/item/{pie}/report", data=dict(reason="misleading")).status_code == 302
assert fay_client.post(f"/item/{pie}/report", data=dict(reason="prohibited")).status_code == 302
with app.app_context():
    pie_report = Report.query.join(Product).filter(Product.title == "Meat pie", Report.status == "open").first().id
assert "2 open reports" in body(admin_client.get("/admin/reports"))
emails = len(outbox)
assert admin_client.post(f"/admin/reports/{pie_report}/remove-listing", data=dict(note="Not real food")).status_code == 302
with app.app_context():
    product = db.session.get(Product, pie)
    assert product.is_removed and product.removed_reason == "Not real food"
assert reports_for("Meat pie", "open") == 0 and reports_for("Meat pie", "actioned") == 2
assert "Meat pie" not in body(anon.get("/")) and anon.get(f"/item/{pie}").status_code == 404
assert len(outbox) == emails + 1 and outbox[-1]["to"] == "ada@binghamuni.edu.ng"
assert "Not real food" in outbox[-1]["body"] and "Meat pie" in outbox[-1]["body"]
mine = body(ada_client.get("/vendor/products"))
assert "Removed by admin" in mine and "Not real food" in mine
ada_client.post(f"/vendor/products/{pie}/toggle")
assert "Meat pie" not in body(anon.get("/")), "toggling availability must not un-remove a listing"
print("remove listing ok")

# 7. Restore.
assert "Restore listing" in body(admin_client.get("/admin/reports?status=actioned"))
assert admin2_client.post(f"/admin/products/{pie}/restore").status_code == 404
emails = len(outbox)
assert admin_client.post(f"/admin/products/{pie}/restore").status_code == 302
with app.app_context():
    product = db.session.get(Product, pie)
    assert not product.is_removed and product.removed_reason is None
    product.is_available = True
    db.session.commit()
assert "Meat pie" in body(anon.get("/")) and len(outbox) == emails + 1
assert admin_client.post(f"/admin/products/{zshirt}/restore").status_code == 404
print("restore ok")

# 8. Revoke vendor.
assert eve.post(f"/item/{phone}/report", data=dict(reason="scam", details="Took my money, no delivery")).status_code == 302
with app.app_context():
    phone_report = Report.query.join(Product).filter(Product.title == "Bob phone").one().id
assert "Revoke vendor" in body(admin_client.get("/admin/reports"))
admin_client.post(f"/admin/reports/{phone_report}/revoke-vendor", data=dict(note=""))
with app.app_context():
    assert db.session.get(User, bob_id).role == "vendor", "a reason is mandatory"
emails = len(outbox)
assert admin_client.post(f"/admin/reports/{phone_report}/revoke-vendor", data=dict(note="Repeated scams")).status_code == 302
with app.app_context():
    bob = db.session.get(User, bob_id)
    assert bob.role == "buyer" and bob.vendor_application.status == "rejected"
    assert bob.vendor_application.rejection_reason == "Repeated scams"
    assert db.session.get(Report, phone_report).status == "actioned"
assert "Bob phone" not in body(anon.get("/")) and anon.get(f"/store/{bob_id}").status_code == 404
assert len(outbox) == emails + 1 and outbox[-1]["to"] == "bob@binghamuni.edu.ng" and "Repeated scams" in outbox[-1]["body"]
assert bob_client.get("/vendor/products").status_code == 302, "revoked vendors lose the listing tools"
assert bob_client.get("/become-vendor/apply").status_code == 200, "but can re-apply"
assert "Repeated scams" in body(bob_client.get("/become-vendor/apply"))
assert admin_client.post(f"/admin/reports/{phone_report}/revoke-vendor", data=dict(note="again")).status_code == 302
assert len(outbox) == emails + 1, "revoking twice must not email twice"
print("revoke vendor ok")

# 9. Revoked vendor can be approved again and their listings return.
with app.app_context():
    admin = User.query.filter_by(email="admin@example.com").one()
    bob = db.session.get(User, bob_id)
    bob.vendor_application.status = "pending"
    bob.vendor_application.approve(admin)
    db.session.commit()
assert "Bob phone" in body(anon.get("/"))
print("re-approval ok")

print("ALL MODERATION CHECKS PASSED")
