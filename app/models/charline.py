"""Update 131: husk furnace and charcoal briquette line (Tim's general arrangement AOI-GA-001 rev D, items 17-24).

    husk bin (19) -> screw feeder -> husk furnace + heat exchanger + blower (17) -> hot-air header with dampers (18)
                                                                                    -> silo 1 / silo 2
    burnt husk char -> cooling screw with T7 and water spray (20) -> char bin + mixer (21) -> briquette extruder (22)
                    -> briquette dryer (23) -> packing and store (24)

One controller (a Raspberry Pi) runs the whole line and talks to AOI with one device key:

    POST /api/v1/charlines/<LINE_ID>/state   header X-Device-Key
         {"units": {"BLOWER": {"run": true, "fault": ""}, "FEED": {...}, "SCREW": ..., "SPRAY": ..., "MIXER": ...,
                    "EXTRUDER": ..., "DRYER": ...},
          "dampers": {"D1": true, "D2": false}, "hot_c": 58.4, "char_c": 47.0, "husk_pct": 62}
    answer: {"ok": true, "out": {"BLOWER": true, "FEED": false, ...}, "dampers": {"D1": true, "D2": false},
             "why": {"FEED": "atTemp"}, "settings": {"hot_set": 60, "hot_band": 3, "hot_max": 90, "char_max": 60, "auto": true}}

AOI keeps what the staff want (WANT), works out what may run now (decide) and answers with it; the controller
switches its contactors to "out". The rules:
  * husk feed follows the hot-air temperature (on below set - band, off above set + band), needs the blower running
    and a damper open, and stops at the hot-air limit;
  * the char screw runs only while the char is at or below CHAR_MAX_C (T7); hotter: the water spray comes on first;
  * mixer -> extruder -> dryer are interlocked: each runs only while the next one is running;
  * a unit in fault is off; alarms (hot char, hot air over the limit, a fault, husk bin low) go to the event list
    and to LINE (ALARM_TO: the customer codes of the people to tell, linked to LINE).

SAFETY: AOI is not the safety system. The same rules must run in the controller itself (the answer carries the
settings for that), so the line stays safe when the internet or AOI is down; E-stop, motor overloads and the furnace
over-temperature cut-out are hardwired (drawing note 4).
"""
import secrets
from datetime import timedelta

from .base import NO_ID, ValidationError, audit_new, audit_update, clean_text, col, round_half_up, to_number
from .automation import ONLINE_SECONDS, _hash, new_device_key
from ..timeutil import now_utc

COL, TELE, LOG = "AOIMS_CHARLINE", "AOITS_CHARLINE_TELE", "AOILOG_CHARLINE"
UNITS = ("BLOWER", "FEED", "SCREW", "SPRAY", "MIXER", "EXTRUDER", "DRYER")
WANTED = ("BLOWER", "FEED", "SCREW", "MIXER", "EXTRUDER", "DRYER")       # SPRAY is automatic
DAMPERS = ("D1", "D2")
NEXT = {"MIXER": "EXTRUDER", "EXTRUDER": "DRYER"}                         # runs only while the next one runs
# setting -> (low, high, default)
SETTINGS = {"HOT_SET_C": (30, 120, 60), "HOT_BAND_C": (1, 15, 3), "HOT_MAX_C": (40, 150, 90), "CHAR_MAX_C": (30, 90, 60),
            "HUSK_LOW_PCT": (0, 50, 15)}
SPRAY_BAND_C = 5                    # the spray stays on until the char is this much below the limit
TELE_EVERY_SECONDS = 30
TELE_KEEP_DAYS = 7
FRESH_SECONDS = 20                  # live values older than this are shown as "no data"
LOG_KEEP = 500


def lines(branch_id):
    out = list(col(COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("LINE_ID", 1)]))
    for line in out:
        line["online"] = is_online(line)
    return out


def find(branch_id, line_id):
    return col(COL).find_one({"BRANCH_ID": branch_id, "LINE_ID": (line_id or "").strip().upper()}, NO_ID)


def is_online(line):
    seen = line.get("LAST_SEEN")
    return bool(seen) and (now_utc() - seen).total_seconds() <= ONLINE_SECONDS


def is_fresh(line):
    when = line.get("TEL_TIME")
    return bool(when) and (now_utc() - when).total_seconds() <= FRESH_SECONDS


