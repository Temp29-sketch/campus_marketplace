"""Order totals and post-payment fulfilment."""
import logging
from datetime import datetime, timedelta, timezone

from flask import current_app

from extensions import db
from models import CartItem, Order, OrderItem, ORDER_PAID, Product, User, utcnow
from services.mail import notify
from services.notifications import notify_admins, notify_user

log = logging.getLogger(__name__)


def calculate_commission(subtotal_kobo, percent):
    """Round a vendor's commission half-up using only integer kobo."""
    return (int(subtotal_kobo) * int(percent) + 50) // 100


def distribute_commission(lines, commission_kobo):
    """Allocate a vendor's rounded commission across its lines; the sum is exact."""
    subtotal = sum(line["line_total_kobo"] for line in lines)
    remaining = int(commission_kobo)
    result = []
    for index, line in enumerate(lines):
        if index == len(lines) - 1:
            share = remaining
        else:
            share = (commission_kobo * line["line_total_kobo"] + subtotal // 2) // subtotal if subtotal else 0
            share = min(share, remaining)
        remaining -= share
        result.append({**line, "commission_kobo": share, "vendor_share_kobo": line["line_total_kobo"] - share})
    return result


def is_confirmed(item, now=None):
    if item.buyer_confirmed_at:
        return True
    if not item.delivered_at:
        return False
    now = now or utcnow()
    delivered = item.delivered_at.replace(tzinfo=timezone.utc) if item.delivered_at.tzinfo is None else item.delivered_at
    return delivered + timedelta(hours=48) <= now


def is_overdue(item, now=None):
    if item.fulfilment_status not in ("new", "preparing"):
        return False
    now = now or utcnow()
    created = item.order.paid_at or item.order.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created + timedelta(hours=24) <= now


def fulfil_order(order, transaction_id=None):
    """Mark a verified payment paid and decrement tracked inventory in one transaction."""
    locked = Order.query.filter_by(id=order.id).with_for_update().first()
    if locked is None:
        return None
    if locked.status == ORDER_PAID:
        return locked
    admin_alert = False
    vendor_ids = set()
    try:
        for item in locked.items:
            vendor_ids.add(item.vendor_id)
            product = db.session.get(Product, item.product_id) if item.product_id else None
            if product is None:
                item.fulfilment_status = "cancelled"
                admin_alert = True
                continue
            if product is not None and product.stock is not None and not product.is_service:
                if not product.decrement_stock(item.quantity, commit=False):
                    item.fulfilment_status = "cancelled"
                    admin_alert = True
                    continue
            item.fulfilment_status = "new"
        locked.status = ORDER_PAID
        locked.paid_at = utcnow()
        locked.paystack_transaction_id = str(transaction_id) if transaction_id is not None else locked.paystack_transaction_id
        product_ids = [item.product_id for item in locked.items if item.product_id]
        if product_ids:
            CartItem.query.filter(CartItem.user_id == locked.buyer_id, CartItem.product_id.in_(product_ids)).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    for vendor_id in vendor_ids:
        vendor = db.session.get(User, vendor_id)
        if vendor:
            notify_user(vendor, "new_order", "New order to deliver", f"Paid order #{locked.id} includes an item from your shop. Check its fulfilment status.", "/vendor/orders")
            notify(to=vendor.email, subject=f"New UniHub order #{locked.id}", body=f"A paid order #{locked.id} includes an item from your shop. Sign in to view its fulfilment status.")
    buyer = db.session.get(User, locked.buyer_id)
    if buyer:
        notify_user(buyer, "order_update", f"Order #{locked.id} placed", "Paystack has emailed your receipt.", f"/orders/{locked.id}")
        notify(to=buyer.email, subject=f"UniHub order #{locked.id} placed", body=f"Your order #{locked.id} was paid. Paystack has emailed your receipt.")
    titles = ", ".join(item.title_snapshot for item in locked.items)
    notify_admins(locked.university_id, "new_order", f"New paid order #{locked.id}", f"Paid order contains: {titles}.", "/admin/orders")
    if admin_alert:
        notify_admins(locked.university_id, "stock_shortage", f"Stock issue on order #{locked.id}", "Payment succeeded but one or more items could not be fulfilled because stock ran out. Review for a manual refund.", "/admin/orders")
    return locked
