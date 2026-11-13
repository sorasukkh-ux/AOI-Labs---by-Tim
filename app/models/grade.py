"""Update 126: rice / paddy quality grading from a camera grader on a Raspberry Pi (Tim's DIY grader plan).

The grader photographs a thin layer of grains, counts whole and broken ones and posts the result to the device API:

    POST /api/v1/graders/<GRADER_ID>/results   header X-Device-Key
         {"sample": "S-0012", "order": "BY-20261114-0003" (optional), "material": "PADDY_HM" (optional),
          "moisture": 14.2, "grains": 512, "whole": 82.4, "broken": 17.6, "head_rice": 48.9,
          "chalky": 3.1, "yellow": 0.8, "avg_len": 7.2, "avg_wid": 2.1, "note": "...",
          "variety": "ขาวดอกมะลิ 105" (optional, the variety recognised), "purity": 96.5 (optional, % of grains of it),
          "photo": "<base64 JPEG or PNG, at most 400 KB>" (optional), "time": ISO 8601 (optional)}
    GET  /api/v1/graders/<GRADER_ID>/orders    the open buying orders of the branch (to pick one on the Pi)

Graders are their own small registry (AOIMS_GRADER: one device key each) - not machines: nothing to start or stop.
A result (AOITS_GRADE, photo in AOITS_GRADEPHOTO) is kept with a buying order: sent with the order's id, or attached
by the staff on the Buying / Selling Order List. Numbers are what the grader measured; AOI does not grade or price by
them yet (grade-linked pricing comes later, after the grader is validated against hand grading).
"""
import base64
import binascii
import secrets
from datetime import datetime, timedelta, timezone

from .base import NO_ID, ValidationError, audit_new, audit_update, clean_text, col, round_half_up, to_number
from .automation import ONLINE_SECONDS, _hash, new_device_key
from .buysell_order import BUY, OPEN, PREPARED, UNPAID, BuySellOrder
from .counter import Counter
from . import variety as var
from ..timeutil import now_utc

DEV, COL, PHOTO = "AOIMS_GRADER", "AOITS_GRADE", "AOITS_GRADEPHOTO"
PHOTO_MAX = 400 * 1024
UNLINKED_HOURS = 72                # a result can be attached to an order for this long
MATERIALS = tuple(c for c in var.ALL if var.kind_of(c) in (var.PADDY, var.RICE))
# field of the request -> (stored field, low, high, places)
NUMBERS = {"moisture": ("MOISTURE_PCT", 0, 50, 1), "whole": ("WHOLE_PCT", 0, 100, 1), "broken": ("BROKEN_PCT", 0, 100, 1),
           "head_rice": ("HEAD_RICE_PCT", 0, 100, 1), "chalky": ("CHALKY_PCT", 0, 100, 1), "yellow": ("YELLOW_PCT", 0, 100, 1),
           "avg_len": ("AVG_LEN_MM", 0, 20, 2), "avg_wid": ("AVG_WID_MM", 0, 10, 2),
           "purity": ("PURITY_PCT", 0, 100, 1)}      # update 127: share of grains of the variety found (needs a trained model on the grader)


# ---------- the grader devices ----------
def devices(branch_id):
    out = list(col(DEV).find({"BRANCH_ID": branch_id}, NO_ID).sort([("GRADER_ID", 1)]))
    for d in out:
        d["online"] = is_online(d)
    return out


def device(branch_id, grader_id):
    return col(DEV).find_one({"BRANCH_ID": branch_id, "GRADER_ID": (grader_id or "").strip().upper()}, NO_ID)


def is_online(dev):
    seen = dev.get("LAST_SEEN")
    return bool(seen) and (now_utc() - seen).total_seconds() <= max(ONLINE_SECONDS, 300)     # a grader reports per sample


def by_key(grader_id, key):
    dev = col(DEV).find_one({"GRADER_ID": (grader_id or "").strip().upper()}, NO_ID)
    if dev and dev.get("ACTIVE_FLG") is not False and dev.get("DEVICE_KEY_HASH") and key and \
            secrets.compare_digest(dev["DEVICE_KEY_HASH"], _hash(key)):
        return dev
    return None


def seen(dev):
    col(DEV).update_one({"GRADER_ID": dev["GRADER_ID"]}, {"$set": {"LAST_SEEN": now_utc()}})


def issue_key(branch_id, grader_id, ope_user):
    """A new device key; the old one stops working. Returns the key (shown once)."""
    if not device(branch_id, grader_id):
        raise ValidationError("grErrDevice")
    key, hashed = new_device_key()
    col(DEV).update_one({"BRANCH_ID": branch_id, "GRADER_ID": grader_id.upper()},
                        {"$set": dict({"DEVICE_KEY_HASH": hashed}, **audit_update(ope_user))})
    return key


