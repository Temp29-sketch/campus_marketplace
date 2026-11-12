"""End-to-end check: any-email buyer -> school-email verification -> application ->
admin review (approve / reject / resubmit) -> vendor lists a product."""
import re

from sqlalchemy.exc import IntegrityError

from app import create_app, seed_defaults
from config import TestConfig
from extensions import db
from models import (
    Category, Hostel, Product, ProductImage, University, User, VendorApplication,
)
from services import paystack

# All approval-time Paystack calls are faked: tests never contact Paystack.
paystack.resolve_account = lambda account_number, bank_code: {"account_name": "Ada" if account_number == "1234567890" else "Bob"}
paystack.create_subaccount = lambda application: {"subaccount_code": "ACCT_TEST"}

app = create_app(TestConfig)
client = app.test_client()
outbox = app.extensions.setdefault("outbox", [])


def login(email, password):
    c = app.test_client()
    r = c.post("/login", data=dict(email=email, password=password))
    assert r.status_code == 302, f"login failed for {email}"
    return c


with app.app_context():
    db.create_all()
    seed_defaults()
    bingham = University.query.filter_by(short_name="BINGHAM").one()
    assert bingham.email_domain == "binghamuni.edu.ng"
    admin = User(university=bingham, name="Admin", email="admin@example.com", role="admin")
    admin.set_password("adminpass1")
    db.session.add(admin)
    db.session.commit()
    bingham_id, admin_id = bingham.id, admin.id
    food_id = Category.query.filter_by(slug="food").one().id
    boys_hostel_id = Hostel.query.filter_by(name="New Hostel (Boys)").one().id

# 1. A student registers as a buyer with a gmail address.
r = client.post("/register", data=dict(
    name="Ada", email="ada@gmail.com", password="password123", confirm_password="password123", university_id=bingham_id, hostel_id=boys_hostel_id, floor="1", room_number="A12"))
assert r.status_code == 302
with app.app_context():
    ada = User.query.filter_by(email="ada@gmail.com").one()
    assert ada.role == "buyer" and not ada.has_verified_school_email
    assert not ada.can_apply_to_be_vendor and not ada.can_list_products
    ada_id = ada.id

# 2. Applying before verifying sends her to the school-email step.
r = client.get("/become-vendor/apply")
assert r.status_code == 302 and r.headers["Location"].endswith("/become-vendor/verify")

# 3. Non-school email refused; Bingham email gets a link; nothing stored until clicked.
r = client.post("/become-vendor/verify", data=dict(school_email="ada@gmail.com", matric_number="19/0001"))
assert r.status_code == 200 and not outbox
r = client.post("/become-vendor/verify", data=dict(
    school_email="ada@binghamuni.edu.ng", matric_number="19/0001"))
assert r.status_code == 302 and len(outbox) == 1
with app.app_context():
    assert db.session.get(User, ada_id).school_email is None
token = re.search(r"/verify-school-email/(\S+)", outbox[0]["body"]).group(1)
assert client.get("/verify-school-email/garbage").status_code == 302
with app.app_context():
    assert not db.session.get(User, ada_id).has_verified_school_email
r = client.get(f"/verify-school-email/{token}")
assert r.status_code == 302 and r.headers["Location"].endswith("/become-vendor/apply")
with app.app_context():
    ada = db.session.get(User, ada_id)
    assert ada.has_verified_school_email and ada.can_apply_to_be_vendor
print("school email verified")

# 4. Application form validation, then a good submission (phone gets normalised).
with app.app_context():
    ada = db.session.get(User, ada_id); ada.photo_key = "fixture.webp"; db.session.commit()
good = dict(business_name="Ada Bites", category_id=food_id,
            bio="Fresh small chops and meat pies delivered around campus.",
            phone_number="0801 234 5678", bank_code="044", account_number="1234567890", own_account="on")
assert client.get("/become-vendor/apply").status_code == 200
r = client.post("/become-vendor/apply", data={**good, "phone_number": "12345"})
assert r.status_code == 200
with app.app_context():
    assert VendorApplication.query.count() == 0
r = client.post("/become-vendor/apply", data=good)
assert r.status_code == 302
with app.app_context():
    va = VendorApplication.query.filter_by(user_id=ada_id).one()
    assert va.status == "pending" and va.phone_number == "+2348012345678"
    ada_app_id = va.id
