"""Admin review of vendor applications and reported listings.

Admins only see and decide things from their own university, so each campus
can have its own moderators as the platform grows.

Moderation actions on a report
- dismiss: nothing wrong; closes just that report.
- remove listing: hides the listing (vendor is emailed the reason) and closes
  every open report on it. Reversible with "restore".
- revoke vendor: for repeat or serious offenders. The vendor goes back to being
  a buyer, all their listings disappear from the catalog, and they're emailed the
  reason. They can re-apply, and an admin can approve them again.
"""
from functools import wraps
import re

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import contains_eager, selectinload

from extensions import db, login_manager
from models import (
    AdRequest,
    APP_APPROVED,
    APP_PENDING,
    APP_REJECTED,
    APP_STATUSES,
    REPORT_ACTIONED,
    REPORT_DISMISSED,
    REPORT_OPEN,
    REPORT_STATUSES,
    ROLE_BUYER,
    ROLE_VENDOR,
    Product,
    Banner,
    Notification,
    Order,
    OrderItem,
    ORDER_PAID,
    AdminAuditLog,
    Hostel,
    HostelBlock,
    Report,
    User,
    VendorApplication,
)
from services.mail import notify
from services.notifications import notify_user, notify_admins
from services.storage import save_banner, delete_image_files
from services.images import ImageError
from services import paystack
from services.paystack import PaystackError
from services.hostels import validate_address, hostel_options
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _scoped_application_or_404(application_id):
    application = db.session.get(VendorApplication, application_id)
    if application is None or application.user.university_id != current_user.university_id:
        abort(404)
    return application


def _ensure_payout_account(application, approve_anyway=False):
    if application.paystack_subaccount_code:
        return None
    if not current_app.config.get("PAYSTACK_VERIFY_BANK"):
        return None
    if not current_app.config.get("PAYSTACK_SECRET_KEY"):
        raise PaystackError("Paystack keys are not configured.")
    resolved = paystack.resolve_account(application.account_number, application.bank_code)
    returned_name = str(resolved.get("account_name") or "").strip()
    expected_name = application.account_name or application.user.name
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    mismatch = returned_name if returned_name and normalize(returned_name) != normalize(expected_name) else None
    result = paystack.create_subaccount(application)
    code = result.get("subaccount_code")
    if not code:
        raise PaystackError("Paystack did not return a payout account code. Please retry.")
    application.paystack_subaccount_code = str(code)
    return None


@admin_bp.route("/", strict_slashes=False)
@admin_required
def dashboard():
    university_id = current_user.university_id
    app_counts = dict(db.session.query(VendorApplication.status, func.count(VendorApplication.id)).join(User, VendorApplication.user_id == User.id).filter(User.university_id == university_id).group_by(VendorApplication.status).all())
    order_statuses = dict(db.session.query(OrderItem.fulfilment_status, func.count(OrderItem.id)).join(Order).filter(
        Order.university_id == university_id, Order.status == ORDER_PAID).group_by(OrderItem.fulfilment_status).all())
    return render_template("admin/dashboard.html", counts={
        "applications": app_counts.get(APP_PENDING, 0),
        "reports": Report.query.join(Product).join(User, Product.vendor_id == User.id).filter(User.university_id == university_id, Report.status == REPORT_OPEN).count(),
        "vendors": User.query.filter_by(university_id=university_id, role=ROLE_VENDOR, is_active_account=True).count(),
        "buyers": User.query.filter_by(university_id=university_id, role=ROLE_BUYER).count(),
        "listings": Product.query.join(User, Product.vendor_id == User.id).filter(User.university_id == university_id, User.is_active_account.is_(True), Product.is_removed.is_(False), Product.is_available.is_(True)).count(),
        "banners": Banner.query.filter((Banner.university_id == university_id) | Banner.university_id.is_(None), Banner.is_active.is_(True)).count(),
        "orders": Order.query.filter(Order.university_id == university_id, Order.status == ORDER_PAID, Order.created_at >= datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)).count(),
    }, order_statuses=order_statuses)


