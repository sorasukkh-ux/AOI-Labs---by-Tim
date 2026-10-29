"""Milling Analysis: what the status timeline (AOILOG_MILLSTATUS) says about the orders of a period.

Per order (registered in the period):
  wait     = minutes from registration (Queue) to the first In process (QR scanned)
  milling  = minutes spent In process (all spells)
  hold     = minutes spent on Hold, and how many times it was held
  ready    = minutes from registration to Finished
  pickup   = minutes from Finished to Closed (take-away) / Ready for Delivery to Closed (delivery)
  yield    = rice received at Finished / weighed paddy (registered paddy when it was not weighed)
  expected = expected rice / registered paddy
Summaries are averages over the orders that have the value, per customer and per rice variety.
"""
from datetime import datetime, time, timedelta, timezone

from .base import col, NO_ID, round_half_up
from . import mill_event
from ..timeutil import local_tz

DONE = ("F", "D", "C")


def period(start_text, end_text, default_days=30):
    """(start date, end date) from yyyy-mm-dd texts; the last 30 days when missing or wrong."""
    from ..timeutil import today_local
    today = today_local()

    def parse(text):
        try:
            return datetime.strptime(text or "", "%Y-%m-%d").date()
        except ValueError:
            return None
    end = parse(end_text) or today
    start = parse(start_text) or (end - timedelta(days=default_days - 1))
    if start > end:
        start, end = end, start
    return start, end


def day_bounds_utc(start, end):
    """UTC [from, to) of the local days start..end."""
    tz = local_tz()
    lo = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return lo, hi


def _first(events, status):
    return next((e["EVENT_DATE"] for e in events if e["TO_STATUS"] == status), None)


def _minutes(a, b):
    return round((b - a).total_seconds() / 60.0, 1) if a and b else None


def order_row(order, events):
    regist = order["REGIST_DATE"]
    started, finished = _first(events, "I"), _first(events, "F")
    closed = _first(events, "C")
    delivery = _first(events, "D")
    milling = sum(e["MINUTES_IN_FROM"] or 0 for e in events if e["FROM_STATUS"] == "I") if started else None
    if started and order["MILL_STATUS"] == "I" and events and events[-1]["TO_STATUS"] == "I":
        milling = None                                      # still milling: not a finished measurement
    holds = [e for e in events if e["TO_STATUS"] == "H"]
    hold_min = sum(e["MINUTES_IN_FROM"] or 0 for e in events if e["FROM_STATUS"] == "H")
    paddy = order.get("ACT_PADDY") or order.get("REGIST_PADDY") or 0
    rice = order.get("ACT_RICE")
    return {
        "ord_id": order["ORD_ID"], "qr": order.get("QR_ID"), "cust": order.get("CUST_CODE"),
        "variety": order.get("RICE_VARIETY") or "WH", "type": order.get("ORDER_TYPE"), "status": order["MILL_STATUS"],
        "regist": regist, "regist_paddy": order.get("REGIST_PADDY") or 0, "act_paddy": order.get("ACT_PADDY"),
        "act_rice": rice, "expct_rice": order.get("EXPCT_RICE") or 0, "scanned": any(e["SCAN_FLG"] for e in events),
        "wait": _minutes(regist, started), "milling": milling if finished else None,
        "hold_count": len(holds), "hold_min": hold_min if holds else 0.0,
        "ready": _minutes(regist, finished), "pickup": _minutes(delivery or finished, closed),
        "yield": (rice / paddy) if rice and paddy else None,
        "expected": (order["EXPCT_RICE"] / order["REGIST_PADDY"]) if order.get("REGIST_PADDY") else None,
        "cancelled": order["MILL_STATUS"] == "CC", "events": len(events),
    }


def _avg(values):
    values = [v for v in values if v is not None]
    return (sum(values) / len(values)) if values else None


def summarize(rows):
    done = [r for r in rows if r["status"] in DONE]
    return {
        "orders": len(rows), "done": len(done), "cancelled": sum(1 for r in rows if r["cancelled"]),
        "held": sum(1 for r in rows if r["hold_count"]), "scanned": sum(1 for r in rows if r["scanned"]),
        "paddy": sum((r["act_paddy"] or r["regist_paddy"]) for r in rows if not r["cancelled"]),
        "rice": sum(r["act_rice"] or 0 for r in rows),
        "wait": _avg(r["wait"] for r in rows), "milling": _avg(r["milling"] for r in rows),
        "ready": _avg(r["ready"] for r in rows), "pickup": _avg(r["pickup"] for r in rows),
        "hold_min": _avg(r["hold_min"] for r in rows if r["hold_count"]),
        "yield": _avg(r["yield"] for r in rows), "expected": _avg(r["expected"] for r in rows if r["yield"] is not None),
    }


