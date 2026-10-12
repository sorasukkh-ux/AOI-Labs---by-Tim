"""Automation: machines and their control (AOIMS_MACHINE, AOITS_MACHINE_CMD, AOILOG_MACHINE) and electricity
meters (AOIMS_METER, AOITS_METER_READ).

Machines and meters can be used without hardware: operators set a machine's status and type meter readings by
hand. Once a device (PLC, smart meter, ESP32 ...) is connected, it sends the same data through the device API
(controllers/device_api.py) with its own device key, and picks up start/stop commands.

Machine usage statistics are worked out from the status history in AOILOG_MACHINE.
"""
import hashlib
import re
import secrets
from datetime import datetime, time, timedelta, timezone

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up, to_number
from .counter import Counter
from ..timeutil import local_tz, now_local, now_utc, to_local

MACHINE_TYPES = ("HUSKER", "WHITENER", "POLISHER", "GRADER", "DRYER", "CONVEYOR", "OTHER")
RUNNING, IDLE, FAULT, MAINTENANCE, OFFLINE = "R", "I", "F", "M", "O"
MACHINE_STATUSES = (RUNNING, IDLE, FAULT, MAINTENANCE, OFFLINE)
SOURCE_USER, SOURCE_DEVICE = "USER", "DEVICE"
CMD_START, CMD_STOP, CMD_RESET = "START", "STOP", "RESET"
COMMANDS = (CMD_START, CMD_STOP, CMD_RESET)
CMD_PENDING, CMD_SENT, CMD_DONE, CMD_FAILED, CMD_EXPIRED = "P", "S", "D", "F", "X"
CMD_TIMEOUT_SECONDS = 120          # a command nobody picked up in time is dropped (never runs late)
ONLINE_SECONDS = 300               # a device that reported within 5 minutes is shown as connected
ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,19}$")


def _clean_id(text, key):
    value = (text or "").strip().upper()
    if not ID_PATTERN.match(value):
        raise ValidationError(key)
    return value


def _hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_device_key():
    """A new device key: (the key shown once to the user, the hash that is stored)."""
    key = secrets.token_urlsafe(24)
    return key, _hash(key)


def day_start_utc(day):
    return datetime.combine(day, time.min, tzinfo=local_tz()).astimezone(timezone.utc)


def period_bounds(days):
    """(start, end) in UTC of the last `days` local days including today."""
    today = now_local().date()
    return day_start_utc(today - timedelta(days=days - 1)), now_utc()


