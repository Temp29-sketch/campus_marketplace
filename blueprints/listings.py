"""Vendor product/service listings with photo uploads.

Rules
- Only approved vendors can list; a vendor can only touch their own listings.
- Physical products need at least one photo; services may have none.
- Photos are validated and processed BEFORE anything is saved, and any files
  written are cleaned up if the database save fails, so no orphaned uploads.
"""
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user

from extensions import db, login_manager
from models import ROLE_BUYER, Category, Product, ProductImage
from services.images import ImageError
from services.storage import delete_image_files, save_image

listings_bp = Blueprint("listings", __name__, url_prefix="/vendor")

TITLE_MAX = 150
DESCRIPTION_MAX = 2000
PRICE_MAX = Decimal("10000000")  # N10m
STOCK_MAX = 100000


def vendor_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_vendor:
            flash("Only approved vendors can list products.", "error")
            return redirect(url_for("vendor.apply" if current_user.role == ROLE_BUYER else "index"))
        return view(*args, **kwargs)

    return wrapped


def _own_product_or_404(product_id):
    product = db.session.get(Product, product_id)
    if product is None or product.vendor_id != current_user.id:
        abort(404)
    return product


def _read_fields(form):
    """Validate the text fields. Returns (fields, error)."""
    title = form.get("title", "").strip()
    description = form.get("description", "").strip()
    raw_price = form.get("price", "").replace(",", "").replace("₦", "").strip()
    is_service = "is_service" in form

    try:
        category = db.session.get(Category, int(form.get("category_id", "")))
    except ValueError:
        category = None
    try:
        price = Decimal(raw_price).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        price = None

    raw_stock = form.get("stock", "").strip()
    stock = None
    stock_error = None
    if raw_stock:
        if not raw_stock.isdigit():
            stock_error = "Stock quantity must be a whole number from 0 to 100000."
        else:
            stock = int(raw_stock)
            if stock > STOCK_MAX:
                stock_error = "Stock quantity must be between 0 and 100000."
    elif not is_service:
        stock_error = "Stock quantity is required for products."

    error = stock_error
    if not error and not (2 <= len(title) <= TITLE_MAX):
        error = f"Title must be 2-{TITLE_MAX} characters."
    elif len(description) > DESCRIPTION_MAX:
        error = f"Description is too long (max {DESCRIPTION_MAX} characters)."
    elif category is None:
        error = "Please choose a category."
    elif price is None or not price.is_finite() or not (0 < price <= PRICE_MAX):
        error = "Enter a valid price in naira, e.g. 2500."

    fields = dict(title=title, description=description, price=price, category=category, is_service=is_service, stock=stock)
    return fields, error


def _uploaded_files():
    return [f for f in request.files.getlist("images") if f and f.filename]


def _save_images(files):
    """Process + store every file or none (cleans up on failure)."""
    keys = []
    try:
        for f in files:
            keys.append(save_image(f))
    except Exception:
        delete_image_files(keys)
        raise
    return keys


def _commit_or_cleanup(keys):
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        delete_image_files(keys)
        raise


def _form_defaults(product):
    return {
        "title": product.title,
        "description": product.description,
        "price": str(product.price),
        "category_id": str(product.category_id),
        "is_service": "on" if product.is_service else "",
        "is_available": "on" if product.is_available else "",
        "stock": "" if product.stock is None else str(product.stock),
    }


@listings_bp.route("/products")
@vendor_required
def my_products():
    products = (
        Product.query.filter_by(vendor_id=current_user.id).order_by(Product.created_at.desc()).all()
    )
    return render_template("vendor/products.html", products=products)


