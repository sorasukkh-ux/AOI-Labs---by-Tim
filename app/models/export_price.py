"""Thai rice EXPORT prices from the Thai Rice Exporters Association (สมาคมผู้ส่งออกข้าวไทย, update 50).

The association publishes its weekly FOB Bangkok prices (US$ per metric ton) on its price page
(AOI_EXPORT_URL, default http://www.thairiceexporters.or.th/price.htm) as an HTML table: one row per kind of rice
(Hom Mali 100%, white rice 5%, A1 Super broken rice, glutinous 10% ...) and one column per week. The page has no
API, so the server downloads the page and reads the table:

  * every table row with a label and prices of 100 - 5,000 (US$/ton) is a price row,
  * the dates come from the header cells (14 Oct 2026, 14/10/2569, 14 ต.ค. 69 ...); with no dates in the header the
    newest date written on the page is used, else the day of the download,
  * a page that only links a PDF: the PDF is read the same way as the rice millers' report.

Stored per row and week in AOITS_EXPRICE (USD_TON). The analysis turns US$/ton into THB/kg with the exchange rate
of Price analysis settings (AOIMS_PRICESIG.FX_USD_THB):  THB/kg = US$/ton x rate / 1000.
Export prices are FOB (milled, packed, at the port): they show the direction of the world market, they are not a
mill-gate price.
"""
import re
import unicodedata
import urllib.parse
from datetime import date
from html.parser import HTMLParser

from flask import current_app

from .base import col, NO_ID, ValidationError, round_half_up
from .market import normalize, http_get, pdf_text, parse_text
from ..timeutil import now_utc, today_local

COL = "AOITS_EXPRICE"
MIN_USD, MAX_USD = 100.0, 5000.0
SOURCE = "TREA"

# first guess of the rows of each SKU (any of the normalized texts); change on Price analysis > Sources
DEFAULT_KEYS = {
    "RICE_HM": ("hommali100", "hommalirice100", "หอมมะลิ100", "ขาวหอมมะลิ"),
    "RICE_W5": ("whiterice5%", "ขาวขาว5%", "ข้าวขาว5%"),
    "RICE_GL": ("glutinous", "ขาวเหนียว", "ข้าวเหนียว"),
    "BROKEN": ("a1super", "a1เลิศ", "เอวนเลศ", "เอวันเลิศ", "a.1super"),
}

MONTHS_EN = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}
MONTHS_TH = {normalize(m): i + 1 for i, m in enumerate(("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."))}
MONTHS_TH.update({normalize(m): i + 1 for i, m in enumerate(("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม",
                                                             "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"))})
D_NUM = re.compile(r"(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{2,4})")
D_EN1 = re.compile(r"(\d{1,2})[\s-]*([A-Za-z]{3,9})\.?[\s,-]*(\d{2,4})")
D_EN2 = re.compile(r"([A-Za-z]{3,9})\.?\s*(\d{1,2})\s*,?\s*(\d{4})")
D_TH = re.compile(r"(\d{1,2})\s*([฀-๿.]{2,12})\s*(\d{2,4})")
NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _year(y):
    y = int(y)
    if y < 100:
        y += 2500 if y >= 60 else 2000           # 69 -> 2569 (Thai short year), 26 -> 2026
    if y > 2400:
        y -= 543
    return y


def _mk(y, m, d):
    try:
        day = date(_year(y), int(m), int(d))
    except ValueError:
        return None
    return day if 2000 <= day.year <= 2100 else None


def find_date(text):
    """The first date in a text: 14/10/2026, 14/10/2569, 14 Oct 2026, Oct 14, 2026, 14 ต.ค. 2569. None if none."""
    text = unicodedata.normalize("NFC", text or "")
    m = D_EN1.search(text)
    if m and m.group(2)[:3].lower() in MONTHS_EN:
        return _mk(m.group(3), MONTHS_EN[m.group(2)[:3].lower()], m.group(1))
    m = D_EN2.search(text)
    if m and m.group(1)[:3].lower() in MONTHS_EN:
        return _mk(m.group(3), MONTHS_EN[m.group(1)[:3].lower()], m.group(2))
    for m in D_TH.finditer(text):
        month = MONTHS_TH.get(normalize(m.group(2)))
        if month:
            return _mk(m.group(3), month, m.group(1))
    m = D_NUM.search(text)
    if m:
        return _mk(m.group(3), m.group(2), m.group(1))
    return None


