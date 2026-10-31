"""TV display of one factory (/tv/<branch>?key=...), for a 50" screen in the waiting area.

The TV does not sign in: each branch has its own display key (AOIMS_SYSPARAM.SYSP_TVKEY), part of the link the
administrator copies on Milling Maintenance. A new key stops the old link at once. The page shows only its own
branch, and customer names are shortened (first name + initial of the surname) because the screen is public.
"""
import secrets
from datetime import timedelta

from .base import col, NO_ID, ValidationError, audit_update, round_half_up
from .branch import Branch
from .customer import Customer
from .milling_order import MillingOrder
from .price import Price
from .rules import status_flow as flow
from . import variety as var
from ..timeutil import now_local, now_utc, to_local

READY = (flow.FINISHED, flow.DELIVERY)
NEW_MINUTES = 5                         # a just-finished order is marked for this long
PRICE_SKUS = ("PADDY_HM", "RICE_HM", "PADDY", "RICE_W5", "PADDY_GL", "RICE_GL", "BRAN", "BROKEN")   # update 61: one broken rice


def issue_key(branch_id, ope_user):
    if not Branch.get(branch_id):
        raise ValidationError("errBranchUnknown")
    key = secrets.token_urlsafe(18)
    values = {"SYSP_TVKEY": key}
    values.update(audit_update(ope_user))
    col(Branch.COL).update_one({"BRANCH_ID": branch_id}, {"$set": values})
    return key


def key_of(branch_id):
    return (Branch.get(branch_id) or {}).get("SYSP_TVKEY")


def allowed(branch_id, key):
    stored = key_of(branch_id)
    return bool(stored and key and secrets.compare_digest(str(stored), str(key)))


def short_name(name):
    """'สมชาย ใจดี' -> 'สมชาย จ.'; one word stays as it is."""
    parts = (name or "").split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    surname = parts[1].lstrip("เแโใไ") or parts[1]          # Thai vowels written before the consonant: ใจดี -> จ
    return "%s %s." % (parts[0], surname[0])


def _first_log(ord_id, status):
    doc = col(MillingOrder.LOG).find_one({"ORD_ID": ord_id, "MILL_STATUS": status}, NO_ID, sort=[("LOG_DATE", 1)])
    return doc["LOG_DATE"] if doc else None


def _hm(value):
    return to_local(value).strftime("%H:%M") if value else ""


def _when(value, today):
    """Time of day, or 'tomorrow' / a date for later days: {'time', 'day': 'today' | 'tomorrow' | 'dd/mm'}."""
    if not value:
        return {"time": "", "day": "today"}
    local = to_local(value)
    if local.date() == today:
        day = "today"
    elif local.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = local.strftime("%d/%m")
    return {"time": local.strftime("%H:%M"), "day": day}


def _th_name(code):
    """SKU name in Thai (the TV is always in Thai, with no signed-in language)."""
    from ..i18n import t_in
    kind, variety = var.INFO.get(code, (None, None))
    if kind is None:
        return code
    return t_in("th", var.KIND_KEYS[kind]) + ((" (%s)" % t_in("th", var.VAR_KEYS[variety])) if variety else "")


