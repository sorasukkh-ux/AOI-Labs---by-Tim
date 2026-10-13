"""Home screen: the menu the signed-in role is allowed to see."""
from flask import Blueprint, abort, render_template, request, url_for

from ..models.menu import Menu, GROUPS, SUBGROUP_LABELS, LABEL_KEYS, ICONS
from ..models.milling_order import MillingOrder
from ..models.role import RoleAuth
from ..security import current_user, operating_branch, require_login
from .common import MENU_ENDPOINTS

bp = Blueprint("home", __name__)


def menu_url(menu_code):
    endpoint = MENU_ENDPOINTS.get(menu_code)
    return url_for(endpoint) if endpoint else url_for("home.pending", menu_code=menu_code)


def build_menu(user, branch):
    """Groups -> sub-groups -> tiles, keeping only the screens the role may View."""
    perms = RoleAuth.for_role(user["ROLE_CODE"])
    badges = {"SV_MILLSTATUS": MillingOrder.count_attention(branch)}
    groups = []
    for code, label_key in GROUPS:
        sections = []                                  # [(sub-group label key or None, [tiles])]
        for menu in Menu.all():
            if menu.get("MENU_GROUP") != code or not perms.get(menu["MENU_CODE"], {}).get("view"):
                continue
            sub = menu.get("MENU_SUBGROUP") or None
            tile = {"code": menu["MENU_CODE"], "label_key": LABEL_KEYS.get(menu["MENU_CODE"]),
                    "name": menu.get("MENU_NAME"), "icon": ICONS.get(menu["MENU_CODE"], "□"),
                    "url": menu_url(menu["MENU_CODE"]), "built": menu["MENU_CODE"] in MENU_ENDPOINTS,
                    "badge": badges.get(menu["MENU_CODE"], 0)}
            if sections and sections[-1][0] == sub:
                sections[-1][1].append(tile)
            else:
                sections.append((sub, [tile]))
        if sections:
            groups.append({"code": code, "label_key": label_key,
                           "sections": [{"label_key": SUBGROUP_LABELS.get(sub), "tiles": tiles} for sub, tiles in sections],
                           "alert": any(tile["badge"] for _sub, tiles in sections for tile in tiles)})
    return groups


@bp.route("/")
@require_login
def index():
    groups = build_menu(current_user(), operating_branch())
    codes = [g["code"] for g in groups]
    active = request.args.get("g") if request.args.get("g") in codes else (codes[0] if codes else None)
    return render_template("home.html", groups=groups, active=active)


@bp.route("/planned/<menu_code>")
@require_login
def pending(menu_code):
    """Screens that exist in the design prototype but are not built in the base project yet."""
    menu = next((m for m in Menu.all() if m["MENU_CODE"] == menu_code), None)
    if not menu or menu_code in MENU_ENDPOINTS:
        abort(404)
    if not RoleAuth.can(current_user()["ROLE_CODE"], menu_code, "view"):
        abort(403)
    return render_template("pending.html", menu=menu, label_key=LABEL_KEYS.get(menu_code), group=menu.get("MENU_GROUP"))
