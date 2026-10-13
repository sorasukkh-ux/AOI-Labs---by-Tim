"""What the customer pays for a milling order.

1. Milling and packaging fee: the rice output rounded up to full units (default 50 kg) x fee per unit
   (default 12 THB). Both are set per branch on Milling Maintenance.
2. Bran and broken rice the customer takes home, at the branch's current selling price: the milling
   promotion discount applies up to what the customer's own paddy produced; anything above that is
   at the normal price.
3. Delivery orders only: delivery distance (km) x the branch's delivery fee per km (default 0).
"""
import math

from ..base import round_half_up as r2


def _take(taken, own, price, discount_pct):
    taken = max(0.0, taken or 0.0)
    own_kg = min(taken, r2(own or 0))
    extra_kg = r2(taken - own_kg)
    amount = r2(own_kg * price * (1 - discount_pct / 100.0) + extra_kg * price)
    return {"price": price, "own_kg": own_kg, "extra_kg": extra_kg, "amount": amount}


def milling_charges(rice_out, bran_out, broken_out, take_bran, take_broken, delivery_km,
                    fee_unit_kg, fee_per_unit, delivery_fee_per_km, bran_price, broken_price, discount_pct):
    """`delivery_km` is None for a take-away order."""
    unit_kg = fee_unit_kg if fee_unit_kg and fee_unit_kg > 0 else 50
    fee_per_unit = fee_per_unit if fee_per_unit and fee_per_unit > 0 else 0
    discount_pct = min(100.0, max(0.0, discount_pct or 0.0))
    units = math.ceil(r2(rice_out or 0) / unit_kg)
    fee = r2(units * fee_per_unit)
    bran = _take(take_bran, bran_out, bran_price or 0, discount_pct)
    broken = _take(take_broken, broken_out, broken_price or 0, discount_pct)
    is_delivery = delivery_km is not None
    km = max(0.0, delivery_km or 0.0) if is_delivery else 0.0
    rate = delivery_fee_per_km if delivery_fee_per_km and delivery_fee_per_km > 0 else 0
    delivery = {"is_delivery": is_delivery, "km": km, "rate_per_km": rate, "amount": r2(km * rate) if is_delivery else 0.0}
    return {
        "units": units, "unit_kg": unit_kg, "fee_per_unit": fee_per_unit, "fee": fee,
        "bran": bran, "broken": broken, "discount_pct": discount_pct, "delivery": delivery,
        "total": r2(fee + bran["amount"] + broken["amount"] + delivery["amount"]),
    }
