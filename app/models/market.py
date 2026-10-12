"""Market prices from the Thai Rice Millers Association (สมาคมโรงสีข้าวไทย, www.thairicemillers.org).

The association publishes its price report as a PDF (Pricerice<DDMMYYYY in the Buddhist year>.pdf) linked from
the price page (AOI_MARKET_URL). The system downloads the newest report, reads the text rows of the PDF
(AOITS_MKREPORT), and the mapping (AOIMS_MKMAP) says which row and number is the market price of which SKU:

  market price (THB/kg) = number on the row x unit factor   (THB/100 kg -> x 0.01, THB/ton -> x 0.001)

The market price is written to AOIMS_PRICE.MKPRICE_BUY and MKPRICE_SELL of every branch; the suggested prices are
recalculated (market + margin) and a changed suggestion waits for approval on Pricing Maintenance, as before.
A market price that moves more than MAX_JUMP_PCT from the previous one is not applied (it is shown for a check),
so a misread PDF cannot change prices by itself.
"""
import io
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import current_app

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up
from .counter import Counter
from . import variety as var
from ..timeutil import now_local, now_utc, to_local

MAX_JUMP_PCT = 30.0
MAX_BYTES = 15 * 1024 * 1024
UNITS = {"auto": None, "kg": 1.0, "100kg": 0.01, "ton": 0.001}
COLUMNS = ("avg", "low", "high")             # for a range "3,300-3,400"; a plain number is the same for all three
LINK = re.compile(r"""href\s*=\s*["']([^"']*?[Pp]rice[Rr]ice[^"']*?\.pdf)["']""")
NUMBER = re.compile(r"(?<![\d/])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d/%])")
RANGE = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*[-–]\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d/%])")
MIN_PRICE = 100.0                           # report prices are per 100 kg or per ton: smaller numbers are not prices

# first guess of the report rows of each SKU (normalized text the row label must contain); change on the screen
DEFAULT_KEYS = {
    "PADDY_HM": "ข้าวเปลือกหอมมะลิ", "RICE_HM": "ข้าวหอมมะลิ100%", "BROKEN_HM": "ปลายข้าวหอมมะลิ",
    "PADDY": "ข้าวเปลือกเจ้า", "RICE_W5": "ข้าวขาว5%", "BROKEN": "ปลายข้าวขาว",
    "PADDY_GL": "ข้าวเปลือกเหนียว", "RICE_GL": "ข้าวเหนียว10%", "BROKEN_GL": "ปลายข้าวเหนียว",
    "BRAN": "รำข้าว",
}


THAI_MARKS = re.compile("[\u0e31\u0e34-\u0e3a\u0e47-\u0e4e\uf700-\uf71f]")
SPACES = re.compile("[\\s\u200b\u200c\u200d\ufeff*]+")


# Thai fonts of Windows/Mac PDFs use private-use code points for marks moved left/down; shown as the real marks
PUA_THAI = dict(zip(range(0xF700, 0xF71B), "ฐิีึื่้๊๋์่้๊๋์ญัํ็่้๊๋์ฺุู"))


def readable(text):
    return (text or "").translate(PUA_THAI)


def normalize(text):
    """For matching row labels. PDF text extraction often drops or reorders the Thai tone marks and the vowels
    written above/below the line (ข้าว comes out as ขาว, ำ as ํา), so the row label and the key are both compared
    without them: NFKD, no Thai combining marks, no spaces or zero-width characters, lower case."""
    text = unicodedata.normalize("NFKD", text or "")
    return SPACES.sub("", THAI_MARKS.sub("", text)).lower()


def report_date(url):
    """Pricerice14112568.pdf -> 2025-11-14 (the year is Buddhist Era). None when the name has no date."""
    m = re.search(r"(\d{2})(\d{2})(\d{4})", urllib.parse.unquote(url.rsplit("/", 1)[-1]))
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year > 2400:
        year -= 543
    try:
        return datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None


