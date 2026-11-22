"""The real rice quality grader for the Raspberry Pi (update 132): a flatbed scanner instead of a camera, and it keeps
working when the internet or AOI is down.

Why a flatbed scanner: even light, no shadows, no lens distortion and a known scale (dots per inch), so a grain's
length in millimetres needs no calibration. Any scanner that Linux "SANE" supports works (e.g. Canon LiDE 300 / 400).

Set up once on the Pi:
    sudo apt install sane-utils python3-opencv python3-numpy
    scanimage -L                                   (the scanner must be listed)
    python3 scripts/aoi_grader.py --init           (asks AOI's address, the grader id and its device key -> grader.json)

Every day:
    python3 scripts/aoi_grader.py                  waits: Enter = scan one sample, q = stop
    python3 scripts/aoi_grader.py --image a.png    grade a picture file instead of scanning (to try it without a scanner)

How to scan: leave the lid OPEN or put a matte black sheet over the grains (the background must be dark), spread about
200 grains so that they do not touch, press Enter. The order is chosen in AOI first ("เลือกออเดอร์นี้เพื่อตรวจ" on the
buying order card); the result is kept with it.

Offline: every result is first written to the queue folder (grader_queue/) and sent from there. With no connection
it simply stays in the queue and is sent, oldest first, when the connection is back (also when the program is started
again). A result made while offline cannot know which order was chosen, so it arrives "not attached" and the staff
attach it by hand (within 72 hours); AOI refuses results older than 7 days.

Exam (update 135): every scan is kept in grader_scans/ (60 days). Samples graded by hand in AOI and marked as exam
samples are graded again by  python3 scripts/aoi_grader.py --exam  - run it after every change of this program (raise
VERSION) and compare the score with the earlier version on AOI > AI > วัดผลเป็นเงิน & ข้อสอบ AI.

What is measured (the same message as scripts/grader_sim.py):
    grains, whole %, broken % (a grain shorter than 3/4 of a whole one; % by area, which follows weight), average
    length / width in mm, and for milled rice chalky % and yellow %. --moisture takes the reading of the moisture meter.
    Head rice % of paddy needs a test mill: mill a weighed paddy sample, scan the milled rice with --sample white and
    give both weights (--paddy-g 100 --milled-g 66): head rice = whole % x milled / paddy.
NOT measured: the rice variety (that needs an AI model trained on labelled grains).
The chalky / yellow limits are starting values: compare with hand grading for 2-4 weeks and adjust them in grader.json.

grader.json and grader_queue/ hold the device key and the results: they are not committed (.gitignore).
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

VERSION = "1.1"                    # update 135: sent with every result; the exam in AOI is kept per version - raise it
                                   # whenever the counting changes (code or the limits below)
DEFAULTS = {"url": "http://127.0.0.1:5000", "grader": "", "key": "", "dpi": 300, "device": "", "queue": "grader_queue",
            "scans": "grader_scans", "keep_scans_days": 60,   # update 135: the raw scans, for the exam (--exam)
            "broken_ratio": 0.75,          # shorter than this share of a whole grain = broken
            "min_len_mm": 1.2,             # smaller specks are dust
            "chalk_v": 205, "chalk_s": 45, # a pixel this bright and this colourless is chalky (milled rice)
            "chalk_share": 0.5,            # a grain with this share of chalky pixels is a chalky grain
            "yellow_s": 75}                # a milled grain this saturated (hue yellow) is a yellow grain
PHOTO_MAX = 380 * 1024
SAMPLES = ("paddy", "brown", "white")


# ---------- settings ----------
def load_config(path):
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    return cfg


def init_config(path):
    cfg = load_config(path)
    for name, label in (("url", "AOI address (e.g. https://aoi.example.com)"), ("grader", "Grader id (e.g. F2-GRADER)"),
                        ("key", "Device key (Rice quality grading > New key)")):
        answer = input("%s [%s]: " % (label, "kept" if name == "key" and cfg[name] else cfg[name])).strip()
        if answer:
            cfg[name] = answer
    cfg["url"], cfg["grader"] = cfg["url"].rstrip("/"), cfg["grader"].upper()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    os.chmod(path, 0o600)
    print("Saved %s (it holds the device key: do not share it)." % path)
    return cfg


# ---------- AOI ----------
def call(cfg, path, body=None, timeout=20):
    """One request to AOI's device API. {"ok": False, "offline": True} when AOI cannot be reached."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("%s/api/v1/graders/%s%s" % (cfg["url"], cfg["grader"], path), data=data,
                                 method="POST" if body is not None else "GET",
                                 headers={"X-Device-Key": cfg["key"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return {"ok": False, "http": error.code, "body": error.read().decode("utf-8", "replace")[:300]}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"ok": False, "offline": True, "error": str(error)}


# ---------- the queue (results wait here until AOI has them) ----------
def enqueue(cfg, body):
    os.makedirs(cfg["queue"], exist_ok=True)
    name = "%s_%03d.json" % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"), len(os.listdir(cfg["queue"])) % 1000)
    path = os.path.join(cfg["queue"], name)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(body, f)
    os.replace(path + ".tmp", path)             # a power cut never leaves half a file
    return path


def waiting(cfg):
    if not os.path.isdir(cfg["queue"]):
        return []
    return sorted(os.path.join(cfg["queue"], n) for n in os.listdir(cfg["queue"]) if n.endswith(".json"))


def flush(cfg, send=call, say=print):
    """Sends the waiting results, oldest first. Returns (sent, still waiting). Stops at the first one AOI cannot be
    reached for; a result AOI refuses (wrong numbers, too old) is moved to rejected/ so it does not block the rest."""
    sent = 0
    for path in waiting(cfg):
        with open(path, encoding="utf-8") as f:
            body = json.load(f)
        reply = send(cfg, "/results", body)
        if reply.get("ok"):
            os.remove(path)
            sent += 1
            say("  sent %s -> %s%s" % (body.get("sample") or os.path.basename(path), reply.get("id"),
                                       ", kept with order %s" % reply["order"] if reply.get("order") else ", not attached to an order"))
        elif reply.get("http") == 422:
            os.makedirs(os.path.join(cfg["queue"], "rejected"), exist_ok=True)
            os.replace(path, os.path.join(cfg["queue"], "rejected", os.path.basename(path)))
            say("  AOI refused %s: %s (moved to rejected/)" % (os.path.basename(path), reply.get("body")))
        else:
            say("  AOI cannot be reached now (%s): %d result(s) wait in %s" %
                ("wrong device key" if reply.get("http") in (401, 403) else reply.get("error") or reply.get("http"),
                 len(waiting(cfg)), cfg["queue"]))
            break
    return sent, len(waiting(cfg))


# ---------- the scanner ----------
def scan(cfg):
    """One colour scan as PNG bytes (SANE's scanimage)."""
    cmd = ["scanimage", "--format=png", "--mode", "Color", "--resolution", str(int(cfg["dpi"]))]
    if cfg.get("device"):
        cmd += ["-d", cfg["device"]]
    done = subprocess.run(cmd, capture_output=True, timeout=180)
    if done.returncode != 0 or not done.stdout:
        raise RuntimeError("scanner: " + (done.stderr.decode("utf-8", "replace").strip() or "no picture") +
                           "  (check: scanimage -L)")
    return done.stdout


# ---------- counting the grains ----------
def analyse(image, cfg, sample="white"):
    """(values for AOI, marked picture) of a scan: BGR image, dark background, grains not touching."""
    import cv2
    import numpy as np
    px = cfg["dpi"] / 25.4                                   # pixels per millimetre
    gray = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edge = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    if float(np.median(edge)) > 120:
        raise ValueError("the background is bright: leave the scanner lid open or cover the grains with a matte black sheet")
    _t, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _h = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for c in contours:
        (_cx, _cy), (w, h), _a = cv2.minAreaRect(c)
        length, width, area = max(w, h) / px, min(w, h) / px, cv2.contourArea(c) / (px * px)
        if length >= cfg["min_len_mm"] and width >= cfg["min_len_mm"] / 3:
            found.append({"c": c, "len": length, "wid": width, "area": area})
    if len(found) < 20:
        raise ValueError("only %d grains found: spread about 200 grains on the glass" % len(found))
    typical = float(np.median([g["area"] for g in found]))
    whole_len = float(np.percentile([g["len"] for g in found if g["area"] <= 1.8 * typical], 90))
    for g in found:                                          # grains that touch show as one big shape
        g["touch"] = g["area"] > 1.8 * typical or g["len"] > 1.6 * whole_len
        g["broken"] = not g["touch"] and g["len"] < cfg["broken_ratio"] * whole_len
    single = [g for g in found if not g["touch"]]
    touching = sum(max(2, int(round(g["area"] / typical))) for g in found if g["touch"])
    if touching > 0.25 * (len(single) + touching):
        raise ValueError("too many grains touch each other (%d): spread them apart and scan again" % touching)
    area_all = sum(g["area"] for g in single)
    whole = 100.0 * sum(g["area"] for g in single if not g["broken"]) / area_all
    out = {"grains": len(single) + touching, "whole": round(whole, 1), "broken": round(100 - whole, 1),
           "avg_len": round(float(np.mean([g["len"] for g in single if not g["broken"]])), 2),
           "avg_wid": round(float(np.mean([g["wid"] for g in single])), 2)}
    if sample == "white":                                    # chalky and yellow grains show on milled rice only
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        chalky = yellow = 0.0
        for g in single:
            one = np.zeros(gray.shape, np.uint8)
            cv2.drawContours(one, [g["c"]], -1, 255, -1)
            h, s, v = (hsv[:, :, k][one > 0] for k in range(3))
            g["chalky"] = float(np.mean((v >= cfg["chalk_v"]) & (s <= cfg["chalk_s"]))) >= cfg["chalk_share"]
            g["yellow"] = not g["chalky"] and float(np.median(s)) >= cfg["yellow_s"] and 12 <= float(np.median(h)) <= 38
            chalky += g["area"] if g["chalky"] else 0
            yellow += g["area"] if g["yellow"] else 0
        out.update(chalky=round(100.0 * chalky / area_all, 1), yellow=round(100.0 * yellow / area_all, 1))
    marked = image.copy()                                    # green = whole, red = broken, yellow = touching (not measured)
    for g in found:
        colour = (0, 215, 255) if g["touch"] else ((60, 60, 235) if g["broken"] else (80, 200, 80))
        cv2.drawContours(marked, [g["c"]], -1, colour, max(1, int(px / 5)))
    out["_touching"] = touching
    return out, marked


def photo_b64(marked):
    """The marked picture as a JPEG small enough for AOI."""
    import cv2
    h, w = marked.shape[:2]
    scale = min(1.0, 1400.0 / max(h, w))
    small = cv2.resize(marked, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else marked
    for quality in (80, 65, 50, 35):
        ok, data = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok and len(data) <= PHOTO_MAX:
            return base64.b64encode(data.tobytes()).decode("ascii")
    return None


def grade(cfg, image, sample, number, moisture=None, paddy_g=None, milled_g=None, material=None, send=call, say=print):
    """Grades one picture, puts the result in the queue and tries to send it. Returns the values."""
    values, marked = analyse(image, cfg, sample)
    touching = values.pop("_touching")
    body = dict(values, sample="S-%s-%02d" % (datetime.now().strftime("%y%m%d-%H%M%S"), number % 100), version=VERSION,
                time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                note={"paddy": "paddy in the husk", "brown": "brown rice", "white": "milled rice"}[sample] +
                     (", %d touching grains not measured" % touching if touching else ""))
    if moisture is not None:
        body["moisture"] = moisture
    if paddy_g and milled_g and sample != "paddy":           # test-milled sample: head rice of the paddy
        body["head_rice"] = round(values["whole"] * milled_g / paddy_g, 1)
    photo = photo_b64(marked)
    if photo:
        body["photo"] = photo
    keep_scan(cfg, image, body["sample"], {"sample": sample, "paddy_g": paddy_g, "milled_g": milled_g})
    info = send(cfg, "/orders", None, 6)                     # the order chosen in AOI, when AOI can be reached now
    if info.get("ok") and info.get("target"):
        body["order"] = info["target"]
    elif material:
        body["material"] = material
    say("  %d grains, whole %.1f%%, broken %.1f%%, length %.2f mm%s%s" % (
        body["grains"], body["whole"], body["broken"], body["avg_len"],
        ", head rice %.1f%%" % body["head_rice"] if "head_rice" in body else "",
        "  -> order %s" % body["order"] if body.get("order") else "  -> no order chosen in AOI (or offline): not attached"))
    enqueue(cfg, body)
    flush(cfg, send, say)
    return body


# ---------- update 135: the raw scans and the exam ----------
def keep_scan(cfg, image, sample, meta):
    """The raw scan (JPEG 92) and how it was graded, so the exam can grade it again with a newer program."""
    import cv2
    folder = cfg.get("scans") or "grader_scans"
    os.makedirs(folder, exist_ok=True)
    cv2.imwrite(os.path.join(folder, sample + ".jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    with open(os.path.join(folder, sample + ".json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    limit = time.time() - float(cfg.get("keep_scans_days") or 60) * 86400
    keep = set(cfg.get("_golden_samples") or [])
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        if name.rsplit(".", 1)[0] not in keep and os.path.getmtime(path) < limit:
            os.remove(path)


def exam(cfg, send=call, say=print):
    """Grades the kept scans of the exam samples (AOI: graded by hand, marked golden) with this program and sends the
    answers. Returns AOI's reply."""
    import cv2
    info = send(cfg, "/golden", None, 20)
    if not info.get("ok"):
        say("AOI cannot be reached or refused the key: %s" % (info.get("error") or info.get("http")))
        return info
    folder = cfg.get("scans") or "grader_scans"
    cfg["_golden_samples"] = [g.get("sample") for g in info.get("golden") or []]
    results, missing = [], 0
    for g in info.get("golden") or []:
        path = os.path.join(folder, "%s.jpg" % g.get("sample"))
        if not g.get("sample") or not os.path.exists(path):
            missing += 1
            continue
        meta = {}
        if os.path.exists(path[:-4] + ".json"):
            with open(path[:-4] + ".json", encoding="utf-8") as f:
                meta = json.load(f)
        try:
            values, _marked = analyse(cv2.imread(path), cfg, meta.get("sample") or "paddy")
        except ValueError as error:
            say("  %s: not graded (%s)" % (g["sample"], error))
            continue
        row = {"grade_id": g["grade_id"], "whole": values["whole"], "broken": values["broken"]}
        for k in ("chalky", "yellow"):
            if k in values:
                row[k] = values[k]
        if meta.get("paddy_g") and meta.get("milled_g"):
            row["head_rice"] = round(values["whole"] * meta["milled_g"] / meta["paddy_g"], 1)
        results.append(row)
    say("Exam set: %d sample(s), %d graded again, %d without a kept scan on this Pi" % (len(info.get("golden") or []), len(results), missing))
    if not results:
        return {"ok": False}
    reply = send(cfg, "/exam", {"version": VERSION, "results": results}, 30)
    if reply.get("ok"):
        for name, s in (reply.get("scores") or {}).items():
            if s.get("n"):
                say("  %-14s n=%-3d average difference %.2f, within 2 points %s%%" % (name, s["n"], s["mae"], s["within"]))
    else:
        say("AOI refused the exam: %s" % reply)
    return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="grader.json")
    parser.add_argument("--init", action="store_true", help="ask the address, grader id and key and save them")
    parser.add_argument("--image", help="grade this picture file instead of scanning")
    parser.add_argument("--sample", choices=SAMPLES, default="paddy", help="what is on the glass (default paddy)")
    parser.add_argument("--moisture", type=float, help="reading of the moisture meter, %%")
    parser.add_argument("--paddy-g", type=float, help="test mill: weight of the paddy sample, g")
    parser.add_argument("--milled-g", type=float, help="test mill: weight of the milled rice from it, g")
    parser.add_argument("--material", help="AOI material code when no order is chosen (e.g. PADDY_HM)")
    parser.add_argument("--flush", action="store_true", help="only send the results that wait in the queue")
    parser.add_argument("--exam", action="store_true", help="grade the exam samples again with this version and send the score")
    args = parser.parse_args()
    if args.init:
        init_config(args.config)
        return 0
    cfg = load_config(args.config)
    if not cfg["grader"] or not cfg["key"]:
        print("No grader id / device key yet: run  python3 scripts/aoi_grader.py --init")
        return 1
    if args.flush:
        flush(cfg)
        return 0
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("OpenCV is missing:  sudo apt install python3-opencv python3-numpy")
        return 1
    if args.exam:
        return 0 if exam(cfg).get("ok") else 1
    extra = dict(moisture=args.moisture, paddy_g=args.paddy_g, milled_g=args.milled_g, material=args.material)
    if args.image:
        image = cv2.imread(args.image)
        if image is None:
            print("Cannot read the picture %s" % args.image)
            return 1
        try:
            grade(cfg, image, args.sample, 1, **extra)
        except ValueError as error:
            print("Not graded: %s" % error)
            return 1
        return 0
    number = 0
    print("AOI grader %s (program %s) -> %s. Sample on the glass: %s." % (cfg["grader"], VERSION, cfg["url"], args.sample))
    golden = call(cfg, "/golden", None, 6)                     # exam samples: their scans are never deleted
    cfg["_golden_samples"] = [g.get("sample") for g in golden.get("golden") or []]
    flush(cfg)
    while True:
        info = call(cfg, "/orders", None, 6)
        chosen = next((o for o in info.get("orders") or [] if o["order"] == info.get("target")), None)
        print("\n%s" % ("Order chosen in AOI: #%s (%s, %s kg)" % (chosen.get("qr"), chosen.get("material"), chosen.get("kg")) if chosen
                        else "OFFLINE - results are kept and sent later" if info.get("offline")
                        else "No order chosen in AOI - the result will not be attached"))
        try:
            answer = input("Enter = scan, a number = moisture %% of this sample, q = stop: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if answer == "q":
            break
        try:
            extra["moisture"] = float(answer) if answer else args.moisture
        except ValueError:
            continue
        try:
            print("  scanning ...")
            image = cv2.imdecode(np.frombuffer(scan(cfg), np.uint8), cv2.IMREAD_COLOR)
            number += 1
            grade(cfg, image, args.sample, number, **extra)
        except (ValueError, RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as error:
            print("  Not graded: %s" % error)
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
