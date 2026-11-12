"""Best-effort in-app notifications. Notification failures never block user actions."""
import logging

from extensions import db
from models import Notification, ROLE_ADMIN, User

log = logging.getLogger(__name__)


def notify_user(user, kind, title, body, link):
    try:
        if user is None:
            return
        db.session.add(Notification(user_id=user.id, kind=kind, title=title, body=body or "", link_url=link or "/"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Could not create in-app notification")


def notify_admins(university_id, kind, title, body, link):
    try:
        admins = User.query.filter_by(university_id=university_id, role=ROLE_ADMIN).all()
        for admin in admins:
            db.session.add(Notification(user_id=admin.id, kind=kind, title=title, body=body or "", link_url=link or "/"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Could not notify campus admins")
