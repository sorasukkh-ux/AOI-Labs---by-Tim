"""Update 108: new members from a paper leaflet, through LINE.

1. Marketing > LINE member leaflet: staff make a leaflet batch (AOIMS_LEAFLET, e.g. "ตลาดนัด ต.1") and print it
   (A5, two per A4 sheet). Its QR opens the chat with the Official Account with "สมัคร REG-<LEAF_ID>" typed in.
2. The person sends it; the webhook replies with a link to a short form (signed for this LINE user, 3 days).
   Adding the account (follow) or a mobile number that is not registered also gets the link (no batch).
3. The form (name, mobile, address, interests, consent) makes a new customer linked to that LINE account, with
   REG_SOURCE = the batch, and LINE sends the member card. A mobile number already at the mill (one customer, not
   linked to another LINE) is linked instead of making a second customer.
"""
import re
import urllib.parse

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text
from .counter import Counter
from .customer import Customer, DELETED
from . import phone
from ..timeutil import now_utc

COL = "AOIMS_LEAFLET"
REG_RE = re.compile(r"REG-(L\d{4})\b", re.I)
TOKEN_DAYS = 3
INTERESTS = ("ML", "SELL", "BUY")          # milling at the mill, sell paddy to the mill, buy rice from the mill
NAME_MAX = 60


# ---------- leaflet batches ----------
def get(leaf_id):
    return col(COL).find_one({"LEAF_ID": (leaf_id or "").upper()}, NO_ID) if leaf_id else None


def batches(branch_id):
    rows = list(col(COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("LEAF_ID", -1)]))
    counts = {}
    for c in col(Customer.COL).find({"REG_SOURCE": {"$in": [r["LEAF_ID"] for r in rows]}}, NO_ID):
        counts[c["REG_SOURCE"]] = counts.get(c["REG_SOURCE"], 0) + 1
    for r in rows:
        r["members"] = counts.get(r["LEAF_ID"], 0)
    return rows


def create(branch_id, form, ope_user):
    name = clean_text(form.get("LEAF_NAME"), NAME_MAX)
    if not name:
        raise ValidationError("lfErrName")
    seq = Counter.next("LEAF")
    if seq > 9999:
        raise ValidationError("errTryAgain")
    doc = {"LEAF_ID": "L%04d" % seq, "BRANCH_ID": branch_id, "LEAF_NAME": name,
           "NOTE": clean_text(form.get("NOTE"), 120) or None, "STATUS": "A"}
    doc.update(audit_new(ope_user))
    col(COL).insert_one(dict(doc))
    return get(doc["LEAF_ID"])


def set_status(leaf_id, branch_id, active, ope_user):
    doc = get(leaf_id)
    if not doc or doc["BRANCH_ID"] != branch_id:
        raise ValidationError("lfErrUnknown")
    values = {"STATUS": "A" if active else "I"}
    values.update(audit_update(ope_user))
    col(COL).update_one({"LEAF_ID": doc["LEAF_ID"]}, {"$set": values})


QR_MAX = 106                               # the QR drawer (js/qr.js) holds up to 106 bytes


def reg_text(leaf_id=None, short=False):
    word = "สมัคร" if not short else ""
    return (word + (" REG-" + leaf_id if leaf_id else "")).strip() or "REG"


def qr_url(leaf_id=None):
    """The leaflet QR: opens the chat with the account, "สมัคร REG-<id>" typed in (Thai letters take 9 bytes each in a
    link, so a long account ID gets the short "REG-<id>"). None without AOI_LINE_OA_ID."""
    from .line_link import oa_id
    if not oa_id():
        return None
    for short in (False, True):
        url = "https://line.me/R/oaMessage/%s/?text=%s" % (urllib.parse.quote(oa_id()), urllib.parse.quote(reg_text(leaf_id, short)))
        if len(url) <= QR_MAX:
            return url
    return url


# ---------- the signed form link ----------
def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="aoi-member-reg")


def token(user_id, leaf_id=None):
    return _signer().dumps({"u": user_id, "s": leaf_id or ""})


