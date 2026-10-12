"""Inventory: Inventory List (with Add New Item, Edit Item and Transfer), Inventory Historical and Dashboard.

Everything works on the branch the user signed in to. Item operations are in models/inventory.py.
"""
import math
from datetime import datetime, time, timedelta, timezone

from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..i18n import t
from ..models.base import ValidationError
from ..models.buysell_order import BuySellOrder
from ..models.customer import Customer
from ..models.inventory import (DECREASE_TYPES, INCREASE_TYPES, OPE_STATUS, REASONS, Inventory, OPE_ADJ_DEC, OPE_ADJ_INC,
                                OPE_INBOUND, OPE_OUTBOUND, OPE_TRANSFER)
from ..models.milling_order import MillingOrder
from ..models.price import MAT_BRAN, MAT_BROKEN, MAT_HUSK, MAT_PADDY, MAT_RICE
from ..security import can, current_user, operating_branch, require
from ..timeutil import fmt_datetime, local_tz, now_local, to_local
from .common import flash_error, flash_ok
from ..models import variety
from ..models.variety import mat_name
from .service_buysell import INV_STATUS_KEYS, MAT_KEYS

bp = Blueprint("inventory", __name__, url_prefix="/inventory")
LIST, HISTORY, DASHBOARD = "IV_LIST", "IV_HISTORY", "IV_DASHBOARD"
MATERIALS = variety.ALL                                  # every SKU (paddy, rice, broken rice per variety; bran; husk)
OPE_KEYS = {OPE_INBOUND: "opTypeInbound", OPE_OUTBOUND: "opTypeOutbound", OPE_ADJ_INC: "opTypeAdjustIncrease",
            OPE_ADJ_DEC: "opTypeAdjustDecrease", OPE_STATUS: "opTypeStatusChange", OPE_TRANSFER: "btnTransfer"}
REASON_KEYS = {"milling": "reasonMilling", "buying": "reasonBuying", "producing": "reasonProducing", "order_prep": "reasonOrderPrep",
               "other": "reasonOther", "milling_return": "reasonMillingReturn", "selling": "reasonSelling"}
STATUS_BADGE = {"A": "available", "B": "blocked", "U": "unable-ship"}
LOW_STOCK_KG = 2000          # the design's low-stock line for the mill's own stock of a material


def _user_id():
    return current_user()["USER_ID"]


def _kept_for(ord_id, cache):
    """'Milling #0003 · customer' for stock kept for an order; None for the mill's own stock."""
    if not ord_id:
        return None
    if ord_id not in cache:
        order, label = MillingOrder.find(ord_id), None
        if order:
            label = t("invKeptMilling", id=order["QR_ID"])
        else:
            order = BuySellOrder.find(ord_id)
            if order:
                label = t("invKeptSell" if order["ORD_TYPE"] == "SL" else "invKeptBuy", id=order["QR_ID"])
        customer = Customer.find(order["CUST_CODE"]) if order else None
        cache[ord_id] = (label or ord_id) + (" · " + customer["CUST_NAME"] if customer else "")
    return cache[ord_id]


def _decorate(items):
    cache = {}
    for item in items:
        item["name"] = mat_name(item["MAT_CODE"])
        item["kept_for"] = _kept_for(item.get("SUB_2"), cache)
        item["loc_text"] = ", ".join("%s (%s)" % (r["LOC_ID"], "{:,.2f}".format(r["INV_QTY"])) for r in item["locations"])
    return items


def _common():
    return {"mat_keys": MAT_KEYS, "inv_status_keys": INV_STATUS_KEYS, "status_badge": STATUS_BADGE, "ope_keys": OPE_KEYS,
            "reason_keys": REASON_KEYS, "reasons": {g: [code for code, _move in r] for g, r in REASONS.items()},
            "can_create": can(LIST, "create"), "can_edit": can(LIST, "edit")}


def _back(endpoint, **values):
    return redirect(url_for(endpoint, **values))


