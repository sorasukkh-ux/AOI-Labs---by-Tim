"""Update 65: start the system again with clean transactions - keeps the customers and every master.

  python -m scripts.clear_transactions            # shows what would be deleted, deletes nothing
  python -m scripts.clear_transactions --yes      # backs up, then deletes (asks you to type CLEAR)
  python -m scripts.clear_transactions --yes --machine-data   # also the machine / meter / health history

Deleted (all branches):
  milling orders and their history, buying / selling orders and their history, QR prints, LINE messages of orders,
  stock on hand and its history, the account ledger and its history; the running numbers of orders, stock, ledger,
  LINE messages and status events start again from 1.
Kept:
  customers (with their LINE link), users, roles and rights, menus, branches and system parameters, locations,
  materials, prices and their history, promotions, milling yields and milling tests, machines and meters, LINE
  message texts, market and export prices, price-signal settings, investment / CAPEX / budget, AI questions.
  With --machine-data also deleted: machine commands, telemetry, machine status log, meter readings, server health
  checks, the app log and the API counters.

Before deleting, every collection that is cleared is saved as JSON in backups/clear-<date-time>/ (not sent to GitHub).
Stop the server first (Ctrl+C), run this, then start it again with bash start.sh.
"""
import json
import os
import sys
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ORDERS = ["AOITS_ORDMILLING", "AOILOG_ORDMILLING", "AOILOG_MILLSTATUS", "AOITS_ORDBUYSELL", "AOILOG_ORDBUYSELL",
          "AOITS_QR_PRINT", "AOITS_NOTIFY", "AOINV_ONHAND", "AOILOG_ONHAND", "AOITS_ACCOUNT", "AOILOG_ACCOUNT"]
MACHINE_DATA = ["AOITS_MACHINE_CMD", "AOITS_MACHINE_TELE", "AOILOG_MACHINE", "AOITS_METER_READ", "AOITS_HEALTH",
                "AOITS_SYSLOG", "AOITS_MONITOR"]
# running numbers that belong to the deleted records: milling (ML-day, queue no.), buying/selling (BS-day), stock
# (IV-day), ledger (AC-day), LINE messages (NT-order / customer), status events (EV-order)
COUNTER_PREFIXES = ["ML-", "BS-", "IV-", "AC-", "NT-", "EV-"]
MACHINE_COUNTERS = ["MC-", "MR-"]


def plan(db, machine_data=False):
    names = ORDERS + (MACHINE_DATA if machine_data else [])
    prefixes = COUNTER_PREFIXES + (MACHINE_COUNTERS if machine_data else [])
    counts = {n: db[n].count_documents({}) for n in names}
    counters = [c["COUNTER_KEY"] for c in db["AOISYS_COUNTER"].find({}, {"_id": 0, "COUNTER_KEY": 1})
                if any(str(c.get("COUNTER_KEY", "")).startswith(p) for p in prefixes)]
    return counts, counters


def _dump(rows):
    try:
        from bson import json_util
        return json_util.dumps(rows, ensure_ascii=False)
    except ImportError:
        return json.dumps(rows, ensure_ascii=False, default=str)


def backup(db, names, counters, folder):
    os.makedirs(folder, exist_ok=True)
    for n in names:
        with open(os.path.join(folder, n + ".json"), "w", encoding="utf-8") as fh:
            fh.write(_dump(list(db[n].find({}))))
    with open(os.path.join(folder, "AOISYS_COUNTER.json"), "w", encoding="utf-8") as fh:
        fh.write(_dump(list(db["AOISYS_COUNTER"].find({"COUNTER_KEY": {"$in": counters}}))))


def clear(db, machine_data=False, backup_dir=None, log=print):
    """Deletes the transactions (see the top of this file). Returns {collection: deleted}. Must run in an app context."""
    counts, counters = plan(db, machine_data)
    if backup_dir:
        backup(db, list(counts), counters, backup_dir)
        log("Backup saved in %s" % backup_dir)
    done = {}
    for n in counts:
        done[n] = db[n].delete_many({}).deleted_count
    if counters:
        db["AOISYS_COUNTER"].delete_many({"COUNTER_KEY": {"$in": counters}})
    done["AOISYS_COUNTER (running numbers)"] = len(counters)
    return done


def main():
    from app import create_app, DatabaseUnavailable
    from app.db import get_db
    machine_data = "--machine-data" in sys.argv
    try:
        app = create_app()
    except DatabaseUnavailable as error:
        print("ERROR: %s" % error)
        return 1
    with app.app_context():
        if app.config.get("DB_BACKEND") == "memory":
            print("The server uses the demo memory database (AOI_DB=memory): there is nothing saved to clear.")
            return 1
        db = get_db()
        counts, counters = plan(db, machine_data)
        print("Database: %s" % app.config.get("MONGO_DB"))
        print("To delete:")
        for n, c in counts.items():
            print("  %-22s %8d records" % (n, c))
        print("  %-22s %8d running numbers" % ("AOISYS_COUNTER", len(counters)))
        print("Kept: customers, users, roles, menus, branches, locations, materials, prices, machines and all other masters.")
        if "--yes" not in sys.argv:
            print("\nNothing was deleted. Run again with --yes to delete (a backup is made first).")
            return 0
        answer = input("\nType CLEAR to delete these records: ")
        if answer.strip() != "CLEAR":
            print("Cancelled. Nothing was deleted.")
            return 1
        folder = os.path.join("backups", "clear-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        done = clear(db, machine_data, folder)
        for n, c in done.items():
            print("  deleted %-34s %d" % (n, c))
        print("Done. Start the server again: bash start.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
