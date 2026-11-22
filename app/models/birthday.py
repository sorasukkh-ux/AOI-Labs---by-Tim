"""Update 133: customer birthdays for marketing (Tim's idea after the fengshuiX Bazi workshop at AI Expo Thailand).

* Customer Registration and the LINE member form take an optional birth date (BIRTH_DATE "YYYY-MM-DD", Gregorian;
  a Buddhist year like 2528 is turned into 1985) and a separate marketing consent (MKT_CONSENT, MKT_CONSENT_DATE).
  PDPA: the birth date is used for marketing only when the customer agreed; without consent nothing is sent.
* The Chinese zodiac animal and element of the birth year are worked out here - no outside service. The year starts
  at Lichun (立春, about 4 February) as in Bazi: someone born on 1-3 February belongs to the year before.
  (fengshuiX has no API; a full Bazi chart would need a paid outside service and is not used.)
* Every morning (AOI_BDAY_HOUR, default 8) the scheduler sends the customers whose birthday is today a LINE birthday
  card: a coupon (Marketing > Birthday: value, valid days) with their zodiac line, or the greeting text only when the
  value is 0. The card goes from the branch of the customer's latest order (no order: the first branch with birthday
  cards on); a branch with birthday cards off sends nothing. One card per customer per year (BDAY_SENT_YEAR is
  claimed before sending, so two workers never send twice). People born on 29 February get it on 28 February in
  other years.
"""
from datetime import date, datetime, timedelta

from .base import col, NO_ID, ValidationError, audit_update, round_half_up, to_number
from .branch import Branch
from .customer import Customer, DELETED
from ..timeutil import local_tz, now_utc, today_local

BDAY = "BDAY"                                   # AOITS_COUPON.CAMP_ID of a birthday coupon
ANIMALS = ("RAT", "OX", "TIGER", "RABBIT", "DRAGON", "SNAKE", "HORSE", "GOAT", "MONKEY", "ROOSTER", "DOG", "PIG")
ANIMAL_ICON = {"RAT": "🐀", "OX": "🐂", "TIGER": "🐅", "RABBIT": "🐇", "DRAGON": "🐉", "SNAKE": "🐍", "HORSE": "🐎",
               "GOAT": "🐐", "MONKEY": "🐒", "ROOSTER": "🐓", "DOG": "🐕", "PIG": "🐖"}
ELEMENTS = ("WOOD", "FIRE", "EARTH", "METAL", "WATER")
LICHUN = (2, 4)                                 # (month, day) the zodiac year starts
DEFAULTS = {"BDAY_FLG": False, "BDAY_VALUE": 20.0, "BDAY_DAYS": 14}
VALUE_MAX, DAYS_MIN, DAYS_MAX = 5000, 1, 90