def read_token(value):
    """(LINE user ID, leaflet ID or None); ValidationError when the link is wrong or older than TOKEN_DAYS."""
    try:
        data = _signer().loads(value or "", max_age=TOKEN_DAYS * 86400)
    except SignatureExpired:
        raise ValidationError("regErrExpired")
    except BadSignature:
        raise ValidationError("regErrLink")
    user_id = data.get("u") or ""
    if not re.match(r"^U[0-9a-f]{32}$", user_id):
        raise ValidationError("regErrLink")
    return user_id, (data.get("s") or None)


def wants_register(text):
    """(True, leaflet ID or None) when the LINE text asks to register."""
    text = text or ""
    match = REG_RE.search(text)
    if match:
        return True, match.group(1).upper()
    return ("สมัคร" in text or text.strip().upper() in ("REG", "REGISTER")), None


def linked_customer(user_id):
    return col(Customer.COL).find_one({"LINE_USER_ID": user_id, "CUST_STATUS": {"$ne": DELETED}}, NO_ID)


# ---------- the form ----------
def register(user_id, leaf_id, form):
    """Makes (or links) the customer; returns (customer, 'new' | 'linked')."""
    from . import line_link
    if linked_customer(user_id):
        raise ValidationError("regErrAlready")
    if form.get("CONSENT") != "1":
        raise ValidationError("regErrConsent")
    interests = [i for i in INTERESTS if form.get("INT_" + i) == "1"]
    leaf = get(leaf_id) if leaf_id else None
    source = leaf["LEAF_ID"] if leaf else None
    values = {"CUST_NAME": form.get("CUST_NAME"), "MOBILE_NO": form.get("MOBILE_NO"), "ADDRESS": form.get("ADDRESS"),
              "MOBILE_CC": form.get("MOBILE_CC")}
    checked = Customer._validate(values)                  # same rules as Customer Maintenance (required, mobile)
    digits = re.sub(r"\D", "", checked["MOBILE_NO"])
    found = line_link._by_phone(digits)
    extra = {"REG_CHANNEL": "LINE", "REG_SOURCE": source, "INTERESTS": interests, "CONSENT_DATE": now_utc()}
    if len(found) == 1:                                   # already a customer: link this LINE account instead
        customer = found[0]
        if customer.get("LINE_USER_ID") and customer["LINE_USER_ID"] != user_id:
            raise ValidationError("regErrPhoneTaken")
        col(Customer.COL).update_one({"CUST_CODE": customer["CUST_CODE"]},
                                     {"$set": {"INTERESTS": interests, "CONSENT_DATE": extra["CONSENT_DATE"]}})
        line_link._link(customer, user_id, "LEAFLET")
        line_link._log("register", user_id, "linked by form", "reg_linked", customer["CUST_CODE"])
        return Customer.find(customer["CUST_CODE"]), "linked"
    if len(found) > 1:
        raise ValidationError("regErrPhoneMany")
    try:
        customer = Customer.create(values, "LINE")
    except ValidationError as error:
        if error.key == "custErrDupName":                 # never show another customer's name or code on a public page
            raise ValidationError("regErrDupName")
        raise
    col(Customer.COL).update_one({"CUST_CODE": customer["CUST_CODE"]}, {"$set": extra})
    line_link._link(customer, user_id, "LEAFLET")
    line_link._log("register", user_id, source or "-", "registered", customer["CUST_CODE"])
    return Customer.find(customer["CUST_CODE"]), "new"


def recent(branch_ids, limit=30):
    """Members who registered through the leaflets of these branches (or without a leaflet), newest first."""
    leafs = {r["LEAF_ID"]: r for r in col(COL).find({"BRANCH_ID": {"$in": list(branch_ids)}}, NO_ID)}
    rows = []
    for c in col(Customer.COL).find({"REG_CHANNEL": "LINE"}, NO_ID):
        if c.get("REG_SOURCE") and c["REG_SOURCE"] not in leafs:
            continue
        rows.append(dict(c, leaf=leafs.get(c.get("REG_SOURCE")), mobile=phone.of(c)))
    rows.sort(key=lambda c: c.get("CONSENT_DATE") or c.get("REGIST_DATE"), reverse=True)
    return rows[:limit]
