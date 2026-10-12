"""Milling Production: output ratios (kg of output per 1 kg of paddy)."""
from ..base import round_half_up

KEYS = ("rice", "bran", "broken", "husk")


def check(ratios):
    """Each ratio must be 0..1 and the four together must not be more than 1 (100% of the paddy)."""
    in_range, total = True, 0.0
    for key in KEYS:
        value = ratios.get(key)
        if not isinstance(value, (int, float)) or value != value or value < 0 or value > 1:
            in_range = False
        else:
            total += value
    total = round_half_up(total, 4)
    return {"in_range": in_range, "total": total, "ok": in_range and total <= 1}


def outputs(paddy_kg, ratios):
    """Expected output weights (kg, 2 decimals) for a paddy weight."""
    return {key: round_half_up((paddy_kg or 0) * (ratios.get(key) or 0), 2) for key in KEYS}
