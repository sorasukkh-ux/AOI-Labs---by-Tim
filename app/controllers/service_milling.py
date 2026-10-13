"""Service > Customer Service (milling order registration) and Update Milling Status.

Everything here works on the branch the user signed in to; only maintenance screens can pick a branch.
"""
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for

from ..i18n import t, texts
from ..models.base import ValidationError
from ..models.buysell_order import BuySellOrder
from ..models.customer import Customer
from ..models.inventory import Inventory, lot_of
from ..models.mill_yield import MillYield
from ..models.milling_order import MillingOrder, PRIORITIES
from ..models.rules import status_flow as flow
from ..security import can, current_user, operating_branch, require
from ..models.branch import Branch
from .service_buysell import INV_STATUS_KEYS
from ..timeutil import fmt_date, fmt_datetime, fmt_time, input_local_datetime, now_local, now_utc, to_local, today_local
from .common import flash_error, flash_ok

bp = Blueprint("service_milling", __name__, url_prefix="/service/milling")
REGISTER, STATUS = "SV_SERVICE", "SV_MILLSTATUS"

STATUS_KEYS = {flow.QUEUE: "tvQueue", flow.IN_PROCESS: "tvInProcess", flow.HOLD: "tvHold", flow.FINISHED: "tvCompleted",
               flow.DELIVERY: "tvReadyForDelivery", flow.CLOSED: "tvClosed", flow.CANCELLED: "tvCancelled"}
PRIORITY_KEYS = {"U": "priorityUrgent", "H": "priorityHigh", "M": "priorityMedium", "L": "priorityLow"}
TYPE_KEYS = {flow.TAKEAWAY: "deliveryTakeaway", flow.DELIVER: "deliveryDelivery"}
# class names of the design prototype's coloured status drop-down
STATUS_CLASS = {flow.QUEUE: "queue", flow.IN_PROCESS: "inprocess", flow.HOLD: "hold", flow.FINISHED: "completed",
                flow.DELIVERY: "ready_for_delivery", flow.CLOSED: "closed", flow.CANCELLED: "cancelled"}
LIST_FILTERS = (flow.QUEUE, flow.IN_PROCESS, flow.HOLD, flow.FINISHED, flow.CLOSED)      # the prototype's status chips


def label_data(queue_no, qr_text, customer_name, pay_total, receive, registered):
    """Everything the printed queue label shows (drawn in the browser by static/js/label.js)."""
    config = current_app.config
    return {"no": queue_no, "qr": qr_text, "customer": customer_name, "pay": "%.2f" % pay_total,
            "receive": fmt_datetime(receive), "date": fmt_date(registered), "time": fmt_time(registered),
            "addrEn": config["LABEL_ADDRESS_EN"], "addrTh": config["LABEL_ADDRESS_TH"], "tel": config["LABEL_TEL"],
            "email": config["LABEL_EMAIL"], "logo": url_for("static", filename="img/logo.png")}


def _trade_rows(branch_id):
    """The rows of the Buy and Sell tables with the branch's current prices."""
    from ..models.buysell_order import TRADE_MATERIALS
    from ..models.price import Price
    from ..models.variety import mat_name, variety_of
    return [{"mat": m, "name": mat_name(m), "variety": variety_of(m), "buy": Price.buy_price(branch_id, m),
             "sell": Price.sell_price(branch_id, m)} for m in TRADE_MATERIALS]


def default_arrival(branch_id):
    """Suggested arrival time: now + the branch's SYSP_DELIVERTIMEADD hours (2 if not set), like the prototype."""
    from datetime import timedelta
    hours = (Branch.get(branch_id) or {}).get("SYSP_DELIVERTIMEADD") or 2
    return (now_local() + timedelta(hours=float(hours))).strftime("%Y-%m-%dT%H:%M")


def _user_id():
    return current_user()["USER_ID"]