def analyse(branch_id, start, end, variety=None):
    lo, hi = day_bounds_utc(start, end)
    query = {"BRANCH_ID": branch_id, "REGIST_DATE": {"$gte": lo, "$lt": hi}}
    if variety:
        query["RICE_VARIETY"] = variety
    orders = list(col("AOITS_ORDMILLING").find(query, NO_ID).sort([("REGIST_DATE", 1)]))
    rows = [order_row(o, mill_event.for_order(o["ORD_ID"])) for o in orders]
    groups = {}
    for key in ("cust", "variety"):
        found = {}
        for r in rows:
            found.setdefault(r[key], []).append(r)
        groups[key] = sorted(((k, summarize(v)) for k, v in found.items()), key=lambda kv: -kv[1]["orders"])
    return {"rows": rows, "total": summarize(rows), "by_customer": groups["cust"], "by_variety": groups["variety"]}


def events_between(branch_id, start, end):
    lo, hi = day_bounds_utc(start, end)
    return list(col(mill_event.COL).find({"BRANCH_ID": branch_id, "EVENT_DATE": {"$gte": lo, "$lt": hi}}, NO_ID)
                .sort([("EVENT_DATE", 1), ("EVENT_SEQ", 1)]))


def pct(value):
    return None if value is None else round_half_up(value * 100, 2)


# ---------- update 80: the dashboard view (charts instead of number tables) ----------
def _nice(value):
    """A round axis maximum >= value (1, 2, 2.5, 5 x 10^n)."""
    import math
    if not value or value <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def _yield_level(y, expected):
    if y is None or expected is None:
        return ""
    gap = (y - expected) * 100
    return "lvl-ok" if gap >= -0.5 else ("lvl-warn" if gap >= -2 else "lvl-crit")


def _arc(cx, cy, r, a0, a1):
    import math
    large = 1 if a1 - a0 > math.pi else 0
    x0, y0 = cx + r * math.sin(a0), cy - r * math.cos(a0)
    x1, y1 = cx + r * math.sin(a1), cy - r * math.cos(a1)
    return "M%.2f %.2f A%.2f %.2f 0 %d 1 %.2f %.2f" % (x0, y0, r, r, large, x1, y1)


