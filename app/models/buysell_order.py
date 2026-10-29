"""AOITS_ORDBUYSELL: buying and selling orders, shown on Update Buy/Sell Status.

Three kinds of order, one record per material:
- Buying (BY), registered on Customer Service > Buy: the mill buys from the customer at the current buying price.
  Prepared = the goods are received into the mill's own stock (inbound pop-up). Closed posts the expense.
- Selling (SL), registered on Customer Service > Sell: the mill sells at the current selling price.
  Prepared = the goods are made ready (no stock moves). Closed releases the quantity from the mill's own stock
  (first in, first out) and posts the income.
- Selling (SL) created by a milling order for the bran/broken rice the customer takes home (REF_ORD_TYPE ML).
  Update 39: ONE order per milling order with a line per SKU (ORD_ITEMS: bran, broken rice of the variety); the
  header MAT_CODE / ORD_QTY / ORD_AMOUNT are the first line / totals. The lines can be maintained (quantities) until
  the order is Unpaid or Closed; the milling order's take-home quantities and charges follow.
  Prepared = the goods are received into a location kept for the customer; Closed releases them and posts the income.

Status flow: Open -> Prepared -> Unpaid -> Closed; Open (and Prepared, except a received buying order) -> Cancelled.
Every change is also written to AOILOG_ORDBUYSELL.
"""
import re
from .account import Account
from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, round_half_up, to_number
from .counter import Counter
from .inventory import Inventory, MOVE_BUYING, MOVE_ORDER_CLOSE, MOVE_ORDER_PREP, MOVE_SELLING, lot_of
MOVE_ORDER_EDIT = "Order changed"
from .price import MAT_BRAN, MAT_BROKEN, MAT_PADDY, MAT_RICE, Price
from .rules import status_flow as mill_flow
from ..timeutil import now_local, now_utc

