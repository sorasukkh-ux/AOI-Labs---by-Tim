"""AOITS_ACCOUNT: the income/expense ledger. Closed orders post automatically; the Account screen lists the
entries and adds or deletes manual ones (auto-posted entries cannot be deleted). Every change goes to AOILOG_ACCOUNT."""
from datetime import datetime

from .base import col, NO_ID, ValidationError, audit_new, clean_text, round_half_up, to_number
from .counter import Counter
from ..timeutil import local_tz, now_local, now_utc

TAKE_HOME_FIELDS = ("EST_PAY_BRAN", "EST_PAY_BROKE")


class Account:
    COL = "AOITS_ACCOUNT"
    LOG = "AOILOG_ACCOUNT"

    @classmethod
    def _next_id(cls):
        today = now_local()
        day = "%04d%02d%02d" % (today.year, today.month, today.day)
        return "AC-%s-%04d" % (day, Counter.next("AC-" + day))

    @classmethod
    def _post(cls, branch_id, acc_type, amount, description, ord_id, ord_type, cust_code, ope_user):
        amount = round_half_up(amount, 2)
        if amount <= 0:
            return None
        entry = {"ACC_ID": cls._next_id(), "BRANCH_ID": branch_id, "ACC_DATE": now_utc(), "ACC_TYPE": acc_type,
                 "ACC_DESC": description, "ACC_AMOUNT": amount, "REF_ORD_ID": ord_id, "REF_ORD_TYPE": ord_type,
                 "CUST_CODE": cust_code, "AUTO_FLG": True}
        entry.update(audit_new(ope_user))
        col(cls.COL).insert_one(dict(entry))
        log = {k: v for k, v in entry.items() if k not in ("REGIST_DATE", "LAST_UPDATE")}
        log.update({"LOG_ACTION": "C", "LOG_DATE": now_utc()})
        col(cls.LOG).insert_one(log)
        return entry

    @classmethod
    def post_milling_income(cls, order, descriptions, ope_user):
        """Income for a closed milling order (payment is taken when the customer collects).

        When the take-home bran/broken rice has its own selling orders (REF_BUYING_ORDER), those orders post
        that part when they are closed, so the milling order posts only the fee and the delivery fee.
        `descriptions`: {"full": ..., "fee": ...} in the user's language."""
        fields = ["EST_PAY_MILLFEE", "EST_PAY_DELIVERY"]
        separate = bool(order.get("REF_BUYING_ORDER"))
        if not separate:
            fields += TAKE_HOME_FIELDS
        amount = sum(order.get(f) or 0 for f in fields)
        return cls._post(order["BRANCH_ID"], "IN", amount, descriptions["fee" if separate else "full"],
                         order["ORD_ID"], "ML", order.get("CUST_CODE"), ope_user)

    @classmethod
    def post_order(cls, order, amount, acc_type, description, ope_user):
        """Income (selling) or expense (buying) for a closed buying/selling order."""
        return cls._post(order["BRANCH_ID"], acc_type, amount, description, order["ORD_ID"], order["ORD_TYPE"],
                         order.get("CUST_CODE"), ope_user)


    # ---------- Account screen ----------
    @classmethod
    def entries(cls, branch_id, acc_type=None, limit=300):
        flt = {"BRANCH_ID": branch_id}
        if acc_type:
            flt["ACC_TYPE"] = acc_type
        return list(col(cls.COL).find(flt, NO_ID).sort([("ACC_DATE", -1), ("ACC_ID", -1)]).limit(limit))

    @classmethod
    def totals(cls, branch_id):
        income = expense = 0.0
        for e in col(cls.COL).find({"BRANCH_ID": branch_id}, {"_id": 0, "ACC_TYPE": 1, "ACC_AMOUNT": 1}):
            if e["ACC_TYPE"] == "IN":
                income += e["ACC_AMOUNT"]
            else:
                expense += e["ACC_AMOUNT"]
        return round_half_up(income, 2), round_half_up(expense, 2), round_half_up(income - expense, 2)

    @classmethod
    def add_manual(cls, branch_id, form, ope_user):
        description = clean_text(form.get("ACC_DESC"))
        try:
            amount = to_number(form.get("ACC_AMOUNT"), None)
        except ValueError:
            raise ValidationError("errNumber")
        if not description or amount is None:
            raise ValidationError("acctAlertFillFields")
        if amount <= 0:
            raise ValidationError("errAcctAmount")
        acc_type = "EX" if form.get("ACC_TYPE") == "EX" else "IN"
        text = (form.get("ACC_DATE") or "").strip()
        try:
            day = datetime.strptime(text, "%Y-%m-%d").replace(hour=12, tzinfo=local_tz()) if text else now_utc()
        except ValueError:
            raise ValidationError("errDate")
        entry = cls._post(branch_id, acc_type, amount, description, clean_text(form.get("REF_ORD_ID"), 20) or None, None, None, ope_user)
        values = {"AUTO_FLG": False, "ACC_DATE": day}
        from .investment import BUDGET_CATS                  # budget category for Investment & Break-even (optional)
        cat = form.get("ACC_CAT")
        if cat in BUDGET_CATS and BUDGET_CATS[cat][0] == acc_type:
            values["ACC_CAT"] = cat
        col(cls.COL).update_one({"ACC_ID": entry["ACC_ID"]}, {"$set": values})
        col(cls.LOG).update_one({"ACC_ID": entry["ACC_ID"], "LOG_ACTION": "C"}, {"$set": values})
        return dict(entry, **values)

    @classmethod
    def delete_manual(cls, branch_id, acc_id, ope_user):
        entry = col(cls.COL).find_one({"BRANCH_ID": branch_id, "ACC_ID": acc_id}, NO_ID)
        if not entry:
            raise ValidationError("errAcctNotFound")
        if entry.get("AUTO_FLG"):
            raise ValidationError("errAcctAuto")
        col(cls.COL).delete_one({"ACC_ID": acc_id})
        log = {k: v for k, v in entry.items() if k not in ("REGIST_DATE", "LAST_UPDATE")}
        log.update({"LOG_ACTION": "D", "LOG_DATE": now_utc(), "OPE_USER": ope_user})
        col(cls.LOG).insert_one(log)