def by_key(line_id, key):
    line = col(COL).find_one({"LINE_ID": (line_id or "").strip().upper()}, NO_ID)
    if line and line.get("ACTIVE_FLG") is not False and line.get("DEVICE_KEY_HASH") and key and \
            secrets.compare_digest(line["DEVICE_KEY_HASH"], _hash(key)):
        return line
    return None


def issue_key(branch_id, line_id, ope_user):
    """A new device key; the old one stops working. Returns the key (shown once)."""
    if not find(branch_id, line_id):
        raise ValidationError("clErrLine")
    key, hashed = new_device_key()
    col(COL).update_one({"BRANCH_ID": branch_id, "LINE_ID": line_id.upper()},
                        {"$set": dict({"DEVICE_KEY_HASH": hashed}, **audit_update(ope_user))})
    return key


def seed(db, branches):
    """One line per branch (F1-CHAR, F2-CHAR ...), without a key until one is issued."""
    for n, branch_id in enumerate(branches):
        digits = "".join(ch for ch in branch_id if ch.isdigit())
        lid = "F%s-CHAR" % (digits or n + 1)
        if not db[COL].find_one({"LINE_ID": lid}, NO_ID):
            doc = {"LINE_ID": lid, "BRANCH_ID": branch_id, "LINE_NAME": "เตาแกลบและสายถ่านอัดแท่ง", "ACTIVE_FLG": True,
                   "DEVICE_KEY_HASH": None, "LAST_SEEN": None, "AUTO_FLG": True, "ALARM_TO": None,
                   "WANT": {}, "STATE": {}, "DAMPER": {}, "OUT": {}, "WHY": {}, "ALARMS": {},
                   "HOT_C": None, "CHAR_C": None, "HUSK_PCT": None, "TEL_TIME": None}
            doc.update({k: v[2] for k, v in SETTINGS.items()})
            doc.update(audit_new("SYSTEM"))
            db[COL].insert_one(doc)


def setting(line, name):
    value = line.get(name)
    return SETTINGS[name][2] if value is None else value


def _log(line, kind, note, user):
    col(LOG).insert_one({"BRANCH_ID": line["BRANCH_ID"], "LINE_ID": line["LINE_ID"], "KIND": kind, "NOTE": clean_text(note, 200),
                         "OPE_USER": user, "LOG_DATE": now_utc()})
    extra = col(LOG).count_documents({"LINE_ID": line["LINE_ID"]}) - LOG_KEEP
    if extra > 0:
        for old in col(LOG).find({"LINE_ID": line["LINE_ID"]}, NO_ID).sort([("LOG_DATE", 1)]).limit(extra):
            col(LOG).delete_one({"LINE_ID": old["LINE_ID"], "LOG_DATE": old["LOG_DATE"], "NOTE": old["NOTE"]})


def events(branch_id, line_id, limit=30):
    return list(col(LOG).find({"BRANCH_ID": branch_id, "LINE_ID": line_id}, NO_ID).sort([("LOG_DATE", -1)]).limit(limit))


# ---------- the rules ----------
def _running(line, unit):
    return bool((line.get("STATE") or {}).get(unit, {}).get("run"))


def _fault(line, unit):
    return (line.get("STATE") or {}).get(unit, {}).get("fault") or None


def decide(line):
    """({unit: may run now}, {unit: reason key when it is wanted but held, or why the spray is on})."""
    want, last = line.get("WANT") or {}, line.get("OUT") or {}
    hot, char = line.get("HOT_C"), line.get("CHAR_C")
    out, why = {}, {}

    def hold(unit, reason):
        out[unit] = False
        if want.get(unit) or unit == "SPRAY":
            why[unit] = reason

    for unit in WANTED:
        out[unit] = bool(want.get(unit))
        if _fault(line, unit):
            hold(unit, "fault")
    if out["FEED"]:
        if not _running(line, "BLOWER"):
            hold("FEED", "needBlower")
        elif not any((line.get("DAMPER") or {}).get(d) for d in DAMPERS):
            hold("FEED", "needDamper")
        elif hot is None:
            hold("FEED", "noTemp")
        elif hot >= setting(line, "HOT_MAX_C"):
            hold("FEED", "hotMax")
        elif line.get("AUTO_FLG") is not False:
            low = setting(line, "HOT_SET_C") - setting(line, "HOT_BAND_C")
            high = setting(line, "HOT_SET_C") + setting(line, "HOT_BAND_C")
            if hot >= high or (hot > low and not last.get("FEED")):      # between low and high: as it was
                hold("FEED", "atTemp")
    limit = setting(line, "CHAR_MAX_C")
    out["SPRAY"] = char is not None and (char > limit or (bool(last.get("SPRAY")) and char > limit - SPRAY_BAND_C))
    if _fault(line, "SPRAY"):
        hold("SPRAY", "fault")
    elif out["SPRAY"]:
        why["SPRAY"] = "charHot"
    if out["SCREW"]:
        if char is None:
            hold("SCREW", "noTemp")
        elif char > limit:
            hold("SCREW", "charHot")
    for unit in ("EXTRUDER", "MIXER"):                       # downstream first: the dryer, then the extruder, then the mixer
        if out[unit] and not (_running(line, NEXT[unit]) and out[NEXT[unit]]):
            hold(unit, "needNext")
    return out, why


