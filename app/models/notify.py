"""LINE messages to customers (AOITS_NOTIFY).

When they are sent (the milling status flow):
  * take-away order set to Finished      -> "your rice is ready" (notifyFinishedMsg)
  * delivery order, delivery time confirmed on Ready for Delivery -> "it will arrive at ..." (notifyDeliveryMsg)

Every message is first written to AOITS_NOTIFY (NOTIFY_FLG 0 = not sent yet), then sent with the LINE Messaging API
(push message, https://api.line.me/v2/bot/message/push) and marked 1 = sent or 9 = error with the reason in ERROR.
LINE Notify closed in March 2025, so a LINE Official Account (OA) of the mill is needed:
  * AOI_LINE_CHANNEL_TOKEN (.env): the channel access token of the OA's Messaging API channel. Not set -> the message
    stays 0 with ERROR lnNotConfigured, and the order list shows "LINE not sent".
  * the customer's LINE user ID (AOIMS_CUSTOMER.LINE_USER_ID, "U" + 32 hex characters). It is NOT the LINE ID people
    type (LINE_ID): LINE only gives it to the OA when the customer adds the OA as a friend or writes to it.
Unsent and failed messages can be sent again (resend) once the token or the customer's user ID is there.
"""
import json
import os
import re
import urllib.error
import urllib.request
import uuid

from .base import col, NO_ID
from .counter import Counter
from .customer import Customer
from ..timeutil import now_utc, to_local

COL = "AOITS_NOTIFY"
UNSENT, SENT, ERROR = "0", "1", "9"
PUSH_URL = "https://api.line.me/v2/bot/message/push"
USER_ID = re.compile(r"^U[0-9a-f]{32}$")
TIMEOUT = 6


def _token():
    return (os.environ.get("AOI_LINE_CHANNEL_TOKEN") or "").strip()


def configured():
    return bool(_token())


def _push(user_id, text):
    """Sends one text message; returns None when LINE accepted it, otherwise the error text."""
    body = json.dumps({"to": user_id, "messages": [{"type": "text", "text": text[:5000]}]}).encode("utf-8")
    req = urllib.request.Request(PUSH_URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + _token(),
        "X-Line-Retry-Key": str(uuid.uuid4())})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return None if 200 <= resp.status < 300 else "HTTP %s" % resp.status
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message", "")
        except Exception:                                 # noqa: BLE001 - any unreadable body
            detail = ""
        return ("HTTP %s %s" % (exc.code, detail)).strip()[:200]
    except Exception as exc:                              # noqa: BLE001 - network errors, timeouts
        return str(exc)[:200] or exc.__class__.__name__


def _message(kind, order, when):
    from ..i18n import t_in
    key = "notifyDeliveryMsg" if kind == "delivery" else "notifyFinishedMsg"
    time = to_local(when).strftime("%H:%M") if when else "-"
    return "#%s %s" % (order.get("QR_ID", ""), t_in("th", key, time=time))


def send(doc):
    """Tries to send one AOITS_NOTIFY record and saves the result."""
    if not configured():
        flag, error = UNSENT, "lnNotConfigured"
    else:
        user_id = ((Customer.find(doc["CUST_CODE"]) or {}).get("LINE_USER_ID") or "").strip()
        if not USER_ID.match(user_id):
            flag, error = ERROR, "lnNoUser"
        else:
            problem = _push(user_id, doc["NOTIFY_MESSAGE"])
            flag, error = (SENT, None) if problem is None else (ERROR, problem)
    now = now_utc()
    values = {"NOTIFY_FLG": flag, "ERROR": error, "LAST_UPDATE": now}
    if flag == SENT:
        values["NOTIFY_DATETIME"] = now
    col(COL).update_one({"NOTIFY_ID": doc["NOTIFY_ID"]}, {"$set": values})
    doc.update(values)
    return doc


def queue(order, kind, when):
    """Writes the message of `kind` ('finished' | 'delivery') for a milling order and tries to send it."""
    now = now_utc()
    seq = Counter.next("NT-" + order["ORD_ID"])
    doc = {"NOTIFY_ID": "NT-%s-%02d" % (order["ORD_ID"], seq), "ORD_ID": order["ORD_ID"], "ORD_TYPE": "ML",
           "CUST_CODE": order["CUST_CODE"], "NOTIFY_KIND": kind, "NOTIFY_DATETIME": None, "NOTIFY_FLG": UNSENT,
           "NOTIFY_MESSAGE": _message(kind, order, when), "ERROR": None, "REGIST_DATE": now, "LAST_UPDATE": now}
    col(COL).insert_one(dict(doc))
    return send(doc)


def last_for(ord_ids):
    """{ORD_ID: the newest AOITS_NOTIFY record} for the order list."""
    found = {}
    for doc in col(COL).find({"ORD_ID": {"$in": list(ord_ids)}}, NO_ID).sort([("REGIST_DATE", 1), ("NOTIFY_ID", 1)]):
        found[doc["ORD_ID"]] = doc
    return found


def resend(ord_id):
    """Sends the newest message of an order again (after the token or the customer's user ID was added)."""
    doc = last_for([ord_id]).get(ord_id)
    if not doc or doc["NOTIFY_FLG"] == SENT:
        return doc
    return send(doc)
