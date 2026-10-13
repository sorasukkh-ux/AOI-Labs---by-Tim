"""LINE registration: the customer's LINE user ID comes from the LINE webhook (POST /line/webhook).

LINE only gives a person's user ID to the Official Account they talk to, so the customer links themself:
  * the queue label has a second QR "LINE" (https://line.me/R/oaMessage/<OA>/?text=LINK <order>-<check>): the
    customer scans it, adds the account and sends the ready-made message. The order's customer gets the user ID.
    <check> is 6 characters derived from the order and AOI_SECRET_KEY, so order numbers cannot be guessed.
  * or the customer sends the mobile number registered at the mill. It links only when exactly one customer
    has that number and that customer is not linked to another LINE account yet (the ticket can always relink).
Replies use the reply API (free, not counted in the monthly messages). Messages that could not be sent because
the customer was not linked yet (lnNoUser) are sent again right after linking. Every event goes to AOILOG_LINE.
"""
import base64
import hashlib
import hmac
import os
import re
import urllib.parse

from flask import current_app

from .base import col, NO_ID
from .counter import Counter
from .customer import Customer
from . import notify, phone
from ..timeutil import now_utc

LOG = "AOILOG_LINE"
LINK_RE = re.compile(r"\bLINK\s*(ML-\d{8}-\d{4})-([0-9a-f]{6})\b", re.I)
RESEND_DAYS = 3


def secret():
    return (os.environ.get("AOI_LINE_CHANNEL_SECRET") or "").strip()


def oa_id():
    """Basic ID of the Official Account, for example @783ycdvd."""
    value = (os.environ.get("AOI_LINE_OA_ID") or "").strip()
    return value if not value or value.startswith("@") else "@" + value


def valid_signature(body, signature):
    if not secret() or not signature:
        return False
    digest = hmac.new(secret().encode("utf-8"), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode("ascii"), signature)


def check_code(ord_id):
    key = (current_app.config.get("SECRET_KEY") or "aoi").encode("utf-8")
    return hmac.new(key, ("line-link:" + ord_id).encode("utf-8"), hashlib.sha256).hexdigest()[:6]


def link_text(ord_id):
    return "LINK %s-%s" % (ord_id, check_code(ord_id))


def ticket_url(ord_id):
    """The label's LINE QR: opens the chat with the account and the link message typed in. None without AOI_LINE_OA_ID."""
    if not oa_id():
        return None
    return "https://line.me/R/oaMessage/%s/?text=%s" % (urllib.parse.quote(oa_id()), urllib.parse.quote(link_text(ord_id)))


def friend_url():
    return "https://line.me/R/ti/p/%s" % urllib.parse.quote(oa_id()) if oa_id() else None


def _log(kind, user_id, text, result, cust_code=None):
    col(LOG).insert_one({"LOG_ID": "LN-%06d" % Counter.next("LINE-LOG"), "EVENT_TYPE": kind, "LINE_USER_ID": user_id,
                         "TEXT": (text or "")[:200], "RESULT": result, "CUST_CODE": cust_code, "LOG_DATE": now_utc()})


def _m(msg_id, **params):
    """A reply text from LINE Message Maintenance (the default Thai text when it was not changed)."""
    from . import line_msg
    return line_msg.text(msg_id, **params)


def _link(customer, user_id, via):
    """First link: the customer record keeps the LINE user ID, the LINE display name and how it was linked."""
    values = {"LINE_USER_ID": user_id, "LINE_LINK_DATE": now_utc(), "LINE_LINK_VIA": via}
    info = notify.profile(user_id) or {}
    if info.get("displayName"):
        values["LINE_DISPLAY_NAME"] = str(info["displayName"])[:100]
    col(Customer.COL).update_one({"CUST_CODE": customer["CUST_CODE"]}, {"$set": values})
    resent = 0
    from datetime import timedelta
    since = now_utc() - timedelta(days=RESEND_DAYS)
    for doc in col(notify.COL).find({"CUST_CODE": customer["CUST_CODE"], "NOTIFY_FLG": {"$ne": notify.SENT},
                                     "REGIST_DATE": {"$gte": since}}, NO_ID):
        if notify.send(doc)["NOTIFY_FLG"] == notify.SENT:
            resent += 1
    return resent


def _short(name):
    from .tv import short_name
    return short_name(name) or name