class Machine:
    COL = "AOIMS_MACHINE"
    LOG = "AOILOG_MACHINE"

    @classmethod
    def for_branch(cls, branch_id):
        return list(col(cls.COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("MACHINE_SEQ", 1), ("MACHINE_ID", 1)]))

    @classmethod
    def find(cls, branch_id, machine_id):
        return col(cls.COL).find_one({"BRANCH_ID": branch_id, "MACHINE_ID": (machine_id or "").strip().upper()}, NO_ID)

    @classmethod
    def by_key(cls, machine_id, key):
        """The machine a device key belongs to (device API); None when the key is wrong."""
        machine = col(cls.COL).find_one({"MACHINE_ID": (machine_id or "").strip().upper()}, NO_ID)
        if machine and machine.get("DEVICE_KEY_HASH") and key and secrets.compare_digest(machine["DEVICE_KEY_HASH"], _hash(key)):
            return machine
        return None

    @staticmethod
    def is_online(machine):
        seen = machine.get("LAST_SEEN")
        return bool(seen) and (now_utc() - seen).total_seconds() <= ONLINE_SECONDS

    @classmethod
    def save(cls, branch_id, form, ope_user, creating):
        """Add or edit a machine (Hardware Control > Machines)."""
        machine_id = _clean_id(form.get("MACHINE_ID"), "errMachineId")
        name = clean_text(form.get("MACHINE_NAME"), 100)
        if not name:
            raise ValidationError("errMachineName")
        mtype = form.get("MACHINE_TYPE") if form.get("MACHINE_TYPE") in MACHINE_TYPES else "OTHER"
        try:
            kw = to_number(form.get("RATED_KW"), None)
            capacity = to_number(form.get("CAPACITY_KGH"), None)
            seq = int(to_number(form.get("MACHINE_SEQ"), 0) or 0)
        except ValueError:
            raise ValidationError("errNumber")
        if (kw is not None and kw < 0) or (capacity is not None and capacity < 0):
            raise ValidationError("errNegative")
        values = {"MACHINE_NAME": name, "MACHINE_TYPE": mtype, "RATED_KW": kw, "CAPACITY_KGH": capacity, "MACHINE_SEQ": seq,
                  "CONTROL_FLG": form.get("CONTROL_FLG") == "1", "METER_ID": (form.get("METER_ID") or "").strip().upper() or None}
        existing = cls.find(branch_id, machine_id)
        if creating:
            if col(cls.COL).find_one({"MACHINE_ID": machine_id}, NO_ID):
                raise ValidationError("errMachineExists", id=machine_id)
            doc = dict(values, BRANCH_ID=branch_id, MACHINE_ID=machine_id, MACHINE_STATUS=OFFLINE, STATUS_TIME=now_utc(),
                       STATUS_SOURCE=SOURCE_USER, DEVICE_KEY_HASH=None, LAST_SEEN=None)
            doc.update(audit_new(ope_user))
            col(cls.COL).insert_one(doc)
        else:
            if not existing:
                raise ValidationError("errMachineUnknown")
            values.update(audit_update(ope_user))
            col(cls.COL).update_one({"BRANCH_ID": branch_id, "MACHINE_ID": machine_id}, {"$set": values})
        return cls.find(branch_id, machine_id)

    @classmethod
    def issue_key(cls, branch_id, machine_id, ope_user):
        """A new device key for the machine; the old one stops working. Returns the key (shown once)."""
        if not cls.find(branch_id, machine_id):
            raise ValidationError("errMachineUnknown")
        key, hashed = new_device_key()
        values = {"DEVICE_KEY_HASH": hashed}
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"BRANCH_ID": branch_id, "MACHINE_ID": machine_id.upper()}, {"$set": values})
        return key

    @classmethod
    def set_status(cls, machine, status, source, ope_user, note=None):
        """Records a status change (from the operator or the device) in AOILOG_MACHINE."""
        if status not in MACHINE_STATUSES:
            raise ValidationError("errMachineStatus")
        now = now_utc()
        values = {"MACHINE_STATUS": status, "STATUS_TIME": now, "STATUS_SOURCE": source, "OPE_USER": ope_user, "LAST_UPDATE": now}
        if source == SOURCE_DEVICE:
            values["LAST_SEEN"] = now
        changed = status != machine.get("MACHINE_STATUS")
        col(cls.COL).update_one({"MACHINE_ID": machine["MACHINE_ID"]}, {"$set": values})
        if changed or note:
            col(cls.LOG).insert_one({"BRANCH_ID": machine["BRANCH_ID"], "MACHINE_ID": machine["MACHINE_ID"],
                                     "OLD_STATUS": machine.get("MACHINE_STATUS"), "NEW_STATUS": status, "SOURCE": source,
                                     "NOTE": clean_text(note, 200) or None, "OPE_USER": ope_user, "LOG_DATE": now})
        return dict(machine, **values)

    @classmethod
    def seen(cls, machine):
        col(cls.COL).update_one({"MACHINE_ID": machine["MACHINE_ID"]}, {"$set": {"LAST_SEEN": now_utc()}})

    @classmethod
    def events(cls, branch_id, since, machine_id=None, limit=None):
        flt = {"BRANCH_ID": branch_id, "LOG_DATE": {"$gte": since}}
        if machine_id:
            flt["MACHINE_ID"] = machine_id
        cursor = col(cls.LOG).find(flt, NO_ID).sort([("LOG_DATE", -1 if limit else 1)])
        return list(cursor.limit(limit)) if limit else list(cursor)

    @classmethod
    def status_before(cls, machine_id, moment):
        last = col(cls.LOG).find_one({"MACHINE_ID": machine_id, "LOG_DATE": {"$lt": moment}}, NO_ID, sort=[("LOG_DATE", -1)])
        return last["NEW_STATUS"] if last else OFFLINE

    # ---------- usage statistics ----------
    @classmethod
    def usage(cls, machine, start, end):
        """Hours in each status between start and end, and the number of faults."""
        hours = {s: 0.0 for s in MACHINE_STATUSES}
        status, cursor, faults = cls.status_before(machine["MACHINE_ID"], start), start, 0
        for event in cls.events(machine["BRANCH_ID"], start, machine["MACHINE_ID"]):
            if event["LOG_DATE"] > end:
                break
            hours[status] += (event["LOG_DATE"] - cursor).total_seconds() / 3600.0
            cursor, status = event["LOG_DATE"], event["NEW_STATUS"]
            if status == FAULT and event.get("OLD_STATUS") != FAULT:
                faults += 1
        hours[status] += max(0.0, (end - cursor).total_seconds() / 3600.0)
        total = sum(hours.values()) or 1.0
        return {"hours": {k: round_half_up(v, 2) for k, v in hours.items()}, "faults": faults,
                "utilization": round_half_up(hours[RUNNING] / total * 100, 1),
                "energy_est": round_half_up(hours[RUNNING] * (machine.get("RATED_KW") or 0), 1)}

    @classmethod
    def daily_run_hours(cls, machine, days):
        """Running hours per local day for the last `days` days (oldest first)."""
        today = now_local().date()
        out = []
        for i in range(days - 1, -1, -1):
            day = today - timedelta(days=i)
            start, end = day_start_utc(day), min(day_start_utc(day + timedelta(days=1)), now_utc())
            out.append((day, cls.usage(machine, start, end)["hours"][RUNNING]))
        return out


