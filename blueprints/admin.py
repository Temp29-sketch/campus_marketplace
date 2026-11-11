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

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import contains_eager, selectinload

from extensions import db, login_manager
from models import (
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
    Report,
    User,
    VendorApplication,
)
from services.mail import notify

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
    return render_template(
        "admin/applications.html",
        applications=query.order_by(order).all(),
        status=status,
        counts=counts,
        tabs=[APP_PENDING, APP_APPROVED, APP_REJECTED, "all"],
    )


@admin_bp.route("/applications/<int:application_id>/approve", methods=["POST"])
@admin_required
def approve(application_id):
    application = _scoped_application_or_404(application_id)
    admin = current_user._get_current_object()

    if application.status != APP_PENDING or application.user.role != ROLE_BUYER:
        flash("That application has already been reviewed.", "error")
        return redirect(url_for("admin.applications"))

    try:
        application.approve(admin)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    except IntegrityError:
        db.session.rollback()
        flash("Couldn't save that decision. Please try again.", "error")
    else:
        notify(
            to=application.user.email,
            subject="Your vendor application was approved",
            body=(
                f"Hi {application.user.name},\n\n"
                f"Good news - {application.business_name} is approved. "
                "You can now list products and services on UniHub.\n"
            ),
        )
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

    reason = request.form.get("reason", "").strip()[:255] or None
    application.reject(admin, reason)
    db.session.commit()

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
