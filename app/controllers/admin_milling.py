"""Administration > Milling Production (AOIMS_MILLYIELD) and Milling Maintenance (AOIMS_SYSPARAM).

These are maintenance screens, so the branch can be chosen here (within the user's authorized branches).
"""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..i18n import texts
from ..models.base import ValidationError
from ..models.branch import Branch
from ..models.customer import Customer
from ..models import tv, line_link
from ..models.mill_test import MillTest
from ..models.mill_yield import MillYield
from ..models import variety as var
from ..models.milling_order import MillingOrder
from ..models.rules import receive_time
from ..security import can, current_user, maintainable_branches, pick_maintenance_branch, require
from ..timeutil import now_local, today_local
from .common import flash_error, flash_ok

bp = Blueprint("admin_milling", __name__, url_prefix="/admin/milling")


def _branch_or_403():
    requested = request.values.get("branch")
    branch = pick_maintenance_branch(requested)
    if not branch or (requested and requested != branch):
        abort(403)                       # asked for a branch this user is not authorized for
    return branch


@bp.route("/production", methods=["GET", "POST"])
@require("AD_MILLPROD")
def production():
    branch = _branch_or_403()
    variety = var.clean(request.values.get("v"))
    values = None
    if request.method == "POST":
        if not can("AD_MILLPROD", "edit"):
            abort(403)
        action = request.form.get("action")
        user_id = current_user()["USER_ID"]
        try:
            if action == "test":
                MillTest.add(branch, request.form, user_id)
                flash_ok("mtSaved")
            elif action == "test_delete":
                MillTest.delete(branch, request.form.get("TEST_ID"), user_id)
                flash_ok("mtDeleted")
            else:
                MillYield.save(branch, request.form, user_id, variety=variety)
                flash_ok("confirmMillingTimeSaved")
        except ValidationError as error:
            flash_error(error)
            if action not in ("test", "test_delete"):
                values = {k: request.form.get(k, "") for k in ("rice", "bran", "broken", "husk")}
            else:
                return redirect(url_for("admin_milling.production", branch=branch, v=variety, _anchor="mill-tests"))
        else:
            anchor = "mill-tests" if action in ("test", "test_delete") else None
            return redirect(url_for("admin_milling.production", branch=branch, v=variety, _anchor=anchor))
    ratios = MillYield.ratios(branch, variety)
    tests = MillTest.for_branch(branch)
    avg = MillTest.averages(branch, variety, tests)
    minutes, timed = MillTest.minutes_per_kg(tests)
    return render_template(
        "admin/milling_production.html", branch=branch, branches=maintainable_branches(), variety=variety,
        values=values or {k: "%.4f" % v for k, v in ratios.items()}, editable=can("AD_MILLPROD", "edit"),
        tests=tests, avg=avg, minutes=minutes, timed=timed, today=today_local().isoformat(),
        own=MillYield.has_own(branch, variety),
        js_text=texts("mpTotalOk", "mpTotalOver", "mpErrTotal", "mpErrRange"))


@bp.route("/maintenance", methods=["GET", "POST"])
@require("AD_MILLTIME")
def maintenance():
    branch = _branch_or_403()
    values = None
    if request.method == "POST":
        if not can("AD_MILLTIME", "edit"):
            abort(403)
        if request.form.get("action") == "tvkey":
            try:
                tv.issue_key(branch, current_user()["USER_ID"])
            except ValidationError as error:
                flash_error(error)
            else:
                flash_ok("msgTvKey")
            return redirect(url_for("admin_milling.maintenance", branch=branch, _anchor="tv-display"))
        try:
            Branch.save_milling_settings(branch, request.form, current_user()["USER_ID"])
        except ValidationError as error:
            flash_error(error)
            values = request.form
        else:
            flash_ok("confirmMillingTimeSaved")
            return redirect(url_for("admin_milling.maintenance", branch=branch))
    saved = Branch.get(branch)
    key = tv.key_of(branch)
    tv_path = url_for("tv.board", branch=branch, key=key) if key else None
    tv_link = (request.url_root.rstrip("/") + tv_path) if key else None
    return render_template("admin/milling_maintenance.html", branch=branch, branches=maintainable_branches(), tv_link=tv_link,
                           tv_path=tv_path, line=line_link.status(), line_events=line_link.recent(8),
                           webhook_path=url_for("line_api.webhook"),
                           values=values or saved, editable=can("AD_MILLTIME", "edit"), preview=_queue_preview(saved))


def _queue_preview(branch):
    """Current queue of the branch with the calculated finish time and the period told to the customer."""
    orders = receive_time.queue_sequence(MillingOrder.for_branch(branch["BRANCH_ID"], ("Q", "I")))
    plan = receive_time.schedule(orders, now_local(), branch.get("SYSP_MILLTIMEKG"), branch.get("SYSP_MILLORDGAP"),
                                 branch.get("SYSP_OPETIMESTART"), branch.get("SYSP_OPETIMEEND"), Branch.periods(branch))
    rows = []
    for order in orders:
        slot = plan[order["ORD_ID"]]
        rows.append({"order": order, "customer": (Customer.find(order["CUST_CODE"]) or {}).get("CUST_NAME", order["CUST_CODE"]),
                     "minutes": slot["minutes"], "finish": slot["finish"], "receive": slot["receive"]})
    return rows


@bp.route("/line-poster")
@require("AD_MILLTIME")
def line_poster():
    """A4 poster for the counter: add the mill's LINE account, then send the mobile number."""
    return render_template("admin/line_poster.html", friend_url=line_link.friend_url(), oa=line_link.oa_id())


ANALYSIS_TABS = ("period", "today", "trade")


@bp.route("/analysis")
@require("AD_MILLANALYSIS")
def analysis():
    """Milling Analysis: the period analysis of the status timeline, today's milling orders and today's buying /
    selling (the former Service Dashboard), on one screen with tabs."""
    from flask import Response
    from . import service_info
    tab = request.args.get("tab") if request.args.get("tab") in ANALYSIS_TABS else "period"
    if tab == "period":
        context = service_info.analysis_view()
        if isinstance(context, Response):
            return context
    else:
        context = service_info.dashboard_context("trade" if tab == "trade" else "milling")
    return render_template("admin/milling_analysis.html", tab=tab, tabs=ANALYSIS_TABS, **context)
