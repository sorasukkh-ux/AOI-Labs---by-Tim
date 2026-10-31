"""Campaign coupons (update 96, Tim): AOITS_COUPON.

Staff issue a coupon (value in THB, expiry) to ONE customer on the campaign page; it is sent only by LINE as a card
with its QR (AOICPN-<COUPON_ID>-<secret>). At the counter the operator scans it on a milling order or a customer
buying order (Selling, SL) before payment. The coupon is used once: it takes off min(value, payment); when it is
worth more than the payment, the rest is lost (FORFEIT) and the coupon cannot be used again.
  * A coupon belongs to its customer and branch, and must be Active and not expired.
  * One coupon per order. Taken off again (Remove) while the order is open, or when the order is cancelled, the
    coupon is Active again.
  * When the order is Closed the ledger keeps the full income and posts the coupon as an expense (EX).
Statuses: A active, U used, X cancelled (by staff). Expired = A with EXPIRE_DATE before today.
"""
import re
import secrets
import string
from datetime import datetime

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up, to_number
from .customer import Customer
from ..timeutil import local_tz, now_utc, to_local, today_local

COL = "AOITS_COUPON"
ACTIVE, USED, CANCELLED = "A", "U", "X"
VALUE_MIN, VALUE_MAX = 1, 100000
QR_RE = re.compile(r"AOICPN-(CPN[A-Z0-9]{8})-([A-Za-z0-9]{10})")
ALPHA = string.ascii_uppercase + string.digits


def get(coupon_id):
    return col(COL).find_one({"COUPON_ID": coupon_id}, NO_ID)


def for_campaign(camp_id):
    return sorted(col(COL).find({"CAMP_ID": camp_id}, NO_ID), key=lambda c: c["REGIST_DATE"], reverse=True)


def qr_text(coupon):
    return "AOICPN-%s-%s" % (coupon["COUPON_ID"], coupon["CODE"])


def state(coupon, today=None):
    """active / used / cancelled / expired"""
    if coupon["STATUS"] == USED:
        return "used"
    if coupon["STATUS"] == CANCELLED:
        return "cancelled"
    if (today or today_local()) > to_local(coupon["EXPIRE_DATE"]).date():
        return "expired"
    return "active"


# ---------- issue (campaign page) ----------
def issue(camp, cust_code, form, ope_user, browser_origin=None):
    from . import campaign as cp
    customer = Customer.find((cust_code or "").strip())
    if not customer:
        raise ValidationError("errCustomerRequired")
    if not (customer.get("LINE_USER_ID") or "").strip():
        raise ValidationError("cpnErrNoLine", name=customer.get("CUST_NAME", customer["CUST_CODE"]))
    try:
        value = to_number(form.get("CPN_VALUE"))
    except ValueError:
        raise ValidationError("errNumber")
    if value is None or not VALUE_MIN <= value <= VALUE_MAX:
        raise ValidationError("cpnErrValue", lo=VALUE_MIN, hi="{:,}".format(VALUE_MAX))
    text = (form.get("CPN_EXPIRE") or "").strip()
    try:
        expire = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=local_tz()) if text else camp["DATE_TO"]
    except ValueError:
        raise ValidationError("cpErrDate", field="expire")
    if to_local(expire).date() < today_local():
        raise ValidationError("cpnErrExpire")
    doc = {"COUPON_ID": "CPN" + "".join(secrets.choice(ALPHA) for _ in range(8)),
           "CODE": "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10)),
           "CAMP_ID": camp["CAMP_ID"], "BRANCH_ID": camp["BRANCH_ID"], "CUST_CODE": customer["CUST_CODE"],
           "VALUE": round_half_up(value, 2), "EXPIRE_DATE": expire, "NOTE": clean_text(form.get("CPN_NOTE"), 80),
           "STATUS": ACTIVE, "LINE_SENT_DATE": None, "LINE_ERROR": None,
           "USED_DATE": None, "USED_USER": None, "USED_ORD_ID": None, "USED_ORD_TYPE": None, "USED_AMOUNT": None, "FORFEIT": None}
    doc.update(audit_new(ope_user))
    col(COL).insert_one(dict(doc))
    send(get(doc["COUPON_ID"]), browser_origin)
    return get(doc["COUPON_ID"])


def send(coupon, browser_origin=None):
    """The coupon card (Flex, with the QR picture) to the customer's LINE. Keeps when it was sent / the problem."""
    from . import notify
    doc = notify.send_coupon(coupon, browser_origin)
    ok = doc.get("NOTIFY_FLG") == notify.SENT
    col(COL).update_one({"COUPON_ID": coupon["COUPON_ID"]},
                        {"$set": {"LINE_SENT_DATE": now_utc() if ok else coupon.get("LINE_SENT_DATE"),
                                  "LINE_ERROR": None if ok else (doc.get("ERROR") or "lnNotConfigured")}})
    return ok