def find_reports(html, base_url):
    """Report links on the price page, newest first: [(absolute url, date or None)]."""
    seen, out = set(), []
    for href in LINK.findall(html or ""):
        url = urllib.parse.urljoin(base_url, href.strip())
        if url not in seen:
            seen.add(url)
            out.append((url, report_date(url)))
    return sorted(out, key=lambda r: r[1] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _num(text):
    return float(text.replace(",", ""))


def parse_line(line):
    """'ข้าวหอมมะลิ 100% ชั้น 2 3,300-3,400 3,350' -> ('ข้าวหอมมะลิ 100% ชั้น 2', [(3300, 3400), (3350, 3350)])."""
    prices, spans = [], []
    for m in RANGE.finditer(line):
        low, high = _num(m.group(1)), _num(m.group(2))
        if low >= MIN_PRICE and high >= MIN_PRICE:
            prices.append((m.start(), (min(low, high), max(low, high))))
            spans.append((m.start(), m.end()))
    for m in NUMBER.finditer(line):
        if any(a <= m.start() < b for a, b in spans):
            continue
        value = _num(m.group(1))
        if value >= MIN_PRICE:
            prices.append((m.start(), (value, value)))
    prices.sort()
    label_end = prices[0][0] if prices else len(line)
    return line[:label_end].strip(" :.-\t"), [p for _pos, p in prices]


def parse_text(text):
    """Rows of the report: [{'label', 'key', 'values': [[low, high], ...]}]. A line with numbers and no label
    belongs to the label line just above it (table cells often come out on their own lines)."""
    rows, pending = [], None
    skip = (normalize("วันที่"), normalize("ประจำวัน"))   # the report's date line (its year looks like a price)
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", unicodedata.normalize("NFC", raw)).strip()
        line = re.sub(r"(,\d{3})(?=\d)", r"\1 ", line)    # two cells joined without a space: 15,20014,900
        if not line or any(k in normalize(line) for k in skip):
            continue
        label, values = parse_line(line)
        if values and not label and pending:
            label = pending
        if values and label:
            rows.append({"label": readable(label)[:120], "key": normalize(label), "values": [list(v) for v in values]})
            pending = None
        elif label and not values:
            pending = label
    return rows


def pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValidationError("mkErrNoPdfLib")
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as error:                          # a damaged or unexpected file
        current_app.logger.warning("Market report PDF could not be read: %s", error)
        raise ValidationError("mkErrPdf")


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "AOI-Labs-rice-mill/1.0 (+market price import)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValidationError("mkErrTooBig")
    return data


def value_of(row, col_index, pick):
    """The chosen number of a row: column (0 = first price on the row) and avg/low/high of a range."""
    if not row or col_index >= len(row["values"]):
        return None
    low, high = row["values"][col_index]
    return {"low": low, "high": high}.get(pick, (low + high) / 2.0)


def unit_factor(unit, value):
    """THB/kg per unit of the report. 'auto': 5,000 or more is per ton, 100 or more per 100 kg."""
    if UNITS.get(unit):
        return UNITS[unit]
    if value is None:
        return None
    return 0.001 if value >= 5000 else (0.01 if value >= 100 else 1.0)


class MarketMap:
    """Where each SKU's market price comes from: SOURCE TRMA (a row of the association's PDF report), NABC (NABC
    daily farm prices of a product name) or HAND (typed on Pricing Maintenance)."""
    COL = "AOIMS_MKMAP"
    SOURCES = ("TRMA", "NABC", "HAND")

    @staticmethod
    def source_of(m):
        return m.get("SOURCE") or ("TRMA" if m.get("ACTIVE_FLG") else "HAND")

    @classmethod
    def all(cls):
        found = {m["MAT_CODE"]: m for m in col(cls.COL).find({}, NO_ID)}
        out = []
        for code in var.ALL:
            if code == var.MAT_HUSK:
                continue
            m = dict(found.get(code) or {"MAT_CODE": code, "ROW_KEY": None, "COL_INDEX": 0, "COL_PICK": "avg", "UNIT": "auto",
                                         "ACTIVE_FLG": False})
            m["SOURCE"] = cls.source_of(m)
            out.append(m)
        return out

    @classmethod
    def save(cls, form, ope_user):
        """Mapping form per SKU: SRC__<MAT> (TRMA / NABC / HAND); for TRMA ROW__, COL__, PICK__, UNIT__;
        for NABC NP__ (exact product name), NPV__ (provinces, comma separated, blank = all), NPS__ (median/avg/low/high)."""
        from . import nabc_price
        changed = 0
        for code in var.ALL:
            if code == var.MAT_HUSK or ("ROW__" + code) not in form:
                continue
            source = form.get("SRC__" + code) if form.get("SRC__" + code) in cls.SOURCES else None
            row_key = normalize(form.get("ROW__" + code)) or None
            try:
                col_index = max(0, min(9, int(form.get("COL__" + code) or 0)))
            except ValueError:
                raise ValidationError("errNumber")
            pick = form.get("PICK__" + code) if form.get("PICK__" + code) in COLUMNS else "avg"
            unit = form.get("UNIT__" + code) if form.get("UNIT__" + code) in UNITS else "auto"
            product = clean_text(form.get("NP__" + code), 200) or None
            provinces = ", ".join(nabc_price.provinces_of(form.get("NPV__" + code))) or None
            stat = form.get("NPS__" + code) if form.get("NPS__" + code) in nabc_price.STATS else "median"
            if source is None:
                source = "TRMA" if row_key else "HAND"
            if source == "TRMA" and not row_key:
                raise ValidationError("mkErrNeedRow", mat=var.mat_name(code))
            if source == "NABC" and not product:
                raise ValidationError("npErrNeedProduct", mat=var.mat_name(code))
            values = {"SOURCE": source, "ROW_KEY": row_key, "ROW_TEXT": clean_text(form.get("ROW__" + code), 120) or None, "COL_INDEX": col_index, "COL_PICK": pick, "UNIT": unit,
                      "ACTIVE_FLG": source == "TRMA", "NABC_PRODUCT": product, "NABC_PROVINCES": provinces, "NABC_STAT": stat}
            existing = col(cls.COL).find_one({"MAT_CODE": code}, NO_ID)
            if existing is None:
                col(cls.COL).insert_one(dict(values, MAT_CODE=code, **audit_new(ope_user)))
                changed += 1
            elif any(existing.get(k) != v for k, v in values.items()):
                values.update(audit_update(ope_user))
                col(cls.COL).update_one({"MAT_CODE": code}, {"$set": values})
                changed += 1
        return changed

    @staticmethod
    def match(mapping, rows):
        """The report row of a mapping: the first row whose label contains ROW_KEY."""
        key = mapping.get("ROW_KEY")
        if not key:
            return None
        return next((r for r in rows if key in r["key"]), None)

    @classmethod
    def resolve(cls, rows):
        """{MAT_CODE: {'row', 'raw', 'factor', 'kg'}} for every SKU taken from the association report."""
        out = {}
        for m in cls.all():
            if m["SOURCE"] != "TRMA":
                continue
            row = cls.match(m, rows)
            raw = value_of(row, m.get("COL_INDEX") or 0, m.get("COL_PICK") or "avg")
            factor = unit_factor(m.get("UNIT") or "auto", raw)
            out[m["MAT_CODE"]] = {"row": row, "raw": raw, "factor": factor,
                                  "kg": round_half_up(raw * factor, 2) if raw is not None and factor else None}
        return out

    @classmethod
    def resolve_nabc(cls):
        """{MAT_CODE: NABC market price (see nabc_price.market_price) or None} for every SKU taken from NABC."""
        from . import nabc_price
        return {m["MAT_CODE"]: nabc_price.market_price(m.get("NABC_PRODUCT"), nabc_price.provinces_of(m.get("NABC_PROVINCES")),
                                                       m.get("NABC_STAT") or "median")
                for m in cls.all() if m["SOURCE"] == "NABC"}


class MarketReport:
    COL = "AOITS_MKREPORT"

    @classmethod
    def latest(cls, ok_only=False):
        flt = {"REPORT_STATUS": "OK"} if ok_only else {}
        return col(cls.COL).find_one(flt, NO_ID, sort=[("FETCH_DATE", -1)])

    @classmethod
    def recent(cls, limit=10):
        return list(col(cls.COL).find({}, {"_id": 0, "ROWS": 0}).sort([("FETCH_DATE", -1)]).limit(limit))

    @classmethod
    def _record(cls, values, ope_user):
        today = now_local()
        day = "%04d%02d%02d" % (today.year, today.month, today.day)
        doc = dict(values, REPORT_ID="MK-%s-%04d" % (day, Counter.next("MK-" + day)), FETCH_DATE=now_utc(), OPE_USER=ope_user)
        col(cls.COL).insert_one(dict(doc))
        return doc

    @classmethod
    def fetch(cls, ope_user, force=False):
        """Downloads the newest report from the association's price page and applies it. Returns the report record.
        A report already imported is not imported again unless `force`."""
        page_url = current_app.config["MARKET_URL"]
        try:
            html = http_get(page_url).decode("utf-8", "replace")
            reports = find_reports(html, page_url)
            if not reports:
                raise ValidationError("mkErrNoReport")
            url, date = reports[0]
            last = cls.latest(ok_only=True)
            if last and last.get("SOURCE_URL") == url and not force:
                return dict(last, ALREADY=True)
            data = http_get(url)
        except ValidationError as error:
            cls._record({"SOURCE_URL": page_url, "REPORT_DATE": None, "REPORT_STATUS": "ERR", "ERROR_KEY": error.key, "ROWS": []}, ope_user)
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            current_app.logger.warning("Market price download failed: %s", error)
            cls._record({"SOURCE_URL": page_url, "REPORT_DATE": None, "REPORT_STATUS": "ERR", "ERROR_KEY": "mkErrDownload", "ROWS": []}, ope_user)
            raise ValidationError("mkErrDownload")
        return cls.import_pdf(data, url, date, ope_user)

    @classmethod
    def import_pdf(cls, data, source, date, ope_user):
        """A report PDF (downloaded, or uploaded by the user): read its rows, store and apply them."""
        if not data or not data.startswith(b"%PDF"):
            raise ValidationError("mkErrPdf")
        rows = parse_text(pdf_text(data))
        if not rows:
            cls._record({"SOURCE_URL": source, "REPORT_DATE": date, "REPORT_STATUS": "ERR", "ERROR_KEY": "mkErrNoRows", "ROWS": []}, ope_user)
            raise ValidationError("mkErrNoRows")
        report = cls._record({"SOURCE_URL": source, "REPORT_DATE": date or report_date(source), "REPORT_STATUS": "OK",
                              "ERROR_KEY": None, "ROWS": rows}, ope_user)
        report["APPLIED"] = apply_report(report, ope_user)
        col(cls.COL).update_one({"REPORT_ID": report["REPORT_ID"]}, {"$set": {"APPLIED": report["APPLIED"]}})
        return report


def _apply(code, kg, date, src, ope_user, branches):
    """One SKU's new market price on every branch: 'applied' | 'same' | 'jump' (moved more than MAX_JUMP_PCT)."""
    from .price import CUSTOM, Price
    status = "same"
    for branch_id in branches:
        price = Price.get(branch_id, code)
        if not price or price.get("PRICE_STATUS") == CUSTOM:
            continue
        old = price.get("MKPRICE_SELL") or price.get("MKPRICE_BUY")
        if old and abs(kg - old) / old * 100 > MAX_JUMP_PCT:
            status = "jump"                                      # shown for a check, not applied
            continue
        extra = {"MKPRICE_BUY": kg, "MKPRICE_SELL": kg, "MKPRICE_DATE": date or now_utc(), "MKPRICE_SRC": src}
        if (price.get("MKPRICE_BUY"), price.get("MKPRICE_SELL")) != (kg, kg):
            Price._resuggest(price, ope_user, extra)
            status = "applied" if status != "jump" else status
        else:
            col(Price.COL).update_one({"BRANCH_ID": branch_id, "MAT_CODE": code},
                                      {"$set": {"MKPRICE_DATE": extra["MKPRICE_DATE"], "MKPRICE_SRC": src}})
    return status


def _branches(branches):
    return branches or [b["BRANCH_ID"] for b in col("AOIMS_SYSPARAM").find({}, NO_ID)]


def apply_report(report, ope_user, branches=None):
    """Writes the association report's market prices to every branch. {MAT_CODE: 'applied' | 'same' | 'jump' | 'missing'}."""
    branches = _branches(branches)
    result = {}
    for code, found in MarketMap.resolve(report.get("ROWS") or []).items():
        if found["kg"] is None or found["kg"] <= 0:
            result[code] = "missing"
            continue
        result[code] = _apply(code, found["kg"], report.get("REPORT_DATE"), report["REPORT_ID"], ope_user, branches)
    return result


def apply_nabc(ope_user, branches=None):
    """Writes the NABC daily market prices to every branch (same result codes as apply_report)."""
    branches = _branches(branches)
    result = {}
    for code, found in MarketMap.resolve_nabc().items():
        if not found or not found["kg"]:
            result[code] = "missing"
            continue
        day = datetime.strptime(found["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        result[code] = _apply(code, found["kg"], day, "NABC:" + found["date"], ope_user, branches)
    return result


def sync_nabc(ope_user):
    """The daily NABC check: page 1 of every mapped product first; new days are downloaded and applied.
    Returns {'new': records, 'applied': {...}, 'errors': [keys], 'latest': {product: day}}."""
    from . import nabc_price
    products = sorted({m["NABC_PRODUCT"] for m in MarketMap.all() if m["SOURCE"] == "NABC" and m.get("NABC_PRODUCT")})
    new, errors, latest = 0, [], {}
    for product in products:
        try:
            result = nabc_price.sync(product)
        except ValidationError as error:
            errors.append(error)
            continue
        new += result["new"]
        latest[product] = result["latest"]
    applied = apply_nabc(ope_user) if products else {}
    return {"new": new, "applied": applied, "errors": errors, "latest": latest, "products": products}


def fetch_all(ope_user, force=True):
    """Fetch now and the daily job: NABC daily prices, then the association report. Each source runs even when the
    other fails. Returns {'nabc': ..., 'report': report or None, 'errors': [ValidationError]}."""
    out = {"nabc": None, "report": None, "errors": []}
    try:
        out["nabc"] = sync_nabc(ope_user)
        out["errors"] += out["nabc"]["errors"]
    except ValidationError as error:
        out["errors"].append(error)
    if any(m["SOURCE"] == "TRMA" for m in MarketMap.all()):
        try:
            out["report"] = MarketReport.fetch(ope_user, force=force)
        except ValidationError as error:
            out["errors"].append(error)
    return out


# SKUs taken from the NABC daily farm prices by default (exact NABC product names)
DEFAULT_NABC = {"PADDY_HM": "ข้าวเปลือกเจ้าหอมมะลิ 105"}


def seed_default_map(ope_user):
    """The first guess of the mapping (only for SKUs that have no mapping yet). Jasmine paddy comes from NABC; a
    mapping saved before NABC (update 12 or older) is moved to NABC once, if it still has no SOURCE."""
    for code, product in DEFAULT_NABC.items():
        col(MarketMap.COL).update_one({"MAT_CODE": code, "SOURCE": {"$exists": False}},
                                      {"$set": {"SOURCE": "NABC", "NABC_PRODUCT": product, "NABC_STAT": "median", "ACTIVE_FLG": False}})
        if col(MarketMap.COL).find_one({"MAT_CODE": code}, NO_ID) is None:
            col(MarketMap.COL).insert_one(dict({"MAT_CODE": code, "SOURCE": "NABC", "NABC_PRODUCT": product, "NABC_PROVINCES": None,
                                                "NABC_STAT": "median", "ROW_KEY": normalize(DEFAULT_KEYS.get(code)),
                                                "ROW_TEXT": DEFAULT_KEYS.get(code), "COL_INDEX": 0,
                                                "COL_PICK": "avg", "UNIT": "auto", "ACTIVE_FLG": False}, **audit_new(ope_user)))
    for code, key in DEFAULT_KEYS.items():
        if col(MarketMap.COL).find_one({"MAT_CODE": code}, NO_ID) is None:
            col(MarketMap.COL).insert_one(dict({"MAT_CODE": code, "ROW_KEY": normalize(key), "ROW_TEXT": key, "COL_INDEX": 0, "COL_PICK": "avg",
                                                "UNIT": "auto", "ACTIVE_FLG": True}, **audit_new(ope_user)))


def daily_due():
    """True when today's automatic fetch has not run yet and it is past AOI_MARKET_HOUR (local time).
    The first caller of the day takes the turn (a counter), so several server workers fetch only once."""
    now = now_local()
    if now.hour < int(current_app.config.get("MARKET_HOUR", 9)):
        return False
    key = "MKAUTO-%04d%02d%02d" % (now.year, now.month, now.day)
    return Counter.next(key) == 1
