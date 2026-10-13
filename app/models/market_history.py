"""Market price history of every SKU (Financial > Pricing Maintenance > Market Price, the graph).

Where each day's price comes from, by the SKU's source (AOIMS_MKMAP.SOURCE):
  * NABC: the stored NABC daily prices (AOITS_NABC_PRICE) of the SKU's product, the same statistic as the market
    price (median by default), THB/kg. The NABC API keeps its prices from January 2025, so "download history"
    reads every page of the product once (about 26 pages for ข้าวเปลือกเจ้าหอมมะลิ 105).
  * TRMA: the association's reports stored in AOITS_MKREPORT, read with the SKU's current row mapping. Older reports
    that the association's page still lists can be downloaded as history (they are stored, not applied).
  * HAND: the market price typed on Pricing Maintenance, from AOILOG_PRICE of the branch.
Periods: 3, 6, 12 or 36 months back from today. The history only goes as far back as the sources have data.
"""
import statistics
from datetime import timedelta

from .base import col, NO_ID, ValidationError, round_half_up
from . import nabc_price, variety as var
from ..timeutil import now_utc, to_local, today_local

PERIODS = {"3M": 3, "6M": 6, "12M": 12, "36M": 36}
HISTORY_PAGES = 400
HISTORY_REPORTS = 60


def start_day(period):
    return today_local() - timedelta(days=round(PERIODS.get(period, 12) * 30.44))


def _nabc_series(mapping, start):
    product = mapping.get("NABC_PRODUCT")
    if not product:
        return []
    provinces = nabc_price.provinces_of(mapping.get("NABC_PROVINCES"))
    stat = mapping.get("NABC_STAT") or "median"
    days = {}
    for r in col(nabc_price.COL).find({"PRODUCT_NAME": product, "DATA_DATE": {"$gte": start.isoformat()}}, NO_ID):
        if provinces and r["PROVINCE"] not in provinces:
            continue
        factor = nabc_price.unit_factor(r.get("UNIT"))
        if factor:
            days.setdefault(r["DATA_DATE"], []).append(r["DAY_PRICE"] * factor)
    return [(day, round_half_up(nabc_price.stat_of(values, stat), 2)) for day, values in sorted(days.items())]


def _trma_series(mapping, start, reports):
    from .market import MarketMap, unit_factor, value_of
    by_day = {}
    for rep in reports:
        when = rep.get("REPORT_DATE") or rep.get("FETCH_DATE")
        if not when:
            continue
        day = to_local(when).date()
        if day < start:
            continue
        row = MarketMap.match(mapping, rep.get("ROWS") or [])
        raw = value_of(row, mapping.get("COL_INDEX") or 0, mapping.get("COL_PICK") or "avg")
        factor = unit_factor(mapping.get("UNIT") or "auto", raw)
        if raw is not None and factor:
            by_day[day.isoformat()] = round_half_up(raw * factor, 2)       # the newest report of a day wins
    return sorted(by_day.items())


def _hand_series(code, branch_id, start):
    from .price import Price
    by_day = {}
    for log in col(Price.LOG).find({"BRANCH_ID": branch_id, "MAT_CODE": code}, NO_ID).sort([("LOG_DATE", 1)]):
        day = to_local(log["LOG_DATE"]).date()
        if day >= start and log.get("MKPRICE_BUY"):
            by_day[day.isoformat()] = round_half_up(float(log["MKPRICE_BUY"]), 2)
    return sorted(by_day.items())


