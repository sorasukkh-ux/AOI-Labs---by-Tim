"""AOINV_ONHAND (stock on hand), AOIMS_LOC (storage locations) and AOILOG_ONHAND (every stock movement).

Orders receive and release stock through inbound/release/release_free. The Inventory screens (list, item,
add, transfer, history, dashboard) use the item operations at the end of the module.

An "item" on the Inventory List is the stock of one material and lot kept for the same order (or the mill's
own stock) with the same status, spread over one or more locations (one AOINV_ONHAND record per location).
"""
import re

from .base import col, NO_ID, ValidationError, audit_new, audit_update, round_half_up, to_number
from .counter import Counter
from ..timeutil import now_local, now_utc

LOC_PATTERN = re.compile(r"^\d{2}-\d{3}-\d{3}$")          # BAY-BANK-LEVEL, for example 02-003-001
INV_STATUSES = ("A", "B", "U")                          # Available, Blocked, Unable to ship
LOC_AVAILABLE, LOC_OCCUPIED, LOC_BLOCKED = "A", "O", "B"
OPE_INBOUND, OPE_OUTBOUND = "I", "O"
OPE_ADJ_INC, OPE_ADJ_DEC, OPE_TRANSFER, OPE_STATUS = "AI", "AD", "T", "ST"
INCREASE_TYPES, DECREASE_TYPES = (OPE_INBOUND, OPE_ADJ_INC), (OPE_OUTBOUND, OPE_ADJ_DEC)
# reasons of the design's inventory form -> MOVE_TYPE written to AOILOG_ONHAND
REASONS = {
    "increase": [("milling", "Rice Mill"), ("buying", "Buying"), ("producing", "Produce"), ("order_prep", "Order preparation"), ("other", "Other")],
    "decrease": [("milling_return", "Milling return"), ("selling", "Selling"), ("producing", "Produce"), ("other", "Other")],
}
MOVE_MILL, MOVE_MILL_PICKUP, MOVE_ORDER_PREP, MOVE_ORDER_CLOSE = "Rice Mill", "Rice Mill pickup", "Order preparation", "Order closed"
MOVE_BUYING, MOVE_SELLING = "Buying", "Selling"


def lot_of(milling_ord_id):
    """SUB_1: everything that came out of one milling order shares this lot number."""
    return "LOT-" + milling_ord_id


class Location:
    COL = "AOIMS_LOC"

    @classmethod
    def get(cls, branch_id, loc_id):
        return col(cls.COL).find_one({"BRANCH_ID": branch_id, "LOC_ID": loc_id}, NO_ID)

    @staticmethod
    def clean(loc_id):
        loc_id = (loc_id or "").strip()
        digits = re.sub(r"\D", "", loc_id)
        if len(digits) == 8:                      # digits only (or any separators): 00001001 -> 00-001-001
            loc_id = "%s-%s-%s" % (digits[:2], digits[2:5], digits[5:])
        if not LOC_PATTERN.match(loc_id):
            raise ValidationError("errLocFormat")
        return loc_id

    @classmethod
    def check_usable(cls, branch_id, loc_id):
        loc = cls.get(branch_id, loc_id)
        if loc and loc.get("LOC_STATUS") == LOC_BLOCKED:
            raise ValidationError("errLocBlocked", loc=loc_id)

    @classmethod
    def mark(cls, branch_id, loc_id, ope_user):
        """Occupied while it holds stock, Available again when the last stock leaves. New locations are added."""
        holds_stock = col(Inventory.COL).count_documents({"BRANCH_ID": branch_id, "LOC_ID": loc_id}) > 0
        status = LOC_OCCUPIED if holds_stock else LOC_AVAILABLE
        loc = cls.get(branch_id, loc_id)
        if loc is None:
            doc = {"BRANCH_ID": branch_id, "LOC_ID": loc_id, "LOC_STATUS": status, "RESERVE_KEY": None}
            doc.update(audit_new(ope_user))
            col(cls.COL).insert_one(doc)
        elif loc.get("LOC_STATUS") != LOC_BLOCKED:
            values = {"LOC_STATUS": status}
            values.update(audit_update(ope_user))
            col(cls.COL).update_one({"BRANCH_ID": branch_id, "LOC_ID": loc_id}, {"$set": values})


