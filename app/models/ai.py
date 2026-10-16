"""Automation > AI: forecasts, price advice and anomaly alerts worked out from the system's own data
(statistics and rules, explained on screen), and "Ask the system", which sends a question with a summary of
the branch's figures to an AI service when an API key is configured (AOI_AI_API_KEY in .env).

The summary sent to the AI service holds totals only: no customer names, phone numbers or addresses.
"""
import json
import statistics
import urllib.error
import urllib.request
from datetime import timedelta

from flask import current_app

from .automation import FAULT, RUNNING, Machine, Meter, day_start_utc, paddy_milled
from .base import col, NO_ID, ValidationError, clean_text, round_half_up
from .inventory import Inventory
from .price import Price
from .mill_yield import MillYield
from . import profit
from ..timeutil import now_local, now_utc, to_local

HISTORY_WEEKS = 8
ASK_LIMIT_PER_HOUR = 20


# ---------- forecasts ----------
def forecast(branch_id, days=7):
    """Paddy (kg) and orders per day for the next `days` days: the average of the same weekday over the last
    HISTORY_WEEKS weeks. `basis` says how many past days of that weekday were used."""
    today = now_local().date()
    start = day_start_utc(today - timedelta(weeks=HISTORY_WEEKS))
    per_day = {}
    for o in col("AOITS_ORDMILLING").find({"BRANCH_ID": branch_id, "REGIST_DATE": {"$gte": start}}, NO_ID):
        if o["MILL_STATUS"] == "CC":
            continue
        d = to_local(o["REGIST_DATE"]).date()
        if d >= today:
            continue
        kg, n = per_day.get(d, (0.0, 0))
        per_day[d] = (kg + (o.get("REGIST_PADDY") or 0), n + 1)
    history_days = [today - timedelta(days=i) for i in range(1, HISTORY_WEEKS * 7 + 1)]
    first_order = min(per_day) if per_day else None
    out = []
    for i in range(1, days + 1):
        day = today + timedelta(days=i)
        same = [d for d in history_days if d.weekday() == day.weekday() and first_order and d >= first_order]
        kgs = [per_day.get(d, (0.0, 0))[0] for d in same]
        counts = [per_day.get(d, (0.0, 0))[1] for d in same]
        out.append({"day": day, "kg": round_half_up(statistics.mean(kgs), 0) if kgs else 0.0,
                    "orders": round_half_up(statistics.mean(counts), 1) if counts else 0.0, "basis": len(same)})
    return out


def queue_load(branch_id):
    """What is waiting now: paddy in Queue / In-process / Hold and the milling time it needs."""
    branch = col("AOIMS_SYSPARAM").find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    waiting = list(col("AOITS_ORDMILLING").find({"BRANCH_ID": branch_id, "MILL_STATUS": {"$in": ["Q", "I", "H"]}}, NO_ID))
    kg = sum(o.get("REGIST_PADDY") or 0 for o in waiting)
    minutes = kg * float(branch.get("SYSP_MILLTIMEKG") or 0) + len(waiting) * float(branch.get("SYSP_MILLORDGAP") or 0)
    last = max((o["EXPCT_RECEIVE_TIME"] for o in waiting if o["MILL_STATUS"] != "H"), default=None)
    return {"orders": len(waiting), "kg": round_half_up(kg, 1), "hours": round_half_up(minutes / 60.0, 1), "last_receive": last}


