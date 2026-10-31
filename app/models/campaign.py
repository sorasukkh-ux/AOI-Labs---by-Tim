"""Christmas / New Year campaign (update 93, Tim): AOIMS_CAMPAIGN, AOITS_CAMPGIFT, AOITS_CAMPDRAW.

One campaign per branch and period, with three order types - Milling (ML), Buying (BY: the mill buys from the customer)
and Selling (SL: the mill sells to the customer). Points ADD UP per customer and order type over the campaign:
points = floor(total service kg / KG_PER_POINT) (default 10 kg = 1 point).
  * Milling: the milling orders registered in the period that reached Finished (F, D or C), weighed paddy
    (ACT_PADDY) or registered paddy.
  * Buying / Selling: the orders registered in the period that are Closed (C), ORD_QTY.
Update 95 (Tim): the three levels are RANKS, one customer each - the customer with the most points wins the level 1
gift, the next one level 2, then level 3 (ties: more exact kg, then who got there first). A customer needs at least
MIN_POINTS to be ranked. The ranking is final after the last day; then staff record the hand-over (AOITS_CAMPGIFT).
A special gift per type is drawn (AOITS_CAMPDRAW; update 99: the wheel can be spun again, every spin kept with ROUND, the latest shown) after the last day among ALL qualified customers (update 98: the
rank winners too), the chance in proportion to their points; the winner is picked on the server
(secrets) before the lucky wheel spins on screen.
"""
import secrets
from datetime import datetime, timedelta

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up
from .customer import Customer
from ..timeutil import local_tz, now_utc, to_local, today_local

COL, GIFT_COL, DRAW_COL = "AOIMS_CAMPAIGN", "AOITS_CAMPGIFT", "AOITS_CAMPDRAW"
TYPES = ("ML", "BY", "SL")
LEVELS = 3
SPECIAL = 0                                             # update 100: AOITS_CAMPGIFT.LEVEL of the special gift
MILL_DONE = ("F", "D", "C")
DEFAULT_MIN = {"ML": 10, "BY": 10, "SL": 5}


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


THEMES = ("xmas", "songkran")                           # update 101: Christmas / New Year or Songkran
THEME_ICON = {"xmas": "\U0001F384", "songkran": "\U0001F4A6"}


def theme_of(camp):
    return (camp or {}).get("THEME") if (camp or {}).get("THEME") in THEMES else "xmas"


def blank(branch_id, theme="xmas"):
    today = today_local()
    year = today.year
    if theme == "songkran":                              # 1-20 April, this year or next
        y = year if today < datetime(year, 4, 20).date() else year + 1
        name, start, end = "Songkran %d" % y, datetime(y, 4, 1, tzinfo=local_tz()), datetime(y, 4, 20, tzinfo=local_tz())
    else:
        name, start, end = "Christmas & New Year %d" % (year + 1), datetime(year, 12, 1, tzinfo=local_tz()), datetime(year + 1, 1, 15, tzinfo=local_tz())
    return {"CAMP_ID": None, "BRANCH_ID": branch_id, "CAMP_NAME": name, "THEME": theme if theme in THEMES else "xmas",
            "DATE_FROM": start, "DATE_TO": end,
            "STATUS": "I", "KG_PER_POINT": 10,
            "TYPES": {t: {"LEVELS": [{"GIFT": ""} for _ in range(LEVELS)], "MIN_POINTS": DEFAULT_MIN[t], "SPECIAL": ""} for t in TYPES}}


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
        try:
            min_points = int(float(form.get("MIN_%s" % t) or 1))
        except ValueError:
            raise ValidationError("errNumber")
        if min_points < 1:
            raise ValidationError("cpErrMin")
        levels = [{"GIFT": clean_text(form.get("GIFT_%s_%d" % (t, n)), 80) or ""} for n in range(1, LEVELS + 1)]
        types[t] = {"LEVELS": levels, "MIN_POINTS": min_points, "SPECIAL": clean_text(form.get("SPECIAL_%s" % t), 80) or ""}
    values = {"CAMP_NAME": name, "DATE_FROM": start, "DATE_TO": end, "KG_PER_POINT": kg, "TYPES": types,
              "STATUS": "A" if form.get("STATUS") == "A" else "I",
              "THEME": form.get("THEME") if form.get("THEME") in THEMES else "xmas"}
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
        found = [(o, o.get("ACT_PADDY") or o.get("REGIST_PADDY") or 0)
                 for o in col("AOITS_ORDMILLING").find(dict(q, MILL_STATUS={"$in": list(MILL_DONE)}), NO_ID)]
    else:
        found = [(o, o.get("ORD_QTY") or 0) for o in col("AOITS_ORDBUYSELL").find(dict(q, ORD_TYPE=kind, ORD_STATUS="C"), NO_ID)]
    for o, kg in found:
        c = out.setdefault(o["CUST_CODE"], {"kg": 0.0, "orders": 0, "last": o["REGIST_DATE"]})
        c["kg"] += float(kg); c["orders"] += 1
        c["last"] = max(c["last"], o["REGIST_DATE"])            # when the total was reached (ties: earlier wins)
    return out


