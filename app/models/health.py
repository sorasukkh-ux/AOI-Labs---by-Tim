"""Server health for Automation > AI (update 48): disk, memory, CPU, the database, the app's own warnings and errors,
and the gateways of the machines in use.

* collect()   reads the figures (Linux /proc and the standard library; no extra package).
* evaluate()  turns them into findings: ok / warn / crit, each with a text key and its numbers.
* check()     both, saved in AOITS_HEALTH (every 5 minutes by the scheduler; kept 7 days).
* live()      update 63: a check made now (kept 10 seconds, not saved) - what the AI screen and the menu show, so a
              stopped device or a problem shows within seconds, not at the next 5-minute check.
* latest()    the live check - the home menu shows a red "!" on AI and
              a banner when it is not ok, so the operator sees it without opening the screen.
* The app's log: every WARNING / ERROR of the app is also kept in AOITS_SYSLOG (DbLogHandler), the same message
  counted once per minute, kept 14 days.
With an AI key the health figures are part of the AI snapshot, so "Ask AI" can explain a warning and what to do.
"""
import logging
import os
import shutil
import threading
import time
from datetime import timedelta

from .base import col, NO_ID
from ..timeutil import now_utc

COL = "AOITS_HEALTH"
LOG_COL = "AOITS_SYSLOG"
KEEP_DAYS, LOG_KEEP_DAYS = 7, 14
FRESH_MINUTES = 10
OK, WARN, CRIT = "ok", "warn", "crit"
RANK = {OK: 0, WARN: 1, CRIT: 2}
# (warn, critical) thresholds
DISK_PCT = (85.0, 95.0)
MEM_PCT = (85.0, 95.0)
CPU_PCT = (85.0, 97.0)
LOAD_PER_CORE = (1.5, 3.0)
ERRORS_HOUR = (1, 20)
WARNINGS_HOUR = (20, 200)
DB_MS = (500.0, 3000.0)
API_5XX = (1, 10)                    # update 49: server errors (5xx) in the last hour
API_SLOW = (10, 50)                  # requests slower than 2 s in the last hour
DEVICE_REJECTED = (20, 200)          # device calls with a wrong key in the last hour
LINE_FAILED = (1, 5)                 # LINE pushes refused in the last 24 h
_STARTED = time.time()
LIVE_SECONDS = 10                    # update 63: the live check is made again after 10 seconds
_LIVE_LOCK = threading.Lock()


# ---------- figures ----------
def _meminfo():
    try:
        with open("/proc/meminfo") as fh:
            info = {line.split(":")[0]: int(line.split()[1]) for line in fh if ":" in line}
        total, avail = info.get("MemTotal", 0), info.get("MemAvailable", info.get("MemFree", 0))
        return {"total_mb": round(total / 1024.0), "used_pct": round(100.0 * (total - avail) / total, 1) if total else None}
    except (OSError, ValueError, IndexError):
        return {"total_mb": None, "used_pct": None}


def _cpu_times():
    with open("/proc/stat") as fh:
        parts = [float(x) for x in fh.readline().split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)
    return sum(parts), idle


def _cpu(sample_seconds):
    out = {"cores": os.cpu_count() or 1, "used_pct": None, "load1": None}
    try:
        out["load1"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    if sample_seconds:
        try:
            t1, i1 = _cpu_times()
            time.sleep(sample_seconds)
            t2, i2 = _cpu_times()
            if t2 > t1:
                out["used_pct"] = round(100.0 * (1 - (i2 - i1) / (t2 - t1)), 1)
        except (OSError, ValueError, IndexError):
            pass
    return out


def _process_mb():
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0)
    except (OSError, ValueError):
        pass
    return None


def _database():
    from flask import current_app
    if current_app.config.get("DB_BACKEND") == "memory":
        return {"ok": True, "ms": 0.0, "kind": "memory", "size_mb": None}
    start = time.monotonic()
    try:
        from ..db import get_db
        database = get_db()
        database.command("ping")
        ms = round((time.monotonic() - start) * 1000, 1)
        stats = database.command("dbstats")
        return {"ok": True, "ms": ms, "kind": "mongodb", "size_mb": round((stats.get("storageSize") or 0) / 1048576.0, 1)}
    except Exception as error:                            # noqa: BLE001 - any database problem is a finding
        return {"ok": False, "ms": None, "kind": "mongodb", "size_mb": None, "error": str(error)[:160]}


def _logs(minutes=60):
    since = now_utc() - timedelta(minutes=minutes)
    errors = warnings = 0
    for doc in col(LOG_COL).find({"LOG_DATE": {"$gte": since}}, {"_id": 0, "LEVEL": 1, "COUNT": 1}):
        if doc.get("LEVEL") in ("ERROR", "CRITICAL"):
            errors += doc.get("COUNT") or 1
        else:
            warnings += doc.get("COUNT") or 1
    return {"errors": errors, "warnings": warnings}