def series(branch_id, period="12M", codes=None):
    """{'start', 'skus': [{'code', 'name', 'variety', 'kind', 'source', 'points': [[day, kg], ...], 'last', 'change'}]}."""
    from .market import MarketMap, MarketReport
    start = start_day(period)
    reports = None
    out = []
    for m in MarketMap.all():
        code = m["MAT_CODE"]
        if codes and code not in codes:
            continue
        if m["SOURCE"] == "NABC":
            points = _nabc_series(m, start)
        elif m["SOURCE"] == "TRMA":
            if reports is None:
                reports = list(col(MarketReport.COL).find({"REPORT_STATUS": "OK"}, NO_ID).sort([("FETCH_DATE", 1)]))
            points = _trma_series(m, start, reports)
        else:
            points = _hand_series(code, branch_id, start)
        first, last = (points[0][1], points[-1][1]) if points else (None, None)
        out.append({"code": code, "name": var.mat_name(code), "variety": var.variety_of(code), "kind": var.kind_of(code),
                    "source": m["SOURCE"], "points": [list(p) for p in points], "last": last,
                    "change": round_half_up((last - first) / first * 100, 1) if first else None,
                    "low": min(p[1] for p in points) if points else None, "high": max(p[1] for p in points) if points else None,
                    "avg": round_half_up(statistics.mean(p[1] for p in points), 2) if points else None})
    return {"start": start.isoformat(), "end": today_local().isoformat(), "period": period, "skus": out}


def download_nabc_history(products=None):
    """Reads every page of the NABC daily prices of the mapped products (stored once; known records are skipped).
    Returns {product: records saved}."""
    from .market import MarketMap
    products = products or sorted({m["NABC_PRODUCT"] for m in MarketMap.all() if m["SOURCE"] == "NABC" and m.get("NABC_PRODUCT")})
    out = {}
    for product in products:
        body = nabc_price._get(product, 1)
        rows, page, saved = body["data"], 1, 0
        per_page = max(1, len(rows))
        total_pages = max(1, -(-int((body.get("pagination") or {}).get("total") or per_page) // per_page))
        while rows:
            saved += sum(1 for rec in rows if nabc_price._store(rec, product))
            page += 1
            if page > min(total_pages, HISTORY_PAGES):
                break
            rows = nabc_price._get(product, page)["data"]
        out[product] = saved
    return out


def download_trma_history(ope_user, limit=HISTORY_REPORTS):
    """Older association reports still listed on its price page: stored for the graph (HISTORY), not applied.
    Returns how many were stored."""
    from flask import current_app
    from .market import MarketReport, find_reports, http_get, parse_text, pdf_text
    page_url = current_app.config["MARKET_URL"]
    try:
        html = http_get(page_url).decode("utf-8", "replace")
    except Exception:                                     # noqa: BLE001
        raise ValidationError("mkErrDownload")
    known = {r.get("SOURCE_URL") for r in col(MarketReport.COL).find({}, {"_id": 0, "SOURCE_URL": 1})}
    stored = 0
    for url, date in find_reports(html, page_url):
        if url in known or stored >= limit:
            continue
        try:
            data = http_get(url)
            rows = parse_text(pdf_text(data)) if data.startswith(b"%PDF") else []
        except Exception:                                 # noqa: BLE001 - one bad report does not stop the others
            continue
        if rows:
            MarketReport._record({"SOURCE_URL": url, "REPORT_DATE": date, "REPORT_STATUS": "OK", "ERROR_KEY": None,
                                  "ROWS": rows, "HISTORY": True}, ope_user)
            stored += 1
    return stored


def history_info():
    """How far back the stored data goes: {'nabc_from', 'nabc_rows', 'reports', 'reports_from'}."""
    from .market import MarketReport
    first = col(nabc_price.COL).find_one({}, NO_ID, sort=[("DATA_DATE", 1)])
    reps = list(col(MarketReport.COL).find({"REPORT_STATUS": "OK"}, {"_id": 0, "REPORT_DATE": 1, "FETCH_DATE": 1}))
    dates = sorted(to_local(r.get("REPORT_DATE") or r["FETCH_DATE"]).date() for r in reps if r.get("REPORT_DATE") or r.get("FETCH_DATE"))
    return {"nabc_from": first["DATA_DATE"] if first else None, "nabc_rows": col(nabc_price.COL).count_documents({}),
            "reports": len(reps), "reports_from": dates[0].isoformat() if dates else None}
