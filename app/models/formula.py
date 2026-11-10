"""Update 122: AI > Product transformation formulas (สูตรการแปรรูปสินค้า), AOIMS_FORMULA.

A formula turns a mill product (rice bran, husk, broken rice ...) into another product (fertilizer, charcoal ...). One
product can have any number of formulas. Each has a batch: ingredients with quantity, unit and price, other costs per
batch (labour, energy, packing), the output quantity and its selling price, process days, steps and a source link.

Costing is per branch (its prices and stock): an ingredient that is a mill SKU without its own price is valued at the
branch's current selling price - what the mill would get by selling it as it is (opportunity cost). The key figure is the
value per kg of the source product compared with that selling price: is converting better than selling raw?

No stock moves: production runs are not recorded (Tim chose formulas + profit first). The AI (background job, kind
"formula") compares the formulas of the branch and suggests which to try; it never invents prices.

Starter templates from public sources (Land Development Department compost, a bokashi recipe, rice-husk charcoal and
husk-charcoal briquettes) are added once by the seed with blank selling prices - the staff fill in local prices.
"""
import json
from datetime import timedelta

from .base import NO_ID, ValidationError, audit_new, audit_update, clean_text, col, round_half_up, to_number
from .counter import Counter
from .inventory import Inventory
from .price import Price
from . import variety as var
from ..timeutil import now_utc

COL = "AOIMS_FORMULA"
SOURCES = (var.MAT_BRAN, var.MAT_HUSK, var.MAT_BROKEN) + tuple(var.CODES[v][k] for v in var.VARIETIES for k in (var.RICE, var.PADDY))
UNITS = ("kg", "l", "pack", "piece", "bag", "set")
MAX_INPUTS = 10
QTY_MAX = 1_000_000
PRICE_MAX = 100_000
AI_LIMIT_PER_DAY = 10


# ---------- reading ----------
def all_formulas(source=None, active_only=False):
    q = {}
    if source:
        q["SOURCE_MAT"] = source
    if active_only:
        q["STATUS"] = "A"
    return list(col(COL).find(q, NO_ID).sort([("SOURCE_MAT", 1), ("FORMULA_NAME", 1)]))


def get(formula_id):
    return col(COL).find_one({"FORMULA_ID": formula_id}, NO_ID) if formula_id else None


# ---------- saving ----------
def _num(raw, low, high, key, **params):
    try:
        value = to_number(raw)
    except ValueError:
        raise ValidationError(key, **params)
    if value is not None and not (low <= value <= high):
        raise ValidationError(key, **params)
    return value


def parse_inputs(form):
    """Rows IN_MAT_n / IN_NAME_n / IN_QTY_n / IN_UNIT_n / IN_PRICE_n, n = 0..MAX_INPUTS-1; blank rows are skipped."""
    rows = []
    for n in range(MAX_INPUTS):
        mat = (form.get("IN_MAT_%d" % n) or "").strip()
        mat = mat if mat in var.ALL else None
        name = clean_text(form.get("IN_NAME_%d" % n), 60)
        qty_raw = (form.get("IN_QTY_%d" % n) or "").strip()
        if not mat and not name and not qty_raw:
            continue
        label = name or (var.mat_name(mat) if mat else "#%d" % (n + 1))
        if not mat and not name:
            raise ValidationError("fmErrInName", n=n + 1)
        qty = _num(qty_raw, 0, QTY_MAX, "fmErrInQty", item=label)
        if not qty:
            raise ValidationError("fmErrInQty", item=label)
        unit = form.get("IN_UNIT_%d" % n) if form.get("IN_UNIT_%d" % n) in UNITS else "kg"
        if mat:
            unit = "kg"                                            # mill SKUs are kept in kg
        price = _num(form.get("IN_PRICE_%d" % n), 0, PRICE_MAX, "fmErrInPrice", item=label)
        rows.append({"MAT_CODE": mat, "NAME": name or None, "QTY": round_half_up(qty, 3), "UNIT": unit,
                     "PRICE": None if price is None else round_half_up(price, 2)})
    return rows


