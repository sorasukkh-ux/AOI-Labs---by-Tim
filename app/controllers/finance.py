"""Financial: Pricing Maintenance, Margin Maintenance, Account, Marketing, Investment & Break-even (draft).

Pricing, Margin and Marketing are maintenance screens: the branch can be chosen (within the user's authorized
branches). Account shows the ledger of the branch signed in to.
"""
from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for

from ..i18n import t
from ..models.account import Account
from ..models.base import ValidationError
from ..models.price import CUSTOM, WAITING, Price, Promotion
from ..security import can, current_user, maintainable_branches, operating_branch, pick_maintenance_branch, require
from ..timeutil import fmt_date, today_local, to_local
from .common import flash_error, flash_ok
from ..models import investment as investment_model, market, market_history, nabc_price, profit, variety as var
from ..models.variety import mat_name

bp = Blueprint("finance", __name__, url_prefix="/finance")
PRICING, MARGIN, ACCOUNT, MARKETING = "FN_PRICING", "FN_MARGIN", "FN_ACCOUNT", "FN_MARKETING"
STATUS_INFO = {"W": ("statusPending", "pending", "btnApprove"), "A": ("statusApproved", "approved", "btnEdit"),
               "C": ("statusManual", "manual", "btnSave")}
STATUS_FILTER = {"pending": "W", "approved": "A", "manual": "C"}


def _user_id():
    return current_user()["USER_ID"]


def _branch_or_403():
    requested = request.values.get("branch")
    branch = pick_maintenance_branch(requested)
    if not branch or (requested and requested != branch):
        abort(403)
    return branch


def _run(action, ok_key, endpoint, **values):
    try:
        action()
    except ValidationError as error:
        if error.key in ("errFloorCeiling",) and "mat" in error.params:
            error.params["mat"] = mat_name(error.params["mat"])
        flash_error(error)
    else:
        flash_ok(ok_key)
    return redirect(url_for(endpoint, **values))


# ---------- Pricing Maintenance ----------
@bp.route("/pricing", methods=["GET", "POST"])
@require(PRICING)
def pricing():
    branch = _branch_or_403()
    if request.method == "POST":
        if not can(PRICING, "edit"):
            abort(403)
        mat = request.form.get("MAT_CODE")
        if request.form.get("action") == "fetch":
            return _fetch_all("finance.pricing", branch=branch)
        if request.form.get("action") == "market":
            action = lambda: Price.update_market(branch, mat, request.form, _user_id())
            ok = "msgMarketSaved"
        else:
            action = lambda: Price.save_price(branch, mat, request.form, can(PRICING, "approve"), _user_id())
            ok = "confirmPriceSaved"
        return _run(action, ok, "finance.pricing", branch=branch, f=request.form.get("f") or None)
    status_filter = STATUS_FILTER.get(request.args.get("f"))
    variety_filter = request.args.get("v") if request.args.get("v") in var.VARIETIES else None
    evaluations = profit.evaluate_all(branch)
    by_paddy = {e["paddy"]: e for e in evaluations}
    cards = []
    for price in Price.for_branch(branch):
        info = STATUS_INFO.get(price.get("PRICE_STATUS"), STATUS_INFO["A"])
        sug_buy, sug_sell = Price.suggestion(price)
        pending = price.get("PRICE_STATUS") == WAITING
        check = by_paddy.get(price["MAT_CODE"])
        market_sug_buy = sug_buy
        if pending and check and check["ok"] and check["limited"] and check["recommended"]:
            sug_buy = check["recommended"]                 # milling would not reach the target margin at the market price
        cards.append(dict(price, name=mat_name(price["MAT_CODE"]), status_key=info[0], badge=info[1], check=check, market_sug_buy=market_sug_buy,
                          button_key=info[2], custom=price.get("PRICE_STATUS") == CUSTOM, pending=pending,
                          sug_buy=sug_buy, sug_sell=sug_sell, margins=Price.margins(price),
                          input_buy=(sug_buy if pending and sug_buy is not None else price.get("PRICE_BUY")),
                          input_sell=(sug_sell if pending and sug_sell is not None else price.get("PRICE_SELL")),
                          shown=(not status_filter or price.get("PRICE_STATUS") == status_filter)
                          and (not variety_filter or var.variety_of(price["MAT_CODE"]) in (variety_filter, None))))
    updated = max((c["LAST_UPDATE"] for c in cards if c.get("LAST_UPDATE")), default=None)
    from ..models.ai import ai_configured
    return render_template("finance/pricing.html", branch=branch, branches=maintainable_branches(), cards=cards,
                           f=request.args.get("f"), v=variety_filter, updated=updated, can_edit=can(PRICING, "edit"),
                           can_approve=can(PRICING, "approve"), evaluations=evaluations, report=market.MarketReport.latest(),
                           can_ask=ai_configured() and can("AT_AI", "create"))


