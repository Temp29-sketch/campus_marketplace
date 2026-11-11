"""Account settings for non-admin users. Sensitive data is only rendered to its owner/admin."""
import re
from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_limiter import Limiter
from sqlalchemy.exc import IntegrityError

from extensions import db, limiter
from models import ROLE_VENDOR
from services.banks import BANKS, BANK_BY_CODE, mask_account
from services.hostels import hostel_options, validate_address
from services.images import ImageError
from services.mail import notify
from services.storage import delete_avatar_files, save_avatar
from blueprints.vendor import BIO_MIN, BIO_MAX, normalize_phone

settings_bp = Blueprint("settings", __name__)


def _allowed():
    if current_user.is_admin: abort(403)
    return current_user._get_current_object()


def _render(user, form=None):
    form = form or {"hostel_id": str(user.hostel_id or ""), "hostel_block_id": str(user.hostel_block_id or ""),
                    "floor": str(user.floor or ""), "room_number": user.room_number or ""}
    return render_template("settings/index.html", user=user, form=form,
                           hostels=hostel_options(user.university_id), banks=BANKS,
                           mask_account=mask_account)


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
        flash("Name must be 1-120 characters.", "error"); return redirect(url_for("settings.index"))
    if remove and user.role == ROLE_VENDOR:
        flash("Vendors need a profile photo. Upload a replacement instead.", "error"); return redirect(url_for("settings.index"))
    new_key = None
    if upload and upload.filename:
        try: new_key = save_avatar(upload)
        except ImageError as exc:
            flash(str(exc), "error"); return redirect(url_for("settings.index"))
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


@settings_bp.post("/settings/address")
@login_required
def address():
    user = _allowed()
    values, error = validate_address(user.university_id, request.form.get("hostel_id"),
        request.form.get("hostel_block_id"), request.form.get("floor"), request.form.get("room_number"))
    if error: flash(error, "error")
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
    if not user.check_password(current): flash("Current password is incorrect.", "error")
    elif len(new) < 8: flash("New password must be at least 8 characters.", "error")
    elif new != request.form.get("confirm_password", ""): flash("The two passwords don't match.", "error")
    else:
        user.set_password(new); db.session.commit()
        notify(user.email, "Your UniHub password was changed", f"Hi {user.name},\\n\\nYour UniHub password was changed. If this wasn't you, contact support.")
        flash("Password changed.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/appearance")
@login_required
def appearance():
    _allowed()
    if request.form.get("theme") not in ("light", "dark", "device"): flash("Choose a valid theme.", "error")
    else: flash("Appearance updated.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/business")
@login_required
def business():
    user = _allowed()
    if user.role != ROLE_VENDOR or not user.vendor_application: abort(403)
    app = user.vendor_application; bio = request.form.get("bio", "").strip(); phone = normalize_phone(request.form.get("phone_number", ""))
    if not BIO_MIN <= len(bio) <= BIO_MAX: flash(f"Business description must be {BIO_MIN}-{BIO_MAX} characters.", "error")
    elif phone is None: flash("Enter a valid Nigerian phone number.", "error")
    else:
        app.bio = bio; app.phone_number = phone; db.session.commit(); flash("Business profile updated.", "success")
    return redirect(url_for("settings.index"))


@settings_bp.post("/settings/bank")
@login_required
def bank():
    user = _allowed()
    if user.role != ROLE_VENDOR or not user.vendor_application: abort(403)
    app = user.vendor_application; code = request.form.get("bank_code", ""); number = request.form.get("account_number", "").strip()
    if not user.check_password(request.form.get("current_password", "")):
        flash("Current password is incorrect.", "error")
    elif code not in BANK_BY_CODE: flash("Please choose a bank.", "error")
    elif not re.fullmatch(r"\d{10}", number): flash("Account number must be exactly 10 digits.", "error")
    else:
        app.bank_code = code; app.bank_name = BANK_BY_CODE[code]; app.account_number = number
        app.account_name = None; app.paystack_subaccount_code = None; db.session.commit()
        notify(user.email, "Your UniHub bank details were changed", f"Hi {user.name},\\n\\nYour UniHub bank details were updated. If this wasn't you, contact support immediately.")
        flash("Bank details updated.", "success")
    return redirect(url_for("settings.index"))