# ---------- the birth date ----------
def parse(text, today=None):
    """'YYYY-MM-DD' (or DD/MM/YYYY), Buddhist year allowed -> 'YYYY-MM-DD' Gregorian; '' -> None."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            y, m, d = (int(p) for p in (text.split("-") if fmt == "%Y-%m-%d" else reversed(text.split("/"))))
            break
        except ValueError:
            continue
    else:
        raise ValidationError("bdErrDate")
    if y > 2400:                                # พ.ศ.
        y -= 543
    try:
        born = date(y, m, d)
    except ValueError:
        raise ValidationError("bdErrDate")
    today = today or today_local()
    if born > today or born.year < today.year - 110:
        raise ValidationError("bdErrDate")
    return born.isoformat()


def as_date(birth):
    return date(*(int(p) for p in birth.split("-"))) if birth else None


def zodiac(birth):
    """{'year': zodiac year, 'animal': key, 'element': key} of a 'YYYY-MM-DD' birth date, or None."""
    born = as_date(birth)
    if not born:
        return None
    year = born.year - 1 if (born.month, born.day) < LICHUN else born.year
    return {"year": year, "animal": ANIMALS[(year - 4) % 12], "element": ELEMENTS[((year - 4) % 10) // 2]}


def zodiac_text(birth, lang=None):
    """'🐂 ปีฉลู ธาตุไม้' (empty without a birth date)."""
    from ..i18n import t, t_in
    z = zodiac(birth)
    if not z:
        return ""
    tr = (lambda k: t_in(lang, k)) if lang else t
    return "%s %s %s" % (ANIMAL_ICON[z["animal"]], tr("bdAnimal_" + z["animal"]), tr("bdElement_" + z["element"]))


def birthday_in(birth, year):
    """The birthday in `year` (29 Feb -> 28 Feb in other years)."""
    born = as_date(birth)
    try:
        return date(year, born.month, born.day)
    except ValueError:
        return date(year, 2, 28)


def next_birthday(birth, today):
    day = birthday_in(birth, today.year)
    return day if day >= today else birthday_in(birth, today.year + 1)


def age_on(birth, day):
    born = as_date(birth)
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def form_values(form, current=None):
    """The birthday fields of a customer form (only when the form has them)."""
    if "BIRTH_DATE" not in form and "MKT_CONSENT" not in form:
        return {}
    birth = parse(form.get("BIRTH_DATE"))
    consent = form.get("MKT_CONSENT") in ("1", "on", "true")
    old = current or {}
    out = {"BIRTH_DATE": birth, "BIRTH_MMDD": birth[5:] if birth else None, "MKT_CONSENT": consent,
           "MKT_CONSENT_DATE": (old.get("MKT_CONSENT_DATE") if old.get("MKT_CONSENT") else now_utc()) if consent else None}
    if "GENDER" in form:                              # update 134: the Bazi API needs gender; birth time is optional
        out["GENDER"] = form.get("GENDER") if form.get("GENDER") in ("male", "female") else None
    if "BIRTH_TIME" in form:
        out["BIRTH_TIME"] = parse_time(form.get("BIRTH_TIME"))
    return out


def parse_time(text):
    """'HH:MM' (24 h) or None."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        h, m = (int(p) for p in text.split(":")[:2])
    except ValueError:
        raise ValidationError("bdErrTime")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValidationError("bdErrTime")
    return "%02d:%02d" % (h, m)


# ---------- settings (Marketing > Birthday, per branch) ----------
def settings(branch_id):
    branch = Branch.get(branch_id) or {}
    return {k: branch.get(k, v) if branch.get(k) is not None else v for k, v in DEFAULTS.items()}


def save_settings(branch_id, form, ope_user):
    if not Branch.get(branch_id):
        raise ValidationError("errBranchUnknown")
    try:
        value, days = to_number(form.get("BDAY_VALUE")), to_number(form.get("BDAY_DAYS"))
    except ValueError:
        raise ValidationError("errNumber")
    if value is None or not 0 <= value <= VALUE_MAX:
        raise ValidationError("bdErrValue", hi="{:,}".format(VALUE_MAX))
    if days is None or not DAYS_MIN <= days <= DAYS_MAX or days != int(days):
        raise ValidationError("bdErrDays", lo=DAYS_MIN, hi=DAYS_MAX)
    values = {"BDAY_FLG": form.get("BDAY_FLG") in ("1", "on", "true"), "BDAY_VALUE": round_half_up(value, 2), "BDAY_DAYS": int(days)}
    values.update(audit_update(ope_user))
    col(Branch.COL).update_one({"BRANCH_ID": branch_id}, {"$set": values})
    return settings(branch_id)


# ---------- who gets a card ----------
def home_branch(cust_code):
    """The branch of the customer's latest order (milling or buying / selling), or None."""
    from .milling_order import MillingOrder
    from .buysell_order import BuySellOrder
    latest = None
    for name in (MillingOrder.COL, BuySellOrder.COL):
        doc = col(name).find_one({"CUST_CODE": cust_code}, {"_id": 0, "BRANCH_ID": 1, "REGIST_DATE": 1}, sort=[("REGIST_DATE", -1)])
        if doc and (latest is None or doc["REGIST_DATE"] > latest["REGIST_DATE"]):
            latest = doc
    return latest["BRANCH_ID"] if latest else None