BUY, SELL = "BY", "SL"
OPEN, PREPARED, UNPAID, CLOSED, CANCELLED = "O", "P", "U", "C", "CC"
PRICE_MIN, PRICE_MAX = 0.01, 1000.0                      # update 78: a price typed by hand, THB/kg
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

    @staticmethod
    def milling_id(order):
        """Update 68: the milling order this order goes with - its take-home order, or linked by hand (LINK_ML_ID)."""
        if order.get("REF_ORD_TYPE") == "ML" and order.get("REF_ORD_ID"):
            return order["REF_ORD_ID"]
        return order.get("LINK_ML_ID") or None

    @classmethod
    def waits_for_milling(cls, order):
        """Update 68 (Tim): goods of an order linked to a milling order leave the stock when the milling order is
        Closed (picked up with the rice), not when this order is Closed (paid). The milling order, when still open."""
        from .milling_order import MillingOrder
        ref = cls.milling_id(order)
        milling = MillingOrder.find(ref) if ref else None
        return milling if milling and milling.get("MILL_STATUS") not in ("C", "CC") else None

    @classmethod
    def pending_with_milling(cls, milling, ope_user):
        """Update 72: the milling order was Cancelled - goods kept for its linked orders become the mill's stock,
        Pending maintenance."""
        out = []
        for o in cls.for_milling(milling["ORD_ID"]) + cls.linked_by_hand(milling["ORD_ID"]):
            out += Inventory.release_to_mill(o["BRANCH_ID"], o["ORD_ID"], o["ORD_TYPE"], ope_user, "Milling order cancelled")
        return out

    @classmethod
    def release_with_milling(cls, milling, ope_user):
        """Update 68: the milling order was Closed (or Cancelled) - the goods kept for its closed linked orders leave."""
        released = []
        for o in cls.for_milling(milling["ORD_ID"]) + cls.linked_by_hand(milling["ORD_ID"]):
            if o["ORD_STATUS"] != CLOSED:
                continue
            move = MOVE_ORDER_CLOSE if cls.is_linked(o) else MOVE_SELLING
            released += Inventory.release(o["BRANCH_ID"], o["ORD_ID"], o["ORD_TYPE"], move, ope_user)
        return released

    # ---------- update 56: a customer buying order (SL) linked by hand to a milling order ----------
    @staticmethod
    def resolve_milling(branch_id, text, cust_code):
        """The milling order typed or scanned (ML-YYYYMMDD-NNNN or the label AOI-ML-...); None when empty.
        It must be of this branch and this customer."""
        from .milling_order import MillingOrder
        code = re.sub(r"\s+", "", text or "").upper()
        if not code:
            return None
        if code.startswith("AOI-"):
            code = code[4:]
        milling = MillingOrder.find(code, branch_id)
        if not milling:
            raise ValidationError("errLinkMlNotFound", id=code)
        if milling["CUST_CODE"] != cust_code:
            raise ValidationError("errLinkMlOtherCustomer", id=milling["QR_ID"])
        return milling

    @classmethod
    def link_milling(cls, ord_id, branch_id, text, ope_user):
        order = cls.find(ord_id, branch_id)
        if not order:
            raise ValidationError("errOrderNotFound")
        if order["ORD_TYPE"] != SELL or cls.is_linked(order) or not cls.is_open(order):
            raise ValidationError("errStatusNotAllowed")
        milling = cls.resolve_milling(branch_id, text, order["CUST_CODE"])
        return cls._save(order, {"LINK_ML_ID": milling["ORD_ID"] if milling else None}, ope_user)

    @classmethod
    def linked_by_hand(cls, milling_ord_id):
        return list(col(cls.COL).find({"LINK_ML_ID": milling_ord_id}, NO_ID).sort([("ORD_ID", 1)]))

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
        """(SKU, quantity field, amount field, expected output field) of the by-products a customer can take home."""
        return ((MAT_BRAN, "BRAN_ORDER", "EST_PAY_BRAN", "EXPCT_BRAN"),
                (variety.code(milling.get("RICE_VARIETY"), variety.BROKEN), "BROKE_ORDER", "EST_PAY_BROKE", "EXPCT_BROKE"))

    # ---------- lines (update 39) ----------
    @staticmethod
    def items_of(order):
        """The order's lines. Orders made before update 39 (and Buy / Sell registrations) have one material:
        a single line is made from the header."""
        if order.get("ORD_ITEMS"):
            return order["ORD_ITEMS"]
        return [{"LINE_NO": 1, "MAT_CODE": order["MAT_CODE"], "ORD_QTY": order.get("ORD_QTY") or 0, "UOM_CODE": order.get("UOM_CODE") or "KG",
                 "PRICING_UINT": order.get("PRICING_UINT") or 0, "ORD_AMOUNT": BuySellOrder.amount(order),
                 "UNIT_PRICE": order.get("PRICING_UINT") or 0, "OWN_KG": 0, "DISC_PCT": 0}]

    @staticmethod
    def _line_amount(line, qty):
        """What the customer pays for qty of a take-home line: the milling discount up to the customer's own output."""
        from .rules.charges import _take
        return _take(qty, line.get("OWN_KG") or 0, line.get("UNIT_PRICE") or 0, line.get("DISC_PCT") or 0)["amount"]

    @staticmethod
    def _header(lines):
        """Header fields that sum the lines (MAT_CODE: the first line with a quantity)."""
        qty = round_half_up(sum(l["ORD_QTY"] for l in lines), 3)
        amount = round_half_up(sum(l["ORD_AMOUNT"] for l in lines), 2)
        first = next((l for l in lines if l["ORD_QTY"] > 0), lines[0])
        return {"ORD_ITEMS": lines, "MAT_CODE": first["MAT_CODE"], "ORD_QTY": qty, "ORD_AMOUNT": amount,
                "PRICING_UINT": round_half_up(amount / qty, 3) if qty else 0}

    @classmethod
    def _milling_lines(cls, milling):
        """Take-home lines from the milling order: quantity and amount as the milling order charged them, with the
        price, discount and own output kept so a changed quantity is charged the same way."""
        from .price import Promotion
        from ..timeutil import today_local
        disc = Promotion.milling_discount_pct(milling["BRANCH_ID"], today_local())
        lines = []
        for n, (mat_code, qty_field, pay_field, own_field) in enumerate(cls._take_home(milling)):
            qty = round_half_up(milling.get(qty_field) or 0, 3)
            amount = round_half_up(milling.get(pay_field) or 0, 2)
            lines.append({"LINE_NO": n + 1, "MAT_CODE": mat_code, "ORD_QTY": qty, "UOM_CODE": "KG",
                          "PRICING_UINT": round_half_up(amount / qty, 3) if qty else 0, "ORD_AMOUNT": amount,
                          "UNIT_PRICE": round_half_up(Price.sell_price(milling["BRANCH_ID"], mat_code), 3),
                          "OWN_KG": round_half_up(milling.get(own_field) or 0, 3), "DISC_PCT": disc, "REF_INV_LOC": None})
        return lines

    @classmethod
    def create_for_milling(cls, milling, ope_user):
        """One selling order for everything the customer takes home (BRAN_ORDER, BROKE_ORDER), a line per SKU.
        Returns [its ID], or [] when nothing is taken home."""
        lines = cls._milling_lines(milling)
        if not any(l["ORD_QTY"] > 0 for l in lines):
            return []
        ord_id, qr_id = cls._new_ids(SELL)
        order = {"ORD_ID": ord_id, "BRANCH_ID": milling["BRANCH_ID"], "ORD_TYPE": SELL, "CUST_CODE": milling["CUST_CODE"],
                 "ORD_STATUS": OPEN, "QR_ID": qr_id, "UOM_CODE": "KG", "REMARK": None,
                 "REF_ORD_ID": milling["ORD_ID"], "REF_ORD_TYPE": "ML", "REF_QR_PRINT": None, "REF_INV_LOC": None}
        order.update(cls._header(lines))
        order.update(audit_new(ope_user))
        col(cls.COL).insert_one(dict(order))
        cls._log(ord_id)
        return [ord_id]

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
        elif new_status == CANCELLED and order["ORD_TYPE"] == SELL and not cls.is_linked(order):
            Inventory.unreserve(order["BRANCH_ID"], order["ORD_ID"], SELL, ope_user)   # update 56: back to the mill's stock
        elif new_status == CANCELLED and cls.is_linked(order):
            # update 72: take-home bran / broken rice already received - the mill's stock, Pending maintenance
            Inventory.release_to_mill(order["BRANCH_ID"], order["ORD_ID"], order["ORD_TYPE"], ope_user)
        return updated

    @classmethod
    def _on_close(cls, order, ope_user, describe):
        """Closed: stock leaves (selling orders) and the ledger entry is posted. Update 68: an order linked to a
        milling order that is still open keeps its goods in stock until that milling order is Closed."""
        if order["ORD_TYPE"] == SELL and cls.waits_for_milling(order) and Inventory.for_order(order["BRANCH_ID"], order["ORD_ID"]):
            pass                                             # paid now, handed over with the rice
        elif cls.is_linked(order):
            Inventory.release(order["BRANCH_ID"], order["ORD_ID"], order["ORD_TYPE"], MOVE_ORDER_CLOSE, ope_user)
        elif order["ORD_TYPE"] == SELL and Inventory.for_order(order["BRANCH_ID"], order["ORD_ID"]):
            # update 56: picked at Prepared (Inventory Outbound) - the kept goods leave now
            Inventory.release(order["BRANCH_ID"], order["ORD_ID"], SELL, MOVE_SELLING, ope_user)
        elif order["ORD_TYPE"] == SELL:
            for line in cls.items_of(order):                   # update 74: every line
                if line["ORD_QTY"] > 0:
                    Inventory.release_free(order["BRANCH_ID"], line["MAT_CODE"], line["ORD_QTY"], order["ORD_ID"], SELL,
                                           MOVE_SELLING, ope_user)
        Account.post_order(order, cls.amount(order), "EX" if order["ORD_TYPE"] == BUY else "IN",
                           describe(order), ope_user)

    @staticmethod
    def needs_inbound(order):
        """Prepared opens the inbound pop-up for a buying order and for take-home goods of a milling order."""
        return order["ORD_TYPE"] == BUY or order.get("REF_ORD_TYPE") == "ML"

    # ---------- registration on Customer Service > Buy / Sell ----------
    @classmethod
    def parse_items(cls, branch_id, ord_type, form, can_override=False):
        """Quantities typed in the Buy or Sell table (QTY_<MAT_CODE>), each with the branch's current price.
        Update 78 (Tim): a user with the Approve right may type another price for the order (PRICE_<MAT_CODE>, THB/kg)
        with a reason (PRICE_REASON); a changed price without that right is refused. Each item keeps the branch
        price (list_price) and whether it was changed (override)."""
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
            list_price = round_half_up(Price.buy_price(branch_id, mat_code) if ord_type == BUY else Price.sell_price(branch_id, mat_code), 3)
            price, override = list_price, False
            typed = (form.get("PRICE_" + mat_code) or "").strip()
            if typed:
                try:
                    value = round_half_up(float(typed.replace(",", "")), 3)
                except ValueError:
                    raise ValidationError("errNumber")
                if abs(value - list_price) >= 0.0005:
                    if not can_override:
                        raise ValidationError("errPriceRight")
                    if not (PRICE_MIN <= value <= PRICE_MAX):
                        raise ValidationError("errOrderPriceRange", lo="%.2f" % PRICE_MIN, hi="%.0f" % PRICE_MAX)
                    price, override = value, True
            if price <= 0:
                raise ValidationError("errNoPrice", mat=mat_code)
            qty = round_half_up(qty, 3)
            items.append({"mat": mat_code, "qty": qty, "price": price, "amount": round_half_up(qty * price, 2),
                          "list_price": list_price, "override": override})
        if not items:
            raise ValidationError("errNoItems")
        if any(i["override"] for i in items) and not clean_text(form.get("PRICE_REASON"), 200):
            raise ValidationError("errPriceReason")
        return items

    @staticmethod
    def override_fields(items, form, ope_user):
        """Header fields of an order whose price was typed by hand (update 78)."""
        if not any(i.get("override") for i in items):
            return {"PRICE_OVR_FLG": False}
        return {"PRICE_OVR_FLG": True, "PRICE_OVR_USER": ope_user, "PRICE_OVR_DATE": now_utc(),
                "PRICE_OVR_REASON": clean_text(form.get("PRICE_REASON"), 200)}

    @classmethod
    def change_prices(cls, ord_id, branch_id, form, ope_user):
        """Update 78: new prices for the lines of an order that is not closed yet (Open, Prepared, Unpaid; not a
        milling order's take-home order, whose price follows the milling charges). PRICE_<MAT_CODE> per line and
        PRICE_REASON. The amounts and the total follow; the ledger takes the new total at Closed."""
        order = cls.find(ord_id, branch_id)
        if not order:
            raise ValidationError("errOrderNotFound")
        if order["ORD_STATUS"] not in (OPEN, PREPARED, UNPAID) or cls.is_linked(order):
            raise ValidationError("errPriceNotNow")
        reason = clean_text(form.get("PRICE_REASON"), 200)
        if not reason:
            raise ValidationError("errPriceReason")
        lines, changed = [], False
        for line in cls.items_of(order):
            new = dict(line)
            typed = (form.get("PRICE_" + line["MAT_CODE"]) or "").strip()
            if typed:
                try:
                    value = round_half_up(float(typed.replace(",", "")), 3)
                except ValueError:
                    raise ValidationError("errNumber")
                if not (PRICE_MIN <= value <= PRICE_MAX):
                    raise ValidationError("errOrderPriceRange", lo="%.2f" % PRICE_MIN, hi="%.0f" % PRICE_MAX)
                if abs(value - float(line.get("PRICING_UINT") or 0)) >= 0.0005:
                    new.setdefault("LIST_PRICE", line.get("PRICING_UINT"))
                    new.update({"PRICING_UINT": value, "UNIT_PRICE": value, "ORD_AMOUNT": round_half_up(line["ORD_QTY"] * value, 2),
                                "PRICE_OVR_FLG": True})
                    changed = True
            lines.append(new)
        if not changed:
            raise ValidationError("errPriceSame")
        values = cls._header(lines)
        values.update({"PRICE_OVR_FLG": True, "PRICE_OVR_USER": ope_user, "PRICE_OVR_DATE": now_utc(), "PRICE_OVR_REASON": reason})
        return cls._save(order, values, ope_user)

    @classmethod
    def register(cls, branch_id, ord_type, cust_code, form, close, ope_user, describe, can_override=False):
        """Open: one Open order with a line per material (update 74; before: one order per material). Close (the design's "Close" button): the orders are completed at once -
        a buying order receives the goods into the location given (LOC_ID) and posts the expense, a selling order
        releases the goods from stock and posts the income. Returns the orders."""
        from .customer import Customer
        if ord_type not in (BUY, SELL):
            raise ValidationError("errStatusNotAllowed")
        customer = Customer.find(cust_code)
        if not customer:
            raise ValidationError("alertCustInfoRequired")
        items = cls.parse_items(branch_id, ord_type, form, can_override)
        link = cls.resolve_milling(branch_id, form.get("LINK_ML"), customer["CUST_CODE"]) if ord_type == SELL else None
        inbound = None
        if close and ord_type == BUY:                        # checked before anything is saved
            inbound = Inventory.parse_inbound(form, 1)
            from .inventory import Location
            Location.check_usable(branch_id, inbound["loc"])
        # update 74 (Tim): everything registered together for one customer is ONE order with a line per SKU
        lines = [{"LINE_NO": n + 1, "MAT_CODE": item["mat"], "ORD_QTY": item["qty"], "UOM_CODE": "KG",
                  "PRICING_UINT": item["price"], "ORD_AMOUNT": item["amount"], "UNIT_PRICE": item["price"],
                  "OWN_KG": 0, "DISC_PCT": 0, "REF_INV_LOC": None, "LIST_PRICE": item["list_price"],
                  "PRICE_OVR_FLG": item["override"]} for n, item in enumerate(items)]
        ord_id, qr_id = cls._new_ids(ord_type)
        order = {"ORD_ID": ord_id, "BRANCH_ID": branch_id, "ORD_TYPE": ord_type, "CUST_CODE": customer["CUST_CODE"],
                 "ORD_STATUS": OPEN, "QR_ID": qr_id, "UOM_CODE": "KG", "REMARK": None,
                 "REF_ORD_ID": None, "REF_ORD_TYPE": None, "REF_QR_PRINT": None, "REF_INV_LOC": None,
                 "LINK_ML_ID": link["ORD_ID"] if link else None}
        order.update(cls._header(lines))
        order.update(cls.override_fields(items, form, ope_user))       # update 78: a price typed by hand
        order.update(audit_new(ope_user))
        col(cls.COL).insert_one(dict(order))
        cls._log(ord_id)
        if close:
            values = {"ORD_STATUS": CLOSED}
            if ord_type == BUY:
                for line in lines:
                    Inventory.inbound(branch_id, line["MAT_CODE"], dict(inbound, qty=line["ORD_QTY"]), lot_of(ord_id), ord_id, BUY,
                                      MOVE_BUYING, ope_user, reserve=False)
                values["REF_INV_LOC"] = inbound["loc"]
            closed = cls._save(order, values, ope_user)
            cls._on_close(closed, ope_user, describe)
        return [cls.find(ord_id)]

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
        order: stored in a location kept for the customer. Customer buying order: taken from stock and kept."""
        if order["ORD_TYPE"] == SELL and not cls.is_linked(order):
            # update 56: a customer buying order - Inventory Outbound: the goods are taken from the mill's stock
            # (the location chosen, or oldest first) and kept for the order until it is closed. Update 74: per line,
            # all lines checked before anything is kept.
            lines = [l for l in cls.items_of(order) if l["ORD_QTY"] > 0]
            locs = {l["MAT_CODE"]: ((form or {}).get("OUT_LOC_" + l["MAT_CODE"]) or (form or {}).get("OUT_LOC") or None) for l in lines}
            for l in lines:
                have = round_half_up(sum(r["INV_QTY"] for r in Inventory.free_stock(order["BRANCH_ID"], l["MAT_CODE"])
                                         if not locs[l["MAT_CODE"]] or r["LOC_ID"] == locs[l["MAT_CODE"]]), 3)
                if have < l["ORD_QTY"]:
                    from .variety import mat_name
                    raise ValidationError("errOutboundShortMat", mat=mat_name(l["MAT_CODE"]), need="%.2f" % l["ORD_QTY"],
                                          have="%.2f" % have, loc=locs[l["MAT_CODE"]] or "-")
            first = None
            for l in lines:
                loc = Inventory.reserve_free(order["BRANCH_ID"], l["MAT_CODE"], l["ORD_QTY"], order["ORD_ID"], SELL, locs[l["MAT_CODE"]], ope_user)
                first = first or loc
            return first
        # update 42 (Tim): the buying/selling order is prepared and closed on its own - it does not wait for the
        # milling order's status. The lot still names the milling order it came from.
        # Buying order (update 74: a line per SKU): the goods go into the mill's own stock (not kept for anyone)
        buying = order["ORD_TYPE"] == BUY
        lot = lot_of(order["REF_ORD_ID"]) if order.get("REF_ORD_TYPE") == "ML" and order.get("REF_ORD_ID") else (lot_of(order["ORD_ID"]) if buying else None)
        lines = [l for l in cls.items_of(order) if l["ORD_QTY"] > 0]
        received = []
        for line in lines:                                   # checked first: one location for the customer's goods
            field = "INV_QTY_" + line["MAT_CODE"] if len(lines) > 1 or form.get("INV_QTY_" + line["MAT_CODE"]) else "INV_QTY"
            received.append((line, Inventory.parse_inbound(dict(form.items(), INV_QTY=form.get(field)), line["ORD_QTY"])))
        for line, inbound in received:
            Inventory.inbound(order["BRANCH_ID"], line["MAT_CODE"], inbound, lot, order["ORD_ID"], order["ORD_TYPE"],
                              MOVE_BUYING if buying else MOVE_ORDER_PREP, ope_user, reserve=not buying)
        return received[0][1]["loc"] if received else None

    @classmethod
    def sync_for_milling(cls, milling, ope_user):
        """The milling order's details were edited: its take-home order follows the new customer, variety and
        quantities. A line can only change while its order is still Open. Returns the IDs of the orders still linked."""
        wanted = {variety.kind_of(l["MAT_CODE"]): l for l in cls._milling_lines(milling)}
        orders = [o for o in cls.for_milling(milling["ORD_ID"]) if o["ORD_STATUS"] != CANCELLED]
        placed = {}                                          # kind -> (order, line) where that by-product is now
        for o in orders:
            for line in cls.items_of(o):
                placed.setdefault(variety.kind_of(line["MAT_CODE"]), (o, line))
        for kind, new in wanted.items():                    # check everything first
            if kind in placed:
                o, line = placed[kind]
                if (line["ORD_QTY"], round_half_up(line["ORD_AMOUNT"], 2), line["MAT_CODE"]) != (new["ORD_QTY"], new["ORD_AMOUNT"], new["MAT_CODE"]) \
                        and o["ORD_STATUS"] != OPEN:
                    raise ValidationError("errLinkedNotOpen", id=o["QR_ID"])
        missing = [new for kind, new in wanted.items() if kind not in placed and new["ORD_QTY"] > 0]
        target = next((o for o in orders if o["ORD_STATUS"] == OPEN), None)
        linked = []
        for o in orders:
            lines = []
            for line in cls.items_of(o):
                kind = variety.kind_of(line["MAT_CODE"])
                if placed.get(kind, (None,))[0] is o:
                    new = dict(wanted[kind], LINE_NO=line.get("LINE_NO"), REF_INV_LOC=line.get("REF_INV_LOC"))
                    lines.append(new if o["ORD_STATUS"] == OPEN else line)
                else:
                    lines.append(line)
            if o is target:
                lines += [dict(m, LINE_NO=len(lines) + i + 1) for i, m in enumerate(missing)]
                missing = []
            values = {}
            if not any(l["ORD_QTY"] > 0 for l in lines):
                if o["ORD_STATUS"] == OPEN:
                    cls._save(o, {"ORD_STATUS": CANCELLED}, ope_user)
                continue
            header = cls._header(lines)
            if header["ORD_ITEMS"] != o.get("ORD_ITEMS") or header["ORD_AMOUNT"] != cls.amount(o):
                values.update(header)
            if o["CUST_CODE"] != milling["CUST_CODE"] and o["ORD_STATUS"] == OPEN:
                values["CUST_CODE"] = milling["CUST_CODE"]
            if values:
                cls._save(o, values, ope_user)
            linked.append(o["ORD_ID"])
        if missing:                                          # nothing Open to add the lines to: a new order
            only = dict(milling, BRAN_ORDER=0, BROKE_ORDER=0, EST_PAY_BRAN=0, EST_PAY_BROKE=0)
            only.update(cls._fields_of(milling, missing))
            linked += cls.create_for_milling(only, ope_user)
        return sorted(linked)

    @classmethod
    def _fields_of(cls, milling, lines):
        """Milling quantity / amount fields of the given lines (for a new order with only those lines)."""
        out = {}
        for mat_code, qty_field, pay_field, own_field in cls._take_home(milling):
            for l in lines:
                if variety.kind_of(l["MAT_CODE"]) == variety.kind_of(mat_code):
                    out[qty_field], out[pay_field] = l["ORD_QTY"], l["ORD_AMOUNT"]
        return out

    # ---------- maintaining a take-home order (update 39) ----------
    EDITABLE = (OPEN, PREPARED)

    @classmethod
    def can_maintain(cls, order):
        return cls.is_linked(order) and order["ORD_STATUS"] in cls.EDITABLE

    @classmethod
    def maintain(cls, ord_id, branch_id, form, ope_user):
        """New quantities of the lines (QTY_<MAT_CODE>) of a take-home order, until it is Unpaid / Closed. The amount
        is charged as at registration (milling discount up to the customer's own output). When the goods are already
        Prepared, the stock kept for the customer is corrected too. The milling order's BRAN_ORDER / BROKE_ORDER and
        charges follow."""
        from .inventory import OPE_ADJ_DEC, OPE_ADJ_INC
        order = cls.find(ord_id, branch_id)
        if not order:
            raise ValidationError("errOrderNotFound")
        if not cls.can_maintain(order):
            raise ValidationError("errLinkedNotEditable")
        lines, changes = [], []
        for line in cls.items_of(order):
            try:
                qty = to_number(form.get("QTY_" + line["MAT_CODE"]), line["ORD_QTY"])
            except ValueError:
                raise ValidationError("errNumber")
            if qty is None or qty < 0:
                raise ValidationError("errNegative")
            qty = round_half_up(qty, 3)
            amount = cls._line_amount(line, qty)
            if qty != line["ORD_QTY"]:
                changes.append((line, qty))
            lines.append(dict(line, ORD_QTY=qty, ORD_AMOUNT=amount, PRICING_UINT=round_half_up(amount / qty, 3) if qty else 0))
        if not any(l["ORD_QTY"] > 0 for l in lines):
            raise ValidationError("errLinkedAllZero")
        if not changes:
            return order
        if order["ORD_STATUS"] == PREPARED:                  # the customer's goods in stock follow
            kept = Inventory.for_order(branch_id, order["ORD_ID"])
            for line, qty in changes:                        # checked first: enough kept to take away
                diff = round_half_up(qty - line["ORD_QTY"], 3)
                have = sum(r["INV_QTY"] for r in kept if r["MAT_CODE"] == line["MAT_CODE"])
                if diff < 0 and have + diff < -0.0005:
                    raise ValidationError("errInvNotEnough", loc=order.get("REF_INV_LOC") or "-", qty="%.2f" % have)
            for line, qty in changes:
                diff = round_half_up(qty - line["ORD_QTY"], 3)
                records = [r for r in kept if r["MAT_CODE"] == line["MAT_CODE"]]
                if diff > 0:
                    if records:
                        rec = Inventory._merge_or_add(branch_id, records[0], records[0]["LOC_ID"], diff, ope_user)
                    else:
                        lot = lot_of(order["REF_ORD_ID"])
                        rec = Inventory.inbound(branch_id, line["MAT_CODE"], {"qty": diff, "loc": order.get("REF_INV_LOC") or "",
                                                "status": "A"}, lot, order["ORD_ID"], order["ORD_TYPE"], MOVE_ORDER_PREP, ope_user)
                        continue                             # inbound() wrote its own log
                    Inventory._log(rec, OPE_ADJ_INC, MOVE_ORDER_EDIT, rec["LOC_ID"], rec["LOC_ID"], diff, order["ORD_ID"],
                                   order["ORD_TYPE"], ope_user)
                else:
                    left = -diff
                    for rec in records:
                        take = min(left, rec["INV_QTY"])
                        if take <= 0:
                            continue
                        Inventory._take(rec, take, ope_user)
                        Inventory._log(rec, OPE_ADJ_DEC, MOVE_ORDER_EDIT, rec["LOC_ID"], rec["LOC_ID"], take, order["ORD_ID"],
                                       order["ORD_TYPE"], ope_user)
                        from .inventory import Location
                        Location.mark(branch_id, rec["LOC_ID"], ope_user)
                        left = round_half_up(left - take, 3)
        updated = cls._save(order, cls._header(lines), ope_user)
        cls._milling_follows(updated, ope_user)
        return updated

    @classmethod
    def _milling_follows(cls, order, ope_user):
        """The milling order's take-home quantities and charges = the lines of its take-home orders (not Cancelled)."""
        from .milling_order import MillingOrder
        milling = MillingOrder.find(order["REF_ORD_ID"])
        if not milling:
            return
        totals = {}
        for o in cls.for_milling(milling["ORD_ID"]):
            if o["ORD_STATUS"] == CANCELLED:
                continue
            for line in cls.items_of(o):
                kind = variety.kind_of(line["MAT_CODE"])
                q, a = totals.get(kind, (0.0, 0.0))
                totals[kind] = (q + line["ORD_QTY"], a + line["ORD_AMOUNT"])
        values = {}
        for mat_code, qty_field, pay_field, own_field in cls._take_home(milling):
            q, a = totals.get(variety.kind_of(mat_code), (0.0, 0.0))
            if (round_half_up(q, 3), round_half_up(a, 2)) != (milling.get(qty_field) or 0, round_half_up(milling.get(pay_field) or 0, 2)):
                values[qty_field], values[pay_field] = round_half_up(q, 3), round_half_up(a, 2)
        if values:
            MillingOrder.follow_take_home(milling, values, ope_user)

    @classmethod
    def merge_take_home(cls, ope_user):
        """Update 39 (start.sh, once per order): a milling order's Open take-home orders made before (one per SKU) become
        ONE order with a line per SKU; the others are cancelled with a remark. Orders already Prepared or later stay
        as they are. Returns how many milling orders were changed."""
        from .milling_order import MillingOrder
        changed = 0
        ids = sorted({o["REF_ORD_ID"] for o in col(cls.COL).find({"REF_ORD_TYPE": "ML", "ORD_STATUS": OPEN, "ORD_ITEMS": None}, NO_ID)})
        for ref in ids:
            milling = MillingOrder.find(ref)
            orders = [o for o in cls.for_milling(ref) if o["ORD_STATUS"] != CANCELLED]
            if not milling or not orders or any(o["ORD_STATUS"] != OPEN for o in orders):
                continue
            lines = cls._milling_lines(milling)
            if not any(l["ORD_QTY"] > 0 for l in lines):
                continue
            keep = orders[0]
            cls._save(keep, cls._header(lines), ope_user)
            for other in orders[1:]:
                cls._save(other, {"ORD_STATUS": CANCELLED, "REMARK": "Merged into %s (update 39)" % keep["ORD_ID"]}, ope_user)
            col(MillingOrder.COL).update_one({"ORD_ID": ref}, {"$set": {"REF_BUYING_ORDER": keep["ORD_ID"]}})
            changed += 1
        return changed

    @classmethod
    def open_for_milling(cls, milling_ord_ids):
        """{milling order ID: [its take-home orders not Closed or Cancelled yet]}. Update 28: while it has any, the
        milling order cannot be Closed or Cancelled."""
        ids = list(milling_ord_ids)
        out = {}
        if not ids:
            return out
        for order in col(cls.COL).find({"REF_ORD_TYPE": "ML", "REF_ORD_ID": {"$in": ids}}, NO_ID).sort([("ORD_ID", 1)]):
            if cls.is_open(order):
                out.setdefault(order["REF_ORD_ID"], []).append(order)
        return out
