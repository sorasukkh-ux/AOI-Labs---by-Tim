"""Update 137: a paddy moisture meter with a data output (USB / RS-232 / Bluetooth serial) sends its readings to AOI.

The meter is a device on Rice Quality Grading (Add device, e.g. "Moisture meter", then New key). Staff choose the
order and this device on Update Buy/Sell Status (Grade a sample) exactly like the grader; the next reading is kept
with that order and the paddy weight is deducted to 15% (models/moisture.py). Readings wait in moisture_queue/ when
AOI cannot be reached and go when it can.

    pip install pyserial                               (on the Pi / mini PC the meter is plugged into)
    python3 scripts/aoi_moisture.py --init             address, device id, key -> moisture.json (gitignored)
    python3 scripts/aoi_moisture.py --list-ports       which serial port the meter is on
    python3 scripts/aoi_moisture.py                    listen on the port and send every reading
    python3 scripts/aoi_moisture.py --value 17.8       send one reading by hand (a test without the meter)
    python3 scripts/aoi_moisture.py --flush            send what waits in the queue

The meter model is not chosen yet. Most meters print a line of text per measurement; a number between 5 and 40 in the line
(one with a decimal point first) is taken as the moisture %. Settings in moisture.json: port, baud (9600), pattern (a regular
expression whose first group is the %). Once the model is known, set the pattern from its manual.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aoi_grader as g                    # the same device API, offline queue and settings file handling

CONFIG = "moisture.json"
DEFAULTS = dict(g.DEFAULTS, queue="moisture_queue", port="", baud=9600, pattern=r"(\d{1,2}(?:[.,]\d)?)")
LOW, HIGH = 5.0, 40.0
VERSION = "1.0"


def load(path=CONFIG):
    """Only these settings come from the file (--init writes the grader's other defaults there too)."""
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        cfg.update({k: saved[k] for k in ("url", "grader", "key", "port", "baud", "pattern") if k in saved})
    return cfg


def reading(line, pattern=DEFAULTS["pattern"]):
    """The moisture % in one line of the meter's output, or None. A number with a decimal point wins (17.8 rather
    than the sample counter No.12); then the first whole number in range."""
    found = []
    for m in re.finditer(pattern, line or ""):
        try:
            value = float(m.group(1).replace(",", "."))
        except (ValueError, IndexError):
            continue
        if LOW <= value <= HIGH:
            found.append((0 if re.search(r"[.,]", m.group(1)) else 1, round(value, 1)))
    return min(found, key=lambda f: f[0])[1] if found else None


def send(cfg, pct, number=1, post=g.call, say=print):
    """Queues one reading and sends the queue. Returns (sent, waiting)."""
    body = {"moisture": pct, "version": "M" + VERSION,
            "sample": "M-%s-%02d" % (datetime.now().strftime("%y%m%d-%H%M%S"), number % 100),
            "time": datetime.now(timezone.utc).isoformat()}
    g.enqueue(cfg, body)
    say("Moisture %.1f%%" % pct)
    return g.flush(cfg, send=post, say=say)


def listen(cfg, say=print):
    import serial                                       # pyserial, only needed here
    n = 0
    say("Listening on %s at %s baud (Ctrl+C to stop)" % (cfg["port"], cfg["baud"]))
    with serial.Serial(cfg["port"], int(cfg["baud"]), timeout=5) as port:
        while True:
            raw = port.readline().decode("ascii", "replace").strip()
            if not raw:
                if g.waiting(cfg):
                    g.flush(cfg, say=say)
                continue
            pct = reading(raw, cfg["pattern"])
            if pct is None:
                say("  ignored: %r" % raw[:60])
                continue
            n += 1
            send(cfg, pct, n, say=say)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--init", action="store_true")
    p.add_argument("--list-ports", action="store_true")
    p.add_argument("--value", type=float)
    p.add_argument("--flush", action="store_true")
    a = p.parse_args()
    if a.init:
        g.init_config(CONFIG)
        return 0
    if a.list_ports:
        from serial.tools import list_ports
        for port in list_ports.comports():
            print(port.device, "-", port.description)
        return 0
    cfg = load()
    if not cfg["key"] or not cfg["grader"]:
        print("Run --init first.")
        return 1
    if a.flush:
        print("sent %d, waiting %d" % g.flush(cfg))
        return 0
    if a.value is not None:
        if not (LOW <= a.value <= HIGH):
            print("Moisture must be %.0f-%.0f%%" % (LOW, HIGH))
            return 1
        send(cfg, round(a.value, 1))
        return 0
    if not cfg["port"]:
        print("Set \"port\" in %s (see --list-ports)." % CONFIG)
        return 1
    try:
        listen(cfg)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
