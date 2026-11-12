from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import Notification

notifications_bp = Blueprint("notifications", __name__)


def _owned(notification_id):
    row = db.session.get(Notification, notification_id)
    if row is None or row.user_id != current_user.id:
        abort(404)
    return row


@notifications_bp.get("/notifications")
@login_required
def index():
    rows = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc(), Notification.id.desc()).paginate(per_page=20, error_out=False)
    return render_template("notifications/index.html", notifications=rows)


@notifications_bp.get("/api/notifications")
@login_required
def api():
    query = Notification.query.filter_by(user_id=current_user.id)
    total = query.count()
    rows = query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(10).all()
    return jsonify(unread_count=query.filter_by(is_read=False).count(), total=total, items=[
        {"id": n.id, "title": n.title, "body": n.body, "link_url": n.link_url,
         "is_read": n.is_read, "created_at": n.created_at.isoformat(), "time_ago": _ago(n.created_at)} for n in rows
    ])


def _ago(value):
    if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
    seconds = max(0, int((datetime.now(timezone.utc) - value).total_seconds()))
    if seconds < 60: return "just now"
    if seconds < 3600: return f"{seconds // 60}m ago"
    if seconds < 86400: return f"{seconds // 3600}h ago"
    if seconds < 604800: return f"{seconds // 86400}d ago"
    return value.strftime("%b %-d")


@notifications_bp.post("/notifications/<int:notification_id>/read")
@login_required
def mark_read(notification_id):
    _owned(notification_id).is_read = True
    db.session.commit()
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("notifications.index"))


@notifications_bp.post("/notifications/read-all")
@login_required
def mark_all_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({Notification.is_read: True})
    db.session.commit()
    target = request.form.get("next", "")
    return redirect(target if target.startswith("/") and not target.startswith("//") else url_for("notifications.index"))
