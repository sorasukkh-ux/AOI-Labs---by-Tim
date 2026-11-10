"""Update 117: long AI calls run in the background (AOITS_AIJOB).

Codespaces and other forwarders stop waiting after about a minute (HTTP 504) while an AI answer with a web search can take
longer. The page starts a job, shows "working" and asks /automation/ai/job/<id> every 4 s; when the job is done the
message (and any answer) is shown after a reload. Kinds: aiweb (today's web prices on the
price analysis page), aihist (the 3-year monthly history)."""
import threading
from datetime import timedelta

from flask import current_app

from .base import NO_ID, ValidationError, col
from .counter import Counter
from ..timeutil import now_utc

COL = "AOITS_AIJOB"
RUN_MINUTES = 12                  # a job still "running" after this is treated as failed (server restarted)
KINDS = ("aiweb", "aihist", "formula")       # update 122: formula = product transformation analysis
_THREADS = []                     # tests wait for these


def running(kind, user_id=None):
    """The running job of this kind (of this user when given), or None."""
    q = {"KIND": kind, "STATUS": "R", "REGIST_DATE": {"$gte": now_utc() - timedelta(minutes=RUN_MINUTES)}}
    if user_id:
        q["REGIST_USER"] = user_id
    return col(COL).find_one(q, NO_ID)


def get(job_id):
    doc = col(COL).find_one({"JOB_ID": str(job_id or "")}, NO_ID)
    if doc and doc["STATUS"] == "R" and doc["REGIST_DATE"] < now_utc() - timedelta(minutes=RUN_MINUTES):
        doc["STATUS"], doc["ERROR"] = "E", "timeout"
    return doc


def start(kind, branch_id, user_id, work, params=None):
    """Runs work() (a function of the app, called inside an app context) in a thread. Returns the job record."""
    if kind not in KINDS:
        raise ValueError(kind)
    if running(kind):
        raise ValidationError("ajErrRunning")
    doc = {"JOB_ID": "AJ%07d" % Counter.next("AIJOB"), "KIND": kind, "BRANCH_ID": branch_id, "PARAMS": params or {},
           "STATUS": "R", "RESULT": None, "ERROR": None, "ERROR_KEY": None, "ERROR_PARAMS": None,
           "REGIST_DATE": now_utc(), "REGIST_USER": user_id, "DONE_DATE": None, "SEEN_FLG": False}
    col(COL).insert_one(dict(doc))
    app = current_app._get_current_object()
    th = threading.Thread(target=_run, args=(app, doc["JOB_ID"], work), daemon=True)
    _THREADS.append(th)
    th.start()
    return doc


def _run(app, job_id, work):
    with app.app_context():
        values = {"DONE_DATE": now_utc()}
        try:
            values.update(STATUS="D", RESULT=work())
        except ValidationError as error:
            values.update(STATUS="E", ERROR_KEY=error.key, ERROR_PARAMS={k: str(v) for k, v in error.params.items()})
        except Exception as error:                      # noqa: BLE001 - always end the job
            app.logger.exception("AI job %s failed", job_id)
            values.update(STATUS="E", ERROR=str(error)[:300])
        col(COL).update_one({"JOB_ID": job_id}, {"$set": values})


def mark_seen(job_id):
    """True the first time a finished job is reported (its message is shown once)."""
    r = col(COL).update_one({"JOB_ID": job_id, "STATUS": {"$in": ["D", "E"]}, "SEEN_FLG": False}, {"$set": {"SEEN_FLG": True}})
    return bool(getattr(r, "modified_count", 0))


def wait_all(timeout=10):
    """Tests: wait for the background threads."""
    for th in list(_THREADS):
        th.join(timeout)
