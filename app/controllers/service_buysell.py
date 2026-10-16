"""Service > Update Buy/Sell Status: the buying/selling order list of the branch signed in to.

So far the orders come from milling registration (bran/broken rice the customer takes home).
"""
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for

from ..i18n import t
from ..models.base import ValidationError
from ..models import buysell_order as bs
from ..models.buysell_order import BuySellOrder
from ..models.customer import Customer
from ..models.inventory import Inventory
from ..models.milling_order import MillingOrder
from ..models.price import MAT_BRAN, MAT_BROKEN, MAT_HUSK, MAT_PADDY, MAT_RICE
from ..models.variety import INFO as VAR_INFO, KIND_KEYS, mat_name
from ..security import can, current_user, operating_branch, require
from ..timeutil import fmt_datetime, fmt_time, now_utc, to_local, today_local
from .common import flash_error, flash_ok

bp = Blueprint("service_buysell", __name__, url_prefix="/service/buysell")
MENU = "SV_BSSTATUS"

BS_STATUS_KEYS = {bs.OPEN: "bsStatusOpen", bs.PREPARED: "bsStatusPrepared", bs.UNPAID: "bsStatusUnpaid",
                  bs.CLOSED: "bsStatusCompleted", bs.CANCELLED: "bsStatusCancelled"}
# class names of the prototype's coloured status drop-down
BS_STATUS_CLASS = {bs.OPEN: "open", bs.PREPARED: "prepared", bs.UNPAID: "unpaid", bs.CLOSED: "completed", bs.CANCELLED: "cancelled"}
TYPE_KEYS = {bs.BUY: "svcBuyLabel", bs.SELL: "svcSellLabel"}
MAT_KEYS = {code: KIND_KEYS[kind] for code, (kind, _v) in VAR_INFO.items()}          # kind label of each SKU (no variety)
INV_STATUS_KEYS = {"A": "statusAvailable", "B": "statusBlocked", "U": "statusUnableShip"}
LIST_FILTERS = (bs.OPEN, bs.PREPARED, bs.UNPAID, bs.CLOSED)


def _decorate(orders):
    customers, millings = {}, {}
    suggest = None
    for order in orders:
        code = order["CUST_CODE"]
        if code not in customers:
            customers[code] = Customer.find(code) or {}
        order["customer"] = customers[code]
        order["next_statuses"] = BuySellOrder.allowed_next(order)
        order["is_open"] = BuySellOrder.is_open(order)
        order["amount"] = BuySellOrder.amount(order)
        order["lines"] = [dict(l, name=mat_name(l["MAT_CODE"])) for l in BuySellOrder.items_of(order)]
        order["lines_text"] = ", ".join("%s %s kg" % (l["name"], ("%.2f" % l["ORD_QTY"]).rstrip("0").rstrip("."))
                                        for l in order["lines"] if l["ORD_QTY"] > 0)
        order["can_maintain"] = BuySellOrder.can_maintain(order)
        registered = to_local(order["REGIST_DATE"])
        order["registered_text"] = fmt_time(registered) if registered.date() == today_local() else fmt_datetime(registered)
        milling = None
        if order.get("REF_ORD_TYPE") == "ML":
            ref = order["REF_ORD_ID"]
            if ref not in millings:
                millings[ref] = MillingOrder.find(ref) or {}
            milling = millings[ref]
        order["milling"] = milling
        order["milling_ready"] = not milling or milling.get("MILL_STATUS") in bs.MILL_READY
        if bs.PREPARED in order["next_statuses"]:
            if milling and milling.get("REF_INV_LOC"):
                order["suggest_loc"] = milling["REF_INV_LOC"]       # next to the customer's rice
            else:
                if suggest is None:
                    suggest = Inventory.last_location(order["BRANCH_ID"])
                order["suggest_loc"] = suggest
        order["needs_inbound"] = BuySellOrder.needs_inbound(order)
        if bs.CLOSED in order["next_statuses"]:
            if BuySellOrder.is_linked(order):
                order["close_text"] = t("confirmCloseBsOrder", id=order["QR_ID"],
                                        stock=Inventory.describe(Inventory.for_order(order["BRANCH_ID"], order["ORD_ID"])) or "-")
            elif order["ORD_TYPE"] == bs.SELL:
                check = BuySellOrder.stock_check(order["BRANCH_ID"], [{"mat": order["MAT_CODE"], "qty": order["ORD_QTY"]}])[0]
                text = t("confirmCloseSellDirect", id=order["QR_ID"], qty="%.2f" % order["ORD_QTY"],
                         mat=mat_name(order["MAT_CODE"]), available="%.2f" % check["available"])
                if check["short"]:
                    text += "\n\n" + t("insufficientStockWarning", available="%.2f" % check["available"], needed="%.2f" % order["ORD_QTY"])
                order["close_text"] = text
            else:
                order["close_text"] = t("confirmCloseBuy", id=order["QR_ID"], amount="%.2f" % order["amount"])
    return orders


