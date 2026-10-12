"""Administration > Customer Registration (AOIMS_CUSTOMER). One screen: search, the chosen customer, history."""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

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
    return render_template(
        "admin/customers.html", mode=mode, customer=customer, values=values if values is not None else (customer or {}),
        customers=found, query=query, scan=scan, history=history, status_keys=STATUS_KEYS,
        can_create=can(MENU, "create"), can_edit=can(MENU, "edit"), can_open_orders=can("SV_MILLSTATUS"),
        next_url=safe_next(request.args.get("next"), ""))      # never trust a link target from the address bar


def _after_save(customer):
    """Shows the saved customer. When the screen was opened from Customer Service (?next=...), the pop-up then
    asks whether to go back there with this customer chosen."""
    target = safe_next(request.args.get("next"), "")
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


@bp.route("/new", methods=["GET", "POST"])
@require(MENU, "create")
def customer_new():
    if request.method == "POST":
        try:
            customer = Customer.create(request.form, current_user()["USER_ID"])
        except ValidationError as error:
            flash_error(error)
            return _page("new", None, request.form)
        flash_ok("custSaved", code=customer["CUST_CODE"])
        return _after_save(customer)
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
