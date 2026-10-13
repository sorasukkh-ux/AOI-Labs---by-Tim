"""AOITS_ORDMILLING: milling orders, from registration to Closed or Cancelled.

Every change is also written to AOILOG_ORDMILLING.
"""
import re
from .account import Account
from .buysell_order import BuySellOrder
from .inventory import Inventory, MOVE_MILL, MOVE_MILL_PICKUP, lot_of
from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, to_number, round_half_up, DuplicateKey
from .branch import Branch
from .counter import Counter
from .customer import Customer
from .mill_yield import MillYield
from .price import Price, Promotion, MAT_BRAN
from . import variety as var
from . import mill_event
from .rules import charges as charge_rule
from .rules import receive_time, status_flow as flow, yield_rule
from ..timeutil import now_local, now_utc, parse_local_datetime, to_local, today_local

PRIORITIES = ("U", "H", "M", "L")
PAY_FIELDS = ("EST_PAY_MILLFEE", "EST_PAY_BRAN", "EST_PAY_BROKE", "EST_PAY_DELIVERY")
NEW = "__new__"


class MillingOrder:
    COL = "AOITS_ORDMILLING"
    LOG = "AOILOG_ORDMILLING"

    # ---------- reads ----------
    @classmethod
    def find(cls, ord_id, branch_id=None):
        flt = {"ORD_ID": (ord_id or "").strip().upper()}
        if branch_id:
            flt["BRANCH_ID"] = branch_id
        return col(cls.COL).find_one(flt, NO_ID)

    @classmethod
    def for_branch(cls, branch_id, statuses=None):
        flt = {"BRANCH_ID": branch_id}
        if statuses:
            flt["MILL_STATUS"] = {"$in": list(statuses)}
        return list(col(cls.COL).find(flt, NO_ID).sort([("REGIST_DATE", -1)]))

    @classmethod
    def for_customer(cls, cust_code, branch_id, limit=20):
        cursor = col(cls.COL).find({"CUST_CODE": cust_code, "BRANCH_ID": branch_id}, NO_ID).sort([("REGIST_DATE", -1)]).limit(limit)
        return list(cursor)

    @classmethod
    def count_attention(cls, branch_id):
        """Orders the operator must look at: cancellation requests and delivery times to confirm (home menu mark)."""
        unconfirmed = col(cls.COL).count_documents({"BRANCH_ID": branch_id, "MILL_STATUS": flow.DELIVERY,
                                                    "DELIVERY_CONFIRM_FLG": {"$ne": True}})
        return cls.count_pending_cancel(branch_id) + unconfirmed

    @classmethod
    def count_pending_cancel(cls, branch_id):
        return col(cls.COL).count_documents({"BRANCH_ID": branch_id, "CANCEL_REQ_FLG": flow.REQ_PENDING})

    @staticmethod
    def _day_key():
        today = now_local()
        return "%04d%02d%02d" % (today.year, today.month, today.day)

    @classmethod
    def preview_ids(cls):
        """(queue number, order ID) the next order would get. A preview: the real ones are given on save."""
        day = cls._day_key()
        seq = Counter.peek("ML-" + day)
        return "%04d" % seq, "ML-%s-%04d" % (day, seq)

    @staticmethod
    def pay_total(order):
        return round_half_up(sum(order.get(f) or 0 for f in PAY_FIELDS), 2)

    # ---------- calculation shared by the live preview and the saved order ----------
    @classmethod
    def quote(cls, branch_id, paddy_kg, take_bran=0, take_broken=0, order_type=flow.TAKEAWAY, distance_km=None, priority="M",
              existing=None, variety=None):
        """Expected outputs, what the customer pays and when the order can be received.
        `existing`: the saved order being edited; it keeps its place in the queue."""
        branch = Branch.get(branch_id)
        if not branch:
            raise ValidationError("errBranchUnknown")
        variety = var.clean(variety)
        ratios = MillYield.ratios(branch_id, variety)
        outputs = yield_rule.outputs(paddy_kg, ratios)
        is_delivery = order_type == flow.DELIVER
        charges = charge_rule.milling_charges(
            outputs["rice"], outputs["bran"], outputs["broken"], take_bran, take_broken,
            (distance_km or 0) if is_delivery else None,
            branch.get("SYSP_MILLFEEUNITKG"), branch.get("SYSP_MILLFEEPERUNIT"), branch.get("SYSP_DELIVERYFEEKM"),
            Price.sell_price(branch_id, MAT_BRAN), Price.sell_price(branch_id, var.code(variety, var.BROKEN)),
            Promotion.milling_discount_pct(branch_id, today_local()))
        candidate = {"ORD_ID": NEW, "MILL_STATUS": flow.QUEUE, "MILL_PRIORITY": priority,
                     "REGIST_PADDY": paddy_kg, "REGIST_DATE": now_utc()}
        waiting = cls.for_branch(branch_id, (flow.QUEUE, flow.IN_PROCESS))
        if existing:
            waiting = [o for o in waiting if o["ORD_ID"] != existing["ORD_ID"]]
            candidate.update({"MILL_STATUS": flow.IN_PROCESS if existing["MILL_STATUS"] == flow.IN_PROCESS else flow.QUEUE,
                              "MILL_PRIORITY": existing.get("MILL_PRIORITY") or priority, "REGIST_DATE": existing["REGIST_DATE"]})
        plan = receive_time.schedule(
            receive_time.queue_sequence(waiting + [candidate]), now_local(),
            branch.get("SYSP_MILLTIMEKG"), branch.get("SYSP_MILLORDGAP"),
            branch.get("SYSP_OPETIMESTART"), branch.get("SYSP_OPETIMEEND"), Branch.periods(branch))[NEW]
        return {"ratios": ratios, "outputs": outputs, "charges": charges, "finish": plan["finish"],
                "receive": plan["receive"], "minutes": plan["minutes"], "orders_ahead": len(waiting)}

    # ---------- registration ----------
    @staticmethod
    def _delivery_inputs(form, customer):
        address = clean_text(form.get("DELIVERY_ADDRESS")) or clean_text((customer or {}).get("ADDRESS"))
        if not address:
            raise ValidationError("errDeliveryAddr")
        try:
            distance = to_number(form.get("DELIVERY_DISTANCE"), (customer or {}).get("DISTANCE_KM"))
        except ValueError:
            raise ValidationError("custErrDistance")
        if distance is None:
            raise ValidationError("alertEnterDistance")
        if distance < 0:
            raise ValidationError("custErrDistance")
        return address, round_half_up(distance, 3)

    @classmethod
    def parse(cls, branch_id, form):
        """Validates the registration form and returns the cleaned inputs."""
        customer = Customer.find(form.get("CUST_CODE"))
        if not customer:
            raise ValidationError("errCustomerRequired")
        try:
            paddy = to_number(form.get("REGIST_PADDY"), 0)
            take_bran = to_number(form.get("BRAN_ORDER"), 0)
            take_broken = to_number(form.get("BROKE_ORDER"), 0)
        except ValueError:
            raise ValidationError("errNumber")
        if paddy <= 0:
            raise ValidationError("alertEnterPaddy")
        if take_bran < 0 or take_broken < 0:
            raise ValidationError("errNegative")
        variety = var.clean(form.get("RICE_VARIETY"))
        if yield_rule.check(MillYield.ratios(branch_id, variety))["total"] <= 0:
            raise ValidationError("errYieldMissing")
        order_type = flow.DELIVER if form.get("ORDER_TYPE") == flow.DELIVER else flow.TAKEAWAY
        priority = form.get("MILL_PRIORITY") if form.get("MILL_PRIORITY") in PRIORITIES else var.DEFAULT_PRIORITY[variety]
        address, distance = None, None
        if order_type == flow.DELIVER:
            address, distance = cls._delivery_inputs(form, customer)
        return {"customer": customer, "paddy": round_half_up(paddy, 3), "take_bran": round_half_up(take_bran, 3),
                "take_broken": round_half_up(take_broken, 3), "order_type": order_type, "priority": priority,
                "address": address, "distance": distance, "remark": clean_text(form.get("REMARK")),
                "variety": variety}

    @classmethod
    def create(cls, branch_id, form, ope_user):
        data = cls.parse(branch_id, form)
        quote = cls.quote(branch_id, data["paddy"], data["take_bran"], data["take_broken"],
                          data["order_type"], data["distance"], data["priority"], variety=data["variety"])
        charges, outputs = quote["charges"], quote["outputs"]
        day = cls._day_key()
        seq = Counter.next("ML-" + day)
        if seq > 9999:
            raise ValidationError("errDailyOrderLimit")
        order = {
            "ORD_ID": "ML-%s-%04d" % (day, seq), "QR_ID": "%04d" % seq,
            "ORDER_TYPE": data["order_type"], "BRANCH_ID": branch_id,
            "MILL_STATUS": flow.QUEUE, "MILL_PRIORITY": data["priority"],
            "CANCEL_REQ_FLG": flow.REQ_NONE, "CANCEL_REQ_DATE": None, "CANCEL_ANS_DATE": None, "CANCEL_REJECT_REASON": None,
            "HOLD_REASON": None, "DELIVERY_CONFIRM_FLG": False, "DELIVERY_ARRIVAL_TIME": None,
            "CUST_CODE": data["customer"]["CUST_CODE"],
            "REGIST_PADDY": data["paddy"], "UOM_CODE": "KG", "RICE_VARIETY": data["variety"],
            "BRAN_ORDER": data["take_bran"], "BROKE_ORDER": data["take_broken"],
            "EXPCT_RICE": outputs["rice"], "EXPCT_BRAN": outputs["bran"],
            "EXPCT_BROKE": outputs["broken"], "EXPCT_HUSK": outputs["husk"],
            "EXPCT_RECEIVE_TIME": quote["receive"],
            "EST_PAY_MILLFEE": charges["fee"], "EST_PAY_BRAN": charges["bran"]["amount"],
            "EST_PAY_BROKE": charges["broken"]["amount"], "EST_PAY_DELIVERY": charges["delivery"]["amount"],
            "DELIVERY_ADDRESS": data["address"], "DELIVERY_DISTANCE": data["distance"],
            "REMARK": data["remark"], "REF_QR_PRINT": None, "REF_BUYING_ORDER": None, "REF_INV_LOC": None,
            "SCAN_DATE": None, "ACT_PADDY": None, "ACT_RICE": None,
        }
        order.update(audit_new(ope_user))
        try:
            col(cls.COL).insert_one(dict(order))
        except DuplicateKey:
            raise ValidationError("errTryAgain")
        # the bran/broken rice the customer takes home becomes selling orders on Update Buy/Sell Status
        linked = BuySellOrder.create_for_milling(order, ope_user)
        if linked:
            col(cls.COL).update_one({"ORD_ID": order["ORD_ID"]}, {"$set": {"REF_BUYING_ORDER": ",".join(linked)}})
        cls._log(order["ORD_ID"])
        saved = cls.find(order["ORD_ID"])
        mill_event.record(saved, "", ope_user, when=saved["REGIST_DATE"])
        return saved, quote

    # ---------- changes ----------
    @classmethod
    def _log(cls, ord_id):
        order = cls.find(ord_id)
        entry = {k: v for k, v in order.items() if k not in ("REGIST_DATE", "LAST_UPDATE")}
        entry["LOG_DATE"] = now_utc()
        col(cls.LOG).insert_one(entry)

    @classmethod
    def _save(cls, order, values, ope_user):
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"ORD_ID": order["ORD_ID"]}, {"$set": values})
        cls._log(order["ORD_ID"])
        saved = cls.find(order["ORD_ID"])
        if saved["MILL_STATUS"] != order["MILL_STATUS"]:     # every status change is kept for the timeline / analysis
            mill_event.record(saved, order["MILL_STATUS"], ope_user,
                              reason=values.get("HOLD_REASON") if saved["MILL_STATUS"] == flow.HOLD else None,
                              scanned="SCAN_DATE" in values)
        return saved

    @classmethod
    def _scan(cls, order, form):
        """In process: the operator scans the QR label on the paddy (the key between the order and the material)
        and weighs the paddy. A label of another order is refused."""
        code = re.sub(r"\s+", "", (form or {}).get("SCAN_QR") or "").upper()
        if not code:
            raise ValidationError("errScanRequired", id=order["QR_ID"])
        if code not in ("AOI-" + order["ORD_ID"], order["ORD_ID"]):
            raise ValidationError("errScanWrong", id=order["QR_ID"])
        try:
            paddy = to_number((form or {}).get("ACT_PADDY"), order["REGIST_PADDY"])
        except ValueError:
            raise ValidationError("errNumber")
        if paddy is None or paddy <= 0:
            raise ValidationError("alertEnterPaddy")
        return {"SCAN_DATE": now_utc(), "ACT_PADDY": round_half_up(paddy, 3)}

    @classmethod
    def _open_order(cls, ord_id, branch_id):
        order = cls.find(ord_id, branch_id)
        if not order:
            raise ValidationError("errOrderNotFound")
        if not flow.is_open(order):
            raise ValidationError("errOrderEnded")
        return order

    @staticmethod
    def _hold_reason(form):
        reason = clean_text((form or {}).get("HOLD_REASON"))
        if not reason:
            raise ValidationError("errHoldReason")
        return reason

    @classmethod
    def change_status(cls, ord_id, branch_id, new_status, ope_user, form=None, descriptions=None):
        """`form`: HOLD_REASON for Hold; INV_QTY, LOC_ID, INV_STATUS (the inbound pop-up) for Finished.
        `descriptions`: ledger texts {"full": ..., "fee": ...} used when the order is closed."""
        order = cls._open_order(ord_id, branch_id)
        if new_status not in flow.allowed_next(order):
            raise ValidationError("errStatusNotAllowed")
        if flow.blocked_by_pending_request(order, new_status):
            raise ValidationError("errPendingRequest")
        values = {"MILL_STATUS": new_status}
        if new_status == flow.DELIVERY:                       # the delivery time is confirmed on the list next
            values.update({"DELIVERY_CONFIRM_FLG": False, "DELIVERY_ARRIVAL_TIME": None})
        if flow.needs_reason(new_status):
            values["HOLD_REASON"] = cls._hold_reason(form)
        elif order["MILL_STATUS"] == flow.HOLD:
            values["HOLD_REASON"] = None                     # the reason belongs to the hold that ends now
            if new_status in (flow.QUEUE, flow.IN_PROCESS):
                values["CANCEL_REQ_FLG"] = flow.REQ_NONE     # resumed: an approved cancellation is dropped
        if new_status == flow.IN_PROCESS:
            values.update(cls._scan(order, form))
        inbound = None
        if flow.needs_inbound(new_status):                   # checked before anything is saved
            inbound = Inventory.parse_inbound(form or {}, order["EXPCT_RICE"])
            values["REF_INV_LOC"] = inbound["loc"]
            values["ACT_RICE"] = inbound["qty"]              # the rice actually received
        if inbound:
            Inventory.inbound(branch_id, var.code(order.get("RICE_VARIETY"), var.RICE), inbound, lot_of(order["ORD_ID"]), order["ORD_ID"], "ML", MOVE_MILL, ope_user)
        updated = cls._save(order, values, ope_user)
        if new_status == flow.CLOSED:
            Inventory.release(branch_id, order["ORD_ID"], "ML", MOVE_MILL_PICKUP, ope_user)
            Account.post_milling_income(updated, descriptions or {"full": "", "fee": ""}, ope_user)
        elif new_status == flow.CANCELLED:
            BuySellOrder.cancel_for_milling(order["ORD_ID"], ope_user)
        elif new_status == flow.FINISHED and updated["ORDER_TYPE"] == flow.TAKEAWAY:
            cls._notify(updated, "finished", now_utc())      # LINE: the rice is ready to pick up
        return updated

    @staticmethod
    def _notify(order, kind, when):
        """A LINE message never stops the status change: a problem is kept on the AOITS_NOTIFY record."""
        from . import notify
        try:
            notify.queue(order, kind, when)
        except Exception:                                    # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception("LINE message for %s was not written", order.get("ORD_ID"))

    @classmethod
    def change_priority(cls, ord_id, branch_id, priority, ope_user):
        order = cls._open_order(ord_id, branch_id)
        if priority not in PRIORITIES:
            raise ValidationError("errStatusNotAllowed")
        return cls._save(order, {"MILL_PRIORITY": priority}, ope_user)

    @classmethod
    def request_cancel(cls, ord_id, branch_id, ope_user):
        """Records a customer's cancellation request (in production it arrives from the LINE app)."""
        order = cls._open_order(ord_id, branch_id)
        if not flow.can_request_cancel(order):
            raise ValidationError("errCancelRequestNotAllowed")
        return cls._save(order, {"CANCEL_REQ_FLG": flow.REQ_PENDING, "CANCEL_REQ_DATE": now_utc(),
                                 "CANCEL_ANS_DATE": None, "CANCEL_REJECT_REASON": None}, ope_user)

    @classmethod
    def answer_cancel(cls, ord_id, branch_id, approve, reason, ope_user):
        """Approve: the order goes on Hold with the reason given (required). Reject: the reason is optional."""
        order = cls._open_order(ord_id, branch_id)
        if order.get("CANCEL_REQ_FLG") != flow.REQ_PENDING:
            raise ValidationError("errNoPendingRequest")
        if approve:
            values = {"CANCEL_REQ_FLG": flow.REQ_APPROVED, "CANCEL_ANS_DATE": now_utc(), "MILL_STATUS": flow.HOLD,
                      "HOLD_REASON": cls._hold_reason({"HOLD_REASON": reason})}
        else:
            values = {"CANCEL_REQ_FLG": flow.REQ_REJECTED, "CANCEL_ANS_DATE": now_utc(),
                      "CANCEL_REJECT_REASON": clean_text(reason) or None}
        return cls._save(order, values, ope_user)

    @classmethod
    def _delivery_fee(cls, branch_id, distance):
        rate = (Branch.get(branch_id) or {}).get("SYSP_DELIVERYFEEKM") or 0
        return round_half_up(max(0.0, distance or 0) * max(0.0, rate), 2)

    @classmethod
    def change_type(cls, ord_id, branch_id, new_type, form, ope_user):
        """Take-away <-> Delivery, while the order is open."""
        order = cls._open_order(ord_id, branch_id)
        if new_type not in (flow.TAKEAWAY, flow.DELIVER) or new_type == order["ORDER_TYPE"]:
            raise ValidationError("errStatusNotAllowed")
        if new_type == flow.DELIVER:
            address, distance = cls._delivery_inputs(form, Customer.find(order["CUST_CODE"]))
            values = {"ORDER_TYPE": flow.DELIVER, "DELIVERY_ADDRESS": address, "DELIVERY_DISTANCE": distance,
                      "EST_PAY_DELIVERY": cls._delivery_fee(branch_id, distance)}
        else:
            values = {"ORDER_TYPE": flow.TAKEAWAY, "EST_PAY_DELIVERY": 0.0, "DELIVERY_CONFIRM_FLG": False, "DELIVERY_ARRIVAL_TIME": None}
            if order["MILL_STATUS"] == flow.DELIVERY:
                values["MILL_STATUS"] = flow.FINISHED          # the customer picks up instead
        updated = cls._save(order, values, ope_user)
        if order["MILL_STATUS"] == flow.DELIVERY and updated["MILL_STATUS"] == flow.FINISHED:
            cls._notify(updated, "finished", now_utc())        # LINE: ready to pick up after all
        return updated

    @classmethod
    def confirm_delivery(cls, ord_id, branch_id, form, ope_user):
        """Ready for Delivery: the operator confirms address, distance and expected arrival time.
        Until then the order stays at the top of the list."""
        order = cls._open_order(ord_id, branch_id)
        if order["ORDER_TYPE"] != flow.DELIVER or order["MILL_STATUS"] != flow.DELIVERY:
            raise ValidationError("errStatusNotAllowed")
        address, distance = cls._delivery_inputs(form, None)
        arrival = parse_local_datetime(form.get("DELIVERY_ARRIVAL_TIME"))
        if arrival is None:
            raise ValidationError("errArrivalTime")
        updated = cls._save(order, {"DELIVERY_ADDRESS": address, "DELIVERY_DISTANCE": distance,
                                    "EST_PAY_DELIVERY": cls._delivery_fee(branch_id, distance),
                                    "DELIVERY_ARRIVAL_TIME": arrival, "DELIVERY_CONFIRM_FLG": True}, ope_user)
        cls._notify(updated, "delivery", arrival)             # LINE: the expected arrival time
        return updated

    @classmethod
    def reopen_delivery(cls, ord_id, branch_id, ope_user):
        """A failed delivery: the details can be corrected and confirmed again (back to the top of the list)."""
        order = cls._open_order(ord_id, branch_id)
        if order["MILL_STATUS"] != flow.DELIVERY or not order.get("DELIVERY_CONFIRM_FLG"):
            raise ValidationError("errStatusNotAllowed")
        return cls._save(order, {"DELIVERY_CONFIRM_FLG": False}, ope_user)

    @staticmethod
    def needs_attention(order):
        """Shown first on the list: a cancellation request to answer, or a delivery time to confirm."""
        return order.get("CANCEL_REQ_FLG") == flow.REQ_PENDING or (
            order["MILL_STATUS"] == flow.DELIVERY and not order.get("DELIVERY_CONFIRM_FLG"))

    # ---------- editing the details ----------
    @classmethod
    def update_details(cls, ord_id, branch_id, form, ope_user):
        """The editable order details: customer, delivery address/distance, remark, and - before Finished -
        paddy weight and take-home bran/broken rice. Outputs, charges and receive time are recalculated."""
        order = cls._open_order(ord_id, branch_id)
        values = {}
        customer = Customer.find(form.get("CUST_CODE") or order["CUST_CODE"])
        if not customer:
            raise ValidationError("errCustomerRequired")
        if customer["CUST_CODE"] != order["CUST_CODE"]:
            values["CUST_CODE"] = customer["CUST_CODE"]
        if "REMARK" in form:
            values["REMARK"] = clean_text(form.get("REMARK")) or None
        try:
            paddy = round_half_up(to_number(form.get("REGIST_PADDY"), order["REGIST_PADDY"]), 3)
            take_bran = round_half_up(to_number(form.get("BRAN_ORDER"), order.get("BRAN_ORDER") or 0), 3)
            take_broken = round_half_up(to_number(form.get("BROKE_ORDER"), order.get("BROKE_ORDER") or 0), 3)
        except ValueError:
            raise ValidationError("errNumber")
        old_variety = var.clean(order.get("RICE_VARIETY"))
        variety = var.clean(form.get("RICE_VARIETY")) if form.get("RICE_VARIETY") else old_variety
        qty_changed = (paddy, take_bran, take_broken, variety) != (order["REGIST_PADDY"], order.get("BRAN_ORDER") or 0,
                                                                   order.get("BROKE_ORDER") or 0, old_variety)
        if qty_changed and not flow.quantities_editable(order):
            raise ValidationError("errQtyLocked")
        if paddy <= 0:
            raise ValidationError("alertEnterPaddy")
        if take_bran < 0 or take_broken < 0:
            raise ValidationError("errNegative")
        distance = order.get("DELIVERY_DISTANCE")
        if order["ORDER_TYPE"] == flow.DELIVER:
            address, distance = cls._delivery_inputs(form, customer)
            if (address, distance) != (order.get("DELIVERY_ADDRESS"), order.get("DELIVERY_DISTANCE")):
                values.update({"DELIVERY_ADDRESS": address, "DELIVERY_DISTANCE": distance,
                               "EST_PAY_DELIVERY": cls._delivery_fee(branch_id, distance)})
                if order["MILL_STATUS"] == flow.DELIVERY:
                    values["DELIVERY_CONFIRM_FLG"] = False        # changed details are confirmed again
        if qty_changed:
            quote = cls.quote(branch_id, paddy, take_bran, take_broken, order["ORDER_TYPE"], distance,
                              order.get("MILL_PRIORITY") or "M", existing=order, variety=variety)
            outputs, charges = quote["outputs"], quote["charges"]
            values.update({"REGIST_PADDY": paddy, "BRAN_ORDER": take_bran, "BROKE_ORDER": take_broken, "RICE_VARIETY": variety,
                           "EXPCT_RICE": outputs["rice"], "EXPCT_BRAN": outputs["bran"], "EXPCT_BROKE": outputs["broken"],
                           "EXPCT_HUSK": outputs["husk"], "EXPCT_RECEIVE_TIME": quote["receive"],
                           "EST_PAY_MILLFEE": charges["fee"], "EST_PAY_BRAN": charges["bran"]["amount"],
                           "EST_PAY_BROKE": charges["broken"]["amount"], "EST_PAY_DELIVERY": charges["delivery"]["amount"]})
        if not values:
            return order
        merged = dict(order, **values)
        if order.get("REF_BUYING_ORDER") or take_bran > 0 or take_broken > 0:
            linked = BuySellOrder.sync_for_milling(merged, ope_user)      # refuses before anything is saved
            values["REF_BUYING_ORDER"] = ",".join(linked) or None
        return cls._save(order, values, ope_user)

    # ---------- list helpers ----------
    @staticmethod
    def sort_for_list(orders):
        """Orders needing attention first (a cancellation request to answer, a delivery time to confirm), then In-process, Hold, Queue, Finished,
        Ready for Delivery, Closed, Cancelled; inside a status by priority, then registration time."""
        def key(order):
            return (0 if MillingOrder.needs_attention(order) else 1,
                    flow.LIST_RANK.get(order["MILL_STATUS"], 9),
                    receive_time.PRIORITY_RANK.get(order.get("MILL_PRIORITY"), 9),
                    to_local(order["REGIST_DATE"]).timestamp())
        return sorted(orders, key=key)
