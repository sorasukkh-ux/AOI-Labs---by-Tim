"""AOITS_MILL_TEST: milling tests (paddy weighed in, milling minutes, outputs weighed out).

The yield ratios (AOIMS_MILLYIELD) and the milling time per kg (AOIMS_SYSPARAM.SYSP_MILLTIMEKG) of a branch are
the weighted averages of its tests, worked out again every time a test is added or deleted:

  ratio of an output = sum of that output (kg) / sum of the paddy (kg) of the tests where it was weighed
  minutes per kg     = sum of the minutes / sum of the paddy of the tests with a time

A test can be for one variety or for all varieties (RICE_VARIETY blank). A variety with tests of its own uses those;
otherwise the all-varieties tests. An output nobody weighed keeps the ratio typed on Milling Production.
"""
from datetime import datetime

from .base import col, NO_ID, ValidationError, audit_new, clean_text, round_half_up, to_number
from .counter import Counter
from .mill_yield import MillYield
from .rules import yield_rule
from . import variety as var
from ..timeutil import local_tz, now_local, now_utc

OUTPUTS = (("rice", "RICE_KG"), ("broken", "BROKE_KG"), ("bran", "BRAN_KG"), ("husk", "HUSK_KG"))


class MillTest:
    COL = "AOITS_MILL_TEST"

    @classmethod
    def for_branch(cls, branch_id):
        return list(col(cls.COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("TEST_DATE", -1), ("TEST_ID", -1)]))

    @staticmethod
    def _kg(form, name, required=False):
        try:
            value = to_number(form.get(name), None)
        except ValueError:
            raise ValidationError("errNumber")
        if value is None:
            if required:
                raise ValidationError("mtErrRequired")
            return None
        if value < 0:
            raise ValidationError("errNegative")
        return round_half_up(value, 3)

    @classmethod
    def add(cls, branch_id, form, ope_user, test_id=None):
        paddy = cls._kg(form, "PADDY_KG", required=True)
        if not paddy:
            raise ValidationError("alertEnterPaddy")
        minutes = cls._kg(form, "MILL_MIN")
        outputs = {field: cls._kg(form, field, required=(field == "RICE_KG")) for _k, field in OUTPUTS}
        if sum(v for v in outputs.values() if v) > paddy:
            raise ValidationError("mtErrMore")                    # more out than in
        variety = (form.get("RICE_VARIETY") or "").strip().upper()
        variety = variety if variety in var.VARIETIES else None
        text = (form.get("TEST_DATE") or "").strip()
        try:
            day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=local_tz()) if text else now_local()
        except ValueError:
            raise ValidationError("errDate")
        if day.date() > now_local().date():
            raise ValidationError("mtErrFuture")
        if test_id is None:
            today = now_local()
            key = "%04d%02d%02d" % (today.year, today.month, today.day)
            test_id = "MT-%s-%04d" % (key, Counter.next("MT-" + key))
        doc = dict(outputs, TEST_ID=test_id, BRANCH_ID=branch_id, RICE_VARIETY=variety, TEST_DATE=day,
                   PADDY_KG=paddy, MILL_MIN=minutes, TEST_REF=clean_text(form.get("TEST_REF"), 100) or None)
        doc.update(audit_new(ope_user))
        col(cls.COL).insert_one(dict(doc))
        cls.apply(branch_id, ope_user)
        return doc

    @classmethod
    def delete(cls, branch_id, test_id, ope_user):
        if not col(cls.COL).find_one({"BRANCH_ID": branch_id, "TEST_ID": test_id}, NO_ID):
            raise ValidationError("mtErrUnknown")
        col(cls.COL).delete_one({"BRANCH_ID": branch_id, "TEST_ID": test_id})
        cls.apply(branch_id, ope_user)

    # ---------- averages ----------
    @classmethod
    def averages(cls, branch_id, variety, tests=None):
        """{'ratios': {...} or partial, 'basis': {output: number of tests}, 'own': tests are of this variety}."""
        tests = cls.for_branch(branch_id) if tests is None else tests
        own = [t for t in tests if t.get("RICE_VARIETY") == variety]
        use = own or [t for t in tests if not t.get("RICE_VARIETY")]
        ratios, basis = {}, {}
        for key, field in OUTPUTS:
            measured = [t for t in use if t.get(field) is not None]
            paddy = sum(t["PADDY_KG"] for t in measured)
            if paddy > 0:
                ratios[key] = round_half_up(sum(t[field] for t in measured) / paddy, 4)
                basis[key] = len(measured)
        return {"ratios": ratios, "basis": basis, "own": bool(own), "tests": len(use)}

    @classmethod
    def minutes_per_kg(cls, tests):
        timed = [t for t in tests if t.get("MILL_MIN") is not None]
        paddy = sum(t["PADDY_KG"] for t in timed)
        return (round_half_up(sum(t["MILL_MIN"] for t in timed) / paddy, 3), len(timed)) if paddy > 0 else (None, 0)

    @classmethod
    def apply(cls, branch_id, ope_user):
        """Recalculates the yield ratios of every variety and the branch's minutes per kg from the tests.
        Returns {variety: ratios applied or None (no tests)}; outputs nobody weighed keep their current ratio."""
        tests = cls.for_branch(branch_id)
        result = {}
        for variety in var.VARIETIES:
            avg = cls.averages(branch_id, variety, tests)
            if not avg["ratios"]:
                result[variety] = None
                continue
            ratios = dict(MillYield.ratios(branch_id, variety), **avg["ratios"])
            measured_total = sum(ratios[k] for k in avg["ratios"])
            rest = [k for k in yield_rule.KEYS if k not in avg["ratios"]]
            rest_total = sum(ratios[k] for k in rest)
            if measured_total + rest_total > 1 and rest_total > 0:      # make room: the estimates shrink, not the weighings
                scale = max(0.0, 1 - measured_total) / rest_total
                for k in rest:
                    ratios[k] = round_half_up(ratios[k] * scale, 4)
            if not yield_rule.check(ratios)["ok"]:
                result[variety] = None
                continue
            result[variety] = MillYield.store(branch_id, variety, ratios, ope_user)
        minutes, _n = cls.minutes_per_kg(tests)
        if minutes is not None:
            from .base import audit_update
            values = {"SYSP_MILLTIMEKG": minutes}
            values.update(audit_update(ope_user))
            col("AOIMS_SYSPARAM").update_one({"BRANCH_ID": branch_id}, {"$set": values})
        return result
