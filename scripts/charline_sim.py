"""A pretend husk furnace + charcoal briquette line (update 131) - for trying the screen before the real line is built.
It uses only the Python standard library.

    python3 scripts/charline_sim.py F2-CHAR <device key>

Options: --url http://127.0.0.1:5000 (default)  --every 2 (seconds between reports)
The device key is issued on Hardware Control > Husk furnace and charcoal line > Settings (shown once), or use
`bash start.sh sim`, which starts this simulator too without a key to copy.

The "physics" is rough: hot air heats while the husk feed and the blower run and cools otherwise; hot char arrives
while the furnace burns and the water spray cools it; the husk bin empties while feeding and is refilled when it is
nearly empty. The real controller replaces `Line.step` with its sensors and contactors; the message stays the same.
It must also keep the rules itself (see the settings in AOI's answer) - AOI is not the safety system.
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

UNITS = ("BLOWER", "FEED", "SCREW", "SPRAY", "MIXER", "EXTRUDER", "DRYER")
AMBIENT = 30.0


def call(base, path, key, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"X-Device-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"ok": False, "http": error.code, "body": error.read().decode("utf-8", "replace")[:200]}
    except (urllib.error.URLError, OSError) as error:
        return {"ok": False, "error": str(error)}


class Line:
    def __init__(self, rng=random):
        self.rng = rng
        self.run = {u: False for u in UNITS}
        self.fault = {u: "" for u in UNITS}
        self.dampers = {"D1": False, "D2": False}
        self.hot, self.char, self.husk = AMBIENT, AMBIENT, 80.0

    def apply(self, answer):
        """Switch to what AOI allows. No answer (AOI or the network is down): the fire is not fed."""
        if not answer.get("ok"):
            self.run["FEED"] = False
            return
        for unit in UNITS:
            self.run[unit] = bool(answer.get("out", {}).get(unit)) and not self.fault[unit]
        self.dampers.update({d: bool(v) for d, v in answer.get("dampers", {}).items()})

    def step(self, seconds):
        k = seconds / 2.0
        burning = self.run["FEED"] and self.run["BLOWER"]
        target = 105.0 if burning else AMBIENT
        self.hot += (target - self.hot) * (0.05 if burning else (0.04 if self.run["BLOWER"] else 0.015)) * k
        char_target = 38.0 if self.run["SPRAY"] else (88.0 if burning else AMBIENT)
        self.char += (char_target - self.char) * (0.14 if self.run["SPRAY"] else 0.06) * k
        if self.run["FEED"]:
            self.husk -= 0.08 * k
        if self.husk < 4:
            self.husk = 100.0                                 # a new load of husk
        self.hot += self.rng.uniform(-0.2, 0.2)
        self.char += self.rng.uniform(-0.2, 0.2)

    def state(self):
        return {"units": {u: {"run": self.run[u], "fault": self.fault[u]} for u in UNITS}, "dampers": dict(self.dampers),
                "hot_c": round(self.hot, 1), "char_c": round(self.char, 1), "husk_pct": round(max(0.0, self.husk), 0)}

    def status_line(self):
        on = [u for u in UNITS if self.run[u]]
        return "hot %.1f C  char %.1f C  husk %.0f %%  running: %s" % (self.hot, self.char, self.husk, ", ".join(on) or "-")


def run(base, line_id, key, every=2.0, stop=None, quiet=False, line=None):
    line = line or Line()
    n = 0
    while not (stop and stop.is_set()):
        answer = call(base, "/api/v1/charlines/%s/state" % line_id, key, line.state())
        line.apply(answer)
        line.step(every)
        n += 1
        if not quiet and (n % 5 == 1 or not answer.get("ok")):
            print("%s  %s%s" % (line_id, line.status_line(), "" if answer.get("ok") else "   AOI: %s" % answer), flush=True)
        if stop:
            stop.wait(every)
        else:
            time.sleep(every)
    return line


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("line")
    parser.add_argument("key")
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--every", type=float, default=2.0)
    args = parser.parse_args()
    try:
        run(args.url.rstrip("/"), args.line.upper(), args.key, max(1.0, args.every))
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