@admin_bp.route("/orders")
@admin_required
def orders():
    status = request.args.get("status", "all")
    allowed = {"all", "new", "preparing", "ready", "delivered", "cancelled"}
    if status not in allowed: status = "all"
    base = Order.query.filter_by(university_id=current_user.university_id, status=ORDER_PAID)
    counts = dict(db.session.query(OrderItem.fulfilment_status, func.count(OrderItem.id)).join(Order).filter(
        Order.university_id == current_user.university_id, Order.status == ORDER_PAID).group_by(OrderItem.fulfilment_status).all())
    query = base.order_by(Order.created_at.desc())
    if status != "all":
        query = query.join(OrderItem).filter(OrderItem.fulfilment_status == status).distinct()
    from services.orders import is_confirmed, is_overdue
    return render_template("admin/orders.html", orders=query.all(), status=status, counts=counts,
                           statuses=["all", "new", "preparing", "ready", "delivered", "cancelled"],
                           is_confirmed=is_confirmed, is_overdue=is_overdue)


@admin_bp.post("/orders/<int:order_id>/items/<int:item_id>/status")
@admin_required
def order_item_status(order_id, item_id):
    order = Order.query.filter_by(id=order_id, university_id=current_user.university_id, status=ORDER_PAID).first_or_404()
    item = OrderItem.query.filter_by(id=item_id, order_id=order.id).first_or_404()
    target = request.form.get("status")
    if target not in ("delivered", "cancelled"):
        abort(400)
    if item.fulfilment_status != target:
        item.fulfilment_status = target
        if target == "delivered": item.delivered_at = datetime.now(timezone.utc)
        db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=order.buyer_id,
            action="order_item_status_override", details=f"Order {order.reference} item {item.id}: {target}"))
        db.session.commit()
        buyer = order.buyer
        vendor = item.vendor
        notify_user(buyer, "order_update", "Delivery update", f"{item.title_snapshot} is now {target}.", f"/orders/{order.id}")
        notify_user(vendor, "order_update", f"Order #{order.id} status updated", f"An admin marked {item.title_snapshot} {target}.", "/vendor/orders")
    return redirect(url_for("admin.orders", status=request.args.get("status", "all")))


@admin_bp.get("/buyers")
@admin_required
def buyers():
    query = User.query.filter_by(university_id=current_user.university_id, role=ROLE_BUYER)
    search = request.args.get("q", "").strip()[:100]
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(User.name.ilike(pattern), User.email.ilike(pattern)))
    page = max(request.args.get("page", 1, type=int), 1)
    pagination = query.order_by(User.created_at.desc(), User.id.desc()).paginate(page=page, per_page=24, error_out=False)
    counts = dict(db.session.query(Order.buyer_id, func.count(Order.id)).filter(Order.buyer_id.in_([u.id for u in pagination.items] or [0])).group_by(Order.buyer_id).all())
    return render_template("admin/buyers.html", pagination=pagination, orders_by_buyer=counts, q=search)


def _scoped_buyer(user_id):
    return User.query.filter_by(id=user_id, university_id=current_user.university_id, role=ROLE_BUYER).first_or_404()


@admin_bp.route("/buyers/<int:user_id>", methods=["GET", "POST"])
@admin_required
def buyer_detail(user_id):
    buyer = _scoped_buyer(user_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:120]
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone_number", "").strip()
        if not 1 <= len(name) <= 120:
            flash("Enter a name between 1 and 120 characters.", "error")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.", "error")
        elif User.query.filter(or_(User.email == email, User.school_email == email), User.id != buyer.id).first():
            flash("That email address is already in use.", "error")
        elif phone and not re.fullmatch(r"(?:\+234|0)[789]\d{9}", re.sub(r"[\s().-]", "", phone)):
            flash("Enter a valid Nigerian phone number or leave it blank.", "error")
        else:
            values, address_error = validate_address(buyer.university_id, request.form.get("hostel_id"), request.form.get("hostel_block_id"), request.form.get("floor"), request.form.get("room_number"))
            if address_error:
                flash(address_error, "error")
            else:
                old_email = buyer.email
                edits = []
                for field, value in {"name": name, "email": email, "phone_number": phone or None, **values}.items():
                    old = getattr(buyer, field)
                    if old != value:
                        edits.append(f"{field}: {old!s} -> {value!s}")
                        setattr(buyer, field, value)
                try:
                    if edits:
                        db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=buyer.id, action="buyer_edited", details="; ".join(edits)))
                        db.session.commit()
                        if any(line.startswith("email:") for line in edits): notify(old_email, "Your UniHub email was changed by an admin", "An admin updated the email on your UniHub account.")
                    flash("Buyer details updated.", "success")
                except IntegrityError:
                    db.session.rollback(); flash("That email address is already in use.", "error")
    buyer_orders = Order.query.filter_by(buyer_id=buyer.id).order_by(Order.created_at.desc()).all()
    audit_rows = AdminAuditLog.query.filter_by(target_user_id=buyer.id).order_by(AdminAuditLog.created_at.desc()).limit(30).all()
    return render_template("admin/buyer_detail.html", buyer=buyer, orders=buyer_orders, audit_rows=audit_rows,
                           hostels=hostel_options(buyer.university_id))


