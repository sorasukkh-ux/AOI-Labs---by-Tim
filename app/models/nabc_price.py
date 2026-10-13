"""Daily farm prices of the National Agricultural Big Data Center (NABC, agriapi.nabc.go.th/home/price).

    GET {AOI_NABC_PRICE_URL}?product_name=<exact name>&page=<n>
    -> {"success": true, "data": [{"data_date": "2026-09-30", "product_category", "product_name", "market_name",
        "province", "day_price": 18000, "unit": "บาท/ตัน"}, ...], "pagination": {"limit": 100, "page": 1, "total": ...}}

One record per market (mill, cooperative, BAAC rice yard) and day, newest day first. The records are kept in
AOITS_NABC_PRICE. The daily check reads page 1 only: when its newest day is already stored, nothing else is
downloaded; when it is newer, the following pages are read until the stored days are reached.

The market price of a SKU = the median (or average / low / high) of the newest day's prices of the chosen
product, optionally only from some provinces, converted to THB/kg (บาท/ตัน x 0.001).
"""
import json
import statistics
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from .base import col, NO_ID, ValidationError, round_half_up
from ..timeutil import now_utc

COL = "AOITS_NABC_PRICE"
FIRST_PAGES = 5                     # first download of a product: about the last 3-4 weeks
MAX_PAGES = 30
STATS = ("median", "avg", "low", "high")
UNIT_FACTORS = {"บาท/ตัน": 0.001, "บาท/กก.": 1.0, "บาท/กิโลกรัม": 1.0, "บาท/100 กก.": 0.01, "บาท/100กก.": 0.01}


def unit_factor(unit):
    unit = (unit or "").replace(" ", "")
    for name, factor in UNIT_FACTORS.items():
        if unit == name.replace(" ", ""):
            return factor
    return None


def _get(product, page):
    base = current_app.config["NABC_PRICE_URL"]
    url = base + ("&" if "?" in base else "?") + urllib.parse.urlencode({"product_name": product, "page": page})
    req = urllib.request.Request(url, headers={"User-Agent": "AOI-Labs-rice-mill/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read(5 * 1024 * 1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        current_app.logger.warning("NABC daily price download failed: %s", error)
        raise ValidationError("npErrDownload")
    if not body.get("success", True) or not isinstance(body.get("data"), list):
        raise ValidationError("npErrFormat")
    return body


def latest_date(product):
    doc = col(COL).find_one({"PRODUCT_NAME": product}, NO_ID, sort=[("DATA_DATE", -1)])
    return doc["DATA_DATE"] if doc else None


def _store(rec, product):
    try:
        price = float(rec.get("day_price"))
    except (TypeError, ValueError):
        return False
    day = str(rec.get("data_date") or "")[:10]
    if len(day) != 10 or price <= 0:
        return False
    key = {"PRODUCT_NAME": product, "DATA_DATE": day, "MARKET_NAME": (rec.get("market_name") or "").strip()[:200],
           "PROVINCE": (rec.get("province") or "").strip()[:100]}
    if col(COL).find_one(key, NO_ID):
        return False
    col(COL).insert_one(dict(key, PRODUCT_CATEGORY=(rec.get("product_category") or "").strip()[:100], DAY_PRICE=price,
                             UNIT=(rec.get("unit") or "").strip()[:30], FETCH_DATE=now_utc()))
    return True


def sync(product):
    """The daily check of one product. Returns {'new': records saved, 'latest': newest day, 'checked': True}."""
    product = (product or "").strip()
    if not product:
        return {"new": 0, "latest": None}
    known = latest_date(product)
    body = _get(product, 1)
    rows = body["data"]
    if not rows:
        raise ValidationError("npErrNoData", product=product)
    newest = max(str(r.get("data_date") or "")[:10] for r in rows)
    if known and newest <= known:
        return {"new": 0, "latest": known}                # nothing new: only page 1 was read
    saved, page = 0, 1
    total_pages = max(1, -(-int((body.get("pagination") or {}).get("total") or len(rows)) // max(1, len(rows))))
    limit = MAX_PAGES if known else FIRST_PAGES
    while True:
        reached = False
        for rec in rows:
            day = str(rec.get("data_date") or "")[:10]
            if known and day < known:
                reached = True                            # older than what we have
                continue
            saved += _store(rec, product)
        page += 1
        if reached or page > min(total_pages, limit):
            break
        rows = _get(product, page)["data"]
        if not rows:
            break
    return {"new": saved, "latest": latest_date(product)}


def _values(product, day, provinces=None):
    flt = {"PRODUCT_NAME": product, "DATA_DATE": day}
    out = []
    for r in col(COL).find(flt, NO_ID):
        if provinces and r["PROVINCE"] not in provinces:
            continue
        factor = unit_factor(r.get("UNIT"))
        if factor:
            out.append((r, r["DAY_PRICE"] * factor))
    return out


def stat_of(values, stat):
    if not values:
        return None
    return {"avg": statistics.mean(values), "low": min(values), "high": max(values)}.get(stat, statistics.median(values))


def provinces_of(text):
    return [p.strip() for p in (text or "").replace("،", ",").split(",") if p.strip()]


def market_price(product, provinces=None, stat="median"):
    """The newest day's market price (THB/kg) of a product: {'kg', 'date', 'n', 'low', 'high', 'rows'} or None."""
    if not product:
        return None
    days = sorted({r["DATA_DATE"] for r in col(COL).find({"PRODUCT_NAME": product}, {"_id": 0, "DATA_DATE": 1})}, reverse=True)
    for day in days[:10]:                          # the newest day that has prices from the chosen provinces
        found = _values(product, day, provinces)
        if found:
            values = [v for _r, v in found]
            return {"kg": round_half_up(stat_of(values, stat), 2), "date": day, "n": len(values),
                    "low": round_half_up(min(values), 2), "high": round_half_up(max(values), 2),
                    "rows": sorted(found, key=lambda x: (x[0]["PROVINCE"], x[0]["MARKET_NAME"]))}
    return None


def trend(product, provinces=None, stat="median", days=20):
    """[(day, THB/kg)] for the newest `days` days, oldest first."""
    all_days = sorted({r["DATA_DATE"] for r in col(COL).find({"PRODUCT_NAME": product}, {"_id": 0, "DATA_DATE": 1})})
    out = []
    for day in all_days[-days:]:
        values = [v for _r, v in _values(product, day, provinces)]
        if values:
            out.append((day, round_half_up(stat_of(values, stat), 2)))
    return out
