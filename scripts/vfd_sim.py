"""Virtual inverter (VFD) + motor, for testing the motor control without hardware (update 30).

It answers Modbus TCP like a Mitsubishi FR-E800 inverter would over Modbus, so the gateway program
(scripts/aoi_gateway.py) that will later talk to the real inverter can be tested now. Standard library only.

    python3 scripts/vfd_sim.py                        listen on 127.0.0.1:5020 (Modbus TCP, slave 1)
    python3 scripts/vfd_sim.py --rated-amp 30 --rated-rpm 1450 --accel 5 --decel 5

Usually you do not start it yourself: `python3 scripts/aoi_gateway.py ... --simulate` starts it inside the gateway.

Typed commands while it runs (Enter after each):
    load 80     mechanical load in % of the motor's rating (default 60; above 100 heats the motor -> E.THM)
    jam         sudden jam (load 250%) -> overcurrent trip E.OC2
    fault       trip now (E.OC1)            clear    remove the jam (load back to 60%)
    status      print the live values       help     this list

Register map (holding registers, 4xxxx = address - 40001), modelled on the Mitsubishi inverter Modbus map.
CHECK THE REAL INVERTER'S MANUAL before using it with real hardware; the gateway keeps the map in one place (REG).
    40002  inverter reset (write 0x9696)
    40009  control (write: bit1 STF forward run, bit2 STR reverse run) / status (read: bit0 RUN, bit1 forward,
           bit2 reverse, bit7 fault)
    40014  frequency set, 0.01 Hz
    40201  output frequency, 0.01 Hz    40202 output current, 0.01 A    40203 output voltage, 0.1 V
    40206  motor speed, r/min (simulator)
    40501  latest fault code (0 = none)
Like the real mill: reverse is locked (Pr.78 = 1, reverse rotation prevention), and the inverter stops with E.SER
when nobody talks to it for COMM_TIMEOUT seconds while running (Pr.122 communication check).
"""
import argparse
import socketserver
import struct
import sys
import threading
import time

COMM_TIMEOUT = 10.0
RESET_MAGIC = 0x9696
FAULTS = {0x10: "E.OC1", 0x11: "E.OC2", 0x90: "E.THM", 0xA7: "E.SER"}     # codes used by this simulator
REG_RESET, REG_CONTROL, REG_FREQ = 1, 8, 13
REG_OUT_HZ, REG_OUT_AMP, REG_OUT_VOLT, REG_RPM = 200, 201, 202, 205
REG_FAULT = 500


class VirtualInverter:
    def __init__(self, rated_amp=30.0, rated_rpm=1450, rated_volt=380.0, base_hz=50.0, accel=5.0, decel=5.0, max_hz=60.0):
        self.rated_amp, self.rated_rpm, self.rated_volt, self.base_hz = rated_amp, rated_rpm, rated_volt, base_hz
        self.accel, self.decel, self.max_hz = accel, decel, max_hz
        self.lock = threading.Lock()
        self.control = 0            # bit1 STF, bit2 STR
        self.set_hz = 0.0
        self.out_hz = 0.0
        self.amp = 0.0
        self.load = 60.0            # % of the motor rating
        self.heat = 0.0             # simple motor thermal model (E.THM above 1)
        self.fault = 0
        self.last_talk = time.monotonic()
        self.reverse_warned = False

    # ---------- Modbus registers ----------
    def read(self, addr):
        with self.lock:
            if addr == REG_CONTROL:
                running = not self.fault and (self.out_hz > 0.05 or self.control & 2)
                return (1 if running else 0) | (2 if running else 0) | (0x80 if self.fault else 0)
            return {REG_FREQ: int(round(self.set_hz * 100)), REG_OUT_HZ: int(round(self.out_hz * 100)),
                    REG_OUT_AMP: int(round(self.amp * 100)),
                    REG_OUT_VOLT: int(round(self.rated_volt * min(1.0, self.out_hz / self.base_hz) * 10)),
                    REG_RPM: int(round(self.rpm())), REG_FAULT: self.fault}.get(addr, 0)

    def write(self, addr, value):
        with self.lock:
            if addr == REG_CONTROL:
                if value & 4 and not self.reverse_warned:
                    print("[vfd] reverse run (STR) refused: reverse rotation prevention (Pr.78 = 1)")
                    self.reverse_warned = True
                self.control = value & 2 if not self.fault else 0         # STR is ignored: the mill never reverses
            elif addr == REG_FREQ:
                self.set_hz = max(0.0, min(self.max_hz, value / 100.0))
            elif addr == REG_RESET and value == RESET_MAGIC:
                if self.fault:
                    print("[vfd] reset: %s cleared" % FAULTS.get(self.fault, self.fault))
                self.fault, self.control, self.heat = 0, 0, 0.0
            else:
                return False
            return True

    def talked(self):
        with self.lock:
            self.last_talk = time.monotonic()

    # ---------- motor ----------
    def rpm(self):
        """4-pole motor: synchronous speed 30 x Hz; the slip grows with the load (the rated slip at 100 % load)."""
        sync_base = 30.0 * self.base_hz
        rated_slip = max(0.0, 1.0 - self.rated_rpm / sync_base)
        return 30.0 * self.out_hz * (1.0 - rated_slip * self.load / 100.0) if self.out_hz > 0 else 0.0

    def trip(self, code):
        if not self.fault:
            print("[vfd] TRIP %s at %.1f Hz, %.1f A" % (FAULTS.get(code, code), self.out_hz, self.amp))
        self.fault, self.control = code, 0
        self.out_hz, self.amp = 0.0, 0.0           # the inverter output is cut; the motor coasts to a stop

    def step(self, dt):
        with self.lock:
            if self.control & 2 and not self.fault and time.monotonic() - self.last_talk > COMM_TIMEOUT:
                self.trip(0xA7)                                     # nobody talks to the inverter any more
            target = self.set_hz if (self.control & 2 and not self.fault) else 0.0
            accelerating = target > self.out_hz + 0.01
            if accelerating:
                self.out_hz = min(target, self.out_hz + self.base_hz / self.accel * dt)
            elif target < self.out_hz:
                self.out_hz = max(target, self.out_hz - self.base_hz / self.decel * dt)
            if self.out_hz <= 0.0:
                self.amp = 0.0
            else:
                ratio = self.out_hz / self.base_hz
                self.amp = self.rated_amp * (0.35 + 0.65 * self.load / 100.0 * ratio) + (0.4 * self.rated_amp if accelerating else 0.0)
                if self.amp > 1.8 * self.rated_amp:
                    self.trip(0x10 if accelerating else 0x11)
            over = self.amp / self.rated_amp - 1.0
            self.heat = max(0.0, self.heat + (over * dt / 6.0 if over > 0 else -dt / 60.0))
            if self.heat > 1.0:
                self.trip(0x90)

    def status_line(self):
        with self.lock:
            return "[vfd] %s set %.1f Hz | out %.1f Hz %.1f A %4.0f rpm | load %d%% | fault %s" % (
                "RUN " if self.control & 2 else "STOP", self.set_hz, self.out_hz, self.amp, self.rpm(), self.load,
                FAULTS.get(self.fault, self.fault) if self.fault else "-")