assert b"under review" in client.get("/become-vendor/apply").data
print("application submitted")

# 5. Only admins can review.
assert client.get("/admin/applications").status_code == 403
assert client.post(f"/admin/applications/{ada_app_id}/approve").status_code == 403
admin_client = login("admin@example.com", "adminpass1")
r = admin_client.get("/admin/applications")
assert r.status_code == 200 and b"Ada Bites" in r.data

# 6. Approval upgrades Ada to vendor and emails her.
assert admin_client.post(f"/admin/applications/{ada_app_id}/approve").status_code == 302
with app.app_context():
    ada = db.session.get(User, ada_id)
    assert ada.role == "vendor" and ada.can_list_products and ada.business_name == "Ada Bites"
assert outbox[-1]["to"] == "ada@gmail.com" and "approved" in outbox[-1]["subject"]
assert client.get("/become-vendor/apply").status_code == 302  # vendors don't re-apply
print("approved -> vendor")

# 7. Vendor lists a product with images; listing exposes contact details.
with app.app_context():
    ada = db.session.get(User, ada_id)
    p = Product(vendor=ada, category_id=food_id, title="Meat pie", description="Hot", price=500)
    p.images = [ProductImage(storage_key="a.jpg", position=0), ProductImage(storage_key="b.jpg", position=1)]
    db.session.add(p)
    db.session.commit()
    assert p.business_name == "Ada Bites" and p.contact_phone == "+2348012345678"
    assert p.cover_image.storage_key == "a.jpg" and len(p.images) == 2

# 8. Rejection and resubmission (Bob has a verified school email already).
with app.app_context():
    bingham = db.session.get(University, bingham_id)
    bob = User(university=bingham, name="Bob", email="bob@gmail.com")
    bob.set_password("password123")
    bob.mark_school_email_verified("bob@binghamuni.edu.ng", "19/0002")
    bob.photo_key = "fixture.webp"
    db.session.add(bob)
    db.session.commit()
    bob_id = bob.id
bob_client = login("bob@gmail.com", "password123")
dup_name = {**good, "business_name": "ada bites"}  # same name as Ada's, different case
r = bob_client.post("/become-vendor/apply", data=dup_name)
assert r.status_code == 200
with app.app_context():
    assert VendorApplication.query.filter_by(user_id=bob_id).count() == 0
bob_form = {**good, "business_name": "Bob Tech", "bio": "Phone repairs", "phone_number": "09012345678"}
bob_form["account_number"] = "9876543210"  # fake Paystack lookup returns Bob for this account
bob_client.post("/become-vendor/apply", data=bob_form)  # bio too short -> rejected by validation
with app.app_context():
    assert VendorApplication.query.filter_by(user_id=bob_id).count() == 0
bob_form["bio"] = "Phone repairs and accessories, fast turnaround on campus."
assert bob_client.post("/become-vendor/apply", data=bob_form).status_code == 302
with app.app_context():
    bob_app_id = VendorApplication.query.filter_by(user_id=bob_id).one().id
assert admin_client.post(f"/admin/applications/{bob_app_id}/reject",
                         data=dict(reason="Please add a clearer bio")).status_code == 302
with app.app_context():
    assert db.session.get(User, bob_id).role == "buyer"
assert b"clearer bio" in bob_client.get("/become-vendor/apply").data
bob_form["bio"] = "Phone repairs, screen protectors and chargers, with a one-week warranty."
assert bob_client.post("/become-vendor/apply", data=bob_form).status_code == 302
with app.app_context():
    apps = VendorApplication.query.filter_by(user_id=bob_id).all()
    assert len(apps) == 1 and apps[0].status == "pending" and apps[0].rejection_reason is None
# an already-reviewed application can't be re-decided
assert admin_client.post(f"/admin/applications/{ada_app_id}/reject").status_code == 302
with app.app_context():
    assert db.session.get(VendorApplication, ada_app_id).status == "approved"
print("reject -> resubmit works")

# 9. An unverified student is bounced, and approval is blocked at the model level too.
with app.app_context():
    bingham = db.session.get(University, bingham_id)
    admin = db.session.get(User, admin_id)
    dave = User(university=bingham, name="Dave", email="dave@gmail.com")
    dave.set_password("password123")
    db.session.add(dave)
    db.session.flush()
    dave_app = VendorApplication(user=dave, business_name="Dave Co", category_id=food_id,
                                 bio="x" * 30, phone_number="+2348022222222")
    db.session.add(dave_app)
    db.session.commit()
    try:
        dave_app.approve(admin)
        raise SystemExit("FAIL: approved a vendor without a verified school email")
    except ValueError:
        print("unverified vendor approval blocked")

