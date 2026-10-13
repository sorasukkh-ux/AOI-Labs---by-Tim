"""Sign in, sign out and language switch."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from ..i18n import t
from ..models.branch import Branch
from ..models.role import Role
from ..models.user import User
from ..security import current_user, login_user, logout_user, safe_next
from .common import branch_label

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("home.index"))
    branches = Branch.ids()
    default_branch = current_app.config["DEFAULT_LOGIN_BRANCH"]
    chosen = request.form.get("branch") or (default_branch if default_branch in branches else (branches[0] if branches else ""))
    error = None
    if request.method == "POST":
        user_id, password = (request.form.get("user_id") or "").strip(), request.form.get("password") or ""
        if not user_id or not password:
            error = t("loginErrRequired")
        else:
            user, error_key, params = User.authenticate(user_id, password)
            if error_key:
                error = t(error_key, **params)
            else:
                role = Role.find(user["ROLE_CODE"])
                allowed = User.allowed_branches(user)
                if not role or role.get("ROLE_STATUS") != "A" or not allowed:
                    error = t("loginErrNoAccess")
                else:
                    # A user fixed to one branch always works there; a user authorized for all picks one.
                    branch = chosen if user["BRANCH_ID"] == Branch.ALL else allowed[0]
                    if branch not in allowed:
                        error = t("loginErrBranch")
                    else:
                        login_user(user, branch)
                        if user["BRANCH_ID"] != Branch.ALL and chosen != branch:
                            flash(t("loginBranchFixed", branch=branch_label(branch)), "ok")
                        return redirect(safe_next(request.args.get("next"), url_for("home.index")))
    return render_template("login.html", branches=branches, chosen=chosen, error=error,
                           entered_user=request.form.get("user_id", ""), no_users=User.count() == 0)


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/lang/<code>")
def set_language(code):
    if code in current_app.config["LANGUAGES"]:
        session["lang"] = code
    return redirect(safe_next(request.args.get("next"), url_for("home.index")))