def _alarms(line):
    """{alarm key: text params} of what is wrong now."""
    found = {}
    hot, char, husk = line.get("HOT_C"), line.get("CHAR_C"), line.get("HUSK_PCT")
    if char is not None and char > setting(line, "CHAR_MAX_C"):
        found["charHot"] = {"c": "%g" % char, "max": "%g" % setting(line, "CHAR_MAX_C")}
    if hot is not None and hot >= setting(line, "HOT_MAX_C"):
        found["hotMax"] = {"c": "%g" % hot, "max": "%g" % setting(line, "HOT_MAX_C")}
    if husk is not None and husk <= setting(line, "HUSK_LOW_PCT") and (line.get("WANT") or {}).get("FEED"):
        found["huskLow"] = {"pct": "%g" % husk}
    for unit in UNITS:
        if _fault(line, unit):
            found["fault" + unit] = {"fault": _fault(line, unit)}
    return found


def alarm_text(key, params, lang=None):
    from ..i18n import t, t_in
    tr = (lambda k, **kw: t_in(lang, k, **kw)) if lang else t
    if key.startswith("fault"):
        return tr("clAlarmFault", unit=tr("clUnit_" + key[5:]), fault=params.get("fault", ""))
    return tr("clAlarm_" + key, **params)


def _tell(line, text):
    """The alarm to the LINE of the people in ALARM_TO. Returns how many got it."""
    from . import notify
    from .customer import Customer
    sent = 0
    if not notify.configured():
        return 0
    for code in [c for c in (line.get("ALARM_TO") or "").split(",") if c]:
        user_id = ((Customer.find(code) or {}).get("LINE_USER_ID") or "").strip()
        if notify.USER_ID.match(user_id) and notify._push(user_id, text) is None:
            sent += 1
    return sent


