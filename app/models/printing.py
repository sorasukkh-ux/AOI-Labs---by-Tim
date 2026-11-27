"""Update 141: server-side printing (claude/Server_Printing_Design.md, Tim 2026-11-22).

Phones and tablets never print. Print on any screen asks the server (POST /print {doc, ref}); the server renders the
document as a 1-bit picture at the printer's dpi (Thai as an image, QR from qr_png), stores a print job and sends it
as ESC/POS raster (GS v 0) to the printer: the USB device (/dev/usb/lp0, the Epson TM-T81III on the NUC) or raw TCP
port 9100 (a network printer later). Retry 3 times 10 s apart, then Failed; Reprint from Printer Maintenance.

    AOIMS_PRINTER   PRINTER_ID PR001.., BRANCH_ID, NAME, KIND (receipt / label), LANGUAGE (ESCPOS), CONN (USB / TCP),
                    DEVICE, IP, PORT, DPI, WIDTH_MM, DEFAULT_FLG, ACTIVE_FLG, LAST_OK, LAST_ERROR
    AOITS_PRINTJOB  JOB_ID PJ000001.., BRANCH_ID, PRINTER_ID, DOC_KIND, REF_ID, COPIES, USER_ID, STATUS (Q / P / F),
                    ATTEMPTS, ERROR, REPRINT_OF, REASON, DATA (base64 ESC/POS), CREATED, SENT

The AOI service user needs write access to the USB device: sudo usermod -aG lp <user> (then restart AOI).
"""
import base64
import glob
import os
import re
import socket
import threading
import time
from datetime import timedelta

from .base import NO_ID, ValidationError, audit_new, audit_update, clean_text, col, round_half_up, to_number
from .counter import Counter
from ..timeutil import now_utc, to_local

PRINTERS, JOBS = "AOIMS_PRINTER", "AOITS_PRINTJOB"
DOCS = ("label", "card", "receipt", "grade", "test")
KINDS, CONNS, LANGUAGES = ("receipt", "label"), ("USB", "TCP"), ("ESCPOS",)
QUEUED, PRINTED, FAILED = "Q", "P", "F"
ATTEMPTS, RETRY_SECONDS, FRESH_MINUTES = 3, 10, 10
FONT_DIRS = ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts"))
FONT_NAMES = ("NotoSansThai-Regular", "NotoSansThaiUI-Regular", "Sarabun-Regular", "Garuda", "Loma", "Kinnari", "Norasi",
              "TlwgTypo", "FreeSerif")
FONT_BOLD = ("NotoSansThai-Bold", "NotoSansThaiUI-Bold", "Sarabun-Bold", "Garuda-Bold", "Loma-Bold", "Kinnari-Bold",
             "Norasi-Bold", "TlwgTypo-Bold", "FreeSerifBold")


# ---------- printers ----------
def printers(branch_id=None, active_only=False):
    q = {"BRANCH_ID": branch_id} if branch_id else {}
    if active_only:
        q["ACTIVE_FLG"] = True
    return list(col(PRINTERS).find(q, NO_ID).sort([("PRINTER_ID", 1)]))


def printer(printer_id):
    return col(PRINTERS).find_one({"PRINTER_ID": (printer_id or "").strip().upper()}, NO_ID)


def default_for(branch_id, kind="receipt"):
    found = [p for p in printers(branch_id, True) if p.get("KIND") == kind] or printers(branch_id, True)
    return next((p for p in found if p.get("DEFAULT_FLG")), found[0] if found else None)