def save(form, ope_user):
    name = clean_text(form.get("FORMULA_NAME"), 80)
    if not name:
        raise ValidationError("fmErrName")
    source = form.get("SOURCE_MAT")
    if source not in SOURCES:
        raise ValidationError("fmErrSource")
    output = clean_text(form.get("OUTPUT_NAME"), 60)
    if not output:
        raise ValidationError("fmErrOutput")
    out_qty = _num(form.get("OUTPUT_QTY"), 0, QTY_MAX, "fmErrOutQty")
    if not out_qty:
        raise ValidationError("fmErrOutQty")
    inputs = parse_inputs(form)
    if not inputs:
        raise ValidationError("fmErrNoInput")
    if not any(i["MAT_CODE"] == source for i in inputs):
        raise ValidationError("fmErrNoSource", mat=var.mat_name(source))
    days = _num(form.get("PROCESS_DAYS"), 0, 365, "fmErrDays")
    values = {
        "FORMULA_NAME": name, "SOURCE_MAT": source, "OUTPUT_NAME": output,
        "OUTPUT_UNIT": form.get("OUTPUT_UNIT") if form.get("OUTPUT_UNIT") in UNITS else "kg",
        "OUTPUT_QTY": round_half_up(out_qty, 3),
        "OUTPUT_PRICE": _price(form.get("OUTPUT_PRICE"), "fmErrOutPrice"),
        "OTHER_COST": _price(form.get("OTHER_COST"), "fmErrOther") or 0.0,
        "PROCESS_DAYS": None if days is None else round_half_up(days, 1),
        "INPUTS": inputs,
        "STEPS": (form.get("STEPS") or "").strip()[:3000] or None,
        "SOURCE_REF": clean_text(form.get("SOURCE_REF"), 300) or None,
        "NOTE": clean_text(form.get("NOTE"), 300) or None,
        "STATUS": "I" if form.get("STATUS") == "I" else "A",
    }
    current = get(form.get("FORMULA_ID"))
    if current:
        values.update(audit_update(ope_user))
        col(COL).update_one({"FORMULA_ID": current["FORMULA_ID"]}, {"$set": values})
        return get(current["FORMULA_ID"])
    values.update({"FORMULA_ID": "FM%04d" % Counter.next("FORMULA"), "TEMPLATE_FLG": False}, **audit_new(ope_user))
    col(COL).insert_one(dict(values))
    return get(values["FORMULA_ID"])


def form_doc(form):
    """What was typed, to show the form again after an error (values kept as typed)."""
    doc = {k: (form.get(k) or "").strip() or None for k in ("FORMULA_ID", "FORMULA_NAME", "SOURCE_MAT", "OUTPUT_NAME", "OUTPUT_UNIT",
                                                             "OUTPUT_QTY", "OUTPUT_PRICE", "OTHER_COST", "PROCESS_DAYS", "STEPS",
                                                             "SOURCE_REF", "NOTE", "STATUS")}
    doc["INPUTS"] = [{"MAT_CODE": form.get("IN_MAT_%d" % n) or None, "NAME": form.get("IN_NAME_%d" % n) or None,
                      "QTY": form.get("IN_QTY_%d" % n) or None, "UNIT": form.get("IN_UNIT_%d" % n) or "kg",
                      "PRICE": form.get("IN_PRICE_%d" % n) or None} for n in range(MAX_INPUTS)]
    return doc


def _price(raw, key):
    value = _num(raw, 0, PRICE_MAX, key)
    return None if value is None else round_half_up(value, 2)


def copy(formula_id, ope_user, suffix):
    """A new formula from an existing one (a variant: other ratios, other ingredients)."""
    src = get(formula_id)
    if not src:
        raise ValidationError("fmErrUnknown")
    doc = {k: v for k, v in src.items() if k not in ("FORMULA_ID", "OPE_USER", "REGIST_DATE", "LAST_UPDATE")}
    doc.update({"FORMULA_ID": "FM%04d" % Counter.next("FORMULA"), "FORMULA_NAME": (src["FORMULA_NAME"] + " " + suffix)[:80],
                "TEMPLATE_FLG": False}, **audit_new(ope_user))
    col(COL).insert_one(dict(doc))
    return get(doc["FORMULA_ID"])


def delete(formula_id):
    if not get(formula_id):
        raise ValidationError("fmErrUnknown")
    col(COL).delete_one({"FORMULA_ID": formula_id})