def cancel(coupon_id, branch_id, ope_user):
    coupon = get(coupon_id)
    if not coupon or coupon["BRANCH_ID"] != branch_id:
        raise ValidationError("cpnErrNotFound")
    if coupon["STATUS"] != ACTIVE:
        raise ValidationError("cpnErrNotActive")
    values = {"STATUS": CANCELLED}
    values.update(audit_update(ope_user))
    col(COL).update_one({"COUPON_ID": coupon_id}, {"$set": values})


# ---------- use at the counter ----------
def _order(ord_type, ord_id, branch_id):
    from .milling_order import MillingOrder
    from .buysell_order import BuySellOrder
    if ord_type == "ML":
        order = MillingOrder.find(ord_id, branch_id)
        if not order or order["MILL_STATUS"] in ("C", "CC"):
            raise ValidationError("cpnErrOrder")
        return order
    order = BuySellOrder.find(ord_id, branch_id)
    if not order or order.get("ORD_TYPE") != "SL" or order["ORD_STATUS"] in ("C", "CC"):
        raise ValidationError("cpnErrOrder")
    return order


def due(ord_type, order):
    """What the customer pays for the order (before the coupon)."""
    from .account import TAKE_HOME_FIELDS
    from .buysell_order import BuySellOrder
    if ord_type == "ML":
        fields = ["EST_PAY_MILLFEE", "EST_PAY_DELIVERY"] + ([] if order.get("REF_BUYING_ORDER") else list(TAKE_HOME_FIELDS))
        return round_half_up(sum(order.get(f) or 0 for f in fields), 2)
    return BuySellOrder.amount(order)


def _save_order(ord_type, order, values, ope_user):
    from .milling_order import MillingOrder
    from .buysell_order import BuySellOrder
    return (MillingOrder if ord_type == "ML" else BuySellOrder)._save(order, values, ope_user)


def apply(ord_type, ord_id, branch_id, scanned, ope_user):
    """The operator scans the coupon on an open order. Returns (coupon, used amount, forfeit)."""
    order = _order(ord_type, ord_id, branch_id)
    if order.get("COUPON_ID"):
        raise ValidationError("cpnErrOrderHas")
    m = QR_RE.search((scanned or "").strip())
    coupon = get(m.group(1)) if m else None
    if not coupon or not secrets.compare_digest(coupon["CODE"], m.group(2)) or coupon["BRANCH_ID"] != branch_id:
        raise ValidationError("cpnErrNotFound")
    st = state(coupon)
    if st != "active":
        raise ValidationError("cpnErr_" + st)
    if coupon["CUST_CODE"] != order["CUST_CODE"]:
        raise ValidationError("cpnErrCustomer", name=(Customer.find(coupon["CUST_CODE"]) or {}).get("CUST_NAME", coupon["CUST_CODE"]))
    pay = due(ord_type, order)
    if pay <= 0:
        raise ValidationError("cpnErrNothing")
    used = round_half_up(min(coupon["VALUE"], pay), 2)
    forfeit = round_half_up(coupon["VALUE"] - used, 2)
    values = {"STATUS": USED, "USED_DATE": now_utc(), "USED_USER": ope_user, "USED_ORD_ID": order["ORD_ID"],
              "USED_ORD_TYPE": ord_type, "USED_AMOUNT": used, "FORFEIT": forfeit}
    values.update(audit_update(ope_user))
    col(COL).update_one({"COUPON_ID": coupon["COUPON_ID"], "STATUS": ACTIVE}, {"$set": values})
    _save_order(ord_type, order, {"COUPON_ID": coupon["COUPON_ID"], "COUPON_AMT": used}, ope_user)
    return get(coupon["COUPON_ID"]), used, forfeit


def _back_to_active(coupon_id, ope_user):
    values = {"STATUS": ACTIVE, "USED_DATE": None, "USED_USER": None, "USED_ORD_ID": None, "USED_ORD_TYPE": None,
              "USED_AMOUNT": None, "FORFEIT": None}
    values.update(audit_update(ope_user))
    col(COL).update_one({"COUPON_ID": coupon_id}, {"$set": values})


def remove(ord_type, ord_id, branch_id, ope_user):
    """Takes the coupon off an open order: the coupon is Active again."""
    order = _order(ord_type, ord_id, branch_id)
    if not order.get("COUPON_ID"):
        raise ValidationError("cpnErrNone")
    _back_to_active(order["COUPON_ID"], ope_user)
    _save_order(ord_type, order, {"COUPON_ID": None, "COUPON_AMT": None}, ope_user)


def release_for(order, ope_user):
    """The order was cancelled: its coupon can be used again."""
    if order.get("COUPON_ID"):
        _back_to_active(order["COUPON_ID"], ope_user)


def post_on_close(order, ord_type, ope_user):
    """Closed: the coupon is an expense in the ledger (the income stays the full amount)."""
    from .account import Account
    amt = order.get("COUPON_AMT") or 0
    if amt > 0:
        Account._post(order["BRANCH_ID"], "EX", amt, "Coupon %s (%s)" % (order.get("COUPON_ID"), order.get("QR_ID") or order["ORD_ID"]),
                      order["ORD_ID"], ord_type, order.get("CUST_CODE"), ope_user)