class MachineCommand:
    COL = "AOITS_MACHINE_CMD"

    @classmethod
    def _expire(cls, branch_id=None):
        limit = now_utc() - timedelta(seconds=CMD_TIMEOUT_SECONDS)
        flt = {"CMD_STATUS": CMD_PENDING, "REQ_DATE": {"$lt": limit}}
        if branch_id:
            flt["BRANCH_ID"] = branch_id
        col(cls.COL).update_many(flt, {"$set": {"CMD_STATUS": CMD_EXPIRED, "ACK_DATE": now_utc(), "RESULT": "Not picked up in time"}})

    @classmethod
    def request(cls, machine, command, ope_user):
        """Start/stop/reset from Hardware Control. Only for machines with remote control and a device connected."""
        if command not in COMMANDS:
            raise ValidationError("errMachineCommand")
        if not machine.get("CONTROL_FLG") or not machine.get("DEVICE_KEY_HASH"):
            raise ValidationError("errMachineNoControl")
        if not Machine.is_online(machine):
            raise ValidationError("errMachineOffline")
        cls._expire(machine["BRANCH_ID"])
        if col(cls.COL).find_one({"MACHINE_ID": machine["MACHINE_ID"], "CMD_STATUS": {"$in": [CMD_PENDING, CMD_SENT]}}, NO_ID):
            raise ValidationError("errMachineBusy")
        today = now_local()
        day = "%04d%02d%02d" % (today.year, today.month, today.day)
        cmd = {"CMD_ID": "MC-%s-%04d" % (day, Counter.next("MC-" + day)), "BRANCH_ID": machine["BRANCH_ID"],
               "MACHINE_ID": machine["MACHINE_ID"], "CMD": command, "CMD_STATUS": CMD_PENDING, "REQ_USER": ope_user,
               "REQ_DATE": now_utc(), "SENT_DATE": None, "ACK_DATE": None, "RESULT": None}
        col(cls.COL).insert_one(dict(cmd))
        return cmd

    @classmethod
    def for_branch(cls, branch_id, limit=30):
        cls._expire(branch_id)
        return list(col(cls.COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("REQ_DATE", -1)]).limit(limit))

    @classmethod
    def get(cls, cmd_id):
        return col(cls.COL).find_one({"CMD_ID": (cmd_id or "").strip().upper()}, NO_ID)

    @classmethod
    def take_pending(cls, machine):
        """The device asks for work: pending commands are handed over (status Sent)."""
        cls._expire(machine["BRANCH_ID"])
        pending = list(col(cls.COL).find({"MACHINE_ID": machine["MACHINE_ID"], "CMD_STATUS": CMD_PENDING}, NO_ID).sort([("REQ_DATE", 1)]))
        for cmd in pending:
            col(cls.COL).update_one({"CMD_ID": cmd["CMD_ID"]}, {"$set": {"CMD_STATUS": CMD_SENT, "SENT_DATE": now_utc()}})
        return pending

    @classmethod
    def acknowledge(cls, machine, cmd_id, ok, result):
        cmd = col(cls.COL).find_one({"CMD_ID": cmd_id, "MACHINE_ID": machine["MACHINE_ID"]}, NO_ID)
        if not cmd or cmd["CMD_STATUS"] not in (CMD_PENDING, CMD_SENT):
            raise ValidationError("errMachineCommand")
        col(cls.COL).update_one({"CMD_ID": cmd_id}, {"$set": {"CMD_STATUS": CMD_DONE if ok else CMD_FAILED, "ACK_DATE": now_utc(),
                                                             "RESULT": clean_text(result, 200) or None}})
        if ok and cmd["CMD"] in (CMD_START, CMD_STOP):
            Machine.set_status(machine, RUNNING if cmd["CMD"] == CMD_START else IDLE, SOURCE_DEVICE, cmd["REQ_USER"],
                               note="%s %s" % (cmd["CMD"], cmd_id))
        return col(cls.COL).find_one({"CMD_ID": cmd_id}, NO_ID)


