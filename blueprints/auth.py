"""Registration, login, and school-email verification.

Rules
- Anyone can register as a buyer with ANY email address.
- To become a vendor, a buyer must verify a school email on their existing
  account (so their account and history carry over). The school email must be
  on their chosen university's domain.
"""
import re
from urllib.parse import urljoin, urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from flask_limiter.util import get_remote_address

from extensions import db, limiter
from models import ROLE_BUYER, University, User
from services.links import absolute_url
from services.mail import send_email
from services.tokens import (
    make_school_email_token,
    make_vendor_signup_token,
    read_school_email_token,
    read_vendor_signup_token,
)

auth_bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MATRIC_RE = re.compile(r"^[A-Za-z0-9/\-]{3,40}$")
MIN_PASSWORD_LENGTH = 8


# --- helpers ----------------------------------------------------------------
def _safe_next(target):
    """Only follow redirects that stay on this site."""
    if not target:
        return None
    host = urlparse(request.host_url)
    dest = urlparse(urljoin(request.host_url, target))
    if dest.scheme in ("http", "https") and dest.netloc == host.netloc:
        return target
    return None


def _get_university(raw_id):
    try:
        uni = db.session.get(University, int(raw_id))
    except (TypeError, ValueError):
        return None
    return uni if uni and uni.is_active else None


def _domain_ok(email: str, domain: str) -> bool:
    host = email.rsplit("@", 1)[-1].lower()
    return host == domain or host.endswith("." + domain)


def _identity_taken(university_id, school_email, matric, exclude_user_id=None):
    """True if this school email or matric number already belongs to an account."""
    email_q = User.query.filter(or_(User.email == school_email, User.school_email == school_email))
    matric_q = User.query.filter(User.university_id == university_id, User.matric_number == matric)
    if exclude_user_id is not None:
        email_q = email_q.filter(User.id != exclude_user_id)
        matric_q = matric_q.filter(User.id != exclude_user_id)
    return email_q.first() is not None or matric_q.first() is not None


def _email_key():
    """Rate-limit key: the email being targeted (so one inbox can't be spammed or one
    account hammered from many IPs); falls back to the client IP."""
    value = request.form.get("school_email") or request.form.get("email") or ""
    return value.strip().lower() or get_remote_address()


def _school_email_conflicts(user, school_email, matric):
    """True if ANOTHER account already uses this school email or matric number."""
    return _identity_taken(user.university_id, school_email, matric, exclude_user_id=user.id)


