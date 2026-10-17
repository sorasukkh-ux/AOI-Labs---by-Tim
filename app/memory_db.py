"""A small in-memory stand-in for the parts of PyMongo this project uses.

It exists so the automated tests (and a quick demo with AOI_DB=memory) can run without a MongoDB
server. It is NOT a database: everything is lost when the process stops. The models only call the
methods implemented here, with the same arguments PyMongo takes, so the same model code runs on both.
"""
import copy
import re
import uuid


class DuplicateKeyError(Exception):
    pass


class _Result:
    def __init__(self, **values):
        self.__dict__.update(values)


def _get(doc, key):
    return doc.get(key)


def _cmp_ok(value, op, operand):
    if op == "$in":
        return value in operand
    if op == "$nin":
        return value not in operand
    if op == "$ne":
        return value != operand
    if op == "$exists":
        return (value is not None) == bool(operand)
    if value is None:
        return False
    if op == "$gt":
        return value > operand
    if op == "$gte":
        return value >= operand
    if op == "$lt":
        return value < operand
    if op == "$lte":
        return value <= operand
    raise NotImplementedError("operator %s is not supported by the memory database" % op)


def _matches(doc, flt):
    for key, cond in (flt or {}).items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
            continue
        if key == "$and":
            if not all(_matches(doc, sub) for sub in cond):
                return False
            continue
        value = _get(doc, key)
        if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
            if "$regex" in cond:
                flags = re.IGNORECASE if "i" in cond.get("$options", "") else 0
                if value is None or not re.search(cond["$regex"], str(value), flags):
                    return False
            for op, operand in cond.items():
                if op in ("$regex", "$options"):
                    continue
                if not _cmp_ok(value, op, operand):
                    return False
        elif value != cond:
            return False
    return True


def _project(doc, projection):
    out = copy.deepcopy(doc)
    if projection:
        if any(v for k, v in projection.items() if k != "_id"):      # inclusion list
            keep = {k for k, v in projection.items() if v}
            out = {k: v for k, v in out.items() if k in keep or (k == "_id" and projection.get("_id", 1))}
        else:
            for k, v in projection.items():
                if not v:
                    out.pop(k, None)
    return out


def _sort_key(value):
    # None sorts first, as in MongoDB
    return (0, 0) if value is None else (1, value)


class _Cursor:
    def __init__(self, docs, projection):
        self._docs, self._projection = docs, projection

    def sort(self, key_or_list, direction=1):
        spec = [(key_or_list, direction)] if isinstance(key_or_list, str) else list(key_or_list)
        for key, order in reversed(spec):
            self._docs.sort(key=lambda d, k=key: _sort_key(d.get(k)), reverse=order < 0)
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter([_project(d, self._projection) for d in self._docs])


class _Collection:
    def __init__(self, name):
        self.name = name
        self._docs = []
        self._unique = []            # list of key tuples

    # ---- indexes ----
    def create_index(self, keys, unique=False, name=None, **_):
        fields = tuple([keys] if isinstance(keys, str) else [k for k, _order in keys])
        if unique and fields not in self._unique:
            self._unique.append(fields)
        return name or "_".join(fields)

    def _check_unique(self, doc, ignore=None):
        for fields in self._unique:
            key = tuple(doc.get(f) for f in fields)
            for other in self._docs:
                if other is not ignore and tuple(other.get(f) for f in fields) == key:
                    raise DuplicateKeyError("duplicate key in %s: %s" % (self.name, dict(zip(fields, key))))

    # ---- writes ----
    def insert_one(self, doc):
        new = copy.deepcopy(doc)
        new.setdefault("_id", uuid.uuid4().hex)
        self._check_unique(new)
        self._docs.append(new)
        doc["_id"] = new["_id"]          # PyMongo also adds _id to the caller's document
        return _Result(inserted_id=new["_id"])

    def insert_many(self, docs):
        return _Result(inserted_ids=[self.insert_one(d).inserted_id for d in docs])

    def _apply(self, doc, update, inserting=False):
        new = copy.deepcopy(doc)
        for op, values in update.items():
            if op == "$set":
                new.update(copy.deepcopy(values))
            elif op == "$inc":
                for k, v in values.items():
                    new[k] = (new.get(k) or 0) + v
            elif op == "$max":
                for k, v in values.items():
                    if new.get(k) is None or v > new[k]:
                        new[k] = v
            elif op == "$unset":
                for k in values:
                    new.pop(k, None)
            elif op == "$setOnInsert":
                if inserting:
                    new.update(copy.deepcopy(values))
            else:
                raise NotImplementedError("update operator %s is not supported by the memory database" % op)
        return new

    def _update(self, flt, update, upsert, many):
        targets = [d for d in self._docs if _matches(d, flt)]
        if not many:
            targets = targets[:1]
        for doc in targets:
            new = self._apply(doc, update)
            self._check_unique(new, ignore=doc)
            doc.clear()
            doc.update(new)
        if not targets and upsert:
            base = {k: v for k, v in (flt or {}).items() if not k.startswith("$") and not isinstance(v, dict)}
            new = self._apply(base, update, inserting=True)
            new.setdefault("_id", uuid.uuid4().hex)
            self._check_unique(new)
            self._docs.append(new)
            return _Result(matched_count=0, modified_count=0, upserted_id=new["_id"]), new
        return _Result(matched_count=len(targets), modified_count=len(targets), upserted_id=None), (targets[0] if targets else None)

    def update_one(self, flt, update, upsert=False):
        return self._update(flt, update, upsert, many=False)[0]

    def update_many(self, flt, update, upsert=False):
        return self._update(flt, update, upsert, many=True)[0]

    def find_one_and_update(self, flt, update, upsert=False, return_document=False, projection=None, **_):
        before = self.find_one(flt)
        _result, doc = self._update(flt, update, upsert, many=False)
        if return_document:                      # ReturnDocument.AFTER is True in PyMongo
            return None if doc is None else _project(doc, projection)
        return None if before is None else _project(before, projection)

    def delete_one(self, flt):
        for doc in self._docs:
            if _matches(doc, flt):
                self._docs.remove(doc)
                return _Result(deleted_count=1)
        return _Result(deleted_count=0)

    def delete_many(self, flt):
        keep = [d for d in self._docs if not _matches(d, flt)]
        removed = len(self._docs) - len(keep)
        self._docs = keep
        return _Result(deleted_count=removed)

    # ---- reads ----
    def find(self, flt=None, projection=None):
        return _Cursor([d for d in self._docs if _matches(d, flt)], projection)

    def find_one(self, flt=None, projection=None, sort=None):
        cursor = self.find(flt, projection)
        if sort:
            cursor.sort(sort)
        for doc in cursor:
            return doc
        return None

    def count_documents(self, flt=None):
        return len([d for d in self._docs if _matches(d, flt)])

    def drop(self):
        self._docs, self._unique = [], []


class MemoryDatabase:
    name = "memory"

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = _Collection(name)
        return self._collections[name]

    def list_collection_names(self):
        return sorted(self._collections)

    def drop_collection(self, name):
        self._collections.pop(name, None)

    def command(self, *_args, **_kwargs):
        return {"ok": 1.0}
