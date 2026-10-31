"""LINE messages to customers (AOITS_NOTIFY).

When they are sent (the milling status flow):
  * take-away order set to Finished      -> "your rice is ready" (notifyFinishedMsg)
  * delivery order, delivery time confirmed on Ready for Delivery -> "it will arrive at ..." (notifyDeliveryMsg)
  * update 47: registration (Print QR), customer linked to LINE -> the queue number and the QR picture (ML_LABEL);
    again with "Send QR to LINE" in the order details

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
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
USER_ID = re.compile(r"^U[0-9a-f]{32}$")
TIMEOUT = 6


def _token():
    return (os.environ.get("AOI_LINE_CHANNEL_TOKEN") or "").strip()


def configured():
    return bool(_token())


def _push(user_id, text, image_url=None, flex=None):
    """Sends one text message (update 47: and the QR picture when `image_url`); returns None when LINE accepted it,
    otherwise the error text. Update 52: with `flex` the card (a Flex Message) is sent instead, the text as its
    alternative text (notification preview)."""
    if flex:
        messages = [{"type": "flex", "altText": text[:400] or "AOI Labs", "contents": flex}]
        image_url = None
    else:
        messages = [{"type": "text", "text": text[:5000]}]
    if image_url:
        messages.append({"type": "image", "originalContentUrl": image_url, "previewImageUrl": image_url})
    body = json.dumps({"to": user_id, "messages": messages}).encode("utf-8")
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


def _post(url, payload):
    """POST to the LINE Messaging API; None when accepted, otherwise the error text."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + _token()})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return None if 200 <= resp.status < 300 else "HTTP %s" % resp.status
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message", "")
        except Exception:                                 # noqa: BLE001
            detail = ""
        return ("HTTP %s %s" % (exc.code, detail)).strip()[:200]
    except Exception as exc:                              # noqa: BLE001
        return str(exc)[:200] or exc.__class__.__name__


def profile(user_id):
    """The LINE profile of a friend of the account: {'displayName', 'language', ...} or None."""
    if not configured() or not USER_ID.match(user_id or ""):
        return None
    req = urllib.request.Request("https://api.line.me/v2/bot/profile/" + user_id, headers={"Authorization": "Bearer " + _token()})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read(65536).decode("utf-8"))
    except Exception:                                     # noqa: BLE001 - the link works without the profile
        return None


def reply(reply_token, text):
    """Answers a message the customer sent (reply API: free, not counted in the monthly messages)."""
    if not reply_token or not configured():
        return "lnNotConfigured"
    return _post(REPLY_URL, {"replyToken": reply_token, "messages": [{"type": "text", "text": text[:5000]}]})


def reason_key(error):
    """Text key of a stored ERROR for the order list: lnNotConfigured / lnNoUser / lnNotFriend / lnApiError."""
    error = error or ""
    if error.startswith("ln"):
        return error
    if "HTTP 400" in error or "HTTP 403" in error or "HTTP 404" in error:
        return "lnNotFriend"
    return "lnApiError"


MSG_IDS = {"finished": "ML_FINISHED", "finished_delivery": "ML_FINISHED_DELIVERY", "delivery": "ML_DELIVERY",
           "label": "ML_LABEL"}                    # update 47: the queue QR at registration


