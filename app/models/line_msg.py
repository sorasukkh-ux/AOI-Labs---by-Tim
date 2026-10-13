"""AOIMS_LINEMSG: the texts of the LINE messages, kept on Administration > LINE Message Maintenance.

Each message has a fixed ID (used by the system), a name, the text with {parameters} and an active flag.
A message without a saved record uses its default text (the Thai text of the system). Inactive "ML_" messages are
not sent; inactive replies fall back to the default text, because the customer expects an answer.
"""
import re

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text

COL = "AOIMS_LINEMSG"
PARAM = re.compile(r"\{([a-z_]+)\}")
MAX_LEN = 1000
ORDER_PARAMS = ("no", "name", "time", "date", "rice_kg", "pay", "branch")
# MSG_ID: (default name key, default text key (Thai), parameters)
MESSAGES = (
    ("ML_FINISHED", "lmName_ML_FINISHED", "lmDefault_ML_FINISHED", ORDER_PARAMS),
    ("ML_DELIVERY", "lmName_ML_DELIVERY", "lmDefault_ML_DELIVERY", ORDER_PARAMS + ("address",)),
    ("LN_WELCOME", "lmName_LN_WELCOME", "lnrWelcome", ()),
    ("LN_WELCOME_BACK", "lmName_LN_WELCOME_BACK", "lnrWelcomeBack", ("name",)),
    ("LN_LINKED_TICKET", "lmName_LN_LINKED_TICKET", "lnrLinkedTicket", ("name", "no")),
    ("LN_LINKED_PHONE", "lmName_LN_LINKED_PHONE", "lnrLinkedPhone", ("name",)),
    ("LN_RESENT", "lmName_LN_RESENT", "lnrResent", ("n",)),
    ("LN_BAD_TICKET", "lmName_LN_BAD_TICKET", "lnrBadTicket", ()),
    ("LN_PHONE_NOT_FOUND", "lmName_LN_PHONE_NOT_FOUND", "lnrPhoneNotFound", ()),
    ("LN_PHONE_MANY", "lmName_LN_PHONE_MANY", "lnrPhoneMany", ()),
    ("LN_PHONE_TAKEN", "lmName_LN_PHONE_TAKEN", "lnrPhoneTaken", ()),
    ("LN_HELP", "lmName_LN_HELP", "lnrHelp", ()),
    ("LN_HELP_LINKED", "lmName_LN_HELP_LINKED", "lnrHelpLinked", ("name",)),
)
INFO = {m[0]: m for m in MESSAGES}
SAMPLE = {"no": "0031", "name": "สมชาย ก.", "time": "14:30", "date": "13/10/2026", "rice_kg": "51.52", "pay": "120.00",
          "branch": "โรงงาน 2", "address": "45 หมู่ 2 ต.บ้านเป็ด อ.เมือง จ.ขอนแก่น", "n": "1"}


def default_text(msg_id):
    from ..i18n import t_in
    return t_in("th", INFO[msg_id][2])


def default_name(msg_id, lang="th"):
    from ..i18n import t_in
    return t_in(lang, INFO[msg_id][1])


def fill(text, params):
    return PARAM.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


def get(msg_id):
    return col(COL).find_one({"MSG_ID": msg_id}, NO_ID)


def active(msg_id):
    doc = get(msg_id)
    return doc is None or doc.get("ACTIVE_FLG", True)


def text(msg_id, **params):
    """The message to send, parameters filled in."""
    doc = get(msg_id)
    body = doc["MSG_TEXT"] if doc and doc.get("MSG_TEXT") and doc.get("ACTIVE_FLG", True) else default_text(msg_id)
    return fill(body, params)


def all_messages(lang="th"):
    out = []
    for msg_id, _name_key, _text_key, params in MESSAGES:
        doc = get(msg_id) or {}
        body = doc.get("MSG_TEXT") or default_text(msg_id)
        out.append({"MSG_ID": msg_id, "MSG_NAME": doc.get("MSG_NAME") or default_name(msg_id, lang), "MSG_TEXT": body,
                    "ACTIVE_FLG": doc.get("ACTIVE_FLG", True), "params": params, "custom": bool(doc.get("MSG_TEXT")),
                    "preview": fill(body, SAMPLE), "LAST_UPDATE": doc.get("LAST_UPDATE"), "OPE_USER": doc.get("OPE_USER"),
                    "can_disable": msg_id.startswith("ML_")})
    return out


def save(msg_id, form, ope_user):
    if msg_id not in INFO:
        raise ValidationError("lmErrUnknown")
    allowed = INFO[msg_id][3]
    body = (form.get("MSG_TEXT") or "").replace("\r\n", "\n").strip()
    name = clean_text(form.get("MSG_NAME"), 100) or default_name(msg_id)
    if not body:
        raise ValidationError("lmErrEmpty")
    if len(body) > MAX_LEN:
        raise ValidationError("lmErrLong", n=MAX_LEN)
    unknown = sorted({p for p in PARAM.findall(body) if p not in allowed})
    if unknown:
        raise ValidationError("lmErrParam", bad=", ".join("{%s}" % p for p in unknown),
                              ok=", ".join("{%s}" % p for p in allowed) or "-")
    values = {"MSG_NAME": name, "MSG_TEXT": body, "MSG_PARAMS": ",".join(allowed),
              "ACTIVE_FLG": True if not msg_id.startswith("ML_") else form.get("ACTIVE_FLG") in ("1", "on", "true")}
    if get(msg_id) is None:
        col(COL).insert_one(dict(values, MSG_ID=msg_id, **audit_new(ope_user)))
    else:
        values.update(audit_update(ope_user))
        col(COL).update_one({"MSG_ID": msg_id}, {"$set": values})


def reset(msg_id):
    if msg_id not in INFO:
        raise ValidationError("lmErrUnknown")
    col(COL).delete_one({"MSG_ID": msg_id})
