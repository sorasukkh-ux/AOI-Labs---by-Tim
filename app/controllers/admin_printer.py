"""Update 141: Administration > Printer Maintenance (AOIMS_PRINTER, AOITS_PRINTJOB) and POST /print (server printing).

Every Print button on a staff screen calls POST /print {doc, ref, copies}; the server prints on the branch's
default printer. Without a printer (Codespaces) the answer says fallback=true and the screen uses the browser print.
"""
from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from ..i18n import t
from ..models import printing
from ..models.base import ValidationError
from ..security import can, current_user, operating_branch, require
from ..timeutil import fmt_datetime
from .common import flash_error, flash_ok

MENU = "AD_PRINTER"
bp = Blueprint("admin_printer", __name__, url_prefix="/admin/printers")
print_bp = Blueprint("printing", __name__, url_prefix="/print")


@bp.route("/", methods=["GET", "POST"])
@require(MENU)
def maintenance():
    branch = operating_branch()
    if request.method == "POST":
        if not can(MENU, "edit"):
            abort(403)
        action = request.form.get("action")
        try:
            if action == "save":
                p = printing.save_printer(branch, request.form, current_user()["USER_ID"])
                flash_ok("prSaved", id=p["PRINTER_ID"])
            elif action == "test":
                job = printing.submit(branch, "test", "", current_user()["USER_ID"], request.form.get("PRINTER_ID"))
                (flash_ok if job["STATUS"] == printing.PRINTED else flash_error)(
                    *(("prTestDone", ) if job["STATUS"] == printing.PRINTED else (ValidationError("prJobFailed", error=job.get("ERROR") or "-"),)))
            elif action == "reprint":
                job = printing.reprint(branch, request.form.get("JOB_ID"), current_user()["USER_ID"], request.form.get("REASON"))
                flash_ok("prQueued", id=job["JOB_ID"])
            else:
                abort(400)
        except ValidationError as error:
            flash_error(error)
        return redirect(url_for("admin_printer.maintenance"))
    names = {p["PRINTER_ID"]: p["NAME"] for p in printing.printers(branch)}
    return render_template("admin/printers.html", printers=printing.printers(branch), jobs=printing.recent(branch),
                           names=names, usb=printing.usb_devices(), editable=can(MENU, "edit"), fmt_datetime=fmt_datetime)


@print_bp.route("", methods=["POST"])
def print_doc():
    """{doc, ref, copies, printer?} -> {ok, job, status, message} (or fallback=true: no printer, print in the browser)."""
    if not current_user():
        abort(401)
    doc = request.form.get("doc") or ""
    if not printing.can_print(doc):
        abort(403)
    try:
        job = printing.submit(operating_branch(), doc, request.form.get("ref") or "", current_user()["USER_ID"],
                              request.form.get("printer") or None, request.form.get("copies") or 1)
    except ValidationError as error:
        return jsonify({"ok": False, "fallback": error.key == "prNoPrinter", "message": t(error.key, **error.params)}), 422
    return jsonify(_job_json(job))


@print_bp.route("/jobs/<job_id>")
def job_state(job_id):
    if not current_user():
        abort(401)
    job = printing.get(job_id)
    if not job or job["BRANCH_ID"] != operating_branch():
        abort(404)
    return jsonify(_job_json(job))


def _job_json(job):
    key = {printing.QUEUED: "prJobQueued", printing.PRINTED: "prJobPrinted", printing.FAILED: "prJobFailed"}[job["STATUS"]]
    return {"ok": job["STATUS"] != printing.FAILED, "job": job["JOB_ID"], "status": job["STATUS"],
            "message": t(key, error=job.get("ERROR") or "-"), "url": url_for("printing.job_state", job_id=job["JOB_ID"])}
