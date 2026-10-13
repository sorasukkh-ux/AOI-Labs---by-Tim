"""AOIMS_MATERIAL, AOIMS_PRICE (prices and margins per branch, log AOILOG_PRICE) and AOIMS_PROMOTION.

Pricing Maintenance, Margin Maintenance and Marketing write here; Customer Service (milling, buy, sell),
Current Price and the order screens read the current prices.

Suggested prices (the design's "market + margin"):
  buy  = market buy  x (1 - buy target %),  sell = market sell x (1 + sell target %),
  each kept between the floor and the ceiling (MG_*_MIN / MG_*_MAX, THB/kg) when they are set.
When a suggestion changes, an Approved price becomes Waiting for approval. Custom prices (husk) have no suggestion.
"""
from datetime import datetime

from .base import col, NO_ID, ValidationError, audit_update, round_half_up, to_number
from ..timeutil import local_tz, now_utc, to_local

MAT_PADDY, MAT_RICE, MAT_BRAN, MAT_BROKEN, MAT_HUSK = "PADDY", "RICE_W5", "BRAN", "BROKEN", "HUSK"


class Material:
    COL = "AOIMS_MATERIAL"

    @classmethod
    def all(cls):
        return list(col(cls.COL).find({}, NO_ID).sort([("MAT_CODE", 1)]))


APPROVED, WAITING, CUSTOM = "A", "W", "C"
from .variety import ALL as PRICED_MATERIALS                  # every SKU, grouped by variety
MARGIN_FIELDS = ("MG_BUY_TARGET", "MG_BUY_RANGEFROM", "MG_BUY_RANGETO", "MG_BUY_MIN", "MG_BUY_MAX",
                 "MG_SELL_TARGET", "MG_SELL_RANGEFROM", "MG_SELL_RANGETO", "MG_SELL_MIN", "MG_SELL_MAX")


def _num(form, name, required=False, minimum=0.0, maximum=None):
    try:
        value = to_number(form.get(name), None)
    except ValueError:
        raise ValidationError("errNumber")
    if value is None:
        if required:
            raise ValidationError("errPriceRequired")
        return None
    if value < minimum or (maximum is not None and value > maximum):
        raise ValidationError("errPriceRange")
    return round_half_up(value, 3)


def _range(text):
    """'3-7' -> (3.0, 7.0); blank -> (None, None)."""
    text = (text or "").replace(" ", "")
    if not text:
        return None, None
    parts = text.split("-")
    try:
        low, high = float(parts[0]), float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        raise ValidationError("errMarginRange")
    if high is None or low < 0 or high > 100 or low > high:
        raise ValidationError("errMarginRange")
    return round_half_up(low, 3), round_half_up(high, 3)