# ---------- price advice ----------
def price_advice(branch_id, days=14):
    """Per product: own stock, average daily sales and purchases over `days` days, days of stock cover, and a
    rule-based advice. The decision stays with the user (prices change only on Pricing Maintenance)."""
    since = now_utc() - timedelta(days=days)
    sold, bought = {}, {}
    for o in col("AOITS_ORDBUYSELL").find({"BRANCH_ID": branch_id, "ORD_STATUS": "C", "LAST_UPDATE": {"$gte": since}}, NO_ID):
        target = sold if o["ORD_TYPE"] == "SL" else bought
        for line in (o.get("ORD_ITEMS") or [o]):          # update 39: take-home orders have a line per SKU
            target[line["MAT_CODE"]] = target.get(line["MAT_CODE"], 0.0) + (line.get("ORD_QTY") or 0)
    out = []
    for price in Price.for_branch(branch_id):
        mat = price["MAT_CODE"]
        stock = Inventory.free_qty(branch_id, mat)
        per_day_sold = sold.get(mat, 0.0) / days
        cover = (stock / per_day_sold) if per_day_sold > 0 else None
        sug_buy, sug_sell = Price.suggestion(price)
        mk_buy = price.get("MKPRICE_BUY")
        advice, advised_buy = "keep", sug_buy
        if price.get("PRICE_STATUS") == "C":
            advice = "manual"
        elif cover is not None and cover < 3:
            advice = "raise_buy"                       # running out: buy more, at the low-margin end of the range
            if mk_buy and price.get("MG_BUY_RANGEFROM") is not None:
                advised_buy = round_half_up(mk_buy * (1 - price["MG_BUY_RANGEFROM"] / 100.0), 2)
        elif cover is not None and cover > 30 or (cover is None and stock > 0 and sold.get(mat, 0) == 0 and stock >= 2000):
            advice = "lower_buy"                       # plenty in stock: buy less, at the high-margin end
            if mk_buy and price.get("MG_BUY_RANGETO") is not None:
                advised_buy = round_half_up(mk_buy * (1 - price["MG_BUY_RANGETO"] / 100.0), 2)
        out.append({"mat": mat, "price_buy": price.get("PRICE_BUY"), "price_sell": price.get("PRICE_SELL"), "sug_buy": sug_buy,
                    "sug_sell": sug_sell, "advised_buy": advised_buy, "stock": stock, "sold_per_day": round_half_up(per_day_sold, 1),
                    "bought": round_half_up(bought.get(mat, 0.0), 1), "cover": round_half_up(cover, 1) if cover is not None else None,
                    "advice": advice, "margins": Price.margins(price)})
    return out


# ---------- anomaly alerts ----------
def anomalies(branch_id, days=30):
    """Unusual things worth a look. Each alert: kind, severity (warning / serious), text key and values."""
    alerts = []
    since = now_utc() - timedelta(days=days)
    # 1. milled rice received far from the expected yield (inbound at Finished vs EXPCT_RICE)
    inbound = {}
    for log in col("AOILOG_ONHAND").find({"BRANCH_ID": branch_id, "OPE_TYPE": "I", "MOVE_TYPE": "Rice Mill", "LOG_DATE": {"$gte": since}}, NO_ID):
        inbound[log.get("REF_ORD_ID")] = inbound.get(log.get("REF_ORD_ID"), 0.0) + log["INV_QTY"]
    for ord_id, qty in inbound.items():
        order = col("AOITS_ORDMILLING").find_one({"ORD_ID": ord_id}, NO_ID)
        if order and order.get("EXPCT_RICE"):
            diff = (qty - order["EXPCT_RICE"]) / order["EXPCT_RICE"] * 100
            if abs(diff) > 10:
                alerts.append({"kind": "yield", "severity": "serious" if abs(diff) > 20 else "warning", "key": "aiAlertYield",
                               "params": {"id": order["QR_ID"], "got": "%.1f" % qty, "exp": "%.1f" % order["EXPCT_RICE"], "pct": "%+.0f" % diff}})
    # 2. electricity per kg of paddy far above the usual day
    today = now_local().date()
    ratios = []
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        start, end = day_start_utc(day), day_start_utc(day + timedelta(days=1))
        kwh, kg = Meter.used_between(branch_id, start, end), paddy_milled(branch_id, start, end)
        if kwh > 0 and kg > 0:
            ratios.append((day, kwh / kg, kwh))
        elif kwh > 0 and kg == 0 and i > 0:
            alerts.append({"kind": "power", "severity": "warning", "key": "aiAlertPowerIdle",
                           "params": {"day": day.strftime("%d/%m"), "kwh": "%.1f" % kwh}})
    if len(ratios) >= 4:
        usual = statistics.median(r for _d, r, _k in ratios)
        for day, ratio, kwh in ratios:
            if usual > 0 and ratio > usual * 1.5:
                alerts.append({"kind": "power", "severity": "warning", "key": "aiAlertPower",
                               "params": {"day": day.strftime("%d/%m"), "ratio": "%.3f" % ratio, "usual": "%.3f" % usual}})
    # 3. machines: faults in the last 7 days, and running much longer than usual today
    week = now_utc() - timedelta(days=7)
    for machine in Machine.for_branch(branch_id):
        faults = sum(1 for e in Machine.events(branch_id, week, machine["MACHINE_ID"]) if e["NEW_STATUS"] == FAULT and e.get("OLD_STATUS") != FAULT)
        if faults:
            alerts.append({"kind": "machine", "severity": "serious" if faults >= 3 else "warning", "key": "aiAlertFaults",
                           "params": {"name": machine["MACHINE_NAME"], "n": faults}})
        if machine.get("MACHINE_STATUS") == FAULT:
            alerts.append({"kind": "machine", "severity": "serious", "key": "aiAlertFaultNow", "params": {"name": machine["MACHINE_NAME"]}})
        daily = [h for _d, h in Machine.daily_run_hours(machine, 14)]
        past = [h for h in daily[:-1] if h > 0]
        if len(past) >= 4 and daily[-1] > statistics.median(past) * 1.5:
            alerts.append({"kind": "machine", "severity": "warning", "key": "aiAlertRunLong",
                           "params": {"name": machine["MACHINE_NAME"], "h": "%.1f" % daily[-1], "usual": "%.1f" % statistics.median(past)}})
    # 4. prices outside their margin range
    for price in Price.for_branch(branch_id):
        m = Price.margins(price)
        if m.get("buy_ok") is False or m.get("sell_ok") is False:
            alerts.append({"kind": "price", "severity": "warning", "key": "aiAlertMargin", "params": {"mat": price["MAT_CODE"]}})
    # 5. supply: the national agricultural production index far from last year (NABC)
    from . import supply
    sp = supply.summary()
    if sp and sp["yoy"] is not None and abs(sp["yoy"]) > 10:
        alerts.append({"kind": "supply", "severity": "warning", "key": "aiAlertSupply",
                       "params": {"pct": "%+.1f" % sp["yoy"], "month": "%02d/%d" % (sp["month"], sp["year_th"])}})
    order = {"serious": 0, "warning": 1}
    return sorted(alerts, key=lambda a: order.get(a["severity"], 2))


