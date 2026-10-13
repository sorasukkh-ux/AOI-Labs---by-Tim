"""AOITS_ORDBUYSELL: buying and selling orders, shown on Update Buy/Sell Status.

Three kinds of order, one record per material:
- Buying (BY), registered on Customer Service > Buy: the mill buys from the customer at the current buying price.
  Prepared = the goods are received into the mill's own stock (inbound pop-up). Closed posts the expense.
- Selling (SL), registered on Customer Service > Sell: the mill sells at the current selling price.
  Prepared = the goods are made ready (no stock moves). Closed releases the quantity from the mill's own stock
  (first in, first out) and posts the income.
- Selling (SL) created by a milling order for the bran/broken rice the customer takes home (REF_ORD_TYPE ML).
  Prepared = the goods are received into a location kept for the customer; Closed releases them and posts the income.

Status flow: Open -> Prepared -> Unpaid -> Closed; Open (and Prepared, except a received buying order) -> Cancelled.
Every change is also written to AOILOG_ORDBUYSELL.
"""
from .account import Account
from .base import col, NO_ID, ValidationError, audit_new, audit_update, round_half_up, to_number
from .counter import Counter
from .inventory import Inventory, MOVE_BUYING, MOVE_ORDER_CLOSE, MOVE_ORDER_PREP, MOVE_SELLING, lot_of
from .price import MAT_BRAN, MAT_BROKEN, MAT_PADDY, MAT_RICE, Price
from .rules import status_flow as mill_flow
from ..timeutil import now_local, now_utc

BUY, SELL = "BY", "SL"
OPEN, PREPARED, UNPAID, CLOSED, CANCELLED = "O", "P", "U", "C", "CC"
END_STATUSES = (CLOSED, CANCELLED)
FLOW = {OPEN: [PREPARED, CANCELLED], PREPARED: [UNPAID, CLOSED, CANCELLED], UNPAID: [CLOSED], CLOSED: [], CANCELLED: []}
LIST_RANK = {s: i for i, s in enumerate((OPEN, PREPARED, UNPAID, CLOSED, CANCELLED))}
MILL_READY = (mill_flow.FINISHED, mill_flow.DELIVERY, mill_flow.CLOSED)   # the milling output exists
from . import variety
from .variety import TRADED as TRADE_MATERIALS       # the rows of the Buy and Sell tables: paddy, rice, broken per variety, bran


