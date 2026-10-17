"""AOI Labs - by Tim. Rice mill system, MVC structure:

    MODEL       app/models        MongoDB collections and business rules
    VIEW        app/views         HTML5 templates, CSS and JavaScript
    CONTROLLER  app/controllers   URL routes: read the request, check permissions, call models, pick a view
"""
import logging
import secrets

try:                                              # reads .env when python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:                               # pragma: no cover
    pass

from flask import Flask, render_template, request

from . import i18n, security, timeutil
from .config import Config
from .db import DatabaseUnavailable, init_db
from .models.schema import ensure_indexes


def create_app(overrides=None):
    app = Flask(__name__, template_folder="views/templates", static_folder="views/static")
    app.config.from_object(Config)
    app.config.update(overrides or {})
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        app.logger.warning("AOI_SECRET_KEY is not set: using a temporary key, so everyone is signed out on restart.")

    init_db(app)
    from .models import health                     # update 48: the app's warnings / errors also go to AOITS_SYSLOG
    health.install(app)
    with app.app_context():
        ensure_indexes()
        if app.config["DB_BACKEND"] == "memory":
            from scripts.seed import seed
            seed(log=lambda _message: None)
            app.logger.warning("AOI_DB=memory: demo data only, nothing is saved when the server stops.")

    from .controllers import register_controllers
    register_controllers(app)

    from . import scheduler
    scheduler.start(app)

    # ---- helpers available in every template and macro (VIEW) ----
    from .controllers.common import branch_label
    from .models import variety, phone
    def fmt_iso(text):                               # update 49: an ISO time from the health figures, shown local
        if not text:
            return "-"
        from datetime import datetime as _dt
        from .timeutil import fmt_datetime as _fd
        try:
            return _fd(_dt.fromisoformat(text))
        except ValueError:
            return text
    app.jinja_env.globals["fmt_iso"] = fmt_iso
    app.jinja_env.globals.update(
        mat_name=variety.mat_name, varieties=variety.VARIETIES, var_keys=variety.VAR_KEYS, default_priority=variety.DEFAULT_PRIORITY,
        phone_of=phone.of, phone_countries=phone.COUNTRIES, phone_default=phone.DEFAULT, variety_of=variety.variety_of,
        t=i18n.t, can=security.can, csrf_token=security.csrf_token, branch_label=branch_label,
        fmt_datetime=timeutil.fmt_datetime, fmt_date=timeutil.fmt_date, fmt_time=timeutil.fmt_time)

    @app.context_processor
    def request_values():
        user = security.current_user()
        role_name = ""
        if user:
            from .models.role import Role
            role_name = (Role.find(user["ROLE_CODE"]) or {}).get("ROLE_NAME") or user["ROLE_CODE"]
        return {"lang": i18n.get_lang(), "user": user, "role_name": role_name, "op_branch": security.operating_branch(),
                "db_is_memory": app.config["DB_BACKEND"] == "memory"}

    @app.template_filter("num")
    def number_filter(value, places=2):
        try:
            return ("{:,.%df}" % places).format(float(value or 0))
        except (TypeError, ValueError):
            return ""

    @app.template_filter("duration")
    def duration_filter(minutes):
        """Minutes as '45 min' or '2 h 05 min' (the timeline and the analysis)."""
        if minutes is None:
            return "-"
        total = int(round(float(minutes)))
        return "%d min" % total if total < 60 else "%d h %02d min" % (total // 60, total % 60)

    @app.template_filter("plain")
    def plain_number(value):
        """50.0 -> 50, 0.273 -> 0.273 (for number fields); text such as a re-shown form value passes through."""
        return ("%.6f" % value).rstrip("0").rstrip(".") if isinstance(value, float) else ("" if value is None else value)

    @app.before_request
    def start_timer():                                   # update 49: API monitoring (models/monitor.py)
        from flask import g
        import time as _t
        g._aoi_t0 = _t.perf_counter()

    @app.before_request
    def protect_forms():
        # the device API is used by machines with their own device key, not by a signed-in browser
        if request.endpoint != "static" and request.blueprint not in ("device_api", "line_api"):
            security.check_csrf()

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; img-src 'self' data:; frame-ancestors 'self'; form-action 'self'; "
                                    # update 34: the SCADA fonts (Chakra Petch, IBM Plex Sans Thai) from Google Fonts;
                                    # without internet the system font is used
                                    "style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com")
        if request.endpoint != "static":
            response.headers.setdefault("Cache-Control", "no-store")
            from flask import g
            import time as _t
            t0 = getattr(g, "_aoi_t0", None)
            if t0 is not None:
                from .models import monitor
                monitor.record_request(request.path, request.blueprint, response.status_code, (_t.perf_counter() - t0) * 1000.0)
        return response

    def error_page(code, key):
        return render_template("error.html", code=code, message=i18n.t(key)), code

    app.register_error_handler(400, lambda e: error_page(400, "errBadRequest"))
    app.register_error_handler(403, lambda e: error_page(403, "errForbidden"))
    app.register_error_handler(404, lambda e: error_page(404, "errNotFound"))
    app.register_error_handler(413, lambda e: error_page(413, "errBadRequest"))

    @app.errorhandler(500)
    def server_error(error):
        logging.getLogger("aoi").exception("Unhandled error: %s", error)
        return error_page(500, "errServer")

    return app


__all__ = ["create_app", "DatabaseUnavailable"]
