"""AOIMS_MILLYIELD: output ratios per branch and rice variety (Milling Production screen).

A record without RICE_VARIETY (saved before the varieties existed) is the white-rice record. A variety with no
record of its own uses the white-rice ratios until it is saved or worked out from its milling tests."""
from .base import col, NO_ID, ValidationError, audit_new, audit_update, to_number, round_half_up
from .rules import yield_rule
from . import variety as var

FIELDS = {"rice": "YIELD_RICE", "bran": "YIELD_BRAN", "broken": "YIELD_BROKE", "husk": "YIELD_HUSK"}


class MillYield:
    COL = "AOIMS_MILLYIELD"

    @classmethod
    def _doc(cls, branch_id, variety):
        doc = col(cls.COL).find_one({"BRANCH_ID": branch_id, "RICE_VARIETY": variety}, NO_ID)
        if doc is None and variety == var.DEFAULT:
            doc = col(cls.COL).find_one({"BRANCH_ID": branch_id, "RICE_VARIETY": {"$exists": False}}, NO_ID)
        return doc

    @classmethod
    def ratios(cls, branch_id, variety=None):
        """{'rice': .., 'bran': .., 'broken': .., 'husk': ..} of a variety (zeros when the branch has no record yet)."""
        variety = var.clean(variety)
        doc = cls._doc(branch_id, variety) or (cls._doc(branch_id, var.DEFAULT) if variety != var.DEFAULT else None) or {}
        return {key: float(doc.get(field) or 0) for key, field in FIELDS.items()}

    @classmethod
    def all_ratios(cls, branch_id):
        """{variety: ratios} for every variety (the registration screen's live outputs)."""
        return {v: cls.ratios(branch_id, v) for v in var.VARIETIES}

    @classmethod
    def has_own(cls, branch_id, variety):
        return cls._doc(branch_id, var.clean(variety)) is not None

    @classmethod
    def save(cls, branch_id, form, ope_user, variety=None):
        ratios = {}
        for key in yield_rule.KEYS:
            try:
                ratios[key] = to_number(form.get(key), float("nan"))
            except ValueError:
                ratios[key] = float("nan")
        result = yield_rule.check(ratios)
        if not result["in_range"]:
            raise ValidationError("mpErrRange")
        if not result["ok"]:
            raise ValidationError("mpErrTotal", pct="%.2f" % (result["total"] * 100))
        return cls.store(branch_id, var.clean(variety or form.get("RICE_VARIETY")), ratios, ope_user)

    @classmethod
    def store(cls, branch_id, variety, ratios, ope_user):
        values = {FIELDS[key]: round_half_up(ratios[key], 4) for key in yield_rule.KEYS}
        values["RICE_VARIETY"] = variety
        existing = cls._doc(branch_id, variety)
        if existing is not None:
            values.update(audit_update(ope_user))
            flt = {"BRANCH_ID": branch_id, "RICE_VARIETY": variety}
            if "RICE_VARIETY" not in existing:
                flt = {"BRANCH_ID": branch_id, "RICE_VARIETY": {"$exists": False}}      # the old record becomes white rice
            col(cls.COL).update_one(flt, {"$set": values})
        else:
            values.update({"BRANCH_ID": branch_id}, **audit_new(ope_user))
            col(cls.COL).insert_one(values)
        return cls.ratios(branch_id, variety)