def _fetch_all(endpoint, **values):
    """Fetch now: NABC daily prices and the association report, one message for both."""
    result = market.fetch_all(_user_id(), force=False)
    applied = dict((result["nabc"] or {}).get("applied") or {})
    report = result["report"]
    if report and not report.get("ALREADY"):
        applied.update(report.get("APPLIED") or {})
    flash_ok("mkFetchedAll", n=sum(1 for s in applied.values() if s == "applied"),
             check=sum(1 for s in applied.values() if s in ("jump", "missing")),
             new=(result["nabc"] or {}).get("new", 0))
    for error in result["errors"]:
        flash_error(error)
    return redirect(url_for(endpoint, **values))


def _fetch_market(action, endpoint, **values):
    try:
        report = action()
    except ValidationError as error:
        flash_error(error)
    else:
        if report.get("ALREADY"):
            flash_ok("mkAlready", date=fmt_date(report.get("REPORT_DATE")) if report.get("REPORT_DATE") else "-")
        else:
            applied = report.get("APPLIED") or {}
            flash_ok("mkFetched", n=sum(1 for s in applied.values() if s == "applied"),
                     check=sum(1 for s in applied.values() if s in ("jump", "missing")))
    return redirect(url_for(endpoint, **values))


# ---------- Market prices (source and mapping) ----------
@bp.route("/market", methods=["GET", "POST"])
@require(PRICING)
def market_source():
    if request.method == "POST":
        if not can(PRICING, "edit"):
            abort(403)
        action = request.form.get("action")
        if action == "fetch":
            return _fetch_all("finance.market_source")
        if action == "history":
            return _download_history()
        if action == "upload":
            upload = request.files.get("REPORT_PDF")
            data = upload.read() if upload else b""
            name = (upload.filename if upload else "") or "upload.pdf"
            return _fetch_market(lambda: market.MarketReport.import_pdf(data, "upload:" + name, market.report_date(name), _user_id()),
                                 "finance.market_source")
        if action == "apply":
            def again():
                report = market.MarketReport.latest(ok_only=True) or {"REPORT_ID": None, "ROWS": []}
                applied = market.apply_nabc(_user_id())
                if report.get("REPORT_ID"):
                    applied.update(market.apply_report(report, _user_id()))
                report["APPLIED"] = applied
                return report
            return _fetch_market(again, "finance.market_source")
        return _run(lambda: market.MarketMap.save(request.form, _user_id()), "mkMapSaved", "finance.market_source")
    report = market.MarketReport.latest(ok_only=True)
    rows = (report or {}).get("ROWS") or []
    resolved = market.MarketMap.resolve(rows)
    nabc = market.MarketMap.resolve_nabc()
    maps = []
    for m in market.MarketMap.all():
        row = market.MarketMap.match(m, rows)
        maps.append(dict(m, name=mat_name(m["MAT_CODE"]), row=row, found=resolved.get(m["MAT_CODE"]), nabc=nabc.get(m["MAT_CODE"]),
                         applied=((report or {}).get("APPLIED") or {}).get(m["MAT_CODE"])))
    branch = request.args.get("branch") if request.args.get("branch") in maintainable_branches() else operating_branch()
    chart = market_history.series(branch, "36M")
    period = request.args.get("p") if request.args.get("p") in market_history.PERIODS else "12M"
    return render_template("finance/market.html", branch=branch, chart=chart, period=period, periods=list(market_history.PERIODS),
                           summary=_history_summary(chart), info=market_history.history_info(), report=report, last=market.MarketReport.latest(), rows=rows, maps=maps,
                           history=market.MarketReport.recent(8), source_url=current_app.config["MARKET_URL"],
                           units=list(market.UNITS), picks=market.COLUMNS, sources=market.MarketMap.SOURCES,
                           stats=nabc_price.STATS, nabc_url=current_app.config["NABC_PRICE_URL"],
                           nabc_products=[(m["NABC_PRODUCT"], m["nabc"]) for m in maps if m["SOURCE"] == "NABC" and m.get("NABC_PRODUCT")], can_edit=can(PRICING, "edit"),
                           max_jump=market.MAX_JUMP_PCT, auto=current_app.config.get("MARKET_AUTO"),
                           hour=current_app.config.get("MARKET_HOUR"))