def _gateways():
    """Machines in use with remote control (update 64: and the mill selector) whose device reported in the last 24 hours
    but not in the last minute (a device that stopped; one never started, or a test key left over, is not a warning)."""
    try:
        from .automation import Machine
        day_ago = now_utc() - timedelta(hours=24)
        silent = [m["MACHINE_ID"] for m in col(Machine.COL).find({"CONTROL_FLG": True}, NO_ID)
                  if m.get("ACTIVE_FLG") is not False and m.get("DEVICE_KEY_HASH")
                  and m.get("LAST_SEEN") and m["LAST_SEEN"] >= day_ago and not Machine.is_online(m)]
        return {"offline": silent}
    except Exception:                                     # noqa: BLE001
        return {"offline": []}


def collect(sample_seconds=0.5):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        du = shutil.disk_usage(root)
        disk = {"total_gb": round(du.total / 1e9, 1), "free_gb": round(du.free / 1e9, 1), "used_pct": round(100.0 * du.used / du.total, 1)}
    except OSError:
        disk = {"total_gb": None, "free_gb": None, "used_pct": None}
    return {"disk": disk, "memory": _meminfo(), "cpu": _cpu(sample_seconds), "app_mb": _process_mb(), "db": _database(),
            "logs": _logs(), "gateways": _gateways(), "uptime_h": round((time.time() - _STARTED) / 3600.0, 1),
            **_interfaces()}


def _interfaces():
    """Update 49: the app's API, the LINE interface and the hardware devices (models/monitor.py)."""
    from . import monitor
    return monitor.collect()


# ---------- findings ----------
def _level(value, limits):
    if value is None:
        return OK
    return CRIT if value >= limits[1] else (WARN if value >= limits[0] else OK)


def evaluate(m):
    """[{"key": text key, "level": ok|warn|crit, "params": {...}}], the worst first."""
    out = []

    def add(key, level, **params):
        out.append({"key": key, "level": level, "params": params})
    add("hlDisk", _level(m["disk"]["used_pct"], DISK_PCT), pct=m["disk"]["used_pct"], free=m["disk"]["free_gb"])
    add("hlMemory", _level(m["memory"]["used_pct"], MEM_PCT), pct=m["memory"]["used_pct"], total=m["memory"]["total_mb"])
    cpu = m["cpu"]
    per_core = (cpu["load1"] / cpu["cores"]) if cpu.get("load1") is not None else None
    level = max(_level(cpu.get("used_pct"), CPU_PCT), _level(per_core, LOAD_PER_CORE), key=RANK.get)
    add("hlCpu", level, pct=cpu.get("used_pct"), load=cpu.get("load1"), cores=cpu["cores"])
    db = m["db"]
    if not db["ok"]:
        add("hlDbDown", CRIT, error=db.get("error", ""))
    else:
        add("hlDb", _level(db.get("ms"), DB_MS), ms=db.get("ms"), size=db.get("size_mb"))
    logs = m["logs"]
    add("hlErrors", _level(logs["errors"], ERRORS_HOUR), n=logs["errors"])
    add("hlWarnings", _level(logs["warnings"], WARNINGS_HOUR), n=logs["warnings"])
    off = m["gateways"]["offline"]
    add("hlGateways", WARN if off else OK, n=len(off), ids=", ".join(off))
    # update 49: API, LINE, hardware devices
    api = m.get("api") or {}
    e5 = sum((a or {}).get("err5xx", 0) for a in api.values())
    add("hlApi5xx", _level(e5, API_5XX), n=e5)
    slow = sum((a or {}).get("slow", 0) for a in api.values())
    add("hlApiSlow", _level(slow, API_SLOW), n=slow)
    rejected = (api.get("device") or {}).get("rejected", 0)
    add("hlDeviceRejected", _level(rejected, DEVICE_REJECTED), n=rejected)
    line = m.get("line") or {}
    if line.get("token"):
        if line.get("bot") == "invalid":
            add("hlLineToken", CRIT)
        elif line.get("bot") == "unreachable":
            add("hlLineUnreachable", WARN)
        add("hlLineFailed", _level(line.get("failed_24h", 0), LINE_FAILED), n=line.get("failed_24h", 0))
        if not line.get("secret"):
            add("hlLineSecret", WARN)
    devs = m.get("devices") or []
    cmds = sum(d.get("failed_cmds", 0) for d in devs)
    add("hlCommands", WARN if cmds else OK, n=cmds, ids=", ".join(d["id"] for d in devs if d.get("failed_cmds")))
    faults = sum(d.get("faults", 0) for d in devs)
    add("hlFaults", WARN if faults else OK, n=faults, ids=", ".join(d["id"] for d in devs if d.get("faults")))
    out.sort(key=lambda f: -RANK[f["level"]])
    return out


def overall(findings):
    return max((f["level"] for f in findings), key=RANK.get, default=OK)


def check(sample_seconds=0.5, save=True):
    metrics = collect(sample_seconds)
    findings = evaluate(metrics)
    doc = {"CHECK_DATE": now_utc(), "LEVEL": overall(findings), "METRICS": metrics, "FINDINGS": findings}
    if save:
        try:
            col(COL).insert_one(dict(doc))
            col(COL).delete_many({"CHECK_DATE": {"$lt": now_utc() - timedelta(days=KEEP_DAYS)}})
        except Exception:                                 # noqa: BLE001 - the database itself may be the problem
            pass
    _remember(doc)
    return doc


