"""Account settings for non-admin users. Sensitive data is only rendered to its owner/admin."""
import re
import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_limiter import Limiter
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import ROLE_VENDOR, utcnow, User
from services.banks import get_banks, bank_name_for_code, mask_account
from services.hostels import hostel_options, validate_address
from services.images import ImageError
from services.mail import notify, send_email
from services.links import absolute_url
from services.storage import delete_avatar_files, save_avatar
from blueprints.vendor import BIO_MIN, BIO_MAX, normalize_phone

settings_bp = Blueprint("settings", __name__)


def _allowed():
    return current_user._get_current_object()


def _render(user, form=None, field_errors=None):
    if user.is_admin:
        error = request.args.get("error")
        if error: flash(error, "error")
        return render_template("settings/admin.html", user=user)
    form = form or {"hostel_id": str(user.hostel_id or ""), "hostel_block_id": str(user.hostel_block_id or ""),
                    "floor": str(user.floor or ""), "room_number": user.room_number or ""}
    if field_errors is None:
        field = request.args.get("field", "")
        message = request.args.get("error", "")
        allowed = {"name", "profile_photo", "hostel_id", "hostel_block_id", "floor", "room_number",
                   "current_password", "new_password", "confirm_password", "theme", "bio", "phone_number",
                   "bank_code", "account_number", "bank_password"}
        field_errors = {field: message} if field in allowed and message else {}
    return render_template("settings/index.html", user=user, form=form, field_errors=field_errors,
                           hostels=hostel_options(user.university_id), banks=get_banks(),
                           mask_account=mask_account)


def _error_redirect(field, message):
    return redirect(url_for("settings.index", field=field, error=message))


@settings_bp.get("/settings")
@login_required
def index():
    return _render(_allowed())


@settings_bp.post("/settings/profile")
@login_required
def profile():
    user = _allowed(); name = request.form.get("name", "").strip(); upload = request.files.get("profile_photo")
    remove = "remove_photo" in request.form
    if not 1 <= len(name) <= 120:
        return _error_redirect("name", "Name must be 1-120 characters.")
    if remove and user.role == ROLE_VENDOR:
        return _error_redirect("profile_photo", "Vendors need a profile photo. Upload a replacement instead.")
    new_key = None
    if upload and upload.filename:
        try: new_key = save_avatar(upload)
        except ImageError as exc:
            return _error_redirect("profile_photo", str(exc))
    old_key = user.photo_key
    user.name = name
    if new_key: user.photo_key = new_key
    elif remove: user.photo_key = None
    try: db.session.commit()
    except Exception:
        db.session.rollback()
        if new_key: delete_avatar_files([new_key])
        flash("Couldn't save your profile. Please try again.", "error")
    else:
        if old_key and (new_key or remove): delete_avatar_files([old_key])
        flash("Profile updated.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/email")