@bp.route("/")
@require(MENU)
def order_list():
    status = request.args.get("status") if request.args.get("status") in LIST_FILTERS else None
    ord_type = request.args.get("type") if request.args.get("type") in (bs.BUY, bs.SELL) else None
    orders = BuySellOrder.for_branch(operating_branch(), [status] if status else None, ord_type)
    q = (request.args.get("q") or "").strip()[:60]
    orders = _decorate(BuySellOrder.sort_for_list(orders))
    if q:
        orders = [o for o in orders if _matches(o, q)]
    return render_template("service/buysell_list.html", orders=orders, q=q,
                           status=status, ord_type=ord_type, filters=LIST_FILTERS, status_keys=BS_STATUS_KEYS,
                           status_class=BS_STATUS_CLASS, type_keys=TYPE_KEYS, mat_keys=MAT_KEYS,
                           inv_status_keys=INV_STATUS_KEYS, can_edit=can(MENU, "edit"), can_cancel=can(MENU, "delete"))


def _matches(order, q):
    """Search at the top of the list (update 33): order no. / ID, the milling order it comes from (ID or queue no.),
    customer name or code. Not case sensitive; a milling order ID opened from the Milling Order List matches exactly."""
    needle = q.strip().lower().lstrip("#")
    if needle.startswith("aoi-"):                        # update 37: the milling label QR is 'AOI-<milling order ID>'
        needle = needle[4:]
    milling = order.get("milling") or {}
    fields = [order.get("ORD_ID"), order.get("QR_ID"), order.get("REF_ORD_ID"), milling.get("QR_ID"), order.get("CUST_CODE"),
              (order.get("customer") or {}).get("CUST_NAME")]
    if needle.startswith("ml-"):
        return (order.get("REF_ORD_ID") or "").lower() == needle
    return any(needle in str(f).lower() for f in fields if f)


@bp.route("/<ord_id>/items", methods=["POST"])
@require(MENU, "edit")
def maintain(ord_id):
    """Update 39: new quantities of a take-home order's lines (bran, broken rice) before it is Unpaid / Closed."""
    order = BuySellOrder.find(ord_id, operating_branch())
    if not order:
        abort(404)
    try:
        BuySellOrder.maintain(order["ORD_ID"], operating_branch(), request.form, current_user()["USER_ID"])
    except ValidationError as error:
        flash_error(error)
    else:
        flash_ok("msgOrderUpdated", id=order["QR_ID"])
    return redirect(url_for("service_buysell.order_list", status=request.form.get("status_filter") or None,
                            type=request.form.get("type_filter") or None, q=request.form.get("q_filter") or None))


