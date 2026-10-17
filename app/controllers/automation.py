"""Automation: AI, Hardware Control, Electricity, Machine Usage Statistics (branch signed in to)."""
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for

from ..i18n import t
from ..models import ai, supply
from ..models.automation import scada as scada_data, SHIFTS
from ..models.automation import (COMMANDS, MACHINE_STATUSES, MACHINE_TYPES, RUNNING, SOURCE_USER, Machine, MachineCommand,
                                 Meter, elec_rate, paddy_milled, period_bounds, post_month_to_account, set_elec_rate)
from ..models.base import ValidationError
from ..security import can, current_user, operating_branch, require
from ..timeutil import fmt_datetime, local_tz, now_local, now_utc, parse_local_datetime
from .common import flash_error, flash_ok
from ..models.variety import mat_name

bp = Blueprint("automation", __name__, url_prefix="/automation")
AI_MENU, HARDWARE, ELECTRIC, USAGE, SCADA = "AT_AI", "AT_HARDWARE", "AT_ELECTRIC", "AT_MACHINE", "AT_SCADA"
STATUS_KEYS = {"R": "mcRunning", "I": "mcIdle", "F": "mcFault", "M": "mcMaintenance", "O": "mcOffline"}
STATUS_BADGE = {"R": "approved", "I": "manual", "F": "blocked", "M": "pending", "O": "neutral"}
TYPE_KEYS = {m: "mcType" + m.capitalize() for m in MACHINE_TYPES}
CMD_STATUS_KEYS = {"P": "cmdPending", "S": "cmdSent", "D": "cmdDone", "F": "cmdFailed", "X": "cmdExpired"}
PERIODS = (1, 7, 30)


def _user_id():
    return current_user()["USER_ID"]


def _days():
    try:
        days = int(request.args.get("days", 7))
    except ValueError:
        days = 7
    return days if days in PERIODS else 7


def _run(action, ok_key, endpoint, **values):
    try:
        result = action()
    except ValidationError as error:
        flash_error(error)
    else:
        if ok_key:
            flash_ok(ok_key, **(result if isinstance(result, dict) else {}))
    return redirect(url_for(endpoint, **values))


def _bars(values, w=360, h=190, pt=20, pb=34, pl=14, max_bw=26):
    """Bar geometry for a simple one-series chart: [(label, value, title)] -> list of dicts."""
    if not values:
        return []
    plot_h = h - pt - pb
    slot = (w - 2 * pl) / len(values)
    bw = min(max_bw, slot * 0.55)
    top = max([v for _l, v, _t in values] + [0.0001])
    out = []
    for i, (label, value, title) in enumerate(values):
        bh = max(value / top * plot_h, 2 if value > 0 else 0)
        x = pl + slot * i + (slot - bw) / 2
        out.append({"label": label, "value": value, "title": title, "x": round(x, 1), "y": round(pt + plot_h - bh, 1),
                    "w": round(bw, 1), "h": round(bh, 1), "cx": round(x + bw / 2, 1)})
    return out