def board(branch_id):
    """Everything the TV shows, as plain data (the page asks for it every 15 seconds)."""
    branch = Branch.get(branch_id) or {}
    now = now_utc()
    today = now_local().date()
    orders = MillingOrder.sort_for_list(MillingOrder.for_branch(branch_id, READY + (flow.IN_PROCESS, flow.QUEUE)),
                                        MillingOrder.list_base(branch_id))
    names = {}

    def name_of(code):
        if code not in names:
            names[code] = short_name((Customer.find(code) or {}).get("CUST_NAME", ""))
        return names[code]

    def base(o):
        return {"no": o["QR_ID"], "name": name_of(o["CUST_CODE"]), "kg": round_half_up(o.get("REGIST_PADDY") or 0, 0),
                "variety": o.get("RICE_VARIETY") or var.DEFAULT, "delivery": o["ORDER_TYPE"] == flow.DELIVER}

    ready, milling, queue = [], [], []
    minutes_kg = float(branch.get("SYSP_MILLTIMEKG") or 0)
    for o in orders:
        status = o["MILL_STATUS"]
        if status in READY:
            done = _first_log(o["ORD_ID"], flow.FINISHED) or o.get("LAST_UPDATE")
            row = dict(base(o), done=_hm(done), new=bool(done and (now - done).total_seconds() < NEW_MINUTES * 60),
                       sort=done or now)
            if status == flow.DELIVERY and o.get("DELIVERY_CONFIRM_FLG") and o.get("DELIVERY_ARRIVAL_TIME"):
                row["arrive"] = _hm(o["DELIVERY_ARRIVAL_TIME"])
            ready.append(row)
        elif status == flow.IN_PROCESS:
            start = _first_log(o["ORD_ID"], flow.IN_PROCESS) or o.get("LAST_UPDATE") or now
            minutes = (o.get("REGIST_PADDY") or 0) * minutes_kg
            finish = start + timedelta(minutes=minutes)
            pct = 0 if minutes <= 0 else min(99, max(1, int((now - start).total_seconds() / 60 / minutes * 100)))
            milling.append(dict(base(o), finish=_hm(finish), pct=pct))
        else:
            queue.append(dict(base(o), receive=_when(o.get("EXPCT_RECEIVE_TIME"), today)))
    ready.sort(key=lambda r: r["sort"], reverse=True)            # the newest finished first
    for r in ready:
        r.pop("sort")
    prices = []
    for code in PRICE_SKUS:
        p = Price.get(branch_id, code) or {}
        buy, sell = float(p.get("PRICE_BUY") or 0), float(p.get("PRICE_SELL") or 0)
        if buy or sell:
            prices.append({"code": code, "name": _th_name(code), "buy": buy, "sell": sell})
    from ..i18n import t_in
    key = "branch" + branch_id.capitalize()
    th_name, en_name = t_in("th", key), t_in("en", key)
    local_now = now_local()
    # update 36: the main motor on the header (the one on shift first), like the SCADA
    motor = None
    try:
        from .automation import Machine, SELECTOR
        machines = Machine.for_branch(branch_id, active_only=True)
        motors = [m for m in machines if m.get("MACHINE_TYPE") == "MAIN_MOTOR"]
        chosen = {m.get("SELECT_TO") for m in machines if m.get("MACHINE_TYPE") == SELECTOR}
        # update 43: the motor that runs, else the one the mill selector points at, else the first
        motors.sort(key=lambda m: (0 if Machine.is_running(m) else 1, 0 if m["MACHINE_ID"] in chosen else 1))
        if motors:
            live = Machine.live(motors[0], minutes=0)
            motor = {"status": live["status"], "running": bool(live["running"]), "hz": live["out_hz"], "fault": live["fault"]}
    except Exception:                                    # noqa: BLE001 - the TV never fails because of the motor
        motor = None
    return {"motor": motor, "branch": branch_id, "branch_name": th_name if th_name != key else (branch.get("BRANCH_NAME") or branch_id),
            "branch_name_en": en_name if en_name != key else (branch.get("BRANCH_NAME") or branch_id),
            "now_ms": int(now.timestamp() * 1000), "tz_min": int(local_now.utcoffset().total_seconds() // 60), "ready": ready, "milling": milling,
            "queue": queue, "prices": prices, "updated": now_local().strftime("%H:%M:%S")}


# ---------- update 83: price billboard (/tv/<branch>/prices?key=...) ----------
BOARD_DAYS = 30


def _price_steps(branch_id, code, since):
    """[(utc datetime, buy, sell)] of the price changes from AOILOG_PRICE since `since`, with the value before it first."""
    log = Price.LOG
    q = {"BRANCH_ID": branch_id, "MAT_CODE": code}
    before = col(log).find_one(dict(q, LOG_DATE={"$lt": since}), NO_ID, sort=[("LOG_DATE", -1)])
    rows = list(col(log).find(dict(q, LOG_DATE={"$gte": since}), NO_ID).sort([("LOG_DATE", 1)]))
    out = []
    for r in ([before] if before else []) + rows:
        out.append((r["LOG_DATE"], float(r.get("PRICE_BUY") or 0), float(r.get("PRICE_SELL") or 0)))
    return out


def _side(steps, idx, current, today, changed_at=None):
    """Daily series (30 days) and the last change of one side (1 = buy, 2 = sell)."""
    series, j, value = [], 0, None
    days = [today - timedelta(days=BOARD_DAYS - 1 - i) for i in range(BOARD_DAYS)]
    for d in days:
        while j < len(steps) and to_local(steps[j][0]).date() <= d:
            value = steps[j][idx]
            j += 1
        series.append(value)
    series[-1] = current                                      # the price now
    first = next((v for v in series if v), current)
    series = [v if v else first for v in series]
    prev, when = None, None                                   # the last different price
    vals = [(s[0], s[idx]) for s in steps if s[idx]]
    for k in range(len(vals) - 1, -1, -1):
        if abs(vals[k][1] - current) > 1e-9:
            prev, when = vals[k][1], (vals[k + 1][0] if k + 1 < len(vals) else None)
            break
    change = round_half_up(current - prev, 2) if prev else 0.0
    return {"series": [round_half_up(v or 0, 2) for v in series], "prev": prev, "change": change,
            "pct": round_half_up(change / prev * 100, 1) if prev else 0.0,
            "since": to_local(when or changed_at).strftime("%d/%m") if (when or changed_at) else None}


def billboard(branch_id):
    """Everything the price billboard shows: per SKU the buying and selling price, the last change and 30 days."""
    from ..i18n import t_in
    from .price import Promotion
    branch = Branch.get(branch_id) or {}
    now = now_utc()
    today = now_local().date()
    since = now - timedelta(days=BOARD_DAYS + 1)
    items = []
    for code in PRICE_SKUS:
        p = Price.get(branch_id, code) or {}
        buy, sell = float(p.get("PRICE_BUY") or 0), float(p.get("PRICE_SELL") or 0)
        if not (buy or sell):
            continue
        kind, variety = var.INFO.get(code, (None, None))
        steps = _price_steps(branch_id, code, since)
        en = t_in("en", var.KIND_KEYS[kind]) + ((" (%s)" % t_in("en", var.VAR_KEYS[variety])) if variety else "") if kind else code
        items.append({"code": code, "name": _th_name(code), "name_en": en, "kind": kind, "variety": variety,
                      "buy": buy, "sell": sell, "b": _side(steps, 1, buy, today, p.get("LAST_UPDATE")), "s": _side(steps, 2, sell, today, p.get("LAST_UPDATE")),
                      "changed": to_local(p["LAST_UPDATE"]).strftime("%d/%m %H:%M") if p.get("LAST_UPDATE") else None})
    key = "branch" + branch_id.capitalize()
    th_name, en_name = t_in("th", key), t_in("en", key)
    local_now = now_local()
    try:
        discount = Promotion.milling_discount_pct(branch_id, today)
    except Exception:                                          # noqa: BLE001
        discount = 0
    return {"branch": branch_id, "branch_name": th_name if th_name != key else (branch.get("BRANCH_NAME") or branch_id),
            "branch_name_en": en_name if en_name != key else (branch.get("BRANCH_NAME") or branch_id),
            "now_ms": int(now.timestamp() * 1000), "tz_min": int(local_now.utcoffset().total_seconds() // 60),
            "items": items, "discount": discount, "updated": local_now.strftime("%H:%M:%S")}