def _by_phone(digits):
    found = []
    for c in col(Customer.COL).find({}, NO_ID):
        mine = re.sub(r"\D", "", c.get("MOBILE_NO") or "")
        if not mine:
            continue
        national = mine[1:] if mine.startswith("0") else mine
        full = (c.get("MOBILE_CC") or phone.DEFAULT).lstrip("+") + national
        if digits in (mine, full) or ("0" + digits.lstrip("0")) == mine:
            found.append(c)
    return found


def answer_text(user_id, text):
    """(reply text, result code, customer code) for one text message."""
    from .milling_order import MillingOrder
    match = LINK_RE.search(text or "")
    if match:
        ord_id, code = match.group(1).upper(), match.group(2).lower()
        order = MillingOrder.find(ord_id)
        if not order or not hmac.compare_digest(code, check_code(ord_id)):
            return _m("LN_BAD_TICKET"), "bad_ticket", None
        customer = Customer.find(order["CUST_CODE"]) or {}
        if not customer:
            return _m("LN_BAD_TICKET"), "bad_ticket", None
        resent = _link(customer, user_id, "TICKET")
        reply = _m("LN_LINKED_TICKET", name=_short(customer.get("CUST_NAME", "")), no=order["QR_ID"])
        return reply + (("\n" + _m("LN_RESENT", n=resent)) if resent else ""), "linked_ticket", customer["CUST_CODE"]
    digits = re.sub(r"\D", "", text or "")
    if 9 <= len(digits) <= 15 and not re.search(r"[A-Za-z฀-๿]", text or ""):
        found = _by_phone(digits)
        if not found:
            return _m("LN_PHONE_NOT_FOUND"), "phone_not_found", None
        if len(found) > 1:
            return _m("LN_PHONE_MANY"), "phone_many", None
        customer = found[0]
        if customer.get("LINE_USER_ID") and customer["LINE_USER_ID"] != user_id:
            return _m("LN_PHONE_TAKEN"), "phone_taken", customer["CUST_CODE"]
        resent = _link(customer, user_id, "PHONE")
        reply = _m("LN_LINKED_PHONE", name=_short(customer.get("CUST_NAME", "")))
        return reply + (("\n" + _m("LN_RESENT", n=resent)) if resent else ""), "linked_phone", customer["CUST_CODE"]
    linked = col(Customer.COL).find_one({"LINE_USER_ID": user_id}, NO_ID)
    if linked:
        return _m("LN_HELP_LINKED", name=_short(linked.get("CUST_NAME", ""))), "help_linked", linked["CUST_CODE"]
    return _m("LN_HELP"), "help", None


def handle(events):
    """Handles the events of one webhook call; returns how many were handled."""
    done = 0
    for event in events or []:
        kind = event.get("type")
        user_id = (event.get("source") or {}).get("userId")
        token = event.get("replyToken")
        if (event.get("source") or {}).get("type") not in (None, "user") or not user_id:
            continue                                         # groups and rooms are not used
        if kind == "follow":
            linked = col(Customer.COL).find_one({"LINE_USER_ID": user_id}, NO_ID)
            reply = _m("LN_WELCOME_BACK", name=_short(linked.get("CUST_NAME", ""))) if linked else _m("LN_WELCOME")
            notify.reply(token, reply)
            _log(kind, user_id, "", "welcome", linked["CUST_CODE"] if linked else None)
        elif kind == "unfollow":
            _log(kind, user_id, "", "unfollow")
        elif kind == "message" and (event.get("message") or {}).get("type") == "text":
            text = event["message"].get("text", "")
            reply, result, cust = answer_text(user_id, text)
            notify.reply(token, reply)
            _log(kind, user_id, text, result, cust)
        else:
            if token:
                notify.reply(token, _m("LN_HELP"))
            _log(kind or "?", user_id, "", "ignored")
        done += 1
    return done


def recent(limit=10):
    return list(col(LOG).find({}, NO_ID).sort([("LOG_DATE", -1)]).limit(limit))


def status():
    """The setup checklist of the LINE connection card."""
    return {"token": notify.configured(), "secret": bool(secret()), "oa": oa_id() or None,
            "linked": col(Customer.COL).count_documents({"LINE_USER_ID": {"$nin": [None, ""]}}),
            "friend_url": friend_url()}