@admin_bp.post("/buyers/<int:user_id>/status")
@admin_required
def buyer_status(user_id):
    buyer = _scoped_buyer(user_id)
    active = request.form.get("active") == "1"
    if buyer.is_active_account != active:
        buyer.is_active_account = active
        buyer.session_version += 1
        action = "buyer_reactivated" if active else "buyer_suspended"
        db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=buyer.id, action=action, details=""))
        db.session.commit()
        if not active: notify(buyer.email, "Your UniHub account was suspended", "An admin suspended your account. Contact support if you have questions.")
        flash("Buyer reactivated." if active else "Buyer suspended.", "success")
    return redirect(url_for("admin.buyer_detail", user_id=buyer.id))


@admin_bp.route("/vendors")
@admin_required
def vendors():
    rows = User.query.filter_by(university_id=current_user.university_id, role=ROLE_VENDOR).order_by(User.created_at.desc()).all()
    return render_template("admin/vendors.html", vendors=rows)


@admin_bp.route("/vendors/<int:user_id>")
@admin_required
def vendor_detail(user_id):
    vendor = User.query.filter_by(id=user_id, university_id=current_user.university_id, role=ROLE_VENDOR).first_or_404()
    return render_template("admin/vendor_detail.html", vendor=vendor, payout_mismatch=None,
                           payout_verification_enabled=current_app.config.get("PAYSTACK_VERIFY_BANK", False))


@admin_bp.post("/vendors/<int:user_id>/payout-account")
@admin_required
def vendor_payout_account(user_id):
    vendor = User.query.filter_by(id=user_id, university_id=current_user.university_id, role=ROLE_VENDOR).first_or_404()
    application = vendor.vendor_application
    if not current_app.config.get("PAYSTACK_VERIFY_BANK"):
        flash("Bank verification is switched off.", "error")
        return redirect(url_for("admin.vendor_detail", user_id=vendor.id))
    if application.paystack_subaccount_code:
        flash("This vendor already has a payout account.", "success")
        return redirect(url_for("admin.vendor_detail", user_id=vendor.id))
    try:
        mismatch = _ensure_payout_account(application, request.form.get("approve_anyway") == "1")
        db.session.commit()
    except PaystackError as exc:
        db.session.rollback(); flash(str(exc), "error")
    else:
        if mismatch:
            flash(f"Payout account created, but the account name differs: {mismatch}.", "warning")
        else:
            flash("Payout account created.", "success")
    return redirect(url_for("admin.vendor_detail", user_id=vendor.id))


@admin_bp.post("/vendors/<int:user_id>/revoke")
@admin_required
def vendor_revoke(user_id):
    vendor = User.query.filter_by(id=user_id, university_id=current_user.university_id, role=ROLE_VENDOR).first_or_404()
    reason = request.form.get("reason", "").strip()[:NOTE_MAX]
    if not reason:
        flash("Please give a reason. The vendor will see it.", "error")
        return redirect(url_for("admin.vendor_detail", user_id=vendor.id))
    vendor.vendor_application.reject(current_user._get_current_object(), reason)
    vendor.role = ROLE_BUYER
    db.session.commit()
    notify_user(vendor, "listing_removed", "Vendor access removed", f"Your vendor access was removed: {reason}", url_for("vendor.apply"))
    notify(to=vendor.email, subject="Your vendor access was removed", body=f"Your vendor access was removed. Reason: {reason}")
    flash(f"Revoked vendor access for {vendor.vendor_application.business_name}.", "success")
    return redirect(url_for("admin.vendors"))


