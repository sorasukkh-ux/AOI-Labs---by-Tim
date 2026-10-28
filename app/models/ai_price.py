"""Market prices found on the web by an AI with search (update 77, Tim: "Gemini can share all the information").

Once a day (and with "Search prices now" on AI > Price analysis) the server asks an AI that can search the web for
today's market price of every traded SKU in the mill's area, as THB/kg with a low / high range, the source name and
its link. The answer is checked and kept per SKU and day in AOITS_AIPRICE (source AIWEB on the analysis).
"Monthly history (estimate)" asks the same AI once for the average of every month of the last 3 years
(AIHIST, ESTIMATE_FLG true): a rough line so every SKU has a graph at once, clearly marked as an estimate.

Which AI:
  * AOI_GEMINI_API_KEY (.env, Google AI Studio) -> Gemini with Google Search (AOI_GEMINI_MODEL, default gemini-2.5-flash)
  * else AOI_AI_API_KEY (the key of Ask AI)        -> Claude with its web search tool (AOI_AI_MODEL)
  * neither: the source is off.
The key stays on the server. Rules for a value: a THB/kg number (per-ton / per-100-kg numbers are converted) between
1 and 150, low <= high, and a source name or link - otherwise it is not kept. An AI can be wrong: the value is one
source among the others, never the base price unless chosen, and the analysis warns when it is far from them.
"""
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, timedelta

from flask import current_app

from .base import col, NO_ID, ValidationError, round_half_up
from ..timeutil import now_utc, today_local

COL = "AOITS_AIPRICE"
DAILY, HISTORY = "AIWEB", "AIHIST"
MIN_KG, MAX_KG = 1.0, 150.0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent"

# what each SKU is, for the question (Thai market names, wholesale / farm-gate, not retail)
SKU_TEXT = {
    "PADDY_HM": "ข้าวเปลือกเจ้าหอมมะลิ 105 (jasmine paddy Hom Mali 105) - farm-gate / mill buying price",
    "RICE_HM": "ข้าวสารหอมมะลิ 100% (jasmine milled rice) - wholesale, not retail bags",
    "PADDY": "ข้าวเปลือกเจ้า ข้าวขาว ที่ไม่ใช่หอมมะลิ (white non-jasmine paddy, e.g. RD / Pathum Thani, 15% moisture) - farm-gate",
    "RICE_W5": "ข้าวสารขาว 5% (white milled rice 5%) - wholesale",
    "PADDY_GL": "ข้าวเปลือกเหนียว กข6 (glutinous paddy RD6) - farm-gate",
    "RICE_GL": "ข้าวสารเหนียว กข6 10% (glutinous milled rice) - wholesale",
    "BROKEN": "ปลายข้าว (broken rice, A1 / ปลายข้าวขาว) - wholesale",
    "BRAN": "รำข้าว รำละเอียด (fine rice bran) - wholesale",
}


def region():
    return (os.environ.get("AOI_AI_PRICE_REGION") or "ขอนแก่น / ภาคตะวันออกเฉียงเหนือ (Khon Kaen, Isan)").strip()


def provider():
    """('gemini' | 'claude' | None, model)."""
    if (os.environ.get("AOI_GEMINI_API_KEY") or "").strip():
        return "gemini", (os.environ.get("AOI_GEMINI_MODEL") or "gemini-2.5-flash").strip()
    if current_app.config.get("AI_API_KEY"):
        return "claude", current_app.config.get("AI_MODEL")
    return None, None


