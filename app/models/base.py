"""Shared pieces for the models."""
from decimal import Decimal, ROUND_HALF_UP

from ..db import col, get_db, DuplicateKey, AFTER      # noqa: F401  (re-exported for the models)
from ..timeutil import now_utc

NO_ID = {"_id": 0}          # projection: MongoDB's internal _id never leaves the model layer


class ValidationError(Exception):
    """A business rule was broken. `key` is a translation key; `params` fill its {placeholders}."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.key, self.params = key, params


def audit_new(ope_user):
    now = now_utc()
    return {"OPE_USER": ope_user, "REGIST_DATE": now, "LAST_UPDATE": now}


def audit_update(ope_user):
    return {"OPE_USER": ope_user, "LAST_UPDATE": now_utc()}


def round_half_up(value, places=2):
    """Rounds the way people do on paper (0.005 -> 0.01), not banker's rounding."""
    quantum = Decimal(1).scaleb(-places)
    return float(Decimal(str(value or 0)).quantize(quantum, rounding=ROUND_HALF_UP))


def to_number(text, default=None):
    """Parses a form value. Returns `default` when blank, raises ValueError when not a number."""
    if text is None:
        return default
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        text = str(text).strip().replace(",", "")
        if text == "":
            return default
        value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("not a finite number")
    return value


def clean_text(text, max_len=200):
    return " ".join(str(text or "").split())[:max_len]
