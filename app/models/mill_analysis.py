"""Milling Analysis: what the status timeline (AOILOG_MILLSTATUS) says about the orders of a period.

Per order (registered in the period):
  wait     = minutes from registration (Queue) to the first In process (QR scanned)
  milling  = minutes spent In process (all spells)
  hold     = minutes spent on Hold, and how many times it was held
  ready    = minutes from registration to Finished
  pickup   = minutes from Finished to Closed (take-away) / Ready for Delivery to Closed (delivery)
  yield    = rice received at Finished / weighed paddy (registered paddy when it was not weighed)
  expected = expected rice / registered paddy
Summaries are averages over the orders that have the value, per customer and per rice variety.
"""
from datetime import datetime, time, timedelta, timezone

from .base import col, NO_ID, round_half_up
from . import mill_event
from ..timeutil import local_tz

DONE = ("F", "D", "C")


def period(start_text, end_text, default_days=30):
    """(start date, end date) from yyyy-mm-dd texts; the last 30 days when missing or wrong."""
    from ..timeutil import today_local
    today = today_local()

    def parse(text):
        try:
            return datetime.strptime(text or "", "%Y-%m-%d").date()
        except ValueError:
            return None
    end = parse(end_text) or today
    start = parse(start_text) or (end - timedelta(days=default_days - 1))
    if start > end:
        start, end = end, start
    return start, end


def day_bounds_utc(start, end):
    """UTC [from, to) of the local days start..end."""
    tz = local_tz()
    lo = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return lo, hi


def _first(events, status):
    return next((e["EVENT_DATE"] for e in events if e["TO_STATUS"] == status), None)


def _minutes(a, b):
    return round((b - a).total_seconds() / 60.0, 1) if a and b else None


def order_row(order, events):
    regist = order["REGIST_DATE"]
    started, finished = _first(events, "I"), _first(events, "F")
    closed = _first(events, "C")
    delivery = _first(events, "D")
    milling = sum(e["MINUTES_IN_FROM"] or 0 for e in events if e["FROM_STATUS"] == "I") if started else None
    if started and order["MILL_STATUS"] == "I" and events and events[-1]["TO_STATUS"] == "I":
        milling = None                                      # still milling: not a finished measurement
    holds = [e for e in events if e["TO_STATUS"] == "H"]
    hold_min = sum(e["MINUTES_IN_FROM"] or 0 for e in events if e["FROM_STATUS"] == "H")
    paddy = order.get("ACT_PADDY") or order.get("REGIST_PADDY") or 0
    rice = order.get("ACT_RICE")
    return {
        "ord_id": order["ORD_ID"], "qr": order.get("QR_ID"), "cust": order.get("CUST_CODE"),
        "variety": order.get("RICE_VARIETY") or "WH", "type": order.get("ORDER_TYPE"), "status": order["MILL_STATUS"],
        "regist": regist, "regist_paddy": order.get("REGIST_PADDY") or 0, "act_paddy": order.get("ACT_PADDY"),
        "act_rice": rice, "expct_rice": order.get("EXPCT_RICE") or 0, "scanned": any(e["SCAN_FLG"] for e in events),
        "wait": _minutes(regist, started), "milling": milling if finished else None,
        "hold_count": len(holds), "hold_min": hold_min if holds else 0.0,
        "ready": _minutes(regist, finished), "pickup": _minutes(delivery or finished, closed),
        "yield": (rice / paddy) if rice and paddy else None,
        "expected": (order["EXPCT_RICE"] / order["REGIST_PADDY"]) if order.get("REGIST_PADDY") else None,
        "cancelled": order["MILL_STATUS"] == "CC", "events": len(events),
    }


def _avg(values):
    values = [v for v in values if v is not None]
    return (sum(values) / len(values)) if values else None


def summarize(rows):
    done = [r for r in rows if r["status"] in DONE]
    return {
        "orders": len(rows), "done": len(done), "cancelled": sum(1 for r in rows if r["cancelled"]),
        "held": sum(1 for r in rows if r["hold_count"]), "scanned": sum(1 for r in rows if r["scanned"]),
        "paddy": sum((r["act_paddy"] or r["regist_paddy"]) for r in rows if not r["cancelled"]),
        "rice": sum(r["act_rice"] or 0 for r in rows),
        "wait": _avg(r["wait"] for r in rows), "milling": _avg(r["milling"] for r in rows),
        "ready": _avg(r["ready"] for r in rows), "pickup": _avg(r["pickup"] for r in rows),
        "hold_min": _avg(r["hold_min"] for r in rows if r["hold_count"]),
        "yield": _avg(r["yield"] for r in rows), "expected": _avg(r["expected"] for r in rows if r["yield"] is not None),
    }


def analyse(branch_id, start, end, variety=None):
    lo, hi = day_bounds_utc(start, end)
    query = {"BRANCH_ID": branch_id, "REGIST_DATE": {"$gte": lo, "$lt": hi}}
    if variety:
        query["RICE_VARIETY"] = variety
    orders = list(col("AOITS_ORDMILLING").find(query, NO_ID).sort([("REGIST_DATE", 1)]))
    rows = [order_row(o, mill_event.for_order(o["ORD_ID"])) for o in orders]
    groups = {}
    for key in ("cust", "variety"):
        found = {}
        for r in rows:
            found.setdefault(r[key], []).append(r)
        groups[key] = sorted(((k, summarize(v)) for k, v in found.items()), key=lambda kv: -kv[1]["orders"])
    return {"rows": rows, "total": summarize(rows), "by_customer": groups["cust"], "by_variety": groups["variety"]}


def events_between(branch_id, start, end):
    lo, hi = day_bounds_utc(start, end)
    return list(col(mill_event.COL).find({"BRANCH_ID": branch_id, "EVENT_DATE": {"$gte": lo, "$lt": hi}}, NO_ID)
                .sort([("EVENT_DATE", 1), ("EVENT_SEQ", 1)]))


def pct(value):
    return None if value is None else round_half_up(value * 100, 2)
