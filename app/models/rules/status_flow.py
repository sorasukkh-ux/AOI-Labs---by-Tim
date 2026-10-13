"""Milling order status flow (agreed 2026-10-05, revised 2026-10-08 from Tim's take-away diagram;
see AOI_Milling_Status_Flow in the project docs).

Status codes are the MILL_STATUS values of AOITS_ORDMILLING.
"""
QUEUE, IN_PROCESS, HOLD, FINISHED, DELIVERY, CLOSED, CANCELLED = "Q", "I", "H", "F", "D", "C", "CC"
ALL_STATUSES = (QUEUE, IN_PROCESS, HOLD, FINISHED, DELIVERY, CLOSED, CANCELLED)
END_STATUSES = (CLOSED, CANCELLED)

TAKEAWAY, DELIVER = "T", "D"                      # ORDER_TYPE
REQ_NONE, REQ_PENDING, REQ_APPROVED, REQ_REJECTED = "N", "P", "A", "R"     # CANCEL_REQ_FLG

FLOW = {
    QUEUE: [IN_PROCESS, HOLD, CANCELLED],
    IN_PROCESS: [QUEUE, HOLD, FINISHED, CANCELLED],
    HOLD: [QUEUE, IN_PROCESS, CANCELLED],
    FINISHED: [DELIVERY, CLOSED, CANCELLED],
    DELIVERY: [CLOSED],
    CLOSED: [],
    CANCELLED: [],
}


def is_open(order):
    return order["MILL_STATUS"] not in END_STATUSES


def allowed_next(order):
    """Statuses the order may move to now."""
    status, is_delivery = order["MILL_STATUS"], order.get("ORDER_TYPE") == DELIVER
    out = []
    for nxt in FLOW.get(status, []):
        if status == FINISHED and nxt == DELIVERY and not is_delivery:
            continue                              # only delivery orders go to Ready for Delivery
        if status == FINISHED and nxt == CLOSED and is_delivery:
            continue                              # a delivery order is closed after it was sent out
        out.append(nxt)
    return out


def blocked_by_pending_request(order, new_status):
    """While a cancellation request is waiting for an answer the order cannot be set to Finished."""
    return order.get("CANCEL_REQ_FLG") == REQ_PENDING and new_status == FINISHED


def can_request_cancel(order):
    """A customer's cancellation request is accepted only in Queue or In-process, one at a time."""
    return order["MILL_STATUS"] in (QUEUE, IN_PROCESS) and order.get("CANCEL_REQ_FLG") != REQ_PENDING


def needs_reason(new_status):
    """Putting an order on Hold always needs a reason (HOLD_REASON)."""
    return new_status == HOLD


def needs_inbound(new_status):
    """Finishing an order receives the milled rice into inventory at the same time."""
    return new_status == FINISHED


# Order of the milling order list: requests waiting for an answer come first (see MillingOrder.sort_for_list).
LIST_RANK = {s: i for i, s in enumerate((IN_PROCESS, HOLD, QUEUE, FINISHED, DELIVERY, CLOSED, CANCELLED))}


def quantities_editable(order):
    """Paddy weight and take-home quantities can change until the order is Finished (the rice is then in stock)."""
    return order["MILL_STATUS"] in (QUEUE, IN_PROCESS, HOLD)
