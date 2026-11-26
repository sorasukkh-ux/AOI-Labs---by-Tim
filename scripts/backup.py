"""Update 136: database backup and restore for the AOI server (Python only - no mongodump needed).

    python3 -m scripts.backup                       one backup now: backups/db-YYYYMMDD-HHMM.json.gz (kept 14 days)
    python3 -m scripts.backup --list                the backups there are
    python3 -m scripts.backup --restore FILE --yes  put a backup back (asks you to type RESTORE; every AOI collection
                                                    in the file is replaced). Stop AOI first:  sudo systemctl stop aoi

The nightly backup at 23:00 is installed by  bash start.sh install-service  (systemd timer aoi-backup).
Copy the newest file to the USB drive once a week: a backup on the same disk does not survive a broken disk.
backups/ is not sent to GitHub (.gitignore).
"""
import argparse
import gzip
import json
import os
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FOLDER = "backups"
KEEP_DAYS = 14
PREFIXES = ("AOI",)                       # AOI collections only (AOIMS_, AOITS_, AOILOG_, AOINV_ ...)


def _dumps(value):
    try:
        from bson import json_util
        return json_util.dumps(value, json_options=json_util.RELAXED_JSON_OPTIONS)
    except ImportError:                    # memory database in tests: no bson, plain JSON with dates as text
        return json.dumps(value, default=lambda v: {"$date": v.isoformat()} if isinstance(v, datetime) else str(v))


def _loads(text):
    try:
        from bson import json_util
        return json_util.loads(text)
    except ImportError:
        def hook(d):
            if set(d) == {"$date"} and isinstance(d["$date"], str):
                return datetime.fromisoformat(d["$date"])
            return d
        return json.loads(text, object_hook=hook)


def names(db):
    return sorted(n for n in db.list_collection_names() if n.startswith(PREFIXES))


def backup(db, folder=FOLDER, keep_days=KEEP_DAYS, now=None):
    """Writes one backup file. Returns (path, collections, documents)."""
    os.makedirs(folder, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(folder, "db-%s.json.gz" % stamp)
    total, cols = 0, names(db)
    with gzip.open(path + ".tmp", "wt", encoding="utf-8") as f:
        f.write('{"version": 1, "taken": "%s", "collections": {' % stamp)
        for i, name in enumerate(cols):
            docs = list(db[name].find({}))
            for d in docs:
                d.pop("_id", None)
            total += len(docs)
            f.write('%s"%s": %s' % ("," if i else "", name, _dumps(docs)))
        f.write("}}")
    os.replace(path + ".tmp", path)                       # a power cut never leaves half a file
    limit = time.time() - keep_days * 86400
    for old in os.listdir(folder):
        full = os.path.join(folder, old)
        if old.startswith("db-") and old.endswith(".json.gz") and full != path and os.path.getmtime(full) < limit:
            os.remove(full)
    return path, len(cols), total


def restore(db, path):
    """Replaces every collection in the file with its saved documents. Returns (collections, documents)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = _loads(f.read())
    cols = data.get("collections") or {}
    total = 0
    for name, docs in cols.items():
        if not name.startswith(PREFIXES):
            continue
        db[name].delete_many({})
        if docs:
            db[name].insert_many(docs)
        total += len(docs)
    return len(cols), total


def _db():
    from app.config import Config
    from pymongo import MongoClient
    client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=True)
    client.admin.command("ping")
    return client[Config.MONGO_DB]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--restore", metavar="FILE")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if args.list:
        files = sorted(f for f in os.listdir(FOLDER) if f.startswith("db-")) if os.path.isdir(FOLDER) else []
        for f in files:
            print("%s  %.1f MB" % (f, os.path.getsize(os.path.join(FOLDER, f)) / 1e6))
        print("%d backup(s) in %s/" % (len(files), FOLDER))
        return 0
    try:
        db = _db()
    except Exception as error:
        print("MongoDB is not reachable: %s" % str(error).splitlines()[0][:200])
        return 1
    if args.restore:
        if not args.yes or input("Every AOI collection in %s will be replaced. Type RESTORE: " % args.restore).strip() != "RESTORE":
            print("Nothing changed.")
            return 1
        safety = backup(db)[0]                            # what is there now, in case the wrong file was chosen
        cols, docs = restore(db, args.restore)
        print("Restored %d collections, %d documents (the data before the restore is in %s)." % (cols, docs, safety))
        return 0
    path, cols, docs = backup(db)
    print("Backup %s: %d collections, %d documents." % (path, cols, docs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