class Meter:
    COL = "AOIMS_METER"
    READ = "AOITS_METER_READ"

    @classmethod
    def for_branch(cls, branch_id):
        return list(col(cls.COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("METER_ID", 1)]))

    @classmethod
    def find(cls, branch_id, meter_id):
        return col(cls.COL).find_one({"BRANCH_ID": branch_id, "METER_ID": (meter_id or "").strip().upper()}, NO_ID)

    @classmethod
    def by_key(cls, meter_id, key):
        meter = col(cls.COL).find_one({"METER_ID": (meter_id or "").strip().upper()}, NO_ID)
        if meter and meter.get("DEVICE_KEY_HASH") and key and secrets.compare_digest(meter["DEVICE_KEY_HASH"], _hash(key)):
            return meter
        return None

    @classmethod
    def save(cls, branch_id, form, ope_user, creating):
        meter_id = _clean_id(form.get("METER_ID"), "errMeterId")
        name = clean_text(form.get("METER_NAME"), 100)
        if not name:
            raise ValidationError("errMeterName")
        values = {"METER_NAME": name, "MACHINE_ID": (form.get("MACHINE_ID") or "").strip().upper() or None}
        if creating:
            if col(cls.COL).find_one({"METER_ID": meter_id}, NO_ID):
                raise ValidationError("errMeterExists", id=meter_id)
            doc = dict(values, BRANCH_ID=branch_id, METER_ID=meter_id, DEVICE_KEY_HASH=None, LAST_SEEN=None)
            doc.update(audit_new(ope_user))
            col(cls.COL).insert_one(doc)
        else:
            if not cls.find(branch_id, meter_id):
                raise ValidationError("errMeterUnknown")
            values.update(audit_update(ope_user))
            col(cls.COL).update_one({"BRANCH_ID": branch_id, "METER_ID": meter_id}, {"$set": values})
        return cls.find(branch_id, meter_id)

    @classmethod
    def issue_key(cls, branch_id, meter_id, ope_user):
        if not cls.find(branch_id, meter_id):
            raise ValidationError("errMeterUnknown")
        key, hashed = new_device_key()
        values = {"DEVICE_KEY_HASH": hashed}
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"BRANCH_ID": branch_id, "METER_ID": meter_id.upper()}, {"$set": values})
        return key

    @classmethod
    def last_reading(cls, meter_id, before=None):
        flt = {"METER_ID": meter_id}
        if before is not None:
            flt["READ_DATE"] = {"$lt": before}
        return col(cls.READ).find_one(flt, NO_ID, sort=[("READ_DATE", -1)])

    @classmethod
    def add_reading(cls, meter, kwh, read_date, source, ope_user):
        """A cumulative meter reading (kWh). USED_KWH is the use since the previous reading."""
        try:
            kwh = to_number(kwh, None)
        except ValueError:
            raise ValidationError("errNumber")
        if kwh is None or kwh < 0:
            raise ValidationError("errMeterReading")
        if read_date is None:                                   # "now": always after the meter's last reading
            read_date = now_utc()
            last = cls.last_reading(meter["METER_ID"])
            if last and last["READ_DATE"] >= read_date:         # two readings in the same millisecond
                read_date = last["READ_DATE"] + timedelta(milliseconds=1)
        if read_date > now_utc() + timedelta(minutes=5):
            raise ValidationError("errMeterFuture")
        previous = cls.last_reading(meter["METER_ID"], before=read_date)
        later = col(cls.READ).find_one({"METER_ID": meter["METER_ID"], "READ_DATE": {"$gte": read_date}}, NO_ID)
        if later:
            raise ValidationError("errMeterOrder")              # readings are added in time order
        if previous and kwh < previous["READ_KWH"]:
            raise ValidationError("errMeterLower", last="%.1f" % previous["READ_KWH"])
        used = round_half_up(kwh - previous["READ_KWH"], 3) if previous else 0.0
        today = now_local()
        day = "%04d%02d%02d" % (today.year, today.month, today.day)
        reading = {"READ_ID": "MR-%s-%04d" % (day, Counter.next("MR-" + day)), "BRANCH_ID": meter["BRANCH_ID"],
                   "METER_ID": meter["METER_ID"], "READ_DATE": read_date, "READ_KWH": round_half_up(kwh, 3), "USED_KWH": used,
                   "SOURCE": source, "OPE_USER": ope_user, "REGIST_DATE": now_utc()}
        col(cls.READ).insert_one(dict(reading))
        if source == SOURCE_DEVICE:
            col(cls.COL).update_one({"METER_ID": meter["METER_ID"]}, {"$set": {"LAST_SEEN": now_utc()}})
        return reading

    @classmethod
    def readings(cls, branch_id, since, meter_id=None, limit=None):
        flt = {"BRANCH_ID": branch_id, "READ_DATE": {"$gte": since}}
        if meter_id:
            flt["METER_ID"] = meter_id
        cursor = col(cls.READ).find(flt, NO_ID).sort([("READ_DATE", -1 if limit else 1)])
        return list(cursor.limit(limit)) if limit else list(cursor)

    @classmethod
    def used_between(cls, branch_id, start, end, meter_id=None):
        return round_half_up(sum(r["USED_KWH"] for r in cls.readings(branch_id, start, meter_id) if r["READ_DATE"] <= end), 3)

    @classmethod
    def daily_use(cls, branch_id, days, meter_id=None):
        """kWh per local day for the last `days` days (oldest first), from the readings' USED_KWH."""
        today = now_local().date()
        start = day_start_utc(today - timedelta(days=days - 1))
        per_day = {}
        for r in cls.readings(branch_id, start, meter_id):
            d = to_local(r["READ_DATE"]).date()
            per_day[d] = per_day.get(d, 0.0) + r["USED_KWH"]
        return [(today - timedelta(days=i), round_half_up(per_day.get(today - timedelta(days=i), 0.0), 2)) for i in range(days - 1, -1, -1)]