def min_points(camp, kind):
    return int(camp["TYPES"][kind].get("MIN_POINTS") or 1)


def final(camp, today=None):
    """The ranking counts until the last day; gifts and the draw only after it."""
    return (today or today_local()) > to_local(camp["DATE_TO"]).date()


def standings(camp, kind):
    """[{rank, cust, name, kg, orders, points, qualified, prize: {level, gift, given, given_date} | None}] best first.
    Level n = rank n (one customer each) among the customers with at least MIN_POINTS."""
    per = int(camp.get("KG_PER_POINT") or 10)
    levels = camp["TYPES"][kind]["LEVELS"]
    need = min_points(camp, kind)
    given = {(g["CUST_CODE"], g["LEVEL"]): g for g in col(GIFT_COL).find({"CAMP_ID": camp["CAMP_ID"], "ORD_TYPE": kind}, NO_ID)}
    rows = []
    for code, v in _kg_by_customer(camp, kind).items():
        rows.append({"cust": code, "name": (Customer.find(code) or {}).get("CUST_NAME", code), "kg": round_half_up(v["kg"], 1),
                     "orders": v["orders"], "points": int(v["kg"] // per), "last": v["last"]})
    rows.sort(key=lambda r: (-r["points"], -r["kg"], r["last"], r["name"]))
    rank = 0
    for r in rows:
        r["qualified"] = r["points"] >= need
        r["prize"] = None
        if r["qualified"]:
            rank += 1
            r["rank"] = rank
            if rank <= LEVELS and levels[rank - 1].get("GIFT"):
                g = given.get((r["cust"], rank))
                r["prize"] = {"level": rank, "gift": levels[rank - 1]["GIFT"], "given": bool(g),
                              "given_date": to_local(g["GIVEN_DATE"]).strftime("%d/%m/%Y") if g else None}
        else:
            r["rank"] = None
            r["to_min"] = need - r["points"]
        r.pop("last")
    return rows


def customer_points(camp, cust_code):
    """For the receipt / LINE: {type: {points, rank, kg}} of one customer."""
    out = {}
    for t in TYPES:
        row = next((r for r in standings(camp, t) if r["cust"] == cust_code), None)
        if row:
            out[t] = {k: row[k] for k in ("points", "rank", "kg")}
    return out


def running(branch_id):
    return next((c for c in for_branch(branch_id) if state(c) == "running"), None)


def give(camp_id, branch_id, kind, cust_code, level, ope_user):
    """Staff hand the level's gift over (once)."""
    camp = get(camp_id)
    if not camp or camp["BRANCH_ID"] != branch_id or kind not in TYPES:
        raise ValidationError("cpErrNotFound")
    if not final(camp):
        raise ValidationError("cpErrNotFinal", day=to_local(camp["DATE_TO"]).strftime("%d/%m/%Y"))
    if level == SPECIAL:                                 # update 100: the special gift of the latest spin
        d = draw_of(camp_id, kind)
        if not d or d["WINNER_CUST"] != cust_code:
            raise ValidationError("cpErrNotReached")
        gift, points = d["GIFT"], d["WINNER_TICKETS"]
    else:
        row = next((r for r in standings(camp, kind) if r["cust"] == cust_code), None)
        if not row or not row["prize"] or row["prize"]["level"] != level:
            raise ValidationError("cpErrNotReached")
        gift, points = row["prize"]["gift"], row["points"]
    if col(GIFT_COL).find_one({"CAMP_ID": camp_id, "ORD_TYPE": kind, "LEVEL": level}):          # one winner per level
        raise ValidationError("cpErrGiven")
    doc = {"CAMP_ID": camp_id, "BRANCH_ID": branch_id, "ORD_TYPE": kind, "CUST_CODE": cust_code, "LEVEL": level,
           "GIFT": gift, "POINTS": points, "GIVEN_DATE": now_utc(), "GIVEN_USER": ope_user}
    doc.update(audit_new(ope_user))
    col(GIFT_COL).insert_one(doc)
    return doc


def ungive(camp_id, branch_id, kind, level, ope_user):
    """Update 100: a hand-over recorded by mistake is taken back (the gift is pending again)."""
    camp = get(camp_id)
    if not camp or camp["BRANCH_ID"] != branch_id:
        raise ValidationError("cpErrNotFound")
    if not col(GIFT_COL).find_one({"CAMP_ID": camp_id, "ORD_TYPE": kind, "LEVEL": level}):
        raise ValidationError("cpErrNotGiven")
    col(GIFT_COL).delete_many({"CAMP_ID": camp_id, "ORD_TYPE": kind, "LEVEL": level})


def winners(camp):
    """Update 100 (Tim): everyone who earns a gift - ranks 1-3 and the special gift (latest spin) of every type -
    with the hand-over status: given / pending / waiting (the campaign has not ended: rank so far)."""
    out, is_final = [], final(camp)
    given = {(g["ORD_TYPE"], g["LEVEL"]): g for g in col(GIFT_COL).find({"CAMP_ID": camp["CAMP_ID"]}, NO_ID)}
    for kind in TYPES:
        for r in standings(camp, kind):
            if r["prize"]:
                out.append({"kind": kind, "level": r["prize"]["level"], "cust": r["cust"], "name": r["name"], "gift": r["prize"]["gift"],
                            "points": r["points"]})
        d = draw_of(camp["CAMP_ID"], kind)
        if d:
            out.append({"kind": kind, "level": SPECIAL, "cust": d["WINNER_CUST"], "name": d["WINNER_NAME"], "gift": d["GIFT"],
                        "points": d["WINNER_TICKETS"]})
    for w in out:
        g = given.get((w["kind"], w["level"]))
        if g and g["CUST_CODE"] == w["cust"]:
            w.update(status="given", given_date=to_local(g["GIVEN_DATE"]).strftime("%d/%m/%Y %H:%M"), given_user=g.get("GIVEN_USER"))
        else:
            w.update(status="pending" if is_final else "waiting", given_date=None, given_user=None)
        c = Customer.find(w["cust"]) or {}
        w["line"] = bool((c.get("LINE_USER_ID") or "").strip())
        from .phone import of as phone_of
        w["mobile"] = phone_of(c) if c else ""
    return out


# ---------- the special gift: one lucky draw per type ----------
def draws_of(camp_id, kind):
    """Update 99 (Tim): the wheel can be spun many times - every spin is kept, newest first."""
    return sorted(col(DRAW_COL).find({"CAMP_ID": camp_id, "ORD_TYPE": kind}, NO_ID), key=lambda d: d.get("ROUND") or 1, reverse=True)


def draw_of(camp_id, kind):
    """The latest spin (the winner shown), or None."""
    found = draws_of(camp_id, kind)
    return found[0] if found else None


def entrants(camp, kind):
    """Every qualified customer (at least MIN_POINTS), the rank 1-3 winners too (update 98, Tim), tickets = points."""
    return [{"cust": r["cust"], "name": r["name"], "tickets": r["points"]} for r in standings(camp, kind) if r["qualified"]]


def draw(camp_id, branch_id, kind, ope_user):
    """Pick the winner once (secrets.SystemRandom, weighted by points) and keep it; the wheel then shows it."""
    camp = get(camp_id)
    if not camp or camp["BRANCH_ID"] != branch_id or kind not in TYPES:
        raise ValidationError("cpErrNotFound")
    if not camp["TYPES"][kind].get("SPECIAL"):
        raise ValidationError("cpErrNoSpecial")
    last = draw_of(camp_id, kind)
    if not final(camp):
        raise ValidationError("cpErrNotFinal", day=to_local(camp["DATE_TO"]).strftime("%d/%m/%Y"))
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
           "ENTRANTS": len(pool), "TICKETS": total, "DRAW_DATE": now_utc(), "DRAW_USER": ope_user,
           "ROUND": ((last or {}).get("ROUND") or (1 if last else 0)) + 1}
    doc.update(audit_new(ope_user))
    col(DRAW_COL).insert_one(dict(doc))
    return doc
