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
from ..models import notify, mill_event, phone, line_link
from ..models.milling_order import MillingOrder, PRIORITIES
from ..models.rules import status_flow as flow
from ..models.variety import mat_name
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
# update 46: the list in two views - active orders, and closed / cancelled ones
ACTIVE_STATUSES = (flow.QUEUE, flow.IN_PROCESS, flow.HOLD, flow.FINISHED, flow.DELIVERY)
ENDED_STATUSES = (flow.CLOSED, flow.CANCELLED)
CLOSED_LIMIT = 200                                     # the closed view shows the newest ones


def label_data(queue_no, qr_text, customer_name, pay_total, receive, registered, customer=None):
    """Everything the printed queue label shows (drawn in the browser by static/js/label.js)."""
    config = current_app.config
    return {"no": queue_no, "qr": qr_text, "customer": customer_name, "pay": "%.2f" % pay_total,
            "receive": fmt_datetime(receive), "date": fmt_date(registered), "time": fmt_time(registered),
            "addrEn": config["LABEL_ADDRESS_EN"], "addrTh": config["LABEL_ADDRESS_TH"], "tel": config["LABEL_TEL"],
            "email": config["LABEL_EMAIL"], "logo": url_for("static", filename="img/logo.png"),
            # second QR: the customer adds the LINE Official Account and links LINE to this order in one scan.
            # Only while the customer has no LINE yet: once linked, every later order reaches the same LINE.
            "line": line_link.ticket_url(qr_text[4:]) if qr_text.startswith("AOI-ML-") and not (customer or {}).get("LINE_USER_ID") else None}


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
            line_sent = _send_label(order, request.form.get("PUBLIC_BASE"))     # update 47: the QR to the customer's LINE
            if wants_json:                               # Print QR on the page: the script prints the label, then asks
                customer = Customer.find(order["CUST_CODE"]) or {}
                return jsonify({
                    "ok": True, "ord_id": order["ORD_ID"], "qr_id": order["QR_ID"], "is_delivery": order["ORDER_TYPE"] == flow.DELIVER,
                    "label": label_data(order["QR_ID"], "AOI-" + order["ORD_ID"], customer.get("CUST_NAME", order["CUST_CODE"]),
                                        MillingOrder.pay_total(order), order["EXPCT_RECEIVE_TIME"], order["REGIST_DATE"], customer),
                    "message": t("orderCreatedMsg", id=order["QR_ID"]) + (("\n" + t(line_sent)) if line_sent else ""),
                    "question": t("confirmGoOrderList"),
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


def _send_label(order, browser_origin, force=False):
    """Update 47: the queue number and QR picture to the customer's LINE, when the customer is linked to the LINE OA.
    Returns the text key for the staff (lnLabelSent / lnLabelText / error key) or None when nothing was sent."""
    customer = Customer.find(order["CUST_CODE"]) or {}
    if not (customer.get("LINE_USER_ID") or force):
        return None
    image = notify.qr_image_url(order, browser_origin)
    doc = notify.queue(order, "label", order.get("EXPCT_RECEIVE_TIME"), image_url=image)
    if not doc:
        return "lnLabelOff" if force else None
    if doc["NOTIFY_FLG"] != notify.SENT:
        return notify.reason_key(doc.get("ERROR"))
    return "lnLabelSent" if image else "lnLabelText"


@bp.route("/<ord_id>/line-qr", methods=["POST"])
@require(STATUS, "edit")
def line_qr(ord_id):
    """Update 47: Send QR to LINE (order details) - the queue number and the QR picture again."""
    order = _order_or_404(ord_id)
    key = _send_label(order, request.form.get("PUBLIC_BASE"), force=True)
    if key in ("lnLabelSent", "lnLabelText"):
        flash_ok(key)
    else:
        flash_error(ValidationError(key or "lnFailed"))
    return _back(order["ORD_ID"])


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
        if exact and exact.get("CUST_STATUS") == "D":
            exact = None
        found = [exact] if exact else Customer.search_code(code, limit=8)
    else:
        found = Customer.search(request.args.get("q", ""), limit=8)
    return jsonify({"exact": bool(exact), "customers": [{"code": c["CUST_CODE"], "name": c["CUST_NAME"], "mobile": phone.of(c),
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
        "label": label_data(queue_no, "AOI-" + ord_id, data["customer"]["CUST_NAME"], charges["total"], result["receive"], now_utc(),
                            data["customer"]),
    })


# ---------- order list and details ----------
def _decorate(orders, details=False):
    customers = {}
    suggest_loc = None
    messages = notify.last_for(o["ORD_ID"] for o in orders)
    linked_open = BuySellOrder.open_for_milling(o["ORD_ID"] for o in orders if flow.is_open(o))
    for order in orders:
        order["notify"] = messages.get(order["ORD_ID"])
        code = order["CUST_CODE"]
        if code not in customers:
            customers[code] = Customer.find(code) or {}
        order["customer"] = customers[code]
        order["pay_total"] = MillingOrder.pay_total(order)
        order["next_statuses"] = flow.allowed_next(order)
        order["linked_open"] = linked_open.get(order["ORD_ID"], [])       # Close / Cancel wait for these (update 28)
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
        if order["MILL_STATUS"] == flow.DELIVERY or flow.DELIVERY in order["next_statuses"]:
            order["arrival_input"] = input_local_datetime(order.get("DELIVERY_ARRIVAL_TIME")) or default_arrival(order["BRANCH_ID"])
        if details:
            order["events"] = mill_event.for_order(order["ORD_ID"])
            order["linked"] = BuySellOrder.for_milling(order["ORD_ID"])
            for bso in order["linked"]:                      # update 39: one order, a line per SKU
                lines = [l for l in BuySellOrder.items_of(bso) if l["ORD_QTY"] > 0] or BuySellOrder.items_of(bso)[:1]
                bso["mat_text"] = ", ".join(mat_name(l["MAT_CODE"]) for l in lines)
                bso["qty_text"] = " + ".join("%s kg" % ("%.2f" % l["ORD_QTY"]) for l in lines)
            order["stock"] = Inventory.for_order(order["BRANCH_ID"], order["ORD_ID"])
    return orders


def _context():
    from .service_buysell import BS_STATUS_KEYS, MAT_KEYS, INV_STATUS_KEYS
    return {"ln_reason": notify.reason_key, "status_keys": STATUS_KEYS, "priority_keys": PRIORITY_KEYS, "type_keys": TYPE_KEYS, "status_class": STATUS_CLASS,
            "priorities": PRIORITIES, "flow": flow, "bs_status_keys": BS_STATUS_KEYS, "mat_keys": MAT_KEYS,
            "inv_status_keys": INV_STATUS_KEYS,
            "can_edit": can(STATUS, "edit"), "can_approve": can(STATUS, "approve"), "can_cancel": can(STATUS, "delete")}


@bp.route("/")
@require(STATUS)
def order_list():
    view = "closed" if request.args.get("view") == "closed" else "active"
    statuses = ENDED_STATUSES if view == "closed" else ACTIVE_STATUSES
    status = request.args.get("status") if request.args.get("status") in statuses else None
    order_type = request.args.get("type") if request.args.get("type") in (flow.TAKEAWAY, flow.DELIVER) else None
    q = (request.args.get("q") or "").strip()[:60]
    branch = operating_branch()
    every = MillingOrder.for_branch(branch, ACTIVE_STATUSES + ENDED_STATUSES)
    if order_type:
        every = [o for o in every if o["ORDER_TYPE"] == order_type]
    if q:                                                # update 37: search (typed, or the QR on the label scanned)
        every = [o for o in every if _matches(dict(o, customer=Customer.find(o["CUST_CODE"]) or {}), q)]
    counts = {"active": sum(1 for o in every if o["MILL_STATUS"] in ACTIVE_STATUSES),
              "closed": sum(1 for o in every if o["MILL_STATUS"] in ENDED_STATUSES)}
    orders = [o for o in every if o["MILL_STATUS"] in ((status,) if status else statuses)]
    base = MillingOrder.list_base(branch)
    if view == "closed":                                 # the most recently closed / cancelled first
        orders = sorted(orders, key=lambda o: o.get("LAST_UPDATE") or o["REGIST_DATE"], reverse=True)[:CLOSED_LIMIT]
    else:
        orders = MillingOrder.sort_for_list(orders, base)
    orders = _decorate(orders)
    exact = bool(q) and len(orders) == 1 and orders[0]["ORD_ID"].lower() == scan_text(q)
    # a scanned / typed order that is in the other view: open that view (the label of a closed order, say)
    if q and not orders and counts["closed" if view == "active" else "active"]:
        return redirect(url_for("service_milling.order_list", view=None if view == "closed" else "closed", type=order_type, q=q))
    status_counts = {s: sum(1 for o in every if o["MILL_STATUS"] == s) for s in statuses}
    return render_template("service/milling_list.html", orders=orders, q=q, auto_open=exact, list_base=base, view=view,
                           view_arg="closed" if view == "closed" else None, counts=counts, status_counts=status_counts,
                           closed_limit=CLOSED_LIMIT, status=status, order_type=order_type, filters=statuses, **_context())


def scan_text(q):
    """What was typed or scanned, for comparing: the label QR is 'AOI-<order ID>'; '#0012' is the queue no."""
    needle = (q or "").strip().lower().lstrip("#")
    return needle[4:] if needle.startswith("aoi-") else needle


def _matches(order, q):
    """Milling order list search: order ID (exact when it is a whole ID, e.g. the label QR), queue no.,
    customer code or name. Not case sensitive."""
    needle = scan_text(q)
    if not needle:
        return True
    if needle.startswith("ml-"):
        return order["ORD_ID"].lower() == needle or (len(needle) < 16 and needle in order["ORD_ID"].lower())
    fields = [order.get("QR_ID"), order.get("CUST_CODE"), (order.get("customer") or {}).get("CUST_NAME")]
    return any(needle in str(f).lower() for f in fields if f)


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
                       order["pay_total"], order["EXPCT_RECEIVE_TIME"], order["REGIST_DATE"], order["customer"])
    template = "service/_order_details.html" if request.args.get("popup") == "1" else "service/milling_detail.html"
    return render_template(template, order=order, label=label, popup=request.args.get("popup") == "1", **_context())


def _back(ord_id):
    if request.form.get("back") == "list":
        return redirect(url_for("service_milling.order_list", status=request.form.get("status_filter") or None,
                                type=request.form.get("type_filter") or None, q=request.form.get("q_filter") or None,
                                view=request.form.get("view_filter") or None))
    return redirect(url_for("service_milling.order_detail", ord_id=ord_id))


def _run(ord_id, action, ok_key="msgOrderUpdated"):
    order = _order_or_404(ord_id)
    started = now_utc()
    try:
        action(order)
    except ValidationError as error:
        flash_error(error)
    else:
        flash_ok(ok_key, id=order["QR_ID"])
        _flash_line(order, started)
    return _back(order["ORD_ID"])


def _flash_line(order, since):
    """Tells the operator what happened to the LINE message written by this change, if any."""
    from flask import flash
    doc = notify.last_for([order["ORD_ID"]]).get(order["ORD_ID"])
    if not doc or not doc.get("LAST_UPDATE") or doc["LAST_UPDATE"] < since:
        return
    if doc["NOTIFY_FLG"] == notify.SENT:
        flash(t("lnSentNotice", message=doc["NOTIFY_MESSAGE"]), "ok")
    else:
        error = doc.get("ERROR") or ""
        flash(t("lnNotSentNotice", reason=t(error) if error.startswith("ln") else error), "error")


@bp.route("/<ord_id>/status", methods=["POST"])
@require(STATUS, "edit")
def change_status(ord_id):
    new_status = request.form.get("new_status")
    if new_status == flow.CANCELLED and not can(STATUS, "delete"):
        abort(403)                                   # cancelling an order needs the Delete right
    descriptions = {"full": t("acctAutoMillingIncomeDesc"), "fee": t("acctAutoMillingFeeDesc")}
    response = _run(ord_id, lambda order: MillingOrder.change_status(
        order["ORD_ID"], operating_branch(), new_status, _user_id(), request.form, descriptions))
    order = MillingOrder.find(ord_id)
    if (new_status == flow.FINISHED and order and order["MILL_STATUS"] == flow.FINISHED and can("SV_BSSTATUS")
            and BuySellOrder.open_for_milling([order["ORD_ID"]]).get(order["ORD_ID"])):
        # update 33: the take-home bran / broken rice orders come next; the list opens filtered on this order
        from flask import flash
        flash(url_for("service_buysell.order_list", q=order["ORD_ID"]), "linked")
    return response


@bp.route("/<ord_id>/line-resend", methods=["POST"])
@require(STATUS, "edit")
def line_resend(ord_id):
    """Sends the order's newest LINE message again (not sent yet, or failed)."""
    order = _order_or_404(ord_id)
    doc = notify.resend(order["ORD_ID"])
    if doc and doc["NOTIFY_FLG"] == notify.SENT:
        flash_ok("lnResent", id=order["QR_ID"])
    else:
        error = (doc or {}).get("ERROR") or ""
        flash_error(ValidationError(error if error.startswith("ln") else "lnFailed"))
    return _back(order["ORD_ID"])


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
