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
    if perms.get("AT_AI", {}).get("view"):                # update 48: server health risk -> red "!" on AI
        badges["AT_AI"] = 1 if server_health_alert() else 0
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


def server_health_alert():
    """Update 48: the newest server health check when it is not ok ({"level", "finding"}), else None."""
    from ..models import health
    try:
        doc = health.latest()
    except Exception:                                     # noqa: BLE001 - the menu always opens
        return None
    if doc["LEVEL"] == health.OK:
        return None
    return {"level": doc["LEVEL"], "finding": doc["FINDINGS"][0]}


@bp.route("/")
@require_login
def index():
    user = current_user()
    groups = build_menu(user, operating_branch())
    codes = [g["code"] for g in groups]
    active = request.args.get("g") if request.args.get("g") in codes else (codes[0] if codes else None)
    alert = server_health_alert() if RoleAuth.can(user["ROLE_CODE"], "AT_AI", "view") else None
    return render_template("home.html", groups=groups, active=active, health_alert=alert)


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


# ---------- update 65: notices for the staff (a customer linked their LINE account) ----------
NOTICE_MENUS = ("SV_SERVICE", "SV_MILLSTATUS", "SV_BSSTATUS", "AD_CUSTOMER")
NOTICE_MAX_MINUTES = 30                          # a page opened later does not show older links


def notices_allowed(user):
    return bool(user) and any(RoleAuth.can(user["ROLE_CODE"], m, "view") for m in NOTICE_MENUS)


@bp.route("/notices.json")
@require_login
def notices():
    """Customers who linked their LINE account after `since` (ISO time, UTC), for the toast on every staff page.
    Answers {"now": ISO, "items": [{code, name, line_name, via, when, url}]}."""
    from datetime import datetime, timedelta, timezone
    from flask import jsonify
    from ..i18n import t
    from ..models.base import col, NO_ID
    from ..models.customer import Customer
    from ..timeutil import now_utc, to_local
    now = now_utc()
    if not notices_allowed(current_user()):
        return jsonify({"now": now.isoformat(), "items": []})
    try:
        since = datetime.fromisoformat(request.args.get("since", "").replace(" ", "+"))   # an unencoded "+00:00"
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
    except ValueError:
        since = now
    since = max(since, now - timedelta(minutes=NOTICE_MAX_MINUTES))
    if now.tzinfo is None:                       # the memory database keeps naive UTC times
        since = since.replace(tzinfo=None)
    items = []
    for c in col(Customer.COL).find({"LINE_LINK_DATE": {"$gt": since}}, NO_ID).sort([("LINE_LINK_DATE", 1)]).limit(20):
        if not c.get("LINE_USER_ID"):
            continue
        can_open = RoleAuth.can(current_user()["ROLE_CODE"], "AD_CUSTOMER", "view")
        items.append({"code": c["CUST_CODE"], "name": c.get("CUST_NAME") or c["CUST_CODE"], "line_name": c.get("LINE_DISPLAY_NAME"),
                      "via": t("lnVia_" + (c.get("LINE_LINK_VIA") or "TICKET")), "when": to_local(c["LINE_LINK_DATE"]).strftime("%H:%M"),
                      "url": url_for("admin_customers.customer_view", code=c["CUST_CODE"]) if can_open else None})
    return jsonify({"now": now.isoformat(), "items": items})
