"""Evaluate & suggest: what a kg of paddy is worth to the mill, and the buying price that gives the best profit.

For each rice variety, with the branch's yield ratios (from the milling tests) and the suggested selling prices:

  value of 1 kg paddy  = rice ratio x rice price + broken ratio x broken price + bran ratio x bran price
                         + husk ratio x husk price
  processing cost /kg  = electricity + labour per kg + other cost per kg (SYSP_PROCCOSTKG)
  electricity per kg   = from the meters (kWh per kg x rate) when there are readings; otherwise the monthly bill
                         (SYSP_ELECMONTH) / (kg per day x working days per month, SYSP_WORKDAYS); otherwise
                         estimated from the machines' rated kW
  labour per kg        = labour per worker per day (SYSP_LABORDAY) x workers (SYSP_WORKERS) / kg paddy per day,
                         kg per day = the planned paddy per day (SYSP_PLANKGDAY, default 2,000 kg); when it is not
                         set, the average of the days with orders in the last 30 days, or the capacity of one
                         working day (operating minutes / minutes per kg). The actual average is shown beside it.
  break-even buy price = value - processing cost
  highest buy price    = break-even x (1 - paddy buying margin target %)     keeps the target profit on milling
  recommended buy      = the lower of the market suggestion (market - margin) and the highest buy price
  profit per kg        = value - processing cost - recommended buy
  rice break-even sell = (buy price + processing cost - by-product value) / rice ratio

kWh per kg comes from the meters and the paddy milled in the last 30 days; without meter readings it is estimated
from the machines' rated kW and the milling minutes per kg. Prices only change when they are approved on
Pricing Maintenance.
"""
from datetime import timedelta

from .base import col, NO_ID, round_half_up
from .mill_yield import MillYield
from .price import Price
from . import variety as var
from ..timeutil import now_utc

DAYS = 30


def _planned(price, side):
    """The price that will apply: the suggestion while it waits for approval, otherwise the current price."""
    if not price:
        return 0.0
    if price.get("PRICE_STATUS") == "W":
        sug = Price.suggestion(price)[0 if side == "buy" else 1]
        if sug is not None:
            return float(sug)
    return float(price.get("PRICE_BUY" if side == "buy" else "PRICE_SELL") or 0)


def energy_per_kg(branch_id):
    """(kWh per kg paddy, 'meter' | 'estimate' | None)."""
    from .automation import Machine, Meter, elec_rate, paddy_milled
    end = now_utc()
    start = end - timedelta(days=DAYS)
    kwh, paddy = Meter.used_between(branch_id, start, end), paddy_milled(branch_id, start, end)
    if kwh > 0 and paddy > 0:
        return kwh / paddy, "meter"
    branch = col("AOIMS_SYSPARAM").find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    minutes = float(branch.get("SYSP_MILLTIMEKG") or 0)
    kw = sum(m.get("RATED_KW") or 0 for m in Machine.for_branch(branch_id, active_only=True))
    if kw > 0 and minutes > 0:
        return kw * minutes / 60.0, "estimate"
    return 0.0, None


def _minutes(text):
    try:
        hours, minutes = (int(x) for x in (text or "").split(":"))
        return hours * 60 + minutes
    except ValueError:
        return None


def labour(branch_id):
    """Labour cost: {'day': THB per day, 'kg_day': kg paddy per day, 'per_kg', 'source': 'orders' | 'capacity' | None,
    'capacity': kg per working day}."""
    from .automation import day_start_utc
    from ..timeutil import now_local, to_local
    branch = col("AOIMS_SYSPARAM").find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    day_cost = float(branch.get("SYSP_LABORDAY") or 0) * float(branch.get("SYSP_WORKERS") or 0)
    open_min, close_min = _minutes(branch.get("SYSP_OPETIMESTART")), _minutes(branch.get("SYSP_OPETIMEEND"))
    per_kg_min = float(branch.get("SYSP_MILLTIMEKG") or 0)
    capacity = ((close_min - open_min) / per_kg_min) if open_min is not None and close_min and per_kg_min > 0 else 0.0
    start = day_start_utc(now_local().date() - timedelta(days=DAYS))
    per_day = {}
    for o in col("AOITS_ORDMILLING").find({"BRANCH_ID": branch_id, "REGIST_DATE": {"$gte": start}}, NO_ID):
        if o["MILL_STATUS"] != "CC":
            d = to_local(o["REGIST_DATE"]).date()
            per_day[d] = per_day.get(d, 0.0) + (o.get("REGIST_PADDY") or 0)
    actual = (sum(per_day.values()) / len(per_day)) if per_day else None
    planned = float(branch.get("SYSP_PLANKGDAY") or 0)
    if planned > 0:
        kg_day, source = planned, "planned"
    elif per_day:
        kg_day, source = actual, "orders"
    elif capacity > 0:
        kg_day, source = capacity, "capacity"
    else:
        kg_day, source = 0.0, None
    per_kg = (day_cost / kg_day) if kg_day > 0 else 0.0
    return {"day": day_cost, "kg_day": round_half_up(kg_day, 1), "per_kg": per_kg, "source": source if day_cost else None,
            "capacity": round_half_up(capacity, 0), "days": len(per_day), "planned": planned or None,
            "actual": round_half_up(actual, 1) if actual is not None else None,
            "per_kg_actual": round_half_up(day_cost / actual, 2) if actual else None,
            "per_kg_capacity": round_half_up(day_cost / capacity, 2) if capacity > 0 else None}


