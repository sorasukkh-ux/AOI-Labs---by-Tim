"""Service > Service Status (the TV screen), Current Price and Service Dashboard. Read-only screens of the
branch signed in to; each links to the screen where the data is changed."""
import math
from datetime import datetime, time, timezone

from flask import Blueprint, render_template, request

from ..i18n import t
from ..models.base import col, NO_ID
from ..models.buysell_order import BuySellOrder, CLOSED as BS_CLOSED, SELL, BUY
from ..models.customer import Customer
from ..models.milling_order import MillingOrder
from ..models.price import Price
from ..models.rules import status_flow as flow
from ..security import operating_branch, require
from ..timeutil import fmt_datetime, fmt_time, local_tz, now_local, to_local
from .common import branch_label
from ..models.variety import mat_name, variety_of

bp = Blueprint("service_info", __name__, url_prefix="/service")
TV_SECTIONS = ((flow.FINISHED, "completed", "tvCompleted"), (flow.IN_PROCESS, "inprocess", "tvInProcess"), (flow.QUEUE, "queue", "tvQueue"))
TV_LIMIT = 20
STATUS_SEGMENTS = ((flow.QUEUE, "tvQueue", "var(--fc-blue)"), (flow.IN_PROCESS, "tvInProcess", "var(--fc-teal)"),
                   (flow.HOLD, "tvHold", "var(--fc-purple)"), (flow.FINISHED, "tvCompleted", "var(--fc-green)"),
                   (flow.CLOSED, "tvClosed", "var(--fc-sub)"))


def _start_of_today_utc():
    return datetime.combine(now_local().date(), time.min, tzinfo=local_tz()).astimezone(timezone.utc)


@bp.route("/status")
def tv():
    """Service Status is part of Current Services since update 26."""
    from flask import redirect, url_for
    return redirect(url_for("service_info.current_price", _anchor="cs-status"))


def _status_sections():
    """Finished, in process and queued orders of the branch (the former Service Status)."""
    orders = MillingOrder.sort_for_list(MillingOrder.for_branch(operating_branch(), [s for s, _c, _k in TV_SECTIONS]))
    names = {}
    sections = []
    for status, css, key in TV_SECTIONS:
        rows = []
        for o in [o for o in orders if o["MILL_STATUS"] == status][:TV_LIMIT]:
            if o["CUST_CODE"] not in names:
                names[o["CUST_CODE"]] = (Customer.find(o["CUST_CODE"]) or {}).get("CUST_NAME", o["CUST_CODE"])
            rows.append({"no": o["QR_ID"], "customer": names[o["CUST_CODE"]], "time": fmt_datetime(o["EXPCT_RECEIVE_TIME"]),
                         "delivery": o["ORDER_TYPE"] == flow.DELIVER})
        sections.append({"css": css, "key": key, "rows": rows})
    return sections


@bp.route("/price")
@require("SV_PRICE")
def current_price():
    """Current Services: today's buying / selling prices and the milling queue (finished, in process, queued)."""
    rows = [{"name": mat_name(p["MAT_CODE"]), "variety": variety_of(p["MAT_CODE"]), "buy": p.get("PRICE_BUY") or 0, "sell": p.get("PRICE_SELL") or 0,
             "waiting": p.get("PRICE_STATUS") == "W"} for p in Price.for_branch(operating_branch())]
    return render_template("service/current_price.html", rows=rows, sections=_status_sections(),
                           branch=branch_label(operating_branch()))


def _donut(segments, cx=80, cy=80, r=64, inner=34):
    total = sum(s["value"] for s in segments) or 1
    start = -math.pi / 2
    pt = lambda radius, angle: "%.2f,%.2f" % (cx + radius * math.cos(angle), cy + radius * math.sin(angle))
    for s in segments:
        frac = s["value"] / total
        s["full"] = frac >= 0.9999
        end = start + frac * 2 * math.pi
        large = 1 if frac > 0.5 else 0
        s["d"] = "M%s A%d,%d 0 %d 1 %s L%s A%d,%d 0 %d 0 %s Z" % (pt(r, start), r, r, large, pt(r, end), pt(inner, end),
                                                                inner, inner, large, pt(inner, start))
        start = end
    return segments


