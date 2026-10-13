"""Expected receive time of milling orders.

The mill works through its orders one at a time, inside operating hours:
  * orders already In-process come first, then the Queue by priority and registration time;
  * milling time = paddy weight (kg) x minutes per kg, plus a gap between orders;
  * an order that cannot be finished before closing starts at the next opening time
    (an order longer than a whole working day simply continues over the following days);
  * the customer is told the next fixed receiving period at or after the finish time.
All times here are local (mill time zone) datetimes.
"""
from datetime import timedelta

PRIORITY_RANK = {"U": 0, "H": 1, "M": 2, "L": 3}


def _minutes(hhmm):
    hours, minutes = str(hhmm).split(":")
    return int(hours) * 60 + int(minutes)


def _at(day, minute_of_day):
    return day.replace(hour=minute_of_day // 60, minute=minute_of_day % 60, second=0, microsecond=0)


def clamp_to_window(moment, open_min, close_min):
    """Moves a time outside operating hours to the next opening time."""
    minute = moment.hour * 60 + moment.minute
    if minute < open_min:
        return _at(moment, open_min)
    if minute >= close_min:
        return _at(moment + timedelta(days=1), open_min)
    return moment


def add_working_minutes(start, minutes, open_min, close_min):
    """Adds working time to `start`, pausing between closing and the next opening."""
    moment, left = clamp_to_window(start, open_min, close_min), float(minutes)
    for _ in range(3660):                                    # safety stop: ten years of days
        until_close = (_at(moment, close_min) - moment).total_seconds() / 60.0
        if left <= until_close:
            return moment + timedelta(minutes=left)
        left -= until_close
        moment = _at(moment + timedelta(days=1), open_min)
    return moment


def round_up_to_period(finish, periods):
    """The first receiving period at or after `finish`; after the last period, the first one next day."""
    marks = sorted(_minutes(p) for p in periods or [])
    if not marks:
        return finish
    minute = finish.hour * 60 + finish.minute + (1 if finish.second or finish.microsecond else 0)
    for mark in marks:
        if mark >= minute:
            return _at(finish, mark)
    return _at(finish + timedelta(days=1), marks[0])


def queue_sequence(orders):
    """Working order: In-process first, then Queue by priority, then by registration time."""
    def key(order):
        return (0 if order["MILL_STATUS"] == "I" else 1,
                PRIORITY_RANK.get(order.get("MILL_PRIORITY"), 9),
                order["REGIST_DATE"].timestamp() if order.get("REGIST_DATE") else float("inf"),
                order.get("ORD_ID", ""))
    return sorted([o for o in orders if o["MILL_STATUS"] in ("Q", "I")], key=key)


def schedule(orders, now, time_per_kg, gap_minutes, open_time, close_time, periods):
    """Returns {ORD_ID: {"start", "finish", "receive", "minutes"}} for the given working sequence.

    `orders` must already be in working order (see queue_sequence); each needs ORD_ID and REGIST_PADDY.
    """
    open_min, close_min = _minutes(open_time), _minutes(close_time)
    if close_min <= open_min:
        open_min, close_min = 0, 24 * 60 - 1
    day_length = close_min - open_min
    pointer, result = clamp_to_window(now, open_min, close_min), {}
    for index, order in enumerate(orders):
        if index > 0:
            pointer = clamp_to_window(pointer + timedelta(minutes=gap_minutes or 0), open_min, close_min)
        minutes = (order.get("REGIST_PADDY") or 0) * (time_per_kg or 0)
        left_today = (_at(pointer, close_min) - pointer).total_seconds() / 60.0
        start = pointer
        if minutes > left_today and minutes <= day_length:
            start = _at(pointer + timedelta(days=1), open_min)
        finish = add_working_minutes(start, minutes, open_min, close_min)
        result[order["ORD_ID"]] = {"start": start, "finish": finish, "minutes": minutes,
                                   "receive": round_up_to_period(finish, periods)}
        pointer = finish
    return result