# ---------- Hardware Control ----------
@bp.route("/hardware", methods=["GET", "POST"])
@require(HARDWARE)
def hardware():
    branch = operating_branch()
    if request.method == "POST":
        action = request.form.get("action")
        machine = Machine.find(branch, request.form.get("MACHINE_ID"))
        if action in ("status", "command", "key") and not machine:
            abort(404)
        if action == "status":
            if not can(HARDWARE, "edit"):
                abort(403)
            return _run(lambda: Machine.set_status(machine, request.form.get("MACHINE_STATUS"), SOURCE_USER, _user_id(),
                                                   note=request.form.get("NOTE")) and None, "msgMachineStatus", "automation.hardware")
        if action == "command":
            if not can(HARDWARE, "approve"):
                abort(403)

            def send():
                if request.form.get("CMD") == "SELECT":                   # update 43: the mill selector
                    done = Machine.select(machine, request.form.get("CMD_VALUE"), _user_id())
                    return {"cmd": t("cmdSelectTo", name=done["target"]), "name": done["name"]}
                cmd = MachineCommand.request(machine, request.form.get("CMD"), _user_id(), request.form.get("CMD_VALUE"))
                label = t("cmd" + cmd["CMD"].capitalize())
                if cmd.get("CMD_VALUE") is not None:
                    label += " %g Hz " % cmd["CMD_VALUE"]
                return {"cmd": label, "name": machine["MACHINE_NAME"]}
            if request.form.get("CMD") == "SELECT":                   # update 45: by hand = switched now (no device)
                def switch():
                    done = Machine.select(machine, request.form.get("CMD_VALUE"), _user_id())
                    key = "msgCommandSent" if done["cmd"] else "msgSelectorSwitched"
                    return key, {"cmd": t("cmdSelectTo", name=done["target"]), "name": done["name"], "target": done["target"]}
                if request.headers.get("X-AOI-Fetch") == "1":
                    try:
                        key, params = switch()
                    except ValidationError as error:
                        return jsonify({"ok": False, "message": t(error.key, **error.params)}), 422
                    return jsonify({"ok": True, "message": t(key, **params)})
                try:
                    key, params = switch()
                except ValidationError as error:
                    flash_error(error)
                else:
                    flash_ok(key, **params)
                return redirect(url_for("automation.scada" if request.form.get("back") == "scada" else "automation.hardware"))
            if request.headers.get("X-AOI-Fetch") == "1":     # update 40: the SCADA sends without leaving the page (full screen)
                try:
                    result = send()
                except ValidationError as error:
                    return jsonify({"ok": False, "message": t(error.key, **error.params)}), 422
                return jsonify({"ok": True, "message": t("msgCommandSent", **result)})
            return _run(send, "msgCommandSent", "automation.scada" if request.form.get("back") == "scada" else "automation.hardware")
        if action in ("save", "key"):
            if not can(HARDWARE, "create"):
                abort(403)
            if action == "key":
                def issue():
                    session["device_key"] = {"id": machine["MACHINE_ID"], "key": Machine.issue_key(branch, machine["MACHINE_ID"], _user_id())}
                return _run(issue, None, "automation.hardware", setup=1)
            creating = request.form.get("mode") == "new"
            return _run(lambda: Machine.save(branch, request.form, _user_id(), creating) and None, "msgMachineSaved",
                        "automation.hardware", setup=1)
        abort(400)
    machines = Machine.for_branch(branch)
    for m in machines:
        m["active"] = m.get("ACTIVE_FLG") is not False
        m["online"] = Machine.is_online(m)
        m["since"] = fmt_datetime(m.get("STATUS_TIME"))
        m["can_command"] = bool(m.get("CONTROL_FLG") and m.get("DEVICE_KEY_HASH") and m["online"])
        m["live"] = Machine.live(m) if m.get("DEVICE_KEY_HASH") and m["active"] else None
        if m.get("MACHINE_TYPE") == "MILL_SELECTOR":                     # update 43: switched by hand or by its device
            m["live"] = None
            m["targets"] = Machine.targets(m)
            m["can_command"] = m["active"] and (not (m.get("CONTROL_FLG") and m.get("DEVICE_KEY_HASH")) or m["online"])
    running_motors = {x["MACHINE_ID"] for x in Machine.running_motors(branch)}
    events = Machine.events(branch, now_utc() - timedelta(days=7), limit=20)
    names = {m["MACHINE_ID"]: m["MACHINE_NAME"] for m in machines}
    edit = Machine.find(branch, request.args.get("edit")) if request.args.get("edit") else None
    return render_template("automation/hardware.html", machines=machines, in_use=[m for m in machines if m["active"]], events=events, names=names,
                           commands=MachineCommand.for_branch(branch, 10), edit=edit, setup=bool(request.args.get("setup") or edit),
                           new_key=session.pop("device_key", None), meters=Meter.for_branch(branch), running_motors=running_motors,
                           status_keys=STATUS_KEYS, status_badge=STATUS_BADGE, type_keys=TYPE_KEYS, types=MACHINE_TYPES,
                           statuses=MACHINE_STATUSES, commands_all=COMMANDS, cmd_status_keys=CMD_STATUS_KEYS,
                           can_edit=can(HARDWARE, "edit"), can_command=can(HARDWARE, "approve"), can_setup=can(HARDWARE, "create"))


@bp.route("/hardware/live.json")
@require(HARDWARE)
def hardware_live():
    """Live values of the machines in use with a device (update 30), polled by js/hardware_live.js every 2 seconds."""
    branch = operating_branch()
    out = [Machine.live(m) for m in Machine.for_branch(branch, active_only=True) if m.get("DEVICE_KEY_HASH")]
    return jsonify({"ok": True, "machines": out, "can_command": can(HARDWARE, "approve")})


# ---------- SCADA (update 31) ----------
@bp.route("/scada")
@require(SCADA)
def scada():
    """The plant diagram with live states (js/scada.js reads /automation/scada.json every 2 seconds)."""
    branch = operating_branch()
    data = scada_data(branch)
    return render_template("automation/scada.html", data=data, branch=branch, type_keys=TYPE_KEYS, status_keys=STATUS_KEYS,
                           type_keys_text={k: t(v) for k, v in TYPE_KEYS.items()},
                           can_command=can(HARDWARE, "approve"), can_hardware=can(HARDWARE))