def _history_summary(chart):
    """{period: [{'name', 'source', 'n', 'last', 'low', 'high', 'avg', 'change'}]} for the table under the graph."""
    out = {}
    for period in market_history.PERIODS:
        start = market_history.start_day(period).isoformat()
        rows = []
        for s in chart["skus"]:
            values = [p[1] for p in s["points"] if p[0] >= start]
            row = {"name": s["name"], "source": s["source"], "n": len(values)}
            if values:
                row.update(last=values[-1], low=min(values), high=max(values), avg=sum(values) / len(values),
                           change=(values[-1] - values[0]) / values[0] * 100 if values[0] else 0.0)
            rows.append(row)
        out[period] = rows
    return out


def _download_history():
    """Market Price > Download price history: all NABC pages of the mapped products, and the older association
    reports its page still lists (stored for the graph only)."""
    try:
        saved = market_history.download_nabc_history()
    except ValidationError as error:
        flash_error(error)
        saved = {}
    reports = 0
    if any(m["SOURCE"] == "TRMA" for m in market.MarketMap.all()):
        try:
            reports = market_history.download_trma_history(_user_id())
        except ValidationError as error:
            flash_error(error)
    flash_ok("mkhHistoryDone", n=sum(saved.values()), reports=reports)
    return redirect(url_for("finance.market_source", _anchor="market-history"))


# ---------- Margin Maintenance ----------
@bp.route("/margin", methods=["GET", "POST"])
@require(MARGIN)
def margin():
    branch = _branch_or_403()
    if request.method == "POST":
        if not can(MARGIN, "edit"):
            abort(403)
        return _run(lambda: Price.save_margins(branch, request.form, _user_id()), "msgMarginSaved", "finance.margin", branch=branch)

    def rng(low, high):
        return "" if low is None or high is None else "%g-%g" % (low, high)
    cards = [dict(p, name=mat_name(p["MAT_CODE"]), custom=p.get("PRICE_STATUS") == CUSTOM,
                  buy_range=rng(p.get("MG_BUY_RANGEFROM"), p.get("MG_BUY_RANGETO")),
                  sell_range=rng(p.get("MG_SELL_RANGEFROM"), p.get("MG_SELL_RANGETO")))
             for p in Price.for_branch(branch)]
    return render_template("finance/margin.html", branch=branch, branches=maintainable_branches(), cards=cards,
                           can_edit=can(MARGIN, "edit"))


# ---------- Account ----------
@bp.route("/account", methods=["GET", "POST"])
@require(ACCOUNT)
def account():
    branch = operating_branch()
    if request.method == "POST":
        if request.form.get("action") == "delete":
            if not can(ACCOUNT, "delete"):
                abort(403)
            return _run(lambda: Account.delete_manual(branch, request.form.get("ACC_ID"), _user_id()), "msgAcctDeleted",
                        "finance.account", f=request.form.get("f") or None)
        if not can(ACCOUNT, "create"):
            abort(403)
        return _run(lambda: Account.add_manual(branch, request.form, _user_id()), "msgAcctAdded", "finance.account",
                    f=request.form.get("f") or None)
    acc_type = {"income": "IN", "expense": "EX"}.get(request.args.get("f"))
    income, expense, net = Account.totals(branch)
    rows = []
    for e in Account.entries(branch, acc_type):
        ref_url = None
        if e.get("REF_ORD_TYPE") == "ML" and can("SV_MILLSTATUS"):
            ref_url = url_for("service_milling.order_detail", ord_id=e["REF_ORD_ID"])
        elif e.get("REF_ORD_TYPE") in ("BY", "SL") and can("SV_BSSTATUS"):
            ref_url = url_for("service_buysell.billing", ids=e["REF_ORD_ID"])
        rows.append(dict(e, date=fmt_date(e["ACC_DATE"]), ref_url=ref_url))
    return render_template("finance/account.html", rows=rows, income=income, expense=expense, net=net,
                           f=request.args.get("f"), today=today_local().isoformat(),
                           can_create=can(ACCOUNT, "create"), can_delete=can(ACCOUNT, "delete"),
                           budget_cats=investment_model.BUDGET_CATS)