# 10. Admins only manage their own university.
with app.app_context():
    other = University(name="Other University", short_name="OTHER", email_domain="other.edu.ng")
    zed = User(university=other, name="Zed", email="zed@gmail.com")
    zed.set_password("password123")
    zed.mark_school_email_verified("zed@other.edu.ng", "20/1")
    db.session.add_all([other, zed])
    db.session.flush()
    zed_app = VendorApplication(user=zed, business_name="Zed Wears", category_id=food_id,
                                bio="x" * 30, phone_number="+2348033333333")
    db.session.add(zed_app)
    db.session.commit()
    zed_app_id = zed_app.id
assert admin_client.post(f"/admin/applications/{zed_app_id}/approve").status_code == 404
assert b"Zed Wears" not in admin_client.get("/admin/applications?status=all").data
print("cross-university access blocked")

# 11. Same matric number at the same university must collide.
with app.app_context():
    bingham = db.session.get(University, bingham_id)
    dup = User(university=bingham, name="Dup", email="dup@gmail.com", matric_number="19/0001")
    dup.set_password("x")
    db.session.add(dup)
    try:
        db.session.commit()
        raise SystemExit("FAIL: duplicate matric allowed")
    except IntegrityError:
        db.session.rollback()
        print("duplicate matric rejected")

# 12. Direct vendor signup: school email first, account only exists after the link is clicked.
fresh = app.test_client()
before = len(outbox)
r = fresh.post("/register/vendor", data=dict(
    name="Chidi", university_id=bingham_id, school_email="chidi@gmail.com", matric_number="20/0003", password="password123", confirm_password="password123", hostel_id=boys_hostel_id, floor="2", room_number="C3"))
assert r.status_code == 200 and len(outbox) == before  # non-school email refused
r = fresh.post("/register/vendor", data=dict(
    name="Chidi", university_id=bingham_id, school_email="chidi@binghamuni.edu.ng", matric_number="20/0003", password="password123", confirm_password="password123", hostel_id=boys_hostel_id, floor="2", room_number="C3"))
assert r.status_code == 200 and len(outbox) == before + 1
with app.app_context():
    assert User.query.filter_by(email="chidi@binghamuni.edu.ng").count() == 0  # nothing created yet
signup_token = re.search(r"/register/vendor/confirm/(\S+)", outbox[-1]["body"]).group(1)
assert fresh.get("/register/vendor/confirm/garbage").status_code == 200
assert fresh.get(f"/register/vendor/confirm/{signup_token}").status_code == 200
with app.app_context():
    assert User.query.filter_by(email="chidi@binghamuni.edu.ng").count() == 0
r = fresh.post(f"/register/vendor/confirm/{signup_token}", data={})
assert r.status_code == 302 and r.headers["Location"].endswith("/become-vendor/apply")
with app.app_context():
    chidi = User.query.filter_by(email="chidi@binghamuni.edu.ng").one()
    assert chidi.role == "buyer" and chidi.has_verified_school_email and chidi.can_apply_to_be_vendor
assert fresh.get("/become-vendor/apply").status_code == 200  # straight into the application form
print("direct vendor signup works")

# 13. Product listings with real image uploads.
import io, os
from PIL import Image