def dashboard(result, start, end):
    """Geometry and figures for the Milling Analysis dashboard (drawn as SVG by the template)."""
    import math
    from ..timeutil import to_local
    rows, total = result["rows"], result["total"]
    # --- trend per day (per week when the period is longer than 62 days) ---
    days = (end - start).days + 1
    weekly = days > 62
    def bucket(d):
        return d - timedelta(days=d.weekday()) if weekly else d
    keys, d = [], bucket(start)
    while d <= end:
        keys.append(d)
        d += timedelta(days=7 if weekly else 1)
    grouped = {k: [] for k in keys}
    for r in rows:
        k = bucket(to_local(r["regist"]).date())
        if k in grouped:
            grouped[k].append(r)
    W, H, L, R, T, B = 720, 240, 58, 50, 14, 34
    pw, ph = W - L - R, H - T - B
    sums = [(k, summarize(grouped[k])) for k in keys]
    top = _nice(max([s["paddy"] for _, s in sums] + [1]))
    ys = [s["yield"] for _, s in sums if s["yield"] is not None]
    ylo = math.floor(min(ys + [total["expected"] or 1]) * 100 / 5) * 5 if ys else 40
    yhi = math.ceil(max(ys + [total["expected"] or 0]) * 100 / 5) * 5 if ys else 70
    if yhi - ylo < 10:
        ylo, yhi = max(0, ylo - 5), yhi + 5
    slot = pw / max(len(sums), 1)
    bw = max(2.0, min(28.0, slot * 0.62))
    label_every = max(1, int(math.ceil(len(sums) / 12.0)))
    bars, points = [], []
    for i, (k, s) in enumerate(sums):
        x = L + slot * i + (slot - bw) / 2
        h = s["paddy"] / top * ph if top else 0
        bars.append({"x": round(x, 2), "y": round(T + ph - h, 2), "w": round(bw, 2), "h": round(h, 2), "cx": round(x + bw / 2, 2),
                     "label": k.strftime("%d/%m"), "show": i % label_every == 0, "orders": s["orders"], "paddy": s["paddy"],
                     "rice": s["rice"], "yield": s["yield"]})
        if s["yield"] is not None:
            py = T + ph - (s["yield"] * 100 - ylo) / (yhi - ylo) * ph
            points.append((round(x + bw / 2, 2), round(py, 2)))
    exp_y = None
    if total["expected"] is not None:
        exp_y = round(T + ph - (total["expected"] * 100 - ylo) / (yhi - ylo) * ph, 2)
    trend = {"W": W, "H": H, "L": L, "R": R, "T": T, "ph": ph, "bars": bars, "weekly": weekly,
             "line": " ".join(("M" if i == 0 else "L") + "%s %s" % p for i, p in enumerate(points)), "dots": points,
             "ticks": [{"y": round(T + ph - ph * i / 4, 2), "kg": top * i / 4, "pct": ylo + (yhi - ylo) * i / 4} for i in range(5)],
             "exp_y": exp_y, "has": any(s["orders"] for _, s in sums)}
    # --- registrations by hour of the day ---
    hours = [0] * 24
    for r in rows:
        hours[to_local(r["regist"]).hour] += 1
    used = [h for h in range(24) if hours[h]]
    h0, h1 = (min(used + [6]), max(used + [19])) if used else (6, 19)
    hmax = max(hours) or 1
    hw = 360.0 / (h1 - h0 + 1)
    hourly = [{"h": h, "n": hours[h], "x": round((h - h0) * hw + 1.5, 2), "w": round(hw - 3, 2),
               "bh": round(hours[h] / hmax * 120, 2), "y": round(130 - hours[h] / hmax * 120, 2), "cx": round((h - h0) * hw + hw / 2, 2)}
              for h in range(h0, h1 + 1)]
    busiest = max(range(24), key=lambda h: hours[h]) if used else None
    # --- time from registration to pickup ---
    wait, mill, ready, pickup = (total[k] or 0 for k in ("wait", "milling", "ready", "pickup"))
    other = max(0.0, ready - wait - mill) if ready else 0.0
    stages = [("wait", wait), ("milling", mill), ("other", other), ("pickup", pickup)]
    span = sum(v for _, v in stages) or 1
    x, flow = 0.0, []
    for key, v in stages:
        w = v / span * 100
        flow.append({"key": key, "min": v, "x": round(x, 3), "w": round(w, 3)})
        x += w
    # --- rice varieties: share of the paddy, yield against expected ---
    paddy_all = sum(g["paddy"] for _, g in result["by_variety"]) or 0
    varieties, a = [], 0.0
    for key, g in result["by_variety"]:
        share = (g["paddy"] / paddy_all) if paddy_all else 0
        arc = None
        if share > 0:
            a1 = a + share * 2 * math.pi
            arc = "full" if share >= 0.999 else _arc(60, 60, 46, a, a1)
            a = a1
        varieties.append({"key": key, "g": g, "share": share, "arc": arc, "level": _yield_level(g["yield"], g["expected"]),
                          "active": g["orders"] - g["done"] - g["cancelled"]})
    # yield scale shared by the variety cards
    vals = [v for _, g in result["by_variety"] for v in (g["yield"], g["expected"]) if v is not None]
    vlo = (math.floor(min(vals) * 100 / 5) * 5 - 5) if vals else 40
    vhi = (math.ceil(max(vals) * 100 / 5) * 5 + 5) if vals else 70
    def on_scale(v):
        return None if v is None else round(min(100, max(0, (v * 100 - vlo) / (vhi - vlo) * 100)), 2)
    for v in varieties:
        v["y_pos"], v["e_pos"] = on_scale(v["g"]["yield"]), on_scale(v["g"]["expected"])
    tmax = max([g[k] or 0 for _, g in result["by_variety"] for k in ("wait", "milling")] + [1])
    for v in varieties:
        v["wait_w"] = round((v["g"]["wait"] or 0) / tmax * 100, 2)
        v["mill_w"] = round((v["g"]["milling"] or 0) / tmax * 100, 2)
    # --- top customers by paddy ---
    customers = sorted(result["by_customer"], key=lambda kv: -kv[1]["paddy"])[:8]
    cmax = max([g["paddy"] for _, g in customers] + [1])
    top_customers = [{"code": k, "g": g, "w": round(g["paddy"] / cmax * 100, 2), "level": _yield_level(g["yield"], g["expected"])}
                     for k, g in customers]
    active = total["orders"] - total["done"] - total["cancelled"]
    n = total["orders"] or 1
    status_bar = [{"key": "done", "n": total["done"], "w": round(total["done"] / n * 100, 2)},
                  {"key": "active", "n": active, "w": round(active / n * 100, 2)},
                  {"key": "cancelled", "n": total["cancelled"], "w": round(total["cancelled"] / n * 100, 2)}]
    scan_pct = total["scanned"] / total["orders"] if total["orders"] else None
    ring = 2 * math.pi * 30
    return {"trend": trend, "hourly": hourly, "busiest": busiest, "flow": flow, "flow_total": ready + pickup if ready else None,
            "varieties": varieties, "v_scale": (vlo, vhi), "customers": top_customers, "status_bar": status_bar,
            "yield_level": _yield_level(total["yield"], total["expected"]), "scan_pct": scan_pct,
            "scan_ring": {"c": round(ring, 2), "dash": round(ring * (scan_pct or 0), 2)},
            "recovery": (total["rice"] / total["paddy"]) if total["paddy"] else None}
