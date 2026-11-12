"""Public discovery feed, listing pages and vendor storefronts.

What shoppers can see
- Only AVAILABLE listings from vendors who are currently approved.
- Logged-in users see their own university's listings. Anonymous visitors see
  the only active university's listings, or everything if several are active.
- Vendor phone numbers are never rendered in public catalog pages.

The feed works as a plain GET form (no JavaScript needed). static/catalog.js
upgrades it: it fetches just the results (`partial=1`) as you type or click.
"""
from flask import Blueprint, abort, current_app, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import or_
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from extensions import db, limiter
from models import APP_APPROVED, ROLE_VENDOR, Banner, Category, Product, University, User, VendorApplication, WishlistItem, utcnow
from services.search import clean_query, like_pattern, parse_terms

catalog_bp = Blueprint("catalog", __name__)

PER_PAGE = 24
STORE_LIMIT = 100
PRODUCT_TYPES = ("product", "service")
SORTS = {
    "new": (Product.created_at.desc(), Product.id.desc()),
    "price_asc": (Product.price.asc(), Product.id.desc()),
    "price_desc": (Product.price.desc(), Product.id.desc()),
}


# --- queries --------------------------------------------------------------------
def _university_scope():
    if current_user.is_authenticated:
        return current_user.university_id
    active = db.session.scalars(
        db.select(University.id).where(University.is_active.is_(True)).limit(2)
    ).all()
    return active[0] if len(active) == 1 else None


def _visible_products(university_id):
    """Listings from currently-approved vendors. A demoted vendor's items vanish,
    and so do listings an admin removed."""
    query = (
        Product.query.join(User, Product.vendor_id == User.id)
        .join(VendorApplication, VendorApplication.user_id == User.id)
        .filter(
            User.role == ROLE_VENDOR,
            User.is_active_account.is_(True),
            VendorApplication.status == APP_APPROVED,
            Product.is_removed.is_(False),
        )
        .options(
            contains_eager(Product.vendor).contains_eager(User.vendor_application),
            selectinload(Product.images),
            joinedload(Product.category),
        )
    )
    if university_id:
        query = query.filter(User.university_id == university_id)
    return query


# --- pages ------------------------------------------------------------------------
@limiter.limit("120 per minute")  # live search fires a request per pause in typing
def feed():
    """Registered as the home page ('index') in app.py."""
    q = clean_query(request.args.get("q"))
    categories = Category.query.order_by(Category.name).all()
    category_by_slug = {c.slug: c for c in categories}
    category = next((c for c in categories if c.slug == request.args.get("category", "")), None)
    ptype = ""
    sort = request.args.get("sort", "new")
    sort = sort if sort in SORTS else "new"
    page = max(request.args.get("page", 1, type=int), 1)

    query = _visible_products(_university_scope()).filter(Product.is_available.is_(True))
    if category:
        query = query.filter(Product.category_id == category.id)
    for term in parse_terms(q):
        pattern = like_pattern(term)
        query = query.filter(
            or_(
                Product.title.ilike(pattern, escape="\\"),
                Product.description.ilike(pattern, escape="\\"),
                VendorApplication.business_name.ilike(pattern, escape="\\"),
            )
        )
    pagination = query.order_by(*SORTS[sort]).paginate(page=page, per_page=PER_PAGE, error_out=False)
    now = utcnow()
    def recent(product):
        created = product.created_at
        if created and created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        return bool(created and (now - created).total_seconds() <= 48 * 60 * 60)
    new_product_ids = {p.id for p in pagination.items if recent(p)}
    saved_product_ids = set()
    if current_user.is_authenticated and pagination.items:
        saved_product_ids = set(db.session.scalars(
            db.select(WishlistItem.product_id).where(
                WishlistItem.user_id == current_user.id,
                WishlistItem.product_id.in_([p.id for p in pagination.items]),
            )
        ).all())

    args = {"q": q, "category": category.slug if category else "", "type": ptype,
            "sort": sort if sort != "new" else ""}

    def link(**overrides):
        """URL for this feed with some filters changed; empty values are dropped."""
        params = {k: v for k, v in {**args, **overrides}.items() if v}
        return url_for("index", **params)

    banner_query = Banner.query.filter(Banner.is_active.is_(True), or_(Banner.starts_at.is_(None), Banner.starts_at <= now), or_(Banner.ends_at.is_(None), Banner.ends_at >= now))
    campus = _university_scope()
    if campus:
        banner_query = banner_query.filter(or_(Banner.university_id == campus, Banner.university_id.is_(None)))
    banners = banner_query.order_by(Banner.position, Banner.id).all()
    context = dict(
        products=pagination.items, pagination=pagination, link=link, q=q,
        categories=categories, category_by_slug=category_by_slug,
        active_category=args["category"], ptype=ptype, sort=sort,
        banners=banners, new_product_ids=new_product_ids, saved_product_ids=saved_product_ids,
    )
    template = "catalog/_results.html" if request.args.get("partial") else "catalog/index.html"
    return render_template(template, **context)


@catalog_bp.route("/item/<int:product_id>")
def item(product_id):
    product = _visible_products(_university_scope()).filter(Product.id == product_id).first_or_404()
    is_saved = current_user.is_authenticated and WishlistItem.query.filter_by(user_id=current_user.id, product_id=product_id).first() is not None
    return render_template("catalog/detail.html", product=product, is_saved=is_saved)


@catalog_bp.route("/store/<int:vendor_id>")
def store(vendor_id):
    vendor = _visible_products(_university_scope()).filter(Product.vendor_id == vendor_id).first()
    if vendor is None:
        # No visible listings: still show the storefront if the vendor is approved and in scope.
        user = db.session.get(User, vendor_id)
        application = user.vendor_application if user else None
        scope = _university_scope()
        if not (user and user.is_vendor and user.is_active_account and application and application.status == APP_APPROVED) or (
            scope and user.university_id != scope
        ):
            abort(404)
    else:
        user = vendor.vendor

    products = (
        _visible_products(None)
        .filter(Product.vendor_id == user.id, Product.is_available.is_(True))
        .order_by(*SORTS["new"])
        .limit(STORE_LIMIT)
        .all()
    )
    saved_product_ids = set()
    if current_user.is_authenticated and products:
        saved_product_ids = set(db.session.scalars(db.select(WishlistItem.product_id).where(
            WishlistItem.user_id == current_user.id, WishlistItem.product_id.in_([p.id for p in products])
        )).all())
    return render_template("catalog/store.html", vendor=user, products=products, saved_product_ids=saved_product_ids, new_product_ids=set())