def _remember(doc):
    try:
        from flask import current_app
        with _LIVE_LOCK:
            current_app.extensions["aoi_health_live"] = (time.time(), doc)
    except RuntimeError:                                  # no app context
        pass


def live(max_age_seconds=LIVE_SECONDS):
    """Update 63: the health right now. A check made at most `max_age_seconds` ago in this server process (CPU sampled
    0.2 s), not saved - the scheduler saves one every 5 minutes for the 24-hour chart. One per app (tests)."""
    from flask import current_app
    with _LIVE_LOCK:
        hit = current_app.extensions.get("aoi_health_live")
        if hit and time.time() - hit[0] <= max_age_seconds:
            return hit[1]
    return check(sample_seconds=0 if current_app.config.get("TESTING") else 0.2, save=False)


def latest(max_age_minutes=FRESH_MINUTES):
    """Update 63: the live check (was: the newest saved one, up to 10 minutes old)."""
    return live()


def latest_saved(max_age_minutes=FRESH_MINUTES):
    """The newest saved check; a quick new one (no CPU sampling) when the newest is older than max_age_minutes."""
    try:
        doc = col(COL).find_one({}, NO_ID, sort=[("CHECK_DATE", -1)])
    except Exception:                                     # noqa: BLE001
        doc = None
    if doc and (now_utc() - doc["CHECK_DATE"]).total_seconds() <= max_age_minutes * 60:
        return doc
    return check(sample_seconds=0, save=True)


def history(hours=24):
    since = now_utc() - timedelta(hours=hours)
    return list(col(COL).find({"CHECK_DATE": {"$gte": since}}, {"_id": 0, "CHECK_DATE": 1, "LEVEL": 1, "METRICS.disk.used_pct": 1,
                                                                   "METRICS.memory.used_pct": 1, "METRICS.cpu": 1}).sort([("CHECK_DATE", 1)]))


def recent_logs(limit=30):
    return list(col(LOG_COL).find({}, NO_ID).sort([("LAST_DATE", -1)]).limit(limit))


def summary():
    """Short figures for the AI snapshot."""
    doc = latest()
    return {"checked": doc["CHECK_DATE"].isoformat(), "level": doc["LEVEL"],
            "findings": [{"what": f["key"], "level": f["level"], **f["params"]} for f in doc["FINDINGS"] if f["level"] != OK],
            "disk_pct": doc["METRICS"]["disk"]["used_pct"], "memory_pct": doc["METRICS"]["memory"]["used_pct"],
            "cpu": doc["METRICS"]["cpu"], "db": doc["METRICS"]["db"], "logs_last_hour": doc["METRICS"]["logs"],
            "recent_log": [{"level": d["LEVEL"], "message": d["MESSAGE"][:160], "count": d.get("COUNT", 1)} for d in recent_logs(8)]}


# ---------- the app's log -> AOITS_SYSLOG ----------
class DbLogHandler(logging.Handler):
    """WARNING and above of the app, saved for the health screen. The same message within a minute is counted, not
    repeated. Never raises (and never logs about itself)."""

    def __init__(self, app):
        super().__init__(level=logging.WARNING)
        self.app = app
        self.local = threading.local()

    def emit(self, record):
        if getattr(self.local, "busy", False):
            return
        self.local.busy = True
        try:
            message = record.getMessage().splitlines()[0][:300] if record.getMessage() else record.levelname
            now = now_utc()
            from flask import current_app, has_app_context
            app = current_app._get_current_object() if has_app_context() else self.app
            with app.app_context():
                c = col(LOG_COL)
                hit = c.find_one({"MESSAGE": message, "LEVEL": record.levelname, "LAST_DATE": {"$gte": now - timedelta(minutes=1)}}, NO_ID)
                if hit:
                    c.update_one({"LOG_ID": hit["LOG_ID"]}, {"$set": {"LAST_DATE": now}, "$inc": {"COUNT": 1}})
                else:
                    c.insert_one({"LOG_ID": "%d-%d" % (int(now.timestamp() * 1000), id(record) % 100000), "LEVEL": record.levelname,
                                  "LOGGER": record.name, "MESSAGE": message, "COUNT": 1, "LOG_DATE": now, "LAST_DATE": now})
                    if int(now.timestamp()) % 50 == 0:
                        c.delete_many({"LAST_DATE": {"$lt": now - timedelta(days=LOG_KEEP_DAYS)}})
        except Exception:                                 # noqa: BLE001 - logging must never break the app
            pass
        finally:
            self.local.busy = False


def install(app):
    """Adds the handler to the app's logger (once)."""
    for h in app.logger.handlers:                    # the logger is shared by every app object: point it at the newest
        if isinstance(h, DbLogHandler):
            h.app = app
            return
    app.logger.addHandler(DbLogHandler(app))
    if app.logger.level == logging.NOTSET or app.logger.level > logging.WARNING:
        app.logger.setLevel(logging.INFO)
