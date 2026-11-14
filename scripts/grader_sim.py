"""A pretend rice quality grader (the Raspberry Pi + camera of update 126) - for trying the grading screens before the
real grader is built. It uses only the Python standard library.

    python3 scripts/grader_sim.py F2-GRADER <device key>              one sample now
    python3 scripts/grader_sim.py F2-GRADER <device key> --every 30   a new sample every 30 seconds
    python3 scripts/grader_sim.py F2-GRADER <device key> --link       keep each sample with an open buying order that has none yet

With --every it also answers "Grade a sample" pressed on a buying order within a few seconds (update 128).

Options: --url http://127.0.0.1:5000 (default)  --count N (stop after N samples)  --no-photo  --material PADDY_HM
The device key is issued on Service > Buying/Selling Order List > Rice quality grading (shown once), or use
`bash start.sh sim`, which starts this simulator too without a key to copy.

Each sample is a made-up tray of grains: whole and broken ones are drawn into a small PNG "photo" and counted the
way the real grader will (broken = shorter than 3/4 of a whole grain), so the numbers match the picture.
The real grader replaces `make_sample` with the camera (picamera2) and the OpenCV counting; the message it sends
stays the same.
"""
import argparse
import base64
import json
import math
import random
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib

VARIETIES = {"PADDY_HM": (7.4, 2.1), "PADDY": (7.0, 2.2), "PADDY_GL": (6.8, 2.3)}      # whole grain length, width in mm
# update 127: the real grader can name the variety only after a model is trained on labelled grains; the simulator
# makes one up so the screens and the slip can be tried
NAMES = {"PADDY_HM": ("ขาวดอกมะลิ 105", "กข15"), "PADDY": ("ปทุมธานี 1", "ชัยนาท 1", "กข79", "กข85"), "PADDY_GL": ("กข6", "สันป่าตอง 1")}


def call(base, path, key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"X-Device-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"ok": False, "http": error.code, "body": error.read().decode("utf-8", "replace")[:300]}
    except (urllib.error.URLError, OSError) as error:
        return {"ok": False, "error": str(error)}


def png(pixels, w, h):
    """An RGB image [(r, g, b)] * w * h as PNG bytes (no extra library)."""
    raw = b"".join(b"\x00" + bytes(c for px in pixels[y * w:(y + 1) * w] for c in px) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def make_sample(material=None, quality=None, rng=random, photo=True, w=320, h=240):
    """One made-up sample: the measured values and (optionally) its picture.
    quality 0..1 (1 = hardly any broken grains); None = random, like paddy of different farmers."""
    material = material or rng.choice(list(VARIETIES))
    length, width = VARIETIES[material]
    quality = rng.uniform(0.62, 0.95) if quality is None else quality
    n = rng.randint(180, 260)
    scale = 3.0                                              # pixels per mm
    pixels = [(24, 44, 92)] * (w * h) if photo else None     # the matte blue tray
    grains = []
    for _ in range(n):
        broken = rng.random() > quality
        chalky = rng.random() < 0.05 + (1 - quality) * 0.08
        yellow = rng.random() < 0.01 + (1 - quality) * 0.03
        gl = length * (rng.uniform(0.35, 0.72) if broken else rng.uniform(0.9, 1.08))
        gw = width * rng.uniform(0.9, 1.1)
        grains.append((gl, gw, broken, chalky, yellow))
        if photo:
            cx, cy, ang = rng.uniform(14, w - 14), rng.uniform(14, h - 14), rng.uniform(0, math.pi)
            a, b = gl * scale / 2, gw * scale / 2
            col = (228, 206, 120) if yellow else ((250, 250, 246) if chalky else (236, 228, 204))
            ca, sa = math.cos(ang), math.sin(ang)
            for y in range(max(0, int(cy - a - 1)), min(h, int(cy + a + 2))):
                for x in range(max(0, int(cx - a - 1)), min(w, int(cx + a + 2))):
                    dx, dy = x - cx, y - cy
                    u, v = dx * ca + dy * sa, -dx * sa + dy * ca
                    if (u / a) ** 2 + (v / b) ** 2 <= 1:
                        pixels[y * w + x] = col
    whole_len = sorted(g[0] for g in grains)[int(len(grains) * 0.9)]          # a whole grain of this sample
    is_broken = [g[0] < whole_len * 0.75 for g in grains]
    nb = sum(is_broken)
    whole = 100.0 * (n - nb) / n
    out = {"material": material, "grains": n, "whole": round(whole, 1), "broken": round(100 - whole, 1),
           # head rice: whole kernels as a share of the paddy weight (husk and bran are about 32 % of it)
           "head_rice": round(whole * 0.68, 1),
           "chalky": round(100.0 * sum(1 for g in grains if g[3]) / n, 1), "yellow": round(100.0 * sum(1 for g in grains if g[4]) / n, 1),
           "avg_len": round(sum(g[0] for g, b in zip(grains, is_broken) if not b) / max(1, n - nb), 2),
           "avg_wid": round(sum(g[1] for g in grains) / n, 2), "moisture": round(rng.uniform(12.5, 16.5), 1),
           "variety": rng.choice(NAMES[material]), "purity": round(rng.uniform(88, 99.5), 1)}
    if photo:
        out["photo"] = base64.b64encode(png(pixels, w, h)).decode("ascii")
    return out


def send_one(base, grader, key, number, material=None, link=False, photo=True, rng=random):
    # update 128: the order the staff chose with "Grade a sample" comes first (AOI keeps the result with it by itself);
    # --link: else an open order that has no result yet. The sample is of the order's material.
    info = call(base, "/api/v1/graders/%s/orders" % grader, key)
    orders, order = info.get("orders") or [], None
    pick = next((o for o in orders if o["order"] == info.get("target")), None)
    if not pick and link:
        pick = order = next((o for o in orders if not o.get("results")), None)
    if pick and pick.get("material") in VARIETIES:
        material = pick["material"]
    body = make_sample(material, rng=rng, photo=photo)
    body.update(sample="SIM-%04d" % number, note="simulated sample")
    if order:
        body["order"] = order["order"]
    reply = call(base, "/api/v1/graders/%s/results" % grader, key, body)
    shown = {k: v for k, v in body.items() if k != "photo"}
    print("sample %s -> %s   %s" % (body["sample"], reply, shown), flush=True)
    return reply


def run(base, grader, key, every=0.0, count=None, material=None, link=False, photo=True, stop=None):
    n = 0
    while not (stop and stop.is_set()):
        n += 1
        send_one(base, grader, key, n, material, link, photo)
        if not every or (count and n >= count):
            break
        # wait for the next round, but answer "Grade a sample" (a target order) within a few seconds
        until = time.time() + every
        while time.time() < until and not (stop and stop.is_set()):
            pause = min(3.0, max(0.1, until - time.time()))
            if stop:
                stop.wait(pause)
            else:
                time.sleep(pause)
            if call(base, "/api/v1/graders/%s/orders" % grader, key).get("target"):
                break
    return n


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("grader")
    parser.add_argument("key")
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--every", type=float, default=0.0, help="seconds between samples (0 = one sample)")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--material", choices=sorted(VARIETIES), default=None)
    parser.add_argument("--link", action="store_true", help="keep each sample with the newest open buying order")
    parser.add_argument("--no-photo", action="store_true")
    args = parser.parse_args()
    try:
        run(args.url.rstrip("/"), args.grader.upper(), args.key, args.every, args.count, args.material, args.link, not args.no_photo)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