def _safe_banner_link(value):
    from urllib.parse import urlparse
    value = (value or "").strip()
    if not value: return None
    parsed = urlparse(value)
    if value.startswith("/") and not value.startswith("//") and "\\" not in value and "\r" not in value and "\n" not in value: return value
    if parsed.scheme in ("http", "https") and parsed.netloc: return value
    return False


@admin_bp.route("/banners")
@admin_required
def banners():
    rows = Banner.query.filter((Banner.university_id == current_user.university_id) | Banner.university_id.is_(None)).order_by(Banner.position, Banner.id).all()
    return render_template("admin/banners.html", banners=rows, new_ad_request_count=AdRequest.query.filter_by(university_id=current_user.university_id, status="new").count())


@admin_bp.route("/banners/new", methods=["GET", "POST"])
@admin_required
def banner_new():
    source_id = request.args.get("ad_request_id", type=int)
    banner = None
    if source_id:
        source = AdRequest.query.filter_by(id=source_id, university_id=current_user.university_id).first_or_404()
        banner = Banner(university_id=current_user.university_id, title=source.business_name, subtitle=source.message[:300], image_key=source.image_key or "")
    return _banner_form(banner)


@admin_bp.route("/banners/<int:banner_id>/edit", methods=["GET", "POST"])
@admin_required
def banner_edit(banner_id):
    banner = Banner.query.filter(Banner.id == banner_id, (Banner.university_id == current_user.university_id) | Banner.university_id.is_(None)).first_or_404()
    return _banner_form(banner)


def _banner_form(banner):
    is_new_record = banner is None or banner.id is None
    start_value, end_value = _banner_local_values(banner)
    fit_mode = banner.fit_mode if banner and banner.fit_mode in {"fill", "fit"} else "fill"
    focus = banner.focus if banner and banner.focus in {"top", "center", "bottom"} else "center"
    if request.method == "POST":
        form = request.form
        fit_mode = form.get("fit_mode", "fill")
        if fit_mode not in {"fill", "fit"}: fit_mode = "fill"
        focus = form.get("focus", "center")
        if focus not in {"top", "center", "bottom"}: focus = "center"
        title, subtitle = form.get("title", "").strip(), form.get("subtitle", "").strip()
        link = _safe_banner_link(form.get("link_url"))
        upload = request.files.get("image")
        start_value, end_value = form.get("starts_at", ""), form.get("ends_at", "")
        starts_at, ends_at = _parse_date(start_value), _parse_date(end_value)
        start_error = "Enter a valid start date and time." if start_value and starts_at is None else ""
        end_error = "Enter a valid end date and time." if end_value and ends_at is None else ""
        if starts_at and ends_at and ends_at <= starts_at:
            end_error = "End time must be after the start time."
        if start_error or end_error:
            return render_template("admin/banner_form.html", banner=banner, start_error=start_error, end_error=end_error, start_value=start_value, end_value=end_value, fit_mode=fit_mode, focus=focus, app_timezone=current_app.config.get("APP_TIMEZONE", "Africa/Lagos")), 400
        title_error = "Keep the title under 150 characters." if len(title) > 150 else ""
        subtitle_error = "Keep the subtitle under 300 characters." if len(subtitle) > 300 else ""
        link_error = "Enter an http(s) URL or a site-relative path such as /store/12, or leave this field empty." if link is False else ""
        image_error = "Choose a banner image." if not (upload and upload.filename) and not (banner and banner.image_key) else ""
        if title_error or subtitle_error or link_error or image_error:
            return render_template("admin/banner_form.html", banner=banner, title_error=title_error, subtitle_error=subtitle_error, link_error=link_error, image_error=image_error, start_value=start_value, end_value=end_value, fit_mode=fit_mode, focus=focus, app_timezone=current_app.config.get("APP_TIMEZONE", "Africa/Lagos")), 400
        else:
            is_new = is_new_record
            try: image_key = save_banner(upload) if upload and upload.filename else banner.image_key
            except Exception:
                current_app.logger.exception("Could not store uploaded banner image.")
                image_error = "The image could not be stored. Choose another JPG, PNG or WebP file."; image_key = None
            if image_key:
                if not banner: banner = Banner(university_id=current_user.university_id)
                old_key = banner.image_key
                banner.title, banner.subtitle, banner.image_key = title, subtitle, image_key
                banner.fit_mode, banner.focus = fit_mode, focus
                banner.link_url, banner.button_text = link, form.get("button_text", "").strip()[:40] or None
                banner.is_active = "is_active" in form
                banner.starts_at, banner.ends_at = starts_at, ends_at
                db.session.add(banner)
                db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=current_user.id,
                    action="banner_created" if is_new else "banner_edited", details=f"Banner {title}"))
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    if upload and upload.filename and image_key != old_key: delete_image_files([image_key])
                    current_app.logger.exception("Could not save banner record.")
                    flash("The banner could not be saved. Please try again.", "error")
                    return render_template("admin/banner_form.html", banner=banner, start_value=start_value, end_value=end_value, fit_mode=fit_mode, focus=focus, app_timezone=current_app.config.get("APP_TIMEZONE", "Africa/Lagos")), 500
                if not is_new and old_key and old_key != image_key: delete_image_files([old_key])
                flash("Banner saved.", "success"); return redirect(url_for("admin.banners"))
    return render_template("admin/banner_form.html", banner=banner, start_value=start_value, end_value=end_value, fit_mode=fit_mode, focus=focus, image_error=locals().get("image_error", ""), app_timezone=current_app.config.get("APP_TIMEZONE", "Africa/Lagos"))