# ---------- Inventory List ----------
@bp.route("/")
@require(LIST)
def item_list():
    items = _decorate(Inventory.items(operating_branch()))
    q = request.args.get("q", "").strip().lower()
    if q:
        items = [i for i in items if q in " ".join(str(v) for v in (i["name"], i["MAT_CODE"], i.get("SUB_1") or "", i["loc_text"],
                                                                     i.get("kept_for") or "")).lower()]
    return render_template("inventory/list.html", items=items, q=request.args.get("q", ""), **_common())


@bp.route("/new", methods=["GET", "POST"])
@require(LIST, "create")
def item_new():
    """Add New Item: the mill's own stock received at a location (also husk from milling)."""
    if request.method == "POST":
        try:
            Inventory.add_item(operating_branch(), request.form, _user_id(), MATERIALS)
        except ValidationError as error:
            flash_error(error)
        else:
            flash_ok("msgInvSaved")
            return _back("inventory.item_list")
    return render_template("inventory/item_new.html", values=request.form, materials=MATERIALS,
                           suggest_loc=Inventory.last_location(operating_branch()), **_common())


@bp.route("/item/<inv_id>", methods=["GET", "POST"])
@require(LIST)
def item_edit(inv_id):
    """Edit Item: the item's locations and one operation (increase, decrease, status change)."""
    if request.method == "POST":
        if not can(LIST, "edit"):
            abort(403)
        record_id = request.form.get("REC_ID") or inv_id
        try:
            Inventory.operate(operating_branch(), record_id, request.form, _user_id())
        except ValidationError as error:
            flash_error(error)
        else:
            flash_ok("msgInvSaved")
            item = Inventory.item(operating_branch(), record_id) or Inventory.item(operating_branch(), inv_id)
            if item:
                return _back("inventory.item_edit", inv_id=item["locations"][0]["INV_ID"])
            return _back("inventory.item_list")
    item = Inventory.item(operating_branch(), inv_id)
    if not item:
        abort(404)
    return render_template("inventory/item_edit.html", item=_decorate([item])[0], values=request.form, **_common())


@bp.route("/transfer", methods=["GET", "POST"])
@require(LIST, "edit")
def transfer():
    if request.method == "POST":
        try:
            target = Inventory.transfer(operating_branch(), request.form, _user_id())
        except ValidationError as error:
            flash_error(error)
        else:
            flash_ok("msgInvTransferred")
            return _back("inventory.item_edit", inv_id=target["INV_ID"])
    items = _decorate(Inventory.items(operating_branch()))
    records = [(r, i) for i in items for r in i["locations"]]
    return render_template("inventory/transfer.html", records=records, chosen=request.values.get("INV_ID") or request.args.get("inv"),
                           values=request.form, **_common())


# ---------- Inventory Historical ----------
HISTORY_COLUMNS = ("time", "sku", "type", "qty", "ref")


def _history_rows():
    cache = {}
    rows = []
    for log in Inventory.history(operating_branch(), newest_first=request.args.get("sort") != "asc"):
        kind = "in" if log["OPE_TYPE"] in INCREASE_TYPES else ("out" if log["OPE_TYPE"] in DECREASE_TYPES else log["OPE_TYPE"])
        if log["OPE_TYPE"] == OPE_TRANSFER:
            ref = "%s → %s" % (log["ORILOC_ID"], log["NEWLOC_ID"])
        elif log["OPE_TYPE"] == OPE_STATUS:
            ref = "%s: %s → %s" % (log["NEWLOC_ID"], t(INV_STATUS_KEYS[log["ORI_STATUS"]]), t(INV_STATUS_KEYS[log["NEW_STATUS"]]))
        else:
            ref = "%s · %s" % (log["MOVE_TYPE"], log["NEWLOC_ID"])
        kept = _kept_for(log.get("REF_ORD_ID") or log.get("SUB_2"), cache)
        if kept:
            ref += " · " + kept
        sign = "+" if kind == "in" else ("-" if kind == "out" else "")
        rows.append({"time": fmt_datetime(log["LOG_DATE"]), "sku": mat_name(log["MAT_CODE"]) + (" · " + log["SUB_1"] if log.get("SUB_1") else ""),
                     "type": t({"in": "invIn", "out": "invOut"}.get(kind, OPE_KEYS.get(log["OPE_TYPE"], "colType"))),
                     "kind": kind, "qty": "%s%s kg" % (sign, "{:,.2f}".format(log["INV_QTY"])), "ref": ref, "user": log.get("OPE_USER")})
    col = request.args.get("col") if request.args.get("col") in HISTORY_COLUMNS else "all"
    needle = request.args.get("v", "").strip().lower()
    if needle:
        rows = [r for r in rows if any(needle in str(r[c]).lower() for c in (HISTORY_COLUMNS if col == "all" else (col,)))]
    return rows, col