@listings_bp.route("/products/new", methods=["GET", "POST"])
@vendor_required
def new_product():
    categories = Category.query.order_by(Category.name).all()

    if request.method == "POST":
        fields, error = _read_fields(request.form)
        files = _uploaded_files()
        limit = current_app.config["MAX_IMAGES_PER_PRODUCT"]

        if not error and len(files) > limit:
            error = f"You can upload up to {limit} photos."
        if not error and not files and not fields["is_service"]:
            error = "Add at least one photo of your product."

        keys = []
        if not error:
            try:
                keys = _save_images(files)
            except ImageError as exc:
                error = str(exc)

        if error:
            flash(error, "error")
        else:
            product = Product(
                vendor=current_user._get_current_object(),
                category=fields["category"],
                title=fields["title"],
                description=fields["description"],
                price=fields["price"],
                is_service=fields["is_service"],
                stock=fields["stock"],
                is_available=fields["stock"] != 0,
            )
            product.images = [ProductImage(storage_key=k, position=i) for i, k in enumerate(keys)]
            db.session.add(product)
            _commit_or_cleanup(keys)
            flash("Listing published.", "success")
            return redirect(url_for("listings.my_products"))

    return render_template("vendor/product_form.html", product=None, categories=categories, form=request.form)


@listings_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@vendor_required
def edit_product(product_id):
    product = _own_product_or_404(product_id)
    categories = Category.query.order_by(Category.name).all()
    form = _form_defaults(product)

    if request.method == "POST":
        form = request.form
        fields, error = _read_fields(form)
        files = _uploaded_files()
        limit = current_app.config["MAX_IMAGES_PER_PRODUCT"]
        total = len(product.images) + len(files)

        if not error and total > limit:
            error = f"A listing can have up to {limit} photos (you have {len(product.images)})."
        if not error and total == 0 and not fields["is_service"]:
            error = "A product needs at least one photo."

        keys = []
        if not error:
            try:
                keys = _save_images(files)
            except ImageError as exc:
                error = str(exc)

        if error:
            flash(error, "error")
        else:
            product.title = fields["title"]
            product.description = fields["description"]
            product.price = fields["price"]
            product.category = fields["category"]
            was_sold_out = product.stock == 0
            product.is_service = fields["is_service"]
            product.stock = fields["stock"]
            product.is_available = False if product.stock == 0 else (
                True if was_sold_out and product.stock is not None and product.stock > 0 else "is_available" in form
            )
            start = max((i.position for i in product.images), default=-1) + 1
            for offset, key in enumerate(keys):
                product.images.append(ProductImage(storage_key=key, position=start + offset))
            _commit_or_cleanup(keys)
            flash("Listing updated.", "success")
            return redirect(url_for("listings.my_products"))

    return render_template("vendor/product_form.html", product=product, categories=categories, form=form)


@listings_bp.route("/products/<int:product_id>/toggle", methods=["POST"])
@vendor_required
def toggle_availability(product_id):
    product = _own_product_or_404(product_id)
    if not product.is_available and product.stock == 0:
        flash("Add stock first.", "error")
        return redirect(url_for("listings.my_products"))
    product.is_available = not product.is_available
    db.session.commit()
    flash("Marked as available." if product.is_available else "Marked as sold out.", "success")
    return redirect(url_for("listings.my_products"))


@listings_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@vendor_required
def delete_product(product_id):
    product = _own_product_or_404(product_id)
    keys = [i.storage_key for i in product.images]
    db.session.delete(product)
    db.session.commit()
    delete_image_files(keys)  # only after the DB delete succeeded
    flash("Listing deleted.", "success")
    return redirect(url_for("listings.my_products"))


@listings_bp.route("/products/<int:product_id>/images/<int:image_id>/delete", methods=["POST"])
@vendor_required
def remove_image(product_id, image_id):
    product = _own_product_or_404(product_id)
    image = next((i for i in product.images if i.id == image_id), None)
    if image is None:
        abort(404)

    if len(product.images) <= 1 and not product.is_service:
        flash("A product needs at least one photo. Upload a new one first, then remove this one.", "error")
    else:
        key = image.storage_key
        product.images.remove(image)  # delete-orphan cascade removes the row
        db.session.commit()
        delete_image_files([key])
        flash("Photo removed.", "success")
    return redirect(url_for("listings.edit_product", product_id=product.id))
