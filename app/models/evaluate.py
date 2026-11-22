"""Update 135: two rules from the AI Expo Thailand talk "7 คาถากันตาย" (Tim):

Rule 3 - managers buy baht, not %: BASELINE in baht.
  AOIMS_KPI: per branch, a few measures with the value BEFORE AOI (typed once, with its date) and the value NOW
  (worked out by AOI where it can, typed otherwise) and how many baht one unit is worth. The page shows the gain per
  month in baht. Measures (KPIS): paddy milled per day, net cash per month (ledger), staff hours on paperwork per day,
  phone calls per day, money lost to mistakes per month.

Rule 7 - without an exam every model change is a guess: EXAMS for the two "AI" parts.
  * Grader: staff grade a sample by hand (AOITS_GRADECHECK, kept with the grader's result of the same sample). AOI
    scores the grader against hand grading per measure (mean difference, share within +-TOL points). Checks marked
    GOLDEN form the fixed exam set: the grader program re-grades the kept scans of those samples with its current code
    (scripts/aoi_grader.py --exam) and sends the answers (POST /api/v1/graders/<id>/exam); AOI keeps each exam with
    the program version (AOITS_AIEXAM), so a new version is compared on the same samples before it is trusted.
  * Price signals: a back-test on past days with known prices - the buy / sell signal of every 7th day of the last
    12 months worked out with only the prices known that day, scored against the price 30 days later, next to the
    "always" baseline (how often the price simply went up / down). Kept with the signal settings (SMA short / long).
"""
from datetime import date, timedelta

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up, to_number
from ..timeutil import now_utc, today_local

KPI, CHECK, EXAM = "AOIMS_KPI", "AOITS_GRADECHECK", "AOITS_AIEXAM"
# key: (unit key, per day (True) or per month, higher is better, auto, default THB per unit)
KPIS = {
    "MILL_KG_DAY": ("kpiUnitKgDay", True, True, True, 0.5),
    "NET_THB_MONTH": ("kpiUnitThbMonth", False, True, True, 1.0),
    "STAFF_H_DAY": ("kpiUnitHourDay", True, False, False, 44.6),       # minimum wage 357 / 8 hours
    "CALLS_DAY": ("kpiUnitCallDay", True, False, False, 3.0),
    "ERROR_THB_MONTH": ("kpiUnitThbMonth", False, False, False, 1.0),
}
WORK_DAYS = 26                                                        # working days in a month (default)
MEASURES = (("WHOLE_PCT", "HAND_WHOLE"), ("BROKEN_PCT", "HAND_BROKEN"), ("HEAD_RICE_PCT", "HAND_HEAD"),
            ("CHALKY_PCT", "HAND_CHALKY"), ("MOISTURE_PCT", "HAND_MOISTURE"))
TOL = 2.0                                                             # points: "within ~1-2 %" of hand grading
READY_N, READY_MAE = 20, 1.5                                          # enough checks and close enough to trust
HORIZON, STEP, BACK_DAYS = 30, 7, 365


# ---------- Rule 3: baseline in baht ----------
def kpi_rows(branch_id, work_days=None):
    docs = {d["KPI_KEY"]: d for d in col(KPI).find({"BRANCH_ID": branch_id}, NO_ID)}
    cfg = docs.get("_CFG") or {}
    wd = work_days or cfg.get("WORK_DAYS") or WORK_DAYS
    auto = auto_values(branch_id, wd)
    rows, total = [], 0.0
    for key, (unit, per_day, higher, is_auto, thb) in KPIS.items():
        d = docs.get(key) or {}
        base = d.get("BASE_VALUE")
        now = auto.get(key) if is_auto else d.get("NOW_VALUE")
        rate = d.get("THB_PER_UNIT") if d.get("THB_PER_UNIT") is not None else thb
        gain = None
        if base is not None and now is not None:
            gain = round_half_up((now - base) * (1 if higher else -1) * rate * (wd if per_day else 1), 2)
            total += gain
        rows.append({"key": key, "unit": unit, "per_day": per_day, "higher": higher, "auto": is_auto, "base": base,
                     "base_date": d.get("BASE_DATE"), "note": d.get("BASE_NOTE"), "now": now, "rate": rate, "gain": gain,
                     "change_pct": round_half_up((now - base) / base * 100, 1) if base and now is not None else None})
    return {"rows": rows, "total": round_half_up(total, 2), "work_days": wd,
            "missing": sum(1 for r in rows if r["base"] is None)}


def auto_values(branch_id, work_days):
    """What AOI can measure itself over the last 30 days."""
    from .automation import paddy_milled
    end = now_utc()
    start = end - timedelta(days=30)
    kg = paddy_milled(branch_id, start, end)
    net = 0.0
    for e in col("AOITS_ACCOUNT").find({"BRANCH_ID": branch_id, "ACC_DATE": {"$gte": start}}, {"_id": 0, "ACC_TYPE": 1, "ACC_AMOUNT": 1}):
        net += e["ACC_AMOUNT"] if e["ACC_TYPE"] == "IN" else -e["ACC_AMOUNT"]
    return {"MILL_KG_DAY": round_half_up(kg / work_days, 1), "NET_THB_MONTH": round_half_up(net, 2)}