# ---------- registration ----------
def _charge_lines(charges):
    """The payment breakdown as text lines for the review panel."""
    def money(value):
        return "%.2f" % value

    def take(part):
        kg = part["own_kg"] + part["extra_kg"]
        if kg <= 0:
            return ""
        if not charges["discount_pct"] > 0:
            return t("payTakeDetailPlain", kg=money(kg), price=money(part["price"]))
        text = t("payTakeDetailOwn", kg=money(part["own_kg"]), price=money(part["price"]),
                 pct="%g" % charges["discount_pct"]) if part["own_kg"] > 0 else ""
        if part["extra_kg"] > 0:
            extra = t("payTakeDetailExtra", kg=money(part["extra_kg"]), price=money(part["price"]))
            text = (text + " " + extra) if text else t("payTakeDetailPlain", kg=money(part["extra_kg"]), price=money(part["price"]))
        return text

    lines = [{"label": t("payFeeLabel"), "amount": charges["fee"],
              "detail": t("payFeeDetail", units=charges["units"], fee=money(charges["fee_per_unit"]), kg="%g" % charges["unit_kg"])}]
    if charges["bran"]["own_kg"] + charges["bran"]["extra_kg"] > 0:
        lines.append({"label": t("payBranLabel"), "amount": charges["bran"]["amount"], "detail": take(charges["bran"])})
    if charges["broken"]["own_kg"] + charges["broken"]["extra_kg"] > 0:
        lines.append({"label": t("payBrokenLabel"), "amount": charges["broken"]["amount"], "detail": take(charges["broken"])})
    if charges["delivery"]["is_delivery"]:
        lines.append({"label": t("payDeliveryLabel"), "amount": charges["delivery"]["amount"],
                      "detail": t("payDeliveryDetail", km=money(charges["delivery"]["km"]), rate=money(charges["delivery"]["rate_per_km"]))})
    return lines


@bp.route("/new", methods=["GET", "POST"])
@require(REGISTER)
def new_order():
    branch = operating_branch()
    if request.method == "POST":
        if not can(REGISTER, "create"):
            abort(403)
        wants_json = request.accept_mimetypes.best == "application/json"
        try:
            order, _quote = MillingOrder.create(branch, request.form, _user_id())
        except ValidationError as error:
            if wants_json:
                return jsonify({"ok": False, "error": t(error.key, **error.params)}), 422
            flash_error(error)
        else:
            if wants_json:                               # Print QR on the page: the script prints the label, then asks
                customer = Customer.find(order["CUST_CODE"]) or {}
                return jsonify({
                    "ok": True, "ord_id": order["ORD_ID"], "qr_id": order["QR_ID"], "is_delivery": order["ORDER_TYPE"] == flow.DELIVER,
                    "label": label_data(order["QR_ID"], "AOI-" + order["ORD_ID"], customer.get("CUST_NAME", order["CUST_CODE"]),
                                        MillingOrder.pay_total(order), order["EXPCT_RECEIVE_TIME"], order["REGIST_DATE"]),
                    "message": t("orderCreatedMsg", id=order["QR_ID"]), "question": t("confirmGoOrderList"),
                    "list_url": url_for("service_milling.order_list") if can(STATUS) else None,
                    "new_url": url_for("service_milling.new_order")})
            flash_ok("orderCreatedMsg", id=order["QR_ID"])
            if can(STATUS):                          # the details page belongs to Update Milling Status
                return redirect(url_for("service_milling.order_detail", ord_id=order["ORD_ID"]))
            return redirect(url_for("service_milling.new_order"))
    customer = Customer.find(request.values.get("CUST_CODE") or request.args.get("cust"))
    return render_template(
        "service/milling_new.html", customer=customer, values=request.form, priorities=PRIORITIES,
        priority_keys=PRIORITY_KEYS, ratios=MillYield.all_ratios(branch), editable=can(REGISTER, "create"),
        can_add_customer=can("AD_CUSTOMER", "create"), service=request.args.get("svc") if request.args.get("svc") in ("buy", "sell") else "milling",
        trade_rows=_trade_rows(branch), bs_editable=can(REGISTER, "create"), inv_status_keys=INV_STATUS_KEYS,
        bs_text=texts("alertCustInfoRequired", "errNoItems", "errServer", "noStockAvailable", "insufficientStockWarning",
                      "confirmCloseQuestion", "sellReleaseDetailsTitle", "colSku", "colLocation", "colOnHand", "colQty",
                      "confirmOrderCreated", "errLocFormat"),
        js_text=texts("custListEmpty", "errCustomerRequired", "alertEnterPaddy", "errServer", "btnPrintQR", "btnConfirmOrder"))


