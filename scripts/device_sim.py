"""A pretend machine / smart meter that talks to the device API - for trying Hardware Control and
Electricity before real hardware is connected. It uses only the Python standard library.

    python3 scripts/device_sim.py machine HUSK1 <device key>        report Running/Idle and carry out start/stop
    python3 scripts/device_sim.py meter MAIN1 <device key>          send a meter reading every 30 seconds

Options: --url http://127.0.0.1:5000 (default)  --every 10 (seconds between polls)
Stop with Ctrl+C. Device keys are issued on the Hardware Control and Electricity screens (shown once).
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request


def call(base, path, key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"X-Device-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"ok": False, "http": error.code, "body": error.read().decode("utf-8", "replace")[:200]}


def run_machine(base, machine, key, every):
    status = "I"
    print(call(base, "/api/v1/machines/%s/status" % machine, key, {"status": status, "note": "simulator started"}))
    while True:
        for cmd in call(base, "/api/v1/machines/%s/commands" % machine, key).get("commands", []):
            print("command:", cmd)
            time.sleep(1)                                   # the "machine" does the work
            status = "R" if cmd["command"] == "START" else ("I" if cmd["command"] == "STOP" else status)
            print(call(base, "/api/v1/commands/%s/ack" % cmd["id"], key, {"ok": True, "result": "simulated"}))
        if random.random() < 0.03:                          # now and then a fault, fixed again on the next poll
            print(call(base, "/api/v1/machines/%s/status" % machine, key, {"status": "F", "note": "simulated fault"}))
            time.sleep(every)
            print(call(base, "/api/v1/machines/%s/status" % machine, key, {"status": status, "note": "fault cleared"}))
        time.sleep(every)


def run_meter(base, meter, key, every, start):
    kwh = start
    while True:
        kwh += random.uniform(0.5, 3.0)
        print(call(base, "/api/v1/meters/%s/readings" % meter, key, {"kwh": round(kwh, 1)}))
        time.sleep(every)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("kind", choices=("machine", "meter"))
    parser.add_argument("device_id")
    parser.add_argument("key")
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--every", type=float, default=10.0)
    parser.add_argument("--start-kwh", type=float, default=None, help="meter: first reading (default: last reading + a little)")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    try:
        if args.kind == "machine":
            run_machine(base, args.device_id.upper(), args.key, args.every)
        else:
            run_meter(base, args.device_id.upper(), args.key, max(args.every, 30.0) if args.every == 10.0 else args.every,
                      args.start_kwh if args.start_kwh is not None else 1000.0)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
