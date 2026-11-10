import logging
import os
import re
from decimal import Decimal

import click
from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

from config import get_config
from extensions import csrf, db, limiter, login_manager, migrate
from services.mail import EmailError
from services.security import init_security

ERROR_PAGES = {
    403: ("Not allowed", "You don't have permission to see that page."),
    404: ("Page not found", "That page doesn't exist, or the listing was removed."),
    429: ("Slow down a little", "You've made too many requests. Please wait a few minutes and try again."),
    500: ("Something went wrong", "That's on us. Please try again in a moment."),
}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def naira(value) -> str:
    """12500 -> ₦12,500   |   12500.5 -> ₦12,500.50"""
    value = Decimal(value)
    if value == value.to_integral_value():
        return f"₦{value:,.0f}"
    return f"₦{value:,.2f}"


def create_app(config_class=None):
    config_class = config_class or get_config()
    config_class.validate()  # production refuses to start with unsafe/incomplete settings

    app = Flask(__name__)
    app.config.from_object(config_class)
    production = app.config["ENV_NAME"] == "production"
    if production:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if app.config["BEHIND_PROXY"]:
        # Trust exactly one proxy hop for the client IP and https. Host is NOT trusted:
        # emailed links come from PUBLIC_BASE_URL.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if app.config["STORAGE_BACKEND"] == "local":
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        if production:
            app.logger.warning(
                "Photos are stored on local disk and are lost if the host wipes its disk. "
                "Use STORAGE_BACKEND=s3, or mount a persistent volume at UPLOAD_FOLDER."
            )

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)
    init_security(app)

    import models  # noqa: F401  (registers models with SQLAlchemy)
    from blueprints.admin import admin_bp
    from blueprints.auth import auth_bp
    from blueprints.catalog import catalog_bp, feed
    from blueprints.listings import listings_bp
    from blueprints.reports import reports_bp
    from blueprints.vendor import vendor_bp
    from services.storage import image_url, thumb_url

    app.register_blueprint(auth_bp)
    app.register_blueprint(vendor_bp)
    app.register_blueprint(listings_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(reports_bp)
    app.add_url_rule("/", endpoint="index", view_func=feed)  # the catalog is the home page

    app.add_template_global(image_url, "image_url")
    app.add_template_global(thumb_url, "thumb_url")
    app.add_template_filter(naira, "naira")

    @app.route("/media/<path:filename>")
    def media(filename):
        # Only used with STORAGE_BACKEND=local. Keys are random and never reused,
        # so browsers can cache them for a year.
        return send_from_directory(
            app.config["UPLOAD_FOLDER"], filename, mimetype="image/webp", max_age=31536000
        )

    @app.route("/healthz")
    def healthz():
        """For the host's uptime check: 200 only if the database answers."""
        try:
            db.session.execute(db.text("SELECT 1"))
        except Exception:
            app.logger.exception("Health check failed")
            return "database unavailable", 503
        return "ok"

    # --- errors -----------------------------------------------------------------
    def render_error(code):
        heading, message = ERROR_PAGES[code]
        try:
            return render_template("errors/error.html", code=code, heading=heading, message=message), code
        except Exception:  # e.g. the database is down and the page can't look up the user
            return f"{heading}\n{message}\n", code

    for code in ERROR_PAGES:
        app.register_error_handler(code, lambda _error, code=code: render_error(code))

    @app.errorhandler(413)
    def too_large(_error):
        flash("Those files are too large. Keep each photo under 10 MB.", "error")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(CSRFError)
    def csrf_failed(_error):
        flash("Your session expired. Please try that again.", "error")
        return redirect(request.referrer or url_for("index"))

    @app.errorhandler(EmailError)
    def email_failed(error):
        app.logger.error("Email failed: %s", error)
        flash("We couldn't send that email right now. Please try again in a few minutes.", "error")
        return redirect(request.referrer or url_for("index"))

    register_cli(app)
    return app


def seed_defaults():
    """Idempotent: default categories and the launch university (Bingham)."""
    from models import DEFAULT_CATEGORIES, Category, University

    for name in DEFAULT_CATEGORIES:
        if not Category.query.filter_by(slug=slugify(name)).first():
            db.session.add(Category(name=name, slug=slugify(name)))

    if not University.query.filter_by(short_name="BINGHAM").first():
        db.session.add(
            University(name="Bingham University", short_name="BINGHAM", email_domain="binghamuni.edu.ng")
        )
    db.session.commit()


def register_cli(app):
    from models import ROLE_ADMIN, University, User

    @app.cli.command("init-db")
    def init_db():
        """Development: create tables directly and seed. Production uses `flask db upgrade` + `flask seed`."""
        db.create_all()
        seed_defaults()
        click.echo("Database ready.")

    @app.cli.command("seed")
    def seed():
        """Seed categories and Bingham University (safe to run repeatedly)."""
        seed_defaults()
        click.echo("Seeded.")

    @app.cli.command("add-university")
    @click.argument("name")
    @click.argument("short_name")
    @click.argument("email_domain")
    def add_university(name, short_name, email_domain):
        """flask add-university "University of Abuja" UNIABUJA uniabuja.edu.ng"""
        db.session.add(University(name=name, short_name=short_name, email_domain=email_domain.lower()))
        db.session.commit()
        click.echo(f"Added {name}.")

    @app.cli.command("create-admin")
    @click.argument("email")
    @click.argument("name")
    @click.option("--university", "short_name", default="BINGHAM", show_default=True)
    @click.password_option()
    def create_admin(email, name, short_name, password):
        """Create an admin account (admins are never self-registered)."""
        uni = University.query.filter_by(short_name=short_name).first()
        if not uni:
            raise click.ClickException(f"No university with short name {short_name}.")
        user = User(university=uni, name=name, email=email.lower(), role=ROLE_ADMIN)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo("Admin created.")


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
