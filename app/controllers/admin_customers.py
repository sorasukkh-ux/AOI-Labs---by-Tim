"""Administration > Customer Registration (AOIMS_CUSTOMER). One screen: search, the chosen customer, history."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..i18n import t
from ..models.base import ValidationError
from ..models.customer import Customer
from ..models.milling_order import MillingOrder
from ..security import can, current_user, operating_branch, require, safe_next
from .common import flash_error, flash_ok
from .service_milling import STATUS_KEYS

bp = Blueprint("admin_customers", __name__, url_prefix="/admin/customers")
MENU = "AD_CUSTOMER"


def _page(mode, customer=None, values=None):
    """mode: "view" (fields locked), "edit" or "new"."""
    query, scan = request.args.get("q", ""), request.args.get("scan", "")
    found = Customer.search_code(scan) if scan and not query else Customer.search(query, limit=50)
    history = []
    if customer and mode != "new":
        for order in MillingOrder.for_customer(customer["CUST_CODE"], operating_branch()):
            order["pay_total"] = MillingOrder.pay_total(order)
            history.append(order)
    from ..models import line_link
    line_qr = line_link.ticket_url(customer["CUST_CODE"]) if customer and mode != "new" else None
    return render_template(
        "admin/customers.html", mode=mode, line_qr=line_qr, customer=customer, values=values if values is not None else (customer or {}),
        customers=found, query=query, scan=scan, history=history, status_keys=STATUS_KEYS,
        can_create=can(MENU, "create"), can_edit=can(MENU, "edit"), can_delete=can(MENU, "delete"), can_open_orders=can("SV_MILLSTATUS"),
        next_url=safe_next(request.args.get("next"), ""))      # never trust a link target from the address bar


def _after_save(customer, new=False):
    """Shows the saved customer. When the screen was opened from Customer Service (?next=...), the pop-up then
    asks whether to go back there with this customer chosen. A new customer registered here: the pop-up asks
    whether to queue a milling order for them now."""
    target = safe_next(request.args.get("next"), "")
    if new and not target and can("SV_SERVICE", "create"):
        flash(url_for("service_milling.new_order", cust=customer["CUST_CODE"]), "queue")
    if target:
        flash(target + ("&" if "?" in target else "?") + "cust=" + customer["CUST_CODE"], "return")
        return redirect(url_for("admin_customers.customer_view", code=customer["CUST_CODE"], next=target))
    return redirect(url_for("admin_customers.customer_view", code=customer["CUST_CODE"]))


@bp.route("/")
@require(MENU)
def customer_list():
    scan = request.args.get("scan", "").strip()
    exact = Customer.find(scan) if scan else None
    if exact:                                            # a scanned or typed customer code opens that customer
        return redirect(url_for("admin_customers.customer_view", code=exact["CUST_CODE"]))
    return _page("view")


@bp.route("/<code>/card")
@require(MENU)
def customer_card(code):
    """Update 33: the customer's QR card (business-card size, printable). The QR holds the customer code, so a
    scanner or the camera on Customer Service / Customer Registration chooses the customer at once."""
    customer = Customer.find(code)
    if not customer or customer.get("CUST_STATUS") == "D":
        abort(404)
    from ..models import phone, notify
    linked = bool(customer.get("LINE_USER_ID"))
    return render_template("admin/customer_card.html", c=customer, mobile=phone.of(customer),
                           copies=max(1, min(10, request.args.get("n", 1, type=int))),
                           line_linked=linked, line_ready=linked and notify.configured() and can(MENU, "edit"))


@bp.route("/<code>/card/line", methods=["POST"])
@require(MENU, "edit")
def customer_card_line(code):
    """Update 48: Print QR card also sends the card (text + QR picture) to the customer's LINE. Answers JSON."""
    from flask import jsonify
    from ..models import notify
    customer = Customer.find(code)
    if not customer or customer.get("CUST_STATUS") == "D":
        abort(404)
    if not customer.get("LINE_USER_ID"):
        return jsonify({"ok": False, "message": t("lnNoUser")}), 422
    doc = notify.send_card(customer, request.form.get("PUBLIC_BASE"))
    if doc.get("NOTIFY_FLG") != notify.SENT:
        return jsonify({"ok": False, "message": t(notify.reason_key(doc.get("ERROR")))}), 422
    return jsonify({"ok": True, "message": t("lnCardSent" if doc.get("IMAGE_URL") else "lnCardText")})


@bp.route("/check.json")
@require(MENU)
def customer_check():
    """Checked before the confirmation pop-up: another customer with the same name?"""
    from flask import jsonify
    dup = Customer.same_name(request.args.get("name", ""), request.args.get("code") or None)
    return jsonify({"ok": dup is None, "dup": {"code": dup["CUST_CODE"], "name": dup["CUST_NAME"]} if dup else None})


@bp.route("/<code>/delete", methods=["POST"])
@require(MENU, "delete")
def customer_delete(code):
    try:
        result = Customer.delete(code, current_user()["USER_ID"])
    except ValidationError as error:
        flash_error(error)
        return redirect(url_for("admin_customers.customer_view", code=code))
    flash_ok("custDeleted" if result == "removed" else "custHidden", code=code.upper())
    return redirect(url_for("admin_customers.customer_list"))


@bp.route("/<code>/line-unlink", methods=["POST"])
@require(MENU, "edit")
def customer_line_unlink(code):
    from ..models import line_link
    if line_link.unlink(code, current_user()["USER_ID"]):
        flash_ok("custLineUnlinked", code=code.upper())
    return redirect(url_for("admin_customers.customer_view", code=code, _anchor="cust-line"))


@bp.route("/new", methods=["GET", "POST"])
@require(MENU, "create")
def customer_new():
    if request.method == "POST":
        try:
            customer = Customer.create(request.form, current_user()["USER_ID"])
        except ValidationError as error:
            flash_error(error)
            return _page("new", None, request.form)
        flash_ok("custRegistered", code=customer["CUST_CODE"], name=customer["CUST_NAME"])
        return _after_save(customer, new=True)
    return _page("new", None, {})


@bp.route("/<code>", methods=["GET", "POST"])
@require(MENU)
def customer_view(code):
    customer = Customer.find(code)
    if not customer:
        abort(404)
    if request.method == "POST":
        if not can(MENU, "edit"):
            abort(403)
        try:
            customer = Customer.update(code, request.form, current_user()["USER_ID"])
        except ValidationError as error:
            flash_error(error)
            return _page("edit", customer, request.form)
        flash_ok("custSaved", code=customer["CUST_CODE"])
        return _after_save(customer)
    editing = request.args.get("edit") == "1" and can(MENU, "edit")
    return _page("edit" if editing else "view", customer)
