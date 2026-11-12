"""Database models for the campus marketplace.

Design notes
- University is a first-class table so the platform can scale to many campuses.
  Every user picks a campus; matric numbers are unique *per university*.
- Buyers can sign up with ANY email. To become a vendor they must verify a school
  email on their existing account (User.school_email + school_email_verified_at),
  and an application cannot be approved without it.
- Roles: 'buyer', 'vendor', 'admin'. A vendor is still a buyer: roles only ADD
  abilities, so vendors can browse and buy from other vendors.
- Category is a table (not a hard-coded list) so admins can add categories
  without a code change.
- Products have many images (ProductImage); the first one is the cover.
"""
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import update

from extensions import db, login_manager

ROLE_BUYER = "buyer"
ROLE_VENDOR = "vendor"
ROLE_ADMIN = "admin"
ROLES = (ROLE_BUYER, ROLE_VENDOR, ROLE_ADMIN)

APP_PENDING = "pending"
APP_APPROVED = "approved"
APP_REJECTED = "rejected"
APP_STATUSES = (APP_PENDING, APP_APPROVED, APP_REJECTED)

REPORT_OPEN = "open"
REPORT_ACTIONED = "actioned"  # an admin took action (removed the listing or the vendor)
REPORT_DISMISSED = "dismissed"  # an admin reviewed it and found nothing wrong
REPORT_STATUSES = (REPORT_OPEN, REPORT_ACTIONED, REPORT_DISMISSED)

# key -> label shown to students and admins
REPORT_REASONS = {
    "scam": "Looks like a scam",
    "misleading": "Fake or misleading listing",
    "prohibited": "Prohibited item or service",
    "offensive": "Offensive content",
    "wrong_category": "Wrong category or price",
    "other": "Something else",
}

DEFAULT_CATEGORIES = [
    "Food",
    "Fashion",
    "Tech",
    "Beauty & Skincare",
    "Services",
    "Other",
]


def utcnow():
    return datetime.now(timezone.utc)


class University(db.Model):
    __tablename__ = "universities"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, unique=True)
    short_name = db.Column(db.String(20), nullable=False, unique=True)
    email_domain = db.Column(db.String(120), nullable=False, unique=True)  # e.g. "binghamuni.edu.ng"
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    users = db.relationship("User", back_populates="university")

    def __repr__(self):
        return f"<University {self.short_name}>"


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), nullable=False, unique=True)
    slug = db.Column(db.String(60), nullable=False, unique=True, index=True)

    products = db.relationship("Product", back_populates="category")

    def __repr__(self):
        return f"<Category {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        # NULL matric numbers (plain buyers) don't collide with each other in SQL.
        db.UniqueConstraint("university_id", "matric_number", name="uq_user_university_matric"),
        db.CheckConstraint("role IN ('buyer','vendor','admin')", name="ck_user_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    university_id = db.Column(db.Integer, db.ForeignKey("universities.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)

    # Login/contact email: any provider (gmail, yahoo, school...).
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    phone_number = db.Column(db.String(20))

    # Only set once the student proves they own a school email (needed to sell).
    school_email = db.Column(db.String(255), unique=True, index=True)
    school_email_verified_at = db.Column(db.DateTime(timezone=True))
    matric_number = db.Column(db.String(40))

    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default=ROLE_BUYER)
    hostel_id = db.Column(db.Integer, db.ForeignKey("hostels.id"))
    hostel_block_id = db.Column(db.Integer, db.ForeignKey("hostel_blocks.id"))
    floor = db.Column(db.Integer)
    room_number = db.Column(db.String(10))
    photo_key = db.Column(db.String(255))
    is_active_account = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    session_version = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    pending_email = db.Column(db.String(255))
    email_change_token_hash = db.Column(db.String(64))
    email_change_expires_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    university = db.relationship("University", back_populates="users")
    hostel = db.relationship("Hostel", foreign_keys=[hostel_id])
    hostel_block = db.relationship("HostelBlock", foreign_keys=[hostel_block_id])
    vendor_application = db.relationship(
        "VendorApplication",
        back_populates="user",
        uselist=False,
        foreign_keys="VendorApplication.user_id",
        cascade="all, delete-orphan",
    )
    products = db.relationship("Product", back_populates="vendor", cascade="all, delete-orphan")

    # --- auth helpers -----------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # --- school email -----------------------------------------------------
    @property
    def has_verified_school_email(self) -> bool:
        return bool(self.school_email and self.school_email_verified_at)

    def mark_school_email_verified(self, school_email: str, matric_number: str) -> None:
        self.school_email = school_email.lower()
        self.matric_number = matric_number
        self.school_email_verified_at = utcnow()

    # --- role helpers -----------------------------------------------------
    @property
    def is_vendor(self) -> bool:
        return self.role == ROLE_VENDOR

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def can_list_products(self) -> bool:
        return self.role == ROLE_VENDOR

    @property
    def can_apply_to_be_vendor(self) -> bool:
        """Buyers with a verified school email and no application (or a rejected one)."""
        if self.role != ROLE_BUYER or not self.has_verified_school_email:
            return False
        app = self.vendor_application
        return app is None or app.status == APP_REJECTED

    @property
    def business_name(self):
        app = self.vendor_application
        return app.business_name if app and app.status == APP_APPROVED else None

    def __repr__(self):
        return f"<User {self.email} ({self.role})>"