def save_kpi(branch_id, form, ope_user):
    def num(name, lo=0.0, hi=1e9, required=False):
        text = (form.get(name) or "").strip()
        if not text:
            if required:
                raise ValidationError("kpiErrNumber")
            return None
        try:
            v = to_number(text)
        except ValueError:
            raise ValidationError("kpiErrNumber")
        if v is None or not lo <= v <= hi:
            raise ValidationError("kpiErrNumber")
        return round_half_up(v, 2)
    wd = num("WORK_DAYS", 1, 31, required=True)
    col(KPI).update_one({"BRANCH_ID": branch_id, "KPI_KEY": "_CFG"}, {"$set": dict({"WORK_DAYS": int(wd)}, **audit_update(ope_user))}, upsert=True)
    for key, (_u, _d, _h, is_auto, _t) in KPIS.items():
        base = num("BASE_" + key, -1e9)
        values = {"BASE_VALUE": base, "BASE_NOTE": clean_text(form.get("NOTE_" + key), 100) or None,
                  "THB_PER_UNIT": num("RATE_" + key, 0, 1e6)}
        if not is_auto:
            values["NOW_VALUE"] = num("NOW_" + key, -1e9)
        old = col(KPI).find_one({"BRANCH_ID": branch_id, "KPI_KEY": key}, NO_ID) or {}
        values["BASE_DATE"] = (old.get("BASE_DATE") if old.get("BASE_VALUE") == base else now_utc()) if base is not None else None
        values.update(audit_update(ope_user))
        col(KPI).update_one({"BRANCH_ID": branch_id, "KPI_KEY": key}, {"$set": values}, upsert=True)


# ---------- Rule 7: the grader against hand grading ----------
def save_check(branch_id, form, ope_user):
    from . import grade
    g = grade.get(form.get("GRADE_ID"), branch_id)
    if not g:
        raise ValidationError("grErrUnknown")
    values, any_value = {}, False
    for _field, hand in MEASURES:
        text = (form.get(hand) or "").strip()
        if text:
            try:
                v = to_number(text)
            except ValueError:
                raise ValidationError("exErrNumber")
            if v is None or not 0 <= v <= 100:
                raise ValidationError("exErrNumber")
            values[hand] = round_half_up(v, 1)
            any_value = True
        else:
            values[hand] = None
    if not any_value:
        raise ValidationError("exErrEmpty")
    values.update(GOLDEN_FLG=form.get("GOLDEN_FLG") in ("1", "on", "true"), NOTE=clean_text(form.get("NOTE"), 100) or None,
                  GRADER_ID=g["GRADER_ID"], SAMPLE_NO=g.get("SAMPLE_NO"), VERSION=g.get("VERSION"), BRANCH_ID=branch_id)
    if col(CHECK).find_one({"GRADE_ID": g["GRADE_ID"]}):
        col(CHECK).update_one({"GRADE_ID": g["GRADE_ID"]}, {"$set": dict(values, **audit_update(ope_user))})
    else:
        col(CHECK).insert_one(dict(values, GRADE_ID=g["GRADE_ID"], **audit_new(ope_user)))
    return g["GRADE_ID"]


def delete_check(branch_id, grade_id):
    col(CHECK).delete_one({"BRANCH_ID": branch_id, "GRADE_ID": (grade_id or "").upper()})


def checks(branch_id, limit=60):
    from . import grade
    out = []
    for c in col(CHECK).find({"BRANCH_ID": branch_id}, NO_ID).sort([("REGIST_DATE", -1)]).limit(limit):
        out.append(dict(c, g=grade.get(c["GRADE_ID"]) or {}))
    return out


def _score(pairs):
    """[(machine, hand)] -> {n, mae, bias, within}"""
    pairs = [(m, h) for m, h in pairs if m is not None and h is not None]
    if not pairs:
        return {"n": 0, "mae": None, "bias": None, "within": None}
    diffs = [m - h for m, h in pairs]
    return {"n": len(pairs), "mae": round_half_up(sum(abs(d) for d in diffs) / len(diffs), 2),
            "bias": round_half_up(sum(diffs) / len(diffs), 2),
            "within": round_half_up(100.0 * sum(1 for d in diffs if abs(d) <= TOL) / len(diffs), 0)}


def grader_score(branch_id, days=60):
    """Live score: the grader's own results against the hand checks of the last `days` days, per measure."""
    since = now_utc() - timedelta(days=days)
    rows = checks(branch_id, 1000)
    rows = [r for r in rows if r["REGIST_DATE"] >= since]
    out = {field: _score([(r["g"].get(field), r.get(hand)) for r in rows]) for field, hand in MEASURES}
    key = [out[f] for f in ("WHOLE_PCT", "BROKEN_PCT") if out[f]["n"]]
    ready = bool(key) and all(s["n"] >= READY_N and s["mae"] <= READY_MAE for s in key)
    return {"measures": out, "n": len(rows), "ready": ready, "days": days}


