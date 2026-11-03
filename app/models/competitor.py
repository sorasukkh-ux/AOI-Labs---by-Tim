"""Update 109: Marketing > Competitor analysis.

Staff record the competitors of a branch (AOIMS_COMPETITOR) and the prices they saw or heard (AOITS_COMPPRICE: paddy
buying price, rice / bran / broken selling price, milling fee, THB per kg). The page compares each competitor's latest
price with the mill's own current price (gap in THB/kg and %), draws the history, and on request asks the AI service for
a short summary with suggested actions (AOITS_COMPAI). Only prices and the competitors' names go to the AI: no customers.
"""
import json
import os
from datetime import date, datetime, timedelta

from flask import current_app

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, to_number, round_half_up
from .branch import Branch
from .counter import Counter
from .price import Price
from . import variety as var
from ..timeutil import now_utc, now_local, to_local, local_tz

COMP = "AOIMS_COMPETITOR"
PRICE = "AOITS_COMPPRICE"
AI = "AOITS_COMPAI"
SKUS = ("PADDY_HM", "PADDY", "PADDY_GL", "RICE_HM", "RICE_W5", "RICE_GL", "BRAN", "BROKEN")
FEE = "MILLFEE"
SOURCES = ("board", "visit", "customer", "phone", "online", "other")
FRESH_DAYS = 60                   # a competitor price older than this is not compared
PERIODS = (30, 90, 180, 365)
PRICE_MAX = 1000
AI_LIMIT_PER_DAY = 10


def items():
    """[(key, MAT_CODE, side)] in display order: paddy bought (B), rice / bran / broken sold (S), the milling fee (F)."""
    out = [("%s:B" % c, c, "B") for c in SKUS if c.startswith("PADDY")]
    out += [("%s:S" % c, c, "S") for c in SKUS if not c.startswith("PADDY")]
    out.append(("%s:F" % FEE, FEE, "F"))
    return out


ITEM_KEYS = tuple(i[0] for i in items())


def item_name(code, lang="th"):
    from ..i18n import t_in
    if code == FEE:
        return t_in(lang, "coFee")
    kind, variety = var.INFO.get(code, (None, None))
    if not kind:
        return code
    return t_in(lang, var.KIND_KEYS[kind]) + ((" (%s)" % t_in(lang, var.VAR_KEYS[variety])) if variety else "")


# ---------- competitors ----------
def competitors(branch_id, active_only=False):
    q = {"BRANCH_ID": branch_id}
    if active_only:
        q["STATUS"] = "A"
    return list(col(COMP).find(q, NO_ID).sort([("COMP_NAME", 1)]))


def get(comp_id):
    return col(COMP).find_one({"COMP_ID": comp_id}, NO_ID) if comp_id else None


def save_competitor(branch_id, form, ope_user):
    if not Branch.get(branch_id):
        raise ValidationError("errBranchUnknown")
    name = clean_text(form.get("COMP_NAME"), 80)
    if not name:
        raise ValidationError("coErrName")
    try:
        km = to_number(form.get("DISTANCE_KM"))
    except ValueError:
        raise ValidationError("errNumber")
    if km is not None and not (0 <= km <= 1000):
        raise ValidationError("errNumber")
    values = {"COMP_NAME": name, "LOCATION": clean_text(form.get("LOCATION"), 120) or None,
              "DISTANCE_KM": None if km is None else round_half_up(km, 1), "NOTE": clean_text(form.get("NOTE"), 200) or None,
              "STATUS": "I" if form.get("STATUS") == "I" else "A"}
    current = get(form.get("COMP_ID"))
    if current:
        if current["BRANCH_ID"] != branch_id:
            raise ValidationError("coErrUnknown")
        values.update(audit_update(ope_user))
        col(COMP).update_one({"COMP_ID": current["COMP_ID"]}, {"$set": values})
        return get(current["COMP_ID"])
    for c in competitors(branch_id):
        if c["COMP_NAME"].strip().lower() == name.lower():
            raise ValidationError("coErrDup", name=c["COMP_NAME"])
    values.update({"COMP_ID": "CM%04d" % Counter.next("COMP"), "BRANCH_ID": branch_id}, **audit_new(ope_user))
    col(COMP).insert_one(dict(values))
    return get(values["COMP_ID"])