def branch_for(cust_code):
    home = home_branch(cust_code)
    if home:
        return home if settings(home)["BDAY_FLG"] else None
    return next((b for b in Branch.ids() if settings(b)["BDAY_FLG"]), None)


def _mmdd_today(today):
    days = [today.strftime("%m-%d")]
    if (today.month, today.day) == (2, 28) and birthday_in("2000-02-29", today.year) == today:
        days.append("02-29")                    # not a leap year: 29 February people on the 28th
    return days


def due(today=None):
    """Customers with a birthday today, consent, LINE, not sent this year."""
    today = today or today_local()
    q = {"BIRTH_MMDD": {"$in": _mmdd_today(today)}, "MKT_CONSENT": True, "CUST_STATUS": {"$ne": DELETED},
         "LINE_USER_ID": {"$nin": [None, ""]}, "BDAY_SENT_YEAR": {"$ne": today.year}}
    return list(col(Customer.COL).find(q, NO_ID).sort([("CUST_CODE", 1)]))


def message(customer, value=None, expire=None, today=None):
    """The LINE text (LN_BIRTHDAY, editable on LINE Message Maintenance). Update 134: + the fengshuiX reading."""
    from . import line_msg
    from .tv import short_name
    today = today or today_local()
    birth = customer.get("BIRTH_DATE")
    fortune = customer.get("BDAY_FORTUNE") if customer.get("BDAY_SENT_YEAR") == today.year else ""
    text = line_msg.text("LN_BIRTHDAY", name=short_name(customer.get("CUST_NAME", "")) or customer["CUST_CODE"],
                         zodiac=zodiac_text(birth, "th"), age=age_on(birth, today) if birth else "", fortune=fortune or "")
    if fortune and fortune not in text:              # the text has no {fortune}: added below the wishes
        text += "\n🔮 " + fortune
    if value:
        from ..i18n import t_in
        text += "\n" + t_in("th", "bdLineCoupon", value="{:,.2f}".format(value), expire=expire)
    return text


def send_one(customer, today=None, browser_origin=None, ope_user="system:birthday"):
    """Claims and sends one customer's card. Returns the result dict (status sent / error / skipped)."""
    from . import coupon as cpn, notify
    today = today or today_local()
    branch = branch_for(customer["CUST_CODE"])
    if not branch:
        return {"code": customer["CUST_CODE"], "status": "skipped"}
    claimed = col(Customer.COL).update_one({"CUST_CODE": customer["CUST_CODE"], "BDAY_SENT_YEAR": {"$ne": today.year}},
                                           {"$set": {"BDAY_SENT_YEAR": today.year, "BDAY_SENT_DATE": now_utc(), "BDAY_BRANCH": branch,
                                                     "BDAY_COUPON_ID": None, "BDAY_ERROR": None}})
    if not claimed.modified_count:
        return {"code": customer["CUST_CODE"], "status": "skipped"}
    fortune(customer, today)                          # update 134: the fengshuiX reading (optional)
    customer = Customer.find(customer["CUST_CODE"])
    cfg = settings(branch)
    if cfg["BDAY_VALUE"] > 0:
        expire = datetime.combine(today + timedelta(days=cfg["BDAY_DAYS"] - 1), datetime.min.time()).replace(tzinfo=local_tz())
        coupon = cpn.create(branch, customer["CUST_CODE"], cfg["BDAY_VALUE"], expire, zodiac_text(customer.get("BIRTH_DATE"), "th"),
                            BDAY, ope_user)
        ok = cpn.send(coupon, browser_origin)
        error = None if ok else (cpn.get(coupon["COUPON_ID"]) or {}).get("LINE_ERROR")
        col(Customer.COL).update_one({"CUST_CODE": customer["CUST_CODE"]}, {"$set": {"BDAY_COUPON_ID": coupon["COUPON_ID"], "BDAY_ERROR": error}})
    else:
        doc = notify.send_text(customer["CUST_CODE"], "birthday", message(customer, today=today))
        ok = doc.get("NOTIFY_FLG") == notify.SENT
        error = None if ok else (doc.get("ERROR") or "lnNotConfigured")
        col(Customer.COL).update_one({"CUST_CODE": customer["CUST_CODE"]}, {"$set": {"BDAY_ERROR": error}})
    return {"code": customer["CUST_CODE"], "status": "sent" if ok else "error", "error": error, "branch": branch}


