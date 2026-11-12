"""Shared public pages, lightweight cart placeholder and authenticated lists."""
from decimal import Decimal, ROUND_HALF_UP

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import APP_APPROVED, CartItem, Order, OrderItem, Product, ROLE_VENDOR, ROLE_BUYER, User, VendorApplication, WishlistItem, ORDER_PAID, AdRequest
from services.mail import notify
from services.notifications import notify_admins
from services.storage import save_banner, delete_image_files
import re
from services.hostels import hostel_options
from sqlalchemy.orm import joinedload

pages_bp = Blueprint("pages", __name__)


def _public_product(product_id):
    query = (
        db.session.query(Product, User)
        .join(User, Product.vendor_id == User.id)
        .join(VendorApplication, VendorApplication.user_id == User.id)
        .filter(User.role == ROLE_VENDOR, User.is_active_account.is_(True), VendorApplication.status == APP_APPROVED,
                Product.id == product_id, Product.is_removed.is_(False))
    )
    if current_user.is_authenticated:
        query = query.filter(User.university_id == current_user.university_id)
    row = query.first()
    return row[0] if row else None


@pages_bp.post("/wishlist/toggle/<int:product_id>")
@limiter.limit("60 per minute")
def wishlist_toggle(product_id):
    wants_json = request.accept_mimetypes.best == "application/json"
    if not current_user.is_authenticated:
        if wants_json: return jsonify(error="Please log in to save listings."), 401
        return redirect(url_for("auth.login", next=(request.referrer or url_for("pages.wishlist")) + f"#product-{product_id}"))
    item = WishlistItem.query.filter_by(user_id=current_user.id, product_id=product_id).first()
    if item:
        db.session.delete(item)
        saved = False
    else:
        if _public_product(product_id) is None:
            abort(404)
        db.session.add(WishlistItem(user_id=current_user.id, product_id=product_id))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        saved = True
    if not item:
        # The insert path commits above; a duplicate race is still treated as saved.
        return jsonify(saved=saved) if wants_json else redirect((request.referrer or url_for("index")) + f"#product-{product_id}")
    db.session.commit()
    return jsonify(saved=saved) if wants_json else redirect((request.referrer or url_for("index")) + f"#product-{product_id}")


@pages_bp.get("/wishlist")
@login_required
def wishlist():
    saved = WishlistItem.query.filter_by(user_id=current_user.id).order_by(WishlistItem.created_at.desc()).all()
    rows = []
    for item in saved:
        product = db.session.get(Product, item.product_id)
        if product is None or product.is_removed:
            continue
        vendor = db.session.get(User, product.vendor_id)
        application = vendor.vendor_application if vendor else None
        if not vendor or vendor.role != ROLE_VENDOR or not application or application.status != APP_APPROVED:
            continue
        rows.append((item, product, bool(product.is_available)))
    saved_product_ids = {product.id for _item, product, _available in rows}
    return render_template("pages/wishlist.html", wishlist_rows=rows, saved_product_ids=saved_product_ids, new_product_ids=set())


@pages_bp.get("/orders")
@login_required
def orders():
    rows = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("pages/orders.html", orders=rows)


