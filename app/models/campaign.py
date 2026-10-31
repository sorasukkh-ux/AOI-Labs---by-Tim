"""Christmas / New Year campaign (update 93, Tim): AOIMS_CAMPAIGN, AOITS_CAMPGIFT, AOITS_CAMPDRAW.

One campaign per branch and period, with three order types - Milling (ML), Buying (BY: the mill buys from the customer)
and Selling (SL: the mill sells to the customer). Points ADD UP per customer and order type over the campaign:
points = floor(total service kg / KG_PER_POINT) (default 10 kg = 1 point).
  * Milling: the milling orders registered in the period that reached Finished (F, D or C), weighed paddy
    (ACT_PADDY) or registered paddy.
  * Buying / Selling: the orders registered in the period that are Closed (C), ORD_QTY.
Each type has three gift levels (points needed + gift, typed by the mill). A customer gets each level's gift once
when the points reach it; staff record when the gift is handed over (AOITS_CAMPGIFT).
A special gift per type is drawn once (AOITS_CAMPDRAW) among the customers who reached level 1, the chance in
proportion to their points; the winner is picked on the server (secrets) before the lucky wheel spins on screen.
"""
import secrets
from datetime import datetime, timedelta

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up
from .customer import Customer
from ..timeutil import local_tz, now_utc, to_local, today_local

COL, GIFT_COL, DRAW_COL = "AOIMS_CAMPAIGN", "AOITS_CAMPGIFT", "AOITS_CAMPDRAW"
TYPES = ("ML", "BY", "SL")
LEVELS = 3
MILL_DONE = ("F", "D", "C")
DEFAULT_LEVELS = {"ML": (50, 100, 200), "BY": (100, 300, 500), "SL": (10, 30, 60)}


def _date(text, field):
    text = (text or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=local_tz())
    except ValueError:
        raise ValidationError("cpErrDate", field=field)


def get(camp_id):
    return col(COL).find_one({"CAMP_ID": camp_id}, NO_ID)


def for_branch(branch_id):
    return sorted(col(COL).find({"BRANCH_ID": branch_id}, NO_ID), key=lambda c: c["DATE_FROM"], reverse=True)


def state(camp, today=None):
    today = today or today_local()
    if camp.get("STATUS") != "A":
        return "inactive"
    if today < to_local(camp["DATE_FROM"]).date():
        return "scheduled"
    if today > to_local(camp["DATE_TO"]).date():
        return "ended"
    return "running"


def blank(branch_id):
    year = today_local().year
    return {"CAMP_ID": None, "BRANCH_ID": branch_id, "CAMP_NAME": "Christmas & New Year %d" % (year + 1),
            "DATE_FROM": datetime(year, 12, 1, tzinfo=local_tz()), "DATE_TO": datetime(year + 1, 1, 15, tzinfo=local_tz()),
            "STATUS": "I", "KG_PER_POINT": 10,
            "TYPES": {t: {"LEVELS": [{"POINTS": p, "GIFT": ""} for p in DEFAULT_LEVELS[t]], "SPECIAL": ""} for t in TYPES}}


def save(branch_id, camp_id, form, ope_user):
    """Create (camp_id None) or change a campaign from the Promotion screen."""
    name = clean_text(form.get("CAMP_NAME"), 80)
    if not name:
        raise ValidationError("cpErrName")
    start, end = _date(form.get("DATE_FROM"), "from"), _date(form.get("DATE_TO"), "to")
    if end < start:
        raise ValidationError("mkErrDates")
    try:
        kg = int(float(form.get("KG_PER_POINT") or 10))
    except ValueError:
        raise ValidationError("errNumber")
    if not 1 <= kg <= 1000:
        raise ValidationError("cpErrKg")
    types = {}
    for t in TYPES:
        levels, last = [], 0
        for n in range(1, LEVELS + 1):
            try:
                pts = int(float(form.get("PTS_%s_%d" % (t, n)) or 0))
            except ValueError:
                raise ValidationError("errNumber")
            gift = clean_text(form.get("GIFT_%s_%d" % (t, n)), 80)
            if gift and pts <= last:                  # levels go up: 1 < 2 < 3
                raise ValidationError("cpErrLevels", type=t)
            if gift:
                last = pts
            levels.append({"POINTS": pts, "GIFT": gift or ""})
        types[t] = {"LEVELS": levels, "SPECIAL": clean_text(form.get("SPECIAL_%s" % t), 80) or ""}
    values = {"CAMP_NAME": name, "DATE_FROM": start, "DATE_TO": end, "KG_PER_POINT": kg, "TYPES": types,
              "STATUS": "A" if form.get("STATUS") == "A" else "I"}
    if camp_id:
        camp = get(camp_id)
        if not camp or camp["BRANCH_ID"] != branch_id:
            raise ValidationError("cpErrNotFound")
        values.update(audit_update(ope_user))
        col(COL).update_one({"CAMP_ID": camp_id}, {"$set": values})
        return get(camp_id)
    camp_id = "CP-%s-%s" % (branch_id, secrets.token_hex(3).upper())
    doc = dict(values, CAMP_ID=camp_id, BRANCH_ID=branch_id)
    doc.update(audit_new(ope_user))
    col(COL).insert_one(dict(doc))
    return get(camp_id)


