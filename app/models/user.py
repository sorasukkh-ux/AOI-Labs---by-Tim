"""AOIMS_USER: the people who sign in. One role per user; the role decides what the user can do."""
import re

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash

from .base import col, NO_ID, ValidationError, audit_new, audit_update, clean_text, DuplicateKey
from .branch import Branch
from .role import Role, ADMIN
from ..timeutil import now_utc

ID_RE = re.compile(r"^[a-z0-9._-]{3,50}$")
MIN_PASSWORD = 8
ACTIVE, INACTIVE, LOCKED = "A", "I", "L"
# Checked when the user ID is unknown, so a wrong ID takes as long to answer as a wrong password.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


class User:
    COL = "AOIMS_USER"

    # ---- reads ----
    @classmethod
    def find(cls, user_id):
        return col(cls.COL).find_one({"USER_ID": (user_id or "").strip().lower()}, NO_ID)

    @classmethod
    def search(cls, query=""):
        query = (query or "").strip().lower()
        users = list(col(cls.COL).find({}, NO_ID).sort([("USER_ID", 1)]))
        if query:
            users = [u for u in users if query in u["USER_ID"].lower() or query in (u.get("USER_NAME") or "").lower()]
        return users

    @classmethod
    def count(cls):
        return col(cls.COL).count_documents({})

    @classmethod
    def count_in_role(cls, role_code):
        return col(cls.COL).count_documents({"ROLE_CODE": role_code})

    @staticmethod
    def allowed_branches(user):
        """Branches this user may operate or maintain."""
        ids = Branch.ids()
        return ids if user.get("BRANCH_ID") == Branch.ALL else [b for b in ids if b == user.get("BRANCH_ID")]

    # ---- validation shared by create and update ----
    @classmethod
    def _common(cls, form, actor_role):
        name = clean_text(form.get("USER_NAME"))
        if not name:
            raise ValidationError("usrErrName")
        role = Role.find(form.get("ROLE_CODE"))
        if not role or role.get("ROLE_STATUS") != "A":
            raise ValidationError("usrErrRole")
        if role["ROLE_CODE"] == ADMIN and actor_role != ADMIN:
            raise ValidationError("usrErrAdminOnly")       # only an administrator can hand out the ADMIN role
        branch = form.get("BRANCH_ID")
        if branch != Branch.ALL and branch not in Branch.ids():
            raise ValidationError("usrErrBranch")
        status = form.get("USER_STATUS") if form.get("USER_STATUS") in (ACTIVE, INACTIVE, LOCKED) else ACTIVE
        return {"USER_NAME": name, "ROLE_CODE": role["ROLE_CODE"], "BRANCH_ID": branch,
                "MOBILE_NO": clean_text(form.get("MOBILE_NO"), 20), "USER_STATUS": status}

    @classmethod
    def _active_admins(cls, excluding=None):
        return [u for u in col(cls.COL).find({"ROLE_CODE": ADMIN, "USER_STATUS": ACTIVE}, NO_ID)
                if u["USER_ID"] != excluding]

    # ---- writes ----
    @classmethod
    def create(cls, form, ope_user, actor_role):
        user_id = (form.get("USER_ID") or "").strip().lower()
        if not ID_RE.match(user_id):
            raise ValidationError("usrErrId")
        password = form.get("PASSWORD") or ""
        if len(password) < MIN_PASSWORD:
            raise ValidationError("usrErrPassword")
        values = cls._common(form, actor_role)
        values.update({"USER_ID": user_id, "PASSWORD_HASH": generate_password_hash(password),
                       "LOGIN_FAIL_COUNT": 0, "LAST_LOGIN": None}, **audit_new(ope_user))
        try:
            col(cls.COL).insert_one(values)
        except DuplicateKey:
            raise ValidationError("usrErrDuplicate", id=user_id)
        return cls.find(user_id)

    @classmethod
    def update(cls, user_id, form, ope_user, actor_role):
        current = cls.find(user_id)
        if not current:
            raise ValidationError("usrErrUnknown")
        if current["ROLE_CODE"] == ADMIN and actor_role != ADMIN:
            raise ValidationError("usrErrAdminOnly")       # and only an administrator can change one
        values = cls._common(form, actor_role)
        password = form.get("PASSWORD") or ""
        if password:
            if len(password) < MIN_PASSWORD:
                raise ValidationError("usrErrPassword")
            values["PASSWORD_HASH"] = generate_password_hash(password)
        was_admin = current["ROLE_CODE"] == ADMIN and current["USER_STATUS"] == ACTIVE
        stays_admin = values["ROLE_CODE"] == ADMIN and values["USER_STATUS"] == ACTIVE
        if was_admin and not stays_admin and not cls._active_admins(excluding=current["USER_ID"]):
            raise ValidationError("usrErrLastAdmin")
        if values["USER_STATUS"] == ACTIVE:
            values["LOGIN_FAIL_COUNT"] = 0            # unlocking also clears the failed-login counter
        values.update(audit_update(ope_user))
        col(cls.COL).update_one({"USER_ID": current["USER_ID"]}, {"$set": values})
        return cls.find(user_id)

    # ---- sign-in ----
    @classmethod
    def authenticate(cls, user_id, password):
        """Returns (user, None) on success or (None, error_key, params)."""
        user = cls.find(user_id)
        if not user:
            check_password_hash(_DUMMY_HASH, password or "")
            return None, "loginErrInvalid", {}
        if user["USER_STATUS"] == INACTIVE:
            return None, "loginErrInactive", {}
        if user["USER_STATUS"] == LOCKED:
            return None, "loginErrLocked", {}
        if not check_password_hash(user["PASSWORD_HASH"], password or ""):
            limit = current_app.config["MAX_LOGIN_FAILS"]
            fails = int(user.get("LOGIN_FAIL_COUNT") or 0) + 1
            values = {"LOGIN_FAIL_COUNT": fails, "LAST_UPDATE": now_utc()}
            if fails >= limit:
                values["USER_STATUS"] = LOCKED
            col(cls.COL).update_one({"USER_ID": user["USER_ID"]}, {"$set": values})
            if fails >= limit:
                return None, "loginErrLockedNow", {"n": limit}
            return None, "loginErrInvalid", {}
        col(cls.COL).update_one({"USER_ID": user["USER_ID"]},
                                {"$set": {"LOGIN_FAIL_COUNT": 0, "LAST_LOGIN": now_utc()}})
        return cls.find(user["USER_ID"]), None, {}
