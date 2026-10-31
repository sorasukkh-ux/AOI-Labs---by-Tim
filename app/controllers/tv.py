"""TV display per factory: /tv/<branch>?key=<display key>. No sign-in; the key is in the link (see models/tv.py)."""
import io

from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for

from ..i18n import t_in
from ..models import tv
from ..models import bb_music
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


# ---------- update 83: price billboard ----------
BB_LABELS = ("tvbBuy", "tvbSell", "tvbUpdated", "tvbOffline", "tvbFull", "tvbFullExit", "bbTitle", "bbSub", "bbBuy", "bbSell",
             "bbUnit", "bbUp", "bbDown", "bbSame", "bbSince", "bbNew", "bbDays", "bbDiscount", "bbAll", "bbNote", "bbXmas", "bbXmasEn", "bbMusicTap", "bbTheme_cards", "rkTitle", "rkSub", "rkOther", "bbSongkran")
SHOW = ("both", "buy", "sell")


@bp.route("/<branch>/prices")
def prices(branch):
    """The price billboard: the same display key as the TV; ?show=buy|sell|both (default both)."""
    if not tv.allowed(branch, request.args.get("key")):
        return render_template("tv/denied.html"), 403
    show = request.args.get("show") if request.args.get("show") in SHOW else "both"
    theme = request.args.get("theme") if request.args.get("theme") in tv.BB_THEMES else None     # update 85: ?theme= tries a theme
    text = {k: {"th": t_in("th", k), "en": t_in("en", k)} for k in BB_LABELS}
    text["days"], text["months"] = DAYS_TH, MONTHS_TH
    data = _with_music(tv.billboard(branch), branch)
    return render_template("tv/prices.html", data=data, text=text, show=show, theme=theme or data["theme"], fixed_theme=theme,
                           data_url=url_for("tv.prices_data", branch=branch, key=request.args.get("key")))


@bp.route("/<branch>/prices/data")
def prices_data(branch):
    _check(branch)
    return jsonify(_with_music(tv.billboard(branch), branch))


def _with_music(data, branch):
    """Update 106: the links of the shop's own music files (the key is needed, so they are made here)."""
    key = request.args.get("key")
    data["music_urls"] = {t: url_for("tv.music", branch=branch, theme=t, ext=f["ext"], key=key, v=f["ver"])
                          for t, f in (data.get("music_files") or {}).items()}
    return data


@bp.route("/<branch>/music/<theme>.<ext>")
def music(branch, theme, ext):
    """Update 106: the uploaded music file of a theme (byte ranges work, so the TV browser can stream and loop it)."""
    _check(branch)
    doc = bb_music.get(branch, theme)
    if not doc or doc.get("FILE_EXT") != ext:
        abort(404)
    resp = send_file(io.BytesIO(bytes(doc["DATA"])), mimetype=doc["MIME"], conditional=True, etag=doc["FILE_VER"],
                     max_age=86400, download_name="%s.%s" % (theme, ext))
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp
