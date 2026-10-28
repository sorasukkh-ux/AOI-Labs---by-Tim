"""Price analysis (Automation > AI > Price analysis, update 50): every price source of a SKU on one chart, the trend,
and buy / sell points worked out like a stock chart, with take-profit and stop-loss levels for the stock on hand.

Sources of a SKU (THB/kg):
  NABC   NABC daily farm prices of the SKU's product (when mapped)            - mill-gate / farm market
  TRMA   Thai Rice Millers Association report rows (when mapped)              - wholesale market
  TREA   Thai Rice Exporters Association weekly FOB prices (US$/ton x rate)   - world market (export)
  APPLIED  the market price applied on Pricing Maintenance of the branch
  BUY / SELL  the branch's own buying / selling orders (average price per day, weighted by kg)

The BASE source of a SKU (set on the screen; first guess: its market source, else the first one with data) is the
line the signals are worked out on, filled day by day (a weekly price holds until the next one):

  SMA short / long   simple moving averages (default 7 and 30 days)
  Bands              average of the last 20 prices +- 2 standard deviations (Bollinger)
  RSI 14             relative strength of the last 14 prices: under 30 cheap (oversold), over 70 expensive (overbought)
                     (bands and RSI count each reported price once, so a weekly export price is not counted 7 times)
  Support / resistance   lowest / highest price of the last 60 days
  Trend              slope of the last 60 days (% per 30 days)
  Crosses            short SMA crossing above the long SMA (golden cross, B) or below it (death cross, S)

  Score  +2 RSI < 30, +1 RSI < 40, -1 RSI > 60, -2 RSI > 70; +2 under the lower band, -2 over the upper band;
         +2 golden cross in the last 7 days, -2 death cross; +1 short over long (up trend), -1 under.
         >= 3 strong buy, >= 1 buy, <= -3 strong sell, <= -1 sell, else hold.
  Buy = a good time to buy / keep stock (cheap or turning up); sell = a good time to sell stock (expensive or turning
  down).

Position: the free stock of the SKU at the average buying price of the last 90 days (own buying orders; else the
branch's buying price, or a price typed on the screen). Take profit = cost x (1 + TP %), stop loss = cost x (1 - SL %).
These are statistics on past prices, not a promise: the decision stays with the user.
"""
import statistics
from datetime import date, timedelta

from .base import col, NO_ID, ValidationError, audit_new, round_half_up
from . import export_price, variety as var
from ..timeutil import now_utc, to_local, today_local

CFG = "AOIMS_PRICESIG"
SOURCES = ("NABC", "TRMA", "TREA", "APPLIED", "AIWEB", "AIHIST", "BUY", "SELL")   # update 77: + AI web search
DEFAULTS = {"FX_USD_THB": 0.0, "SMA_SHORT": 7, "SMA_LONG": 30, "TP_PCT": 8.0, "SL_PCT": 5.0, "BASE": {}, "TREA_KEYS": {}}
BAND_DAYS, RSI_DAYS, RANGE_DAYS, CROSS_DAYS = 20, 14, 60, 7
PERIODS = {"3M": 92, "6M": 183, "12M": 365, "36M": 1096}


# ---------- settings ----------
def settings():
    from flask import current_app
    doc = col(CFG).find_one({"CFG_ID": "SIG"}, NO_ID) or {}
    out = dict(DEFAULTS, **{k: v for k, v in doc.items() if v is not None})
    if not out.get("FX_USD_THB"):
        out["FX_USD_THB"] = current_app.config.get("USD_THB") or 0.0
    return out


def save_settings(form, ope_user):
    def num(name, lo, hi, cast=float):
        try:
            v = cast(str(form.get(name) or "").replace(",", "").strip())
        except ValueError:
            raise ValidationError("psErrNumber", field=name)
        if not lo <= v <= hi:
            raise ValidationError("psErrNumber", field=name)
        return v
    cur = settings()
    values = {"FX_USD_THB": round_half_up(num("FX_USD_THB", 1, 200), 4), "SMA_SHORT": num("SMA_SHORT", 2, 60, int),
              "SMA_LONG": num("SMA_LONG", 5, 250, int), "TP_PCT": num("TP_PCT", 0.5, 100), "SL_PCT": num("SL_PCT", 0.5, 100)}
    if values["SMA_SHORT"] >= values["SMA_LONG"]:
        raise ValidationError("psErrSma")
    base, keys = dict(cur.get("BASE") or {}), dict(cur.get("TREA_KEYS") or {})
    for code in var.TRADED:
        if form.get("BASE__" + code) in SOURCES:
            base[code] = form["BASE__" + code]
        elif ("BASE__" + code) in form:
            base.pop(code, None)
        if ("TREA__" + code) in form:
            k = (form.get("TREA__" + code) or "").strip()
            keys[code] = k or None
    values.update(BASE=base, TREA_KEYS=keys, FX_DATE=now_utc(), OPE_USER=ope_user, LAST_UPDATE=now_utc())
    if col(CFG).find_one({"CFG_ID": "SIG"}, NO_ID) is None:
        col(CFG).insert_one(dict(values, CFG_ID="SIG", **audit_new(ope_user)))
    else:
        col(CFG).update_one({"CFG_ID": "SIG"}, {"$set": values})
    return values


def trea_key(code, cfg, keys=None):
    """The export price row of a SKU: saved on the screen (empty = none), else the first guess."""
    saved = cfg.get("TREA_KEYS") or {}
    if code in saved:
        return saved[code]
    return export_price.guess_key(code, keys)


# ---------- sources ----------
def _own_series(branch_id, code, side, start):
    from .buysell_order import BuySellOrder, CANCELLED
    days = {}
    since = to_utc_day(start)
    for o in col(BuySellOrder.COL).find({"BRANCH_ID": branch_id, "ORD_TYPE": side, "ORD_STATUS": {"$ne": CANCELLED},
                                         "REGIST_DATE": {"$gte": since}}, NO_ID):
        day = to_local(o["REGIST_DATE"]).date().isoformat()
        for line in BuySellOrder.items_of(o):
            qty, price = float(line.get("ORD_QTY") or 0), float(line.get("UNIT_PRICE") or line.get("PRICING_UINT") or 0)
            if line.get("MAT_CODE") == code and qty > 0 and price > 0:
                kg, amount = days.get(day, (0.0, 0.0))
                days[day] = (kg + qty, amount + qty * price)
    return [(d, round_half_up(a / k, 2)) for d, (k, a) in sorted(days.items()) if k]


def to_utc_day(day):
    from .automation import day_start_utc
    return day_start_utc(day)


def sources(branch_id, code, start, cfg=None, export_keys=None):
    """{source: [(day iso, THB/kg)]} of a SKU from `start` (only sources with data)."""
    from .market import MarketMap, MarketReport
    from .market_history import _nabc_series, _trma_series, _hand_series
    cfg = cfg or settings()
    m = next((x for x in MarketMap.all() if x["MAT_CODE"] == code), {})
    out = {}

    def put(name, fn):
        try:
            pts = fn()
        except Exception:                                 # noqa: BLE001 - one source never stops the analysis
            pts = []
        if pts:
            out[name] = [(d, float(v)) for d, v in pts if v]
    if m.get("NABC_PRODUCT"):
        put("NABC", lambda: _nabc_series(m, start))
    if m.get("ROW_KEY"):
        put("TRMA", lambda: _trma_series(m, start, list(col(MarketReport.COL).find({"REPORT_STATUS": "OK"}, NO_ID).sort([("FETCH_DATE", 1)]))))
    put("TREA", lambda: export_price.series(trea_key(code, cfg, export_keys), start, cfg.get("FX_USD_THB")))
    put("APPLIED", lambda: _hand_series(code, branch_id, start))
    from . import ai_price                                # update 77: prices found on the web by the AI
    put("AIWEB", lambda: ai_price.series(code, ai_price.DAILY, start))
    put("AIHIST", lambda: ai_price.series(code, ai_price.HISTORY, start))
    put("BUY", lambda: _own_series(branch_id, code, "BY", start))
    put("SELL", lambda: _own_series(branch_id, code, "SL", start))
    return out


def base_of(code, found, cfg):
    """The source the signals are worked out on: the one chosen in the settings; else (update 77) the SKU's market
    mapping, NABC, millers' report, applied, export, AI web price - the first with at least BASE_MIN_POINTS prices;
    else the source with the most prices (the AI monthly estimate and own prices last)."""
    from .market import MarketMap
    chosen = (cfg.get("BASE") or {}).get(code)
    if chosen in found:
        return chosen
    m = next((x for x in MarketMap.all() if x["MAT_CODE"] == code), {})
    order = ([m.get("SOURCE")] if m.get("SOURCE") in found else []) + ["NABC", "TRMA", "APPLIED", "TREA", "AIWEB", "AIHIST", "SELL", "BUY"]
    for name in order:
        if name in found and len(found[name]) >= BASE_MIN_POINTS and name not in ("AIHIST", "SELL", "BUY"):
            return name
    ranked = sorted((name for name in order if name in found), key=lambda n: -len(found[n]))
    return ranked[0] if ranked else None


BASE_MIN_POINTS = 20
# update 77 (Tim): jasmine and white / glutinous are different qualities - a white or glutinous price this close to
# the jasmine price of the same stage is most likely the wrong row or product
QUALITY_PAIRS = (("PADDY", "PADDY_HM"), ("PADDY_GL", "PADDY_HM"), ("RICE_W5", "RICE_HM"), ("RICE_GL", "RICE_HM"))
QUALITY_MAX_RATIO = 0.90


def quality_check(prices):
    """`prices` {MAT_CODE: THB/kg} -> [(code, jasmine code, ratio %)] where a lower grade is >= 90% of jasmine."""
    out = []
    for low, high in QUALITY_PAIRS:
        a, b = prices.get(low), prices.get(high)
        if a and b and a >= b * QUALITY_MAX_RATIO:
            out.append((low, high, round_half_up(a / b * 100, 0)))
    return out


# ---------- indicators ----------
def daily(points, end=None):
    """[(date, price)] every day from the first point to `end`, a price holding until the next one."""
    if not points:
        return []
    pts = sorted((date.fromisoformat(d), v) for d, v in points)
    end = max(end or pts[-1][0], pts[-1][0])
    out, i, day, cur = [], 0, pts[0][0], pts[0][1]
    while day <= end:
        while i < len(pts) and pts[i][0] <= day:
            cur = pts[i][1]
            i += 1
        out.append((day, cur))
        day += timedelta(days=1)
    return out


def sma(values, n):
    out, total = [], 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def rsi(values, n=RSI_DAYS):
    """Wilder's RSI; None until n changes are known, 50 for a flat price."""
    out = [None] * len(values)
    if len(values) <= n:
        return out
    gains = [max(0.0, values[i] - values[i - 1]) for i in range(1, len(values))]
    losses = [max(0.0, values[i - 1] - values[i]) for i in range(1, len(values))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(values)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 50.0 if ag == al == 0 else (100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al))
    return out


def bands(values, n=BAND_DAYS, k=2.0):
    mid, up, lo = [], [], []
    for i in range(len(values)):
        if i < n - 1:
            mid.append(None); up.append(None); lo.append(None)
            continue
        w = values[i - n + 1:i + 1]
        m, sd = statistics.mean(w), statistics.pstdev(w)
        mid.append(m); up.append(m + k * sd); lo.append(m - k * sd)
    return mid, up, lo


def slope_pct(values, days=RANGE_DAYS):
    """Least-squares slope of the last `days` days as % of the average per 30 days."""
    w = values[-days:]
    if len(w) < 10:
        return None
    n, xm, ym = len(w), (len(w) - 1) / 2.0, statistics.mean(w)
    den = sum((i - xm) ** 2 for i in range(n))
    if not den or not ym:
        return None
    return sum((i - xm) * (v - ym) for i, v in enumerate(w)) / den * 30 / ym * 100


def crosses(days, short, long_):
    """[(date, 'B' or 'S', price)]: the short average crossing above (B) or below (S) the long one."""
    out = []
    for i in range(1, len(days)):
        a0, b0, a1, b1 = short[i - 1], long_[i - 1], short[i], long_[i]
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:
            out.append((days[i][0], "B", days[i][1]))
        elif a0 >= b0 and a1 < b1:
            out.append((days[i][0], "S", days[i][1]))
    return out


def signal(days, cfg, obs=None):
    """Indicators and the score. `days`: the filled daily series (averages, support / resistance, trend, crosses);
    `obs`: the prices as reported (RSI and bands per report, so a weekly price is not counted 7 times)."""
    if len(days) < 2:
        return None
    values = [v for _d, v in days]
    s, l_ = sma(values, cfg["SMA_SHORT"]), sma(values, cfg["SMA_LONG"])
    obs = obs or days
    ov = [v for _d, v in obs]
    mid, up, lo = bands(ov)
    r = rsi(ov)
    last = values[-1]
    window = values[-RANGE_DAYS:]
    cr = crosses(days, s, l_)
    recent = [c for c in cr if (days[-1][0] - c[0]).days <= CROSS_DAYS]
    score, reasons = 0, []

    def add(points, key, **params):
        nonlocal score
        score += points
        reasons.append({"key": key, "points": points, "params": params})
    if r[-1] is not None:
        rv = round_half_up(r[-1], 1)
        if r[-1] < 30:
            add(2, "psWhyRsiLow", rsi=rv)
        elif r[-1] < 40:
            add(1, "psWhyRsiSoft", rsi=rv)
        elif r[-1] > 70:
            add(-2, "psWhyRsiHigh", rsi=rv)
        elif r[-1] > 60:
            add(-1, "psWhyRsiFirm", rsi=rv)
    if lo[-1] is not None and up[-1] is not None and up[-1] > lo[-1]:
        if last < lo[-1]:
            add(2, "psWhyUnderBand", band=round_half_up(lo[-1], 2))
        elif last > up[-1]:
            add(-2, "psWhyOverBand", band=round_half_up(up[-1], 2))
    if recent:
        kind = recent[-1][1]
        add(2 if kind == "B" else -2, "psWhyGolden" if kind == "B" else "psWhyDeath", day=recent[-1][0].strftime("%d/%m"))
    elif s[-1] is not None and l_[-1] is not None and s[-1] != l_[-1]:
        add(1 if s[-1] > l_[-1] else -1, "psWhyUp" if s[-1] > l_[-1] else "psWhyDown")
    label = "strong_buy" if score >= 3 else "buy" if score >= 1 else "strong_sell" if score <= -3 else "sell" if score <= -1 else "hold"
    rnd = lambda x: round_half_up(x, 2) if x is not None else None
    return {"last": rnd(last), "last_day": days[-1][0].isoformat(), "sma_short": rnd(s[-1]), "sma_long": rnd(l_[-1]),
            "band_mid": rnd(mid[-1]), "band_up": rnd(up[-1]), "band_low": rnd(lo[-1]), "rsi": round_half_up(r[-1], 1) if r[-1] is not None else None,
            "support": rnd(min(window)), "resistance": rnd(max(window)), "trend_pct": round_half_up(slope_pct(values), 1) if slope_pct(values) is not None else None,
            "change_30d": rnd((last - values[-31]) / values[-31] * 100) if len(values) > 30 and values[-31] else None,
            "score": score, "label": label, "reasons": reasons,
            "buy_zone": rnd(max(min(window), lo[-1]) if lo[-1] is not None else min(window)),
            "sell_zone": rnd(min(max(window), up[-1]) if up[-1] is not None else max(window)),
            "crosses": [(c[0].isoformat(), c[1], rnd(c[2])) for c in cr],
            "lines": {"sma_short": [(d, v) for (d, _p), v in zip(days, s)], "sma_long": [(d, v) for (d, _p), v in zip(days, l_)],
                      "band_up": [(d, v) for (d, _p), v in zip(obs, up)], "band_low": [(d, v) for (d, _p), v in zip(obs, lo)]}}


# ---------- position ----------
def position(branch_id, code, last, cfg, entry=None):
    """Stock on hand, its cost per kg and the profit / loss at the latest base price; take profit and stop loss."""
    from .inventory import Inventory
    from .price import Price
    qty = Inventory.free_qty(branch_id, code)
    cost, cost_src = None, None
    if entry:
        cost, cost_src = float(entry), "typed"
    else:
        start = today_local() - timedelta(days=90)
        buys = _own_series(branch_id, code, "BY", start)
        if buys:
            from .buysell_order import BuySellOrder, CANCELLED
            kg = amount = 0.0
            for o in col(BuySellOrder.COL).find({"BRANCH_ID": branch_id, "ORD_TYPE": "BY", "ORD_STATUS": {"$ne": CANCELLED},
                                                 "REGIST_DATE": {"$gte": to_utc_day(start)}}, NO_ID):
                for line in BuySellOrder.items_of(o):
                    q, p = float(line.get("ORD_QTY") or 0), float(line.get("UNIT_PRICE") or line.get("PRICING_UINT") or 0)
                    if line.get("MAT_CODE") == code and q > 0 and p > 0:
                        kg += q
                        amount += q * p
            if kg:
                cost, cost_src = amount / kg, "buy90"
        if cost is None:
            price = Price.get(branch_id, code) or {}
            if price.get("PRICE_BUY"):
                cost, cost_src = float(price["PRICE_BUY"]), "branch"
    if cost is None or last is None:
        return {"qty": qty, "cost": None, "cost_src": None}
    tp, sl = cost * (1 + cfg["TP_PCT"] / 100.0), cost * (1 - cfg["SL_PCT"] / 100.0)
    pl_kg = last - cost
    state = ("nostock" if qty <= 0 else "tp" if last >= tp else "sl" if last <= sl else
             ("gain" if pl_kg > 0 else "loss" if pl_kg < 0 else "even"))
    rnd = lambda x: round_half_up(x, 2)
    return {"qty": qty, "cost": rnd(cost), "cost_src": cost_src, "tp": rnd(tp), "sl": rnd(sl), "pl_kg": rnd(pl_kg),
            "pl_pct": round_half_up(pl_kg / cost * 100, 1) if cost else None, "pl_total": rnd(pl_kg * qty), "state": state}


# ---------- update 75: the mill's own buying and selling in AOI ----------
def trading(branch_id, code, start, end, market_days=None, stock_qty=None):
    """What the mill bought and sold of a SKU in AOI between `start` and `end` (dates, local): kg, average THB/kg,
    orders, customers, margin, the gap to the market on the same days, and a bar per week (month for 3 years).
    Customer selling orders (BY) are the mill buying, customer buying orders (SL) the mill selling; cancelled
    orders are left out, open ones count (registered = agreed price)."""
    from .buysell_order import BuySellOrder, CANCELLED
    market = {d: v for d, v in (market_days or [])}
    monthly = (end - start).days > 400
    side = {"BY": {"kg": 0.0, "thb": 0.0, "orders": 0, "cust": set(), "mkt_kg": 0.0, "mkt_thb": 0.0},
            "SL": {"kg": 0.0, "thb": 0.0, "orders": 0, "cust": set(), "mkt_kg": 0.0, "mkt_thb": 0.0}}
    buckets, sold_30 = {}, 0.0
    since30 = end - timedelta(days=30)
    for o in col(BuySellOrder.COL).find({"BRANCH_ID": branch_id, "ORD_TYPE": {"$in": ["BY", "SL"]}, "ORD_STATUS": {"$ne": CANCELLED},
                                         "REGIST_DATE": {"$gte": to_utc_day(start)}}, NO_ID):
        day = to_local(o["REGIST_DATE"]).date()
        if day > end:
            continue
        lines = [l for l in BuySellOrder.items_of(o) if l.get("MAT_CODE") == code and float(l.get("ORD_QTY") or 0) > 0]
        if not lines:
            continue
        sd = side[o["ORD_TYPE"]]
        sd["orders"] += 1
        sd["cust"].add(o.get("CUST_CODE"))
        key = day.replace(day=1) if monthly else day - timedelta(days=day.weekday())
        b = buckets.setdefault(key, {"BY": [0.0, 0.0], "SL": [0.0, 0.0]})
        for l in lines:
            qty = float(l["ORD_QTY"])
            thb = float(l.get("ORD_AMOUNT") or 0) or qty * float(l.get("UNIT_PRICE") or l.get("PRICING_UINT") or 0)
            sd["kg"] += qty
            sd["thb"] += thb
            b[o["ORD_TYPE"]][0] += qty
            b[o["ORD_TYPE"]][1] += thb
            if day in market:
                sd["mkt_kg"] += qty
                sd["mkt_thb"] += qty * market[day]
            if o["ORD_TYPE"] == "SL" and day > since30:
                sold_30 += qty
    rnd = lambda x, n=2: round_half_up(x, n) if x is not None else None
    out = {}
    for k, sd in side.items():
        avg = sd["thb"] / sd["kg"] if sd["kg"] else None
        mkt = sd["mkt_thb"] / sd["mkt_kg"] if sd["mkt_kg"] else None
        out[k] = {"kg": rnd(sd["kg"], 1), "thb": rnd(sd["thb"]), "avg": rnd(avg), "orders": sd["orders"], "customers": len(sd["cust"]),
                  "market_avg": rnd(mkt), "vs_market_pct": rnd((avg - mkt) / mkt * 100, 1) if avg and mkt else None}
    buy, sell = out["BY"], out["SL"]
    margin = (sell["avg"] - buy["avg"]) if sell["avg"] and buy["avg"] else None
    daily_sold = sold_30 / 30.0
    rows = []
    keys, k = [], (start.replace(day=1) if monthly else start - timedelta(days=start.weekday()))
    while k <= end:                                       # every week / month of the period, also the empty ones
        keys.append(k)
        k = (k.replace(day=28) + timedelta(days=4)).replace(day=1) if monthly else k + timedelta(days=7)
    for key in keys:
        b = buckets.get(key, {"BY": [0.0, 0.0], "SL": [0.0, 0.0]})
        rows.append({"label": key.strftime("%m/%y" if monthly else "%d/%m"), "start": key.isoformat(),
                     "buy_kg": rnd(b["BY"][0], 1), "sell_kg": rnd(b["SL"][0], 1),
                     "buy_avg": rnd(b["BY"][1] / b["BY"][0]) if b["BY"][0] else None,
                     "sell_avg": rnd(b["SL"][1] / b["SL"][0]) if b["SL"][0] else None})
    return {"buy": buy, "sell": sell, "margin_kg": rnd(margin), "margin_pct": rnd(margin / buy["avg"] * 100, 1) if margin is not None else None,
            "gross_profit": rnd(margin * sell["kg"]) if margin is not None else None, "net_kg": rnd((buy["kg"] or 0) - (sell["kg"] or 0), 1),
            "sold_per_day": rnd(daily_sold, 1), "cover_days": rnd(stock_qty / daily_sold, 0) if stock_qty and daily_sold else None,
            "unit": "month" if monthly else "week", "rows": rows,
            "max_kg": max([r["buy_kg"] for r in rows] + [r["sell_kg"] for r in rows] + [0])}


def trade_notes(tr):
    """Plain findings from the mill's own trading (text keys + values), shown under the trading card and given to the AI."""
    notes = []
    b, s = tr["buy"], tr["sell"]
    if b["vs_market_pct"] is not None and b["vs_market_pct"] >= 3:
        notes.append(("psTnBuyHigh", {"pct": "%+.1f" % b["vs_market_pct"]}))
    elif b["vs_market_pct"] is not None and b["vs_market_pct"] <= -3:
        notes.append(("psTnBuyLow", {"pct": "%+.1f" % b["vs_market_pct"]}))
    if s["vs_market_pct"] is not None and s["vs_market_pct"] <= -3:
        notes.append(("psTnSellLow", {"pct": "%+.1f" % s["vs_market_pct"]}))
    if tr["margin_kg"] is not None and tr["margin_kg"] <= 0:
        notes.append(("psTnNoMargin", {"m": "%.2f" % tr["margin_kg"]}))
    if tr["cover_days"] is not None and tr["cover_days"] > 60:
        notes.append(("psTnSlowStock", {"d": "%.0f" % tr["cover_days"]}))
    if not b["kg"] and not s["kg"]:
        notes.append(("psTnNone", {}))
    return notes


# ---------- the screen ----------
def analyse(branch_id, code, period="12M", cfg=None, entry=None, export_keys=None):
    cfg = cfg or settings()
    end = today_local()
    shown_from = end - timedelta(days=PERIODS.get(period, 365))
    start = shown_from - timedelta(days=max(cfg["SMA_LONG"], RANGE_DAYS) + 5)   # warm-up for the averages
    found = sources(branch_id, code, start, cfg, export_keys)
    base = base_of(code, found, cfg)
    days = daily(found.get(base) or [], end)
    obs = sorted({d: v for d, v in (found.get(base) or [])}.items())
    obs = [(date.fromisoformat(d), v) for d, v in obs]
    sig = signal(days, cfg, obs) if base else None
    chart = {"start": shown_from.isoformat(), "end": end.isoformat(), "base": base,
             "sources": {k: [[d, v] for d, v in pts if d >= shown_from.isoformat()] for k, pts in found.items()}}
    if sig:
        rnd = lambda x: round_half_up(x, 2) if x is not None else None
        chart["lines"] = {name: [[d.isoformat(), rnd(v)] for d, v in vals if v is not None and d >= shown_from]
                          for name, vals in sig.pop("lines").items()}
        chart["marks"] = [c for c in sig["crosses"] if c[0] >= shown_from.isoformat()]
    pos = position(branch_id, code, sig["last"] if sig else None, cfg, entry)
    if sig and pos.get("cost"):
        chart["levels"] = {"cost": pos["cost"], "tp": pos["tp"], "sl": pos["sl"]}
    latest = {k: {"last": pts[-1][1], "day": pts[-1][0], "change_30d": _change(pts)} for k, pts in found.items()}
    # update 75: the mill's own buying and selling in AOI over the period shown, against the base market price
    market_days = [(d, v) for d, v in days] if base not in ("BUY", "SELL") else []
    trade = trading(branch_id, code, shown_from, end, market_days, pos.get("qty"))
    trade["notes"] = trade_notes(trade)
    spark = [v for d, v in days[-90:]][::3] + ([days[-1][1]] if days else [])  # last 90 days, every 3rd day
    return {"code": code, "name": var.mat_name(code), "base": base, "signal": sig, "position": pos, "chart": chart,
            "latest": latest, "period": period, "spark": spark, "trade": trade}


def _change(pts):
    if len(pts) < 2:
        return None
    last_day = date.fromisoformat(pts[-1][0])
    old = next((v for d, v in reversed(pts) if (last_day - date.fromisoformat(d)).days >= 30), None)
    return round_half_up((pts[-1][1] - old) / old * 100, 1) if old else None


def overview(branch_id, cfg=None):
    """One line per traded SKU (no chart): base source, last price, signal, profit / loss. For the AI page and the AI."""
    cfg = cfg or settings()
    keys = [i["key"] for i in export_price.items()]
    out = []
    for code in var.TRADED:
        a = analyse(branch_id, code, "6M", cfg, export_keys=keys)
        s, p = a["signal"] or {}, a["position"]
        out.append({"code": code, "name": a["name"], "base": a["base"], "last": s.get("last"), "label": s.get("label"),
                    "score": s.get("score"), "rsi": s.get("rsi"), "trend_pct": s.get("trend_pct"), "buy_zone": s.get("buy_zone"),
                    "sell_zone": s.get("sell_zone"), "qty": p.get("qty"), "cost": p.get("cost"), "pl_pct": p.get("pl_pct") if p.get("qty") else None,
                    "pl_total": p.get("pl_total") if p.get("qty") else None, "state": p.get("state"), "tp": p.get("tp"), "sl": p.get("sl"),
                    "sources": sorted(a["latest"]), "spark": a["spark"], "support": s.get("support"),
                    "resistance": s.get("resistance"), "change_30d": s.get("change_30d"),
                    # update 75: the mill's own trading over 6 months
                    "own_buy_kg": a["trade"]["buy"]["kg"], "own_buy_avg": a["trade"]["buy"]["avg"],
                    "own_buy_vs_market_pct": a["trade"]["buy"]["vs_market_pct"], "own_sell_kg": a["trade"]["sell"]["kg"],
                    "own_sell_avg": a["trade"]["sell"]["avg"], "own_sell_vs_market_pct": a["trade"]["sell"]["vs_market_pct"],
                    "own_margin_kg": a["trade"]["margin_kg"], "own_gross_profit": a["trade"]["gross_profit"],
                    "stock_cover_days": a["trade"]["cover_days"]})
    return out


def warnings(overview_rows):
    """Update 77: [(text key, params)] for the Price analysis page - grades too close to jasmine, and a source far
    (> 25%) from the AI web price of the same SKU."""
    from . import ai_price
    out = []
    last = {o["code"]: o["last"] for o in overview_rows if o.get("last")}
    for low, high, pct in quality_check(last):
        out.append(("psWarnQuality", {"low": var.mat_name(low), "high": var.mat_name(high), "pct": "%.0f" % pct}))
    web = ai_price.latest()
    for o in overview_rows:
        w = web.get(o["code"])
        if w and o.get("last") and o.get("base") not in ("AIWEB", "AIHIST") and abs(o["last"] - w["MID"]) > w["MID"] * 0.25:
            out.append(("psWarnFarFromWeb", {"name": o["name"], "src": o.get("base") or "-", "v": "%.2f" % o["last"],
                                             "web": "%.2f-%.2f" % (w["LOW"], w["HIGH"])}))
    return out


def for_ai(branch_id):
    """Compact summary for the AI question (no personal data)."""
    try:
        cfg = settings()
        return {"fx_usd_thb": cfg.get("FX_USD_THB"), "take_profit_pct": cfg["TP_PCT"], "stop_loss_pct": cfg["SL_PCT"],
                "skus": [{k: v for k, v in o.items() if k != "spark" and (v not in (None, [], 0) or k == "score")}
                         for o in overview(branch_id, cfg) if o["base"] or o["own_buy_kg"] or o["own_sell_kg"]]}
    except Exception:                                     # noqa: BLE001
        return None