# ---------- calling the AI ----------
def _post(url, body, headers, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=dict({"content-type": "application/json"}, **headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        current_app.logger.warning("AI price search: HTTP %s %s", error.code, error.read()[:300])
        raise ValidationError("apErrService")
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        current_app.logger.warning("AI price search failed: %s", error)
        raise ValidationError("apErrService")


def _ask(prompt, max_tokens):
    """(text of the answer, [source links found by the search])."""
    name, model = provider()
    if name == "gemini":
        reply = _post(GEMINI_URL % model, {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                           "tools": [{"google_search": {}}],
                                           "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens}},
                      {"x-goog-api-key": os.environ["AOI_GEMINI_API_KEY"].strip()})
        cand = (reply.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []))
        links = [c.get("web", {}).get("uri") for c in (cand.get("groundingMetadata") or {}).get("groundingChunks", []) if c.get("web")]
        return text, [u for u in links if u]
    if name == "claude":
        cfg = current_app.config
        reply = _post(cfg["AI_API_URL"], {"model": model, "max_tokens": max_tokens,
                                          "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
                                          "messages": [{"role": "user", "content": prompt}]},
                      {"x-api-key": cfg["AI_API_KEY"], "anthropic-version": "2023-06-01"})
        text, links = "", []
        for part in reply.get("content", []):
            if part.get("type") == "text":
                text += part.get("text", "")
                links += [c.get("url") for c in part.get("citations") or [] if c.get("url")]
        return text, links
    raise ValidationError("apErrNotConfigured")


def _json_of(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValidationError("apErrAnswer")
    try:
        return json.loads(m.group(0))
    except ValueError:
        raise ValidationError("apErrAnswer")


def to_kg(value, unit=None):
    """THB/kg from a number that may be per ton or per 100 kg (the unit when given, else its size: above 5,000 is
    per ton, above 150 per 100 kg); None when it is not a believable price."""
    try:
        v = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    u = (unit or "").lower().replace(" ", "")
    if "ton" in u or "ตัน" in u:
        v /= 1000.0
    elif "100" in u or "ร้อย" in u or "quintal" in u:
        v /= 100.0
    elif v > 5000:
        v /= 1000.0
    elif v > MAX_KG:
        v /= 100.0
    return round_half_up(v, 2) if MIN_KG <= v <= MAX_KG else None


# ---------- today's prices ----------
def _prompt_today(day):
    skus = "\n".join("- %s: %s" % (k, v) for k, v in SKU_TEXT.items())
    return ("Search the web for the current Thai market prices of these rice products in %s, as of %s or the most "
            "recent date published. Prefer official or trade sources (กรมการค้าภายใน, สมาคมโรงสีข้าวไทย, สำนักงานเศรษฐกิจ"
            "การเกษตร, ธ.ก.ส., provincial commerce offices, local mill price boards). Jasmine (Hom Mali) and white rice are "
            "different qualities with clearly different prices - never use a jasmine price for white rice.\n%s\n\n"
            "Answer with ONLY one JSON object, no other text:\n"
            '{"items": [{"sku": "PADDY_HM", "low": 0, "high": 0, "unit": "THB/kg", "date": "YYYY-MM-DD", '
            '"source": "name of the source", "url": "https://..."}]}\n'
            "Prices in THB per kg (convert from per ton or per 100 kg). Leave out a product you cannot find - never guess."
            % (region(), day.isoformat(), skus))


def fetch(ope_user):
    """Asks the AI for today's prices and keeps the good ones. Returns {'saved', 'skipped', 'provider'}."""
    name, model = provider()
    if not name:
        raise ValidationError("apErrNotConfigured")
    today = today_local()
    text, links = _ask(_prompt_today(today), 2500)
    data = _json_of(text)
    saved, skipped = [], []
    for item in data.get("items") or []:
        code = str(item.get("sku") or "").strip().upper()
        if code not in SKU_TEXT:
            continue
        low, high = to_kg(item.get("low"), item.get("unit")), to_kg(item.get("high"), item.get("unit"))
        low, high = (low or high), (high or low)
        source = str(item.get("source") or "").strip()[:120]
        own_url = str(item.get("url") or "").strip()[:400]
        if not low or not (source or own_url):                # the answer must name where the price comes from
            skipped.append(code)
            continue
        url = own_url or (links[0] if links else "")
        if low > high:
            low, high = high, low
        when = _day(item.get("date")) or today
        if when > today or (today - when).days > 45:            # not today's market any more
            skipped.append(code)
            continue
        doc = {"PRICE_DATE": today.isoformat(), "MAT_CODE": code, "LOW": low, "HIGH": high,
               "MID": round_half_up((low + high) / 2.0, 2), "SOURCE_NAME": source, "URL": url if url.startswith("http") else "",
               "SOURCE_DATE": when.isoformat(), "SOURCE": DAILY, "ESTIMATE_FLG": False, "PROVIDER": "%s:%s" % (name, model),
               "REGION": region(), "FETCH_DATE": now_utc(), "OPE_USER": ope_user}
        col(COL).update_one({"PRICE_DATE": doc["PRICE_DATE"], "MAT_CODE": code, "SOURCE": DAILY}, {"$set": doc}, upsert=True)
        saved.append(code)
    return {"saved": saved, "skipped": skipped, "provider": name}


def _day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


# ---------- 3-year monthly estimate ----------
def fetch_history(ope_user, months=36):
    """Asks once for the monthly average of every SKU over the last `months` months (AIHIST, an estimate)."""
    name, model = provider()
    if not name:
        raise ValidationError("apErrNotConfigured")
    first = (today_local().replace(day=1) - timedelta(days=months * 31)).replace(day=1)
    skus = "\n".join("- %s: %s" % (k, v) for k, v in SKU_TEXT.items())
    prompt = ("Search the web for the history of Thai market prices of these rice products in %s from %s to now:\n%s\n\n"
              "Give the AVERAGE price of every month (THB per kg) you can support with published data - official or trade "
              "sources. Jasmine and white rice are different qualities with different prices. Answer with ONLY one JSON "
              "object, no other text:\n"
              '{"items": [{"sku": "PADDY_HM", "month": "YYYY-MM", "avg": 0}], "sources": ["https://..."]}\n'
              "Leave out months you cannot support - never guess." % (region(), first.strftime("%Y-%m"), skus))
    text, links = _ask(prompt, 16000)
    data = _json_of(text)
    srcs = [str(u)[:400] for u in (data.get("sources") or []) + links if str(u).startswith("http")][:10]
    if not srcs:
        raise ValidationError("apErrNoSources")
    saved = 0
    for item in data.get("items") or []:
        code = str(item.get("sku") or "").strip().upper()
        month = str(item.get("month") or "")[:7]
        value = to_kg(item.get("avg"))
        if code not in SKU_TEXT or not value or not re.match(r"^\d{4}-\d{2}$", month):
            continue
        day = month + "-15"
        if not (first.isoformat() <= day <= today_local().isoformat()):
            continue
        col(COL).update_one({"PRICE_DATE": day, "MAT_CODE": code, "SOURCE": HISTORY},
                            {"$set": {"PRICE_DATE": day, "MAT_CODE": code, "LOW": value, "HIGH": value, "MID": value,
                                      "SOURCE_NAME": "AI estimate", "URL": srcs[0], "SOURCES": srcs, "SOURCE": HISTORY,
                                      "ESTIMATE_FLG": True, "PROVIDER": "%s:%s" % (name, model), "REGION": region(),
                                      "FETCH_DATE": now_utc(), "OPE_USER": ope_user}}, upsert=True)
        saved += 1
    return {"saved": saved, "provider": name}


# ---------- reading ----------
def series(code, source, start):
    """[(day iso, THB/kg)] from `start`."""
    return [(d["PRICE_DATE"], d["MID"]) for d in col(COL).find({"MAT_CODE": code, "SOURCE": source,
                                                                 "PRICE_DATE": {"$gte": start.isoformat()}}, NO_ID).sort([("PRICE_DATE", 1)])]


def latest(code=None):
    """The newest daily answers: {MAT_CODE: doc} (or one doc)."""
    out = {}
    for d in col(COL).find({"SOURCE": DAILY}, NO_ID).sort([("PRICE_DATE", 1)]):
        out[d["MAT_CODE"]] = d
    return out.get(code) if code else out


def last_fetch():
    return col(COL).find_one({}, NO_ID, sort=[("FETCH_DATE", -1)])


def daily_due():
    """True once a day when a provider is set and today's answers are not there yet."""
    from .counter import Counter
    if not provider()[0] or col(COL).find_one({"SOURCE": DAILY, "PRICE_DATE": today_local().isoformat()}):
        return False
    return Counter.next("AIPRICE-" + today_local().strftime("%Y%m%d")) == 1     # one server worker takes the turn
