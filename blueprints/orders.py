"""Cart checkout, verified Paystack callbacks, and vendor fulfilment controls."""
import hashlib
import hmac
import json
import logging
import secrets
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from blueprints.pages import _cart_groups
from extensions import csrf, db, limiter
from models import CartItem, Hostel, HostelBlock, Order, OrderItem, PaystackEvent, Product, User, ORDER_FAILED, ORDER_PAID, ORDER_PENDING, utcnow
from services.hostels import validate_address
from services.orders import calculate_commission, distribute_commission, fulfil_order, is_confirmed, is_overdue
from services.notifications import notify_user
from services.paystack import PaystackError

log = logging.getLogger(__name__)
orders_bp = Blueprint("orders", __name__)
vendor_orders_bp = Blueprint("vendor_orders", __name__)


def _delivery(user):
    return validate_address(user.university_id, user.hostel_id, user.hostel_block_id, user.floor, user.room_number)


def _cart_issue(groups):
    return any(line["problem"] for group in groups for line in group["items"])


def _prepare_checkout(user):
    groups = _cart_groups()
    items = CartItem.query.filter_by(user_id=user.id).order_by(CartItem.created_at).all()
    vendors = {item.product.vendor_id: item.product for item in items if item.product}
    too_many = len(vendors) > current_app.config["MAX_VENDORS_PER_ORDER"]
    missing = []
    for vendor_id, product in vendors.items():
        application = product.vendor.vendor_application
        if current_app.config.get("PAYMENTS_MODE", "paystack") == "paystack" and (not application or not application.paystack_subaccount_code):
            missing.append(application.business_name if application else product.vendor.name)
    address, address_error = _delivery(user)
    return groups, items, vendors, too_many, missing, address, address_error


