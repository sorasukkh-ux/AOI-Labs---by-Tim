"""AOIMS_CUSTOMER: customer master. Code = C + YYMMDD + 3-digit running number, restarting each day."""
import re
from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, to_number, round_half_up, DuplicateKey
from .counter import Counter
from . import phone
from ..timeutil import now_local


ACTIVE, DELETED = "A", "D"


class Customer:
    COL = "AOIMS_CUSTOMER"

    @classmethod
    def find(cls, code):
        return col(cls.COL).find_one({"CUST_CODE": (code or "").strip().upper()}, NO_ID)

    @classmethod
    def search(cls, query="", limit=50):
        """Matches code, name or mobile number. A small master, so the filter runs in Python."""
        query = (query or "").strip().lower()
        digits = "".join(ch for ch in query if ch.isdigit())
        out = []
        for c in col(cls.COL).find({"CUST_STATUS": {"$ne": DELETED}}, NO_ID).sort([("CUST_NAME", 1)]):
            mobile_digits = "".join(ch for ch in (c.get("MOBILE_NO") or "") if ch.isdigit())
            # also the international form: +66 81 234 5678 finds 081-234-5678
            full = (c.get("MOBILE_CC") or phone.DEFAULT).lstrip("+") + (mobile_digits[1:] if mobile_digits.startswith("0") else mobile_digits)
            text = " ".join([c["CUST_CODE"], c.get("CUST_NAME") or "", c.get("MOBILE_NO") or ""]).lower()
            if not query or query in text or (len(digits) >= 3 and (digits in mobile_digits or digits in full)):
                out.append(c)
                if len(out) >= limit:
                    break
        return out

    @classmethod
    def all_count(cls):
        return col(cls.COL).count_documents({})

    @classmethod
    def search_code(cls, text, limit=50):
        """Customers whose code contains `text` (Scan QR field: a scanned or partly typed customer ID)."""
        text = (text or "").strip().upper()
        out = []
        for c in col(cls.COL).find({"CUST_STATUS": {"$ne": DELETED}}, NO_ID).sort([("CUST_CODE", 1)]):
            if text and text in c["CUST_CODE"]:
                out.append(c)
                if len(out) >= limit:
                    break
        return out

    @staticmethod
    def name_key(name):
        """Names are compared without case and extra spaces: 'สมชาย  ใจดี' = 'สมชาย ใจดี'."""
        return re.sub(r"\s+", " ", (name or "").strip()).lower()

    @classmethod
    def same_name(cls, name, except_code=None):
        """Another customer (not deleted) with the same name, or None."""
        key = cls.name_key(name)
        if not key:
            return None
        for c in col(cls.COL).find({"CUST_STATUS": {"$ne": DELETED}}, NO_ID):
            if c["CUST_CODE"] != except_code and cls.name_key(c.get("CUST_NAME")) == key:
                return c
        return None

    @staticmethod
    def _validate(form):
        name, mobile, address = clean_text(form.get("CUST_NAME")), clean_text(form.get("MOBILE_NO"), 20), clean_text(form.get("ADDRESS"))
        if not name or not mobile or not address:
            raise ValidationError("custErrRequired")
        if not re.match(r"^[0-9 ()\-]+$", mobile) or not 6 <= len(re.sub(r"\D", "", mobile)) <= 15:
            raise ValidationError("custErrMobile")
        try:
            distance = to_number(form.get("DISTANCE_KM"))
        except ValueError:
            raise ValidationError("custErrDistance")
        if distance is not None and distance < 0:
            raise ValidationError("custErrDistance")
        values = {"CUST_NAME": name, "LINE_ID": clean_text(form.get("LINE_ID"), 50), "MOBILE_NO": mobile, "ADDRESS": address,
                  "MOBILE_CC": phone.clean_code(form.get("MOBILE_CC")),
                  "DISTANCE_KM": None if distance is None else round_half_up(distance, 3)}
        if "LINE_USER_ID" in form:                # Customer Maintenance only: LINE's own ID for sending messages
            user_id = (form.get("LINE_USER_ID") or "").strip()
            if user_id and not re.match(r"^U[0-9a-f]{32}$", user_id):
                raise ValidationError("custErrLineUser")
            values["LINE_USER_ID"] = user_id or None
        return values

    @classmethod
    def next_code(cls):
        today = now_local()
        prefix = "C%02d%02d%02d" % (today.year % 100, today.month, today.day)
        seq = Counter.next("CUST-" + prefix)
        if seq > 999:
            raise ValidationError("custErrDailyLimit")
        return "%s%03d" % (prefix, seq)

    @classmethod
    def create(cls, form, ope_user):
        values = cls._validate(form)
        dup = cls.same_name(values["CUST_NAME"])
        if dup:
            raise ValidationError("custErrDupName", name=dup["CUST_NAME"], code=dup["CUST_CODE"])
        values["CUST_STATUS"] = ACTIVE
        for _attempt in range(3):                  # the counter is atomic; the retry only covers imported data
            values.update({"CUST_CODE": cls.next_code()}, **audit_new(ope_user))
            try:
                col(cls.COL).insert_one(dict(values))
                return cls.find(values["CUST_CODE"])
            except DuplicateKey:
                continue
        raise ValidationError("errTryAgain")

    @classmethod
    def update(cls, code, form, ope_user):
        current = cls.find(code)
        if not current:
            raise ValidationError("custErrSelect")
        values = cls._validate(form)
        dup = cls.same_name(values["CUST_NAME"], current["CUST_CODE"])
        if dup:
            raise ValidationError("custErrDupName", name=dup["CUST_NAME"], code=dup["CUST_CODE"])
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"CUST_CODE": current["CUST_CODE"]}, {"$set": values})
        return cls.find(code)

    @classmethod
    def order_counts(cls, code):
        """(all orders, open orders) of the customer: milling and buying / selling."""
        total = opened = 0
        for name, field, done in (("AOITS_ORDMILLING", "MILL_STATUS", ("C", "CC")), ("AOITS_ORDBUYSELL", "ORD_STATUS", ("C", "CC"))):
            for o in col(name).find({"CUST_CODE": code}, {"_id": 0, field: 1}):
                total += 1
                opened += o.get(field) not in done
        return total, opened

    @classmethod
    def delete(cls, code, ope_user):
        """A customer without orders is removed. One with finished orders is marked deleted (CUST_STATUS D): it no
        longer appears in searches or new orders, and its order history stays for the accounts and the analysis.
        Returns 'removed' or 'hidden'."""
        current = cls.find(code)
        if not current or current.get("CUST_STATUS") == DELETED:
            raise ValidationError("custErrSelect")
        total, opened = cls.order_counts(current["CUST_CODE"])
        if opened:
            raise ValidationError("custErrOpenOrders", n=opened)
        if not total:
            col(cls.COL).delete_one({"CUST_CODE": current["CUST_CODE"]})
            return "removed"
        values = {"CUST_STATUS": DELETED, "LINE_USER_ID": None}
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"CUST_CODE": current["CUST_CODE"]}, {"$set": values})
        return "hidden"