def fortune(customer, today):
    """Asks the fengshuiX Bazi API for the customer's birthday reading and keeps it (BDAY_FORTUNE) - or the reason
    it is not there (BDAY_FORTUNE_ERROR). Never stops the card."""
    from . import bazi_api
    if not bazi_api.configured() or not customer.get("BIRTH_DATE"):
        return None
    result = bazi_api.call(customer["BIRTH_DATE"], customer.get("GENDER"), customer.get("BIRTH_TIME"), end_user=customer["CUST_CODE"],
                           idem=bazi_api.idem_for(customer["CUST_CODE"], today.year))
    col(Customer.COL).update_one({"CUST_CODE": customer["CUST_CODE"]},
                                 {"$set": {"BDAY_FORTUNE": result["text"] or None,
                                           "BDAY_FORTUNE_ERROR": None if result["ok"] else (result.get("detail") or result["error"])}})
    return result


def run(today=None, browser_origin=None, ope_user="system:birthday"):
    """Today's birthday cards. Returns the results."""
    return [send_one(c, today, browser_origin, ope_user) for c in due(today)]


def daily_due(hour):
    """The scheduler's turn: once a day after `hour` (the first worker takes it)."""
    from .counter import Counter
    from ..timeutil import now_local
    now = now_local()
    if now.hour < hour:
        return False
    return Counter.next("BDAY-RUN-" + now.strftime("%Y%m%d")) == 1


# ---------- the Marketing > Birthday page ----------
def upcoming(days=30, today=None):
    """Customers with a birth date whose birthday is within `days` days: [(customer, day, days left, zodiac text)]."""
    today = today or today_local()
    out = []
    for c in col(Customer.COL).find({"BIRTH_DATE": {"$nin": [None, ""]}, "CUST_STATUS": {"$ne": DELETED}}, NO_ID):
        day = next_birthday(c["BIRTH_DATE"], today)
        left = (day - today).days
        if left <= days:
            out.append({"c": c, "day": day, "left": left, "age": age_on(c["BIRTH_DATE"], day), "zodiac": zodiac_text(c["BIRTH_DATE"]),
                        "ready": bool(c.get("MKT_CONSENT") and (c.get("LINE_USER_ID") or "").strip())})
    return sorted(out, key=lambda r: (r["left"], r["c"]["CUST_CODE"]))


def sent(branch_id, limit=50):
    """The cards sent from this branch, newest first, with the coupon state."""
    from . import coupon as cpn
    rows = []
    for c in col(Customer.COL).find({"BDAY_BRANCH": branch_id, "BDAY_SENT_DATE": {"$ne": None}}, NO_ID).sort([("BDAY_SENT_DATE", -1)]).limit(limit):
        coupon = cpn.get(c["BDAY_COUPON_ID"]) if c.get("BDAY_COUPON_ID") else None
        rows.append({"c": c, "coupon": coupon, "state": cpn.state(coupon) if coupon else None})
    return rows


def counts():
    q = {"CUST_STATUS": {"$ne": DELETED}}
    return {"with_date": col(Customer.COL).count_documents(dict(q, BIRTH_DATE={"$nin": [None, ""]})),
            "consent": col(Customer.COL).count_documents(dict(q, MKT_CONSENT=True, BIRTH_DATE={"$nin": [None, ""]})),
            "gender": col(Customer.COL).count_documents(dict(q, MKT_CONSENT=True, GENDER={"$in": ["male", "female"]})),
            "all": col(Customer.COL).count_documents(q)}