# ---------- costing ----------
def mill_price(branch_id, mat_code):
    """The branch's current selling price of a mill SKU (THB/kg), or None."""
    p = Price.get(branch_id, mat_code) or {}
    value = float(p.get("PRICE_SELL") or 0)
    return value or None


def cost(f, branch_id):
    """Batch costing of one formula at a branch. Missing prices are listed, never guessed."""
    lines, total, missing = [], 0.0, []
    source_kg = 0.0
    for i in f.get("INPUTS") or []:
        price, auto = i.get("PRICE"), False
        if price is None and i.get("MAT_CODE"):
            price, auto = mill_price(branch_id, i["MAT_CODE"]), True
        name = i.get("NAME") or var.mat_name(i.get("MAT_CODE"))
        amount = None if price is None else round_half_up(i["QTY"] * price, 2)
        if amount is None:
            missing.append(name)
        else:
            total += amount
        if i.get("MAT_CODE") == f["SOURCE_MAT"]:
            source_kg += i["QTY"]
        lines.append({"name": name, "mat": i.get("MAT_CODE"), "qty": i["QTY"], "unit": i["UNIT"], "price": price,
                      "auto": auto, "amount": amount})
    other = float(f.get("OTHER_COST") or 0)
    total += other
    out_price = f.get("OUTPUT_PRICE")
    revenue = None if out_price is None else round_half_up(f["OUTPUT_QTY"] * out_price, 2)
    if out_price is None:
        missing.append("__output__")
    complete = not missing
    profit = round_half_up(revenue - total, 2) if complete else None
    raw_price = mill_price(branch_id, f["SOURCE_MAT"])
    # what one kg of the source product earns in this formula: the revenue less every cost except the source itself
    per_kg = None
    if complete and source_kg:
        source_cost = sum(l["amount"] or 0 for l in lines if l["mat"] == f["SOURCE_MAT"])
        per_kg = round_half_up((revenue - (total - source_cost)) / source_kg, 2)
    free = Inventory.free_qty(branch_id, f["SOURCE_MAT"]) if source_kg else 0.0
    return {"lines": lines, "other": round_half_up(other, 2), "total": round_half_up(total, 2), "revenue": revenue,
            "profit": profit, "margin_pct": round_half_up(profit / revenue * 100, 1) if profit is not None and revenue else None,
            "unit_cost": round_half_up(total / f["OUTPUT_QTY"], 2) if complete and f["OUTPUT_QTY"] else None,
            "source_kg": round_half_up(source_kg, 3), "raw_price": raw_price, "per_kg": per_kg,
            "gain_kg": round_half_up(per_kg - raw_price, 2) if per_kg is not None and raw_price else None,
            "free": free, "batches": int(free // source_kg) if source_kg else 0,
            "complete": complete, "missing": missing}


def overview(branch_id, source=None):
    """Every formula with its costing, best first by value per kg of the source product."""
    out = [dict(f, c=cost(f, branch_id)) for f in all_formulas(source)]
    out.sort(key=lambda x: (x["STATUS"] != "A", x["c"]["per_kg"] is None, -(x["c"]["per_kg"] or 0), x["FORMULA_NAME"]))
    return out


def ranking(rows):
    """Per source product: active formulas with complete costing, best value per kg first (the comparison table)."""
    by = {}
    for r in rows:
        if r["STATUS"] == "A" and r["c"]["per_kg"] is not None:
            by.setdefault(r["SOURCE_MAT"], []).append(r)
    return [(m, by[m]) for m in SOURCES if m in by]


def counts(rows):
    return {"all": len(rows), "active": sum(1 for r in rows if r["STATUS"] == "A"),
            "ready": sum(1 for r in rows if r["STATUS"] == "A" and r["c"]["complete"]),
            "better": sum(1 for r in rows if r["STATUS"] == "A" and (r["c"]["gain_kg"] or 0) > 0)}


# ---------- AI ----------
def ai_input(branch_id):
    rows = [r for r in overview(branch_id) if r["STATUS"] == "A"]
    return {"branch": branch_id, "currency": "THB",
            "note": "per_kg = what 1 kg of the source product earns in the formula (revenue minus all other costs, per kg of "
                    "source); raw_price = the mill's current selling price of the source product; gain_kg = per_kg - raw_price; "
                    "free_stock_kg = the mill's own free stock of the source product; missing = prices still blank",
            "formulas": [{"id": r["FORMULA_ID"], "name": r["FORMULA_NAME"], "source": var.mat_name(r["SOURCE_MAT"], "en"),
                          "output": "%s %s %s" % (r["OUTPUT_QTY"], r["OUTPUT_UNIT"], r["OUTPUT_NAME"]),
                          "output_price": r.get("OUTPUT_PRICE"), "days": r.get("PROCESS_DAYS"),
                          "inputs": [{k: l[k] for k in ("name", "qty", "unit", "price")} for l in r["c"]["lines"]],
                          "other_cost": r["c"]["other"], "batch_cost": r["c"]["total"], "revenue": r["c"]["revenue"],
                          "profit": r["c"]["profit"], "per_kg": r["c"]["per_kg"], "raw_price": r["c"]["raw_price"],
                          "gain_kg": r["c"]["gain_kg"], "free_stock_kg": r["c"]["free"], "missing": r["c"]["missing"],
                          "steps": (r.get("STEPS") or "")[:600]} for r in rows]}


def _prompt(data, lang):
    return ("You advise a Thai rice mill (AOI Labs, Khon Kaen) on turning its by-products (rice bran, husk, broken rice) "
            "into higher-value products. Write in %s, short and practical, using only the figures given (THB). "
            "Sections: 1) Ranking: which formulas earn more than selling the source product as it is (gain_kg), best first, "
            "with profit per batch; 2) Formulas that cannot be judged yet (missing prices) and what to fill in; "
            "3) Practical risks per formula (supply of other ingredients, process days, quality, market for the output, "
            "rules such as fertilizer registration) in one line each; 4) Which 1-2 formulas to pilot first with a small batch "
            "and what to measure; 5) At most 3 other well-known products from these by-products worth adding as new formulas "
            "(name only, marked 'idea - needs checking'). Never invent prices or yields. Write only the final answer for the "
            "staff. Start the answer with the line ===ANSWER===.\n\nData (JSON):\n%s"
            % ("Thai" if lang == "th" else "English", json.dumps(data, ensure_ascii=False, default=str)))


def ai_start(branch_id, user_id, lang):
    """Runs the AI analysis in the background (ai_job kind "formula"). Returns the job."""
    from . import ai_job
    from .competitor import ai_ready, _ai_text
    if not ai_ready():
        raise ValidationError("coErrAiOff")
    data = ai_input(branch_id)
    if not data["formulas"]:
        raise ValidationError("fmErrAiNone")
    day_ago = now_utc() - timedelta(days=1)
    if col(ai_job.COL).count_documents({"KIND": "formula", "BRANCH_ID": branch_id, "REGIST_DATE": {"$gte": day_ago}}) >= AI_LIMIT_PER_DAY:
        raise ValidationError("coErrAiLimit", n=AI_LIMIT_PER_DAY)
    prompt = _prompt(data, lang)

    def work():
        text, model = _ai_text(prompt, lang=lang)
        if not text:
            raise ValidationError("coErrAiService")
        return {"text": text[:8000], "model": model, "lang": lang}
    return ai_job.start("formula", branch_id, user_id, work)


def last_ai(branch_id):
    from . import ai_job
    return col(ai_job.COL).find_one({"KIND": "formula", "BRANCH_ID": branch_id, "STATUS": "D"}, NO_ID, sort=[("REGIST_DATE", -1)])


# ---------- starter templates (public sources; selling prices left blank) ----------
TEMPLATES = [
    {"FORMULA_NAME": "ปุ๋ยโบกาฉิมูลสัตว์ (1:1:1)", "SOURCE_MAT": var.MAT_BRAN, "OUTPUT_NAME": "ปุ๋ยโบกาฉิ", "OUTPUT_UNIT": "kg",
     "OUTPUT_QTY": 270.0, "PROCESS_DAYS": 7.0, "OTHER_COST": 0.0,
     "INPUTS": [{"MAT_CODE": var.MAT_BRAN, "NAME": "รำละเอียด", "QTY": 100.0, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": None, "NAME": "มูลสัตว์", "QTY": 100.0, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": var.MAT_HUSK, "NAME": "แกลบดิบ", "QTY": 100.0, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": None, "NAME": "หัวเชื้อ EM", "QTY": 0.3, "UNIT": "l", "PRICE": None},
                {"MAT_CODE": None, "NAME": "กากน้ำตาล", "QTY": 0.3, "UNIT": "l", "PRICE": None}],
     "STEPS": "1. ผสมน้ำ กากน้ำตาล และหัวเชื้อ EM (น้ำ 20 ลิตร : EM 20 ซีซี : กากน้ำตาล 20 ซีซี)\n"
              "2. คลุกมูลสัตว์ แกลบ และรำให้เข้ากัน ค่อยๆ รดน้ำผสมจนชื้นพอกำเป็นก้อนได้แต่น้ำไม่ไหลออกตามนิ้ว\n"
              "3. ใส่กระสอบที่ระบายอากาศได้ประมาณ 3/4 ไม่ต้องอัดแน่น มัดปากหลวมๆ เก็บในที่ร่ม\n"
              "4. หมัก 7 วัน จนเย็น แห้ง ร่วน ไม่มีกลิ่น",
     "SOURCE_REF": "https://www.rakbankerd.com/agriculture/page.php?id=7146&s=tblplant",
     "NOTE": "แม่แบบ: อัตราส่วน 1:1:1 ตามแหล่งที่มา ปริมาณ EM/กากน้ำตาลคิดจากน้ำประมาณ 300 ลิตร ผลผลิต 270 กก. เป็นค่าประมาณ (ความชื้นหาย ~10%) ให้ชั่งจริงแล้วแก้"},
    {"FORMULA_NAME": "ปุ๋ยหมักสูตร พด.1 (กรมพัฒนาที่ดิน)", "SOURCE_MAT": var.MAT_HUSK, "OUTPUT_NAME": "ปุ๋ยหมัก", "OUTPUT_UNIT": "kg",
     "OUTPUT_QTY": 1000.0, "PROCESS_DAYS": 60.0, "OTHER_COST": 0.0,
     "INPUTS": [{"MAT_CODE": var.MAT_HUSK, "NAME": "แกลบ (วัสดุพืช)", "QTY": 500.0, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": None, "NAME": "ฟาง / ใบไม้ (วัสดุพืช)", "QTY": 500.0, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": None, "NAME": "มูลสัตว์", "QTY": 200.0, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": None, "NAME": "น้ำหมักชีวภาพจากปลา", "QTY": 9.0, "UNIT": "l", "PRICE": None},
                {"MAT_CODE": None, "NAME": "สารเร่ง พด.1", "QTY": 1.0, "UNIT": "pack", "PRICE": None}],
     "STEPS": "1. ละลาย พด.1 1 ซองในน้ำ 20 ลิตร คน 5-10 นาที\n"
              "2. แบ่งวัสดุพืชเป็น 3-4 ส่วน วางชั้นแรกกว้าง 2 ม. ยาว 3 ม. สูง 30-40 ซม. เหยียบให้แน่น รดน้ำ\n"
              "3. โรยมูลสัตว์ ราดน้ำหมักปลาและสารเร่งให้ทั่ว ทำซ้ำจนครบ 3-4 ชั้น คลุมกอง\n"
              "4. รักษาความชื้น 50-60% กลับกองทุก 7-10 วัน หมัก 45-60 วัน\n"
              "5. ปุ๋ยเสร็จ: สีน้ำตาลเข้มถึงดำ ร่วนยุ่ย (เฉลี่ย N 1.0% P 0.5% K 0.5%)",
     "SOURCE_REF": "https://www.ldd.go.th/WEB_Download/Data/Poster/Info_3.pdf",
     "NOTE": "แม่แบบ: สูตรต้นฉบับใช้วัสดุพืช 1,000 กก. (ไม่ได้กำหนดเป็นแกลบ) แกลบย่อยช้า จึงผสมกับฟาง/ใบไม้ครึ่งหนึ่ง ระยะเวลาอาจนานกว่า 60 วัน"},
    {"FORMULA_NAME": "ถ่านแกลบ (แกลบดำ / ไบโอชาร์)", "SOURCE_MAT": var.MAT_HUSK, "OUTPUT_NAME": "ถ่านแกลบ", "OUTPUT_UNIT": "kg",
     "OUTPUT_QTY": 37.0, "PROCESS_DAYS": 1.0, "OTHER_COST": 0.0,
     "INPUTS": [{"MAT_CODE": var.MAT_HUSK, "NAME": "แกลบดิบ", "QTY": 100.0, "UNIT": "kg", "PRICE": None}],
     "STEPS": "1. เผาแกลบแบบอับอากาศ (เตาเผาถ่านแกลบ / ถังเผา) จนแกลบเป็นสีดำทั้งหมด\n"
              "2. ดับไฟด้วยน้ำหรือปิดอากาศทันที ไม่ให้กลายเป็นขี้เถ้าขาว\n"
              "3. ผึ่งให้แห้ง บรรจุถุง ใช้ผสมดินปลูก / วัสดุเพาะ / ปรับปรุงดิน",
     "SOURCE_REF": "https://www.sciencedirect.com/science/article/pii/S2590123022001657",
     "NOTE": "แม่แบบ: ผลผลิตถ่าน 36.97% ของน้ำหนักแกลบ (ผลงานวิจัย เตาแบบต่อเนื่อง) เตาบ้านอาจได้น้อยกว่า ใส่ค่าแรง/เชื้อเพลิงในต้นทุนอื่น"},
    {"FORMULA_NAME": "ถ่านอัดแท่งจากถ่านแกลบ (แป้งมัน 5%)", "SOURCE_MAT": var.MAT_HUSK, "OUTPUT_NAME": "ถ่านอัดแท่ง", "OUTPUT_UNIT": "kg",
     "OUTPUT_QTY": 100.0, "PROCESS_DAYS": 5.0, "OTHER_COST": 0.0,
     "INPUTS": [{"MAT_CODE": var.MAT_HUSK, "NAME": "แกลบดิบ (เผาเป็นผงถ่าน ~37%)", "QTY": 260.0, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": None, "NAME": "แป้งมันสำปะหลัง", "QTY": 4.8, "UNIT": "kg", "PRICE": None},
                {"MAT_CODE": None, "NAME": "น้ำร้อน", "QTY": 96.0, "UNIT": "l", "PRICE": 0.0}],
     "STEPS": "1. เผาแกลบเป็นถ่าน บดเป็นผงละเอียด (แกลบ 260 กก. ได้ผงถ่านราว 96 กก.)\n"
              "2. ผสมผงถ่าน 500 ก. : แป้งมันสำปะหลัง 25 ก. : น้ำร้อน 500 มล. (แป้ง 5% ของผงถ่าน) นวดให้เข้ากัน\n"
              "3. อัดขึ้นรูปด้วยเครื่องอัดแท่ง\n"
              "4. ผึ่งในร่มก่อน แล้วตากแดด 4-5 วัน (ตากแดดจัดทันทีจะแตก)",
     "SOURCE_REF": "https://ph03.tci-thaijo.org/index.php/scicru/article/download/380/264/1094",
     "NOTE": "แม่แบบ: อัตราส่วนสูตรที่ดีที่สุดในงานวิจัย (ติดไฟง่าย ไหม้นานถึง 145 นาที) ผลผลิตหลังตากแห้งประมาณเท่าผงถ่าน+แป้ง ค่าไฟเครื่องบด/เครื่องอัดใส่ในต้นทุนอื่น"},
]


def seed_templates(db):
    """Adds the starter templates once (a counter marks it, so templates deleted later stay deleted)."""
    if db["AOISYS_COUNTER"].find_one({"COUNTER_KEY": "MIG-FORMULA-TPL"}, NO_ID):
        return 0
    n = 0
    for t in TEMPLATES:
        if db[COL].find_one({"FORMULA_NAME": t["FORMULA_NAME"]}, NO_ID):
            continue
        n += 1
        doc = dict(t, FORMULA_ID="FM%04d" % Counter.next("FORMULA"), OUTPUT_PRICE=None, STATUS="A", TEMPLATE_FLG=True,
                   **audit_new("SYSTEM"))
        doc["INPUTS"] = [dict(i) for i in t["INPUTS"]]
        db[COL].insert_one(doc)
    db["AOISYS_COUNTER"].insert_one({"COUNTER_KEY": "MIG-FORMULA-TPL", "SEQ": 1})
    return n
