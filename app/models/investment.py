"""Financial > Investment & Break-even (draft, update 27): project investment, yearly budget and the break-even point.

Per branch (factory):
  * AOIMS_INVPROJECT   one record per branch: project name, start date, loan (amount, interest % per year, years),
                       business mix (share of the paddy milled as a service, the rest bought and sold by the mill),
                       share of the service by-products the mill keeps, rice variety used for the per-kg figures.
  * AOIMS_CAPEX        investment items: category, name, amount, purchase date, useful life (years), salvage value,
                       funded by own money or the loan. Depreciation is straight line: (amount - salvage) / life.
                       Land and working capital have no life (not depreciated).
  * AOITS_BUDGET       yearly budget per category: income or expense, fixed or variable cost, amount per year.
                       Actual figures come from the Account ledger (AOITS_ACCOUNT) of the same year.
  * AOILOG_INVEST      every create / update / delete of the three.

Break-even per month, all THB, kg = kg paddy:
  fixed cost / month    = depreciation / 12 + loan interest of year 1 / 12 + fixed expense budget / 12
  revenue / kg          = service share x (milling fee per kg paddy + by-products kept x by-product value per kg)
                          + trading share x value of 1 kg paddy (Pricing > profit check)
  variable cost / kg    = electricity per kg + other cost per kg (profit check) + trading share x paddy buying price
  contribution / kg     = revenue / kg - variable cost / kg
  break-even kg / month = fixed cost / month / contribution per kg       (per day: / working days per month)
  profit / month        = contribution x planned kg per month - fixed cost
  payback (years)       = total investment / (yearly profit + yearly depreciation)
Labour is a fixed cost here (a budget line), so it is not counted again per kg; the electricity budget line is
variable and only used for budget vs actual (the per-kg electricity is used for the break-even).
"""
import math
from datetime import date, datetime

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up, to_number
from .counter import Counter
from . import profit, variety as var
from ..timeutil import local_tz, now_utc, to_local, today_local

PROJECT, CAPEX, BUDGET, LOG = "AOIMS_INVPROJECT", "AOIMS_CAPEX", "AOITS_BUDGET", "AOILOG_INVEST"

# category -> default useful life in years (0 = not depreciated)
CAPEX_CATS = {"LAND": 0, "BUILDING": 20, "MACHINE": 10, "EQUIP": 5, "VEHICLE": 5, "INSTALL": 10, "PREOP": 5,
              "WORKCAP": 0, "OTHER": 5}
FUNDS = ("OWN", "LOAN")
# category -> (IN income / EX expense, F fixed / V variable / None for income)
BUDGET_CATS = {
    "MILLFEE": ("IN", None), "SALES": ("IN", None), "OTHERIN": ("IN", None),
    "PADDY": ("EX", "V"), "ELEC": ("EX", "V"), "PACK": ("EX", "V"), "TRANSPORT": ("EX", "V"), "OTHERVR": ("EX", "V"),
    "LABOUR": ("EX", "F"), "MAINT": ("EX", "F"), "ADMIN": ("EX", "F"), "RENT": ("EX", "F"), "INSUR": ("EX", "F"),
    "OTHERFX": ("EX", "F"),
}
DEFAULT_PROJECT = {"PROJECT_NAME": "", "START_DATE": None, "LOAN_AMOUNT": 0.0, "LOAN_RATE": 0.0, "LOAN_YEARS": 0,
                   "SERVICE_SHARE": 80.0, "BYPROD_KEEP": 100.0, "CALC_VARIETY": var.VARIETIES[0]}
YEARS_SHOWN = 10


def _num(form, name, default=None, low=None, high=None, whole=False):
    try:
        value = to_number(form.get(name), default)
    except ValueError:
        raise ValidationError("errNumber")
    if value is None:
        return None
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValidationError("ivErrRange", field=name)
    return int(value) if whole else value