def _rate_points(orders, w=360, h=150, pad_l=36, pad_r=16, pad_b=28, pad_t=16):
    """The design's rate trend: price per kg of the last orders, oldest left."""
    if not orders:
        return None
    rates = [o["PRICING_UINT"] for o in orders]
    low, high = min(rates), max(rates)
    span = (high - low) or 1
    low, high = low - span * 0.3, high + span * 0.3
    plot_w, plot_h, n = w - pad_l - pad_r, h - pad_t - pad_b, len(orders)
    points = []
    for i, o in enumerate(orders):
        x = pad_l + (plot_w / 2 if n == 1 else plot_w / (n - 1) * i)
        y = pad_t + plot_h - (o["PRICING_UINT"] - low) / (high - low) * plot_h
        points.append({"x": round(x, 1), "y": round(y, 1), "rate": o["PRICING_UINT"], "time": fmt_time(o["REGIST_DATE"]),
                       "name": mat_name(o["MAT_CODE"]), "qr": o["QR_ID"]})
    path = " ".join(("M" if i == 0 else "L") + "%.1f,%.1f" % (p["x"], p["y"]) for i, p in enumerate(points))
    # a line only joins orders of one product; mixed products are shown as labelled dots
    return {"points": points, "path": path, "base": pad_t + plot_h, "same": len({o["MAT_CODE"] for o in orders}) == 1,
            "area": "%s L%.1f,%d L%.1f,%d Z" % (path, points[-1]["x"], pad_t + plot_h, points[0]["x"], pad_t + plot_h)}


@bp.route("/dashboard")
def dashboard():
    """Moved to Administration > Milling > Milling Analysis (update 25)."""
    from flask import redirect, url_for
    return redirect(url_for("admin_milling.analysis", tab="trade" if request.args.get("view") == "trade" else "today"))


def dashboard_context(view):
    """Today's milling orders (view 'milling') or today's buying / selling (view 'trade') of the branch."""
    branch = operating_branch()
    start = _start_of_today_utc()
    context = {"view": view}
    if view == "milling":
        today = list(col(MillingOrder.COL).find({"BRANCH_ID": branch, "REGIST_DATE": {"$gte": start}}, NO_ID).sort([("REGIST_DATE", 1)]))
        open_orders = MillingOrder.for_branch(branch, (flow.QUEUE, flow.IN_PROCESS, flow.HOLD, flow.FINISHED, flow.DELIVERY))
        counts = {}
        for o in today:
            counts[o["MILL_STATUS"]] = counts.get(o["MILL_STATUS"], 0) + 1
        segments = _donut([{"status": s, "label": t(k), "color": c, "value": counts.get(s, 0)} for s, k, c in STATUS_SEGMENTS
                           if counts.get(s, 0) > 0])
        colors = {s: c for s, _k, c in STATUS_SEGMENTS}
        bars = [{"qr": o["QR_ID"], "time": fmt_time(o["REGIST_DATE"]), "color": colors.get(o["MILL_STATUS"], "var(--fc-sub)"),
                 "status": t({flow.DELIVERY: "tvReadyForDelivery", flow.CANCELLED: "tvCancelled"}.get(o["MILL_STATUS"], dict((s, k) for s, k, _c in STATUS_SEGMENTS).get(o["MILL_STATUS"], "tvQueue"))),
                 "kg": o["REGIST_PADDY"]} for o in today][-12:]
        context.update(total=len(today), in_queue=sum(1 for o in open_orders if o["MILL_STATUS"] == flow.QUEUE),
                       in_process=sum(1 for o in open_orders if o["MILL_STATUS"] == flow.IN_PROCESS),
                       completed=counts.get(flow.FINISHED, 0) + counts.get(flow.DELIVERY, 0) + counts.get(flow.CLOSED, 0),
                       segments=segments, bars=bars, status_legend=[(t(k), c) for _s, k, c in STATUS_SEGMENTS])
    else:
        closed_today = list(col(BuySellOrder.COL).find({"BRANCH_ID": branch, "ORD_STATUS": BS_CLOSED, "LAST_UPDATE": {"$gte": start}}, NO_ID))
        sells = [o for o in closed_today if o["ORD_TYPE"] == SELL]
        buys = [o for o in closed_today if o["ORD_TYPE"] == BUY]
        total_sell = sum(BuySellOrder.amount(o) for o in sells)
        total_buy = sum(BuySellOrder.amount(o) for o in buys)

        def avg(rows):
            qty = sum(o["ORD_QTY"] for o in rows)
            return (sum(BuySellOrder.amount(o) for o in rows) / qty) if qty else 0.0

        def recent(kind):
            rows = list(col(BuySellOrder.COL).find({"BRANCH_ID": branch, "ORD_TYPE": kind, "REF_ORD_ID": None,
                                                    "ORD_STATUS": {"$ne": "CC"}}, NO_ID).sort([("REGIST_DATE", -1)]).limit(8))
            return list(reversed(rows))
        top = max(total_sell, total_buy, 1)
        context.update(total_sell=total_sell, total_buy=total_buy, avg_sell=avg(sells), avg_buy=avg(buys),
                       h_sell=total_sell / top * 94, h_buy=total_buy / top * 94,
                       sell_trend=_rate_points(recent(SELL)), buy_trend=_rate_points(recent(BUY)))
    return context