def paddy_milled(branch_id, start, end):
    """Paddy (kg) of the milling orders registered in the period that were not cancelled (for kWh per kg)."""
    total = 0.0
    for o in col("AOITS_ORDMILLING").find({"BRANCH_ID": branch_id, "REGIST_DATE": {"$gte": start}}, NO_ID):
        if o["REGIST_DATE"] <= end and o["MILL_STATUS"] != "CC":
            total += o.get("REGIST_PADDY") or 0
    return round_half_up(total, 2)


# ---------- electricity rate and cost ----------
def elec_rate(branch_id):
    """THB per kWh of the branch (AOIMS_SYSPARAM.SYSP_ELEC_RATE, 0 when not set)."""
    branch = col("AOIMS_SYSPARAM").find_one({"BRANCH_ID": branch_id}, NO_ID) or {}
    return float(branch.get("SYSP_ELEC_RATE") or 0)


def set_elec_rate(branch_id, text, ope_user):
    try:
        rate = to_number(text, None)
    except ValueError:
        raise ValidationError("errNumber")
    if rate is None or rate < 0 or rate > 100:
        raise ValidationError("errElecRate")
    values = {"SYSP_ELEC_RATE": round_half_up(rate, 4)}
    values.update(audit_update(ope_user))
    col("AOIMS_SYSPARAM").update_one({"BRANCH_ID": branch_id}, {"$set": values})
    return values["SYSP_ELEC_RATE"]


def month_bounds(year, month):
    start = datetime(year, month, 1, tzinfo=local_tz())
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=local_tz())
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def post_month_to_account(branch_id, year, month, description, ope_user):
    """Posts the electricity cost of a month (all meters, kWh x rate) as an expense, once (reference EL-YYYYMM).
    It is posted as a manual entry, so it can be deleted on Account and posted again after a correction."""
    from .account import Account
    ref = "EL-%04d%02d" % (year, month)
    if col(Account.COL).find_one({"BRANCH_ID": branch_id, "REF_ORD_ID": ref}, NO_ID):
        raise ValidationError("errElecPosted", ref=ref)
    start, end = month_bounds(year, month)
    kwh = Meter.used_between(branch_id, start, end)
    amount = round_half_up(kwh * elec_rate(branch_id), 2)
    if amount <= 0:
        raise ValidationError("errElecNothing")
    entry = Account._post(branch_id, "EX", amount, description % {"kwh": "%.1f" % kwh}, ref, None, None, ope_user)
    values = {"AUTO_FLG": False, "ACC_DATE": min(end, now_utc()) - timedelta(seconds=1)}
    col(Account.COL).update_one({"ACC_ID": entry["ACC_ID"]}, {"$set": values})
    col(Account.LOG).update_one({"ACC_ID": entry["ACC_ID"], "LOG_ACTION": "C"}, {"$set": values})
    return dict(entry, **values), kwh