# ---------- Marketing ----------
@bp.route("/marketing", methods=["GET", "POST"])
@require(MARKETING)
def marketing():
    branch = _branch_or_403()
    if request.method == "POST":
        if not can(MARKETING, "edit"):
            abort(403)
        return _run(lambda: Promotion.save(branch, Promotion.MILL_OWN, request.form, _user_id()), "msgPromoSaved",
                    "finance.marketing", branch=branch)
    promo = Promotion.get(branch, Promotion.MILL_OWN) or {"DISCOUNT_PCT": 10.0, "PROMO_STATUS": "I"}
    state = Promotion.state(promo, today_local())
    pct = float(promo.get("DISCOUNT_PCT") or 0)
    examples = []
    for mat in ("BRAN", "BROKEN"):
        sell = Price.sell_price(branch, mat)
        examples.append({"name": mat_name(mat), "sell": sell, "discounted": round(sell * (1 - pct / 100.0), 2)})

    def day(value):
        return to_local(value).date().isoformat() if value else ""
    return render_template("finance/marketing.html", branch=branch, branches=maintainable_branches(), promo=promo,
                           state=state, pct=pct, examples=examples, valid_from=day(promo.get("VALID_FROM")),
                           valid_to=day(promo.get("VALID_TO")), can_edit=can(MARKETING, "edit"))


# ---------- Investment & Break-even (draft, update 27) ----------
INVEST = "FN_INVEST"
INVEST_TABS = ("capex", "budget", "breakeven")


def _year():
    try:
        year = int(request.values.get("year") or today_local().year)
    except ValueError:
        year = today_local().year
    return min(max(year, 2000), 2100)


@bp.route("/investment", methods=["GET", "POST"])
@require(INVEST)
def investment():
    inv = investment_model
    branch = _branch_or_403()
    tab = request.values.get("tab") if request.values.get("tab") in INVEST_TABS else "capex"
    year = _year()
    back = dict(branch=branch, tab=tab, year=year)
    if request.method == "POST":
        action = request.form.get("action")
        perm = {"capex_delete": "delete", "sample": "create", "capex_add": "create"}.get(action, "edit")
        if action == "capex_save" and not request.form.get("CAPEX_ID"):
            perm = "create"
        if not can(INVEST, perm):
            abort(403)
        actions = {
            "capex_save": (lambda: inv.save_capex(branch, request.form, _user_id()), "ivMsgCapexSaved"),
            "capex_delete": (lambda: inv.delete_capex(branch, request.form.get("CAPEX_ID"), _user_id()), "ivMsgCapexDeleted"),
            "project": (lambda: inv.save_project(branch, request.form, _user_id()), "ivMsgProjectSaved"),
            "budget": (lambda: inv.save_budget(branch, year, request.form, _user_id()), "ivMsgBudgetSaved"),
            "budget_copy": (lambda: inv.copy_budget(branch, year, _user_id()), "ivMsgBudgetCopied"),
            "budget_fill": (lambda: inv.fill_from_settings(branch, year, _user_id()), "ivMsgBudgetFilled"),
            "sample": (lambda: inv.load_sample(branch, _user_id()), "ivMsgSample"),
        }
        if action not in actions:
            abort(400)
        return _run(actions[action][0], actions[action][1], "finance.investment", **back)
    data = inv.analyse(branch, year)
    edit_id = request.args.get("edit")
    editing = next((i for i in data["items"] if i["CAPEX_ID"] == edit_id), None)
    for item in data["items"]:
        item["date"] = fmt_date(item["BUY_DATE"]) if item.get("BUY_DATE") else "-"
    by_cat = {}
    for item in data["items"]:
        by_cat[item["CAPEX_CAT"]] = by_cat.get(item["CAPEX_CAT"], 0.0) + item["CAPEX_AMOUNT"]
    chart = dict(data["chart"], text={"x": t("ivChartX"), "y": t("ivChartY"), "rev": t("ivChartRevenue"),
                                      "cost": t("ivChartCost"), "fixed": t("ivChartFixed"), "be": t("ivChartBe"),
                                      "planned": t("ivChartPlanned"), "capacity": t("ivChartCapacity"),
                                      "profit": t("ivChartProfit"), "loss": t("ivChartLoss"), "kg": t("ivKgMonth")})
    proj = data["project"]
    return render_template(
        "finance/investment.html", branch=branch, branches=maintainable_branches(), tab=tab, year=year, d=data,
        bva=inv.budget_vs_actual(branch, year) if tab == "budget" else None, editing=editing, by_cat=by_cat,
        capex_cats=inv.CAPEX_CATS, budget_cats=inv.BUDGET_CATS, funds=inv.FUNDS, chart=chart, varieties=var.VARIETIES,
        start_date=to_local(proj["START_DATE"]).date().isoformat() if proj.get("START_DATE") else "",
        edit_date=to_local(editing["BUY_DATE"]).date().isoformat() if editing and editing.get("BUY_DATE") else today_local().isoformat(),
        years=list(range(today_local().year - 2, today_local().year + 3)),
        can_create=can(INVEST, "create"), can_edit=can(INVEST, "edit"), can_delete=can(INVEST, "delete"))


