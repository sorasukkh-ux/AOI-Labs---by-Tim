"""AOIMS_ROLE and AOIMS_ROLEAUTH: roles and what each role may do on each screen."""
import re

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, DuplicateKey
from .menu import Menu

PERMS = ("view", "create", "edit", "delete", "approve")
PERM_FIELD = {"view": "CAN_VIEW", "create": "CAN_CREATE", "edit": "CAN_EDIT", "delete": "CAN_DELETE", "approve": "CAN_APPROVE"}
ADMIN = "ADMIN"                       # this role always has full access and cannot be changed
CODE_RE = re.compile(r"^[A-Z0-9_]{2,20}$")


def normalise(perm):
    """A screen that cannot be opened cannot be worked in: any other right needs View; View off clears the rest."""
    perm = {k: bool(perm.get(k)) for k in PERMS}
    if not perm["view"]:
        return {k: False for k in PERMS}
    return perm


class Role:
    COL = "AOIMS_ROLE"

    @classmethod
    def all(cls):
        return list(col(cls.COL).find({}, NO_ID).sort([("ROLE_CODE", 1)]))

    @classmethod
    def find(cls, code):
        return col(cls.COL).find_one({"ROLE_CODE": code}, NO_ID)

    @classmethod
    def save(cls, code, form, perms, ope_user, is_new):
        """Creates or updates a role together with its permission table. `perms`: {menu: {perm: bool}}."""
        from .user import User                      # local import: user.py imports this module
        code = (code or "").strip().upper()
        name = clean_text(form.get("ROLE_NAME"))
        status = form.get("ROLE_STATUS") if form.get("ROLE_STATUS") in ("A", "I") else "A"
        if not CODE_RE.match(code):
            raise ValidationError("roleErrCode")
        if not name:
            raise ValidationError("roleErrName")
        existing = cls.find(code)
        if is_new and existing:
            raise ValidationError("roleErrDuplicate", code=code)
        if not is_new and not existing:
            raise ValidationError("roleErrUnknown")
        if code == ADMIN:
            status = "A"                            # the administrator role cannot be switched off
        in_use = User.count_in_role(code)
        if status == "I" and in_use:
            raise ValidationError("roleErrInUse", n=in_use)
        values = {"ROLE_NAME": name, "ROLE_DESC": clean_text(form.get("ROLE_DESC")), "ROLE_STATUS": status}
        if existing:
            values.update(audit_update(ope_user))
            col(cls.COL).update_one({"ROLE_CODE": code}, {"$set": values})
        else:
            values.update({"ROLE_CODE": code}, **audit_new(ope_user))
            try:
                col(cls.COL).insert_one(values)
            except DuplicateKey:
                raise ValidationError("roleErrDuplicate", code=code)
        if code != ADMIN:
            RoleAuth.save(code, perms, ope_user)
        return cls.find(code)


class RoleAuth:
    COL = "AOIMS_ROLEAUTH"

    @classmethod
    def for_role(cls, role_code):
        """{MENU_CODE: {view, create, edit, delete, approve}} for every menu."""
        menus = Menu.codes()
        if role_code == ADMIN:
            return {m: {k: True for k in PERMS} for m in menus}
        saved = {d["MENU_CODE"]: d for d in col(cls.COL).find({"ROLE_CODE": role_code}, NO_ID)}
        return {m: {k: bool(saved.get(m, {}).get(PERM_FIELD[k])) for k in PERMS} for m in menus}

    @classmethod
    def can(cls, role_code, menu_code, perm="view"):
        if role_code == ADMIN:
            return True
        role = Role.find(role_code)
        if not role or role.get("ROLE_STATUS") != "A":
            return False
        doc = col(cls.COL).find_one({"ROLE_CODE": role_code, "MENU_CODE": menu_code}, NO_ID)
        return bool(doc and doc.get(PERM_FIELD[perm]))

    @classmethod
    def save(cls, role_code, perms, ope_user):
        for menu_code in Menu.codes():
            perm = normalise((perms or {}).get(menu_code, {}))
            values = {PERM_FIELD[k]: perm[k] for k in PERMS}
            values.update(audit_update(ope_user))
            col(cls.COL).update_one(
                {"ROLE_CODE": role_code, "MENU_CODE": menu_code},
                {"$set": values, "$setOnInsert": {"REGIST_DATE": values["LAST_UPDATE"]}}, upsert=True)
