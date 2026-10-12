"""AOIMS_SYSPARAM: one record per branch (factory) with its milling settings."""
import re

from .base import col, NO_ID, ValidationError, audit_update, to_number, round_half_up

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Branch:
    COL = "AOIMS_SYSPARAM"
    ALL = "ALL"                     # AOIMS_USER.BRANCH_ID value meaning "every branch"

    @classmethod
    def all(cls):
        return list(col(cls.COL).find({}, NO_ID).sort([("BRANCH_ID", 1)]))

    @classmethod
    def ids(cls):
        return [b["BRANCH_ID"] for b in cls.all()]

    @classmethod
    def get(cls, branch_id):
        return col(cls.COL).find_one({"BRANCH_ID": branch_id}, NO_ID)

    @staticmethod
    def periods(branch):
        """SYSP_QUEUEPERIOD 'HH:MM, HH:MM' -> sorted list."""
        return sorted(p.strip() for p in (branch.get("SYSP_QUEUEPERIOD") or "").split(",") if p.strip())

    @classmethod
    def save_milling_settings(cls, branch_id, form, ope_user):
        """Milling Maintenance screen. `form` holds the raw text of each field."""
        if not cls.get(branch_id):
            raise ValidationError("errBranchUnknown")
        try:
            time_kg = to_number(form.get("SYSP_MILLTIMEKG"))
            gap = to_number(form.get("SYSP_MILLORDGAP"), 0)
            deliver_add = to_number(form.get("SYSP_DELIVERTIMEADD"), 0)
            unit_kg = to_number(form.get("SYSP_MILLFEEUNITKG"))
            fee = to_number(form.get("SYSP_MILLFEEPERUNIT"))
            delivery_fee = to_number(form.get("SYSP_DELIVERYFEEKM"), 0)
            proc_cost = to_number(form.get("SYSP_PROCCOSTKG"), 0)
            labour_day = to_number(form.get("SYSP_LABORDAY"), 0)
            workers = to_number(form.get("SYSP_WORKERS"), 0)
            plan_kg = to_number(form.get("SYSP_PLANKGDAY"), 0)
            elec_month = to_number(form.get("SYSP_ELECMONTH"), 0)
            workdays = to_number(form.get("SYSP_WORKDAYS"), 26)
        except ValueError:
            raise ValidationError("errNumber")
        if time_kg is None or time_kg <= 0:
            raise ValidationError("errTimePerKg")
        if gap < 0 or deliver_add < 0:
            raise ValidationError("errNegative")
        if unit_kg is None or unit_kg <= 0 or fee is None or fee < 0:
            raise ValidationError("errFeeUnit")
        if delivery_fee < 0:
            raise ValidationError("errDeliveryFee")
        if proc_cost < 0 or proc_cost > 100:
            raise ValidationError("errProcCost")
        if labour_day < 0 or labour_day > 100000:
            raise ValidationError("errLabourDay")
        if workers < 0 or workers > 500 or workers != int(workers):
            raise ValidationError("errWorkers")
        if plan_kg < 0 or plan_kg > 1000000:
            raise ValidationError("errPlanKg")
        if elec_month < 0 or elec_month > 10000000:
            raise ValidationError("errElecMonth")
        if workdays < 1 or workdays > 31:
            raise ValidationError("errWorkdays")
        open_time = (form.get("SYSP_OPETIMESTART") or "").strip()
        close_time = (form.get("SYSP_OPETIMEEND") or "").strip()
        if not TIME_RE.match(open_time) or not TIME_RE.match(close_time) or open_time >= close_time:
            raise ValidationError("errOperatingHours")
        periods = [p.strip() for p in (form.get("SYSP_QUEUEPERIOD") or "").split(",") if p.strip()]
        if not periods or any(not TIME_RE.match(p) for p in periods):
            raise ValidationError("errPeriods")
        values = {
            "SYSP_MILLTIMEKG": round_half_up(time_kg, 3), "SYSP_MILLORDGAP": round_half_up(gap, 3),
            "SYSP_OPETIMESTART": open_time, "SYSP_OPETIMEEND": close_time,
            "SYSP_QUEUEPERIOD": ", ".join(sorted(set(periods))),
            "SYSP_DELIVERTIMEADD": round_half_up(deliver_add, 3),
            "SYSP_MILLFEEUNITKG": round_half_up(unit_kg, 3), "SYSP_MILLFEEPERUNIT": round_half_up(fee, 3),
            "SYSP_DELIVERYFEEKM": round_half_up(delivery_fee, 3), "SYSP_PROCCOSTKG": round_half_up(proc_cost, 3),
            "SYSP_LABORDAY": round_half_up(labour_day, 2), "SYSP_WORKERS": int(workers),
            "SYSP_PLANKGDAY": round_half_up(plan_kg, 1), "SYSP_ELECMONTH": round_half_up(elec_month, 2),
            "SYSP_WORKDAYS": round_half_up(workdays, 1),
        }
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"BRANCH_ID": branch_id}, {"$set": values})
        return cls.get(branch_id)