def _day(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(hour=12, tzinfo=local_tz())
    except ValueError:
        raise ValidationError("errDate")


def _log(table, key, action, data, ope_user):
    clean = {k: v for k, v in (data or {}).items() if k not in ("REGIST_DATE", "LAST_UPDATE", "_id")}
    col(LOG).insert_one({"LOG_ID": "IL-%06d" % Counter.next("INVEST-LOG"), "TABLE_NAME": table, "REC_KEY": key,
                         "LOG_ACTION": action, "DATA": clean, "OPE_USER": ope_user, "LOG_DATE": now_utc()})


# ---------- project settings ----------
def project(branch_id):
    doc = col(PROJECT).find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    out = dict(DEFAULT_PROJECT, BRANCH_ID=branch_id)
    out.update({k: v for k, v in doc.items() if v is not None})
    return out


def save_project(branch_id, form, ope_user):
    values = {
        "PROJECT_NAME": clean_text(form.get("PROJECT_NAME"), 100),
        "START_DATE": _day(form.get("START_DATE")),
        "LOAN_AMOUNT": _num(form, "LOAN_AMOUNT", 0.0, 0),
        "LOAN_RATE": _num(form, "LOAN_RATE", 0.0, 0, 30),
        "LOAN_YEARS": _num(form, "LOAN_YEARS", 0, 0, 40, whole=True),
        "SERVICE_SHARE": _num(form, "SERVICE_SHARE", 100.0, 0, 100),
        "BYPROD_KEEP": _num(form, "BYPROD_KEEP", 100.0, 0, 100),
        "CALC_VARIETY": form.get("CALC_VARIETY") if form.get("CALC_VARIETY") in var.VARIETIES else var.VARIETIES[0],
    }
    if values["LOAN_AMOUNT"] and not values["LOAN_YEARS"]:
        raise ValidationError("ivErrLoanYears")
    exists = col(PROJECT).find_one({"BRANCH_ID": branch_id}, NO_ID)
    if exists:
        col(PROJECT).update_one({"BRANCH_ID": branch_id}, {"$set": dict(values, **audit_update(ope_user))})
    else:
        col(PROJECT).insert_one(dict(values, BRANCH_ID=branch_id, **audit_new(ope_user)))
    _log(PROJECT, branch_id, "U" if exists else "C", values, ope_user)


# ---------- investment items ----------
def capex_items(branch_id):
    items = list(col(CAPEX).find({"BRANCH_ID": branch_id}, NO_ID).sort([("BUY_DATE", 1), ("CAPEX_ID", 1)]))
    for item in items:
        life = item.get("LIFE_YEARS") or 0
        base = max(0.0, (item.get("CAPEX_AMOUNT") or 0) - (item.get("SALVAGE_VALUE") or 0))
        item["dep_year"] = round_half_up(base / life, 2) if life else 0.0
    return items


def save_capex(branch_id, form, ope_user):
    cat = form.get("CAPEX_CAT")
    if cat not in CAPEX_CATS:
        raise ValidationError("ivErrCategory")
    name = clean_text(form.get("CAPEX_NAME"), 100)
    amount = _num(form, "CAPEX_AMOUNT", None, 0)
    if not name or not amount:
        raise ValidationError("ivErrCapexFields")
    life = _num(form, "LIFE_YEARS", CAPEX_CATS[cat], 0, 50, whole=True)
    salvage = _num(form, "SALVAGE_VALUE", 0.0, 0)
    if salvage > amount:
        raise ValidationError("ivErrSalvage")
    values = {"CAPEX_CAT": cat, "CAPEX_NAME": name, "CAPEX_AMOUNT": round_half_up(amount, 2),
              "BUY_DATE": _day(form.get("BUY_DATE")) or now_utc(), "LIFE_YEARS": life,
              "SALVAGE_VALUE": round_half_up(salvage, 2), "FUND_SOURCE": form.get("FUND_SOURCE") if form.get("FUND_SOURCE") in FUNDS else "OWN",
              "REMARK": clean_text(form.get("REMARK"), 200) or None}
    capex_id = (form.get("CAPEX_ID") or "").strip()
    if capex_id:
        if not col(CAPEX).find_one({"BRANCH_ID": branch_id, "CAPEX_ID": capex_id}, NO_ID):
            raise ValidationError("ivErrNotFound")
        col(CAPEX).update_one({"CAPEX_ID": capex_id}, {"$set": dict(values, **audit_update(ope_user))})
        _log(CAPEX, capex_id, "U", values, ope_user)
    else:
        capex_id = "CX-%05d" % Counter.next("CAPEX")
        col(CAPEX).insert_one(dict(values, CAPEX_ID=capex_id, BRANCH_ID=branch_id, **audit_new(ope_user)))
        _log(CAPEX, capex_id, "C", values, ope_user)
    return capex_id


def delete_capex(branch_id, capex_id, ope_user):
    item = col(CAPEX).find_one({"BRANCH_ID": branch_id, "CAPEX_ID": capex_id}, NO_ID)
    if not item:
        raise ValidationError("ivErrNotFound")
    col(CAPEX).delete_one({"CAPEX_ID": capex_id})
    _log(CAPEX, capex_id, "D", item, ope_user)


# ---------- budget ----------
def budget_lines(branch_id, year):
    """Every category of the year, saved or not: [{'BUDGET_CAT', 'BUDGET_TYPE', 'COST_TYPE', 'BUDGET_AMOUNT', ...}]."""
    saved = {b["BUDGET_CAT"]: b for b in col(BUDGET).find({"BRANCH_ID": branch_id, "BUDGET_YEAR": year}, NO_ID)}
    lines = []
    for cat, (kind, cost) in BUDGET_CATS.items():
        line = saved.get(cat) or {"BUDGET_CAT": cat, "BUDGET_TYPE": kind, "COST_TYPE": cost, "BUDGET_AMOUNT": 0.0, "REMARK": None}
        lines.append(dict(line, saved=cat in saved))
    return lines


def save_budget(branch_id, year, form, ope_user):
    """One form with every category: B_<cat> amount per year, C_<cat> F/V, R_<cat> remark."""
    changed = 0
    for cat, (kind, cost) in BUDGET_CATS.items():
        amount = _num(form, "B_" + cat, 0.0, 0)
        cost_type = (form.get("C_" + cat) if form.get("C_" + cat) in ("F", "V") else cost) if kind == "EX" else None
        values = {"BUDGET_TYPE": kind, "COST_TYPE": cost_type, "BUDGET_AMOUNT": round_half_up(amount, 2),
                  "REMARK": clean_text(form.get("R_" + cat), 200) or None}
        key = {"BRANCH_ID": branch_id, "BUDGET_YEAR": year, "BUDGET_CAT": cat}
        old = col(BUDGET).find_one(key, NO_ID)
        if old and all(old.get(k) == v for k, v in values.items()):
            continue
        if old:
            col(BUDGET).update_one(key, {"$set": dict(values, **audit_update(ope_user))})
        elif not amount and not values["REMARK"]:
            continue
        else:
            col(BUDGET).insert_one(dict(key, BUDGET_ID="BG-%s-%d-%s" % (branch_id, year, cat), **values, **audit_new(ope_user)))
        _log(BUDGET, "%s-%d-%s" % (branch_id, year, cat), "U" if old else "C", values, ope_user)
        changed += 1
    return changed


def copy_budget(branch_id, year, ope_user):
    """Copies last year's budget into this year (only categories this year does not have yet)."""
    copied = 0
    for b in col(BUDGET).find({"BRANCH_ID": branch_id, "BUDGET_YEAR": year - 1}, NO_ID):
        key = {"BRANCH_ID": branch_id, "BUDGET_YEAR": year, "BUDGET_CAT": b["BUDGET_CAT"]}
        if col(BUDGET).find_one(key, NO_ID) or not b.get("BUDGET_AMOUNT"):
            continue
        values = {k: b.get(k) for k in ("BUDGET_TYPE", "COST_TYPE", "BUDGET_AMOUNT", "REMARK")}
        col(BUDGET).insert_one(dict(key, BUDGET_ID="BG-%s-%d-%s" % (branch_id, year, b["BUDGET_CAT"]), **values, **audit_new(ope_user)))
        _log(BUDGET, "%s-%d-%s" % (branch_id, year, b["BUDGET_CAT"]), "C", dict(values, FROM_YEAR=year - 1), ope_user)
        copied += 1
    if not copied:
        raise ValidationError("ivErrNothingToCopy")
    return copied


def fill_from_settings(branch_id, year, ope_user):
    """Labour and electricity from Milling Maintenance (only for empty lines)."""
    p = col("AOIMS_SYSPARAM").find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    days = float(p.get("SYSP_WORKDAYS") or 26)
    guesses = {"LABOUR": float(p.get("SYSP_LABORDAY") or 0) * float(p.get("SYSP_WORKERS") or 0) * days * 12,
               "ELEC": float(p.get("SYSP_ELECMONTH") or 0) * 12}
    filled = 0
    for cat, amount in guesses.items():
        key = {"BRANCH_ID": branch_id, "BUDGET_YEAR": year, "BUDGET_CAT": cat}
        old = col(BUDGET).find_one(key, NO_ID)
        if amount <= 0 or (old and old.get("BUDGET_AMOUNT")):
            continue
        kind, cost = BUDGET_CATS[cat]
        values = {"BUDGET_TYPE": kind, "COST_TYPE": cost, "BUDGET_AMOUNT": round_half_up(amount, 2), "REMARK": None}
        if old:
            col(BUDGET).update_one(key, {"$set": dict(values, **audit_update(ope_user))})
        else:
            col(BUDGET).insert_one(dict(key, BUDGET_ID="BG-%s-%d-%s" % (branch_id, year, cat), **values, **audit_new(ope_user)))
        _log(BUDGET, "%s-%d-%s" % (branch_id, year, cat), "U" if old else "C", values, ope_user)
        filled += 1
    if not filled:
        raise ValidationError("ivErrNothingToFill")
    return filled


# ---------- actual figures from the Account ledger ----------
def category_of(entry):
    """Budget category of a ledger entry: the category chosen on a manual entry, otherwise from the order type."""
    if entry.get("ACC_CAT") in BUDGET_CATS:
        return entry["ACC_CAT"]
    kind, ref = entry.get("REF_ORD_TYPE"), entry.get("REF_ORD_ID") or ""
    if kind == "ML":
        return "MILLFEE"
    if kind == "SL":
        return "SALES"
    if kind == "BY":
        return "PADDY"
    if ref.startswith("EL-"):
        return "ELEC"
    return "OTHERIN" if entry.get("ACC_TYPE") == "IN" else "OTHERFX"


def actuals(branch_id, year):
    """{'cat': {cat: amount}, 'month': [{'IN': x, 'EX': y}] * 12}."""
    start = datetime(year, 1, 1, tzinfo=local_tz())
    end = datetime(year + 1, 1, 1, tzinfo=local_tz())
    cats, months = {}, [{"IN": 0.0, "EX": 0.0} for _ in range(12)]
    for e in col("AOITS_ACCOUNT").find({"BRANCH_ID": branch_id, "ACC_DATE": {"$gte": start, "$lt": end}}, NO_ID):
        cat = category_of(e)
        cats[cat] = cats.get(cat, 0.0) + e["ACC_AMOUNT"]
        months[to_local(e["ACC_DATE"]).month - 1]["IN" if e["ACC_TYPE"] == "IN" else "EX"] += e["ACC_AMOUNT"]
    return {"cat": {k: round_half_up(v, 2) for k, v in cats.items()},
            "month": [{k: round_half_up(v, 2) for k, v in m.items()} for m in months]}


def budget_vs_actual(branch_id, year):
    lines = budget_lines(branch_id, year)
    act = actuals(branch_id, year)
    today = today_local()
    months_gone = 12 if year < today.year else (0 if year > today.year else today.month)
    for line in lines:
        line["actual"] = act["cat"].get(line["BUDGET_CAT"], 0.0)
        budget = line["BUDGET_AMOUNT"] or 0
        line["used_pct"] = round_half_up(line["actual"] / budget * 100, 1) if budget else None
        line["to_date"] = round_half_up(budget * months_gone / 12, 2)        # budget spread evenly over the months
    totals = {}
    for kind in ("IN", "EX"):
        mine = [l for l in lines if l["BUDGET_TYPE"] == kind]
        totals[kind] = {"budget": round_half_up(sum(l["BUDGET_AMOUNT"] or 0 for l in mine), 2),
                        "actual": round_half_up(sum(l["actual"] for l in mine), 2)}
    month_rows = []
    for i, m in enumerate(act["month"]):
        month_rows.append({"month": i + 1, "b_in": round_half_up(totals["IN"]["budget"] / 12, 2), "a_in": m["IN"],
                           "b_ex": round_half_up(totals["EX"]["budget"] / 12, 2), "a_ex": m["EX"],
                           "net": round_half_up(m["IN"] - m["EX"], 2), "future": i >= months_gone})
    return {"lines": lines, "totals": totals, "months": month_rows, "months_gone": months_gone}


# ---------- break-even ----------
def loan_figures(proj):
    amount, rate, years = float(proj.get("LOAN_AMOUNT") or 0), float(proj.get("LOAN_RATE") or 0) / 100.0, int(proj.get("LOAN_YEARS") or 0)
    if amount <= 0 or years <= 0:
        return {"amount": 0.0, "payment_year": 0.0, "interest_year1": 0.0, "schedule": []}
    payment = amount / years if rate == 0 else amount * rate / (1 - (1 + rate) ** -years)
    balance, schedule = amount, []
    for y in range(1, years + 1):
        interest = balance * rate
        principal = payment - interest
        balance = max(0.0, balance - principal)
        schedule.append({"year": y, "interest": round_half_up(interest, 2), "principal": round_half_up(principal, 2),
                         "balance": round_half_up(balance, 2)})
    return {"amount": amount, "payment_year": round_half_up(payment, 2), "interest_year1": schedule[0]["interest"],
            "schedule": schedule}


def analyse(branch_id, year=None):
    year = year or today_local().year
    proj = project(branch_id)
    items = capex_items(branch_id)
    total = round_half_up(sum(i["CAPEX_AMOUNT"] for i in items), 2)
    dep_year = round_half_up(sum(i["dep_year"] for i in items), 2)
    loan = loan_figures(proj)
    lines = budget_lines(branch_id, year)
    fixed_budget = sum(l["BUDGET_AMOUNT"] or 0 for l in lines if l["BUDGET_TYPE"] == "EX" and l["COST_TYPE"] == "F")
    fixed_month = (dep_year + loan["interest_year1"] + fixed_budget) / 12.0

    variety = proj["CALC_VARIETY"]
    e = profit.evaluate(branch_id, variety)
    p = col("AOIMS_SYSPARAM").find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    unit_kg = float(p.get("SYSP_MILLFEEUNITKG") or 50) or 50
    fee_kg = float(p.get("SYSP_MILLFEEPERUNIT") or 0) / unit_kg * e["ratios"]["rice"]     # fee per kg paddy
    service = float(proj["SERVICE_SHARE"]) / 100.0
    keep = float(proj["BYPROD_KEEP"]) / 100.0
    buy = e["recommended"] if e["recommended"] is not None else e["current_buy"]
    service_rev = fee_kg + keep * e["by_products"]
    rev_kg = service * service_rev + (1 - service) * e["value"]
    var_kg = e["energy_cost"] + e["other"] + (1 - service) * (buy or 0)
    cm_kg = rev_kg - var_kg

    work = profit.labour(branch_id)
    workdays = float(p.get("SYSP_WORKDAYS") or 26)
    planned_day = float(p.get("SYSP_PLANKGDAY") or 0) or work["kg_day"]
    planned_month = planned_day * workdays
    capacity_month = (work["capacity"] or 0) * workdays
    be_month = fixed_month / cm_kg if cm_kg > 0 else None
    be_day = be_month / workdays if be_month is not None and workdays else None
    profit_month = cm_kg * planned_month - fixed_month
    cash_year = (profit_month * 12) + dep_year
    payback = total / cash_year if total and cash_year > 0 else None
    years = []
    cumulative = -total
    for y in range(1, YEARS_SHOWN + 1):
        interest = loan["schedule"][y - 1]["interest"] if y <= len(loan["schedule"]) else 0.0
        principal = loan["schedule"][y - 1]["principal"] if y <= len(loan["schedule"]) else 0.0
        dep = sum(i["dep_year"] for i in items if (i.get("LIFE_YEARS") or 0) >= y)
        year_profit = cm_kg * planned_month * 12 - fixed_budget - interest - dep
        cash = year_profit + dep
        cumulative += cash
        years.append({"year": y, "profit": round_half_up(year_profit, 0), "cash": round_half_up(cash, 0),
                      "principal": round_half_up(principal, 0), "cumulative": round_half_up(cumulative, 0)})
    x_max = max(planned_month, capacity_month, (be_month or 0) * 1.4, 1000.0)
    x_max = math.ceil(x_max / 10000.0) * 10000 if x_max > 10000 else math.ceil(x_max / 1000.0) * 1000
    return {
        "year": year, "project": proj, "items": items, "total": total, "dep_year": dep_year, "loan": loan,
        "own": round_half_up(sum(i["CAPEX_AMOUNT"] for i in items if i.get("FUND_SOURCE") != "LOAN"), 2),
        "fixed_budget_year": round_half_up(fixed_budget, 2), "fixed_month": round_half_up(fixed_month, 2),
        "fixed_parts": {"dep": round_half_up(dep_year / 12, 2), "interest": round_half_up(loan["interest_year1"] / 12, 2),
                        "budget": round_half_up(fixed_budget / 12, 2)},
        "variety": variety, "eval": e, "fee_kg": round_half_up(fee_kg, 3), "service_rev": round_half_up(service_rev, 3),
        "service": service * 100, "keep": keep * 100, "buy": buy,
        "rev_kg": round_half_up(rev_kg, 3), "var_kg": round_half_up(var_kg, 3), "cm_kg": round_half_up(cm_kg, 3),
        "workdays": workdays, "planned_day": round_half_up(planned_day, 0), "planned_month": round_half_up(planned_month, 0),
        "capacity_day": work["capacity"], "capacity_month": round_half_up(capacity_month, 0), "actual_day": work["actual"],
        "be_month": round_half_up(be_month, 0) if be_month is not None else None,
        "be_day": round_half_up(be_day, 0) if be_day is not None else None,
        "be_revenue": round_half_up(be_month * rev_kg, 0) if be_month is not None else None,
        "be_util": round_half_up(be_day / work["capacity"] * 100, 1) if be_day and work["capacity"] else None,
        "safety": round_half_up((planned_month - be_month) / planned_month * 100, 1) if be_month is not None and planned_month else None,
        "profit_month": round_half_up(profit_month, 0), "profit_year": round_half_up(profit_month * 12, 0),
        "roi": round_half_up(profit_month * 12 / total * 100, 1) if total else None,
        "payback": round_half_up(payback, 1) if payback is not None else None, "years": years,
        "chart": {"x_max": x_max, "fixed": round_half_up(fixed_month, 2), "rev_kg": rev_kg, "var_kg": var_kg,
                  "be": be_month, "planned": planned_month, "capacity": capacity_month or None},
    }


# ---------- sample project (for the review of the draft) ----------
SAMPLE_CAPEX = [("BUILDING", "อาคารโรงสีและโกดัง", 1500000, 20, 0, "LOAN"), ("MACHINE", "ชุดเครื่องสีข้าว (กะเทาะ ขัดขาว ขัดมัน คัดแยก)", 1200000, 10, 100000, "LOAN"),
                ("EQUIP", "เครื่องชั่ง สายพาน เครื่องวัดความชื้น", 250000, 5, 0, "OWN"), ("INSTALL", "ติดตั้งระบบไฟฟ้าและเดินเครื่อง", 300000, 10, 0, "OWN"),
                ("VEHICLE", "รถกระบะส่งข้าว", 600000, 5, 100000, "OWN"), ("WORKCAP", "เงินทุนหมุนเวียนซื้อข้าวเปลือก", 300000, 0, 0, "OWN")]
SAMPLE_BUDGET = {"MAINT": 60000, "ADMIN": 36000, "INSUR": 15000, "PACK": 24000}


def load_sample(branch_id, ope_user):
    """Sample figures to review the screens; only when the branch has no investment items yet."""
    if col(CAPEX).find_one({"BRANCH_ID": branch_id}, NO_ID):
        raise ValidationError("ivErrSampleExists")
    year = today_local().year
    for cat, name, amount, life, salvage, fund in SAMPLE_CAPEX:
        save_capex(branch_id, {"CAPEX_CAT": cat, "CAPEX_NAME": name, "CAPEX_AMOUNT": amount, "LIFE_YEARS": life,
                               "SALVAGE_VALUE": salvage, "FUND_SOURCE": fund, "BUY_DATE": date(year, 1, 5).isoformat(),
                               "REMARK": "ตัวอย่าง (sample)"}, ope_user)
    save_project(branch_id, {"PROJECT_NAME": "โรงสีข้าว (ตัวอย่าง)", "START_DATE": date(year, 1, 5).isoformat(), "LOAN_AMOUNT": 2000000,
                             "LOAN_RATE": 6.5, "LOAN_YEARS": 7, "SERVICE_SHARE": DEFAULT_PROJECT["SERVICE_SHARE"],
                             "BYPROD_KEEP": 100, "CALC_VARIETY": var.VARIETIES[0]}, ope_user)
    try:
        fill_from_settings(branch_id, year, ope_user)
    except ValidationError:
        pass
    form = {}
    for line in budget_lines(branch_id, year):
        form["B_" + line["BUDGET_CAT"]] = line["BUDGET_AMOUNT"] or SAMPLE_BUDGET.get(line["BUDGET_CAT"], 0)
        form["C_" + line["BUDGET_CAT"]] = line["COST_TYPE"]
    a = analyse(branch_id, year)                                 # income budget = the planned volume
    form["B_MILLFEE"] = round_half_up(a["service"] / 100 * a["fee_kg"] * a["planned_month"] * 12, 0)
    form["B_SALES"] = round_half_up((a["rev_kg"] - a["service"] / 100 * a["fee_kg"]) * a["planned_month"] * 12, 0)
    if a["service"] < 100:
        form["B_PADDY"] = round_half_up((1 - a["service"] / 100) * (a["buy"] or 0) * a["planned_month"] * 12, 0)
    save_budget(branch_id, year, form, ope_user)