# ---------- prices ----------
def save_prices(branch_id, form, ope_user):
    """One sighting: a competitor, a date and a source, with any number of item prices (PR_<item key>).
    Returns how many prices were saved. Our own price of that day is kept beside each one (OUR_PRICE)."""
    comp = get(form.get("COMP_ID"))
    if not comp or comp["BRANCH_ID"] != branch_id:
        raise ValidationError("coErrPick")
    try:
        day = now_local().date() if not form.get("PRICE_DATE") else date.fromisoformat(form["PRICE_DATE"])
    except ValueError:
        raise ValidationError("coErrDate")
    today = now_local().date()
    if day > today or day < today - timedelta(days=400):
        raise ValidationError("coErrDate")
    source = form.get("SOURCE") if form.get("SOURCE") in SOURCES else "other"
    note = clean_text(form.get("NOTE"), 120) or None
    rows = []
    for key, code, side in items():
        raw = (form.get("PR_" + key.replace(":", "_")) or "").strip()
        if not raw:
            continue
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            raise ValidationError("coErrPrice", item=item_name(code))
        if not (0 < value <= PRICE_MAX):
            raise ValidationError("coErrPrice", item=item_name(code))
        rows.append((code, side, round_half_up(value, 2)))
    if not rows:
        raise ValidationError("coErrNoPrice")
    when = datetime(day.year, day.month, day.day, 12, 0, tzinfo=local_tz())
    now = now_utc()
    for code, side, value in rows:
        doc = {"PRICE_ID": "CQ%07d" % Counter.next("COMPPRICE"), "BRANCH_ID": branch_id, "COMP_ID": comp["COMP_ID"],
               "MAT_CODE": code, "SIDE": side, "PRICE": value, "PRICE_DATE": when, "SOURCE": source, "NOTE": note,
               "OUR_PRICE": our_price(branch_id, code, side), "REGIST_DATE": now, "REGIST_USER": ope_user}
        col(PRICE).insert_one(dict(doc))
    return len(rows)


def delete_price(branch_id, price_id):
    doc = col(PRICE).find_one({"PRICE_ID": price_id}, NO_ID)
    if not doc or doc["BRANCH_ID"] != branch_id:
        raise ValidationError("coErrUnknown")
    col(PRICE).delete_one({"PRICE_ID": price_id})


def our_price(branch_id, code, side):
    if code == FEE:
        b = Branch.get(branch_id) or {}
        unit, fee = float(b.get("SYSP_MILLFEEUNITKG") or 0), float(b.get("SYSP_MILLFEEPERUNIT") or 0)
        return round_half_up(fee / unit, 3) if unit > 0 else None
    p = Price.get(branch_id, code) or {}
    value = float(p.get("PRICE_BUY" if side == "B" else "PRICE_SELL") or 0)
    return value or None


def recent(branch_id, limit=40):
    names = {c["COMP_ID"]: c["COMP_NAME"] for c in competitors(branch_id)}
    rows = list(col(PRICE).find({"BRANCH_ID": branch_id}, NO_ID).sort([("PRICE_DATE", -1), ("REGIST_DATE", -1)]).limit(limit))
    for r in rows:
        r["comp"] = names.get(r["COMP_ID"], r["COMP_ID"])
        r["item"] = item_name(r["MAT_CODE"])
    return rows


# ---------- analysis ----------
def worse(side, gap):
    """True when the competitor's price is better for the customer than ours (a risk for us)."""
    if gap is None:
        return False
    return gap > 0 if side == "B" else gap < 0


