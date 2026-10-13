"""Administration > LINE Message Maintenance: the texts of the LINE messages (AOIMS_LINEMSG)."""
from flask import Blueprint, abort, redirect, render_template, request, url_for

from ..models import line_msg
from ..models.base import ValidationError
from ..security import can, current_user, require
from .common import flash_error, flash_ok

MENU = "AD_LINEMSG"
bp = Blueprint("admin_line", __name__, url_prefix="/admin/line-messages")


@bp.route("/", methods=["GET", "POST"])
@require(MENU)
def messages():
    from ..i18n import get_lang
    if request.method == "POST":
        if not can(MENU, "edit"):
            abort(403)
        msg_id = request.form.get("MSG_ID") or ""
        try:
            if request.form.get("action") == "reset":
                line_msg.reset(msg_id)
                flash_ok("lmResetDone", id=msg_id)
            else:
                line_msg.save(msg_id, request.form, current_user()["USER_ID"])
                flash_ok("lmSaved", id=msg_id)
        except ValidationError as error:
            flash_error(error)
        return redirect(url_for("admin_line.messages", _anchor="msg-" + msg_id))
    return render_template("admin/line_messages.html", messages=line_msg.all_messages(get_lang()), editable=can(MENU, "edit"))