# ---------- Christmas / New Year campaign (update 93) ----------
CP_TYPE_KEYS = {"ML": "cpTypeML", "BY": "cpTypeBY", "SL": "cpTypeSL"}


@bp.route("/campaign", methods=["GET", "POST"])
@require(MARKETING)
def campaign():
    """Marketing > Campaign: the campaign set-up (3 levels of gifts per order type + a special gift), the customers'
    points, gifts handed over and the lucky draw."""
    from ..models import campaign as cp
    branch = _branch_or_403()
    camp_id = request.values.get("id") or None
    if request.method == "POST":
        if not can(MARKETING, "edit"):
            abort(403)
        action = request.form.get("action")
        if action == "give":
            try:
                cp.give(camp_id, branch, request.form.get("kind"), request.form.get("cust"), int(request.form.get("level") or 0), _user_id())
            except (ValidationError, ValueError) as error:
                flash_error(error if isinstance(error, ValidationError) else ValidationError("errNumber"))
            else:
                flash_ok("cpMsgGiven")
            return redirect(url_for("finance.campaign", branch=branch, id=camp_id, kind=request.form.get("kind"), _anchor="cp-standings"))
        try:
            saved = cp.save(branch, camp_id, request.form, _user_id())
        except ValidationError as error:
            flash_error(error)
            return redirect(url_for("finance.campaign", branch=branch, id=camp_id))
        flash_ok("cpMsgSaved")
        return redirect(url_for("finance.campaign", branch=branch, id=saved["CAMP_ID"]))
    camps = cp.for_branch(branch)
    camp = (cp.get(camp_id) if camp_id else None) or (camps[0] if camps and request.args.get("new") != "1" else None)
    if camp and camp["BRANCH_ID"] != branch:
        abort(404)
    kind = request.args.get("kind") if request.args.get("kind") in cp.TYPES else "ML"
    data = None
    if camp:
        data = {t: {"rows": cp.standings(camp, t), "draw": cp.draw_of(camp["CAMP_ID"], t)} for t in cp.TYPES}
    form = camp or cp.blank(branch)

    def day(value):
        return to_local(value).date().isoformat() if value else ""
    return render_template("finance/campaign.html", branch=branch, branches=maintainable_branches(), camps=camps, camp=camp,
                           form=form, data=data, kind=kind, types=cp.TYPES, type_keys=CP_TYPE_KEYS, day=day,
                           state_of=cp.state, can_edit=can(MARKETING, "edit"))


@bp.route("/campaign/<camp_id>/draw/<kind>", methods=["GET", "POST"])
@require(MARKETING)
def campaign_draw(camp_id, kind):
    """The lucky wheel for the special gift of one order type. POST picks the winner (once) and returns it."""
    from flask import jsonify
    from ..models import campaign as cp
    from ..i18n import texts
    camp = cp.get(camp_id)
    if not camp or kind not in cp.TYPES or camp["BRANCH_ID"] not in [b for b in maintainable_branches()]:
        abort(404)
    if request.method == "POST":
        if not can(MARKETING, "edit"):
            abort(403)
        try:
            d = cp.draw(camp_id, camp["BRANCH_ID"], kind, _user_id())
        except ValidationError as error:
            return jsonify({"ok": False, "error": t(error.key, **error.params)}), 422
        return jsonify({"ok": True, "winner": d["WINNER_CUST"], "name": d["WINNER_NAME"], "gift": d["GIFT"],
                        "tickets": d["WINNER_TICKETS"], "total": d["TICKETS"]})
    done = cp.draw_of(camp_id, kind)
    return render_template("finance/campaign_draw.html", camp=camp, kind=kind, type_keys=CP_TYPE_KEYS,
                           entrants=cp.entrants(camp, kind), done=done, can_edit=can(MARKETING, "edit"),
                           back=url_for("finance.campaign", branch=camp["BRANCH_ID"], id=camp_id, kind=kind),
                           js_text=texts("cpSpin", "cpSpinning", "cpWinner", "cpReplay", "errServer", "cpTickets"))