def _parse_date(value):
    if not value: return None
    try:
        local = datetime.strptime(value, "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo(current_app.config.get("APP_TIMEZONE", "Africa/Lagos")))
        return local.astimezone(timezone.utc)
    except (ValueError, KeyError): return None


def _banner_local_values(banner):
    def value(date):
        if not date: return ""
        if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
        return date.astimezone(ZoneInfo(current_app.config.get("APP_TIMEZONE", "Africa/Lagos"))).strftime("%Y-%m-%dT%H:%M")
    return (value(banner.starts_at), value(banner.ends_at)) if banner else ("", "")


@admin_bp.post("/banners/<int:banner_id>/toggle")
@admin_required
def banner_toggle(banner_id):
    banner = Banner.query.filter(Banner.id == banner_id, (Banner.university_id == current_user.university_id) | Banner.university_id.is_(None)).first_or_404()
    banner.is_active = not banner.is_active
    db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=current_user.id, action="banner_toggled", details=f"Banner {banner.title}: {'active' if banner.is_active else 'inactive'}"))
    db.session.commit()
    return redirect(url_for("admin.banners"))


@admin_bp.post("/banners/<int:banner_id>/move/<direction>")
@admin_required
def banner_move(banner_id, direction):
    if direction not in ("up", "down"): abort(404)
    rows = Banner.query.filter((Banner.university_id == current_user.university_id) | Banner.university_id.is_(None)).order_by(Banner.position, Banner.id).all()
    index = next((i for i, row in enumerate(rows) if row.id == banner_id), None)
    if index is None: abort(404)
    other = index - 1 if direction == "up" else index + 1
    if 0 <= other < len(rows):
        rows[index].position, rows[other].position = rows[other].position, rows[index].position
        db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=current_user.id, action="banner_moved", details=f"Banner {rows[other].title} {direction}"))
        db.session.commit()
    return redirect(url_for("admin.banners"))


@admin_bp.post("/banners/<int:banner_id>/delete")
@admin_required
def banner_delete(banner_id):
    banner = Banner.query.filter(Banner.id == banner_id, (Banner.university_id == current_user.university_id) | Banner.university_id.is_(None)).first_or_404()
    key = banner.image_key
    db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=current_user.id, action="banner_deleted", details=f"Banner {banner.title}"))
    db.session.delete(banner); db.session.commit(); delete_image_files([key])
    return redirect(url_for("admin.banners"))