def report(line, body):
    """The controller's state. Stores it, works out what may run and raises / clears alarms. Returns the answer."""
    if not isinstance(body, dict):
        raise ValidationError("clErrBody")

    def num(name, low, high):
        value = body.get(name)
        if value is None or value == "":
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ValidationError("clErrNumber", field=name)
        if value != value or not (low <= value <= high):
            raise ValidationError("clErrNumber", field=name)
        return round_half_up(value, 1)
    units = body.get("units") if isinstance(body.get("units"), dict) else {}
    state = {u: {"run": bool((units.get(u) or {}).get("run")) and not (units.get(u) or {}).get("fault"),
                 "fault": clean_text((units.get(u) or {}).get("fault"), 20) or None}
             for u in UNITS if isinstance(units.get(u), dict)}
    dampers = body.get("dampers") if isinstance(body.get("dampers"), dict) else {}
    now = now_utc()
    values = {"STATE": state, "DAMPER": {d: bool(dampers.get(d)) for d in DAMPERS}, "HOT_C": num("hot_c", -20, 400),
              "CHAR_C": num("char_c", -20, 900), "HUSK_PCT": num("husk_pct", 0, 100), "TEL_TIME": now, "LAST_SEEN": now}
    before = line.get("STATE") or {}
    line = dict(line, **values)
    out, why = decide(line)
    values.update(OUT=out, WHY=why)
    # alarms: new ones to the log and to LINE, gone ones to the log
    now_wrong, known = _alarms(line), dict(line.get("ALARMS") or {})
    for key, params in now_wrong.items():
        if key not in known:
            text = alarm_text(key, params, "th")
            told = _tell(line, "⚠ %s (%s)\n%s" % (line.get("LINE_NAME") or line["LINE_ID"], line["LINE_ID"], text))
            _log(line, "ALARM", text + (" · LINE %d" % told if told else ""), "device:" + line["LINE_ID"])
            known[key] = text
    for key in [k for k in known if k not in now_wrong]:
        _log(line, "CLEAR", known[key], "device:" + line["LINE_ID"])
        del known[key]
    values["ALARMS"] = known
    for unit, st in state.items():                            # starts and stops as the controller reports them
        if st["run"] != bool(before.get(unit, {}).get("run")):
            _log(line, "RUN" if st["run"] else "STOP", unit, "device:" + line["LINE_ID"])
    col(COL).update_one({"LINE_ID": line["LINE_ID"]}, {"$set": values})
    last = col(TELE).find_one({"LINE_ID": line["LINE_ID"]}, NO_ID, sort=[("TEL_DATE", -1)])
    if not last or (now - last["TEL_DATE"]).total_seconds() >= TELE_EVERY_SECONDS:
        col(TELE).insert_one({"LINE_ID": line["LINE_ID"], "BRANCH_ID": line["BRANCH_ID"], "TEL_DATE": now, "HOT_C": values["HOT_C"],
                              "CHAR_C": values["CHAR_C"], "HUSK_PCT": values["HUSK_PCT"], "FEED": _running(line, "FEED")})
        col(TELE).delete_many({"LINE_ID": line["LINE_ID"], "TEL_DATE": {"$lt": now - timedelta(days=TELE_KEEP_DAYS)}})
    want = line.get("WANT") or {}
    return {"ok": True, "out": out, "why": why, "dampers": {d: bool(want.get(d)) for d in DAMPERS},
            "settings": {"hot_set": setting(line, "HOT_SET_C"), "hot_band": setting(line, "HOT_BAND_C"),
                         "hot_max": setting(line, "HOT_MAX_C"), "char_max": setting(line, "CHAR_MAX_C"),
                         "auto": line.get("AUTO_FLG") is not False}}


# ---------- the staff ----------
def want(branch_id, line_id, unit, on, ope_user):
    """Start / stop one unit, or open / close a damper. A start is refused when its condition is not met (so the staff
    see why at once); a stop always goes through."""
    line = find(branch_id, line_id)
    if not line:
        raise ValidationError("clErrLine")
    unit = (unit or "").strip().upper()
    if unit not in WANTED + DAMPERS:
        raise ValidationError("clErrUnit")
    if on:
        if line.get("ACTIVE_FLG") is False or not line.get("DEVICE_KEY_HASH") or not is_online(line):
            raise ValidationError("clErrOffline")
        if unit in WANTED and _fault(line, unit):
            raise ValidationError("clErrFault")
        if unit == "FEED" and not _running(line, "BLOWER"):
            raise ValidationError("clWhy_needBlower")
        if unit == "FEED" and not any((line.get("DAMPER") or {}).get(d) for d in DAMPERS):
            raise ValidationError("clWhy_needDamper")
        if unit in NEXT and not _running(line, NEXT[unit]):
            raise ValidationError("clErrNext", unit=NEXT[unit])
    wanted = dict(line.get("WANT") or {}, **{unit: bool(on)})
    if not on and unit == "BLOWER":                           # no blower, no fire
        wanted["FEED"] = False
    col(COL).update_one({"LINE_ID": line["LINE_ID"]}, {"$set": dict({"WANT": wanted}, **audit_update(ope_user))})
    _log(line, "WANT", "%s %s" % (unit, "ON" if on else "OFF"), ope_user)
    return find(branch_id, line_id)


def stop_all(branch_id, line_id, ope_user):
    """Everything off (the dampers stay as they are, so the furnace can cool through the silos)."""
    line = find(branch_id, line_id)
    if not line:
        raise ValidationError("clErrLine")
    wanted = dict(line.get("WANT") or {}, **{u: False for u in WANTED})
    col(COL).update_one({"LINE_ID": line["LINE_ID"]}, {"$set": dict({"WANT": wanted}, **audit_update(ope_user))})
    _log(line, "WANT", "STOP ALL", ope_user)
    return find(branch_id, line_id)


