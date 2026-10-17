"""Interface monitoring for the server health screen (update 49): the app's own API, the LINE interface and the
hardware devices.

* API: every request is counted per area and hour in AOITS_MONITOR (web pages, device API /api/v1, LINE webhook,
  public pictures): calls, 4xx, 5xx, slow (> SLOW_MS), the slowest time. Kept 3 days.
* LINE: token / channel secret set, the last webhook call from LINE, the last message LINE accepted, failed pushes in
  the last 24 h (from AOITS_NOTIFY), waiting messages, and - every 30 minutes - a check of the token with LINE
  (GET /v2/bot/info).
* Hardware: every machine in use with remote control and every meter with a device key: online, last report, live
  values fresh, failed or expired commands and faults of the last 24 h, the last meter reading.
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import timedelta

from .base import col, NO_ID
from ..timeutil import now_utc, to_local

COL = "AOITS_MONITOR"
KEEP_DAYS = 3
SLOW_MS = 2000
AREAS = ("web", "device", "line", "public")
_BOT = {"checked": 0.0, "result": None}
BOT_CHECK_SECONDS = 30 * 60


# ---------- the app's API ----------
def area_of(path, blueprint):
    if blueprint == "device_api" or path.startswith("/api/"):
        return "device"
    if path.startswith("/line/webhook"):
        return "line"
    if path.startswith("/line/") or path.startswith("/tv/"):
        return "public"
    return "web"


def record_request(path, blueprint, status, ms):
    """Called after every request (not static files). Never raises."""
    try:
        now = now_utc()
        area = area_of(path, blueprint)
        hour = now.strftime("%Y%m%d%H")
        inc = {"COUNT": 1, "ERR4XX": 1 if 400 <= status < 500 else 0, "ERR5XX": 1 if status >= 500 else 0,
               "SLOW": 1 if ms >= SLOW_MS else 0, "SUM_MS": round(ms, 1)}
        if area == "device" and status in (401, 403):
            inc["REJECTED"] = 1
        col(COL).update_one({"KEY": "api|%s|%s" % (area, hour)},
                            {"$inc": inc, "$max": {"MAX_MS": round(ms, 1)},
                             "$set": {"AREA": area, "HOUR": hour, "LAST_DATE": now}}, upsert=True)
        if area == "line" and status < 400:
            record_event("line_webhook")
        if int(now.timestamp()) % 97 == 0:
            col(COL).delete_many({"KEY": {"$regex": "^api\\|"}, "LAST_DATE": {"$lt": now - timedelta(days=KEEP_DAYS)}})
    except Exception:                                      # noqa: BLE001 - monitoring must never break a request
        pass


def record_event(name):
    try:
        col(COL).update_one({"KEY": "event|" + name}, {"$inc": {"COUNT": 1}, "$set": {"LAST_DATE": now_utc()}}, upsert=True)
    except Exception:                                      # noqa: BLE001
        pass


def event(name):
    return col(COL).find_one({"KEY": "event|" + name}, NO_ID)


def api_stats(hours=1):
    """{area: {calls, err4xx, err5xx, slow, rejected, avg_ms, max_ms}} over the last `hours` hour buckets."""
    now = now_utc()
    keys = {(now - timedelta(hours=h)).strftime("%Y%m%d%H") for h in range(hours + 1)}
    out = {a: {"calls": 0, "err4xx": 0, "err5xx": 0, "slow": 0, "rejected": 0, "sum_ms": 0.0, "max_ms": 0.0} for a in AREAS}
    for d in col(COL).find({"KEY": {"$regex": "^api\\|"}}, NO_ID):
        if d.get("HOUR") not in keys or d.get("AREA") not in out:
            continue
        o = out[d["AREA"]]
        o["calls"] += d.get("COUNT") or 0
        o["err4xx"] += d.get("ERR4XX") or 0
        o["err5xx"] += d.get("ERR5XX") or 0
        o["slow"] += d.get("SLOW") or 0
        o["rejected"] += d.get("REJECTED") or 0
        o["sum_ms"] += d.get("SUM_MS") or 0
        o["max_ms"] = max(o["max_ms"], d.get("MAX_MS") or 0)
    for o in out.values():
        o["avg_ms"] = round(o.pop("sum_ms") / o["calls"], 1) if o["calls"] else None
        o["max_ms"] = round(o["max_ms"], 1) if o["calls"] else None
    return out


def api_hourly(hours=24):
    """[{'hour': 'HH', 'calls', 'errors'}] per hour, oldest first (all areas; errors = 5xx + rejected device keys)."""
    now = now_utc()
    keys = [(now - timedelta(hours=h)) for h in range(hours - 1, -1, -1)]
    by = {k.strftime("%Y%m%d%H"): {"hour": to_local(k).strftime("%H"), "calls": 0, "errors": 0} for k in keys}
    for d in col(COL).find({"KEY": {"$regex": "^api\\|"}}, NO_ID):
        b = by.get(d.get("HOUR"))
        if b is not None:
            b["calls"] += d.get("COUNT") or 0
            b["errors"] += (d.get("ERR5XX") or 0) + (d.get("REJECTED") or 0)
    return [by[k.strftime("%Y%m%d%H")] for k in keys]


# ---------- LINE ----------
def _bot_info():
    """Token check with LINE, at most every 30 minutes: "ok", "invalid", "unreachable" or None (not checked)."""
    from flask import current_app
    from . import notify
    if current_app.config.get("TESTING") or not notify.configured():
        return None
    if time.time() - _BOT["checked"] < BOT_CHECK_SECONDS and _BOT["result"]:
        return _BOT["result"]
    req = urllib.request.Request("https://api.line.me/v2/bot/info", headers={"Authorization": "Bearer " + notify._token()})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = "ok" if resp.status == 200 else "invalid"
    except urllib.error.HTTPError as exc:
        result = "invalid" if exc.code in (401, 403) else "unreachable"
    except Exception:                                      # noqa: BLE001
        result = "unreachable"
    _BOT.update(checked=time.time(), result=result)
    return result


def line_status():
    from . import line_link, notify
    since = now_utc() - timedelta(hours=24)
    failed = waiting = 0
    for d in col(notify.COL).find({"LAST_UPDATE": {"$gte": since}}, {"_id": 0, "NOTIFY_FLG": 1, "ERROR": 1}):
        err = d.get("ERROR") or ""
        if d.get("NOTIFY_FLG") == notify.ERROR and not err.startswith("ln"):
            failed += 1                                     # LINE (or the network) refused it
        elif d.get("NOTIFY_FLG") == notify.UNSENT:
            waiting += 1
    last_ok = col(notify.COL).find_one({"NOTIFY_FLG": notify.SENT}, NO_ID, sort=[("NOTIFY_DATETIME", -1)])
    hook = event("line_webhook")
    return {"token": notify.configured(), "secret": bool(line_link.secret()), "bot": _bot_info(),
            "last_webhook": hook["LAST_DATE"].isoformat() if hook else None,
            "last_sent": last_ok["NOTIFY_DATETIME"].isoformat() if last_ok and last_ok.get("NOTIFY_DATETIME") else None,
            "failed_24h": failed, "waiting_24h": waiting}


# ---------- hardware devices ----------
def devices():
    """[{id, name, kind, online, last_seen, fresh, failed_cmds, faults}] for machines in use with remote control and
    meters, that have a device key."""
    from .automation import Machine, MachineCommand, Meter, SELECTOR, FAULT, TELE_FRESH_SECONDS, CMD_FAILED, CMD_EXPIRED
    since = now_utc() - timedelta(hours=24)
    out = []
    for m in col(Machine.COL).find({}, NO_ID).sort([("BRANCH_ID", 1), ("MACHINE_SEQ", 1)]):
        if m.get("ACTIVE_FLG") is False or not m.get("CONTROL_FLG") or m.get("MACHINE_TYPE") == SELECTOR:
            continue
        seen = m.get("LAST_SEEN")
        failed = col(MachineCommand.COL).count_documents({"MACHINE_ID": m["MACHINE_ID"], "CMD_STATUS": {"$in": [CMD_FAILED, CMD_EXPIRED]},
                                                          "REQ_DATE": {"$gte": since}})
        faults = col(Machine.LOG).count_documents({"MACHINE_ID": m["MACHINE_ID"], "NEW_STATUS": FAULT, "LOG_DATE": {"$gte": since}})
        out.append({"id": m["MACHINE_ID"], "name": m.get("MACHINE_NAME"), "kind": "machine", "branch": m.get("BRANCH_ID"),
                    "has_key": bool(m.get("DEVICE_KEY_HASH")), "online": Machine.is_online(m),
                    "last_seen": to_local(seen).strftime("%d/%m %H:%M") if seen else None,
                    "recent": bool(seen and seen >= since),
                    "fresh": bool(m.get("TEL_TIME")) and (now_utc() - m["TEL_TIME"]).total_seconds() <= TELE_FRESH_SECONDS,
                    "failed_cmds": failed, "faults": faults})
    for mt in col(Meter.COL).find({}, NO_ID):
        if not mt.get("DEVICE_KEY_HASH"):
            continue
        seen = mt.get("LAST_SEEN")
        out.append({"id": mt["METER_ID"], "name": mt.get("METER_NAME"), "kind": "meter", "branch": mt.get("BRANCH_ID"),
                    "has_key": True, "online": bool(seen) and (now_utc() - seen).total_seconds() <= 2 * 3600,
                    "last_seen": to_local(seen).strftime("%d/%m %H:%M") if seen else None, "recent": bool(seen and seen >= since),
                    "fresh": None, "failed_cmds": 0, "faults": 0})
    return out


def collect():
    def safe(fn, default):
        try:
            return fn()
        except Exception:                                  # noqa: BLE001
            return default
    return {"api": safe(api_stats, {}), "line": safe(line_status, {}), "devices": safe(devices, [])}