def golden(grader_id):
    """The fixed exam set of a grader: [{grade_id, sample, hand values}]."""
    out = []
    for c in col(CHECK).find({"GRADER_ID": grader_id, "GOLDEN_FLG": True}, NO_ID).sort([("REGIST_DATE", 1)]):
        out.append({"grade_id": c["GRADE_ID"], "sample": c.get("SAMPLE_NO"),
                    "hand": {hand.replace("HAND_", "").lower(): c.get(hand) for _f, hand in MEASURES if c.get(hand) is not None}})
    return out


def save_grader_exam(dev, body):
    """The grader re-graded its golden samples with its current program: {"version", "results": [{grade_id, whole, ...}]}."""
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise ValidationError("exErrBody")
    version = clean_text(body.get("version"), 20)
    if not version:
        raise ValidationError("exErrVersion")
    hand = {c["grade_id"]: c["hand"] for c in golden(dev["GRADER_ID"])}
    names = {"WHOLE_PCT": "whole", "BROKEN_PCT": "broken", "HEAD_RICE_PCT": "head", "CHALKY_PCT": "chalky", "MOISTURE_PCT": "moisture"}
    pairs = {f: [] for f, _h in MEASURES}
    used = 0
    for r in body["results"][:500]:
        if not isinstance(r, dict) or r.get("grade_id") not in hand:
            continue
        used += 1
        for field, _h in MEASURES:
            key = names[field]
            try:
                machine = to_number(r.get(key if key != "head" else "head_rice"))
            except (ValueError, TypeError):
                machine = None
            pairs[field].append((machine, hand[r["grade_id"]].get(key)))
    if not used:
        raise ValidationError("exErrNoGolden")
    doc = {"EXAM_ID": "EX%s" % now_utc().strftime("%Y%m%d%H%M%S%f")[:18], "KIND": "GRADER", "BRANCH_ID": dev["BRANCH_ID"],
           "SUBJECT": dev["GRADER_ID"], "VERSION": version, "N": used,
           "SCORES": {f: _score(p) for f, p in pairs.items()}, "EXAM_DATE": now_utc()}
    col(EXAM).insert_one(dict(doc))
    return doc


# ---------- Rule 7: the price signals on past days ----------
def price_exam(branch_id, code, ope_user, cfg=None, end=None):
    """Back-test of the buy / sell signal of SKU `code`. Saved and returned."""
    from . import price_signal as ps, export_price
    cfg = cfg or ps.settings()
    end = end or today_local()
    warm = max(cfg["SMA_LONG"], ps.RANGE_DAYS) + 5
    start = end - timedelta(days=BACK_DAYS + HORIZON + warm)
    keys = [i["key"] for i in export_price.items()]
    found = ps.sources(branch_id, code, start, cfg, keys)
    base = ps.base_of(code, found, cfg, export_suspect=bool(ps.export_check(code, found, cfg, keys)))
    days = ps.daily(found.get(base) or [], end) if base else []
    obs_all = sorted((date.fromisoformat(d), v) for d, v in {d: v for d, v in (found.get(base) or [])}.items())
    tally = {"buy": [0, 0], "sell": [0, 0], "hold": [0, 0]}
    ups = tested = 0
    first = max(warm, 1)
    for i in range(first, len(days) - HORIZON, STEP):
        day, price = days[i]
        later = days[i + HORIZON][1]
        sig = ps.signal(days[:i + 1], cfg, [o for o in obs_all if o[0] <= day])
        if not sig or not price:
            continue
        tested += 1
        up = later > price
        ups += up
        side = "buy" if sig["score"] >= 1 else "sell" if sig["score"] <= -1 else "hold"
        tally[side][0] += 1
        tally[side][1] += up if side == "buy" else (later < price) if side == "sell" else 0
    rate = lambda k: round_half_up(100.0 * tally[k][1] / tally[k][0], 0) if tally[k][0] else None
    doc = {"EXAM_ID": "EX%s" % now_utc().strftime("%Y%m%d%H%M%S%f")[:18], "KIND": "PRICE", "BRANCH_ID": branch_id, "SUBJECT": code,
           "VERSION": "SMA %s/%s" % (cfg["SMA_SHORT"], cfg["SMA_LONG"]), "BASE": base, "N": tested,
           "SCORES": {"buy_n": tally["buy"][0], "buy_hit": rate("buy"), "sell_n": tally["sell"][0], "sell_hit": rate("sell"),
                      "hold_n": tally["hold"][0], "up_pct": round_half_up(100.0 * ups / tested, 0) if tested else None},
           "EXAM_DATE": now_utc(), "OPE_USER": ope_user}
    if not tested:
        raise ValidationError("exErrNoPrices", code=code)
    col(EXAM).insert_one(dict(doc))
    return doc


def exams(branch_id, kind, limit=20):
    return list(col(EXAM).find({"BRANCH_ID": branch_id, "KIND": kind}, NO_ID).sort([("EXAM_DATE", -1)]).limit(limit))