@admin_bp.get("/ad-requests")
@admin_required
def ad_requests():
    status = request.args.get("status", "all")
    statuses = {"all", "new", "contacted", "accepted", "rejected"}
    if status not in statuses:
        status = "all"
    query = AdRequest.query.filter_by(university_id=current_user.university_id)
    if status != "all":
        query = query.filter_by(status=status)
    rows = query.order_by(AdRequest.created_at.desc(), AdRequest.id.desc()).all()
    return render_template("admin/ad_requests.html", requests=rows, status=status,
                           new_count=AdRequest.query.filter_by(university_id=current_user.university_id, status="new").count())


@admin_bp.route("/ad-requests/<int:request_id>", methods=["GET", "POST"])
@admin_required
def ad_request_detail(request_id):
    row = AdRequest.query.filter_by(id=request_id, university_id=current_user.university_id).first_or_404()
    if request.method == "POST":
        status = request.form.get("status", "")
        if status not in {"contacted", "accepted", "rejected"}:
            abort(400)
        row.status = status
        row.admin_note = request.form.get("admin_note", "").strip()[:2000]
        db.session.add(AdminAuditLog(admin_id=current_user.id, target_user_id=row.user_id,
                                    action="ad_request_updated", details=f"Request {row.id}: {status}"))
        db.session.commit()
        flash("Advertising request updated.", "success")
        return redirect(url_for("admin.ad_request_detail", request_id=row.id))
    return render_template("admin/ad_request_detail.html", ad_request=row)


@admin_bp.route("/applications")
@admin_required
def applications():
    status = request.args.get("status", APP_PENDING)
    if status not in APP_STATUSES and status != "all":
        status = APP_PENDING

    base = VendorApplication.query.join(User, VendorApplication.user_id == User.id).filter(
        User.university_id == current_user.university_id
    )
    query = base if status == "all" else base.filter(VendorApplication.status == status)
    order = VendorApplication.created_at.asc() if status == APP_PENDING else VendorApplication.created_at.desc()

    counts = dict(
        db.session.query(VendorApplication.status, func.count(VendorApplication.id))
        .join(User, VendorApplication.user_id == User.id)
        .filter(User.university_id == current_user.university_id)
        .group_by(VendorApplication.status)
        .all()
    )
    new_ids = {n.link_url.rsplit("/", 1)[-1] for n in Notification.query.filter_by(user_id=current_user.id, kind="vendor_application", is_read=False).all()}
    Notification.query.filter_by(user_id=current_user.id, kind="vendor_application", is_read=False).update({Notification.is_read: True})
    db.session.commit()
    return render_template(
        "admin/applications.html",
        applications=query.order_by(order).all(),
        status=status,
        counts=counts,
        tabs=[APP_PENDING, APP_APPROVED, APP_REJECTED, "all"],
        new_ids=new_ids,
    )


@admin_bp.route("/applications/<int:application_id>")
@admin_required
def application_detail(application_id):
    return render_template("admin/application_detail.html", application=_scoped_application_or_404(application_id), payout_mismatch=None,
                           payout_verification_enabled=current_app.config.get("PAYSTACK_VERIFY_BANK", False))


@admin_bp.route("/applications/<int:application_id>/approve", methods=["POST"])
@admin_required
def approve(application_id):
    application = _scoped_application_or_404(application_id)
    admin = current_user._get_current_object()

    if application.status != APP_PENDING or application.user.role != ROLE_BUYER:
        flash("That application has already been reviewed.", "error")
        return redirect(url_for("admin.applications"))
    if not application.user.has_verified_school_email or not application.user.photo_key or not application.bank_code or not application.account_number:
        flash("The applicant must verify a school email, upload a photo, and provide bank details before approval.", "error")
        return redirect(url_for("admin.application_detail", application_id=application.id))

    try:
        application.approve(admin)
        db.session.commit()
    except (ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, ValueError) else "Couldn't save that decision. Please try again.", "error")
        return redirect(url_for("admin.application_detail", application_id=application.id))
    payout_note = None
    try:
        payout_note = _ensure_payout_account(application)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Best-effort payout account setup failed for vendor %s.", application.user_id)
    notify_user(application.user, "application", "Vendor application approved", "Your application was approved.", url_for("vendor.apply"))
    notify(
        to=application.user.email,
        subject="Your vendor application was approved",
        body=f"Hi {application.user.name},\n\nGood news - {application.business_name} is approved. You can now list products and services on UniHub.\n",
    )
    if payout_note:
        flash(f"Approved {application.business_name}. Warning: Paystack returned a different account name ({payout_note}); review payout details.", "warning")
    elif not application.paystack_subaccount_code:
        flash(f"Approved {application.business_name}. Payout account not set up; you can retry from the vendor page.", "warning")
    else:
        flash(f"Approved {application.business_name}.", "success")
    return redirect(url_for("admin.applications"))


