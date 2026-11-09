"""Update 120: market interest from Google Trends (AOITS_GTREND, AOITS_GTRENDBATCH, AOIMS_GTRENDTERM).

Staff download "Interest over time" as CSV on trends.google.com and upload it on AI > Price analysis. Google Trends
values are RELATIVE (0-100, 100 = the peak of that download), so every upload is kept as its own batch and a term is
always read from the newest batch that has it - values of different downloads are never mixed.
Signals per term: latest value, the last 4 points against the 4 before (rising / falling / flat at +-15 %), the same
time a year earlier when the data goes back that far, and the calendar month with the highest average (season).
"""
import csv
import io
import re
from datetime import date, timedelta

from .base import NO_ID, ValidationError, col
from .counter import Counter
from ..timeutil import now_utc

ROWS, BATCH, TERMS = "AOITS_GTREND", "AOITS_GTRENDBATCH", "AOIMS_GTRENDTERM"
MAX_BYTES = 2 * 1024 * 1024
KINDS = ("HM", "WH", "GL", "MILLING", "OTHER")          # what a term tells about: a rice variety, milling demand
RISE_PCT = 15.0
TIME_HEADS = ("week", "day", "month", "time", "date", "สัปดาห์", "วัน", "เดือน", "เวลา", "วันที่")
GUESS = [("HM", ("หอมมะลิ", "jasmine", "hom mali")), ("GL", ("เหนียว", "glutinous", "sticky")),
         ("WH", ("ข้าวเจ้า", "ข้าวขาว", "white rice")), ("MILLING", ("สีข้าว", "โรงสี", "rice mill", "milling"))]


def guess_kind(term):
    low = term.lower()
    for kind, words in GUESS:
        if any(w in low for w in words):
            return kind
    return "OTHER"


def _day(text):
    """A Trends date cell: 2025-11-02, 2025-11 (month), 2025-11-02T10 (hour) or 2025-11-02 - 2025-11-08 (week range)."""
    text = (text or "").strip().strip('"')
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if not m:
        m2 = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", text)          # 11/2/2025 (some locales)
        if not m2:
            return None
        try:
            return date(int(m2.group(3)), int(m2.group(1)), int(m2.group(2)))
        except ValueError:
            return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return None


def _value(text):
    text = (text or "").strip().replace(",", "")
    if text.startswith("<"):                                            # "<1": searched, but below 1
        return 0.5
    try:
        return float(text)
    except ValueError:
        return None