def save_printer(branch_id, form, ope_user):
    """Adds (no PRINTER_ID) or changes a printer. Returns it."""
    name = clean_text(form.get("NAME"), 60)
    kind, conn = form.get("KIND") or "receipt", form.get("CONN") or "USB"
    language = form.get("LANGUAGE") or "ESCPOS"
    if not name:
        raise ValidationError("prErrName")
    if kind not in KINDS or conn not in CONNS or language not in LANGUAGES:
        raise ValidationError("prErrKind")
    device = (form.get("DEVICE") or "").strip()[:120] or None
    ip = (form.get("IP") or "").strip()[:60] or None
    try:
        port = int(to_number(form.get("PORT"), 9100))
        dpi = int(to_number(form.get("DPI"), 203))
        width = round_half_up(to_number(form.get("WIDTH_MM"), 80), 1)
    except (ValueError, TypeError):
        raise ValidationError("errNumber")
    if conn == "USB" and not (device and device.startswith("/")):
        raise ValidationError("prErrDevice")
    if conn == "TCP" and not (ip and re.match(r"^[A-Za-z0-9.\-]+$", ip) and 1 <= port <= 65535):
        raise ValidationError("prErrIp")
    if not (150 <= dpi <= 600 and 40 <= width <= 120):
        raise ValidationError("prErrSize")
    values = {"BRANCH_ID": branch_id, "NAME": name, "KIND": kind, "LANGUAGE": language, "CONN": conn, "DEVICE": device,
              "IP": ip, "PORT": port, "DPI": dpi, "WIDTH_MM": width, "DEFAULT_FLG": form.get("DEFAULT_FLG") == "1",
              "ACTIVE_FLG": form.get("ACTIVE_FLG") == "1"}
    pid = (form.get("PRINTER_ID") or "").strip().upper()
    if pid:
        old = printer(pid)
        if not old or old["BRANCH_ID"] != branch_id:
            raise ValidationError("prErrUnknown")
        values.update(audit_update(ope_user))
        col(PRINTERS).update_one({"PRINTER_ID": pid}, {"$set": values})
    else:
        pid = "PR%03d" % Counter.next("PRINTER")
        doc = dict(values, PRINTER_ID=pid, LAST_OK=None, LAST_ERROR=None)
        doc.update(audit_new(ope_user))
        col(PRINTERS).insert_one(doc)
    if values["DEFAULT_FLG"]:                            # one default per branch and kind
        col(PRINTERS).update_many({"BRANCH_ID": branch_id, "KIND": kind, "PRINTER_ID": {"$ne": pid}}, {"$set": {"DEFAULT_FLG": False}})
    return printer(pid)


def usb_devices():
    """The USB printer devices the server sees now (to pick one on Printer Maintenance)."""
    return sorted(glob.glob("/dev/usb/lp*"))


# ---------- rendering (1-bit picture, Thai as an image) ----------
def _font_file(names):
    env = os.environ.get("AOI_PRINT_FONT")
    if env and os.path.exists(env) and names is FONT_NAMES:
        return env
    files = {}
    for d in FONT_DIRS:
        for path in glob.glob(os.path.join(d, "**", "*.[ot]tf"), recursive=True):
            files.setdefault(os.path.splitext(os.path.basename(path))[0], path)
    return next((files[n] for n in names if n in files), None)


_FONTS = {}


def font(size, bold=False):
    from PIL import ImageFont
    key = (size, bold)
    if key not in _FONTS:
        path = (_font_file(FONT_BOLD) if bold else None) or _font_file(FONT_NAMES)
        if not path:
            raise ValidationError("prErrFont")
        _FONTS[key] = ImageFont.truetype(path, size)
    return _FONTS[key]