# ---------- ask the system ----------
def snapshot(branch_id):
    """The figures sent with a question: totals only, no personal data."""
    today = now_local().date()
    start = day_start_utc(today)
    statuses = {}
    for o in col("AOITS_ORDMILLING").find({"BRANCH_ID": branch_id, "MILL_STATUS": {"$nin": ["C", "CC"]}}, NO_ID):
        statuses[o["MILL_STATUS"]] = statuses.get(o["MILL_STATUS"], 0) + 1
    week = day_start_utc(today - timedelta(days=6))
    income = expense = 0.0
    for e in col("AOITS_ACCOUNT").find({"BRANCH_ID": branch_id, "ACC_DATE": {"$gte": week}}, NO_ID):
        if e["ACC_TYPE"] == "IN":
            income += e["ACC_AMOUNT"]
        else:
            expense += e["ACC_AMOUNT"]
    stock = {}
    for item in Inventory.items(branch_id):
        key = item["MAT_CODE"] + ("" if not item.get("SUB_2") else " (kept for customers)")
        stock[key] = round_half_up(stock.get(key, 0) + item["qty"], 1)
    return {
        "date": today.isoformat(), "branch": branch_id,
        "open_milling_orders_by_status": statuses,
        "milling_today_paddy_kg": paddy_milled(branch_id, start, now_utc()),
        "queue_load": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in queue_load(branch_id).items()},
        "forecast_next_7_days": [{"day": f["day"].isoformat(), "paddy_kg": f["kg"], "orders": f["orders"]} for f in forecast(branch_id)],
        "stock_kg": stock,
        "prices_thb_per_kg": {p["MAT_CODE"]: {"buy": p.get("PRICE_BUY"), "sell": p.get("PRICE_SELL"), "status": p.get("PRICE_STATUS")}
                              for p in Price.for_branch(branch_id)},
        "ledger_last_7_days_thb": {"income": round_half_up(income, 2), "expense": round_half_up(expense, 2)},
        "electricity_last_7_days_kwh": Meter.used_between(branch_id, week, now_utc()),
        "machines": [{"name": m["MACHINE_NAME"], "type": m["MACHINE_TYPE"], "status": m.get("MACHINE_STATUS")}
                     for m in Machine.for_branch(branch_id)],
        "alerts": [a["key"] + " " + json.dumps(a["params"], ensure_ascii=False) for a in anomalies(branch_id)][:10],
        "market_prices": {p["MAT_CODE"]: p.get("MKPRICE_SELL") for p in Price.for_branch(branch_id) if p.get("MKPRICE_SELL")},
        "market_report_date": _report_date(),
        "profit_check": [{k: e[k] for k in ("variety", "value", "cost", "break_even", "market_sug", "highest", "recommended",
                                             "profit", "current_buy", "current_profit", "rice_break_even", "rice_sell")}
                         for e in profit.evaluate_all(branch_id)],
        "yields": {v: MillYield.ratios(branch_id, v) for v in ("HM", "WH", "GL")},
        "supply_indicator_nabc_agri_production_index": _supply(),
    }