@bp.route("/customers.json")
def customer_search():
    """Customer search for registration and for changing the customer in the order details."""
    if not current_user():
        abort(401)
    if not (can(REGISTER) or can(STATUS, "edit")):
        abort(403)
    code = request.args.get("code", "").strip()
    exact = None
    if code:                                             # Scan QR field: the exact customer, or the codes that contain the text
        exact = Customer.find(code)
        found = [exact] if exact else Customer.search_code(code, limit=8)
    else:
        found = Customer.search(request.args.get("q", ""), limit=8)
    return jsonify({"exact": bool(exact), "customers": [{"code": c["CUST_CODE"], "name": c["CUST_NAME"], "mobile": c.get("MOBILE_NO") or "",
                     "line": c.get("LINE_ID") or "", "address": c.get("ADDRESS") or "", "distance": c.get("DISTANCE_KM")}
                    for c in found]})


@bp.route("/quote", methods=["POST"])
@require(REGISTER)
def quote():
    """Review: recalculates outputs, payment and receive time on the server. Nothing is saved."""
    branch = operating_branch()
    try:
        data = MillingOrder.parse(branch, request.form)
        result = MillingOrder.quote(branch, data["paddy"], data["take_bran"], data["take_broken"],
                                    data["order_type"], data["distance"], data["priority"], variety=data["variety"])
    except ValidationError as error:
        return jsonify({"ok": False, "error": t(error.key, **error.params)}), 422
    charges = result["charges"]
    queue_no, ord_id = MillingOrder.preview_ids()
    return jsonify({
        "ok": True, "outputs": result["outputs"], "total": charges["total"], "lines": _charge_lines(charges),
        "receive": fmt_datetime(result["receive"]), "finish": fmt_datetime(result["finish"]),
        "orders_ahead": result["orders_ahead"], "minutes": round(result["minutes"], 1),
        "take": {"bran": data["take_bran"], "broken": data["take_broken"]}, "is_delivery": data["order_type"] == flow.DELIVER,
        # preview only: the real queue number is given when the order is saved
        "label": label_data(queue_no, "AOI-" + ord_id, data["customer"]["CUST_NAME"], charges["total"], result["receive"], now_utc()),
    })


# ---------- order list and details ----------
def _decorate(orders, details=False):
    customers = {}
    suggest_loc = None
    for order in orders:
        code = order["CUST_CODE"]
        if code not in customers:
            customers[code] = Customer.find(code) or {}
        order["customer"] = customers[code]
        order["pay_total"] = MillingOrder.pay_total(order)
        order["next_statuses"] = flow.allowed_next(order)
        order["is_open"] = flow.is_open(order)
        order["can_request_cancel"] = flow.can_request_cancel(order)
        registered = to_local(order["REGIST_DATE"])
        order["registered_text"] = fmt_time(registered) if registered.date() == today_local() else fmt_datetime(registered)
        order["lot"] = lot_of(order["ORD_ID"])
        if flow.FINISHED in order["next_statuses"]:
            if suggest_loc is None:
                suggest_loc = Inventory.last_location(order["BRANCH_ID"])
            order["suggest_loc"] = suggest_loc
        order["needs_attention"] = MillingOrder.needs_attention(order)
        order["qty_editable"] = flow.quantities_editable(order)
        if order["MILL_STATUS"] == flow.DELIVERY:
            order["arrival_input"] = input_local_datetime(order.get("DELIVERY_ARRIVAL_TIME")) or default_arrival(order["BRANCH_ID"])
        if details:
            order["linked"] = BuySellOrder.for_milling(order["ORD_ID"])
            order["stock"] = Inventory.for_order(order["BRANCH_ID"], order["ORD_ID"])
    return orders


def _context():
    from .service_buysell import BS_STATUS_KEYS, MAT_KEYS, INV_STATUS_KEYS
    return {"status_keys": STATUS_KEYS, "priority_keys": PRIORITY_KEYS, "type_keys": TYPE_KEYS, "status_class": STATUS_CLASS,
            "priorities": PRIORITIES, "flow": flow, "bs_status_keys": BS_STATUS_KEYS, "mat_keys": MAT_KEYS,
            "inv_status_keys": INV_STATUS_KEYS,
            "can_edit": can(STATUS, "edit"), "can_approve": can(STATUS, "approve"), "can_cancel": can(STATUS, "delete")}


@bp.route("/")
@require(STATUS)
def order_list():
    status = request.args.get("status") if request.args.get("status") in LIST_FILTERS else None
    order_type = request.args.get("type") if request.args.get("type") in (flow.TAKEAWAY, flow.DELIVER) else None
    orders = MillingOrder.for_branch(operating_branch(), [status] if status else None)
    if order_type:
        orders = [o for o in orders if o["ORDER_TYPE"] == order_type]
    return render_template("service/milling_list.html", orders=_decorate(MillingOrder.sort_for_list(orders)),
                           status=status, order_type=order_type, filters=LIST_FILTERS, **_context())


