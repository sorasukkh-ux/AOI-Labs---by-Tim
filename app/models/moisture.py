"""Update 137: paddy moisture on a buying order (Tim: weight deducted to 15%).

    paid kg = weighed kg x (100 - moisture) / (100 - 15)          only when the moisture is above 15%

The weighed kg stays on each paddy line (WEIGH_QTY); ORD_QTY becomes the paid kg and the amount follows. Applying a
new reading always starts again from the weighed kg, so the newest reading wins and nothing is deducted twice.
Only while the order is Open (the goods are not received yet), so stock and money stay the same.

The reading comes from:
- the Buy table on Customer Service (typed, before Open / Complete), or
- the order list (typed), or
- a moisture meter with a data output: it is added as a device on Rice Quality Grading and posts
  {"moisture": 17.8} like the grader (scripts/aoi_moisture.py); the order chosen for it gets the reading.
"""
from .base import ValidationError, round_half_up, to_number
from . import variety as var

BASE_PCT = 15.0
LOW, HIGH = 5.0, 40.0


def is_paddy(mat_code):
    return var.kind_of(mat_code) == var.PADDY


def parse(text, required=False):
    """A typed moisture % (None when empty)."""
    try:
        value = to_number(text)
    except (ValueError, TypeError):
        raise ValidationError("mtErrNumber", lo="%.0f" % LOW, hi="%.0f" % HIGH)
    if value is None:
        if required:
            raise ValidationError("mtErrNumber", lo="%.0f" % LOW, hi="%.0f" % HIGH)
        return None
    if not (LOW <= value <= HIGH):
        raise ValidationError("mtErrNumber", lo="%.0f" % LOW, hi="%.0f" % HIGH)
    return round_half_up(value, 1)


def paid_kg(weighed, pct):
    if pct is None or pct <= BASE_PCT:
        return round_half_up(weighed, 3)
    return round_half_up(weighed * (100 - pct) / (100 - BASE_PCT), 3)


def deduct_line(line, pct):
    """A paddy line with the moisture applied (other lines unchanged)."""
    if not is_paddy(line["MAT_CODE"]):
        return line
    weighed = line.get("WEIGH_QTY") if line.get("WEIGH_QTY") is not None else line["ORD_QTY"]
    qty = paid_kg(weighed, pct)
    price = line.get("PRICING_UINT") or line.get("UNIT_PRICE") or 0
    return dict(line, WEIGH_QTY=weighed, MOISTURE_PCT=pct, ORD_QTY=qty, ORD_AMOUNT=round_half_up(qty * price, 2))


def deduct_items(items, pct):
    """parse_items() output of the Buy table: paddy quantities and amounts after the moisture."""
    out = []
    for i in items:
        if pct is not None and is_paddy(i["mat"]):
            qty = paid_kg(i["qty"], pct)
            i = dict(i, weighed=i["qty"], moisture=pct, qty=qty, amount=round_half_up(qty * i["price"], 2))
        out.append(i)
    return out


def applies(order):
    from .buysell_order import BUY, OPEN, BuySellOrder
    return bool(order and order["ORD_TYPE"] == BUY and order["ORD_STATUS"] == OPEN
                and any(is_paddy(l["MAT_CODE"]) for l in BuySellOrder.items_of(order)))


def apply(order, pct, source, ope_user):
    """Puts a reading on an Open buying order with paddy. Returns the saved order."""
    from .buysell_order import BuySellOrder
    from ..timeutil import now_utc
    if not applies(order):
        raise ValidationError("mtErrOrder")
    lines = [deduct_line(l, pct) for l in BuySellOrder.items_of(order)]
    values = BuySellOrder._header(lines)
    values.update({"MOISTURE_PCT": pct, "MOISTURE_SRC": source, "MOISTURE_USER": ope_user, "MOISTURE_DATE": now_utc()})
    return BuySellOrder._save(order, values, ope_user)


def from_device(order, pct, device_id):
    """A meter / grader reading kept with an order: applied when the order still allows it (else only recorded)."""
    if pct is None or not applies(order) or not (LOW <= pct <= HIGH):
        return None
    return apply(order, round_half_up(pct, 1), "device:" + device_id, "device:" + device_id)