@login_required
@limiter.limit("5 per hour")
def admin_email():
    user = _allowed()
    if not user.is_admin: abort(404)
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("current_password", "")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        flash("Enter a valid email address.", "error")
    elif User.query.filter((User.email == email) | (User.school_email == email), User.id != user.id).first():
        flash("That email address is already in use.", "error")
    elif not user.check_password(password):
        flash("Current password is incorrect.", "error")
    else:
        token = secrets.token_urlsafe(32)
        user.pending_email = email
        user.email_change_token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.email_change_expires_at = utcnow() + timedelta(hours=1)
        db.session.commit()
        link = absolute_url("settings.confirm_admin_email", token=token)
        try: send_email(to=email, subject="Confirm your new UniHub admin email", body=f"Confirm this email change within one hour:\n{link}")
        except Exception: current_app.logger.exception("Could not send admin email-change confirmation.")
        notify(user.email, "UniHub admin email change requested", f"A request was made to change your admin email to {email}. Confirm it from that new address if this was you.")
        flash("Check the new email address for a confirmation link.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.get("/settings/confirm-email/<token>")
def confirm_admin_email(token):
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = User.query.filter_by(email_change_token_hash=token_hash).first()
    if not user or not user.pending_email or not user.email_change_expires_at:
        flash("That email confirmation link is invalid or expired.", "error")
        return redirect(url_for("auth.login"))
    expiry = user.email_change_expires_at.replace(tzinfo=utcnow().tzinfo) if user.email_change_expires_at.tzinfo is None else user.email_change_expires_at
    if expiry <= utcnow() or User.query.filter(User.email == user.pending_email, User.id != user.id).first():
        flash("That email confirmation link is invalid or expired.", "error")
        return redirect(url_for("auth.login"))
    user.email = user.pending_email
    user.pending_email = None; user.email_change_token_hash = None; user.email_change_expires_at = None
    db.session.commit()
    flash("Admin email updated. Please log in with the new address.", "success")
    return redirect(url_for("auth.login"))


@settings_bp.post("/settings/address")
@login_required
def address():
    user = _allowed()
    if user.is_admin: abort(403)
    values, error = validate_address(user.university_id, request.form.get("hostel_id"),
        request.form.get("hostel_block_id"), request.form.get("floor"), request.form.get("room_number"))
    if error:
        lowered = error.lower()
        field = "hostel_block_id" if "block" in lowered else "floor" if "floor" in lowered else "room_number" if "room" in lowered else "hostel_id"
        return _error_redirect(field, error)
    else:
        for key, value in values.items(): setattr(user, key, value)
        db.session.commit(); flash("Delivery address updated.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/password")
@login_required
@limiter.limit("5 per hour")
def password():
    user = _allowed(); current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    if not user.check_password(current): return _error_redirect("current_password", "Current password is incorrect.")
    elif len(new) < 8: return _error_redirect("new_password", "New password must be at least 8 characters.")
    elif new != request.form.get("confirm_password", ""): return _error_redirect("confirm_password", "The two passwords don't match.")
    else:
        user.set_password(new); db.session.commit()
        notify(user.email, "Your UniHub password was changed", f"Hi {user.name},\\n\\nYour UniHub password was changed. If this wasn't you, contact support.")
        flash("Password changed.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/appearance")
@login_required
def appearance():
    _allowed()
    if request.form.get("theme") not in ("light", "dark", "device"): return _error_redirect("theme", "Choose a valid theme.")
    else: flash("Appearance updated.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/business")
@login_required
def business():
    user = _allowed()
    if user.is_admin: abort(403)
    if user.role != ROLE_VENDOR or not user.vendor_application: abort(403)
    app = user.vendor_application; bio = request.form.get("bio", "").strip(); phone = normalize_phone(request.form.get("phone_number", ""))
    if not BIO_MIN <= len(bio) <= BIO_MAX: return _error_redirect("bio", f"Business description must be {BIO_MIN}-{BIO_MAX} characters.")
    elif phone is None: return _error_redirect("phone_number", "Enter a valid Nigerian phone number.")
    else:
        app.bio = bio; app.phone_number = phone; db.session.commit(); flash("Business profile updated.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/bank")
@login_required
def bank():
    user = _allowed()
    if user.is_admin: abort(403)
    if user.role != ROLE_VENDOR or not user.vendor_application: abort(403)
    app = user.vendor_application; code = request.form.get("bank_code", ""); number = request.form.get("account_number", "").strip()
    account_name = request.form.get("account_name", "").strip() or user.name
    if not user.check_password(request.form.get("current_password", "")):
        return _error_redirect("bank_password", "Current password is incorrect.")
    elif not bank_name_for_code(code): return _error_redirect("bank_code", "Please choose a bank.")
    elif not re.fullmatch(r"\d{10}", number): return _error_redirect("account_number", "Account number must be exactly 10 digits.")
    else:
        app.bank_code = code; app.bank_name = bank_name_for_code(code); app.account_number = number
        app.account_name = account_name; app.paystack_subaccount_code = None; db.session.commit()
        notify(user.email, "Your UniHub bank details were changed", f"Hi {user.name},\\n\\nYour UniHub bank details were updated. If this wasn't you, contact support immediately.")
        flash("Bank details updated.", "success")
    return redirect(url_for("settings.index"))