@bp.route("/scada.json")
@require(SCADA)
def scada_json():
    return jsonify(dict(scada_data(operating_branch()), ok=True, can_command=can(HARDWARE, "approve")))


# ---------- Machine Usage Statistics ----------
@bp.route("/usage")
@require(USAGE)
def usage():
    branch = operating_branch()
    days = _days()
    start, end = period_bounds(days)
    machines = Machine.for_branch(branch)
    rows = []
    for m in list(machines):
        u = Machine.usage(m, start, end)
        if m.get("ACTIVE_FLG") is False and not any(u["hours"][s] for s in (RUNNING, "I", "F", "M")):
            machines.remove(m)                      # planned machine without any history: not listed (update 31)
            continue
        metered = Meter.used_between(branch, start, end, m["METER_ID"]) if m.get("METER_ID") else None
        rows.append({"m": m, "u": u, "metered": metered})
    paddy = paddy_milled(branch, start, end)
    run_total = sum(r["u"]["hours"][RUNNING] for r in rows)
    bars = _bars([(r["m"]["MACHINE_ID"], r["u"]["hours"][RUNNING], "%s · %.1f h" % (r["m"]["MACHINE_NAME"], r["u"]["hours"][RUNNING])) for r in rows])
    daily = {}
    for m in machines:
        for day, hours in Machine.daily_run_hours(m, 14):
            daily[day] = daily.get(day, 0.0) + hours
    trend = _bars([(d.strftime("%d/%m"), round(h, 2), "%s · %.1f h" % (d.strftime("%d/%m"), h)) for d, h in sorted(daily.items())])
    return render_template("automation/usage.html", rows=rows, days=days, periods=PERIODS, paddy=paddy, run_total=run_total,
                           kg_per_hour=(paddy / run_total) if run_total else None, bars=bars, trend=trend,
                           status_keys=STATUS_KEYS, statuses=MACHINE_STATUSES, type_keys=TYPE_KEYS)


# ---------- Electricity ----------
@bp.route("/electricity", methods=["GET", "POST"])
@require(ELECTRIC)
def electricity():
    branch = operating_branch()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "reading":
            if not can(ELECTRIC, "create"):
                abort(403)
            meter = Meter.find(branch, request.form.get("METER_ID"))
            if not meter:
                abort(404)
            when = parse_local_datetime(request.form.get("READ_DATE")) if request.form.get("READ_DATE") else None
            if when is not None and abs((now_utc() - when).total_seconds()) < 60:
                when = None                     # the form's default "now" (minutes only): keep the exact time
            return _run(lambda: Meter.add_reading(meter, request.form.get("READ_KWH"), when, SOURCE_USER, _user_id()) and None,
                        "msgReadingSaved", "automation.electricity")
        if action == "rate":
            if not can(ELECTRIC, "edit"):
                abort(403)
            return _run(lambda: set_elec_rate(branch, request.form.get("SYSP_ELEC_RATE"), _user_id()) and None,
                        "msgElecRateSaved", "automation.electricity")
        if action == "post":
            if not can(ELECTRIC, "approve"):
                abort(403)
            try:
                year, month = (int(x) for x in request.form.get("MONTH", "").split("-"))
            except ValueError:
                abort(400)

            def post():
                entry, kwh = post_month_to_account(branch, year, month, t("elecAcctDesc", month="%02d/%04d" % (month, year)) + " (%(kwh)s kWh)", _user_id())
                return {"amount": "%.2f" % entry["ACC_AMOUNT"]}
            return _run(post, "msgElecPosted", "automation.electricity")
        if action in ("meter", "key"):
            if not can(ELECTRIC, "create"):
                abort(403)
            if action == "key":
                meter = Meter.find(branch, request.form.get("METER_ID"))
                if not meter:
                    abort(404)

                def issue():
                    session["device_key"] = {"id": meter["METER_ID"], "key": Meter.issue_key(branch, meter["METER_ID"], _user_id())}
                return _run(issue, None, "automation.electricity", setup=1)
            creating = request.form.get("mode") == "new"
            return _run(lambda: Meter.save(branch, request.form, _user_id(), creating) and None, "msgMeterSaved",
                        "automation.electricity", setup=1)
        abort(400)
    days = _days()
    start, end = period_bounds(days)
    rate = elec_rate(branch)
    kwh = Meter.used_between(branch, start, end)
    paddy = paddy_milled(branch, start, end)
    meters = Meter.for_branch(branch)
    for m in meters:
        last = Meter.last_reading(m["METER_ID"])
        m["last"] = last
        m["period_kwh"] = Meter.used_between(branch, start, end, m["METER_ID"])
        m["online"] = bool(m.get("LAST_SEEN")) and (now_utc() - m["LAST_SEEN"]).total_seconds() <= 3600
    daily = Meter.daily_use(branch, 14)
    bars = _bars([(d.strftime("%d/%m"), v, "%s · %.1f kWh · %.2f THB" % (d.strftime("%d/%m"), v, v * rate)) for d, v in daily])
    today = now_local()
    this_month = "%04d-%02d" % (today.year, today.month)
    prev = (today.replace(day=1) - timedelta(days=1))
    prev_month = "%04d-%02d" % (prev.year, prev.month)
    edit = Meter.find(branch, request.args.get("edit")) if request.args.get("edit") else None
    return render_template("automation/electricity.html", days=days, periods=PERIODS, rate=rate, kwh=kwh, cost=kwh * rate,
                           paddy=paddy, kwh_per_kg=(kwh / paddy) if paddy else None, meters=meters, bars=bars,
                           readings=Meter.readings(branch, now_utc() - timedelta(days=60), limit=15),
                           months=[this_month, prev_month], machines=Machine.for_branch(branch), edit=edit,
                           setup=bool(request.args.get("setup") or edit), new_key=session.pop("device_key", None),
                           now_input=now_local().strftime("%Y-%m-%dT%H:%M"),
                           can_read=can(ELECTRIC, "create"), can_rate=can(ELECTRIC, "edit"), can_post=can(ELECTRIC, "approve"),
                           can_setup=can(ELECTRIC, "create"))