class Inventory:
    COL = "AOINV_ONHAND"
    LOG = "AOILOG_ONHAND"

    @staticmethod
    def parse_inbound(form, expected_qty):
        """The inbound pop-up: quantity received (defaults to the expected quantity), location and stock status."""
        try:
            qty = to_number(form.get("INV_QTY"), expected_qty)
        except ValueError:
            raise ValidationError("errNumber")
        if qty is None or qty <= 0:
            raise ValidationError("errInboundQty")
        status = form.get("INV_STATUS") if form.get("INV_STATUS") in INV_STATUSES else "A"
        return {"qty": round_half_up(qty, 3), "loc": Location.clean(form.get("LOC_ID")), "status": status}

    @classmethod
    def _next_id(cls):
        today = now_local()
        day = "%04d%02d%02d" % (today.year, today.month, today.day)
        return "IV-%s-%04d" % (day, Counter.next("IV-" + day))

    @classmethod
    def _log(cls, record, ope_type, move_type, ori_loc, new_loc, qty, ref_ord_id, ref_ord_type, ope_user, new_status=None):
        col(cls.LOG).insert_one({
            "INV_ID": record["INV_ID"], "BRANCH_ID": record["BRANCH_ID"], "ORILOC_ID": ori_loc, "NEWLOC_ID": new_loc,
            "MAT_CODE": record["MAT_CODE"], "SUB_1": record.get("SUB_1"), "SUB_2": record.get("SUB_2"), "INV_QTY": qty,
            "ORI_STATUS": record["INV_STATUS"], "NEW_STATUS": new_status or record["INV_STATUS"], "UOM_CODE": record["UOM_CODE"],
            "OPE_TYPE": ope_type, "MOVE_TYPE": move_type, "REF_ORD_ID": ref_ord_id, "REF_ORD_TYPE": ref_ord_type,
            "OPE_USER": ope_user, "LOG_DATE": now_utc()})

    @classmethod
    def inbound(cls, branch_id, mat_code, inbound, lot, ref_ord_id, ref_ord_type, move_type, ope_user, reserve=True):
        """Receives stock into a location. `inbound` is the result of parse_inbound().
        reserve=True keeps the stock for the order (SUB_2 = order ID, for a customer's goods);
        reserve=False makes it the mill's own stock (SUB_2 empty), which selling orders take from."""
        Location.check_usable(branch_id, inbound["loc"])
        record = {"INV_ID": cls._next_id(), "BRANCH_ID": branch_id, "LOC_ID": inbound["loc"], "MAT_CODE": mat_code,
                  "SUB_1": lot, "SUB_2": ref_ord_id if reserve else None, "INV_QTY": inbound["qty"], "INV_STATUS": inbound["status"], "UOM_CODE": "KG"}
        record.update(audit_new(ope_user))
        col(cls.COL).insert_one(dict(record))
        cls._log(record, OPE_INBOUND, move_type, inbound["loc"], inbound["loc"], inbound["qty"], ref_ord_id, ref_ord_type, ope_user)
        Location.mark(branch_id, inbound["loc"], ope_user)
        return record

    @classmethod
    def for_order(cls, branch_id, ord_id):
        """Stock kept for one order (SUB_2 = the order ID)."""
        return list(col(cls.COL).find({"BRANCH_ID": branch_id, "SUB_2": ord_id}, NO_ID).sort([("REGIST_DATE", 1)]))

    @classmethod
    def release(cls, branch_id, ord_id, ref_ord_type, move_type, ope_user):
        """Outbound of everything kept for the order, when the customer has collected it. Returns the released records."""
        released = cls.for_order(branch_id, ord_id)
        for record in released:
            col(cls.COL).delete_one({"INV_ID": record["INV_ID"]})
            cls._log(record, OPE_OUTBOUND, move_type, record["LOC_ID"], record["LOC_ID"], record["INV_QTY"], ord_id, ref_ord_type, ope_user)
            Location.mark(branch_id, record["LOC_ID"], ope_user)
        return released

    @staticmethod
    def describe(records):
        """'17.00 kg at 01-002-005' lines for confirmations."""
        return ", ".join("%.2f kg @ %s" % (r["INV_QTY"], r["LOC_ID"]) for r in records)

    @classmethod
    def last_location(cls, branch_id):
        """The location of the most recent inbound at the branch, offered as the suggestion in the pop-up."""
        last = col(cls.LOG).find_one({"BRANCH_ID": branch_id, "OPE_TYPE": OPE_INBOUND}, NO_ID, sort=[("LOG_DATE", -1)])
        return (last or {}).get("NEWLOC_ID") or ""


    # ---------- the mill's own stock (not kept for an order) ----------
    @classmethod
    def free_stock(cls, branch_id, mat_code):
        """Available stock of a material that is not kept for any order, oldest first."""
        return list(col(cls.COL).find({"BRANCH_ID": branch_id, "MAT_CODE": mat_code, "SUB_2": None, "INV_STATUS": "A"},
                                      NO_ID).sort([("REGIST_DATE", 1)]))

    @classmethod
    def free_qty(cls, branch_id, mat_code):
        return round_half_up(sum(r["INV_QTY"] for r in cls.free_stock(branch_id, mat_code)), 3)

    @classmethod
    def release_free(cls, branch_id, mat_code, qty, ord_id, ord_type, move_type, ope_user):
        """Outbound of `qty` from the mill's own stock, first in first out. Takes what there is when the stock
        is short (the operator was warned). Returns [(LOC_ID, qty)] taken."""
        taken, remaining = [], round_half_up(qty, 3)
        for record in cls.free_stock(branch_id, mat_code):
            if remaining <= 0:
                break
            take = min(record["INV_QTY"], remaining)
            left = round_half_up(record["INV_QTY"] - take, 3)
            if left > 0:
                values = {"INV_QTY": left}
                values.update(audit_update(ope_user))
                col(cls.COL).update_one({"INV_ID": record["INV_ID"]}, {"$set": values})
            else:
                col(cls.COL).delete_one({"INV_ID": record["INV_ID"]})
            cls._log(record, OPE_OUTBOUND, move_type, record["LOC_ID"], record["LOC_ID"], take, ord_id, ord_type, ope_user)
            Location.mark(branch_id, record["LOC_ID"], ope_user)
            taken.append((record["LOC_ID"], take))
            remaining = round_half_up(remaining - take, 3)
        return taken


    # ---------- Inventory screens: items and their operations ----------
    @staticmethod
    def item_key(record):
        return (record["MAT_CODE"], record.get("SUB_1") or "", record.get("SUB_2") or "", record["INV_STATUS"])

    @classmethod
    def items(cls, branch_id):
        """The Inventory List: records grouped into items, each with its locations and total."""
        groups = {}
        for record in col(cls.COL).find({"BRANCH_ID": branch_id}, NO_ID).sort([("REGIST_DATE", 1)]):
            key = cls.item_key(record)
            item = groups.setdefault(key, {"MAT_CODE": record["MAT_CODE"], "SUB_1": record.get("SUB_1"), "SUB_2": record.get("SUB_2"),
                                           "INV_STATUS": record["INV_STATUS"], "UOM_CODE": record["UOM_CODE"], "INV_ID": record["INV_ID"],
                                           "qty": 0.0, "locations": [], "LAST_UPDATE": record["LAST_UPDATE"]})
            item["qty"] = round_half_up(item["qty"] + record["INV_QTY"], 3)
            item["locations"].append(record)
            item["LAST_UPDATE"] = max(item["LAST_UPDATE"], record["LAST_UPDATE"])
        return sorted(groups.values(), key=lambda i: (i["MAT_CODE"], i["SUB_1"] or "", i["SUB_2"] or ""))

    @classmethod
    def item(cls, branch_id, inv_id):
        """The item a record belongs to (opened from the list by one of its INV_IDs)."""
        record = col(cls.COL).find_one({"BRANCH_ID": branch_id, "INV_ID": inv_id}, NO_ID)
        if not record:
            return None
        key = cls.item_key(record)
        return next(i for i in cls.items(branch_id) if cls.item_key(i["locations"][0]) == key)

    @staticmethod
    def _qty(form, field="INV_QTY"):
        try:
            qty = to_number(form.get(field), None)
        except ValueError:
            raise ValidationError("errNumber")
        if qty is None or qty <= 0:
            raise ValidationError("errInvQty")
        return round_half_up(qty, 3)

    @staticmethod
    def move_reason(ope_type, form):
        """MOVE_TYPE from the reason chosen; "other" needs the remark and stores it."""
        group = "increase" if ope_type in INCREASE_TYPES else "decrease"
        reasons = dict(REASONS[group])
        reason = form.get("REASON") if form.get("REASON") in reasons else None
        if not reason:
            raise ValidationError("errInvReason")
        if reason == "other":
            remark = " ".join(str(form.get("REMARK") or "").split())[:180]
            if not remark:
                raise ValidationError("errInvRemark")
            return "Other - " + remark
        return reasons[reason]

    @classmethod
    def _merge_or_add(cls, branch_id, template, loc_id, qty, ope_user):
        """Adds qty at a location to the record of the same item there, or a new record. Returns the record."""
        flt = {"BRANCH_ID": branch_id, "LOC_ID": loc_id, "MAT_CODE": template["MAT_CODE"], "SUB_1": template.get("SUB_1"),
               "SUB_2": template.get("SUB_2"), "INV_STATUS": template["INV_STATUS"]}
        existing = col(cls.COL).find_one(flt, NO_ID)
        if existing:
            values = {"INV_QTY": round_half_up(existing["INV_QTY"] + qty, 3)}
            values.update(audit_update(ope_user))
            col(cls.COL).update_one({"INV_ID": existing["INV_ID"]}, {"$set": values})
            return dict(existing, **values)
        record = dict(flt, INV_ID=cls._next_id(), INV_QTY=qty, UOM_CODE=template.get("UOM_CODE") or "KG")
        record.update(audit_new(ope_user))
        col(cls.COL).insert_one(dict(record))
        return record

    @classmethod
    def _take(cls, record, qty, ope_user):
        left = round_half_up(record["INV_QTY"] - qty, 3)
        if left < 0:
            raise ValidationError("errInvNotEnough", loc=record["LOC_ID"], qty="%.2f" % record["INV_QTY"])
        if left == 0:
            col(cls.COL).delete_one({"INV_ID": record["INV_ID"]})
        else:
            values = {"INV_QTY": left}
            values.update(audit_update(ope_user))
            col(cls.COL).update_one({"INV_ID": record["INV_ID"]}, {"$set": values})

    @classmethod
    def _record(cls, branch_id, inv_id):
        record = col(cls.COL).find_one({"BRANCH_ID": branch_id, "INV_ID": inv_id}, NO_ID)
        if not record:
            raise ValidationError("errInvNotFound")
        return record

    @classmethod
    def add_item(cls, branch_id, form, ope_user, materials):
        """Add New Item: the mill's own stock of a material received at a location (Inbound or Adjust Increase).
        Husk from milling is registered here. `materials`: the material codes allowed."""
        mat = form.get("MAT_CODE")
        if mat not in materials:
            raise ValidationError("errInvMaterial")
        ope_type = form.get("OPE_TYPE") if form.get("OPE_TYPE") in INCREASE_TYPES else OPE_INBOUND
        qty = cls._qty(form)
        loc = Location.clean(form.get("LOC_ID"))
        Location.check_usable(branch_id, loc)
        status = form.get("INV_STATUS") if form.get("INV_STATUS") in INV_STATUSES else "A"
        move = cls.move_reason(ope_type, form)
        lot = " ".join(str(form.get("SUB_1") or "").split())[:20].upper()
        if not lot:
            today = now_local()
            lot = "LOT-%04d%02d%02d" % (today.year, today.month, today.day)
        template = {"MAT_CODE": mat, "SUB_1": lot, "SUB_2": None, "INV_STATUS": status, "UOM_CODE": "KG"}
        record = cls._merge_or_add(branch_id, template, loc, qty, ope_user)
        cls._log(record, ope_type, move, loc, loc, qty, None, None, ope_user)
        Location.mark(branch_id, loc, ope_user)
        return record

    @classmethod
    def operate(cls, branch_id, inv_id, form, ope_user):
        """Edit Item: one operation on a location of the item.
        I / AI: add qty at a location (an existing one or a new one); O / AD: take qty from a location;
        ST: change the stock status of the qty at a location."""
        record = cls._record(branch_id, inv_id)
        ope_type = form.get("OPE_TYPE")
        if ope_type in INCREASE_TYPES:
            qty = cls._qty(form)
            loc = Location.clean(form.get("LOC_ID") or record["LOC_ID"])
            Location.check_usable(branch_id, loc)
            move = cls.move_reason(ope_type, form)
            target = cls._merge_or_add(branch_id, record, loc, qty, ope_user)
            cls._log(target, ope_type, move, loc, loc, qty, record.get("SUB_2"), None, ope_user)
            Location.mark(branch_id, loc, ope_user)
            return target
        if ope_type in DECREASE_TYPES:
            qty = cls._qty(form)
            move = cls.move_reason(ope_type, form)
            cls._take(record, qty, ope_user)
            cls._log(record, ope_type, move, record["LOC_ID"], record["LOC_ID"], qty, record.get("SUB_2"), None, ope_user)
            Location.mark(branch_id, record["LOC_ID"], ope_user)
            return record
        if ope_type == OPE_STATUS:
            new_status = form.get("NEW_STATUS")
            if new_status not in INV_STATUSES or new_status == record["INV_STATUS"]:
                raise ValidationError("errInvStatus")
            qty = cls._qty(form) if form.get("INV_QTY") not in (None, "") else record["INV_QTY"]
            cls._take(record, qty, ope_user)
            target = cls._merge_or_add(branch_id, dict(record, INV_STATUS=new_status), record["LOC_ID"], qty, ope_user)
            cls._log(record, OPE_STATUS, "Status change", record["LOC_ID"], record["LOC_ID"], qty, record.get("SUB_2"), None,
                     ope_user, new_status=new_status)
            return target
        raise ValidationError("errInvOperation")

    @classmethod
    def transfer(cls, branch_id, form, ope_user):
        """Transfer Stock Location: moves qty of one record to another location (same item)."""
        record = cls._record(branch_id, form.get("INV_ID"))
        to_loc = Location.clean(form.get("TO_LOC"))
        if to_loc == record["LOC_ID"]:
            raise ValidationError("transferSameLocation")
        qty = cls._qty(form)
        if qty > record["INV_QTY"]:
            raise ValidationError("transferInsufficientQty")
        Location.check_usable(branch_id, to_loc)
        cls._take(record, qty, ope_user)
        target = cls._merge_or_add(branch_id, record, to_loc, qty, ope_user)
        cls._log(record, OPE_TRANSFER, "Transfer", record["LOC_ID"], to_loc, qty, record.get("SUB_2"), None, ope_user)
        Location.mark(branch_id, record["LOC_ID"], ope_user)
        Location.mark(branch_id, to_loc, ope_user)
        return target

    # ---------- history and dashboard ----------
    @classmethod
    def history(cls, branch_id, limit=500, newest_first=True):
        cursor = col(cls.LOG).find({"BRANCH_ID": branch_id}, NO_ID).sort([("LOG_DATE", -1 if newest_first else 1)]).limit(limit)
        return list(cursor)

    @classmethod
    def movements_since(cls, branch_id, since_utc):
        return list(col(cls.LOG).find({"BRANCH_ID": branch_id, "LOG_DATE": {"$gte": since_utc}}, NO_ID))