class BuySellOrder:
    COL = "AOITS_ORDBUYSELL"
    LOG = "AOILOG_ORDBUYSELL"

    # ---------- reads ----------
    @classmethod
    def find(cls, ord_id, branch_id=None):
        flt = {"ORD_ID": (ord_id or "").strip().upper()}
        if branch_id:
            flt["BRANCH_ID"] = branch_id
        return col(cls.COL).find_one(flt, NO_ID)

    @classmethod
    def for_branch(cls, branch_id, statuses=None, ord_type=None):
        flt = {"BRANCH_ID": branch_id}
        if statuses:
            flt["ORD_STATUS"] = {"$in": list(statuses)}
        if ord_type:
            flt["ORD_TYPE"] = ord_type
        return list(col(cls.COL).find(flt, NO_ID).sort([("REGIST_DATE", -1)]))

    @classmethod
    def for_milling(cls, milling_ord_id):
        return list(col(cls.COL).find({"REF_ORD_ID": milling_ord_id, "REF_ORD_TYPE": "ML"}, NO_ID).sort([("ORD_ID", 1)]))

    @staticmethod
    def is_linked(order):
        return order.get("REF_ORD_TYPE") == "ML"

    @classmethod
    def allowed_next(cls, order):
        nxt = list(FLOW.get(order["ORD_STATUS"], []))
        if order["ORD_TYPE"] == BUY and order["ORD_STATUS"] == PREPARED:
            nxt.remove(CANCELLED)                    # the goods are already in stock
        return nxt

    @staticmethod
    def is_open(order):
        return order["ORD_STATUS"] not in END_STATUSES

    @staticmethod
    def sort_for_list(orders):
        """Open first, then Prepared, Unpaid, Closed, Cancelled; newest first inside a status."""
        return sorted(orders, key=lambda o: (LIST_RANK.get(o["ORD_STATUS"], 9), -o["REGIST_DATE"].timestamp()))

    # ---------- creation ----------
    @classmethod
    def _new_ids(cls, ord_type):
        today = now_local()
        day = "%04d%02d%02d" % (today.year, today.month, today.day)
        seq = Counter.next("BS-" + day)                  # one daily number for buying and selling orders
        if seq > 9999:
            raise ValidationError("errDailyOrderLimit")
        return "%s-%s-%04d" % (ord_type, day, seq), "%04d" % seq

    @classmethod
    def _log(cls, ord_id):
        order = cls.find(ord_id)
        entry = {k: v for k, v in order.items() if k not in ("REGIST_DATE", "LAST_UPDATE")}
        entry["LOG_DATE"] = now_utc()
        col(cls.LOG).insert_one(entry)

    @staticmethod
    def _take_home(milling):
        """(SKU, quantity field, amount field) of the by-products a milling order's customer can take home."""
        return ((MAT_BRAN, "BRAN_ORDER", "EST_PAY_BRAN"),
                (variety.code(milling.get("RICE_VARIETY"), variety.BROKEN), "BROKE_ORDER", "EST_PAY_BROKE"))

    @classmethod
    def create_for_milling(cls, milling, ope_user):
        """One selling order per by-product the customer takes home (BRAN_ORDER, BROKE_ORDER). Returns the new IDs."""
        created = []
        for mat_code, qty_field, pay_field in cls._take_home(milling):
            qty = milling.get(qty_field) or 0
            if qty <= 0:
                continue
            amount = round_half_up(milling.get(pay_field) or 0, 2)
            ord_id, qr_id = cls._new_ids(SELL)
            order = {"ORD_ID": ord_id, "BRANCH_ID": milling["BRANCH_ID"], "ORD_TYPE": SELL, "CUST_CODE": milling["CUST_CODE"],
                     "ORD_STATUS": OPEN, "QR_ID": qr_id, "MAT_CODE": mat_code, "ORD_QTY": qty, "UOM_CODE": "KG",
                     "REMARK": None, "PRICING_UINT": round_half_up(amount / qty, 3), "ORD_AMOUNT": amount,
                     "REF_ORD_ID": milling["ORD_ID"], "REF_ORD_TYPE": "ML", "REF_QR_PRINT": None, "REF_INV_LOC": None}
            order.update(audit_new(ope_user))
            col(cls.COL).insert_one(dict(order))
            cls._log(ord_id)
            created.append(ord_id)
        return created

    # ---------- changes ----------
    @classmethod
    def _save(cls, order, values, ope_user):
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"ORD_ID": order["ORD_ID"]}, {"$set": values})
        cls._log(order["ORD_ID"])
        return cls.find(order["ORD_ID"])

    @staticmethod
    def amount(order):
        if order.get("ORD_AMOUNT") is not None:
            return round_half_up(order["ORD_AMOUNT"], 2)
        return round_half_up((order.get("ORD_QTY") or 0) * (order.get("PRICING_UINT") or 0), 2)

    @classmethod
    def change_status(cls, ord_id, branch_id, new_status, form, ope_user, describe):
        """`describe(order)`: the ledger text for the order, in the user's language."""
        order = cls.find(ord_id, branch_id)
        if not order:
            raise ValidationError("errOrderNotFound")
        if not cls.is_open(order):
            raise ValidationError("errOrderEnded")
        if new_status not in cls.allowed_next(order):
            raise ValidationError("errStatusNotAllowed")
        values = {"ORD_STATUS": new_status}
        if new_status == PREPARED:
            loc = cls._prepare(order, form, ope_user)
            if loc:
                values["REF_INV_LOC"] = loc
        updated = cls._save(order, values, ope_user)
        if new_status == CLOSED:
            cls._on_close(updated, ope_user, describe)
        return updated

    @classmethod
    def _on_close(cls, order, ope_user, describe):
        """Closed: stock leaves (selling orders) and the ledger entry is posted."""
        if cls.is_linked(order):
            Inventory.release(order["BRANCH_ID"], order["ORD_ID"], order["ORD_TYPE"], MOVE_ORDER_CLOSE, ope_user)
        elif order["ORD_TYPE"] == SELL:
            Inventory.release_free(order["BRANCH_ID"], order["MAT_CODE"], order["ORD_QTY"], order["ORD_ID"], SELL,
                                   MOVE_SELLING, ope_user)
        Account.post_order(order, cls.amount(order), "EX" if order["ORD_TYPE"] == BUY else "IN",
                           describe(order), ope_user)

    @staticmethod
    def needs_inbound(order):
        """Prepared opens the inbound pop-up for a buying order and for take-home goods of a milling order."""
        return order["ORD_TYPE"] == BUY or order.get("REF_ORD_TYPE") == "ML"

    # ---------- registration on Customer Service > Buy / Sell ----------
    @classmethod
    def parse_items(cls, branch_id, ord_type, form):
        """Quantities typed in the Buy or Sell table (QTY_<MAT_CODE>), each with the branch's current price."""
        items = []
        for mat_code in TRADE_MATERIALS:
            try:
                qty = to_number(form.get("QTY_" + mat_code), 0)
            except ValueError:
                raise ValidationError("errNumber")
            if qty < 0:
                raise ValidationError("errNegative")
            if qty == 0:
                continue
            price = Price.buy_price(branch_id, mat_code) if ord_type == BUY else Price.sell_price(branch_id, mat_code)
            if price <= 0:
                raise ValidationError("errNoPrice", mat=mat_code)
            qty = round_half_up(qty, 3)
            items.append({"mat": mat_code, "qty": qty, "price": round_half_up(price, 3), "amount": round_half_up(qty * price, 2)})
        if not items:
            raise ValidationError("errNoItems")
        return items

    @classmethod
    def register(cls, branch_id, ord_type, cust_code, form, close, ope_user, describe):
        """Open: one Open order per material. Close (the design's "Close" button): the orders are completed at once -
        a buying order receives the goods into the location given (LOC_ID) and posts the expense, a selling order
        releases the goods from stock and posts the income. Returns the orders."""
        from .customer import Customer
        if ord_type not in (BUY, SELL):
            raise ValidationError("errStatusNotAllowed")
        customer = Customer.find(cust_code)
        if not customer:
            raise ValidationError("alertCustInfoRequired")
        items = cls.parse_items(branch_id, ord_type, form)
        inbound = None
        if close and ord_type == BUY:                        # checked before anything is saved
            inbound = Inventory.parse_inbound(form, 1)
            from .inventory import Location
            Location.check_usable(branch_id, inbound["loc"])
        orders = []
        for item in items:
            ord_id, qr_id = cls._new_ids(ord_type)
            order = {"ORD_ID": ord_id, "BRANCH_ID": branch_id, "ORD_TYPE": ord_type, "CUST_CODE": customer["CUST_CODE"],
                     "ORD_STATUS": OPEN, "QR_ID": qr_id, "MAT_CODE": item["mat"], "ORD_QTY": item["qty"], "UOM_CODE": "KG",
                     "REMARK": None, "PRICING_UINT": item["price"], "ORD_AMOUNT": item["amount"],
                     "REF_ORD_ID": None, "REF_ORD_TYPE": None, "REF_QR_PRINT": None, "REF_INV_LOC": None}
            order.update(audit_new(ope_user))
            col(cls.COL).insert_one(dict(order))
            cls._log(ord_id)
            if close:
                values = {"ORD_STATUS": CLOSED}
                if ord_type == BUY:
                    received = dict(inbound, qty=item["qty"])
                    Inventory.inbound(branch_id, item["mat"], received, lot_of(ord_id), ord_id, BUY, MOVE_BUYING, ope_user,
                                      reserve=False)
                    values["REF_INV_LOC"] = inbound["loc"]
                closed = cls._save(order, values, ope_user)
                cls._on_close(closed, ope_user, describe)
            orders.append(cls.find(ord_id))
        return orders

    @classmethod
    def stock_check(cls, branch_id, items):
        """For the Sell confirmation: what each material has in the mill's own stock, and where."""
        out = []
        for item in items:
            stock = Inventory.free_stock(branch_id, item["mat"])
            available = round_half_up(sum(r["INV_QTY"] for r in stock), 3)
            out.append(dict(item, available=available, short=available < item["qty"],
                            locations=[(r["LOC_ID"], r["INV_QTY"]) for r in stock]))
        return out

    @classmethod
    def _prepare(cls, order, form, ope_user):
        """Prepared. Buying order: the goods are received into the mill's own stock. Take-home goods of a milling
        order: stored in a location kept for the customer. Direct selling order: no stock moves (released at Closed)."""
        if order["ORD_TYPE"] == BUY:
            inbound = Inventory.parse_inbound(form, order["ORD_QTY"])
            Inventory.inbound(order["BRANCH_ID"], order["MAT_CODE"], inbound, lot_of(order["ORD_ID"]), order["ORD_ID"], BUY,
                              MOVE_BUYING, ope_user, reserve=False)
            return inbound["loc"]
        if not cls.is_linked(order):
            return None
        lot = None
        if order.get("REF_ORD_TYPE") == "ML":
            from .milling_order import MillingOrder              # imported here: milling orders also create these orders
            milling = MillingOrder.find(order["REF_ORD_ID"])
            if not milling or milling["MILL_STATUS"] not in MILL_READY:
                raise ValidationError("errMillingNotFinished", id=(milling or {}).get("QR_ID", order["REF_ORD_ID"]))
            lot = lot_of(milling["ORD_ID"])
        inbound = Inventory.parse_inbound(form, order["ORD_QTY"])
        Inventory.inbound(order["BRANCH_ID"], order["MAT_CODE"], inbound, lot, order["ORD_ID"], order["ORD_TYPE"],
                          MOVE_ORDER_PREP, ope_user)
        return inbound["loc"]

    @classmethod
    def sync_for_milling(cls, milling, ope_user):
        """The milling order's details were edited: its take-home orders follow the new customer and quantities.
        A quantity can only change while the order is still Open. Returns the IDs of the orders still linked."""
        # matched by kind: when the milling order's variety changes, its broken rice order changes SKU too
        current = {variety.kind_of(o["MAT_CODE"]): o for o in cls.for_milling(milling["ORD_ID"]) if o["ORD_STATUS"] != CANCELLED}
        plan = []
        for mat_code, qty_field, pay_field in cls._take_home(milling):
            qty = milling.get(qty_field) or 0
            amount = round_half_up(milling.get(pay_field) or 0, 2)
            existing = current.get(variety.kind_of(mat_code))
            if existing and (existing["ORD_QTY"] != qty or BuySellOrder.amount(existing) != amount
                             or existing["MAT_CODE"] != mat_code) and existing["ORD_STATUS"] != OPEN:
                raise ValidationError("errLinkedNotOpen", id=existing["QR_ID"])
            plan.append((mat_code, qty, amount, existing))
        linked = []
        for mat_code, qty, amount, existing in plan:          # checked everything first, now change
            if existing is None:
                if qty > 0:
                    linked += cls.create_for_milling(dict(milling, **{"BRAN_ORDER": qty if mat_code == MAT_BRAN else 0,
                                                                      "BROKE_ORDER": qty if mat_code != MAT_BRAN else 0}), ope_user)
                continue
            if qty <= 0:
                cls._save(existing, {"ORD_STATUS": CANCELLED}, ope_user)
                continue
            values = {}
            if existing["CUST_CODE"] != milling["CUST_CODE"]:
                values["CUST_CODE"] = milling["CUST_CODE"]
            if existing["MAT_CODE"] != mat_code:
                values["MAT_CODE"] = mat_code
            if existing["ORD_QTY"] != qty or cls.amount(existing) != amount:
                values.update({"ORD_QTY": qty, "ORD_AMOUNT": amount, "PRICING_UINT": round_half_up(amount / qty, 3)})
            if values:
                cls._save(existing, values, ope_user)
            linked.append(existing["ORD_ID"])
        return sorted(linked)

    @classmethod
    def cancel_for_milling(cls, milling_ord_id, ope_user):
        """The milling order was cancelled: its open take-home orders are cancelled too. Stock is not moved."""
        for order in cls.for_milling(milling_ord_id):
            if cls.is_open(order) and order["ORD_STATUS"] != UNPAID:
                cls._save(order, {"ORD_STATUS": CANCELLED}, ope_user)
