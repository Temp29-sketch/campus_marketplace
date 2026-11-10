"""Students report listings; admins review them (see blueprints/admin.py).

Guards against abuse of the report button itself:
- login required, and you can't report your own listing;
- one OPEN report per student per listing;
- at most MAX_REPORTS_PER_DAY reports per student per 24 hours.
Reports are private: vendors are never told who reported them.
"""
from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from blueprints.catalog import _university_scope, _visible_products
from extensions import db
from models import REPORT_OPEN, REPORT_REASONS, Product, Report, utcnow

reports_bp = Blueprint("reports", __name__)

MAX_REPORTS_PER_DAY = 10
DETAILS_MAX = 500
OTHER_MIN_DETAILS = 10  # "Something else" needs an explanation


@reports_bp.route("/item/<int:product_id>/report", methods=["GET", "POST"])
@login_required
def new(product_id):
    product = _visible_products(_university_scope()).filter(Product.id == product_id).first_or_404()
    user = current_user._get_current_object()
    back = redirect(url_for("catalog.item", product_id=product.id))

    if product.vendor_id == user.id:
        flash("You can't report your own listing.", "error")
        return back
    if Report.query.filter_by(product_id=product.id, reporter_id=user.id, status=REPORT_OPEN).first():
        flash("You've already reported this listing. An admin will review it.", "success")
        return back

    if request.method == "POST":
        reason = request.form.get("reason", "")
        details = request.form.get("details", "").strip()[:DETAILS_MAX]
        recent = Report.query.filter(
            Report.reporter_id == user.id, Report.created_at >= utcnow() - timedelta(days=1)
        ).count()

        error = None
        if reason not in REPORT_REASONS:
            error = "Please choose what's wrong with this listing."
        elif reason == "other" and len(details) < OTHER_MIN_DETAILS:
            error = "Please tell us a little more about what's wrong."
        elif recent >= MAX_REPORTS_PER_DAY:
            error = "You've sent a lot of reports today. Please try again tomorrow."

        if error:
            flash(error, "error")
        else:
            db.session.add(Report(product=product, reporter=user, reason=reason, details=details or None))
            db.session.commit()
            flash("Thanks. An admin will review your report. The vendor won't see who sent it.", "success")
            return back

    return render_template("reports/new.html", product=product, reasons=REPORT_REASONS, form=request.form)