@login_manager.user_loader
def load_user(user_id):
    from flask import session
    user = db.session.get(User, int(user_id))
    if user and (not user.is_active_account or session.get("_auth_version") != user.session_version):
        return None
    return user



class Hostel(db.Model):
    __tablename__ = "hostels"
    __table_args__ = (
        db.UniqueConstraint("university_id", "name", name="uq_hostel_university_name"),
        db.CheckConstraint("gender IN ('boys','girls')", name="ck_hostel_gender"),
    )

    id = db.Column(db.Integer, primary_key=True)
    university_id = db.Column(db.Integer, db.ForeignKey("universities.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    has_blocks = db.Column(db.Boolean, nullable=False, default=False)
    floor_count = db.Column(db.Integer)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    blocks = db.relationship("HostelBlock", back_populates="hostel", cascade="all, delete-orphan", order_by="HostelBlock.sort_order")


class HostelBlock(db.Model):
    __tablename__ = "hostel_blocks"
    __table_args__ = (db.UniqueConstraint("hostel_id", "name", name="uq_hostelblock_hostel_name"),)

    id = db.Column(db.Integer, primary_key=True)
    hostel_id = db.Column(db.Integer, db.ForeignKey("hostels.id"), nullable=False, index=True)
    name = db.Column(db.String(40), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    hostel = db.relationship("Hostel", back_populates="blocks")


class PendingSignup(db.Model):
    __tablename__ = "pending_signups"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    school_email = db.Column(db.String(255), nullable=False, index=True)
    matric_number = db.Column(db.String(40), nullable=False)
    university_id = db.Column(db.Integer, db.ForeignKey("universities.id"), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    hostel_id = db.Column(db.Integer, db.ForeignKey("hostels.id"), nullable=False)
    hostel_block_id = db.Column(db.Integer, db.ForeignKey("hostel_blocks.id"))
    floor = db.Column(db.Integer)
    room_number = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User")


class VendorApplication(db.Model):
    """One application per user. A rejected user may resubmit (we reuse the row)."""

    __tablename__ = "vendor_applications"
    __table_args__ = (
        db.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_vendorapp_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    business_name = db.Column(db.String(120), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    bio = db.Column(db.Text, nullable=False)
    phone_number = db.Column(db.String(25), nullable=False)
    bank_code = db.Column(db.String(10))
    bank_name = db.Column(db.String(80))
    account_number = db.Column(db.String(10))
    account_name = db.Column(db.String(120))
    paystack_subaccount_code = db.Column(db.String(40))
    status = db.Column(db.String(10), nullable=False, default=APP_PENDING, index=True)
    rejection_reason = db.Column(db.String(255))
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User", back_populates="vendor_application", foreign_keys=[user_id])
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_id])
    category = db.relationship("Category")

    def approve(self, admin: "User") -> None:
        if not self.user.has_verified_school_email:
            raise ValueError("Vendor must verify a school email before approval.")
        if not self.user.photo_key:
            raise ValueError("Vendor must upload a profile photo before approval.")
        if not self.bank_code or not self.account_number:
            raise ValueError("Vendor must provide bank details before approval.")
        self.status = APP_APPROVED
        self.rejection_reason = None
        self.reviewed_by = admin
        self.reviewed_at = utcnow()
        self.user.role = ROLE_VENDOR  # unlocks product listing

    def reject(self, admin: "User", reason: str | None = None) -> None:
        self.status = APP_REJECTED
        self.rejection_reason = reason
        self.reviewed_by = admin
        self.reviewed_at = utcnow()

    def __repr__(self):
        return f"<VendorApplication {self.business_name} [{self.status}]>"


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False, default="")
    price = db.Column(db.Numeric(10, 2), nullable=False)  # Naira
    is_service = db.Column(db.Boolean, nullable=False, default=False)  # product vs service
    stock = db.Column(db.Integer, nullable=True)
    is_available = db.Column(db.Boolean, nullable=False, default=True, index=True)  # vendor: in stock?
    # Admin moderation: a removed listing is hidden from everyone but its vendor.
    # Separate from is_available so a vendor can't bring a removed listing back.
    is_removed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    removed_reason = db.Column(db.String(255))
    removed_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    vendor = db.relationship("User", back_populates="products")
    category = db.relationship("Category", back_populates="products")
    images = db.relationship(
        "ProductImage",
        back_populates="product",
        order_by="ProductImage.position",
        cascade="all, delete-orphan",
    )
    reports = db.relationship("Report", back_populates="product", cascade="all, delete-orphan")

    def decrement_stock(self, quantity=1, commit=True):
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0 or self.stock is None:
            return False
        result = db.session.execute(
            update(Product).where(Product.id == self.id, Product.stock >= quantity)
            .values(stock=Product.stock - quantity).execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return False
        db.session.execute(
            update(Product).where(Product.id == self.id, Product.stock == 0).values(is_available=False)
        )
        db.session.refresh(self)
        if commit:
            db.session.commit()
        return True

    def remove(self, reason: str | None = None) -> None:
        self.is_removed = True
        self.removed_reason = reason
        self.removed_at = utcnow()

    def restore(self) -> None:
        self.is_removed = False
        self.removed_reason = None
        self.removed_at = None

    @property
    def cover_image(self):
        return self.images[0] if self.images else None

    @property
    def business_name(self):
        return self.vendor.business_name

    @property
    def contact_phone(self):
        app = self.vendor.vendor_application
        return app.phone_number if app else None

    def __repr__(self):
        return f"<Product {self.title}>"


class ProductImage(db.Model):
    """One uploaded photo. `storage_key` is a filename locally, or an object key
    (Cloudinary/S3/R2) in production; the storage layer turns it into a URL."""

    __tablename__ = "product_images"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    storage_key = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    product = db.relationship("Product", back_populates="images")


class WishlistItem(db.Model):
    __tablename__ = "wishlist_items"
    __table_args__ = (db.UniqueConstraint("user_id", "product_id", name="uq_wishlist_user_product"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    user = db.relationship("User", backref=db.backref("wishlist_items", cascade="all, delete-orphan"))
    product = db.relationship("Product", backref=db.backref("wishlist_items", cascade="all, delete-orphan"))


class CartItem(db.Model):
    __tablename__ = "cart_items"
    __table_args__ = (db.UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price_kobo = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", backref=db.backref("cart_items", cascade="all, delete-orphan"))
    product = db.relationship("Product", backref=db.backref("cart_items", cascade="all, delete-orphan"))


ORDER_PENDING = "pending"
ORDER_PAID = "paid"
ORDER_FAILED = "failed"
ORDER_CANCELLED = "cancelled"
ORDER_STATUSES = (ORDER_PENDING, ORDER_PAID, ORDER_FAILED, ORDER_CANCELLED)
FULFILMENT_STATUSES = ("new", "preparing", "ready", "delivered", "cancelled")


class Order(db.Model):
    __tablename__ = "orders"
    __table_args__ = (db.CheckConstraint("status IN ('pending','paid','failed','cancelled')", name="ck_order_status"),)
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    university_id = db.Column(db.Integer, db.ForeignKey("universities.id"), nullable=False, index=True)
    reference = db.Column(db.String(50), nullable=False, unique=True, index=True)
    total_kobo = db.Column(db.Integer, nullable=False)
    commission_kobo = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(12), nullable=False, default=ORDER_PENDING, index=True)
    paystack_transaction_id = db.Column(db.String(80))
    paid_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    hostel_name = db.Column(db.String(80), nullable=False)
    hostel_block_name = db.Column(db.String(40))
    floor = db.Column(db.Integer)
    room_number = db.Column(db.String(10), nullable=False)
    note = db.Column(db.String(300))
    delivery_phone = db.Column(db.String(20))
    buyer = db.relationship("User", foreign_keys=[buyer_id])
    university = db.relationship("University")
    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class OrderItem(db.Model):
    __tablename__ = "order_items"
    __table_args__ = (db.CheckConstraint("fulfilment_status IN ('new','preparing','ready','delivered','cancelled')", name="ck_orderitem_fulfilment_status"),)
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=True, index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title_snapshot = db.Column(db.String(150), nullable=False)
    unit_price_kobo = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    line_total_kobo = db.Column(db.Integer, nullable=False)
    commission_kobo = db.Column(db.Integer, nullable=False)
    vendor_share_kobo = db.Column(db.Integer, nullable=False)
    fulfilment_status = db.Column(db.String(12), nullable=False, default="new")
    delivered_at = db.Column(db.DateTime(timezone=True))
    buyer_confirmed_at = db.Column(db.DateTime(timezone=True))
    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")
    vendor = db.relationship("User")


class PaystackEvent(db.Model):
    __tablename__ = "paystack_events"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(100), unique=True, index=True)
    reference = db.Column(db.String(50), unique=True, index=True)
    event_type = db.Column(db.String(80), nullable=False)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Report(db.Model):
    """A student flagging a listing. Admins of the vendor's campus review it."""

    __tablename__ = "reports"
    __table_args__ = (
        db.CheckConstraint("status IN ('open','actioned','dismissed')", name="ck_report_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    reason = db.Column(db.String(20), nullable=False)  # a key of REPORT_REASONS
    details = db.Column(db.String(500))
    status = db.Column(db.String(10), nullable=False, default=REPORT_OPEN, index=True)
    resolution_note = db.Column(db.String(255))
    resolved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    product = db.relationship("Product", back_populates="reports")
    reporter = db.relationship("User", foreign_keys=[reporter_id])
    resolved_by = db.relationship("User", foreign_keys=[resolved_by_id])

    @property
    def reason_label(self) -> str:
        return REPORT_REASONS.get(self.reason, self.reason)

    def resolve(self, admin: "User", status: str, note: str | None = None) -> None:
        if status not in (REPORT_ACTIONED, REPORT_DISMISSED):
            raise ValueError("A report can only be resolved as actioned or dismissed.")
        self.status = status
        self.resolved_by = admin
        self.resolved_at = utcnow()
        self.resolution_note = note


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (db.Index("ix_notification_user_read_created", "user_id", "is_read", "created_at"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    body = db.Column(db.String(500), nullable=False, default="")
    link_url = db.Column(db.String(500), nullable=False, default="/")
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", backref=db.backref("notifications", cascade="all, delete-orphan"))


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    target_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    admin = db.relationship("User", foreign_keys=[admin_id])
    target_user = db.relationship("User", foreign_keys=[target_user_id])


class Banner(db.Model):
    __tablename__ = "banners"
    id = db.Column(db.Integer, primary_key=True)
    university_id = db.Column(db.Integer, db.ForeignKey("universities.id"), nullable=True, index=True)
    title = db.Column(db.String(150), nullable=False)
    subtitle = db.Column(db.String(300), nullable=False, default="")
    image_key = db.Column(db.String(255), nullable=False)
    fit_mode = db.Column(db.String(10), nullable=False, default="fill", server_default="fill")
    focus = db.Column(db.String(10), nullable=False, default="center", server_default="center")
    link_url = db.Column(db.String(500))
    button_text = db.Column(db.String(40))
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    starts_at = db.Column(db.DateTime(timezone=True))
    ends_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    university = db.relationship("University")


class AdRequest(db.Model):
    __tablename__ = "ad_requests"
    __table_args__ = (db.CheckConstraint("status IN ('new','contacted','accepted','rejected')", name="ck_ad_request_status"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    university_id = db.Column(db.Integer, db.ForeignKey("universities.id"), nullable=False, index=True)
    business_name = db.Column(db.String(150), nullable=False)
    contact_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    message = db.Column(db.String(1000), nullable=False)
    image_key = db.Column(db.String(255))
    status = db.Column(db.String(20), nullable=False, default="new", server_default="new", index=True)
    admin_note = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    user = db.relationship("User", backref=db.backref("ad_requests", lazy="dynamic"))
    university = db.relationship("University")
