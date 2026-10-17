"""AOI gateway: links Hardware Control to the motor's inverter (VFD) by Modbus (update 30).

This is the program that will run on the Raspberry Pi (or any small PC) at the mill. Today it can run in the
Codespace against the virtual inverter. Standard library only (pyserial is needed only for a real RS-485 port).

    App (Hardware Control)  <--HTTPS, device key-->  this gateway  <--Modbus-->  inverter  -->  motor

Try it without hardware (in a second terminal, while the app runs):
    python3 scripts/aoi_gateway.py --machine F2-MOTOR --key <device key> --simulate
  (the device key is issued on Hardware Control > Machine setup > New key; it is shown once)
  Update 45 - all machines at once, no key copying:  python3 scripts/sim_all.py   (see that file)

Several machines with ONE gateway (update 45), e.g. both main motors on one RS-485 bus (station 1 and 2):
    python3 scripts/aoi_gateway.py --config gateways.json
  gateways.json:  {"url": "https://<server>",
                   "machines": [{"machine": "F2-MOTOR",  "key": "...", "modbus": "rtu:/dev/ttyUSB0:19200:E:1", "slave": 1},
                                {"machine": "F2-MOTOR2", "key": "...", "modbus": "rtu:/dev/ttyUSB0:19200:E:1", "slave": 2}]}
  Machines on the same port share it (one at a time on the wire). Keep the file private (chmod 600).

Later, with the real inverter on RS-485 (USB adapter):
    pip install pyserial
    python3 scripts/aoi_gateway.py --url https://<server> --machine F2-MOTOR --key <key> --modbus rtu:/dev/ttyUSB0:19200:E:1
  or an inverter with Ethernet (Modbus TCP):  --modbus tcp:192.168.1.50:502

What it does, every second:
  * asks the app for commands: START (runs forward at the Hz given), STOP, SPEED (Hz), RESET (fault reset),
    writes them to the inverter, and reports the result (ack);
  * reads the inverter (running, set Hz, output Hz, current, voltage, rpm, fault) and posts it to /telemetry
    every --tele seconds (at once when running or fault changes);
  * safety: speed is kept within --min-hz..--max-hz; there is no reverse (STR is never written); when the app
    cannot be reached for --link-loss seconds while the motor runs, the motor is stopped (unless --keep-running).
The emergency stop, the motor overload relay and the local/remote switch stay hard-wired - this program never
replaces them.

Register map: REG below, modelled on the Mitsubishi inverter Modbus map. Check it against the real inverter's manual
(and set the inverter: Pr.78 = 1 reverse rotation prevention, Modbus RTU protocol, station number, communication
check time) before connecting real hardware.
"""
import argparse
import json
import os
import socket
import struct
import sys
import threading
import time
import urllib.error
import urllib.request

REG = {"reset": 1, "control": 8, "freq_set": 13, "out_hz": 200, "out_amp": 201, "out_volt": 202, "rpm": 205, "fault": 500}
RESET_MAGIC = 0x9696
STF = 0x02                                            # forward run; STR (0x04) is never used: the mill does not reverse
STATUS_RUN, STATUS_FAULT = 0x01, 0x80
FAULT_NAMES = {0x10: "E.OC1", 0x11: "E.OC2", 0x12: "E.OC3", 0x90: "E.THM", 0x91: "E.THT", 0xA7: "E.SER"}


# ---------- Modbus clients ----------
class ModbusError(Exception):
    pass


def _crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