@admin_bp.route("/applications/<int:application_id>/reject", methods=["POST"])
@admin_required
def reject(application_id):
    application = _scoped_application_or_404(application_id)
    admin = current_user._get_current_object()

    if application.status != APP_PENDING:
        flash("That application has already been reviewed.", "error")
        return redirect(url_for("admin.applications"))

    reason = request.form.get("reason", "").strip()[:255]
    if not reason:
        flash("Please provide a reason for rejection.", "error")
        return redirect(url_for("admin.application_detail", application_id=application.id))
    application.reject(admin, reason)
    db.session.commit()
    notify_user(application.user, "application", "Vendor application update", f"Your application was rejected: {reason}", url_for("vendor.apply"))

    notify(
        to=application.user.email,
        subject="Update on your vendor application",
        body=(
            f"Hi {application.user.name},\n\n"
            f"We couldn't approve {application.business_name} this time."
            + (f"\nReason: {reason}" if reason else "")
            + "\n\nYou can update your application and apply again from your account.\n"
        ),
    )
    flash(f"Rejected {application.business_name}.", "success")
    return redirect(url_for("admin.applications"))


# --- reported listings ------------------------------------------------------------
NOTE_MAX = 255


def _scoped_report_or_404(report_id):
    report = db.session.get(Report, report_id)
    if report is None or report.product.vendor.university_id != current_user.university_id:
        abort(404)
    return report


def _resolve_open_reports(product_ids, admin, status, note):
    """Close every open report on these listings with the same outcome."""
    for report in Report.query.filter(Report.product_id.in_(product_ids), Report.status == REPORT_OPEN):
        report.resolve(admin, status, note)


def _vendor_label(user):
    application = user.vendor_application
    return application.business_name if application else user.name


@admin_bp.route("/reports")
@admin_required
def reports():
    status = request.args.get("status", REPORT_OPEN)
    if status not in REPORT_STATUSES and status != "all":
        status = REPORT_OPEN

    base = (
        Report.query.join(Product, Report.product_id == Product.id)
        .join(User, Product.vendor_id == User.id)  # the vendor (not the reporter)
        .filter(User.university_id == current_user.university_id)
    )
    query = base if status == "all" else base.filter(Report.status == status)
    order = Report.created_at.asc() if status == REPORT_OPEN else Report.created_at.desc()
    rows = (
        query.options(
            contains_eager(Report.product).contains_eager(Product.vendor),
            selectinload(Report.reporter),
            selectinload(Report.resolved_by),
        )
        .order_by(order)
        .limit(200)
        .all()
    )

    counts = dict(
        db.session.query(Report.status, func.count(Report.id))
        .join(Product, Report.product_id == Product.id)
        .join(User, Product.vendor_id == User.id)
        .filter(User.university_id == current_user.university_id)
        .group_by(Report.status)
        .all()
    )
    open_per_product = dict(
        db.session.query(Report.product_id, func.count(Report.id))
        .filter(Report.status == REPORT_OPEN, Report.product_id.in_([r.product_id for r in rows] or [0]))
        .group_by(Report.product_id)
        .all()
    )
    return render_template(
        "admin/reports.html",
        reports=rows,
        status=status,
        counts=counts,
        open_per_product=open_per_product,
        tabs=[REPORT_OPEN, REPORT_ACTIONED, REPORT_DISMISSED, "all"],
        vendor_label=_vendor_label,
    )


