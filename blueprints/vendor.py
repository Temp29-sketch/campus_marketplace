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
from services.images import ImageError
from services.storage import delete_avatar_files, save_avatar
from services.banks import get_banks, bank_name_for_code
from services.notifications import notify_admins
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

    field_errors = {}
    if request.method == "POST":
        form = request.form
        business_name = form.get("business_name", "").strip(); bio = form.get("bio", "").strip()
        phone = normalize_phone(form.get("phone_number", "")); bank_code = form.get("bank_code", "")
        account_number = form.get("account_number", "").strip()
        account_name = form.get("account_name", "").strip() or user.name
        upload = request.files.get("profile_photo")
        try: category = db.session.get(Category, int(form.get("category_id", "")))
        except ValueError: category = None
        error = None
        if not (BUSINESS_NAME_MIN <= len(business_name) <= BUSINESS_NAME_MAX): error = f"Business name must be {BUSINESS_NAME_MIN}-{BUSINESS_NAME_MAX} characters."
        elif category is None: error = "Please choose a category."
        elif not (BIO_MIN <= len(bio) <= BIO_MAX): error = f"Tell buyers about your business in {BIO_MIN}-{BIO_MAX} characters."
        elif phone is None: error = "Enter a valid Nigerian phone number, e.g. 0801 234 5678."
        elif _name_taken(user, business_name): error = "A business with that name already exists on your campus. Please pick another."
        elif not bank_name_for_code(bank_code): error = "Please choose a bank."
        elif not re.fullmatch(r"\d{10}", account_number): error = "Account number must be exactly 10 digits."
        elif not (2 <= len(account_name) <= 120): error = "Enter the account name (2-120 characters)."
        elif "own_account" not in form: error = "Confirm these are your own account details."
        elif not user.photo_key and (not upload or not upload.filename): error = "Upload a profile photo."
        if error:
            lowered = error.lower()
            if "business name" in lowered or "business with that name" in lowered: field = "business_name"
            elif "category" in lowered: field = "category_id"
            elif "business" in lowered and "buyers" in lowered: field = "bio"
            elif "phone" in lowered: field = "phone_number"
            elif "bank" in lowered: field = "bank_code"
            elif "account number" in lowered: field = "account_number"
            elif "own account" in lowered: field = "own_account"
            else: field = "profile_photo"
            field_errors[field] = error
        else:
            new_key = None
            if upload and upload.filename:
                try: new_key = save_avatar(upload)
                except ImageError as exc:
                    error = str(exc); field_errors["profile_photo"] = error
            if error:
                field_errors["profile_photo"] = error
            else:
                application = existing or VendorApplication(user=user)
                if existing:
                    application.reviewed_by = None; application.reviewed_at = None; application.rejection_reason = None
                else: db.session.add(application)
                application.business_name = business_name; application.category = category; application.bio = bio
                application.phone_number = phone; application.bank_code = bank_code
                application.bank_name = bank_name_for_code(bank_code); application.account_number = account_number
                application.account_name = account_name
                application.status = APP_PENDING
                old_key = user.photo_key
                if new_key: user.photo_key = new_key
                try: db.session.commit()
                except Exception:
                    db.session.rollback()
                    if new_key: delete_avatar_files([new_key])
                    flash("Couldn't save your application. Please try again.", "error")
                else:
                    if new_key and old_key: delete_avatar_files([old_key])
                    notify_admins(user.university_id, "vendor_application", "New vendor application", f"{user.name} submitted a vendor application.", f"/admin/applications/{application.id}")
                    flash("Application submitted! An admin will review it shortly.", "success")
                return redirect(url_for("vendor.apply"))
    elif existing and existing.status == APP_REJECTED:  # prefill with the previous attempt
        form = {
            "business_name": existing.business_name,
            "category_id": str(existing.category_id),
            "bio": existing.bio,
            "phone_number": existing.phone_number,
            "bank_code": existing.bank_code or "",
            "account_number": existing.account_number or "",
            "account_name": existing.account_name or user.name,
        }
    else:
        form = {}

    return render_template("vendor/apply.html", categories=categories, form=form, application=existing, banks=get_banks(), field_errors=field_errors)