def _term_geo(head):
    """'ข้าวหอมมะลิ: (Thailand)' -> ('ข้าวหอมมะลิ', 'Thailand')."""
    m = re.match(r"^(.*?):\s*\((.*)\)\s*$", head.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (head.strip(), "")


def parse_csv(data):
    """{'terms': [(term, geo)], 'points': [(day, [values])], 'gran': D/W/M}. Raises gtErrFormat / gtErrGeo."""
    if len(data) > MAX_BYTES:
        raise ValidationError("gtErrSize", mb=MAX_BYTES // (1024 * 1024))
    for enc in ("utf-8-sig", "utf-16", "cp874"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValidationError("gtErrFormat")
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    head_at = None
    for i, r in enumerate(rows[:-1]):
        if len(r) >= 2 and (r[0].strip().lower() in TIME_HEADS or (_day(rows[i + 1][0]) and not _day(r[0]))):
            head_at = i
            break
    if head_at is None:
        if any(r and r[0].strip().lower() in ("region", "ภูมิภาค", "city", "เมือง", "metro", "subregion", "province", "จังหวัด") for r in rows[:5]):
            raise ValidationError("gtErrGeo")
        raise ValidationError("gtErrFormat")
    terms = [_term_geo(h) for h in rows[head_at][1:] if h.strip()]
    points = []
    for r in rows[head_at + 1:]:
        d = _day(r[0])
        if not d:
            continue
        points.append((d, [_value(r[k + 1]) if k + 1 < len(r) else None for k in range(len(terms))]))
    if not terms or len(points) < 2:
        raise ValidationError("gtErrFormat")
    points.sort(key=lambda p: p[0])
    step = (points[-1][0] - points[0][0]).days / max(1, len(points) - 1)
    gran = "D" if step < 3 else ("W" if step < 20 else "M")
    return {"terms": terms, "points": points, "gran": gran}


def import_csv(data, file_name, ope_user):
    """Saves one download as a batch. Returns the batch record."""
    p = parse_csv(data)
    batch_id = "GT%05d" % Counter.next("GTREND")
    docs = []
    for k, (term, geo) in enumerate(p["terms"]):
        for d, values in p["points"]:
            if values[k] is not None:
                docs.append({"BATCH_ID": batch_id, "TERM": term[:100], "GEO": geo[:60], "DAY": d.isoformat(), "VALUE": values[k]})
        if not col(TERMS).find_one({"TERM": term[:100], "GEO": geo[:60]}):
            col(TERMS).insert_one({"TERM": term[:100], "GEO": geo[:60], "KIND": guess_kind(term), "REGIST_DATE": now_utc(), "REGIST_USER": ope_user})
    if not docs:
        raise ValidationError("gtErrFormat")
    col(ROWS).insert_many(docs)
    batch = {"BATCH_ID": batch_id, "FILE_NAME": (file_name or "")[:120], "TERMS": [t for t, _ in p["terms"]][:20],
             "GEO": p["terms"][0][1][:60], "GRAN": p["gran"], "DATE_FROM": p["points"][0][0].isoformat(),
             "DATE_TO": p["points"][-1][0].isoformat(), "ROWS": len(docs), "REGIST_DATE": now_utc(), "REGIST_USER": ope_user}
    col(BATCH).insert_one(dict(batch))
    return batch


def delete_batch(batch_id):
    if not col(BATCH).find_one({"BATCH_ID": batch_id}):
        raise ValidationError("gtErrNoBatch")
    col(ROWS).delete_many({"BATCH_ID": batch_id})
    col(BATCH).delete_one({"BATCH_ID": batch_id})


def set_kind(term, geo, kind):
    if kind not in KINDS:
        raise ValidationError("gtErrKind")
    col(TERMS).update_one({"TERM": term, "GEO": geo}, {"$set": {"KIND": kind}}, upsert=True)


def batches(limit=20):
    return list(col(BATCH).find({}, NO_ID).sort([("REGIST_DATE", -1)]).limit(limit))


def series(term, geo):
    """[(day iso, value)] of the newest batch that has the term, and that batch."""
    for b in col(BATCH).find({}, NO_ID).sort([("REGIST_DATE", -1)]):
        rows = list(col(ROWS).find({"BATCH_ID": b["BATCH_ID"], "TERM": term, "GEO": geo}, NO_ID).sort([("DAY", 1)]))
        if rows:
            return [(r["DAY"], r["VALUE"]) for r in rows], b
    return [], None


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def signal(points, gran):
    """Latest value, recent change, a year ago, the season peak month and a label rising / falling / flat."""
    vals = [v for _, v in points]
    if len(vals) < 2:
        return None
    n = 4 if gran != "M" else 2
    now_avg, prev_avg = _avg(vals[-n:]), _avg(vals[-2 * n:-n])
    change = round((now_avg - prev_avg) / prev_avg * 100, 1) if prev_avg else None
    last_day = date.fromisoformat(points[-1][0])
    year_ago = None
    target = last_day - timedelta(days=365)
    near = [v for d, v in points if abs((date.fromisoformat(d) - target).days) <= (10 if gran != "M" else 20)]
    if near and near[0]:
        year_ago = round((vals[-1] - near[0]) / near[0] * 100, 1)
    months = {}
    for d, v in points:
        months.setdefault(int(d[5:7]), []).append(v)
    peak = max(months.items(), key=lambda kv: _avg(kv[1]))[0] if len(points) >= 12 else None
    label = "flat" if change is None or abs(change) < RISE_PCT else ("rising" if change > 0 else "falling")
    return {"last": vals[-1], "last_day": points[-1][0], "change_pct": change, "yoy_pct": year_ago, "peak_month": peak, "label": label,
            "max": max(vals)}


def overview():
    """Every known term with its newest series and signal (for the card and the AI)."""
    out = []
    for t in col(TERMS).find({}, NO_ID).sort([("TERM", 1)]):
        pts, b = series(t["TERM"], t.get("GEO", ""))
        if not pts:
            continue
        out.append({"term": t["TERM"], "geo": t.get("GEO", ""), "kind": t.get("KIND", "OTHER"), "points": pts, "batch": b,
                    "signal": signal(pts, b["GRAN"])})
    return out


def for_ai(limit=12):
    """Market interest for the AI snapshot - signals only, no series."""
    return [{"term": o["term"], "region": o["geo"], "about": o["kind"], "batch_date": o["batch"]["DATE_TO"],
             **{k: v for k, v in (o["signal"] or {}).items() if k != "max"}} for o in overview()[:limit]]


COLORS = ["#2563eb", "#d97706", "#7c3aed", "#059669", "#db2777", "#0891b2", "#65a30d", "#dc2626"]


def chart(items, w=1000, h=260, pad=36):
    """SVG geometry for the card: one line per term (each on its own 0-100 scale), the dates of the newest batch."""
    lines = [i for i in items if i["points"]][:8]
    if not lines:
        return None
    days = sorted({d for i in lines for d, _ in i["points"]})
    d0, d1 = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    span = max(1, (d1 - d0).days)
    X = lambda d: pad + (date.fromisoformat(d) - d0).days / span * (w - 2 * pad)
    Y = lambda v: h - pad - (v or 0) / 100.0 * (h - 2 * pad)
    out = []
    for k, i in enumerate(lines):
        out.append({"term": i["term"], "color": COLORS[k % len(COLORS)],
                    "path": "M" + " L".join("%.1f %.1f" % (X(d), Y(v)) for d, v in i["points"]),
                    "last": i["points"][-1][1], "lx": X(i["points"][-1][0]), "ly": Y(i["points"][-1][1])})
    ticks = []
    y = d0.year
    while date(y, 1, 1) <= d1:
        if date(y, 1, 1) >= d0:
            ticks.append({"x": X(date(y, 1, 1).isoformat()), "label": str(y)})
        y += 1
    if not ticks:                                                        # less than a year: month marks
        m = date(d0.year, d0.month, 1)
        while m <= d1:
            if m >= d0:
                ticks.append({"x": X(m.isoformat()), "label": m.strftime("%m/%y")})
            m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return {"w": w, "h": h, "pad": pad, "lines": lines and out, "ticks": ticks, "from": days[0], "to": days[-1],
            "grid": [{"y": Y(v), "v": v} for v in (0, 25, 50, 75, 100)]}