def qr_token(ord_id):
    """The secret part of the public QR picture address of an order (LINE fetches it without signing in)."""
    import hashlib
    import hmac
    from flask import current_app
    key = (current_app.config.get("SECRET_KEY") or "aoi").encode("utf-8")
    return hmac.new(key, ("qr:" + ord_id).encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def public_base(browser_origin=None):
    """https://<server> that LINE can reach: AOI_PUBLIC_URL (.env), else the address in the staff's browser
    (behind Codespaces / a proxy the server only sees its own local address). None when there is no https address."""
    for base in ((os.environ.get("AOI_PUBLIC_URL") or "").strip(), (browser_origin or "").strip()):
        base = base.rstrip("/")
        if re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?$", base):
            return base
    return None


def picture_problem(url):
    """Update 67: can LINE fetch this picture? The server asks its own public address the way LINE does (no sign-in).
    None = fine (or not checked: tests, no address); otherwise a short reason. A Codespaces port that is Private
    answers with GitHub's sign-in page, so LINE shows the card with an empty picture."""
    from flask import current_app
    if not url or current_app.config.get("TESTING"):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "LineBotWebhook/2.0 (AOI picture check)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            kind = resp.headers.get("Content-Type", "")
            if resp.status == 200 and kind.startswith("image/"):
                return None
            return "private" if "github" in resp.geturl() or "text/html" in kind else "HTTP %s %s" % (resp.status, kind)
    except urllib.error.HTTPError as exc:
        return "private" if exc.code in (401, 403) else "HTTP %s" % exc.code
    except Exception as exc:                                  # noqa: BLE001 - no answer at all
        return "unreachable: %s" % str(exc)[:80]


def qr_image_url(order, browser_origin=None):
    from flask import url_for
    base = public_base(browser_origin)
    if not base:
        return None
    return base + url_for("line_api.order_qr", ord_id=order["ORD_ID"], t=qr_token(order["ORD_ID"]))


def order_params(order, when):
    """The {parameters} of the order messages (LINE Message Maintenance)."""
    from .tv import short_name
    from ..i18n import t_in
    local = to_local(when) if when else None
    customer = Customer.find(order.get("CUST_CODE")) or {}
    try:
        from .milling_order import MillingOrder
        pay = "%.2f" % MillingOrder.pay_total(order)
    except Exception:                                     # noqa: BLE001
        pay = "-"
    branch_key = "branch" + (order.get("BRANCH_ID") or "").capitalize()
    branch = t_in("th", branch_key)
    from . import line_msg
    from ..timeutil import now_local
    return {"no": order.get("QR_ID", ""), "name": short_name(customer.get("CUST_NAME", "")) or "",
            "day": line_msg.day_word(local.date() if local else None, now_local().date()),
            "time": local.strftime("%H:%M") if local else "-", "date": local.strftime("%d/%m/%Y") if local else "-",
            "rice_kg": "%.2f" % float(order.get("ACT_RICE") or order.get("EXPCT_RICE") or 0), "pay": pay,
            "branch": branch if branch != branch_key else (order.get("BRANCH_ID") or ""),
            "address": order.get("DELIVERY_ADDRESS") or ""}


def _message(kind, order, when):
    from . import line_msg
    return line_msg.text(MSG_IDS.get(kind, "ML_FINISHED"), **order_params(order, when))


def send(doc):
    """Tries to send one AOITS_NOTIFY record and saves the result."""
    if not configured():
        flag, error = UNSENT, "lnNotConfigured"
    else:
        user_id = ((Customer.find(doc["CUST_CODE"]) or {}).get("LINE_USER_ID") or "").strip()
        if not USER_ID.match(user_id):
            flag, error = ERROR, "lnNoUser"
        else:
            problem = _push(user_id, doc["NOTIFY_MESSAGE"], doc.get("IMAGE_URL"), doc.get("NOTIFY_FLEX"))
            flag, error = (SENT, None) if problem is None else (ERROR, problem)
    now = now_utc()
    values = {"NOTIFY_FLG": flag, "ERROR": error, "LAST_UPDATE": now}
    if flag == SENT:
        values["NOTIFY_DATETIME"] = now
    col(COL).update_one({"NOTIFY_ID": doc["NOTIFY_ID"]}, {"$set": values})
    doc.update(values)
    return doc


def queue(order, kind, when, image_url=None, flex=None):
    """Writes the message of `kind` ('finished' | 'finished_delivery' | 'delivery' | 'label') for a milling order and tries
    to send it. Nothing is written when the message is switched off on LINE Message Maintenance. `image_url`: the QR
    picture sent after the text (update 47)."""
    from . import line_msg
    if not line_msg.active(MSG_IDS.get(kind, "ML_FINISHED")):
        return None
    now = now_utc()
    seq = Counter.next("NT-" + order["ORD_ID"])
    doc = {"NOTIFY_ID": "NT-%s-%02d" % (order["ORD_ID"], seq), "ORD_ID": order["ORD_ID"], "ORD_TYPE": "ML",
           "CUST_CODE": order["CUST_CODE"], "NOTIFY_KIND": kind, "NOTIFY_DATETIME": None, "NOTIFY_FLG": UNSENT,
           "NOTIFY_MESSAGE": _message(kind, order, when), "ERROR": None, "REGIST_DATE": now, "LAST_UPDATE": now,
           "IMAGE_URL": image_url, "NOTIFY_FLEX": flex}
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


# ---------- update 48: the customer card (QR of the customer code) to the customer's LINE ----------
def card_token(cust_code):
    import hashlib
    import hmac
    from flask import current_app
    key = (current_app.config.get("SECRET_KEY") or "aoi").encode("utf-8")
    return hmac.new(key, ("card:" + cust_code).encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def card_image_url(cust_code, browser_origin=None):
    from flask import url_for
    base = public_base(browser_origin)
    return base + url_for("line_api.customer_qr", code=cust_code, t=card_token(cust_code)) if base else None


def send_card(customer, browser_origin=None):
    """The customer card text (LN_CARD) and its QR picture; saved in AOITS_NOTIFY (kind card, ORD_TYPE CU).
    Update 52: sent as the whole card (Flex Message), like the printed card."""
    from . import line_flex, line_msg, phone
    from .tv import short_name
    from ..i18n import t_in
    if not line_msg.active("LN_CARD"):
        return {"NOTIFY_FLG": ERROR, "ERROR": "lnLabelOff"}
    now = now_utc()
    seq = Counter.next("NT-" + customer["CUST_CODE"])
    doc = {"NOTIFY_ID": "NT-%s-%02d" % (customer["CUST_CODE"], seq), "ORD_ID": None, "ORD_TYPE": "CU",
           "CUST_CODE": customer["CUST_CODE"], "NOTIFY_KIND": "card", "NOTIFY_DATETIME": None, "NOTIFY_FLG": UNSENT,
           "NOTIFY_MESSAGE": line_msg.text("LN_CARD", name=short_name(customer.get("CUST_NAME", "")) or customer["CUST_CODE"],
                                           code=customer["CUST_CODE"]),
           "ERROR": None, "REGIST_DATE": now, "LAST_UPDATE": now, "IMAGE_URL": card_image_url(customer["CUST_CODE"], browser_origin)}
    base = public_base(browser_origin)
    doc["NOTIFY_FLEX"] = line_flex.customer_card(customer, doc["IMAGE_URL"], base, phone.of(customer),
                                                 t_in("th", "custCardSub"), t_in("th", "custCardNote")) if doc["IMAGE_URL"] else None
    col(COL).insert_one(dict(doc))
    return send(doc)


def send_receipt(orders, customer, browser_origin=None):
    """Update 62: the receipt (Print Billing) of customer buying/selling orders to the customer's LINE, as a card
    (Flex Message) with the LN_RECEIPT text as its alternative text. Saved in AOITS_NOTIFY (kind receipt, ORD_TYPE
    BY / SL, ORD_ID the first order). The logo is shown only with a public https address; the card is sent anyway."""
    from . import line_flex, line_msg
    from .buysell_order import BuySellOrder
    from .tv import short_name
    from .variety import mat_name
    from ..i18n import t_in
    if not line_msg.active("LN_RECEIPT"):
        return {"NOTIFY_FLG": ERROR, "ERROR": "lnLabelOff"}
    first = orders[0]
    type_th = t_in("th", "svcBuyLabel" if first.get("ORD_TYPE") == "BY" else "svcSellLabel")
    numbers = ", ".join("#%s" % (o.get("QR_ID") or o["ORD_ID"]) for o in orders)
    total = sum(BuySellOrder.amount(o) for o in orders)
    date = to_local(now_utc()).strftime("%d/%m/%Y %H:%M")
    lines = [(mat_name(l["MAT_CODE"], "th"), "{:,.2f}".format(l.get("ORD_QTY") or 0), "{:,.2f}".format(l.get("PRICING_UINT") or 0),
              "{:,.2f}".format(l.get("ORD_AMOUNT") or 0))
             for o in orders for l in BuySellOrder.items_of(o) if (l.get("ORD_QTY") or 0) > 0]
    name = customer.get("CUST_NAME") or first["CUST_CODE"]
    now = now_utc()
    seq = Counter.next("NT-" + first["ORD_ID"])
    doc = {"NOTIFY_ID": "NT-%s-%02d" % (first["ORD_ID"], seq), "ORD_ID": first["ORD_ID"], "ORD_TYPE": first.get("ORD_TYPE"),
           "CUST_CODE": first["CUST_CODE"], "NOTIFY_KIND": "receipt", "NOTIFY_DATETIME": None, "NOTIFY_FLG": UNSENT,
           "NOTIFY_MESSAGE": line_msg.text("LN_RECEIPT", no=numbers, name=short_name(name) or name, type=type_th,
                                           total="{:,.2f}".format(total), date=date),
           "ERROR": None, "REGIST_DATE": now, "LAST_UPDATE": now, "IMAGE_URL": None}
    doc["NOTIFY_FLEX"] = line_flex.receipt("%s · %s" % (t_in("th", "receiptTitle"), type_th), numbers, name, date, lines,
                                           "{:,.2f}".format(total), public_base(browser_origin),
                                           {"no": t_in("th", "receiptOrderNo"), "customer": t_in("th", "receiptCustomer"),
                                            "date": t_in("th", "receiptDate"), "total": t_in("th", "totalLabel")})
    col(COL).insert_one(dict(doc))
    return send(doc)


# ---------- update 96: campaign coupon to the customer's LINE ----------
def coupon_token(coupon_id):
    import hashlib
    import hmac
    from flask import current_app
    key = (current_app.config.get("SECRET_KEY") or "aoi").encode("utf-8")
    return hmac.new(key, ("cpn:" + coupon_id).encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def coupon_image_url(coupon_id, browser_origin=None):
    from flask import url_for
    base = public_base(browser_origin)
    return base + url_for("line_api.coupon_qr", coupon_id=coupon_id, t=coupon_token(coupon_id)) if base else None


def send_coupon(coupon, browser_origin=None):
    """The coupon card (Flex) with its QR; the text is the alternative text. Saved in AOITS_NOTIFY (kind coupon)."""
    from . import line_flex
    from . import campaign as cp
    from .tv import short_name
    from ..i18n import t_in
    customer = Customer.find(coupon["CUST_CODE"]) or {}
    camp = cp.get(coupon["CAMP_ID"]) or {}
    name = short_name(customer.get("CUST_NAME", "")) or coupon["CUST_CODE"]
    value = "{:,.2f}".format(coupon["VALUE"])
    expire = to_local(coupon["EXPIRE_DATE"]).strftime("%d/%m/%Y")
    now = now_utc()
    seq = Counter.next("NT-" + coupon["COUPON_ID"])
    doc = {"NOTIFY_ID": "NT-%s-%02d" % (coupon["COUPON_ID"], seq), "ORD_ID": None, "ORD_TYPE": "CP",
           "CUST_CODE": coupon["CUST_CODE"], "NOTIFY_KIND": "coupon", "NOTIFY_DATETIME": None, "NOTIFY_FLG": UNSENT,
           "NOTIFY_MESSAGE": t_in("th", "cpnLineText", name=name, value=value, expire=expire, camp=camp.get("CAMP_NAME", "")),
           "ERROR": None, "REGIST_DATE": now, "LAST_UPDATE": now, "IMAGE_URL": None}
    qr = coupon_image_url(coupon["COUPON_ID"], browser_origin)
    if qr:
        doc["NOTIFY_FLEX"] = line_flex.coupon_card(camp.get("CAMP_NAME", ""), name, value, expire, coupon.get("NOTE"), qr,
                                                   coupon["COUPON_ID"], public_base(browser_origin),
                                                   {"coupon": t_in("th", "cpnCardSub"), "for": t_in("th", "cpnCardFor"),
                                                    "expire": t_in("th", "cpnCardExpire"), "how": t_in("th", "cpnCardHow")})
    else:
        doc["NOTIFY_FLEX"] = None
        doc["NOTIFY_FLG"], doc["ERROR"] = ERROR, "lnNoPublicUrl"
        col(COL).insert_one(dict(doc))
        return doc
    col(COL).insert_one(dict(doc))
    return send(doc)