# ---------- Milling Analysis (update 22): the status timeline of the orders of a period ----------
ANALYSIS = "AD_MILLANALYSIS"
CSV_ORDER_COLS = ("ORD_ID", "QR_ID", "REGIST_DATE", "CUST_CODE", "CUST_NAME", "RICE_VARIETY", "ORDER_TYPE", "MILL_STATUS",
                  "REGIST_PADDY", "ACT_PADDY", "EXPCT_RICE", "ACT_RICE", "YIELD_PCT", "EXPECTED_PCT", "QR_SCANNED",
                  "WAIT_MIN", "MILLING_MIN", "HOLD_COUNT", "HOLD_MIN", "READY_MIN", "PICKUP_MIN")
CSV_EVENT_COLS = ("EVENT_ID", "ORD_ID", "QR_ID", "EVENT_DATE", "CUST_CODE", "RICE_VARIETY", "ORDER_TYPE", "FROM_STATUS",
                  "TO_STATUS", "MINUTES_IN_FROM", "REASON", "SCAN_FLG", "REGIST_PADDY", "ACT_PADDY", "ACT_RICE", "OPE_USER", "SOURCE")


def _csv(name, header, rows):
    import csv
    import io
    from flask import Response
    out = io.StringIO()
    out.write("﻿")                                   # Excel opens Thai text correctly with the BOM
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=%s" % name})


@bp.route("/analysis")
def analysis():
    """Moved to Administration > Milling > Milling Analysis (update 25)."""
    from flask import redirect, url_for
    return redirect(url_for("admin_milling.analysis", tab="period", **request.args.to_dict()))


def analysis_view():
    """The period analysis: a CSV Response (?csv=orders|events) or the template context."""
    from ..models import mill_analysis as ma
    from ..models import variety as var
    branch = operating_branch()
    start, end = ma.period(request.args.get("start"), request.args.get("end"))
    variety = request.args.get("variety") if request.args.get("variety") in var.VARIETIES else None
    result = ma.analyse(branch, start, end, variety)
    names = {}

    def cust_name(code):
        if code not in names:
            names[code] = (Customer.find(code) or {}).get("CUST_NAME", code)
        return names[code]
    stamp = "%s_%s_%s" % (branch, start.isoformat(), end.isoformat())
    kind = request.args.get("csv")
    if kind == "orders":
        def local(value):
            return to_local(value).strftime("%Y-%m-%d %H:%M") if value else ""
        rows = [(r["ord_id"], r["qr"], local(r["regist"]), r["cust"], cust_name(r["cust"]), r["variety"], r["type"], r["status"],
                 r["regist_paddy"], r["act_paddy"] or "", r["expct_rice"], r["act_rice"] or "", ma.pct(r["yield"]) or "",
                 ma.pct(r["expected"]) or "", "Y" if r["scanned"] else "N", r["wait"] if r["wait"] is not None else "",
                 r["milling"] if r["milling"] is not None else "", r["hold_count"], r["hold_min"],
                 r["ready"] if r["ready"] is not None else "", r["pickup"] if r["pickup"] is not None else "")
                for r in result["rows"]]
        return _csv("milling_orders_%s.csv" % stamp, CSV_ORDER_COLS, rows)
    if kind == "events":
        events = ma.events_between(branch, start, end)
        rows = [tuple((to_local(e[c]).strftime("%Y-%m-%d %H:%M:%S") if c == "EVENT_DATE" else
                       ("" if e.get(c) is None else e.get(c))) for c in CSV_EVENT_COLS) for e in events]
        return _csv("milling_status_events_%s.csv" % stamp, CSV_EVENT_COLS, rows)
    return dict(start=start, end=end, variety=variety, result=result, cust_name=cust_name, pct=ma.pct,
                branch_name=branch_label(branch))