@orders_bp.route("/checkout", methods=["GET", "POST"])
@login_required
@limiter.limit("10 per minute", methods=["POST"])
def checkout():
    if current_user.is_admin:
        abort(403)
    user = current_user._get_current_object()
    groups, items, vendors, too_many, missing, address, address_error = _prepare_checkout(user)
    errors = []
    if not items: errors.append("Your cart is empty.")
    if _cart_issue(groups): errors.append("Fix the items marked in your cart before checkout.")
    max_vendors = current_app.config["MAX_VENDORS_PER_ORDER"]
    if too_many: errors.append(f"Please checkout items from at most {max_vendors} vendors at a time")
    if missing: errors.append("These vendors cannot take payments yet: " + ", ".join(sorted(set(missing))) + ".")
    if address_error: errors.append("Complete your delivery address in Profile settings before checkout.")
    if request.method == "GET":
        configured = current_app.config.get("PAYMENTS_MODE", "paystack") == "demo" or bool(current_app.config.get("PAYSTACK_SECRET_KEY"))
        total_kobo = sum(group["subtotal_kobo"] for group in groups)
        return render_template("pages/checkout.html", groups=groups, items=items, vendors=vendors,
                               address=address, address_error=address_error, errors=errors, payments_configured=configured,
                               total_kobo=total_kobo)
    if errors:
        for error in errors: flash(error, "error")
        return render_template("pages/checkout.html", groups=groups, items=items, vendors=vendors,
                               address=address, address_error=address_error, errors=errors, payments_configured=current_app.config.get("PAYMENTS_MODE", "paystack") == "demo" or bool(current_app.config.get("PAYSTACK_SECRET_KEY")),
                               total_kobo=sum(group["subtotal_kobo"] for group in groups)), 400
    note = request.form.get("note", "").strip()[:300]
    raw_phone = request.form.get("delivery_phone", "").strip()
    compact_phone = __import__("re").sub(r"[\s().-]", "", raw_phone)
    if raw_phone and not __import__("re").fullmatch(r"(?:\+234|0)[789]\d{9}", compact_phone):
        flash("Enter a valid Nigerian delivery phone number, or leave it blank.", "error")
        return render_template("pages/checkout.html", groups=groups, items=items, vendors=vendors,
            address=address, address_error=address_error, errors=["Enter a valid Nigerian delivery phone number, or leave it blank."],
            payments_configured=current_app.config.get("PAYMENTS_MODE", "paystack") == "demo" or bool(current_app.config.get("PAYSTACK_SECRET_KEY")),
            total_kobo=sum(group["subtotal_kobo"] for group in groups)), 400
    delivery_phone = ("+234" + compact_phone[1:]) if compact_phone.startswith("0") else compact_phone
    # All prices and quantities are read from the persisted cart and current database rows.
    total_kobo = 0
    grouped = defaultdict(list)
    for cart_item in items:
        product = cart_item.product
        unit = int((Decimal(product.price) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        line_total = unit * cart_item.quantity
        total_kobo += line_total
        grouped[product.vendor_id].append({"product": product, "cart_item": cart_item, "unit_price_kobo": unit,
                                           "line_total_kobo": line_total, "quantity": cart_item.quantity})
    percent = current_app.config["COMMISSION_PERCENT"]
    commission_total = 0
    commissions = {}
    for vendor_id, lines in grouped.items():
        subtotal = sum(line["line_total_kobo"] for line in lines)
        commission = calculate_commission(subtotal, percent)
        commissions[vendor_id] = distribute_commission(lines, commission)
        commission_total += commission

    order = Order(
        buyer_id=user.id, university_id=user.university_id, reference="UH-" + secrets.token_hex(8),
        total_kobo=total_kobo, commission_kobo=commission_total, status=ORDER_PENDING,
        hostel_name=db.session.get(Hostel, address["hostel_id"]).name,
        hostel_block_name=db.session.get(HostelBlock, address["hostel_block_id"]).name if address["hostel_block_id"] else None,
        floor=address["floor"], room_number=address["room_number"], note=note or None, delivery_phone=delivery_phone or None,
    )
    for vendor_id, lines in commissions.items():
        for line in lines:
            product = line["product"]
            order.items.append(OrderItem(product_id=product.id, vendor_id=vendor_id, title_snapshot=product.title,
                unit_price_kobo=line["unit_price_kobo"], quantity=line["quantity"], line_total_kobo=line["line_total_kobo"],
                commission_kobo=line["commission_kobo"], vendor_share_kobo=line["vendor_share_kobo"]))
    db.session.add(order)
    db.session.commit()
    if current_app.config.get("PAYMENTS_MODE", "paystack") == "demo":
        return redirect(url_for("orders.demo_payment", reference=order.reference))
    subaccounts = []
    for vendor_id, lines in commissions.items():
        application = grouped[vendor_id][0]["product"].vendor.vendor_application
        subaccounts.append({"subaccount": application.paystack_subaccount_code,
                            "share": sum(line["vendor_share_kobo"] for line in lines)})
    estimated_fee = (total_kobo * 150 + 9999) // 10000 + 10000  # approximate 1.5% + ₦100
    if commission_total < estimated_fee:
        log.warning("Order %s commission is below the estimated Paystack fee.", order.id)
    base = current_app.config.get("PUBLIC_BASE_URL") or request.url_root.rstrip("/")
    callback_path = current_app.config.get("PAYSTACK_CALLBACK_PATH", "/checkout/return")
    if not callback_path.startswith("/") or callback_path.startswith("//"):
        callback_path = url_for("orders.payment_return")
    payload = {
        "email": user.email, "amount": total_kobo, "reference": order.reference,
        "callback_url": base.rstrip("/") + callback_path,
        "metadata": {"order_id": order.id},
        "split": {"type": "flat", "bearer_type": "account", "subaccounts": subaccounts},
    }
    try:
        from services import paystack
        result = paystack.initialize_transaction(payload)
        authorization_url = result.get("authorization_url")
        parsed = urlparse(authorization_url or "")
        if parsed.scheme != "https" or not parsed.hostname or not (parsed.hostname == "paystack.com" or parsed.hostname.endswith(".paystack.com")):
            raise PaystackError("Paystack returned an invalid checkout link.")
    except PaystackError as exc:
        order.status = ORDER_FAILED
        db.session.commit()
        flash(str(exc), "error")
        return redirect(url_for("pages.cart"))
    db.session.commit()
    return redirect(authorization_url)


@orders_bp.get("/checkout/demo/<reference>")
@login_required
def demo_payment(reference):
    if current_app.config.get("PAYMENTS_MODE", "paystack") != "demo": abort(404)
    order = Order.query.filter_by(reference=reference, buyer_id=current_user.id).first_or_404()
    return render_template("pages/demo_payment.html", order=order)


@orders_bp.post("/checkout/demo/<reference>/<outcome>")
@login_required
@limiter.limit("10 per minute")
def demo_payment_result(reference, outcome):
    if current_app.config.get("PAYMENTS_MODE", "paystack") != "demo": abort(404)
    if outcome not in ("success", "failed"): abort(404)
    order = Order.query.filter_by(reference=reference, buyer_id=current_user.id).first_or_404()
    if order.status != ORDER_PENDING:
        flash("This demo payment has already been completed.", "error")
        return redirect(url_for("pages.order_detail", order_id=order.id))
    if outcome == "success":
        fulfil_order(order, "DEMO-" + order.reference)
        flash("Demo payment succeeded. No real money was charged.", "success")
    else:
        order.status = ORDER_FAILED
        db.session.commit()
        flash("Demo payment failed. No real money was charged.", "error")
    return redirect(url_for("pages.order_detail", order_id=order.id))


@orders_bp.get("/checkout/return")
@login_required
@limiter.limit("30 per minute")
def payment_return():
    reference = request.args.get("reference", "").strip()
    order = Order.query.filter_by(reference=reference, buyer_id=current_user.id).first_or_404()
    if order.status == ORDER_PAID:
        return render_template("pages/payment_result.html", order=order, state="success")
    if not reference:
        abort(404)
    try:
        from services import paystack
        result = paystack.verify_transaction(reference)
    except PaystackError:
        return render_template("pages/payment_result.html", order=order, state="pending"), 202
    if result.get("status") == "success":
        if result.get("amount") == order.total_kobo and str(result.get("currency", "")).upper() == "NGN":
            order = fulfil_order(order, result.get("id"))
            return render_template("pages/payment_result.html", order=order, state="success")
        log.warning("Verified transaction did not match order %s.", order.id)
        return render_template("pages/payment_result.html", order=order, state="pending"), 202
    if result.get("status") == "failed":
        order.status = ORDER_FAILED; db.session.commit()
        return render_template("pages/payment_result.html", order=order, state="failed")
    return render_template("pages/payment_result.html", order=order, state="pending"), 202


@orders_bp.post("/webhooks/paystack")
@csrf.exempt
@limiter.exempt
def paystack_webhook():
    raw = request.get_data(cache=False)
    secret = current_app.config.get("PAYSTACK_SECRET_KEY", "").encode("utf-8")
    supplied = request.headers.get("x-paystack-signature", "")
    expected = hmac.new(secret, raw, hashlib.sha512).hexdigest() if secret else ""
    if not supplied or not hmac.compare_digest(expected, supplied):
        log.warning("Rejected webhook with an invalid signature.")
        return "", 401
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "", 200
    if not isinstance(payload, dict):
        return "", 200
    event_type = str(payload.get("event", "unknown"))[:80]
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = data.get("reference")
    event_id = str(payload.get("id") or hashlib.sha256(raw).hexdigest())
    if event_type != "charge.success" or not reference:
        if not PaystackEvent.query.filter_by(event_id=event_id).first():
            db.session.add(PaystackEvent(event_id=event_id, event_type=event_type)); db.session.commit()
        return "", 200
    if PaystackEvent.query.filter((PaystackEvent.event_id == event_id) | (PaystackEvent.reference == reference)).first():
        return "", 200
    order = Order.query.filter_by(reference=reference).first()
    if order is not None and data.get("amount") == order.total_kobo and str(data.get("currency", "")).upper() == "NGN":
        try:
            fulfil_order(order, data.get("id"))
        except Exception:
            db.session.rollback()
            log.exception("Could not fulfil paid order %s from webhook.", order.id)
            # Do not persist the idempotency marker when fulfilment failed, so a
            # verified replay can retry after the underlying issue is corrected.
            return "", 200
    db.session.add(PaystackEvent(event_id=event_id, reference=reference, event_type=event_type))
    try: db.session.commit()
    except Exception: db.session.rollback()
    return "", 200


@vendor_orders_bp.get("/vendor/orders")
@login_required
def index():
    if not current_user.is_vendor: abort(403)
    rows = (Order.query.join(OrderItem).filter(Order.status == ORDER_PAID, OrderItem.vendor_id == current_user.id)
            .order_by(Order.created_at.desc()).distinct().all())
    status = request.args.get("status", "to-deliver")
    if status not in ("to-deliver", "delivered", "cancelled"): status = "to-deliver"
    filtered = []
    for order in rows:
        order.vendor_items = [i for i in order.items if i.vendor_id == current_user.id]
        matches = any((i.fulfilment_status in ("new", "preparing", "ready") if status == "to-deliver" else i.fulfilment_status == status) for i in order.vendor_items)
        if matches: filtered.append(order)
    return render_template("vendor/orders.html", orders=filtered, status=status, is_confirmed=is_confirmed, is_overdue=is_overdue)


@vendor_orders_bp.post("/vendor/orders/<int:order_id>/items/<int:item_id>/status")
@login_required
@limiter.limit("60 per minute")
def update_status(order_id, item_id):
    if not current_user.is_vendor: abort(403)
    order = Order.query.filter_by(id=order_id, status=ORDER_PAID).first_or_404()
    item = OrderItem.query.filter_by(id=item_id, order_id=order.id, vendor_id=current_user.id).first_or_404()
    transitions = {"new": "preparing", "preparing": "ready", "ready": "delivered"}
    target = request.form.get("status")
    if transitions.get(item.fulfilment_status) != target:
        flash("That order status change is not available.", "error")
    else:
        item.fulfilment_status = target
        if target == "delivered": item.delivered_at = utcnow()
        db.session.commit()
        buyer = db.session.get(User, order.buyer_id)
        title = "Delivery update" if target == "delivered" else f"Order #{order.id} update"
        notify_user(buyer, "order_update", title, f"{item.title_snapshot} is now {target}.", f"/orders/{order.id}")
        flash(f"Order item marked {target}.", "success")
    return redirect(url_for("vendor_orders.index"))


@orders_bp.post("/orders/items/<int:item_id>/confirm")
@login_required
@limiter.limit("20 per hour")
def confirm_delivery(item_id):
    item = OrderItem.query.join(Order).filter(OrderItem.id == item_id, Order.buyer_id == current_user.id,
        Order.status == ORDER_PAID, OrderItem.fulfilment_status == "delivered").first_or_404()
    if not item.buyer_confirmed_at and not is_confirmed(item):
        item.buyer_confirmed_at = utcnow(); db.session.commit()
        flash("Thanks for confirming delivery.", "success")
    return redirect(url_for("pages.order_detail", order_id=item.order_id))