@bp.route("/<ord_id>/status", methods=["POST"])
@require(MENU, "edit")
def change_status(ord_id):
    new_status = request.form.get("new_status")
    if new_status == bs.CANCELLED and not can(MENU, "delete"):
        abort(403)
    order = BuySellOrder.find(ord_id, operating_branch())
    if not order:
        abort(404)
    try:
        BuySellOrder.change_status(order["ORD_ID"], operating_branch(), new_status, request.form,
                                   current_user()["USER_ID"], describe)
    except ValidationError as error:
        flash_error(error)
    else:
        flash_ok("msgOrderUpdated", id=order["QR_ID"])
    return redirect(url_for("service_buysell.order_list", status=request.form.get("status_filter") or None,
                            type=request.form.get("type_filter") or None, q=request.form.get("q_filter") or None))


# ---------- Customer Service > Buy / Sell (registration) ----------
REGISTER = "SV_SERVICE"


def describe(order):
    """Ledger text of a closed buying/selling order: Bought / Sold <material(s)>."""
    material = ", ".join(mat_name(l["MAT_CODE"]) for l in BuySellOrder.items_of(order) if l["ORD_QTY"] > 0) or mat_name(order["MAT_CODE"])
    return t("acctAutoBuyExpenseDesc" if order["ORD_TYPE"] == bs.BUY else "acctAutoSellIncomeDesc", mat=material)


def _json_error(error, status=422):
    if error.key == "errNoPrice":                         # name the material in the user's language
        error.params = dict(error.params, mat=mat_name(error.params.get("mat")))
    return jsonify({"ok": False, "error": t(error.key, **error.params)}), status


@bp.route("/new", methods=["POST"])
@require(REGISTER, "create")
def register():
    """Open or Close button of the Buy / Sell table. Answers in JSON for the page's script."""
    close = request.form.get("mode") == "close"
    try:
        orders = BuySellOrder.register(operating_branch(), request.form.get("ORD_TYPE"), request.form.get("CUST_CODE"),
                                       request.form, close, current_user()["USER_ID"], describe)
    except ValidationError as error:
        return _json_error(error)
    ids = [o["ORD_ID"] for o in orders]
    message = t("bsClosedMsg", count=len(orders)) if close else t("confirmOrderCreated", count=len(orders))
    return jsonify({"ok": True, "ids": ids, "closed": close, "message": message,
                    "list_url": url_for("service_buysell.order_list") if can(MENU) else None,
                    "billing_url": url_for("service_buysell.billing", ids=",".join(ids)),
                    "new_url": url_for("service_milling.new_order", cust=request.form.get("CUST_CODE"))})


@bp.route("/check", methods=["POST"])
@require(REGISTER, "create")
def check():
    """Before Close on the Sell table: the stock that will be released, with a warning when it is short."""
    ord_type = request.form.get("ORD_TYPE")
    try:
        items = BuySellOrder.parse_items(operating_branch(), ord_type, request.form)
    except ValidationError as error:
        return _json_error(error)
    lines = BuySellOrder.stock_check(operating_branch(), items) if ord_type == bs.SELL else items
    for line in lines:
        line["name"] = mat_name(line["mat"])
    return jsonify({"ok": True, "lines": lines, "suggest_loc": Inventory.last_location(operating_branch())})


@bp.route("/billing")
def billing():
    """The receipt (Print Billing) of one or more buying/selling orders, as pop-up content."""
    if not current_user():
        abort(401)
    if not (can(REGISTER) or can(MENU)):
        abort(403)
    orders = [o for o in (BuySellOrder.find(i, operating_branch()) for i in request.args.get("ids", "").split(",")[:10]) if o]
    if not orders:
        abort(404)
    customer = Customer.find(orders[0]["CUST_CODE"]) or {}
    return render_template("service/_billing.html", orders=orders, customer=customer, mat_keys=MAT_KEYS, items_of=BuySellOrder.items_of,
                           type_keys=TYPE_KEYS, total=sum(BuySellOrder.amount(o) for o in orders),
                           printed=fmt_datetime(now_utc()), address=current_app.config["LABEL_ADDRESS_TH"],
                           address_en=current_app.config["LABEL_ADDRESS_EN"], tel=current_app.config["LABEL_TEL"])