@pages_bp.get("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.filter_by(id=order_id, buyer_id=current_user.id).first_or_404()
    from services.orders import is_confirmed, is_overdue
    return render_template("pages/order_detail.html", order=order, is_confirmed=is_confirmed, is_overdue=is_overdue)


def _price_kobo(product):
    return int((Decimal(product.price) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _cart_limit(product):
    if product.is_service:
        return 1
    return min(product.stock, 100000) if product.stock is not None else 10


def _cart_product(product_id):
    product = _public_product(product_id)
    if product is None or product.vendor_id == current_user.id:
        return None
    return product


def _cart_groups():
    items = CartItem.query.filter_by(user_id=current_user.id).order_by(CartItem.created_at).all()
    groups = {}
    for item in items:
        product = item.product
        vendor = product.vendor if product else None
        app = vendor.vendor_application if vendor else None
        problem = None
        if product is None or product.is_removed or vendor is None or vendor.role != ROLE_VENDOR or not vendor.is_active_account or not app or app.status != APP_APPROVED:
            problem = "This listing was removed."
        elif vendor.university_id != current_user.university_id:
            problem = "This listing is outside your campus."
        elif not product.is_available or (product.stock is not None and product.stock < 1):
            problem = "This listing is sold out."
        elif item.unit_price_kobo != _price_kobo(product):
            problem = "The price changed. Remove and add the listing again to use the new price."
        elif item.quantity < 1 or item.quantity > _cart_limit(product):
            problem = "The requested quantity is no longer available."
        group_key = vendor.id if vendor else -item.id
        group = groups.setdefault(group_key, {"vendor": vendor, "business_name": app.business_name if app else "Unavailable vendor", "items": [], "subtotal_kobo": 0})
        group["items"].append({"item": item, "product": product, "problem": problem})
        if product:
            group["subtotal_kobo"] += item.quantity * _price_kobo(product)
    return list(groups.values())


@pages_bp.route("/cart", methods=["GET"])
@login_required
def cart():
    groups = _cart_groups()
    return render_template("pages/cart.html", groups=groups, has_problems=any(line["problem"] for g in groups for line in g["items"]))


@pages_bp.post("/cart/add/<int:product_id>")
@limiter.limit("60 per minute")
def cart_add(product_id):
    wants_json = request.accept_mimetypes.best == "application/json"
    if not current_user.is_authenticated:
        if wants_json: return jsonify(error="Please log in to add listings to your cart."), 401
        return redirect(url_for("auth.login", next=(request.referrer or url_for("index")) + f"#product-{product_id}"))
    product = _cart_product(product_id)
    if product is None:
        abort(404)
    if not product.is_available or (product.stock is not None and product.stock < 1):
        if wants_json: return jsonify(error="This listing is sold out."), 409
        flash("This listing is sold out.", "error")
        return redirect((request.referrer or url_for("index")) + f"#product-{product_id}")
    limit = _cart_limit(product)
    item = CartItem.query.filter_by(user_id=current_user.id, product_id=product.id).first()
    if item:
        item.unit_price_kobo = _price_kobo(product)
        item.quantity = min(limit, item.quantity + 1)
        flash("Quantity updated in your cart." if item.quantity < limit else f"Cart quantity is limited to {limit}.", "success")
    else:
        db.session.add(CartItem(user_id=current_user.id, product_id=product.id, quantity=1, unit_price_kobo=_price_kobo(product)))
        flash("Added to your cart.", "success")
    db.session.commit()
    cart_count = sum(row.quantity for row in CartItem.query.filter_by(user_id=current_user.id).all())
    return jsonify(cart_count=cart_count) if wants_json else redirect((request.referrer or url_for("index")) + f"#product-{product_id}")


@pages_bp.post("/cart/update/<int:product_id>")
@login_required
@limiter.limit("60 per minute")
def cart_update(product_id):
    item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first_or_404()
    if request.form.get("refresh_price") == "1" and item.product and not item.product.is_removed:
        item.unit_price_kobo = _price_kobo(item.product)
        flash("Price updated. Review your cart before checkout.", "success")
    else:
        try: quantity = int(request.form.get("quantity", ""))
        except (TypeError, ValueError): quantity = 0
        product = item.product
        if product is None or quantity < 1 or quantity > _cart_limit(product):
            flash("Enter a quantity within the available limit.", "error")
        else:
            item.quantity = quantity
            flash("Cart updated.", "success")
    db.session.commit()
    return redirect(url_for("pages.cart"))


@pages_bp.post("/cart/remove/<int:product_id>")
@login_required
@limiter.limit("60 per minute")
def cart_remove(product_id):
    item = CartItem.query.filter_by(user_id=current_user.id, product_id=product_id).first_or_404()
    db.session.delete(item); db.session.commit()
    flash("Removed from your cart.", "success")
    return redirect(url_for("pages.cart"))


@pages_bp.get("/how-it-works")
def how_it_works():
    return render_template("pages/info.html", title="How it works", page="how-it-works")


@pages_bp.get("/seller-guidelines")
def seller_guidelines():
    return render_template("pages/info.html", title="Seller guidelines", page="seller-guidelines")


@pages_bp.get("/terms")
def terms():
    return render_template("pages/info.html", title="Terms", page="terms")


@pages_bp.get("/privacy")
def privacy():
    return render_template("pages/info.html", title="Privacy", page="privacy")


@pages_bp.get("/help")
def help_page():
    return render_template("pages/info.html", title="Help", page="help", support_email=current_app.config["SUPPORT_EMAIL"])


def _ad_role_required():
    if current_user.role not in (ROLE_BUYER, ROLE_VENDOR):
        abort(403)


@pages_bp.route("/advertise", methods=["GET", "POST"])
@login_required
@limiter.limit("3 per hour", key_func=lambda: f"advertise:{current_user.get_id()}")
def advertise():
    _ad_role_required()
    if request.method == "GET":
        return render_template("pages/advertise.html")
    business = request.form.get("business_name", "").strip()
    contact = request.form.get("contact_name", "").strip()
    phone_raw = re.sub(r"[\s().-]", "", request.form.get("phone", ""))
    if phone_raw.startswith("0") and len(phone_raw) == 11:
        phone_raw = "+234" + phone_raw[1:]
    elif phone_raw.startswith("234"):
        phone_raw = "+" + phone_raw
    email = request.form.get("email", "").strip().lower()
    message = request.form.get("message", "").strip()
    upload = request.files.get("image")
    if request.form.get("website", "").strip():
        flash("We could not submit that request. Please try again.", "error")
    elif not 2 <= len(business) <= 150 or not 2 <= len(contact) <= 120:
        flash("Enter a business name and contact name.", "error")
    elif not re.fullmatch(r"\+234[789]\d{9}", phone_raw):
        flash("Enter a valid Nigerian phone number.", "error")
    elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("Enter a valid email address.", "error")
    elif not 1 <= len(message) <= 1000:
        flash("Add a message of up to 1,000 characters.", "error")
    else:
        image_key = None
        try:
            if upload and upload.filename:
                image_key = save_banner(upload)
            row = AdRequest(user_id=current_user.id, university_id=current_user.university_id,
                            business_name=business, contact_name=contact, phone=phone_raw,
                            email=email, message=message, image_key=image_key)
            db.session.add(row)
            db.session.commit()
        except Exception:
            db.session.rollback()
            if image_key:
                delete_image_files([image_key])
            current_app.logger.exception("Could not save advertiser request")
            flash("We could not submit your request. Please try again.", "error")
        else:
            link = f"/admin/ad-requests/{row.id}"
            notify_admins(row.university_id, "ad_request", "New advertising request", f"{business} sent an advertising request.", link)
            notify(current_app.config["SUPPORT_EMAIL"], "New UniHub advertising request", f"A new advertising request was submitted for {row.university.name}. Review it in the admin dashboard.")
            notify(email, "We received your advertising request", "Thanks, we will contact you. We will contact you with pricing and payment details.")
            return redirect(url_for("pages.advertise_thanks"))
    return render_template("pages/advertise.html"), 400


@pages_bp.get("/advertise/thanks")
@login_required
def advertise_thanks():
    _ad_role_required()
    return render_template("pages/advertise_success.html")
