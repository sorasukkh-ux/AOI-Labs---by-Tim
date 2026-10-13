"""AOILOG_MILLSTATUS: one record for every status change of a milling order (for the timeline and the analysis).

Written by MillingOrder whenever MILL_STATUS changes (registration counts as "" -> Q). Each record keeps what the
analysis needs without joining: branch, customer, variety, pickup method, from -> to, when, who, the reason
(hold / cancellation), the minutes spent in the previous status, and the weights known at that moment
(registered paddy; weighed paddy from the QR scan at In process; rice received at Finished).
Orders made before update 22 get their records once from the AOILOG_ORDMILLING snapshots (backfill).
"""
from .base import col, NO_ID
from .counter import Counter
from ..timeutil import now_utc

COL = "AOILOG_MILLSTATUS"
SNAPSHOTS = "AOILOG_ORDMILLING"


def _minutes(start, end):
    return round((end - start).total_seconds() / 60.0, 1) if start and end else None


def last(ord_id):
    docs = list(col(COL).find({"ORD_ID": ord_id}, NO_ID).sort([("EVENT_DATE", -1), ("EVENT_SEQ", -1)]).limit(1))
    return docs[0] if docs else None


def record(order, from_status, ope_user, when=None, reason=None, scanned=False, source="USER"):
    """`order` is the order after the change."""
    when = when or now_utc()
    prev = last(order["ORD_ID"])
    seq = Counter.next("EV-" + order["ORD_ID"])
    doc = {"EVENT_ID": "EV-%s-%02d" % (order["ORD_ID"], seq), "EVENT_SEQ": seq, "ORD_ID": order["ORD_ID"],
           "QR_ID": order.get("QR_ID"), "BRANCH_ID": order.get("BRANCH_ID"), "CUST_CODE": order.get("CUST_CODE"),
           "RICE_VARIETY": order.get("RICE_VARIETY") or "WH", "ORDER_TYPE": order.get("ORDER_TYPE"),
           "FROM_STATUS": from_status or "", "TO_STATUS": order["MILL_STATUS"], "EVENT_DATE": when,
           "MINUTES_IN_FROM": _minutes(prev["EVENT_DATE"], when) if prev else None,
           "REASON": reason, "SCAN_FLG": bool(scanned), "REGIST_PADDY": order.get("REGIST_PADDY"),
           "ACT_PADDY": order.get("ACT_PADDY"), "ACT_RICE": order.get("ACT_RICE"), "EXPCT_RICE": order.get("EXPCT_RICE"),
           "SOURCE": source, "OPE_USER": ope_user, "REGIST_DATE": now_utc()}
    col(COL).insert_one(dict(doc))
    return doc


def for_order(ord_id):
    return list(col(COL).find({"ORD_ID": ord_id}, NO_ID).sort([("EVENT_DATE", 1), ("EVENT_SEQ", 1)]))


def backfill():
    """Orders without records get them from their snapshots (every change of MILL_STATUS). Returns how many orders."""
    done = {d["ORD_ID"] for d in col(COL).find({}, {"ORD_ID": 1, "_id": 0})}
    count = 0
    for order in col("AOITS_ORDMILLING").find({}, NO_ID):
        if order["ORD_ID"] in done:
            continue
        snaps = list(col(SNAPSHOTS).find({"ORD_ID": order["ORD_ID"]}, NO_ID).sort([("LOG_DATE", 1)]))
        if not snaps:
            snaps = [dict(order, LOG_DATE=order.get("REGIST_DATE") or now_utc())]
        status = ""
        for snap in snaps:
            if snap.get("MILL_STATUS") and snap["MILL_STATUS"] != status:
                record(snap, status, snap.get("OPE_USER"), when=snap["LOG_DATE"],
                       reason=snap.get("HOLD_REASON") if snap["MILL_STATUS"] == "H" else None, source="BACKFILL")
                status = snap["MILL_STATUS"]
        count += 1
    return count