@bp.route("/history")
@require(HISTORY)
def history():
    rows, col = _history_rows()
    return render_template("inventory/history.html", rows=rows, col=col, value=request.args.get("v", ""),
                           sort=request.args.get("sort", "desc"), columns=HISTORY_COLUMNS)


# ---------- Dashboard ----------
def _donut(parts, cx=80, cy=80, r=64, inner=34):
    """SVG path of each donut segment (the design's composition chart)."""
    total = sum(p["qty"] for p in parts) or 1
    start = -math.pi / 2
    for p in parts:
        frac = p["qty"] / total
        p["pct"] = round(frac * 100, 1)
        p["full"] = frac >= 0.9999
        end = start + frac * 2 * math.pi
        large = 1 if frac > 0.5 else 0
        pt = lambda radius, angle: "%.2f,%.2f" % (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
        p["d"] = "M%s A%d,%d 0 %d 1 %s L%s A%d,%d 0 %d 0 %s Z" % (pt(r, start), r, r, large, pt(r, end), pt(inner, end),
                                                                inner, inner, large, pt(inner, start))
        start = end
    return parts


@bp.route("/dashboard")
@require(DASHBOARD)
def dashboard():
    branch = operating_branch()
    items = _decorate(Inventory.items(branch))
    start = datetime.combine(now_local().date(), time.min, tzinfo=local_tz()).astimezone(timezone.utc)
    today = Inventory.movements_since(branch, start)
    stock_in = sum(m["INV_QTY"] for m in today if m["OPE_TYPE"] in INCREASE_TYPES)
    stock_out = sum(m["INV_QTY"] for m in today if m["OPE_TYPE"] in DECREASE_TYPES)
    by_mat = {}
    own_by_mat = {}
    for item in items:
        by_mat[item["MAT_CODE"]] = by_mat.get(item["MAT_CODE"], 0) + item["qty"]
        if not item.get("SUB_2"):
            own_by_mat[item["MAT_CODE"]] = own_by_mat.get(item["MAT_CODE"], 0) + item["qty"]
    # low stock: SKUs the mill keeps (some own stock now, or moved in the last 30 days) with less than LOW_STOCK_KG
    month = Inventory.movements_since(branch, start - timedelta(days=30))
    kept = {m["MAT_CODE"] for m in month} | set(own_by_mat)
    low = [m for m in MATERIALS if m in kept and own_by_mat.get(m, 0) < LOW_STOCK_KG]
    recent = list(reversed(Inventory.history(branch, limit=8)))
    trend = [{"label": fmt_datetime(m["LOG_DATE"])[-5:], "qty": m["INV_QTY"],
              "kind": "in" if m["OPE_TYPE"] in INCREASE_TYPES else ("out" if m["OPE_TYPE"] in DECREASE_TYPES else "other"),
              "name": mat_name(m["MAT_CODE"])} for m in recent]
    by_kind = {}
    for m, qty in by_mat.items():                      # composition by kind (all varieties together), one colour each
        by_kind[variety.kind_of(m)] = by_kind.get(variety.kind_of(m), 0) + qty
    composition = _donut([{"mat": k, "name": t(variety.KIND_KEYS[k]), "qty": by_kind[k]}
                          for k in (variety.PADDY, variety.RICE, variety.BRAN, variety.BROKEN, variety.HUSK) if by_kind.get(k, 0) > 0])
    return render_template("inventory/dashboard.html", total_items=len(items), total_stock=sum(by_mat.values()),
                           stock_in=stock_in, stock_out=stock_out, low=low, low_kg=LOW_STOCK_KG, trend=trend,
                           composition=composition, mat_keys=MAT_KEYS)
