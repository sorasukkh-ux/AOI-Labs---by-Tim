"""Test ALL machines at once, with no device keys to copy (update 45).

    python3 scripts/sim_all.py                    # factory 2 (the branch of the sample data)
    python3 scripts/sim_all.py --branch factory1

Run it in a second terminal while the app runs (bash start.sh in the first). It
  1. finds the machines of the branch that are in use and have remote control (main motor 1, main motor 2 ...),
  2. gives each a device key directly in the database - kept in .gateways.local.json (private, not in git) and
     reused next time while it still matches, so normally nothing changes,
  3. starts a virtual inverter for each and ONE gateway for all of them.
Then ON / OFF / speed work on Hardware Control and the SCADA for every motor.

Test commands (type, then Enter):  "1 load 120"  "2 jam"  "1 clear"  "2 fault"  "status"   (1, 2 ... = the motor number
in the list printed at the start). Ctrl+C stops everything.

Only for testing: it writes keys straight into the database. A machine whose key belongs to a real device that is
online is skipped (unless --force). For the Raspberry Pi at the mill:  --export pi.json  writes a gateway file for
`python3 scripts/aoi_gateway.py --config pi.json` (RS-485 port, station 1, 2 ... - check them).
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
KEYS_FILE = os.path.join(ROOT, ".gateways.local.json")


def load_keys():
    try:
        with open(KEYS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_keys(keys):
    with open(KEYS_FILE, "w", encoding="utf-8") as fh:
        json.dump(keys, fh, indent=1)
    os.chmod(KEYS_FILE, 0o600)


def prepare(branch, force=False, user="sim_all"):
    """[(machine record, key)] for the branch's machines in use with remote control (not the mill selector)."""
    from app import create_app
    from app.models.automation import Machine, SELECTOR, _hash
    app = create_app()
    keys, out = load_keys(), []
    with app.app_context():
        for m in Machine.for_branch(branch, active_only=True):
            if not m.get("CONTROL_FLG") or m.get("MACHINE_TYPE") == SELECTOR:
                continue
            mid, key = m["MACHINE_ID"], keys.get(m["MACHINE_ID"])
            if not (key and m.get("DEVICE_KEY_HASH") == _hash(key)):
                if m.get("DEVICE_KEY_HASH") and Machine.is_online(m) and not force:
                    print("  %s: skipped - its key belongs to a device that is online now (use --force to replace it)" % mid)
                    continue
                key = Machine.issue_key(branch, mid, user)
                keys[mid] = key
            out.append((m, key))
        save_keys(keys)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--branch", default="factory2")
    parser.add_argument("--url", default=os.environ.get("AOI_URL", "http://127.0.0.1:%s" % os.environ.get("AOI_PORT", "5000")))
    parser.add_argument("--force", action="store_true", help="also replace the key of a device that is online")
    parser.add_argument("--export", metavar="FILE", help="write a gateway file for the Raspberry Pi instead of simulating")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="--export: the RS-485 port of the Pi")
    args = parser.parse_args()
    machines = prepare(args.branch, args.force)
    if not machines:
        print("No machine in use with remote control on %s (Hardware Control > Machine setup)." % args.branch)
        return 1
    if args.export:
        cfg = {"url": args.url, "machines": [{"machine": m["MACHINE_ID"], "key": key, "modbus": "rtu:%s:19200:E:1" % args.port,
                                               "slave": n + 1, "min_hz": m.get("SPEED_MIN_HZ") or 30, "max_hz": m.get("SPEED_MAX_HZ") or 50}
                                              for n, (m, key) in enumerate(machines)]}
        with open(args.export, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1)
        os.chmod(args.export, 0o600)
        print("Wrote %s for %d machine(s): copy it to the Pi, check the url, port and station numbers, then run\n"
              "  python3 scripts/aoi_gateway.py --config %s" % (args.export, len(machines), os.path.basename(args.export)))
        return 0
    import aoi_gateway
    print("Simulating %d machine(s) of %s against %s:" % (len(machines), args.branch, args.url))
    for n, (m, _key) in enumerate(machines):
        print("  %d  %s  %s" % (n + 1, m["MACHINE_ID"], m["MACHINE_NAME"]))
    sims, stop, _threads = aoi_gateway.run_many(args.url, [
        {"machine": m["MACHINE_ID"], "key": key, "slave": 1, "min_hz": m.get("SPEED_MIN_HZ") or 30, "max_hz": m.get("SPEED_MAX_HZ") or 50,
         "rated_amp": m.get("RATED_AMP")} for m, key in machines], simulate=True)
    order = [m["MACHINE_ID"] for m, _k in machines]
    print('Test commands: "1 load 120", "2 jam", "1 clear", "2 fault", "status". Ctrl+C stops everything.')
    try:
        if sys.stdin and sys.stdin.isatty():
            for line in sys.stdin:
                words = line.strip().lower().split()
                if not words:
                    continue
                if words[0] == "status":
                    for mid in order:
                        print("  %s  %s" % (mid, sims[mid].status_line()))
                    continue
                if not words[0].isdigit() or not (1 <= int(words[0]) <= len(order)) or len(words) < 2:
                    print('  e.g. "1 load 120", "2 jam", "1 clear", "2 fault", "status"')
                    continue
                vfd = sims[order[int(words[0]) - 1]]
                with vfd.lock:
                    if words[1] == "load" and len(words) > 2 and words[2].replace(".", "", 1).isdigit():
                        vfd.load = float(words[2])
                    elif words[1] == "jam":
                        vfd.load = 250.0
                    elif words[1] == "clear":
                        vfd.load = 60.0
                    elif words[1] == "fault":
                        vfd.trip(0x10)
                print("  %s  %s" % (order[int(words[0]) - 1], vfd.status_line()))
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
