"""Vendor application (step 2 of becoming a vendor).

Only buyers with a verified school email can apply. Rejected applicants can
edit and resubmit; the same row is reused so there is one application per user.
"""
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import APP_PENDING, APP_REJECTED, ROLE_BUYER, Category, User, VendorApplication

vendor_bp = Blueprint("vendor", __name__)

BUSINESS_NAME_MIN, BUSINESS_NAME_MAX = 2, 120
BIO_MIN, BIO_MAX = 20, 1000


def normalize_phone(raw: str):
    """Return a Nigerian mobile number as +234XXXXXXXXXX, or None if invalid."""
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if re.fullmatch(r"0[789]\d{9}", digits):
        return "+234" + digits[1:]
    if re.fullmatch(r"\+?234[789]\d{9}", digits):
        return "+" + digits.lstrip("+")
    return None


def _name_taken(user, business_name: str) -> bool:
    """Another live (pending/approved) business with this name at the same university."""
    return (
        db.session.query(VendorApplication.id)
        .join(User, VendorApplication.user_id == User.id)
        .filter(
            User.university_id == user.university_id,
            VendorApplication.user_id != user.id,
            VendorApplication.status != APP_REJECTED,
            func.lower(VendorApplication.business_name) == business_name.lower(),
        )
        .first()
        is not None
    )


@vendor_bp.route("/become-vendor/apply", methods=["GET", "POST"])
@login_required
def apply():
    user = current_user._get_current_object()

    if user.role != ROLE_BUYER:
        flash("Your account already has vendor or admin access.", "success")
        return redirect(url_for("index"))
    if not user.has_verified_school_email:
        flash("First confirm your school email.", "error")
        return redirect(url_for("auth.verify_school_email"))

    existing = user.vendor_application
    if existing and existing.status == APP_PENDING:
        return render_template("vendor/pending.html", application=existing)

    categories = Category.query.order_by(Category.name).all()

    if request.method == "POST":
        form = request.form
        business_name = form.get("business_name", "").strip()
        bio = form.get("bio", "").strip()
        phone = normalize_phone(form.get("phone_number", ""))
        try:
            category = db.session.get(Category, int(form.get("category_id", "")))
        except ValueError:
            category = None

        error = None
        if not (BUSINESS_NAME_MIN <= len(business_name) <= BUSINESS_NAME_MAX):
            error = f"Business name must be {BUSINESS_NAME_MIN}-{BUSINESS_NAME_MAX} characters."
        elif category is None:
            error = "Please choose a category."
        elif not (BIO_MIN <= len(bio) <= BIO_MAX):
            error = f"Tell buyers about your business in {BIO_MIN}-{BIO_MAX} characters."
        elif phone is None:
            error = "Enter a valid Nigerian phone number, e.g. 0801 234 5678."
        elif _name_taken(user, business_name):
            error = "A business with that name already exists on your campus. Please pick another."

        if error:
            flash(error, "error")
        else:
            if existing:  # resubmission after rejection
                application = existing
                application.reviewed_by = None
                application.reviewed_at = None
                application.rejection_reason = None
            else:
                application = VendorApplication(user=user)
                db.session.add(application)
            application.business_name = business_name
            application.category = category
            application.bio = bio
            application.phone_number = phone
            application.status = APP_PENDING
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("You already have an application on file.", "error")
            else:
                flash("Application submitted! An admin will review it shortly.", "success")
            return redirect(url_for("vendor.apply"))
    elif existing and existing.status == APP_REJECTED:  # prefill with the previous attempt
        form = {
            "business_name": existing.business_name,
            "category_id": str(existing.category_id),
            "bio": existing.bio,
            "phone_number": existing.phone_number,
        }
    else:
        form = {}

    return render_template("vendor/apply.html", categories=categories, form=form, application=existing)