def save_settings(branch_id, line_id, form, ope_user):
    line = find(branch_id, line_id)
    if not line:
        raise ValidationError("clErrLine")
    values = {}
    for name, (low, high, _default) in SETTINGS.items():
        try:
            v = to_number(form.get(name), None)
        except ValueError:
            raise ValidationError("errNumber")
        if v is None or not (low <= v <= high):
            raise ValidationError("clErrRange", field=name, low=low, high=high)
        values[name] = round_half_up(v, 1)
    if values["HOT_SET_C"] + values["HOT_BAND_C"] >= values["HOT_MAX_C"]:
        raise ValidationError("clErrHotMax")
    codes = []
    from .customer import Customer
    for code in (form.get("ALARM_TO") or "").replace(" ", ",").upper().split(","):
        if code and code not in codes:
            if not Customer.find(code):
                raise ValidationError("clErrAlarmTo", code=code[:20])
            codes.append(code)
    if len(codes) > 5:
        raise ValidationError("clErrAlarmMany")
    values.update(ALARM_TO=",".join(codes) or None, AUTO_FLG=form.get("AUTO_FLG") == "1",
                  LINE_NAME=clean_text(form.get("LINE_NAME"), 60) or line.get("LINE_NAME"),
                  ACTIVE_FLG=form.get("ACTIVE_FLG") == "1")
    if not values["ACTIVE_FLG"]:
        values["WANT"] = {}
    values.update(audit_update(ope_user))
    col(COL).update_one({"LINE_ID": line["LINE_ID"]}, {"$set": values})
    _log(line, "SET", "set %(HOT_SET_C)g±%(HOT_BAND_C)g max %(HOT_MAX_C)g char %(CHAR_MAX_C)g" % values, ope_user)
    return find(branch_id, line_id)


# ---------- the screen ----------
def view(line):
    """What the screen shows: per unit want / out / run / fault / reason, sensors (None when stale), alarms."""
    fresh = is_fresh(line)
    want_, out, why = line.get("WANT") or {}, line.get("OUT") or {}, line.get("WHY") or {}
    units = []
    for unit in UNITS:
        units.append({"id": unit, "want": bool(want_.get(unit)) if unit != "SPRAY" else None,
                      "out": bool(out.get(unit)), "run": fresh and _running(line, unit), "fault": _fault(line, unit) if fresh else None,
                      "why": why.get(unit), "auto": unit == "SPRAY"})
    return {"units": units, "by": {u["id"]: u for u in units}, "fresh": fresh, "online": is_online(line),
            "dampers": [{"id": d, "want": bool(want_.get(d)), "open": fresh and bool((line.get("DAMPER") or {}).get(d))} for d in DAMPERS],
            "hot": line.get("HOT_C") if fresh else None, "char": line.get("CHAR_C") if fresh else None,
            "husk": line.get("HUSK_PCT") if fresh else None,
            "alarms": [(k, alarm_text(k, params)) for k, params in _alarms(line).items()] if fresh else []}


def chart(line, hours=2, w=640, h=200):
    """Hot-air and char temperature of the last hours as SVG polylines (None when there is nothing yet)."""
    since = now_utc() - timedelta(hours=hours)
    rows = list(col(TELE).find({"LINE_ID": line["LINE_ID"], "TEL_DATE": {"$gte": since}}, NO_ID).sort([("TEL_DATE", 1)]))
    if len(rows) < 2:
        return None
    pl, pr, pt, pb = 34, 8, 10, 22
    top = max([setting(line, "HOT_MAX_C")] + [r[f] for r in rows for f in ("HOT_C", "CHAR_C") if r.get(f) is not None]) * 1.08
    span = hours * 3600.0

    def x(when):
        return round(pl + (w - pl - pr) * (1 - (now_utc() - when).total_seconds() / span), 1)

    def y(v):
        return round(pt + (h - pt - pb) * (1 - v / top), 1)

    def line_of(field):
        return " ".join("%s,%s" % (x(r["TEL_DATE"]), y(r[field])) for r in rows if r.get(field) is not None)
    step = 20 if top <= 130 else 50
    return {"w": w, "h": h, "hot": line_of("HOT_C"), "char": line_of("CHAR_C"), "x0": pl, "x1": w - pr, "y0": h - pb,
            "set_y": y(setting(line, "HOT_SET_C")), "char_y": y(setting(line, "CHAR_MAX_C")),
            "grid": [(v, y(v)) for v in range(0, int(top) + 1, step)], "hours": hours}