# ---------- AI ----------
@bp.route("/ai", methods=["GET", "POST"])
@require(AI_MENU)
def ai_screen():
    branch = operating_branch()
    answer = None
    if request.method == "POST":
        if not can(AI_MENU, "create"):
            abort(403)
        if request.form.get("action") == "health":             # update 48: check the server now
            from ..models import health
            doc = health.check(sample_seconds=1.0)
            flash_ok("hlChecked", level=t("hlLevel_" + doc["LEVEL"]))
            return redirect(url_for("automation.ai_screen", _anchor="ai-health"))
        if request.form.get("action") == "supply":
            try:
                n = supply.fetch(_user_id())
            except ValidationError as error:
                flash_error(error)
            else:
                flash_ok("spFetched", n=n)
            return redirect(url_for("automation.ai_screen", _anchor="ai-supply"))
        try:
            answer = ai.ask(branch, _user_id(), request.form.get("QUESTION"), session.get("lang", "th"))
        except ValidationError as error:
            flash_error(error)
            return redirect(url_for("automation.ai_screen", _anchor="ai-ask"))
        session["ai_answer"] = {"q": request.form.get("QUESTION"), "a": answer}
        return redirect(url_for("automation.ai_screen", _anchor="ai-ask"))
    from ..models import health
    hl = health.latest()
    hl_logs = health.recent_logs(20)
    for d in hl_logs:
        d["when"] = fmt_datetime(d.get("LAST_DATE"))
    fc = ai.forecast(branch)
    bars = _bars([(f["day"].strftime("%d/%m"), f["kg"], "%s · %.0f kg · %.1f %s" % (f["day"].strftime("%d/%m"), f["kg"], f["orders"], t("aiOrders")))
                  for f in fc], w=640, max_bw=44)
    advice = ai.price_advice(branch)
    for a in advice:
        a["name"] = mat_name(a["mat"])
    alerts = ai.anomalies(branch)
    for a in alerts:
        if "mat" in a["params"]:
            a["params"] = dict(a["params"], mat=mat_name(a["params"]["mat"]))
    sp = supply.summary()
    sp_bars = _bars([("%s/%s" % (d["PERIOD"][5:], str(d["YEAR_TH"])[2:]), d["INDEX_VALUE"],
                      "%s/%s · %.1f" % (d["PERIOD"][5:], d["YEAR_TH"], d["INDEX_VALUE"])) for d in supply.series(sp["scope"] if sp else None, 24)], w=640, max_bw=22)
    from ..models import monitor, price_signal
    signals = price_signal.overview(branch)
    hist = health.history(24)
    health_hist = {"disk": [h["METRICS"]["disk"]["used_pct"] for h in hist if h.get("METRICS", {}).get("disk")],
                   "memory": [h["METRICS"]["memory"]["used_pct"] for h in hist if h.get("METRICS", {}).get("memory")],
                   "cpu": [(h["METRICS"].get("cpu") or {}).get("used_pct") for h in hist if h.get("METRICS")]}
    counts = {"buy": sum(1 for o in signals if o["label"] in ("buy", "strong_buy")),
              "sell": sum(1 for o in signals if o["label"] in ("sell", "strong_sell")),
              "hold": sum(1 for o in signals if o["label"] == "hold")}
    return render_template("automation/ai.html", signals=signals, sig_counts=counts, health_hist=health_hist,
                           api_hourly=monitor.api_hourly(24), fc_total=round(sum(f["kg"] for f in fc)), health=hl, health_logs=hl_logs, health_time=fmt_datetime(hl["CHECK_DATE"]),
                           health_q=t("hlAskQuestion"), supply=sp, supply_bars=sp_bars, can_ask_fetch=can(AI_MENU, "create"), forecast=fc, bars=bars, queue=ai.queue_load(branch), advice=advice,
                           alerts=alerts, configured=ai.ai_configured(), last=session.pop("ai_answer", None),
                           recent=ai.recent_questions(branch, _user_id()), can_ask=can(AI_MENU, "create"),
                           basis=min((f["basis"] for f in fc), default=0))