def png_bytes(size=(3000, 2000), color=(200, 40, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def listing(title="Jollof rice", **extra):
    data = dict(title=title, category_id=food_id, price="1,500", description="Tasty", stock="12", **extra)
    return data


ada_client = login("ada@gmail.com", "password123")
upload_dir = app.config["UPLOAD_FOLDER"]

assert fresh.get("/vendor/products").status_code == 302          # buyer (Chidi) is bounced
assert app.test_client().get("/vendor/products").status_code == 302  # anonymous too
assert ada_client.get("/vendor/products").status_code == 200
assert ada_client.get("/vendor/products/new").status_code == 200

# a product needs a photo
r = ada_client.post("/vendor/products/new", data=listing(), content_type="multipart/form-data")
assert r.status_code == 200
# a fake image (text renamed .jpg) is refused and nothing is saved
r = ada_client.post("/vendor/products/new", data={**listing(), "images": (io.BytesIO(b"not an image"), "x.jpg")},
                    content_type="multipart/form-data")
assert r.status_code == 200 and not os.listdir(upload_dir)
# bad price
r = ada_client.post("/vendor/products/new", data={**listing(), "price": "free",
                    "images": (io.BytesIO(png_bytes((50, 50))), "a.png")}, content_type="multipart/form-data")
assert r.status_code == 200 and not os.listdir(upload_dir)
with app.app_context():
    assert Product.query.filter_by(vendor_id=ada_id, title="Jollof rice").count() == 0

# a real, large photo is accepted, shrunk and converted to WebP (+ thumbnail)
r = ada_client.post("/vendor/products/new", data={**listing(), "images": (io.BytesIO(png_bytes()), "big.png")},
                    content_type="multipart/form-data")
assert r.status_code == 302
with app.app_context():
    jollof = Product.query.filter_by(vendor_id=ada_id, title="Jollof rice").one()
    assert str(jollof.price) == "1500.00" and len(jollof.images) == 1
    key = jollof.images[0].storage_key
    jollof_id, image_id = jollof.id, jollof.images[0].id
with Image.open(os.path.join(upload_dir, key)) as full:
    assert full.format == "WEBP" and max(full.size) == 1600
with Image.open(os.path.join(upload_dir, key.replace(".webp", "_t.webp"))) as thumb:
    assert max(thumb.size) == 480
assert ada_client.get(f"/media/{key}").status_code == 200
print("image upload + processing works")

# a service may have no photos; too many photos is refused
r = ada_client.post("/vendor/products/new", data=listing("Math tutoring", is_service="on"),
                    content_type="multipart/form-data")
assert r.status_code == 302
files_before = len(os.listdir(upload_dir))
six = [(io.BytesIO(png_bytes((60, 60))), f"{i}.png") for i in range(6)]
r = ada_client.post("/vendor/products/new", data={**listing("Too many"), "images": six},
                    content_type="multipart/form-data")
assert r.status_code == 200 and len(os.listdir(upload_dir)) == files_before

# edit: change price, add a photo; can't strip the last photo from a product
r = ada_client.post(f"/vendor/products/{jollof_id}/edit",
                    data={**listing(), "price": "2000", "is_available": "on",
                          "images": (io.BytesIO(png_bytes((400, 300))), "two.png")},
                    content_type="multipart/form-data")
assert r.status_code == 302
with app.app_context():
    jollof = db.session.get(Product, jollof_id)
    assert str(jollof.price) == "2000.00" and len(jollof.images) == 2
assert ada_client.post(f"/vendor/products/{jollof_id}/images/{image_id}/delete").status_code == 302
with app.app_context():
    assert len(db.session.get(Product, jollof_id).images) == 1
assert not os.path.exists(os.path.join(upload_dir, key))  # removed file is gone from disk
with app.app_context():
    last_image_id = db.session.get(Product, jollof_id).images[0].id
ada_client.post(f"/vendor/products/{jollof_id}/images/{last_image_id}/delete")
with app.app_context():
    assert len(db.session.get(Product, jollof_id).images) == 1  # last photo protected

# sold-out toggle
ada_client.post(f"/vendor/products/{jollof_id}/toggle")
with app.app_context():
    assert db.session.get(Product, jollof_id).is_available is False

# ownership: another vendor cannot touch Ada's listing
admin_client.post(f"/admin/applications/{bob_app_id}/approve")  # Bob becomes a vendor
bob_client = login("bob@gmail.com", "password123")
assert bob_client.get(f"/vendor/products/{jollof_id}/edit").status_code == 404
assert bob_client.post(f"/vendor/products/{jollof_id}/delete").status_code == 404
assert bob_client.post(f"/vendor/products/{jollof_id}/toggle").status_code == 404

# delete removes the listing and its files
with app.app_context():
    keys = [i.storage_key for i in db.session.get(Product, jollof_id).images]
assert ada_client.post(f"/vendor/products/{jollof_id}/delete").status_code == 302
with app.app_context():
    assert db.session.get(Product, jollof_id) is None
    assert ProductImage.query.filter(ProductImage.storage_key.in_(keys)).count() == 0
assert all(not os.path.exists(os.path.join(upload_dir, k)) for k in keys)
print("listing edit / ownership / delete works")

print("ALL CHECKS PASSED")