def evaluate(branch_id, variety):
    """The evaluation of one variety (all prices THB/kg)."""
    from .automation import elec_rate
    ratios = MillYield.ratios(branch_id, variety)
    prices = {code: Price.get(branch_id, code) for code in var.ALL}
    paddy = var.code(variety, var.PADDY)
    sell = {kind: _planned(prices.get(var.code(variety, kind)), "sell") for kind in (var.RICE, var.BROKEN, var.BRAN, var.HUSK)}
    by_products = ratios["broken"] * sell[var.BROKEN] + ratios["bran"] * sell[var.BRAN] + ratios["husk"] * sell[var.HUSK]
    value = ratios["rice"] * sell[var.RICE] + by_products
    kwh_kg, energy_source = energy_per_kg(branch_id)
    branch = col("AOIMS_SYSPARAM").find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    rate = elec_rate(branch_id)
    other = float(branch.get("SYSP_PROCCOSTKG") or 0)
    work = labour(branch_id)
    energy_cost = kwh_kg * rate
    bill = float(branch.get("SYSP_ELECMONTH") or 0)
    workdays = float(branch.get("SYSP_WORKDAYS") or 0)
    if energy_source != "meter" and bill > 0 and workdays > 0 and work["kg_day"] > 0:
        # no meter readings: the monthly electricity bill shared over the paddy of a month (kg per day x working days)
        energy_cost, energy_source = bill / (work["kg_day"] * workdays), "bill"
        kwh_kg = (energy_cost / rate) if rate else 0.0
    cost = energy_cost + work["per_kg"] + other
    break_even = value - cost
    paddy_price = prices.get(paddy) or {}
    target = paddy_price.get("MG_BUY_TARGET")
    target = float(target) if target is not None else 0.0
    highest = break_even * (1 - target / 100.0)
    market_sug = Price.suggestion(paddy_price)[0] if paddy_price else None
    current_buy = float(paddy_price.get("PRICE_BUY") or 0)
    candidates = [v for v in (market_sug, highest) if v is not None and v > 0]
    recommended = min(candidates) if candidates else None
    limited = recommended is not None and market_sug is not None and highest < market_sug
    buy_for_rice = recommended if recommended is not None else current_buy
    rice_break_even = ((buy_for_rice + cost - by_products) / ratios["rice"]) if ratios["rice"] > 0 else None
    paddy_resell = _planned(paddy_price, "sell")
    profit = (value - cost - recommended) if recommended is not None else None
    return {
        "variety": variety, "paddy": paddy, "ratios": ratios, "sell": sell,
        "value": round_half_up(value, 2), "by_products": round_half_up(by_products, 2),
        "kwh_kg": round_half_up(kwh_kg, 4), "energy_source": energy_source, "rate": rate, "other": other,
        "energy_cost": round_half_up(energy_cost, 2), "bill": bill, "workdays": workdays, "labour": work, "labour_kg": round_half_up(work["per_kg"], 2),
        "cost": round_half_up(cost, 2), "break_even": round_half_up(break_even, 2), "target": target,
        "highest": round_half_up(highest, 2), "market": paddy_price.get("MKPRICE_BUY"),
        "market_sug": market_sug, "current_buy": current_buy,
        "recommended": round_half_up(recommended, 2) if recommended is not None else None, "limited": limited,
        "profit": round_half_up(profit, 2) if profit is not None else None,
        "profit_ton": round_half_up(profit * 1000, 0) if profit is not None else None,
        "current_profit": round_half_up(value - cost - current_buy, 2) if current_buy else None,
        "rice_break_even": round_half_up(rice_break_even, 2) if rice_break_even is not None else None,
        "rice_sell": sell[var.RICE], "rice_ok": rice_break_even is None or sell[var.RICE] >= rice_break_even,
        "resell_profit": round_half_up(paddy_resell - buy_for_rice, 2) if paddy_resell else None,
        "mill_better": profit is not None and (not paddy_resell or profit >= paddy_resell - buy_for_rice),
        "ok": ratios["rice"] > 0 and sell[var.RICE] > 0,
    }


def evaluate_all(branch_id):
    return [evaluate(branch_id, v) for v in var.VARIETIES]


def recommended_buy(branch_id):
    """{paddy SKU: recommended buying price} where milling limits the price below the market suggestion."""
    return {e["paddy"]: e["recommended"] for e in evaluate_all(branch_id) if e["ok"] and e["limited"] and e["recommended"]}
