"""Running numbers (order number, customer code). One record per key, increased atomically."""
from .base import col, AFTER, NO_ID


class Counter:
    COL = "AOISYS_COUNTER"

    @classmethod
    def next(cls, key):
        doc = col(cls.COL).find_one_and_update(
            {"COUNTER_KEY": key}, {"$inc": {"SEQ": 1}}, upsert=True, return_document=AFTER, projection=NO_ID)
        return int(doc["SEQ"])

    @classmethod
    def peek(cls, key):
        """The number the next call of next() would return, without taking it (for previews only)."""
        doc = col(cls.COL).find_one({"COUNTER_KEY": key}, NO_ID)
        return int((doc or {}).get("SEQ") or 0) + 1
