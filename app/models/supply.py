"""Supply indicator: the monthly agricultural production index of the National Agricultural Big Data Center
(NABC, agriapi.nabc.go.th, AOI_NABC_INDEX_URL).

It is an index (no unit, no prices) for the whole agricultural sector, so it is shown on Automation > AI as a hint
of the supply direction next to the price advice, never used to set a price:

  change vs the same month last year > +5%  -> production up: more supply, paddy prices may soften
  change vs the same month last year < -5%  -> production down: less supply, paddy prices may firm up

Records are kept in AOITS_NABC_INDEX, one per scope (product_name) and month; the newest are fetched once a day.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from .base import col, NO_ID, ValidationError, round_half_up
from .counter import Counter
from ..timeutil import now_local, now_utc

COL = "AOITS_NABC_INDEX"
MAX_PAGES = 10
SIGNAL_PCT = 5.0


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "AOI-Labs-rice-mill/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read(5 * 1024 * 1024).decode("utf-8"))


def _page_url(base, page):
    parts = urllib.parse.urlsplit(base)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query.update({"page": str(page), "limit": "100"})
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def parse_record(rec):
    """One API record -> our record, or None when it lacks the month or the value."""
    try:
        year = int(str(rec.get("year_th")).strip())
        month = int(str(rec.get("month")).strip())
        value = float(rec.get("production_index"))
    except (TypeError, ValueError):
        return None
    if year > 2400:
        year -= 543                                    # Buddhist Era -> CE
    if not 1 <= month <= 12:
        return None
    scope = (rec.get("product_name") or rec.get("commod") or rec.get("product_sector") or "").strip()[:100]
    return {"SCOPE": scope, "PERIOD": "%04d-%02d" % (year, month), "YEAR_TH": year + 543, "MONTH": month,
            "INDEX_VALUE": round_half_up(value, 4), "DATA_DATE": rec.get("data_date")}


def fetch(ope_user):
    """Downloads every page of the index and saves new or changed months. Returns the number saved."""
    base = current_app.config["NABC_INDEX_URL"]
    saved, page, pages = 0, 1, 1
    try:
        while page <= min(pages, MAX_PAGES):
            body = _get_json(_page_url(base, page))
            if not body.get("success", True) or not isinstance(body.get("data"), list):
                raise ValidationError("spErrFormat")
            pages = int((body.get("pagination") or {}).get("totalPages") or 1)
            for rec in body["data"]:
                doc = parse_record(rec)
                if doc is None:
                    continue
                old = col(COL).find_one({"SCOPE": doc["SCOPE"], "PERIOD": doc["PERIOD"]}, NO_ID)
                if old and old.get("INDEX_VALUE") == doc["INDEX_VALUE"]:
                    continue
                doc.update({"FETCH_DATE": now_utc(), "OPE_USER": ope_user})
                if old:
                    col(COL).update_one({"SCOPE": doc["SCOPE"], "PERIOD": doc["PERIOD"]}, {"$set": doc})
                else:
                    col(COL).insert_one(dict(doc))
                saved += 1
            page += 1
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        current_app.logger.warning("NABC production index download failed: %s", error)
        raise ValidationError("spErrDownload")
    return saved


def series(scope=None, months=24):
    """The newest `months` months, oldest first."""
    flt = {"SCOPE": scope} if scope else {}
    docs = list(col(COL).find(flt, NO_ID).sort([("PERIOD", -1)]).limit(months))
    return list(reversed(docs))


def summary():
    """Latest month, its change vs the month before and vs the same month last year, and the signal."""
    latest = col(COL).find_one({}, NO_ID, sort=[("PERIOD", -1)])
    if not latest:
        return None
    scope = latest["SCOPE"]
    year, month = int(latest["PERIOD"][:4]), int(latest["PERIOD"][5:])
    prev_period = "%04d-%02d" % (year - (month == 1), 12 if month == 1 else month - 1)
    year_ago = col(COL).find_one({"SCOPE": scope, "PERIOD": "%04d-%02d" % (year - 1, month)}, NO_ID)
    prev = col(COL).find_one({"SCOPE": scope, "PERIOD": prev_period}, NO_ID)

    def pct(old):
        return round_half_up((latest["INDEX_VALUE"] - old["INDEX_VALUE"]) / old["INDEX_VALUE"] * 100, 1) if old and old["INDEX_VALUE"] else None
    yoy = pct(year_ago)
    signal = "flat"
    if yoy is not None and yoy > SIGNAL_PCT:
        signal = "up"
    elif yoy is not None and yoy < -SIGNAL_PCT:
        signal = "down"
    last_fetch = col(COL).find_one({}, NO_ID, sort=[("FETCH_DATE", -1)])
    return {"scope": scope, "period": latest["PERIOD"], "year_th": latest["YEAR_TH"], "month": latest["MONTH"],
            "value": latest["INDEX_VALUE"], "mom": pct(prev), "yoy": yoy, "signal": signal,
            "year_ago": year_ago["INDEX_VALUE"] if year_ago else None, "fetched": (last_fetch or {}).get("FETCH_DATE")}


def daily_due():
    """Same once-a-day turn as the market prices, with its own key."""
    now = now_local()
    if now.hour < int(current_app.config.get("MARKET_HOUR", 9)):
        return False
    return Counter.next("NABC-%04d%02d%02d" % (now.year, now.month, now.day)) == 1
