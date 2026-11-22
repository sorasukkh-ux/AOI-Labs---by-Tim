"""Update 134: the fengshuiX Bazi partner API (https://bazi.fengshuix.com, shown by อ.แบ๊ท at AI Expo Thailand).

Used for one thing: a short personal reading in the customer's birthday card (models/birthday.py). One call per
customer per year, only for customers who agreed to birthday cards and have a gender (the API needs it); the birth
time is optional (without it the hour pillar is left out). When the API is not set up, fails or answers something
AOI cannot read, the card still goes, with AOI's own zodiac line only.

Settings (.env, never in the code or the chat):
    AOI_BAZI_API_KEY=bz_live_...      the partner key (Bazi site > /partner/keys); empty = not used
    AOI_BAZI_ENDPOINT=/api/v2/daily   the endpoint (same "birth" body as /api/v2/transit)
    AOI_BAZI_MAX_COST=                optional: refuse a call that costs more tokens than this (X-Max-Cost)

The request (partner guide): POST JSON {"birth": {"date", "time"?, "gender": male|female}, "lang": "th"} with headers
x-api-key, Idempotency-Key (one per customer and year, so a retry within 10 minutes is not charged twice) and
X-End-User (the customer code - never a name or phone). The answer is {"success", "data", "cost", ...}; the guide does
not document the /daily "data", so reading() looks for the first readable text in it (summary, verdict, message ...).
Test it on Marketing > วันเกิดลูกค้า > ทดสอบ API, which shows the raw answer.
"""
import json
import os
import urllib.error
import urllib.request
import uuid

BASE = "https://bazi.fengshuix.com"
TEXT_KEYS = ("summary", "verdict", "headline", "message", "advice", "text", "description", "overview", "title")
MAX_TEXT = 280


def key():
    return (os.environ.get("AOI_BAZI_API_KEY") or "").strip()


def configured():
    return bool(key())


def endpoint():
    path = (os.environ.get("AOI_BAZI_ENDPOINT") or "/api/v2/daily").strip()
    return path if path.startswith("/api/") else "/api/v2/daily"


def _post(path, body, headers, timeout=15):
    """(HTTP status, JSON or None). Network problems -> (None, None)."""
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=dict(headers, **{"Content-Type": "application/json", "Accept": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read().decode("utf-8"))
        except ValueError:
            return error.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def call(birth_date, gender, birth_time=None, end_user=None, idem=None, lang="th"):
    """One reading. Returns {"ok", "status", "answer", "text", "error"}."""
    if not configured():
        return {"ok": False, "status": None, "answer": None, "text": "", "error": "bzErrNoKey"}
    if gender not in ("male", "female"):
        return {"ok": False, "status": None, "answer": None, "text": "", "error": "bzErrGender"}
    birth = {"date": birth_date, "gender": gender}
    if birth_time:
        birth["time"] = birth_time
    headers = {"x-api-key": key(), "Idempotency-Key": idem or str(uuid.uuid4())}
    if end_user:
        headers["X-End-User"] = "".join(ch for ch in str(end_user) if ch.isalnum() or ch in "_.:-")[:64]
    max_cost = (os.environ.get("AOI_BAZI_MAX_COST") or "").strip()
    if max_cost:
        headers["X-Max-Cost"] = max_cost
    status, answer = _post(endpoint(), {"birth": birth, "lang": lang}, headers)
    if status is None:
        return {"ok": False, "status": None, "answer": None, "text": "", "error": "bzErrNetwork"}
    if not isinstance(answer, dict) or not answer.get("success"):
        detail = (answer or {}).get("error") if isinstance(answer, dict) else None
        return {"ok": False, "status": status, "answer": answer, "text": "", "error": "bzErrHttp", "detail": "%s %s" % (status, detail or "")}
    text = reading(answer.get("data"))
    return {"ok": bool(text), "status": status, "answer": answer, "text": text, "error": None if text else "bzErrNoText"}


def reading(data, depth=0):
    """The first readable text in the answer's data: a known key first, then nested objects / lists."""
    if depth > 4 or data is None:
        return ""
    if isinstance(data, str):
        return _clip(data) if len(data.strip()) >= 8 else ""
    if isinstance(data, dict):
        for k in TEXT_KEYS:
            v = data.get(k)
            if isinstance(v, str) and len(v.strip()) >= 8:
                return _clip(v)
        for k, v in data.items():
            if k in ("subject", "resolved", "warnings", "request_id", "endpoint"):
                continue
            if isinstance(v, (dict, list)):
                found = reading(v, depth + 1)
                if found:
                    return found
        return ""
    if isinstance(data, list):
        for v in data[:5]:
            found = reading(v, depth + 1)
            if found:
                return found
    return ""


def _clip(text):
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1].rstrip() + "…"


def idem_for(cust_code, year):
    """The same Idempotency-Key for a customer's card of one year (a retry is not charged twice)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "aoi-bday:%s:%s" % (cust_code, year)))