@admin_bp.route("/reports/<int:report_id>/dismiss", methods=["POST"])
@admin_required
def dismiss_report(report_id):
    report = _scoped_report_or_404(report_id)
    if report.status != REPORT_OPEN:
        flash("That report has already been handled.", "error")
    else:
        report.resolve(current_user._get_current_object(), REPORT_DISMISSED, request.form.get("note", "").strip()[:NOTE_MAX] or None)
        db.session.commit()
        flash("Report dismissed.", "success")
    return redirect(url_for("admin.reports"))


@admin_bp.route("/reports/<int:report_id>/remove-listing", methods=["POST"])
@admin_required
def remove_listing(report_id):
    report = _scoped_report_or_404(report_id)
    admin = current_user._get_current_object()
    if report.status != REPORT_OPEN:
        flash("That report has already been handled.", "error")
        return redirect(url_for("admin.reports"))

    product = report.product
    reason = request.form.get("note", "").strip()[:NOTE_MAX] or report.reason_label
    product.remove(reason)
    _resolve_open_reports([product.id], admin, REPORT_ACTIONED, f"Listing removed: {reason}"[:NOTE_MAX])
    db.session.commit()

    vendor = product.vendor
    notify_user(vendor, "listing_removed", "Listing removed by an admin", f"{product.title}: {reason}", url_for("listings.my_products"))
    notify(
        to=vendor.email,
        subject="One of your listings was removed",
        body=(
            f"Hi {vendor.name},\n\n"
            f'An admin removed your listing "{product.title}" from UniHub.\n'
            f"Reason: {reason}\n\n"
            "If you think this was a mistake, reply to this email or contact your campus admin.\n"
        ),
    )
    flash(f"Removed “{product.title}” and closed its reports.", "success")
    return redirect(url_for("admin.reports"))


@admin_bp.route("/reports/<int:report_id>/revoke-vendor", methods=["POST"])
@admin_required
def revoke_vendor(report_id):
    report = _scoped_report_or_404(report_id)
    admin = current_user._get_current_object()
    vendor = report.product.vendor

    reason = request.form.get("note", "").strip()[:NOTE_MAX]
    if report.status != REPORT_OPEN or vendor.role != ROLE_VENDOR or vendor.vendor_application is None:
        flash("That report has already been handled, or the vendor is no longer active.", "error")
        return redirect(url_for("admin.reports"))
    if not reason:
        flash("Please give a reason. The vendor will see it.", "error")
        return redirect(url_for("admin.reports"))

    business = vendor.vendor_application.business_name
    vendor.vendor_application.reject(admin, reason)  # status -> rejected, so they can re-apply
    vendor.role = ROLE_BUYER
    product_ids = [p.id for p in vendor.products]
    _resolve_open_reports(product_ids, admin, REPORT_ACTIONED, f"Vendor revoked: {reason}"[:NOTE_MAX])
    db.session.commit()

    notify(
        to=vendor.email,
        subject="Your vendor access was removed",
        body=(
            f"Hi {vendor.name},\n\n"
            f"An admin removed vendor access for {business} on UniHub, and your listings are no longer visible.\n"
            f"Reason: {reason}\n\n"
            "You can update your application and apply again from your account.\n"
        ),
    )
    flash(f"Revoked vendor access for {business}.", "success")
    return redirect(url_for("admin.reports"))


@admin_bp.route("/products/<int:product_id>/restore", methods=["POST"])
@admin_required
def restore_listing(product_id):
    product = db.session.get(Product, product_id)
    if product is None or product.vendor.university_id != current_user.university_id:
        abort(404)
    if not product.is_removed:
        flash("That listing isn't removed.", "error")
    else:
        product.restore()
        db.session.commit()
        notify(
            to=product.vendor.email,
            subject="Your listing was restored",
            body=(
                f"Hi {product.vendor.name},\n\n"
                f'Your listing "{product.title}" was restored on UniHub.\n'
            ),
        )
        flash(f"Restored “{product.title}”.", "success")
    return redirect(url_for("admin.reports", status=REPORT_ACTIONED))