class Price:
    COL = "AOIMS_PRICE"
    LOG = "AOILOG_PRICE"

    @classmethod
    def get(cls, branch_id, mat_code):
        return col(cls.COL).find_one({"BRANCH_ID": branch_id, "MAT_CODE": mat_code}, NO_ID)

    @classmethod
    def for_branch(cls, branch_id):
        found = {p["MAT_CODE"]: p for p in col(cls.COL).find({"BRANCH_ID": branch_id}, NO_ID)}
        return [found[m] for m in PRICED_MATERIALS if m in found]

    @staticmethod
    def suggestion(price):
        """(suggested buy, suggested sell) from market price and margin target, or the stored values."""
        def one(market, target, low, high, sign):
            if market is None or target is None:
                return None
            value = market * (1 + sign * target / 100.0)
            if low is not None:
                value = max(value, low)
            if high is not None:
                value = min(value, high)
            return round_half_up(value, 2)
        buy = one(price.get("MKPRICE_BUY"), price.get("MG_BUY_TARGET"), price.get("MG_BUY_MIN"), price.get("MG_BUY_MAX"), -1)
        sell = one(price.get("MKPRICE_SELL"), price.get("MG_SELL_TARGET"), price.get("MG_SELL_MIN"), price.get("MG_SELL_MAX"), 1)
        return (buy if buy is not None else price.get("SUGPRICE_BUY"), sell if sell is not None else price.get("SUGPRICE_SELL"))

    @staticmethod
    def margins(price):
        """Current margins in % of the market price, and whether they are inside the margin ranges."""
        out = {}
        mk_buy, mk_sell = price.get("MKPRICE_BUY"), price.get("MKPRICE_SELL")
        if mk_buy and price.get("PRICE_BUY"):
            out["buy"] = round_half_up((mk_buy - price["PRICE_BUY"]) / mk_buy * 100, 1)
        if mk_sell and price.get("PRICE_SELL"):
            out["sell"] = round_half_up((price["PRICE_SELL"] - mk_sell) / mk_sell * 100, 1)
        for side, low, high in (("buy", "MG_BUY_RANGEFROM", "MG_BUY_RANGETO"), ("sell", "MG_SELL_RANGEFROM", "MG_SELL_RANGETO")):
            if side in out and price.get(low) is not None and price.get(high) is not None:
                out[side + "_ok"] = price[low] <= out[side] <= price[high]
        return out

    @classmethod
    def _save(cls, price, values, ope_user):
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"BRANCH_ID": price["BRANCH_ID"], "MAT_CODE": price["MAT_CODE"]}, {"$set": values})
        saved = cls.get(price["BRANCH_ID"], price["MAT_CODE"])
        log = {k: v for k, v in saved.items() if k not in ("REGIST_DATE", "LAST_UPDATE")}
        log["LOG_DATE"] = now_utc()
        col(cls.LOG).insert_one(log)
        return saved

    @classmethod
    def _resuggest(cls, price, ope_user, extra=None):
        merged = dict(price, **(extra or {}))
        values = dict(extra or {})
        if merged.get("PRICE_STATUS") != CUSTOM:
            sug_buy, sug_sell = cls.suggestion(merged)
            values.update({"SUGPRICE_BUY": sug_buy, "SUGPRICE_SELL": sug_sell})
            changed = (sug_buy, sug_sell) != (merged.get("PRICE_BUY"), merged.get("PRICE_SELL"))
            if changed and (sug_buy, sug_sell) != (price.get("SUGPRICE_BUY"), price.get("SUGPRICE_SELL")):
                values["PRICE_STATUS"] = WAITING              # a new suggestion waits for approval
        return cls._save(price, values, ope_user) if values else price

    @classmethod
    def save_price(cls, branch_id, mat_code, form, approve, ope_user):
        """Pricing card: Approve (waiting price, needs the Approve right), Edit (approved) or Save (custom: husk)."""
        price = cls.get(branch_id, mat_code)
        if not price:
            raise ValidationError("errPriceUnknown")
        custom = price.get("PRICE_STATUS") == CUSTOM
        values = {"PRICE_SELL": _num(form, "PRICE_SELL", required=True)}
        values["PRICE_BUY"] = (price.get("PRICE_BUY") or 0.0) if custom else _num(form, "PRICE_BUY", required=True)
        if price.get("PRICE_STATUS") == WAITING:
            if not approve:
                raise ValidationError("errPriceApproveRight")
            values["PRICE_STATUS"] = APPROVED
        return cls._save(price, values, ope_user)

    @classmethod
    def update_market(cls, branch_id, mat_code, form, ope_user):
        """New market prices (typed in until a market feed is connected): the suggestion is recalculated."""
        price = cls.get(branch_id, mat_code)
        if not price or price.get("PRICE_STATUS") == CUSTOM:
            raise ValidationError("errPriceUnknown")
        return cls._resuggest(price, ope_user, {"MKPRICE_BUY": _num(form, "MKPRICE_BUY", required=True),
                                                "MKPRICE_SELL": _num(form, "MKPRICE_SELL", required=True)})

    @classmethod
    def save_margins(cls, branch_id, form, ope_user):
        """Margin Maintenance: target, range, floor and ceiling per material (fields <NAME>__<MAT_CODE>).
        The design has one floor and one ceiling per material; they bound both suggested prices."""
        plans = []
        for price in cls.for_branch(branch_id):
            mat = price["MAT_CODE"]
            if price.get("PRICE_STATUS") == CUSTOM or ("buy_target__" + mat) not in form:
                continue
            g = lambda name: {name: form.get(name + "__" + mat)}
            buy_t = _num(g("buy_target"), "buy_target", maximum=100)
            sell_t = _num(g("sell_target"), "sell_target", maximum=1000)
            buy_lo, buy_hi = _range(form.get("buy_range__" + mat))
            sell_lo, sell_hi = _range(form.get("sell_range__" + mat))
            floor, ceiling = _num(g("floor"), "floor"), _num(g("ceiling"), "ceiling")
            if floor is not None and ceiling is not None and floor > ceiling:
                raise ValidationError("errFloorCeiling", mat=mat)
            plans.append((price, {"MG_BUY_TARGET": buy_t, "MG_BUY_RANGEFROM": buy_lo, "MG_BUY_RANGETO": buy_hi,
                                  "MG_BUY_MIN": floor, "MG_BUY_MAX": ceiling, "MG_SELL_TARGET": sell_t,
                                  "MG_SELL_RANGEFROM": sell_lo, "MG_SELL_RANGETO": sell_hi, "MG_SELL_MIN": floor, "MG_SELL_MAX": ceiling}))
        changed = 0
        for price, values in plans:                        # everything checked first
            if any(price.get(k) != v for k, v in values.items()):
                cls._resuggest(price, ope_user, values)
                changed += 1
        return changed

    @classmethod
    def buy_price(cls, branch_id, mat_code):
        doc = col(cls.COL).find_one({"BRANCH_ID": branch_id, "MAT_CODE": mat_code}, NO_ID)
        return float((doc or {}).get("PRICE_BUY") or 0)

    @classmethod
    def sell_price(cls, branch_id, mat_code):
        doc = col(cls.COL).find_one({"BRANCH_ID": branch_id, "MAT_CODE": mat_code}, NO_ID)
        return float((doc or {}).get("PRICE_SELL") or 0)


