"""TV display per factory: /tv/<branch>?key=<display key>. No sign-in; the key is in the link (see models/tv.py)."""
from flask import Blueprint, abort, jsonify, render_template, request, url_for

from ..i18n import t_in
from ..models import tv
from ..models import variety as var

bp = Blueprint("tv", __name__, url_prefix="/tv")
LABELS = ("tvbReady", "tvbMilling", "tvbQueue", "tvbDoneAt", "tvbFinishAt", "tvbReceive", "tvbDeliver", "tvbArrive",
          "tvbTomorrow", "tvbMore", "tvbPage", "tvbBuy", "tvbSell", "tvbUnit", "tvbUpdated", "tvbEvery", "tvbNone",
          "tvbStatus", "tvbScan", "tvbOffline", "tvbKg", "tvbMotor", "tvbMotorOn", "tvbMotorOff", "tvbFull", "tvbFullExit")
DAYS_TH = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
MONTHS_TH = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")


def _check(branch):
    if not tv.allowed(branch, request.args.get("key")):
        abort(403)


@bp.route("/<branch>")
def board(branch):
    if not tv.allowed(branch, request.args.get("key")):
        return render_template("tv/denied.html"), 403
    text = {k: {"th": t_in("th", k), "en": t_in("en", k)} for k in LABELS}
    text["var"] = {v: t_in("th", var.VAR_KEYS[v]) for v in var.VARIETIES}
    text["days"], text["months"] = DAYS_TH, MONTHS_TH
    return render_template("tv/board.html", data=tv.board(branch), text=text,
                           data_url=url_for("tv.data", branch=branch, key=request.args.get("key")))


@bp.route("/<branch>/data")
def data(branch):
    _check(branch)
    return jsonify(tv.board(branch))