class _Tables(HTMLParser):
    """Rows of every <table> as lists of cell texts (colspan repeated so the columns line up)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell, self._span, self.links, self.text = [], None, None, 1, [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._span = [], max(1, min(20, int(a.get("colspan") or 1) if str(a.get("colspan") or "1").isdigit() else 1))
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.extend([re.sub(r"\s+", " ", "".join(self._cell)).strip()] * self._span)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        self.text.append(data)


def _price(cell):
    """'1,050' -> 1050.0, '1,050-1,060' -> 1055.0 (the middle), else None (also outside 100 - 5,000)."""
    nums = [float(n.replace(",", "")) for n in NUM.findall(cell or "")]
    if not nums or len(nums) > 2 or re.search(r"[A-Za-z฀-๿]{2,}", cell or ""):
        return None
    value = sum(nums) / len(nums)
    return value if MIN_USD <= value <= MAX_USD else None


def parse_html(html, fetch_day=None):
    """[{'day': date, 'label': text, 'key': normalized, 'usd': US$/ton}] from the price page."""
    p = _Tables()
    p.feed(html or "")
    page_day = find_date(" ".join(p.text)) or fetch_day or today_local()
    out, header = [], None
    for row in p.rows:
        dates = [find_date(c) for c in row]
        found = [i for i, d in enumerate(dates) if d]
        if found and not any(_price(c) for c in row):
            if len(found) >= 2 or (len(row) > 1 and found[0] > 0):
                header = dates                              # the weeks of the price rows below
            else:
                page_day = dates[found[0]]                  # a title such as "ราคา ณ วันที่ 14 ต.ค. 2569"
            continue
        prices = [(i, _price(c)) for i, c in enumerate(row)]
        prices = [(i, v) for i, v in prices if v is not None]
        labels = [c for c in row if c and _price(c) is None and re.search(r"[A-Za-z\u0e00-\u0e7f]", c)]
        if not prices or not labels:
            continue
        label = labels[0][:120]
        if header:
            pairs = [(header[i], usd) for i, usd in prices if i < len(header) and header[i]]
        else:
            pairs = [(page_day, prices[0][1])]              # no dates: the first price of the row is the newest
        for day, usd in pairs:
            out.append({"day": day, "label": label, "key": normalize(label), "usd": round_half_up(usd, 2)})
    seen, unique = set(), []
    for r in out:
        k = (r["day"], r["key"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique, p.links


def parse_pdf(data, fetch_day=None):
    text = pdf_text(data)
    day = find_date(text) or fetch_day or today_local()
    out = []
    for row in parse_text(text):
        usd = next(((lo + hi) / 2.0 for lo, hi in row["values"] if MIN_USD <= (lo + hi) / 2.0 <= MAX_USD), None)
        if usd:
            out.append({"day": day, "label": row["label"], "key": row["key"], "usd": round_half_up(usd, 2)})
    return out


def store(rows, url, ope_user):
    """Saves new rows (a row and day already stored is updated). Returns how many were new."""
    new = 0
    for r in rows:
        key = {"PRICE_DATE": r["day"].isoformat(), "ITEM_KEY": r["key"]}
        old = col(COL).find_one(key, NO_ID)
        values = {"ITEM_LABEL": r["label"], "USD_TON": r["usd"], "SOURCE_URL": url, "FETCH_DATE": now_utc(), "OPE_USER": ope_user}
        if old is None:
            col(COL).insert_one(dict(key, **values))
            new += 1
        elif old.get("USD_TON") != r["usd"]:
            col(COL).update_one(key, {"$set": values})
    return new


def fetch(ope_user):
    """Downloads the price page and stores its prices. Returns {'rows', 'new', 'url', 'newest'}."""
    url = current_app.config["EXPORT_URL"]
    try:
        data = http_get(url)
    except Exception:                                     # noqa: BLE001
        raise ValidationError("psErrDownload")
    return import_data(data, url, ope_user)


def import_data(data, url, ope_user):
    """A downloaded page or a saved copy (HTML or PDF) of it."""
    if data.startswith(b"%PDF"):
        rows = parse_pdf(data)
    else:
        html = _decode(data)
        rows, links = parse_html(html)
        if not rows:                                      # the page may only link a PDF of the prices
            pdfs = [urllib.parse.urljoin(url or "", h) for h in links if h.lower().endswith(".pdf") and "pric" in h.lower()]
            if pdfs and url:
                try:
                    rows = parse_pdf(http_get(pdfs[0]))
                    url = pdfs[0]
                except Exception:                         # noqa: BLE001
                    rows = []
    if not rows:
        raise ValidationError("psErrNoRows")
    new = store(rows, url, ope_user)
    return {"rows": len(rows), "new": new, "url": url, "newest": max(r["day"] for r in rows).isoformat()}


def _decode(data):
    head = data[:2000].decode("ascii", "ignore").lower()
    m = re.search(r"charset=[\"']?([a-z0-9_-]+)", head)
    for enc in ([m.group(1)] if m else []) + ["utf-8", "tis-620", "cp874"]:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def items():
    """The kinds of rice stored (newest label of each): [{'key', 'label', 'last_date', 'usd'}]."""
    by = {}
    for r in col(COL).find({}, NO_ID).sort([("PRICE_DATE", 1)]):
        by[r["ITEM_KEY"]] = {"key": r["ITEM_KEY"], "label": r["ITEM_LABEL"], "last_date": r["PRICE_DATE"], "usd": r["USD_TON"]}
    return sorted(by.values(), key=lambda x: x["label"])


def guess_key(mat_code, keys=None):
    """The stored row that the default texts of the SKU point to, or None."""
    wanted = DEFAULT_KEYS.get(mat_code) or ()
    keys = keys if keys is not None else [i["key"] for i in items()]
    if mat_code != "BROKEN":                  # update 81: a whole-grain SKU never takes a broken rice row
        keys = [k for k in keys if not any(normalize(b) in k for b in BROKEN_WORDS)]
    for w in wanted:
        nw = normalize(w)
        hits = [k for k in keys if nw in k]
        if hits:                              # the plainest row: "hom mali 100%" before "hom mali 100% (new crop, grade B)"
            return min(hits, key=len)
    return None


BROKEN_WORDS = ("broken", "ปลาย", "a1super", "a.1", "เอวัน", "brokens")


def series(item_key, start, fx):
    """[(day iso, THB/kg)] of a row from `start`."""
    if not item_key or not fx:
        return []
    return [(r["PRICE_DATE"], round_half_up(r["USD_TON"] * fx / 1000.0, 2))
            for r in col(COL).find({"ITEM_KEY": item_key, "PRICE_DATE": {"$gte": start.isoformat()}}, NO_ID).sort([("PRICE_DATE", 1)])]


def label_of(item_key):
    r = col(COL).find_one({"ITEM_KEY": item_key}, NO_ID, sort=[("PRICE_DATE", -1)]) if item_key else None
    return (r["ITEM_LABEL"], r["USD_TON"]) if r else (None, None)


def latest_fetch():
    return col(COL).find_one({}, NO_ID, sort=[("FETCH_DATE", -1)])