class ModbusTCP:
    def __init__(self, host, port, unit=1, timeout=2.0):
        self.host, self.port, self.unit, self.timeout = host, port, unit, timeout
        self.sock, self.tid = None, 0
        self.lock = threading.Lock()

    def _call(self, pdu):
        for attempt in (1, 2):
            try:
                if self.sock is None:
                    self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                self.tid = (self.tid + 1) & 0xFFFF
                self.sock.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.unit) + pdu)
                head = self._recv(7)
                tid, _proto, length, _unit = struct.unpack(">HHHB", head)
                reply = self._recv(length - 1)
                if reply[0] & 0x80:
                    raise ModbusError("exception code %d" % reply[1])
                return reply
            except (OSError, struct.error) as error:
                self.close()
                if attempt == 2:
                    raise ModbusError("no answer from %s:%d (%s)" % (self.host, self.port, error))

    def _recv(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise OSError("connection closed")
            data += chunk
        return data

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def read(self, addr, count):
        reply = self._call(struct.pack(">BHH", 3, addr, count))
        return list(struct.unpack(">" + "H" * count, reply[2:2 + 2 * count]))

    def write(self, addr, value):
        self._call(struct.pack(">BHH", 6, addr, value & 0xFFFF))


class ModbusRTU(ModbusTCP):
    """RS-485 through a USB adapter. Needs pyserial. Not tested on hardware yet: check with the real inverter."""

    def __init__(self, port, baud=19200, parity="E", stop=1, unit=1, timeout=1.0):
        try:
            import serial                              # noqa: F401
        except ImportError:
            sys.exit("RS-485 needs pyserial: pip install pyserial")
        import serial
        self.serial = serial.Serial(port, baudrate=baud, parity=parity, stopbits=stop, bytesize=8, timeout=timeout)
        self.unit = unit
        self.lock = threading.Lock()

    def _call(self, pdu):
        frame = bytes([self.unit]) + pdu
        self.serial.reset_input_buffer()
        self.serial.write(frame + _crc16(frame))
        head = self.serial.read(2)
        if len(head) < 2:
            raise ModbusError("no answer from the inverter (check wiring, station number, baud rate)")
        if head[1] & 0x80:
            rest = self.serial.read(3)
            raise ModbusError("exception code %d" % (rest[0] if rest else -1))
        if head[1] == 3:
            n = self.serial.read(1)
            body = n + self.serial.read(n[0] + 2) if n else b""
        else:
            body = self.serial.read(6)
        reply = head + body
        if len(reply) < 4 or _crc16(reply[:-2]) != reply[-2:]:
            raise ModbusError("bad answer (CRC)")
        return reply[1:-2]

    def close(self):
        pass


class Station:
    """One inverter (station number) on a bus; several stations can share one bus (update 45)."""

    def __init__(self, bus, unit):
        self.bus, self.unit = bus, unit

    def read(self, addr, count):
        with self.bus.lock:
            self.bus.unit = self.unit
            return self.bus.read(addr, count)

    def write(self, addr, value):
        with self.bus.lock:
            self.bus.unit = self.unit
            return self.bus.write(addr, value)


_BUSES = {}


def connect(spec, unit):
    """The inverter at `unit` on the bus `spec` (tcp:<host>:<port> or rtu:<port>:<baud>:<parity>:<stop>)."""
    if spec not in _BUSES:
        kind, _, rest = spec.partition(":")
        if kind == "tcp":
            host, _, port = rest.rpartition(":")
            _BUSES[spec] = ModbusTCP(host or "127.0.0.1", int(port or 502), unit)
        elif kind == "rtu":
            parts = rest.split(":")
            _BUSES[spec] = ModbusRTU(parts[0], int(parts[1]) if len(parts) > 1 else 19200, parts[2] if len(parts) > 2 else "E",
                                     int(parts[3]) if len(parts) > 3 else 1, unit)
        else:
            sys.exit("--modbus must be tcp:<host>:<port> or rtu:<port>:<baud>:<parity>:<stop>")
    return Station(_BUSES[spec], unit)


# ---------- the inverter ----------
class Inverter:
    def __init__(self, bus, min_hz, max_hz):
        self.bus, self.min_hz, self.max_hz = bus, min_hz, max_hz

    def hz(self, value):
        return max(self.min_hz, min(self.max_hz, float(value)))

    def run(self, value):
        self.bus.write(REG["freq_set"], int(round(self.hz(value) * 100)))
        self.bus.write(REG["control"], STF)

    def stop(self):
        self.bus.write(REG["control"], 0)

    def speed(self, value):
        self.bus.write(REG["freq_set"], int(round(self.hz(value) * 100)))

    def reset(self):
        self.bus.write(REG["reset"], RESET_MAGIC)

    def read(self):
        status = self.bus.read(REG["control"], 1)[0]
        set_hz = self.bus.read(REG["freq_set"], 1)[0] / 100.0
        mon = self.bus.read(REG["out_hz"], REG["rpm"] - REG["out_hz"] + 1)
        fault = self.bus.read(REG["fault"], 1)[0]
        return {"running": bool(status & STATUS_RUN), "set_hz": round(set_hz, 2), "out_hz": round(mon[0] / 100.0, 2),
                "amp": round(mon[1] / 100.0, 2), "volt": round(mon[2] / 10.0, 1), "rpm": mon[REG["rpm"] - REG["out_hz"]],
                "fault": (FAULT_NAMES.get(fault, "E.%02X" % fault) if (fault and status & STATUS_FAULT) else "")}


# ---------- the app ----------
class App:
    def __init__(self, url, machine, key):
        self.base, self.machine, self.key = url.rstrip("/"), machine.upper(), key

    def call(self, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                                     headers={"X-Device-Key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def commands(self):
        return self.call("/api/v1/machines/%s/commands" % self.machine).get("commands", [])

    def ack(self, cmd_id, ok, result):
        return self.call("/api/v1/commands/%s/ack" % cmd_id, {"ok": ok, "result": result[:200]})

    def telemetry(self, values):
        return self.call("/api/v1/machines/%s/telemetry" % self.machine, values)

    def status(self, status, note):
        return self.call("/api/v1/machines/%s/status" % self.machine, {"status": status, "note": note})


_PRINT = threading.Lock()


def log(text, tag=None):
    with _PRINT:
        print(time.strftime("%H:%M:%S"), ("[%s] " % tag if tag else "") + text, flush=True)


def carry_out(inverter, cmd):
    kind, value = cmd["command"], cmd.get("value")
    if kind == "START":
        inverter.run(value if value is not None else inverter.max_hz)
        return "run forward %.1f Hz" % inverter.hz(value if value is not None else inverter.max_hz)
    if kind == "STOP":
        inverter.stop()
        return "stop"
    if kind == "SPEED":
        if value is None:
            raise ModbusError("no speed given")
        inverter.speed(value)
        return "speed %.1f Hz" % inverter.hz(value)
    if kind == "RESET":
        inverter.reset()
        return "fault reset"
    raise ModbusError("unknown command %s" % kind)


def run(machine, key, url, modbus, slave=1, min_hz=30.0, max_hz=50.0, poll=1.0, tele=2.0, link_loss=30.0,
        keep_running=False, stop=None, tag=None):
    """The gateway loop for one machine, until `stop` (a threading.Event) is set."""
    stop = stop or threading.Event()
    say = lambda text: log(text, tag)
    app = App(url, machine, key)
    inverter = Inverter(connect(modbus, slave), min_hz, max_hz)
    say("gateway for %s: app %s, inverter %s station %d (speed %.0f-%.0f Hz)" % (app.machine, app.base, modbus, slave, min_hz, max_hz))
    last_app_ok, last_post, last_sent, inverter_down = time.monotonic(), 0.0, None, False
    values = {"running": False}
    while not stop.is_set():
        now = time.monotonic()
        # 1. read the inverter
        try:
            values = inverter.read()
            if inverter_down:
                say("inverter answers again")
            inverter_down = False
        except ModbusError as error:
            if not inverter_down:
                say("inverter: %s" % error)
                try:
                    app.status("F", "inverter not answering: %s" % error)
                except (urllib.error.URLError, OSError, ValueError):
                    pass
            inverter_down = True
        # 2. commands from the app
        try:
            for cmd in app.commands():
                try:
                    if inverter_down:
                        raise ModbusError("inverter not answering")
                    result = carry_out(inverter, cmd)
                    app.ack(cmd["id"], True, result)
                    say("%s %s -> %s" % (cmd["id"], cmd["command"], result))
                except ModbusError as error:
                    app.ack(cmd["id"], False, str(error))
                    say("%s %s FAILED: %s" % (cmd["id"], cmd["command"], error))
            last_app_ok = now
            # 3. live values
            state = (values.get("running"), values.get("fault"))
            if not inverter_down and (now - last_post >= tele or state != last_sent):
                reply = app.telemetry(values)
                inverter.min_hz, inverter.max_hz = reply.get("min_hz", inverter.min_hz), reply.get("max_hz", inverter.max_hz)
                last_post, last_sent = now, state
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                say("the app refused the device key (%s): issue a new key on Hardware Control and update the key" % error.code)
            if now - last_app_ok > link_loss and values.get("running") and not keep_running and not inverter_down:
                inverter.stop()
                say("app unreachable for %.0f s: motor STOPPED for safety (%s)" % (now - last_app_ok, error))
                last_app_ok = now
        except (urllib.error.URLError, OSError, ValueError) as error:
            if now - last_app_ok > link_loss and values.get("running") and not keep_running and not inverter_down:
                inverter.stop()
                say("app unreachable for %.0f s: motor STOPPED for safety (%s)" % (now - last_app_ok, error))
                last_app_ok = now                           # log once per period
        stop.wait(poll)
    return 0


def run_many(url, machines, simulate=False, poll=1.0, tele=2.0, link_loss=30.0, keep_running=False, sim_port=5020):
    """Update 45: one gateway process for several machines (a thread each). `machines`: dicts with machine, key,
    modbus (default: simulated tcp:127.0.0.1:<sim_port + n>), slave, min_hz, max_hz. Returns {machine: virtual inverter}
    when simulating (for test commands) and the stop event."""
    stop, sims, threads = threading.Event(), {}, []
    for n, m in enumerate(machines):
        modbus, slave = m.get("modbus"), int(m.get("slave") or 1)
        if simulate:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import vfd_sim
            port = sim_port + n
            modbus = "tcp:127.0.0.1:%d" % port
            sims[m["machine"].upper()] = vfd_sim.start("127.0.0.1", port, slave, console=False,
                                                       **({"rated_amp": float(m["rated_amp"])} if m.get("rated_amp") else {}))
        if not modbus:
            sys.exit("%s: no modbus given" % m["machine"])
        t = threading.Thread(target=run, daemon=True, kwargs=dict(
            machine=m["machine"], key=m["key"], url=url, modbus=modbus, slave=slave, min_hz=float(m.get("min_hz") or 30),
            max_hz=float(m.get("max_hz") or 50), poll=poll, tele=tele, link_loss=link_loss, keep_running=keep_running,
            stop=stop, tag=m["machine"].upper()))
        t.start()
        threads.append(t)
    return sims, stop, threads


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("AOI_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--machine", help="machine ID on Hardware Control, e.g. F2-MOTOR")
    parser.add_argument("--key", default=os.environ.get("AOI_DEVICE_KEY"), help="device key (or AOI_DEVICE_KEY)")
    parser.add_argument("--config", help="update 45: a JSON file with several machines (see the top of this file)")
    parser.add_argument("--modbus", default="tcp:127.0.0.1:5020")
    parser.add_argument("--slave", type=int, default=1, help="inverter station number")
    parser.add_argument("--simulate", action="store_true", help="start the virtual inverter (scripts/vfd_sim.py) here")
    parser.add_argument("--min-hz", type=float, default=30.0)
    parser.add_argument("--max-hz", type=float, default=50.0)
    parser.add_argument("--poll", type=float, default=1.0, help="seconds between command checks")
    parser.add_argument("--tele", type=float, default=2.0, help="seconds between live value posts")
    parser.add_argument("--link-loss", type=float, default=30.0, help="stop the motor when the app is unreachable this long")
    parser.add_argument("--keep-running", action="store_true", help="do not stop the motor when the app is unreachable")
    args = parser.parse_args()
    if args.config:
        with open(args.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        machines = cfg.get("machines") or []
        if not machines:
            sys.exit("%s: no machines" % args.config)
        sims, stop, _threads = run_many(cfg.get("url") or args.url, machines, simulate=bool(cfg.get("simulate") or args.simulate),
                                        poll=args.poll, tele=args.tele, link_loss=args.link_loss, keep_running=args.keep_running)
        log("%d machine(s) on one gateway. Ctrl+C to stop." % len(machines))
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            stop.set()
            log("gateway stopped")
            return 0
    if not args.machine:
        sys.exit("--machine (or --config) is required")
    if not args.key:
        sys.exit("--key is required (Hardware Control > Machine setup > New key)")
    if args.simulate:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import vfd_sim
        host, _, port = args.modbus.partition(":")[2].rpartition(":")
        vfd_sim.start(host or "127.0.0.1", int(port or 5020), args.slave)
    log("Ctrl+C to stop.")
    try:
        return run(args.machine, args.key, args.url, args.modbus, args.slave, args.min_hz, args.max_hz, args.poll, args.tele,
                   args.link_loss, args.keep_running)
    except KeyboardInterrupt:
        log("gateway stopped (the inverter keeps its last state; it stops by itself after its communication check time)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