def _order_or_404(ord_id):
    order = MillingOrder.find(ord_id, operating_branch())
    if not order:
        abort(404)
    return order


@bp.route("/<ord_id>")
@require(STATUS)
def order_detail(ord_id):
    """The order details: a pop-up on the list (?popup=1 returns just the content) or a page after registration."""
    order = _decorate([_order_or_404(ord_id)], details=True)[0]
    label = label_data(order["QR_ID"], "AOI-" + order["ORD_ID"], order["customer"].get("CUST_NAME", order["CUST_CODE"]),
                       order["pay_total"], order["EXPCT_RECEIVE_TIME"], order["REGIST_DATE"])
    template = "service/_order_details.html" if request.args.get("popup") == "1" else "service/milling_detail.html"
    return render_template(template, order=order, label=label, popup=request.args.get("popup") == "1", **_context())


def _back(ord_id):
    if request.form.get("back") == "list":
        return redirect(url_for("service_milling.order_list", status=request.form.get("status_filter") or None,
                                type=request.form.get("type_filter") or None))
    return redirect(url_for("service_milling.order_detail", ord_id=ord_id))


def _run(ord_id, action, ok_key="msgOrderUpdated"):
    order = _order_or_404(ord_id)
    try:
        action(order)
    except ValidationError as error:
        flash_error(error)
    else:
        flash_ok(ok_key, id=order["QR_ID"])
    return _back(order["ORD_ID"])


@bp.route("/<ord_id>/status", methods=["POST"])
@require(STATUS, "edit")
def change_status(ord_id):
    new_status = request.form.get("new_status")
    if new_status == flow.CANCELLED and not can(STATUS, "delete"):
        abort(403)                                   # cancelling an order needs the Delete right
    descriptions = {"full": t("acctAutoMillingIncomeDesc"), "fee": t("acctAutoMillingFeeDesc")}
    return _run(ord_id, lambda order: MillingOrder.change_status(
        order["ORD_ID"], operating_branch(), new_status, _user_id(), request.form, descriptions))


@bp.route("/<ord_id>/priority", methods=["POST"])
@require(STATUS, "edit")
def change_priority(ord_id):
    return _run(ord_id, lambda order: MillingOrder.change_priority(
        order["ORD_ID"], operating_branch(), request.form.get("priority"), _user_id()))


@bp.route("/<ord_id>/cancel-request", methods=["POST"])
@require(STATUS, "edit")
def cancel_request(ord_id):
    return _run(ord_id, lambda order: MillingOrder.request_cancel(order["ORD_ID"], operating_branch(), _user_id()))


@bp.route("/<ord_id>/cancel-answer", methods=["POST"])
@require(STATUS, "approve")
def cancel_answer(ord_id):
    approve = request.form.get("answer") == "approve"
    return _run(ord_id, lambda order: MillingOrder.answer_cancel(
        order["ORD_ID"], operating_branch(), approve, request.form.get("reason"), _user_id()))


@bp.route("/<ord_id>/type", methods=["POST"])
@require(STATUS, "edit")
def change_type(ord_id):
    return _run(ord_id, lambda order: MillingOrder.change_type(
        order["ORD_ID"], operating_branch(), request.form.get("new_type"), request.form, _user_id()))


@bp.route("/<ord_id>/delivery", methods=["POST"])
@require(STATUS, "edit")
def update_delivery(ord_id):
    """Ready for Delivery: confirm the delivery time (action=confirm) or reopen it after a failed delivery (action=reopen)."""
    if request.form.get("action") == "reopen":
        return _run(ord_id, lambda order: MillingOrder.reopen_delivery(order["ORD_ID"], operating_branch(), _user_id()))
    return _run(ord_id, lambda order: MillingOrder.confirm_delivery(
        order["ORD_ID"], operating_branch(), request.form, _user_id()), "msgDeliveryConfirmed")


@bp.route("/<ord_id>/details", methods=["POST"])
@require(STATUS, "edit")
def update_details(ord_id):
    """The editable order details pop-up."""
    return _run(ord_id, lambda order: MillingOrder.update_details(
        order["ORD_ID"], operating_branch(), request.form, _user_id()))
