"""Administration > Role Authorization (AOIMS_ROLE, AOIMS_ROLEAUTH)."""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..models.base import ValidationError
from ..models.menu import Menu, GROUPS, SUBGROUP_LABELS, LABEL_KEYS
from ..models.role import Role, RoleAuth, PERMS, ADMIN
from ..models.user import User
from ..security import can, current_user, require
from .common import flash_error, flash_ok

bp = Blueprint("admin_roles", __name__, url_prefix="/admin/roles")
MENU = "AD_ROLE"


def _rows():
    """Permission table rows: a heading per group and sub-group, then one row per screen."""
    rows = []
    for code, label_key in GROUPS:
        rows.append({"kind": "group", "label_key": label_key})
        last_sub = None
        for menu in Menu.all():
            if menu.get("MENU_GROUP") != code:
                continue
            sub = menu.get("MENU_SUBGROUP") or None
            if sub and sub != last_sub:
                rows.append({"kind": "sub", "label_key": SUBGROUP_LABELS.get(sub)})
                last_sub = sub
            rows.append({"kind": "menu", "code": menu["MENU_CODE"], "label_key": LABEL_KEYS.get(menu["MENU_CODE"]),
                         "name": menu.get("MENU_NAME")})
    return rows


def _render(role, is_new, perms, values=None):
    return render_template("admin/roles.html", roles=Role.all(), role=role, is_new=is_new, perms=perms,
                           values=values or role or {"ROLE_STATUS": "A"}, rows=_rows(), perm_names=PERMS,
                           locked=bool(role) and role["ROLE_CODE"] == ADMIN,
                           user_count=User.count_in_role(role["ROLE_CODE"]) if role else 0,
                           editable=can(MENU, "create" if is_new else "edit"))


@bp.route("/")
@require(MENU)
def role_form():
    roles = Role.all()
    if request.args.get("new") == "1":
        if not can(MENU, "create"):
            abort(403)
        return _render(None, True, {m: {p: False for p in PERMS} for m in Menu.codes()})
    code = request.args.get("role") or (roles[0]["ROLE_CODE"] if roles else None)
    role = Role.find(code) if code else None
    if not role:
        abort(404)
    return _render(role, False, RoleAuth.for_role(role["ROLE_CODE"]))


@bp.route("/save", methods=["POST"])
@require(MENU)
def role_save():
    is_new = request.form.get("is_new") == "1"
    if not can(MENU, "create" if is_new else "edit"):
        abort(403)
    code = (request.form.get("ROLE_CODE") or "").strip().upper()
    perms = {m: {p: request.form.get("perm_%s_%s" % (m, p)) == "on" for p in PERMS} for m in Menu.codes()}
    try:
        role = Role.save(code, request.form, perms, current_user()["USER_ID"], is_new)
    except ValidationError as error:
        flash_error(error)
        existing = None if is_new else Role.find(code)
        if not is_new and not existing:
            abort(404)
        return _render(existing, is_new, perms, request.form)
    flash_ok("roleSaved", code=role["ROLE_CODE"])
    return redirect(url_for("admin_roles.role_form", role=role["ROLE_CODE"]))