def compare(branch_id):
    """Per item: our price and every active competitor's latest price (within FRESH_DAYS) with the gap."""
    comps = competitors(branch_id, active_only=True)
    since = now_utc() - timedelta(days=FRESH_DAYS)
    latest = {}
    for r in col(PRICE).find({"BRANCH_ID": branch_id, "PRICE_DATE": {"$gte": since}}, NO_ID).sort([("PRICE_DATE", 1), ("REGIST_DATE", 1)]):
        latest[(r["COMP_ID"], r["MAT_CODE"], r["SIDE"])] = r
    out = []
    for key, code, side in items():
        ours = our_price(branch_id, code, side)
        cells = []
        for c in comps:
            r = latest.get((c["COMP_ID"], code, side))
            if not r:
                cells.append(None)
                continue
            gap = round_half_up(r["PRICE"] - ours, 2) if ours else None
            cells.append({"price": r["PRICE"], "date": to_local(r["PRICE_DATE"]).strftime("%d/%m"), "source": r["SOURCE"],
                          "gap": gap, "pct": round_half_up(gap / ours * 100, 1) if ours and gap is not None else None,
                          "worse": worse(side, gap)})
        seen = [x for x in cells if x]
        best = None
        if seen:
            best = max(seen, key=lambda x: x["price"]) if side == "B" else min(seen, key=lambda x: x["price"])
        out.append({"key": key, "code": code, "side": side, "name": item_name(code), "ours": ours, "cells": cells,
                    "n": len(seen), "avg": round_half_up(sum(x["price"] for x in seen) / len(seen), 2) if seen else None,
                    "best": best["price"] if best else None, "risk": bool(best and best["worse"]),
                    "best_gap": best["gap"] if best else None})
    return comps, out


def summary(rows):
    seen = [r for r in rows if r["n"]]
    return {"total": len(rows), "seen": len(seen), "risk": sum(1 for r in seen if r["risk"]),
            "ok": sum(1 for r in seen if not r["risk"])}


def series(branch_id, key, days):
    """Chart data of one item: our price by day (from AOILOG_PRICE) and each competitor's sightings."""
    code, side = key.split(":")
    start = now_local().date() - timedelta(days=days - 1)
    since = now_utc() - timedelta(days=days + 1)
    ours = []
    if code == FEE:
        value = our_price(branch_id, code, side)
        ours = [[start.isoformat(), value], [now_local().date().isoformat(), value]] if value else []
    else:
        from .tv import _price_steps
        steps = _price_steps(branch_id, code, since)
        idx = 1 if side == "B" else 2
        for when, buy, sell in steps:
            day = max(to_local(when).date(), start)
            v = buy if idx == 1 else sell
            if v:
                ours.append([day.isoformat(), v])
        now_v = our_price(branch_id, code, side)
        if now_v:
            ours.append([now_local().date().isoformat(), now_v])
        if ours and ours[0][0] > start.isoformat():          # no change logged before: the price held from the start
            ours.insert(0, [start.isoformat(), ours[0][1]])
    names = {c["COMP_ID"]: c["COMP_NAME"] for c in competitors(branch_id)}
    comp = {}
    for r in col(PRICE).find({"BRANCH_ID": branch_id, "MAT_CODE": code, "SIDE": side, "PRICE_DATE": {"$gte": since}}, NO_ID).sort([("PRICE_DATE", 1)]):
        comp.setdefault(r["COMP_ID"], {"name": names.get(r["COMP_ID"], r["COMP_ID"]), "points": []})["points"].append(
            [to_local(r["PRICE_DATE"]).date().isoformat(), r["PRICE"]])
    return {"key": key, "name": item_name(code), "side": side, "start": start.isoformat(), "end": now_local().date().isoformat(),
            "ours": ours, "comps": list(comp.values())}


# ---------- AI summary ----------
def ai_ready():
    from .ai_price import provider
    return provider()[0] is not None