class Promotion:
    COL = "AOIMS_PROMOTION"
    MILL_OWN = "MILL_OWN"

    @staticmethod
    def _date(text):
        text = (text or "").strip()
        if not text:
            return None
        try:
            day = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            raise ValidationError("errDate")
        return day.replace(tzinfo=local_tz())

    @classmethod
    def save(cls, branch_id, promo_code, form, ope_user):
        """Marketing: promotion on/off, discount % and optional dates."""
        try:
            pct = to_number(form.get("DISCOUNT_PCT"), None)
        except ValueError:
            raise ValidationError("mkErrPct")
        if pct is None or pct < 0 or pct > 100:
            raise ValidationError("mkErrPct")
        start, end = cls._date(form.get("VALID_FROM")), cls._date(form.get("VALID_TO"))
        if start and end and end < start:
            raise ValidationError("mkErrDates")
        values = {"DISCOUNT_PCT": round_half_up(pct, 3), "PROMO_STATUS": "A" if form.get("PROMO_STATUS") == "A" else "I",
                  "VALID_FROM": start, "VALID_TO": end}
        values.update(audit_update(ope_user))
        existing = cls.get(branch_id, promo_code)
        if existing is None:
            from .base import audit_new
            doc = {"BRANCH_ID": branch_id, "PROMO_CODE": promo_code, "PROMO_NAME": "Milling promotion"}
            doc.update(values)
            doc.update(audit_new(ope_user))
            col(cls.COL).insert_one(doc)
        else:
            col(cls.COL).update_one({"BRANCH_ID": branch_id, "PROMO_CODE": promo_code}, {"$set": values})
        return cls.get(branch_id, promo_code)

    @classmethod
    def get(cls, branch_id, promo_code):
        return col(cls.COL).find_one({"BRANCH_ID": branch_id, "PROMO_CODE": promo_code}, NO_ID)

    @staticmethod
    def state(promo, today):
        """running / inactive / scheduled / expired, for a local calendar date."""
        if not promo or promo.get("PROMO_STATUS") != "A":
            return "inactive"
        start, end = promo.get("VALID_FROM"), promo.get("VALID_TO")
        if start and today < to_local(start).date():
            return "scheduled"
        if end and today > to_local(end).date():
            return "expired"
        return "running"

    @classmethod
    def milling_discount_pct(cls, branch_id, today):
        promo = cls.get(branch_id, cls.MILL_OWN)
        return float(promo.get("DISCOUNT_PCT") or 0) if cls.state(promo, today) == "running" else 0.0