def seed_devices(db, branches):
    """One grader per branch (F1-GRADER, F2-GRADER ...), without a key until one is issued."""
    for n, branch_id in enumerate(branches):
        digits = "".join(ch for ch in branch_id if ch.isdigit())
        gid = "F%s-GRADER" % (digits or n + 1)
        if not db[DEV].find_one({"GRADER_ID": gid}, NO_ID):
            db[DEV].insert_one(dict({"GRADER_ID": gid, "BRANCH_ID": branch_id, "GRADER_NAME": "เครื่องตรวจคุณภาพข้าว (Raspberry Pi)",
                                     "DEVICE_KEY_HASH": None, "LAST_SEEN": None, "ACTIVE_FLG": True}, **audit_new("SYSTEM")))


# ---------- results ----------
def _photo(raw):
    """(bytes, mime) of a base64 photo, or (None, None). JPEG and PNG only."""
    if not raw:
        return None, None
    if not isinstance(raw, str):
        raise ValidationError("grErrPhoto")
    text = raw.split(",", 1)[1] if raw.startswith("data:") and "," in raw else raw
    if len(text) > PHOTO_MAX * 4 // 3 + 16:
        raise ValidationError("grErrPhotoSize", kb=PHOTO_MAX // 1024)
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError("grErrPhoto")
    if len(data) > PHOTO_MAX:
        raise ValidationError("grErrPhotoSize", kb=PHOTO_MAX // 1024)
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    raise ValidationError("grErrPhoto")


def save_result(dev, body):
    """Stores one result posted by a grader. Returns the record."""
    if not isinstance(body, dict):
        raise ValidationError("grErrBody")
    values = {}
    for name, (field, low, high, places) in NUMBERS.items():
        try:
            v = to_number(body.get(name))
        except (ValueError, TypeError):
            raise ValidationError("grErrNumber", field=name)
        if v is not None and not (low <= v <= high):
            raise ValidationError("grErrNumber", field=name)
        values[field] = None if v is None else round_half_up(v, places)
    if values["WHOLE_PCT"] is None and values["BROKEN_PCT"] is None and values["HEAD_RICE_PCT"] is None and values["MOISTURE_PCT"] is None:
        raise ValidationError("grErrEmpty")
    if values["WHOLE_PCT"] is not None and values["BROKEN_PCT"] is not None and values["WHOLE_PCT"] + values["BROKEN_PCT"] > 100.5:
        raise ValidationError("grErrSum")
    try:
        grains = to_number(body.get("grains"))
    except (ValueError, TypeError):
        raise ValidationError("grErrNumber", field="grains")
    if grains is not None and not (1 <= grains <= 100000):
        raise ValidationError("grErrNumber", field="grains")
    material = str(body.get("material") or "").strip().upper() or None
    if material and material not in MATERIALS:
        raise ValidationError("grErrMaterial")
    when = now_utc()
    if body.get("time"):
        try:
            when = datetime.fromisoformat(str(body["time"]).replace("Z", "+00:00"))
            when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValidationError("grErrTime")
        if when > now_utc() + timedelta(minutes=10) or when < now_utc() - timedelta(days=7):
            raise ValidationError("grErrTime")
    photo, mime = _photo(body.get("photo"))
    order = None
    if body.get("order"):
        order = _buy_order(dev["BRANCH_ID"], str(body["order"]))
    doc = dict(values, GRADE_ID="GR%06d" % Counter.next("GRADE"), BRANCH_ID=dev["BRANCH_ID"], GRADER_ID=dev["GRADER_ID"],
               SAMPLE_NO=clean_text(body.get("sample"), 30) or None, MAT_CODE=material or (order or {}).get("MAT_CODE"),
               GRAINS=None if grains is None else int(grains), NOTE=clean_text(body.get("note"), 200) or None,
               VARIETY=clean_text(body.get("variety"), 40) or None,          # update 127: the variety the grader recognised, as text
               PHOTO_FLG=bool(photo), ORD_ID=order["ORD_ID"] if order else None, LINK_USER="device:" + dev["GRADER_ID"] if order else None,
               LINK_DATE=now_utc() if order else None, TEST_DATE=when, REGIST_DATE=now_utc())
    col(COL).insert_one(dict(doc))
    if photo:
        col(PHOTO).insert_one({"GRADE_ID": doc["GRADE_ID"], "MIME": mime, "DATA": base64.b64encode(photo).decode("ascii"), "REGIST_DATE": now_utc()})
    seen(dev)
    return doc


def _buy_order(branch_id, ord_id):
    """The buying order a result belongs to: by ORD_ID or by its QR text (AOI-<ORD_ID>)."""
    text = ord_id.strip().upper()
    text = text[4:] if text.startswith("AOI-") else text
    order = BuySellOrder.find(text, branch_id)
    if not order or order["ORD_TYPE"] != BUY:
        raise ValidationError("grErrOrder", id=ord_id[:40])
    return order


def get(grade_id, branch_id=None):
    q = {"GRADE_ID": (grade_id or "").strip().upper()}
    if branch_id:
        q["BRANCH_ID"] = branch_id
    return col(COL).find_one(q, NO_ID)


def photo(grade_id, branch_id):
    """(bytes, mime) of a result's photo, or None."""
    if not get(grade_id, branch_id):
        return None
    doc = col(PHOTO).find_one({"GRADE_ID": grade_id.upper()}, NO_ID)
    return (base64.b64decode(doc["DATA"]), doc["MIME"]) if doc else None


def recent(branch_id, limit=40):
    return list(col(COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("TEST_DATE", -1)]).limit(limit))


def unlinked(branch_id):
    """Results not yet kept with an order, newest first (only the last UNLINKED_HOURS)."""
    since = now_utc() - timedelta(hours=UNLINKED_HOURS)
    return list(col(COL).find({"BRANCH_ID": branch_id, "ORD_ID": None, "TEST_DATE": {"$gte": since}}, NO_ID).sort([("TEST_DATE", -1)]).limit(30))


def for_orders(ord_ids):
    """{ORD_ID: [results, newest first]}"""
    out = {}
    if ord_ids:
        for g in col(COL).find({"ORD_ID": {"$in": list(ord_ids)}}, NO_ID).sort([("TEST_DATE", -1)]):
            out.setdefault(g["ORD_ID"], []).append(g)
    return out


def attach(branch_id, grade_id, ord_id, ope_user):
    g = get(grade_id, branch_id)
    if not g:
        raise ValidationError("grErrUnknown")
    if g.get("ORD_ID"):
        raise ValidationError("grErrLinked", id=g["ORD_ID"])
    order = _buy_order(branch_id, ord_id)
    values = {"ORD_ID": order["ORD_ID"], "LINK_USER": ope_user, "LINK_DATE": now_utc()}
    if not g.get("MAT_CODE"):
        values["MAT_CODE"] = order.get("MAT_CODE")
    col(COL).update_one({"GRADE_ID": g["GRADE_ID"]}, {"$set": values})
    return get(g["GRADE_ID"])


def detach(branch_id, grade_id, ope_user):
    g = get(grade_id, branch_id)
    if not g or not g.get("ORD_ID"):
        raise ValidationError("grErrUnknown")
    col(COL).update_one({"GRADE_ID": g["GRADE_ID"]}, {"$set": {"ORD_ID": None, "LINK_USER": ope_user, "LINK_DATE": now_utc()}})
    return get(g["GRADE_ID"])


def open_orders(branch_id):
    """For the grader's screen: the buying orders still in work (no customer data)."""
    out = []
    for o in BuySellOrder.for_branch(branch_id, (OPEN, PREPARED, UNPAID), BUY)[:30]:
        out.append({"order": o["ORD_ID"], "qr": o.get("QR_ID"), "material": o.get("MAT_CODE"), "kg": o.get("ORD_QTY"),
                    "registered": o["REGIST_DATE"].isoformat()})
    return out


def summary(branch_id, days=30):
    since = now_utc() - timedelta(days=days)
    rows = list(col(COL).find({"BRANCH_ID": branch_id, "TEST_DATE": {"$gte": since}}, NO_ID))
    avg = lambda f: round_half_up(sum(r[f] for r in rows if r.get(f) is not None) / max(1, sum(1 for r in rows if r.get(f) is not None)), 1) \
        if any(r.get(f) is not None for r in rows) else None
    return {"n": len(rows), "linked": sum(1 for r in rows if r.get("ORD_ID")), "head_rice": avg("HEAD_RICE_PCT"),
            "broken": avg("BROKEN_PCT"), "moisture": avg("MOISTURE_PCT"), "days": days}


# ---------- update 127: the result as a printed slip (receipt format) and to the customer's LINE ----------
REPORT_FIELDS = (("VARIETY", "grVariety", ""), ("PURITY_PCT", "grPurity", "%"), ("HEAD_RICE_PCT", "grHead", "%"), ("WHOLE_PCT", "grWhole", "%"), ("BROKEN_PCT", "grBroken", "%"), ("CHALKY_PCT", "grChalky", "%"),
                 ("YELLOW_PCT", "grYellow", "%"), ("MOISTURE_PCT", "grMoisture", "%"), ("GRAINS", "grGrains", ""),
                 ("AVG_LEN_MM", "grAvgLen", " mm"), ("AVG_WID_MM", "grAvgWid", " mm"))


def report_rows(g, lang=None):
    """[(label, value text)] of the values the grader measured (missing ones left out)."""
    from ..i18n import t, t_in
    tr = (lambda k: t_in(lang, k)) if lang else t
    return [(tr(key), "%s%s" % (g[field], unit)) for field, key, unit in REPORT_FIELDS if g.get(field) is not None]


def report_context(g):
    """The order and customer of a result (None, {} when it is not kept with an order yet)."""
    from .customer import Customer
    order = BuySellOrder.find(g["ORD_ID"], g["BRANCH_ID"]) if g.get("ORD_ID") else None
    return order, (Customer.find(order["CUST_CODE"]) or {}) if order else {}


def mark_line(grade_id, ope_user):
    col(COL).update_one({"GRADE_ID": grade_id}, {"$set": {"LINE_DATE": now_utc(), "LINE_USER": ope_user}})
