"""Administration > User Maintenance (AOIMS_USER). One screen: the list, with the form card above it."""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..models.base import ValidationError
from ..models.branch import Branch
from ..models.role import Role
from ..models.user import User
from ..security import can, current_user, require
from .common import flash_error, flash_ok

bp = Blueprint("admin_users", __name__, url_prefix="/admin/users")
MENU = "AD_USER"


def _page(form_mode=None, target=None, values=None):
    """form_mode: None (list only), "new" or "edit"."""
    query = request.args.get("q", "")
    status = request.args.get("status") if request.args.get("status") in ("A", "I", "L") else None
    users = [u for u in User.search(query) if not status or u["USER_STATUS"] == status]
    all_roles = Role.all()
    roles = [r for r in all_roles if r.get("ROLE_STATUS") == "A" or (target and r["ROLE_CODE"] == target.get("ROLE_CODE"))]
    return render_template(
        "admin/users.html", users=users, total=User.count(), query=query, status=status,
        role_names={r["ROLE_CODE"]: r["ROLE_NAME"] for r in all_roles}, roles=roles, branches=Branch.ids(),
        all_branches=Branch.ALL, form_mode=form_mode, target=target, values=values or target or {},
        can_create=can(MENU, "create"), can_edit=can(MENU, "edit"))


@bp.route("/")
@require(MENU)
def user_list():
    return _page()


@bp.route("/new", methods=["GET", "POST"])
@require(MENU, "create")
def user_new():
    if request.method == "POST":
        try:
            user = User.create(request.form, current_user()["USER_ID"], current_user()["ROLE_CODE"])
        except ValidationError as error:
            flash_error(error)
            return _page("new", None, request.form)
        flash_ok("usrSavedNew", id=user["USER_ID"])
        return redirect(url_for("admin_users.user_list"))
    return _page("new", None, {"USER_STATUS": "A", "BRANCH_ID": Branch.ALL})


@bp.route("/<user_id>", methods=["GET", "POST"])
@require(MENU, "edit")
def user_edit(user_id):
    target = User.find(user_id)
    if not target:
        abort(404)
    if request.method == "POST":
        try:
            User.update(user_id, request.form, current_user()["USER_ID"], current_user()["ROLE_CODE"])
        except ValidationError as error:
            flash_error(error)
            return _page("edit", target, request.form)
        flash_ok("usrSavedEdit", id=target["USER_ID"])
        return redirect(url_for("admin_users.user_list"))
    return _page("edit", target)