def _ai_text(prompt, max_tokens=2000):
    from .ai_price import provider, _post, _gemini_post
    name, model = provider()
    if name == "gemini":
        gen = {"temperature": 0.3, "maxOutputTokens": max_tokens}
        if "flash" in model and "2.5" in model:                # update 112: no hidden "thinking" eating the answer's tokens
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        reply = _gemini_post(model, {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen},
                      {"x-goog-api-key": os.environ["AOI_GEMINI_API_KEY"].strip()})
        cand = (reply.get("candidates") or [{}])[0]
        return "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])).strip(), reply.get("_model") or model
    if name == "claude":
        cfg = current_app.config
        reply = _post(cfg["AI_API_URL"], {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
                      {"x-api-key": cfg["AI_API_KEY"], "anthropic-version": "2023-06-01"})
        return "".join(p.get("text", "") for p in reply.get("content", []) if p.get("type") == "text").strip(), model
    raise ValidationError("coErrAiOff")


def ai_input(branch_id):
    comps, rows = compare(branch_id)
    data = {"branch": branch_id, "today": now_local().date().isoformat(), "unit": "THB per kg",
            "competitors": [{"id": c["COMP_ID"], "name": c["COMP_NAME"], "km": c.get("DISTANCE_KM"), "note": c.get("NOTE")} for c in comps],
            "items": []}
    for r in rows:
        data["items"].append({"item": item_name(r["code"], "en"), "side": {"B": "we buy paddy", "S": "we sell", "F": "milling fee"}[r["side"]],
                              "ours": r["ours"], "competitors": [dict(id=comps[i]["COMP_ID"], **{k: v for k, v in cell.items() if k != "worse"})
                                                                 for i, cell in enumerate(r["cells"]) if cell]})
    # 90-day direction of each competitor price (first and last sighting)
    since = now_utc() - timedelta(days=90)
    trend = {}
    for p in col(PRICE).find({"BRANCH_ID": branch_id, "PRICE_DATE": {"$gte": since}}, NO_ID).sort([("PRICE_DATE", 1)]):
        t = trend.setdefault("%s %s:%s" % (p["COMP_ID"], p["MAT_CODE"], p["SIDE"]), [p["PRICE"], p["PRICE"], 0])
        t[1] = p["PRICE"]
        t[2] += 1
    data["trend_90d"] = {k: {"first": v[0], "last": v[1], "n": v[2]} for k, v in trend.items() if v[2] > 1}
    return data


def ai_summary(branch_id, user_id, lang):
    if not ai_ready():
        raise ValidationError("coErrAiOff")
    day_ago = now_utc() - timedelta(days=1)
    if col(AI).count_documents({"BRANCH_ID": branch_id, "REGIST_DATE": {"$gte": day_ago}}) >= AI_LIMIT_PER_DAY:
        raise ValidationError("coErrAiLimit", n=AI_LIMIT_PER_DAY)
    data = ai_input(branch_id)
    if not any(i["competitors"] for i in data["items"]):
        raise ValidationError("coErrAiNoData")
    prompt = ("You advise a Thai rice mill (AOI Labs) about its local competitors. Write in %s, short and practical, "
              "using only the figures given (THB per kg; 'gap' = competitor minus ours; for paddy buying a higher competitor "
              "price attracts farmers away, for selling and the milling fee a lower competitor price attracts buyers away). "
              "Sections: 1) Where we are behind (biggest gaps first, name the competitor), 2) Where we are ahead, "
              "3) Trends, 4) 3-5 suggested actions (price, promotion, service) with expected effect, 5) Data gaps to fill "
              "(items or competitors without recent prices). Say that prices come from staff reports and may be incomplete. "
              "Never invent numbers.\n\nData (JSON):\n%s" % ("Thai" if lang == "th" else "English", json.dumps(data, ensure_ascii=False, default=str)))
    text, model = _ai_text(prompt)
    if not text:
        raise ValidationError("coErrAiService")
    doc = {"AI_ID": "CA%06d" % Counter.next("COMPAI"), "BRANCH_ID": branch_id, "SUMMARY": text[:8000], "MODEL": model,
           "LANG": lang, "REGIST_DATE": now_utc(), "REGIST_USER": user_id}
    col(AI).insert_one(dict(doc))
    return doc


def last_ai(branch_id):
    return col(AI).find_one({"BRANCH_ID": branch_id}, NO_ID, sort=[("REGIST_DATE", -1)])
