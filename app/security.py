"""Sign-in session, screen permissions and CSRF protection.

CONTROLLERS call `require("MENU_CODE", "edit")` etc.; the answer comes from the role tables in the MODEL.
"""
import hmac
import secrets
from functools import wraps
from urllib.parse import urlparse

from flask import abort, g, redirect, request, session, url_for

from .models.role import Role, RoleAuth, ADMIN
from .models.user import User, ACTIVE


# ---------- session ----------
def current_user():
    """The signed-in user, re-read on every request so a locked or changed account takes effect at once."""
    if "user" not in g:
        g.user = None
        user_id, branch = session.get("user_id"), session.get("branch")
        if user_id:
            user = User.find(user_id)
            role = Role.find(user["ROLE_CODE"]) if user else None
            ok = (user and user["USER_STATUS"] == ACTIVE and role and role.get("ROLE_STATUS") == "A"
                  and branch in User.allowed_branches(user))
            if ok:
                g.user = user
            else:
                logout_user()
    return g.user


def operating_branch():
    return session.get("branch")


def login_user(user, branch):
    lang = session.get("lang")
    session.clear()                              # a fresh session for every sign-in
    session.permanent = True
    session["user_id"], session["branch"] = user["USER_ID"], branch
    if lang:
        session["lang"] = lang
    g.pop("user", None)


def logout_user():
    lang = session.get("lang")
    session.clear()
    if lang:
        session["lang"] = lang
    g.user = None


# ---------- permissions ----------
def can(menu_code, perm="view"):
    user = current_user()
    return bool(user) and RoleAuth.can(user["ROLE_CODE"], menu_code, perm)


def is_admin():
    user = current_user()
    return bool(user) and user["ROLE_CODE"] == ADMIN


def require_login(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapper


def require(menu_code, perm="view"):
    """Decorator: the signed-in role must hold `perm` on `menu_code` (403 otherwise)."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not current_user():
                return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
            if not can(menu_code, perm):
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def maintainable_branches():
    """Branches whose settings the user may maintain: every branch for ALL, otherwise the user's own."""
    user = current_user()
    return User.allowed_branches(user) if user else []


def pick_maintenance_branch(requested):
    allowed = maintainable_branches()
    if requested in allowed:
        return requested
    return operating_branch() if operating_branch() in allowed else (allowed[0] if allowed else None)


# ---------- CSRF ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    """Every request that changes data must carry the session's token (form field or X-CSRF-Token header)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get("csrf") or ""
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="csrf")


def safe_next(target, fallback):
    """Only follow a `next` address that stays inside this site."""
    if target:
        parts = urlparse(target)
        if not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return fallback