# ---------- Modbus TCP server (functions 03, 06, 16) ----------
def make_handler(vfd, unit_id):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            sock = self.request
            while True:
                head = _recv(sock, 7)
                if not head:
                    return
                tid, proto, length, unit = struct.unpack(">HHHB", head)
                pdu = _recv(sock, length - 1)
                if pdu is None:
                    return
                vfd.talked()
                reply = self.answer(pdu) if unit in (unit_id, 0, 255) else bytes([pdu[0] | 0x80, 0x0B])
                sock.sendall(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)

        def answer(self, pdu):
            fn = pdu[0]
            try:
                if fn == 3:
                    addr, count = struct.unpack(">HH", pdu[1:5])
                    if not 1 <= count <= 125:
                        return bytes([0x83, 3])
                    values = [vfd.read(addr + i) for i in range(count)]
                    return bytes([3, count * 2]) + struct.pack(">" + "H" * count, *values)
                if fn == 6:
                    addr, value = struct.unpack(">HH", pdu[1:5])
                    return pdu[:5] if vfd.write(addr, value) else bytes([0x86, 2])
                if fn == 16:
                    addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                    values = struct.unpack(">" + "H" * count, pdu[6:6 + nbytes])
                    for i, value in enumerate(values):
                        if not vfd.write(addr + i, value):
                            return bytes([0x90, 2])
                    return pdu[:5]
            except struct.error:
                return bytes([fn | 0x80, 3])
            return bytes([fn | 0x80, 1])
    return Handler


def _recv(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            return None
        data += chunk
    return data


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start(host="127.0.0.1", port=5020, unit_id=1, console=True, **motor):
    """Starts the virtual inverter in background threads; returns it."""
    vfd = VirtualInverter(**motor)
    server = _Server((host, port), make_handler(vfd, unit_id))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def physics():
        last = time.monotonic()
        while True:
            time.sleep(0.1)
            now = time.monotonic()
            vfd.step(now - last)
            last = now
    threading.Thread(target=physics, daemon=True).start()
    if console and sys.stdin and sys.stdin.isatty():
        threading.Thread(target=_console, args=(vfd,), daemon=True).start()
    print("[vfd] virtual inverter on %s:%d (Modbus TCP, slave %d) - motor %.0f A, %d rpm. Type 'help' for test commands."
          % (host, port, unit_id, vfd.rated_amp, vfd.rated_rpm))
    return vfd


def _console(vfd):
    for line in sys.stdin:
        words = line.strip().lower().split()
        if not words:
            continue
        with vfd.lock:
            if words[0] == "load" and len(words) > 1 and words[1].replace(".", "", 1).isdigit():
                vfd.load = float(words[1])
            elif words[0] == "jam":
                vfd.load = 250.0
            elif words[0] == "clear":
                vfd.load = 60.0
            elif words[0] == "fault":
                vfd.trip(0x10)
            elif words[0] != "status":
                print(__doc__.split("Typed commands")[1].split("Register map")[0])
                continue
        print(vfd.status_line())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--slave", type=int, default=1)
    parser.add_argument("--rated-amp", type=float, default=30.0)
    parser.add_argument("--rated-rpm", type=int, default=1450)
    parser.add_argument("--accel", type=float, default=5.0, help="seconds from 0 to 50 Hz")
    parser.add_argument("--decel", type=float, default=5.0, help="seconds from 50 Hz to 0")
    args = parser.parse_args()
    vfd = start(args.host, args.port, args.slave, rated_amp=args.rated_amp, rated_rpm=args.rated_rpm,
                accel=args.accel, decel=args.decel)
    try:
        while True:
            time.sleep(5)
            print(vfd.status_line())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