# ---------- points ----------
def _kg_by_customer(camp, kind):
    lo = camp["DATE_FROM"]
    hi = camp["DATE_TO"] + timedelta(days=1)                   # to the end of the last day
    q = {"BRANCH_ID": camp["BRANCH_ID"], "REGIST_DATE": {"$gte": lo, "$lt": hi}}
    out = {}
    if kind == "ML":
        for o in col("AOITS_ORDMILLING").find(dict(q, MILL_STATUS={"$in": list(MILL_DONE)}), NO_ID):
            kg = o.get("ACT_PADDY") or o.get("REGIST_PADDY") or 0
            c = out.setdefault(o["CUST_CODE"], {"kg": 0.0, "orders": 0})
            c["kg"] += float(kg); c["orders"] += 1
    else:
        for o in col("AOITS_ORDBUYSELL").find(dict(q, ORD_TYPE=kind, ORD_STATUS="C"), NO_ID):
            c = out.setdefault(o["CUST_CODE"], {"kg": 0.0, "orders": 0})
            c["kg"] += float(o.get("ORD_QTY") or 0); c["orders"] += 1
    return out


def standings(camp, kind):
    """[{cust, name, kg, orders, points, level, next, to_next, gifts: [{level, gift, given, given_date}]}] best first."""
    per = int(camp.get("KG_PER_POINT") or 10)
    levels = [l for l in camp["TYPES"][kind]["LEVELS"] if l.get("GIFT")]
    given = {(g["CUST_CODE"], g["LEVEL"]): g for g in col(GIFT_COL).find({"CAMP_ID": camp["CAMP_ID"], "ORD_TYPE": kind}, NO_ID)}
    rows = []
    for code, v in _kg_by_customer(camp, kind).items():
        points = int(v["kg"] // per)
        reached = [i + 1 for i, l in enumerate(levels) if points >= l["POINTS"]]
        nxt = next((l for l in levels if points < l["POINTS"]), None)
        rows.append({"cust": code, "name": (Customer.find(code) or {}).get("CUST_NAME", code), "kg": round_half_up(v["kg"], 1),
                     "orders": v["orders"], "points": points, "level": max(reached) if reached else 0,
                     "next": nxt, "to_next": (nxt["POINTS"] - points) if nxt else 0,
                     "gifts": [{"level": n, "gift": levels[n - 1]["GIFT"], "given": (code, n) in given,
                                "given_date": to_local(given[(code, n)]["GIVEN_DATE"]).strftime("%d/%m/%Y") if (code, n) in given else None}
                               for n in reached]})
    rows.sort(key=lambda r: (-r["points"], r["name"]))
    return rows


def customer_points(camp, cust_code):
    """For the receipt / LINE: {type: {points, level, next, to_next}} of one customer."""
    out = {}
    for t in TYPES:
        row = next((r for r in standings(camp, t) if r["cust"] == cust_code), None)
        if row:
            out[t] = {k: row[k] for k in ("points", "level", "next", "to_next", "kg")}
    return out


def running(branch_id):
    return next((c for c in for_branch(branch_id) if state(c) == "running"), None)


def give(camp_id, branch_id, kind, cust_code, level, ope_user):
    """Staff hand the level's gift over (once)."""
    camp = get(camp_id)
    if not camp or camp["BRANCH_ID"] != branch_id or kind not in TYPES:
        raise ValidationError("cpErrNotFound")
    row = next((r for r in standings(camp, kind) if r["cust"] == cust_code), None)
    if not row or level not in [g["level"] for g in row["gifts"]]:
        raise ValidationError("cpErrNotReached")
    if col(GIFT_COL).find_one({"CAMP_ID": camp_id, "ORD_TYPE": kind, "CUST_CODE": cust_code, "LEVEL": level}):
        raise ValidationError("cpErrGiven")
    doc = {"CAMP_ID": camp_id, "BRANCH_ID": branch_id, "ORD_TYPE": kind, "CUST_CODE": cust_code, "LEVEL": level,
           "GIFT": camp["TYPES"][kind]["LEVELS"][level - 1]["GIFT"], "POINTS": row["points"], "GIVEN_DATE": now_utc(), "GIVEN_USER": ope_user}
    doc.update(audit_new(ope_user))
    col(GIFT_COL).insert_one(doc)
    return doc


# ---------- the special gift: one lucky draw per type ----------
def draw_of(camp_id, kind):
    return col(DRAW_COL).find_one({"CAMP_ID": camp_id, "ORD_TYPE": kind}, NO_ID)


def entrants(camp, kind):
    """Customers who reached level 1, with tickets = points."""
    return [{"cust": r["cust"], "name": r["name"], "tickets": r["points"]} for r in standings(camp, kind) if r["level"] >= 1]


def draw(camp_id, branch_id, kind, ope_user):
    """Pick the winner once (secrets.SystemRandom, weighted by points) and keep it; the wheel then shows it."""
    camp = get(camp_id)
    if not camp or camp["BRANCH_ID"] != branch_id or kind not in TYPES:
        raise ValidationError("cpErrNotFound")
    if not camp["TYPES"][kind].get("SPECIAL"):
        raise ValidationError("cpErrNoSpecial")
    done = draw_of(camp_id, kind)
    if done:
        return done
    pool = entrants(camp, kind)
    if not pool:
        raise ValidationError("cpErrNoEntrants")
    total = sum(p["tickets"] for p in pool)
    pick = secrets.SystemRandom().uniform(0, total)
    acc, winner = 0, pool[-1]
    for p in pool:
        acc += p["tickets"]
        if pick < acc:
            winner = p
            break
    doc = {"CAMP_ID": camp_id, "BRANCH_ID": branch_id, "ORD_TYPE": kind, "GIFT": camp["TYPES"][kind]["SPECIAL"],
           "WINNER_CUST": winner["cust"], "WINNER_NAME": winner["name"], "WINNER_TICKETS": winner["tickets"],
           "ENTRANTS": len(pool), "TICKETS": total, "DRAW_DATE": now_utc(), "DRAW_USER": ope_user}
    doc.update(audit_new(ope_user))
    col(DRAW_COL).insert_one(dict(doc))
    return draw_of(camp_id, kind)