def _supply():
    from . import supply
    sp = supply.summary()
    return {k: sp[k] for k in ("period", "value", "mom", "yoy", "signal")} if sp else None


def _report_date():
    from .market import MarketReport
    report = MarketReport.latest(ok_only=True)
    return report["REPORT_DATE"].date().isoformat() if report and report.get("REPORT_DATE") else None


def ai_configured():
    return bool(current_app.config.get("AI_API_KEY"))


def ask(branch_id, user_id, question, lang):
    """Sends the question and the snapshot to the AI service. Returns the answer text (logged in AOILOG_AI_ASK)."""
    question = clean_text(question, 500)
    if not question:
        raise ValidationError("aiErrEmpty")
    if not ai_configured():
        raise ValidationError("aiErrNotConfigured")
    hour_ago = now_utc() - timedelta(hours=1)
    if col("AOILOG_AI_ASK").count_documents({"USER_ID": user_id, "LOG_DATE": {"$gte": hour_ago}}) >= ASK_LIMIT_PER_HOUR:
        raise ValidationError("aiErrLimit", n=ASK_LIMIT_PER_HOUR)
    config = current_app.config
    system = ("You help the staff of a rice mill (AOI Labs). Answer in %s, briefly, using only the figures given. "
              "If the figures do not answer the question, say so and say which screen of the system to check. "
              "Rice varieties: HM jasmine (Hom Mali), WH white, GL glutinous. Material codes: PADDY_HM / PADDY / PADDY_GL paddy, "
              "RICE_HM / RICE_W5 / RICE_GL rice, BROKEN_HM / BROKEN / BROKEN_GL broken rice (jasmine / white / glutinous), "
              "BRAN rice bran, HUSK husk. Prices are THB per kg. profit_check gives, per variety, the value of 1 kg paddy once "
              "milled, the break-even and recommended buying prices. Prices only change when a person approves them. "
              "Milling statuses: Q queue, I in process, H hold, F finished, D ready for delivery. "
              "Never invent numbers." % ("Thai" if lang == "th" else "English"))
    body = {"model": config["AI_MODEL"], "max_tokens": 600, "system": system,
            "messages": [{"role": "user", "content": "Figures of the branch (JSON):\n%s\n\nQuestion: %s"
                          % (json.dumps(snapshot(branch_id), ensure_ascii=False, default=str), question)}]}
    req = urllib.request.Request(config["AI_API_URL"], data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"content-type": "application/json", "x-api-key": config["AI_API_KEY"],
                                          "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            reply = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        current_app.logger.warning("AI service call failed: %s", error)
        raise ValidationError("aiErrService")
    answer = "".join(part.get("text", "") for part in reply.get("content", []) if part.get("type") == "text").strip()
    if not answer:
        raise ValidationError("aiErrService")
    col("AOILOG_AI_ASK").insert_one({"BRANCH_ID": branch_id, "USER_ID": user_id, "QUESTION": question, "ANSWER": answer[:4000],
                                     "MODEL": config["AI_MODEL"], "LOG_DATE": now_utc()})
    return answer


def recent_questions(branch_id, user_id, limit=5):
    return list(col("AOILOG_AI_ASK").find({"BRANCH_ID": branch_id, "USER_ID": user_id}, NO_ID).sort([("LOG_DATE", -1)]).limit(limit))