class Sheet:
    """A receipt-width picture built top to bottom; `image()` crops it to what was drawn."""

    def __init__(self, width_px):
        from PIL import Image, ImageDraw
        self.w, self.y, self.pad = width_px, 8, 8
        self.img = Image.new("L", (width_px, 6000), 255)
        self.draw = ImageDraw.Draw(self.img)

    def _size(self, text, f):
        box = self.draw.textbbox((0, 0), text, font=f)
        return box[2] - box[0], box[3]

    def _wrap(self, text, f, width):
        words, lines, cur = (text or "").split(" "), [], ""
        for w in words:
            test = (cur + " " + w).strip()
            if self._size(test, f)[0] <= width or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        out = []
        for line in lines:                               # Thai has no spaces: cut long runs by width
            while self._size(line, f)[0] > width and len(line) > 1:
                cut = len(line)
                while cut > 1 and self._size(line[:cut], f)[0] > width:
                    cut -= 1
                out.append(line[:cut])
                line = line[cut:]
            out.append(line)
        return out

    def text(self, text, size=24, bold=False, align="left", gap=4):
        f = font(size, bold)
        for line in self._wrap(str(text), f, self.w - 2 * self.pad):
            tw, th = self._size(line, f)
            x = {"center": (self.w - tw) // 2, "right": self.w - self.pad - tw}.get(align, self.pad)
            self.draw.text((x, self.y), line, font=f, fill=0)
            self.y += int(size * 1.35)
        self.y += gap

    def row(self, left, right, size=24, bold=False):
        f = font(size, bold)
        rw = self._size(str(right), f)[0]
        lines = self._wrap(str(left), f, self.w - 2 * self.pad - rw - 16)
        for i, line in enumerate(lines):
            self.draw.text((self.pad, self.y), line, font=f, fill=0)
            if i == 0:
                self.draw.text((self.w - self.pad - rw, self.y), str(right), font=f, fill=0)
            self.y += int(size * 1.35)
        self.y += 2

    def rule(self, dashed=True):
        self.y += 6
        for x in range(self.pad, self.w - self.pad, 12 if dashed else 1):
            self.draw.line((x, self.y, min(x + (7 if dashed else 1), self.w - self.pad), self.y), fill=0, width=2)
        self.y += 10

    def qr(self, text, size_px=300):
        from . import qr_png
        m = qr_png.encode(text)
        n = len(m) + 8
        scale = max(2, size_px // n)
        x0 = (self.w - n * scale) // 2
        for r, line in enumerate(m):
            for c, dark in enumerate(line):
                if dark:
                    x, y = x0 + (c + 4) * scale, self.y + (r + 4) * scale
                    self.draw.rectangle((x, y, x + scale - 1, y + scale - 1), fill=0)
        self.y += n * scale + 4

    def logo(self, width_px=160):
        from PIL import Image
        path = os.path.join(os.path.dirname(__file__), "..", "views", "static", "img", "logo.png")
        if not os.path.exists(path):
            return
        im = Image.open(path).convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("L")
        h = int(im.height * width_px / im.width)
        im = im.resize((width_px, h)).point(lambda v: 0 if v < 215 else 255)      # coloured logo -> solid black on paper
        self.img.paste(im, ((self.w - width_px) // 2, self.y))
        self.y += h + 6

    def space(self, px=10):
        self.y += px

    def image(self):
        return self.img.crop((0, 0, self.w, min(self.y + 16, self.img.height)))


def _head(s, cfg, title):
    s.logo()
    s.text("AOI Labs", 32, True, "center", 0)
    s.text(cfg["LABEL_ADDRESS_TH"], 20, align="center", gap=0)
    s.text("Tel. %s" % cfg["LABEL_TEL"], 20, align="center")
    if title:
        s.text(title, 28, True, "center")
    s.rule()


def render(doc, ref, width_px=576):
    """(PIL 'L' image, text for the job list) of one document. Raises ValidationError when ref is unknown."""
    from flask import current_app
    from ..i18n import t
    cfg = current_app.config
    s = Sheet(width_px)
    if doc == "test":
        _head(s, cfg, t("prTestTitle"))
        s.text(t("prTestLine", name=ref or "-"), 24)
        s.text("ภาษาไทย: ข้าวหอมมะลิ ปลายข้าว รำ 0123456789", 24)
        s.text(to_local(now_utc()).strftime("%d/%m/%Y %H:%M"), 22)
        s.qr("AOI-TEST", 240)
        return s.image(), "TEST"
    if doc == "label":
        from .customer import Customer
        from .milling_order import MillingOrder
        from ..controllers.service_milling import label_data
        order = MillingOrder.find((ref or "").replace("AOI-", "", 1))
        if not order:
            raise ValidationError("errOrderNotFound")
        c = Customer.find(order["CUST_CODE"]) or {}
        d = label_data(order["QR_ID"], "AOI-" + order["ORD_ID"], c.get("CUST_NAME", order["CUST_CODE"]),
                       MillingOrder.pay_total(order), order["EXPCT_RECEIVE_TIME"], order["REGIST_DATE"], c)
        s.logo(120)
        s.text("MILLING", 26, True, "center", 0)
        s.text(d["no"], 72, True, "center", 0)
        s.text(d["addrEn"], 18, align="center", gap=0)
        s.text(d["addrTh"], 18, align="center")
        s.rule(False)
        s.text("Customer : %s" % d["customer"], 28, True)
        s.text("Payment: %s THB" % d["pay"], 24, gap=0)
        s.text("Receive: %s" % d["receive"], 22)
        s.qr(d["qr"], 320)
        s.text(d["qr"], 20, align="center")
        if d.get("line"):
            s.rule()
            s.text("LINE: สแกนรับแจ้งเตือน / Scan for LINE updates", 20, True, "center")
            s.qr(d["line"], 220)
        s.rule()
        s.row("Tel: %s" % d["tel"], "%s %s" % (d["date"], d["time"]), 20)
        return s.image(), "#%s" % order["QR_ID"]
    if doc == "card":
        from .customer import Customer
        from . import phone
        c = Customer.find(ref)
        if not c or c.get("CUST_STATUS") == "D":
            raise ValidationError("alertCustInfoRequired")
        _head(s, cfg, t("prCardTitle"))
        s.text(c.get("CUST_NAME", ""), 34, True, "center")
        s.text(c["CUST_CODE"], 24, align="center", gap=0)
        mobile = phone.of(c)
        if mobile:
            s.text(mobile, 22, align="center")
        s.qr(c["CUST_CODE"], 300)
        return s.image(), c["CUST_CODE"]
    if doc == "receipt":
        from .buysell_order import BuySellOrder
        from .customer import Customer
        from .variety import mat_name
        orders = [o for o in (BuySellOrder.find(i) for i in (ref or "").split(",")[:10]) if o]
        if not orders:
            raise ValidationError("errOrderNotFound")
        c = Customer.find(orders[0]["CUST_CODE"]) or {}
        _head(s, cfg, "%s · %s" % (t("receiptTitle"), t("svcBuyLabel" if orders[0]["ORD_TYPE"] == "BY" else "svcSellLabel")))
        s.row(t("receiptOrderNo"), ", ".join("#%s" % (o.get("QR_ID") or o["ORD_ID"]) for o in orders), 22)
        s.row(t("receiptCustomer"), c.get("CUST_NAME", orders[0]["CUST_CODE"]), 22)
        s.row(t("receiptDate"), to_local(now_utc()).strftime("%d/%m/%Y %H:%M"), 22)
        s.rule()
        total = 0.0
        for o in orders:
            for l in BuySellOrder.items_of(o):
                if (l.get("ORD_QTY") or 0) <= 0:
                    continue
                s.text(mat_name(l["MAT_CODE"]), 24, True, gap=0)
                s.row("  {:,.2f} kg x {:,.2f}".format(l["ORD_QTY"], l.get("PRICING_UINT") or 0), "{:,.2f}".format(l.get("ORD_AMOUNT") or 0), 22)
                if l.get("WEIGH_QTY") is not None and l.get("MOISTURE_PCT") is not None:
                    s.text("  " + t("mtBill", pct=l["MOISTURE_PCT"], weighed="{:,.2f}".format(l["WEIGH_QTY"]),
                                    paid="{:,.2f}".format(l["ORD_QTY"])), 18)
            total += BuySellOrder.amount(o)
        extras, ml_coupon = BuySellOrder.receipt_extras(orders)
        for e in extras:
            s.row(t("rcMillFee" if e["kind"] == "fee" else "rcDeliveryFee", no=e["qr"]), "{:,.2f}".format(e["amount"]), 22)
            total += e["amount"]
        s.rule(False)
        s.row(t("totalLabel"), "{:,.2f} THB".format(total), 28, True)
        coupon = sum(o.get("COUPON_AMT") or 0 for o in orders) + ml_coupon
        if coupon:
            s.row(t("cpnLabel"), "-{:,.2f}".format(coupon), 22)
            s.row(t("cpnToPay"), "{:,.2f} THB".format(total - coupon), 28, True)
        s.space(12)
        s.text(t("prThanks"), 20, align="center")
        return s.image(), ", ".join("#%s" % (o.get("QR_ID") or o["ORD_ID"]) for o in orders)
    if doc == "grade":
        from . import grade
        g = grade.get(ref)
        if not g:
            raise ValidationError("grErrUnknown")
        order, c = grade.report_context(g)
        _head(s, cfg, t("grReportTitle"))
        if order:
            s.row(t("receiptOrderNo"), "#%s" % (order.get("QR_ID") or order["ORD_ID"]), 22)
            s.row(t("receiptCustomer"), c.get("CUST_NAME", order["CUST_CODE"]), 22)
        s.row(t("receiptDate"), to_local(g["TEST_DATE"]).strftime("%d/%m/%Y %H:%M"), 22)
        s.row(t("grReportNo"), g["GRADE_ID"] + (" · " + g["SAMPLE_NO"] if g.get("SAMPLE_NO") else ""), 22)
        s.rule()
        for label, value in grade.report_rows(g):
            s.row(label, value, 24, label == t("grHead"))
        s.rule()
        s.text(t("grReportNote"), 18)
        return s.image(), g["GRADE_ID"]
    raise ValidationError("prErrDoc")


def escpos(img):
    """ESC/POS bytes: initialise, the picture as raster bands (GS v 0), feed, cut."""
    from PIL import ImageOps
    bw = ImageOps.invert(img.convert("L")).point(lambda v: 255 if v > 127 else 0).convert("1")   # 1 bit = black dot
    w, h = bw.size
    wb = (w + 7) // 8
    out = bytearray(b"\x1b@")
    for top in range(0, h, 255):
        band = bw.crop((0, top, wb * 8, min(h, top + 255)))
        bh = band.size[1]
        out += b"\x1dv0\x00" + bytes((wb % 256, wb // 256, bh % 256, bh // 256)) + band.tobytes()
    out += b"\n\n\n\n" + b"\x1dV\x42\x00"
    return bytes(out)


# ---------- jobs ----------
def can_print(doc):
    from ..security import can
    return {"label": can("SV_SERVICE") or can("SV_MILLSTATUS"), "card": can("AD_CUSTOMER"),
            "receipt": can("SV_SERVICE") or can("SV_BSSTATUS"), "grade": can("SV_BSSTATUS"),
            "test": can("AD_PRINTER", "edit")}.get(doc, False)


def submit(branch_id, doc, ref, user_id, printer_id=None, copies=1, reprint_of=None, reason=None):
    """Renders the document and queues one job, then sends it (a thread; inline in tests). Returns the job."""
    if doc not in DOCS:
        raise ValidationError("prErrDoc")
    p = printer(printer_id) if printer_id else default_for(branch_id, "receipt")
    if not p or not p.get("ACTIVE_FLG") or p["BRANCH_ID"] != branch_id:
        raise ValidationError("prNoPrinter")
    width_px = int(round(p.get("WIDTH_MM", 80) * p.get("DPI", 203) / 25.4 * 0.9 / 8)) * 8      # printable ~90% (80 mm -> 576 dots)
    img, label = render(doc, ref if doc != "test" else p["NAME"], width_px)
    copies = max(1, min(10, int(copies or 1)))
    data = escpos(img) * copies
    job = {"JOB_ID": "PJ%06d" % Counter.next("PRINTJOB"), "BRANCH_ID": branch_id, "PRINTER_ID": p["PRINTER_ID"],
           "DOC_KIND": doc, "REF_ID": (ref or "")[:200], "REF_TEXT": label, "COPIES": copies, "USER_ID": user_id,
           "STATUS": QUEUED, "ATTEMPTS": 0, "ERROR": None, "REPRINT_OF": reprint_of, "REASON": clean_text(reason, 200) or None,
           "DATA": base64.b64encode(data).decode("ascii"), "CREATED": now_utc(), "SENT": None}
    col(JOBS).insert_one(dict(job))
    from flask import current_app
    if current_app.config.get("TESTING") or os.environ.get("AOI_PRINT_SYNC") == "1":
        process(job["JOB_ID"], wait=0)
    else:
        app = current_app._get_current_object()
        threading.Thread(target=_run, args=(app, job["JOB_ID"]), daemon=True).start()
    return get(job["JOB_ID"])


def _run(app, job_id):
    with app.app_context():
        try:
            process(job_id)
        except Exception:                                   # never let a print thread take anything down
            app.logger.exception("Print job %s", job_id)


def send_bytes(p, data):
    if p["CONN"] == "TCP":
        with socket.create_connection((p["IP"], int(p.get("PORT") or 9100)), timeout=8) as sock:
            sock.sendall(data)
    else:
        with open(p["DEVICE"], "wb", buffering=0) as f:
            f.write(data)


def _reason(error):
    text = str(error)
    if isinstance(error, PermissionError):
        return "permission denied on %s (sudo usermod -aG lp <AOI user>, then restart AOI)" % getattr(error, "filename", "")
    if isinstance(error, FileNotFoundError):
        return "device not found: %s (printer off or unplugged?)" % getattr(error, "filename", "")
    return text[:200] or error.__class__.__name__


def process(job_id, wait=None):
    """Sends one queued job: up to ATTEMPTS tries, RETRY_SECONDS apart. Returns the job."""
    wait = RETRY_SECONDS if wait is None else wait
    job = get(job_id)
    if not job or job["STATUS"] != QUEUED:
        return job
    p = printer(job["PRINTER_ID"])
    data = base64.b64decode(col(JOBS).find_one({"JOB_ID": job["JOB_ID"]}, {"_id": 0, "DATA": 1})["DATA"])
    error = None
    for attempt in range(job.get("ATTEMPTS", 0) + 1, ATTEMPTS + 1):
        try:
            send_bytes(p, data)
        except Exception as exc:                            # noqa: BLE001 - every printer error is reported
            error = _reason(exc)
            col(JOBS).update_one({"JOB_ID": job_id}, {"$set": {"ATTEMPTS": attempt, "ERROR": error}})
            if attempt < ATTEMPTS and wait:
                time.sleep(wait)
            continue
        col(JOBS).update_one({"JOB_ID": job_id}, {"$set": {"STATUS": PRINTED, "ATTEMPTS": attempt, "ERROR": None, "SENT": now_utc()}})
        col(PRINTERS).update_one({"PRINTER_ID": p["PRINTER_ID"]}, {"$set": {"LAST_OK": now_utc(), "LAST_ERROR": None}})
        return get(job_id)
    col(JOBS).update_one({"JOB_ID": job_id}, {"$set": {"STATUS": FAILED}})
    col(PRINTERS).update_one({"PRINTER_ID": p["PRINTER_ID"]}, {"$set": {"LAST_ERROR": error}})
    return get(job_id)


def get(job_id):
    return col(JOBS).find_one({"JOB_ID": (job_id or "").strip().upper()}, {"_id": 0, "DATA": 0})


def recent(branch_id, limit=30):
    return list(col(JOBS).find({"BRANCH_ID": branch_id}, {"_id": 0, "DATA": 0}).sort([("CREATED", -1)]).limit(limit))


def reprint(branch_id, job_id, user_id, reason):
    old = get(job_id)
    if not old or old["BRANCH_ID"] != branch_id:
        raise ValidationError("prErrUnknown")
    if not clean_text(reason, 200):
        raise ValidationError("prErrReason")
    return submit(branch_id, old["DOC_KIND"], old["REF_ID"], user_id, old["PRINTER_ID"], old.get("COPIES", 1), old["JOB_ID"], reason)


def resume(app):
    """At start-up: jobs still queued from the last few minutes are sent; older ones are left for a manual reprint."""
    if app.config.get("TESTING"):
        return
    with app.app_context():
        since = now_utc() - timedelta(minutes=FRESH_MINUTES)
        for job in col(JOBS).find({"STATUS": QUEUED}, {"JOB_ID": 1, "CREATED": 1, "_id": 0}):
            if job["CREATED"] >= since:
                threading.Thread(target=_run, args=(app, job["JOB_ID"]), daemon=True).start()
            else:
                col(JOBS).update_one({"JOB_ID": job["JOB_ID"]}, {"$set": {"STATUS": FAILED, "ERROR": "not sent in time (server restarted): reprint"}})