# ---------- AI > Price analysis (update 50) ----------
@bp.route("/ai/prices", methods=["GET", "POST"])
@require(AI_MENU)
def ai_prices():
    from ..models import export_price, price_signal, variety as var
    branch = operating_branch()
    code = request.values.get("sku") if request.values.get("sku") in var.TRADED else "PADDY_HM"
    period = request.values.get("p") if request.values.get("p") in price_signal.PERIODS else "12M"
    back = lambda anchor=None: redirect(url_for("automation.ai_prices", sku=code, p=period, _anchor=anchor))
    if request.method == "POST":
        if not can(AI_MENU, "create"):
            abort(403)
        action = request.form.get("action")
        try:
            if action == "fetch":
                r = export_price.fetch(_user_id())
                flash_ok("psFetched", rows=r["rows"], new=r["new"], day=r["newest"])
                return back("ps-sources")
            if action == "import":
                upload = request.files.get("PAGE_FILE")
                data = upload.read(15 * 1024 * 1024) if upload else b""
                if not data:
                    raise ValidationError("psErrNoFile")
                r = export_price.import_data(data, "upload:" + (upload.filename or "file")[:80], _user_id())
                flash_ok("psFetched", rows=r["rows"], new=r["new"], day=r["newest"])
                return back("ps-sources")
            if action == "settings":
                if not can(AI_MENU, "edit"):
                    abort(403)
                price_signal.save_settings(request.form, _user_id())
                flash_ok("psSaved")
                return back("ps-settings")
            if action == "ask":
                a = price_signal.analyse(branch, code, period)
                s = a["signal"] or {}
                q = t("psAskQuestion", name=a["name"], label=t("psLabel_" + (s.get("label") or "hold")))
                answer = ai.ask(branch, _user_id(), q, session.get("lang", "th"))
                session["ps_answer"] = {"q": q, "a": answer, "sku": code}
                return back("ps-ask")
        except ValidationError as error:
            flash_error(error)
            return back()
        abort(400)
    cfg = price_signal.settings()
    entry = request.args.get("entry")
    try:
        entry = float(entry) if entry and float(entry) > 0 else None
    except ValueError:
        entry = None
    items = export_price.items()
    keys = [i["key"] for i in items]
    a = price_signal.analyse(branch, code, period, cfg, entry, keys)
    answer = session.get("ps_answer")
    if answer and answer.get("sku") != code:
        answer = None
    last_fetch = export_price.latest_fetch()
    return render_template("automation/ai_prices.html", a=a, code=code, period=period, periods=list(price_signal.PERIODS),
                           cfg=cfg, overview=price_signal.overview(branch, cfg), src_names={x: t("psSrc_" + x) for x in price_signal.SOURCES}, items=items, sources=price_signal.SOURCES,
                           trea_keys={c: price_signal.trea_key(c, cfg, keys) for c in var.TRADED}, traded=var.TRADED,
                           last_fetch=fmt_datetime(last_fetch["FETCH_DATE"]) if last_fetch else None, entry=entry,
                           export_url=current_app.config["EXPORT_URL"], answer=answer,
                           configured=ai.ai_configured(), can_fetch=can(AI_MENU, "create"), can_edit=can(AI_MENU, "edit"))