# --- register / login / logout -----------------------------------------------
@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    universities = University.query.filter_by(is_active=True).order_by(University.name).all()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        uni = _get_university(request.form.get("university_id"))

        errors = []
        if not name:
            errors.append("Please enter your name.")
        if not EMAIL_RE.match(email):
            errors.append("Please enter a valid email address.")
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
        if uni is None:
            errors.append("Please choose your university.")
        if not errors and User.query.filter(
            or_(User.email == email, User.school_email == email)
        ).first():
            errors.append("An account with this email already exists. Try logging in.")

        if not errors:
            user = User(university=uni, name=name, email=email, role=ROLE_BUYER)
            user.set_password(password)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                errors.append("An account with this email already exists. Try logging in.")
            else:
                login_user(user)
                flash("Welcome! Your account is ready.", "success")
                return redirect(url_for("index"))

        for e in errors:
            flash(e, "error")

    return render_template("auth/register.html", universities=universities, form=request.form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
@limiter.limit("20 per hour", key_func=_email_key, methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter(or_(User.email == email, User.school_email == email)).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(_safe_next(request.form.get("next")) or url_for("index"))
        flash("Incorrect email or password.", "error")  # same message either way

    return render_template("auth/login.html", next=request.args.get("next", ""), form=request.form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


# --- school-email verification (step 1 of becoming a vendor) -------------------
@auth_bp.route("/become-vendor/verify", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
@limiter.limit("3 per hour", key_func=_email_key, methods=["POST"])
@login_required
def verify_school_email():
    user = current_user._get_current_object()

    if user.role != ROLE_BUYER:
        flash("Your account already has vendor access.", "success")
        return redirect(url_for("index"))
    if user.has_verified_school_email:
        flash("Your school email is already verified. You can now apply to be a vendor.", "success")
        return redirect(url_for("vendor.apply"))

    domain = user.university.email_domain

    if request.method == "POST":
        school_email = request.form.get("school_email", "").strip().lower()
        matric = request.form.get("matric_number", "").strip().upper()

        error = None
        if not EMAIL_RE.match(school_email):
            error = "Please enter a valid email address."
        elif not _domain_ok(school_email, domain):
            error = f"Vendors must use their school email (it ends with @{domain})."
        elif not MATRIC_RE.match(matric):
            error = "Please enter a valid matriculation number."
        elif _school_email_conflicts(user, school_email, matric):
            error = "That school email or matric number is already linked to another account."

        if error:
            flash(error, "error")
        else:
            token = make_school_email_token(user.id, school_email, matric)
            link = absolute_url("auth.confirm_school_email", token=token)
            send_email(
                to=school_email,
                subject=f"Confirm your {user.university.short_name} school email",
                body=(
                    f"Hi {user.name},\n\n"
                    f"Confirm your {user.university.name} school email to continue your vendor application:\n"
                    f"{link}\n\n"
                    "This link expires in 24 hours. If you didn't request it, ignore this email."
                ),
            )
            flash(f"We sent a confirmation link to {school_email}. Open it to continue.", "success")
            return redirect(url_for("auth.verify_school_email"))

    return render_template("auth/verify_school_email.html", domain=domain, form=request.form)


@auth_bp.route("/verify-school-email/<token>")
@login_required
def confirm_school_email(token):
    user = current_user._get_current_object()
    payload = read_school_email_token(token)

    if not payload or payload.get("uid") != user.id:
        flash("That confirmation link is invalid or has expired. Please request a new one.", "error")
        return redirect(url_for("auth.verify_school_email"))

    if user.role != ROLE_BUYER or user.has_verified_school_email:
        return redirect(url_for("vendor.apply"))

    school_email, matric = payload["email"], payload["matric"]

    # Re-check everything: time has passed since the link was issued.
    if not _domain_ok(school_email, user.university.email_domain) or _school_email_conflicts(
        user, school_email, matric
    ):
        flash("We couldn't verify that school email. Please try again.", "error")
        return redirect(url_for("auth.verify_school_email"))

    user.mark_school_email_verified(school_email, matric)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That school email or matric number was just linked to another account.", "error")
        return redirect(url_for("auth.verify_school_email"))

    flash("School email verified! You can now apply to be a vendor.", "success")
    return redirect(url_for("vendor.apply"))


# --- direct vendor signup (no buyer account needed first) -----------------------
@auth_bp.route("/register/vendor", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
@limiter.limit("3 per hour", key_func=_email_key, methods=["POST"])
def register_vendor():
    """Step 1: school email + matric. The account is NOT created yet."""
    if current_user.is_authenticated:
        return redirect(url_for("vendor.apply"))

    universities = University.query.filter_by(is_active=True).order_by(University.name).all()
    sent_to = None

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        school_email = request.form.get("school_email", "").strip().lower()
        matric = request.form.get("matric_number", "").strip().upper()
        uni = _get_university(request.form.get("university_id"))

        error = None
        if not name or len(name) > 120:
            error = "Please enter your name."
        elif uni is None:
            error = "Please choose your university."
        elif not EMAIL_RE.match(school_email):
            error = "Please enter a valid email address."
        elif not _domain_ok(school_email, uni.email_domain):
            error = f"Vendors must sign up with their school email (it ends with @{uni.email_domain})."
        elif not MATRIC_RE.match(matric):
            error = "Please enter a valid matriculation number."
        elif User.query.filter(or_(User.email == school_email, User.school_email == school_email)).first():
            error = "An account with this email already exists. Log in, then choose 'Become a vendor'."
        elif User.query.filter_by(university_id=uni.id, matric_number=matric).first():
            error = "That matric number is already linked to another account."

        if error:
            flash(error, "error")
        else:
            token = make_vendor_signup_token(name, school_email, matric, uni.id)
            link = absolute_url("auth.confirm_vendor_signup", token=token)
            send_email(
                to=school_email,
                subject=f"Finish creating your {uni.short_name} vendor account",
                body=(
                    f"Hi {name},\n\n"
                    "Confirm your school email and choose a password to finish signing up:\n"
                    f"{link}\n\n"
                    "This link expires in 24 hours. If you didn't request it, ignore this email."
                ),
            )
            sent_to = school_email

    return render_template(
        "auth/register_vendor.html", universities=universities, form=request.form, sent_to=sent_to
    )


@auth_bp.route("/register/vendor/confirm/<token>", methods=["GET", "POST"])
def confirm_vendor_signup(token):
    """Step 2: owner of the school email clicks the link and sets a password."""
    if current_user.is_authenticated:
        return redirect(url_for("vendor.apply"))

    payload = read_vendor_signup_token(token)
    uni = _get_university(payload["uni"]) if payload else None
    if not payload or uni is None or not _domain_ok(payload["email"], uni.email_domain):
        flash("That signup link is invalid or has expired. Please start again.", "error")
        return redirect(url_for("auth.register_vendor"))

    name, email, matric = payload["name"], payload["email"], payload["matric"]

    if User.query.filter(or_(User.email == email, User.school_email == email)).first():
        flash("An account with this email already exists. Please log in.", "error")
        return redirect(url_for("auth.login"))
    if User.query.filter_by(university_id=uni.id, matric_number=matric).first():
        flash("That matric number is already linked to another account.", "error")
        return redirect(url_for("auth.register_vendor"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if len(password) < MIN_PASSWORD_LENGTH:
            flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
        elif password != request.form.get("confirm_password", ""):
            flash("The two passwords don't match.", "error")
        else:
            # Role stays 'buyer' until an admin approves the application.
            user = User(university=uni, name=name, email=email, role=ROLE_BUYER)
            user.set_password(password)
            user.mark_school_email_verified(email, matric)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("An account with this email or matric number already exists.", "error")
                return redirect(url_for("auth.login"))
            login_user(user)
            flash("Account created! Now tell us about your business.", "success")
            return redirect(url_for("vendor.apply"))

    return render_template("auth/set_password.html", name=name, email=email)
