"""End-to-end tests through the controllers.

    python -m unittest discover -s tests -v                      in-memory database, no MongoDB needed
    AOI_TEST_MONGO=1 python -m unittest discover -s tests -v     the same tests on the MongoDB in AOI_MONGO_URI

On MongoDB the whole run shares ONE throw-away database (aoi_test_...): it is emptied before each test
and dropped at the end. Creating and dropping a database per test opened so many data files that the
MongoDB server ran out of file handles and aborted.
"""
import os
import sys
import re
import unittest
import uuid
import io
import json
import urllib.error
from datetime import timedelta, timezone
from unittest import mock

USE_MONGO = os.environ.get("AOI_TEST_MONGO") == "1"
MONGO_TEST_DB = "aoi_test_" + uuid.uuid4().hex[:12]
DB_DOWN = []                                                  # filled once MongoDB stops answering

from app import create_app, DatabaseUnavailable             # noqa: E402
from app.db import col, get_db                               # noqa: E402
from app.models.schema import COLLECTIONS                    # noqa: E402
from app.models.base import ValidationError                  # noqa: E402
from app.models.user import User                             # noqa: E402

PASSWORD = "aoi12345"


class Base(unittest.TestCase):
    def setUp(self):
        settings = {"TESTING": True, "SECRET_KEY": "test-key", "DB_BACKEND": "memory"}
        if not USE_MONGO:
            self.app = create_app(settings)                  # the memory backend seeds itself
        else:
            if DB_DOWN:
                self.skipTest("MongoDB stopped answering earlier in this run")
            settings.update(DB_BACKEND="mongodb", MONGO_DB=MONGO_TEST_DB)
            try:
                self.app = create_app(settings)
            except DatabaseUnavailable as error:
                DB_DOWN.append(True)                         # report it once, skip the rest
                self.fail("MongoDB is not reachable: %s" % str(error).split(" Details:")[0])
            from scripts.seed import seed
            with self.app.app_context():
                database = get_db()
                self.addCleanup(database.client.close)       # one connection pool per test, closed again
                for name in COLLECTIONS:                     # same database every test: empty it, keep the indexes
                    database[name].delete_many({})
                seed(log=lambda _message: None)
        self.client = self.app.test_client()
        # no test reaches the internet: the NABC APIs answer "not reachable" unless a test fakes them
        for target in ("app.models.nabc_price._get", "app.models.supply._get_json"):
            patcher = mock.patch(target, side_effect=ValidationError("npErrDownload"))
            patcher.start()
            self.addCleanup(patcher.stop)
        # ... and no LINE message leaves: not connected unless a test sets the token and fakes the push
        for patcher in (mock.patch.dict(os.environ, {"AOI_LINE_CHANNEL_TOKEN": "", "AOI_LINE_CHANNEL_SECRET": "", "AOI_LINE_OA_ID": ""}),
                        mock.patch("app.models.notify._post", return_value="offline in tests"),
                        mock.patch("app.models.notify._push", return_value="offline in tests")):
            patcher.start()
            self.addCleanup(patcher.stop)
        # The older tests were written for the first mill test (33 kg paddy -> 17 kg rice, 2 kg broken rice, 9 minutes).
        # Since update 10 the seeded yields are the average of the mill's three tests; these tests keep the old numbers.
        with self.app.app_context():
            col("AOIMS_MILLYIELD").update_many({}, {"$set": {"YIELD_RICE": 0.5152, "YIELD_BRAN": 0.1, "YIELD_BROKE": 0.0606, "YIELD_HUSK": 0.22}})
            col("AOIMS_SYSPARAM").update_many({}, {"$set": {"SYSP_MILLTIMEKG": 0.273}})

    # ---- helpers ----
    def token(self, path="/login"):
        page = self.client.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def login(self, user_id, branch="factory2", password=PASSWORD):
        return self.client.post("/login", data={"csrf_token": self.token(), "user_id": user_id,
                                                "password": password, "branch": branch})

    def logout(self):
        self.client.post("/logout", data={"csrf_token": self.token("/")})

    def post(self, path, data=None, token_from="/", headers=None):
        data = dict(data or {})
        data["csrf_token"] = self.token(token_from)
        return self.client.post(path, data=data, headers=headers or {})

    def text(self, path):
        return self.client.get(path).get_data(as_text=True)

    def db(self, name):
        with self.app.app_context():
            return list(col(name).find({}, {"_id": 0}))

    def new_order(self, **fields):
        data = {"CUST_CODE": "C261001001", "REGIST_PADDY": "33", "BRAN_ORDER": "0", "BROKE_ORDER": "0",
                "ORDER_TYPE": "T", "MILL_PRIORITY": "M"}
        data.update(fields)
        response = self.post("/service/milling/new", data, "/service/milling/new")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[-600:])
        return sorted(self.db("AOITS_ORDMILLING"), key=lambda o: o["ORD_ID"])[-1]

    def order(self, ord_id):
        return next(o for o in self.db("AOITS_ORDMILLING") if o["ORD_ID"] == ord_id)

    def set_status(self, ord_id, status, **fields):
        """Finished needs the inbound pop-up's location, Hold needs a reason: sample values unless given."""
        data = {"new_status": status}
        if status == "F":
            data["LOC_ID"] = "01-001-001"
        if status == "H":
            data["HOLD_REASON"] = "Machine check"
        if status == "I":
            data["SCAN_QR"] = "AOI-" + ord_id                # the QR label on the paddy (update 22)
        data.update(fields)
        return self.post("/service/milling/%s/status" % ord_id, data)


def tearDownModule():
    """Removes the shared test database after a MongoDB run."""
    if USE_MONGO and not DB_DOWN:
        try:
            from pymongo import MongoClient
            from app.config import Config
            client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=3000)
            client.drop_database(MONGO_TEST_DB)
            client.close()
        except Exception as error:                           # pragma: no cover
            print("Could not drop %s: %s" % (MONGO_TEST_DB, error))


class SignIn(Base):
    def test_pages_need_a_sign_in(self):
        response = self.client.get("/admin/users/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_admin_signs_in_at_the_chosen_branch(self):
        self.assertEqual(self.login("admin", "factory1").status_code, 302)
        self.client.get("/lang/en")
        self.assertIn('id="session-branch">Factory 1<', self.text("/"))

    def test_fixed_branch_user_always_works_at_own_branch(self):
        self.login("somchai.k", "factory2")                  # authorized for Factory 1 only
        self.client.get("/lang/en")
        self.assertIn('id="session-branch">Factory 1<', self.text("/"))

    def test_wrong_password_locks_after_five_attempts(self):
        for _ in range(4):
            self.assertEqual(self.login("wanida.s", password="wrong").status_code, 200)
        self.client.get("/lang/en")
        page = self.login("wanida.s", password="wrong").get_data(as_text=True)
        self.assertIn("The account is now locked", page)
        self.assertIn("This account is locked", self.login("wanida.s").get_data(as_text=True))

    def test_unknown_user_and_wrong_password_look_the_same(self):
        self.client.get("/lang/en")
        first = self.login("nobody", password="wrong").get_data(as_text=True)
        second = self.login("admin", password="wrong").get_data(as_text=True)
        self.assertIn("User ID or password is incorrect", first)
        self.assertIn("User ID or password is incorrect", second)

    def test_inactive_and_locked_accounts_cannot_sign_in(self):
        self.client.get("/lang/en")
        self.assertIn("inactive", self.login("anan.t").get_data(as_text=True))
        self.assertIn("locked", self.login("nittaya.p").get_data(as_text=True))

    def test_form_without_token_is_refused(self):
        response = self.client.post("/login", data={"user_id": "admin", "password": PASSWORD, "branch": "factory2"})
        self.assertEqual(response.status_code, 400)

    def test_next_address_must_stay_on_this_site(self):
        response = self.client.post("/login?next=https://example.com/", data={
            "csrf_token": self.token(), "user_id": "admin", "password": PASSWORD, "branch": "factory2"})
        self.assertEqual(response.headers["Location"], "/")

    def test_link_targets_from_the_address_bar_are_checked(self):
        self.login("admin")
        page = self.text("/admin/customers/new?next=javascript:alert(1)")
        self.assertNotIn('href="javascript:', page)
        self.assertIn('id="cust-cancel-btn" href="/admin/customers/"', page)      # Cancel falls back to the list
        self.assertIn('id="cust-cancel-btn" href="/service/milling/new"', self.text("/admin/customers/new?next=/service/milling/new"))

    def test_account_changes_apply_to_a_running_session(self):
        self.login("somchai.k")
        with self.app.app_context():
            col("AOIMS_USER").update_one({"USER_ID": "somchai.k"}, {"$set": {"USER_STATUS": "I"}})
        self.assertEqual(self.client.get("/").status_code, 302)          # signed out on the next request


class Permissions(Base):
    def test_operator_cannot_open_or_change_admin_screens(self):
        self.login("somchai.k")
        for path in ("/admin/users/", "/admin/roles/", "/admin/milling/production", "/admin/milling/maintenance"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.post("/admin/milling/production", {"rice": "0.1", "bran": "0", "broken": "0", "husk": "0"}).status_code, 403)

    def test_home_shows_only_screens_the_role_can_view(self):
        self.login("somchai.k")
        page = self.text("/?g=AD")
        self.assertIn('data-menu="AD_CUSTOMER"', page)
        self.assertNotIn('data-menu="AD_USER"', page)
        self.assertNotIn('data-menu="AD_MILLPROD"', page)
        self.logout()
        self.login("wanida.s")                                      # accountant: only Milling Analysis in Administration
        page = self.text("/?g=AD")
        self.assertIn('data-menu="AD_MILLANALYSIS"', page)
        self.assertNotIn('data-menu="AD_MILLTIME"', page)

    def test_manager_views_users_but_cannot_save(self):
        self.login("prasert.m")
        self.assertEqual(self.client.get("/admin/users/").status_code, 200)
        self.assertEqual(self.client.get("/admin/users/new").status_code, 403)
        self.assertEqual(self.post("/admin/users/somchai.k", {"USER_NAME": "X", "ROLE_CODE": "ADMIN", "BRANCH_ID": "ALL"}).status_code, 403)

    def test_fixed_branch_user_cannot_maintain_another_branch(self):
        with self.app.app_context():                         # give the operator the screen, at Factory 1 only
            col("AOIMS_ROLEAUTH").update_one({"ROLE_CODE": "OPERATOR", "MENU_CODE": "AD_MILLPROD"},
                                             {"$set": {"CAN_VIEW": True, "CAN_EDIT": True}})
        self.login("somchai.k")
        self.assertEqual(self.client.get("/admin/milling/production?branch=factory1").status_code, 200)
        self.assertEqual(self.client.get("/admin/milling/production?branch=factory2").status_code, 403)

    def test_only_an_administrator_hands_out_the_admin_role(self):
        with self.app.app_context():
            with self.assertRaises(ValidationError) as caught:
                User.update("somchai.k", {"USER_NAME": "S", "ROLE_CODE": "ADMIN", "BRANCH_ID": "ALL"}, "prasert.m", "MANAGER")
            self.assertEqual(caught.exception.key, "usrErrAdminOnly")
            with self.assertRaises(ValidationError):
                User.update("admin", {"USER_NAME": "A", "ROLE_CODE": "OPERATOR", "BRANCH_ID": "ALL"}, "prasert.m", "MANAGER")


class Administration(Base):
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def yields(self, branch):
        return next(y for y in self.db("AOIMS_MILLYIELD") if y["BRANCH_ID"] == branch and y["RICE_VARIETY"] == "WH")

    def test_output_ratios_over_100_percent_are_rejected(self):
        page = self.post("/admin/milling/production", {"branch": "factory2", "rice": "0.65", "bran": "0.08", "broken": "0.05", "husk": "0.2201"})
        self.assertIn("must not total more than 100%", page.get_data(as_text=True))
        self.assertEqual(self.yields("factory2")["YIELD_RICE"], 0.5152)
        for bad in ("1.2", "-0.1", "", "abc"):
            page = self.post("/admin/milling/production", {"branch": "factory2", "rice": bad, "bran": "0", "broken": "0", "husk": "0"})
            self.assertIn("between 0 and 1", page.get_data(as_text=True), bad)

    def test_output_ratios_are_saved_per_branch(self):
        response = self.post("/admin/milling/production", {"branch": "factory2", "rice": "0.65", "bran": "0.08", "broken": "0.05", "husk": "0.22"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.yields("factory2")["YIELD_RICE"], 0.65)
        self.assertEqual(self.yields("factory1")["YIELD_RICE"], 0.5152)

    def maintenance(self, **changes):
        data = {"branch": "factory1", "SYSP_MILLTIMEKG": "0.3", "SYSP_MILLORDGAP": "2", "SYSP_OPETIMESTART": "08:00",
                "SYSP_OPETIMEEND": "17:00", "SYSP_QUEUEPERIOD": "16:00, 10:00", "SYSP_DELIVERTIMEADD": "1",
                "SYSP_MILLFEEUNITKG": "25", "SYSP_MILLFEEPERUNIT": "7", "SYSP_DELIVERYFEEKM": "5"}
        data.update(changes)
        return self.post("/admin/milling/maintenance", data)

    def test_milling_maintenance_saves_and_validates(self):
        self.assertEqual(self.maintenance().status_code, 302)
        branch = next(b for b in self.db("AOIMS_SYSPARAM") if b["BRANCH_ID"] == "factory1")
        self.assertEqual((branch["SYSP_MILLTIMEKG"], branch["SYSP_QUEUEPERIOD"], branch["SYSP_DELIVERYFEEKM"]), (0.3, "10:00, 16:00", 5.0))
        for field, bad in (("SYSP_MILLTIMEKG", "0"), ("SYSP_MILLFEEUNITKG", "0"), ("SYSP_MILLFEEPERUNIT", "-1"),
                           ("SYSP_DELIVERYFEEKM", "-1"), ("SYSP_OPETIMEEND", "07:00"), ("SYSP_QUEUEPERIOD", "25:00"), ("SYSP_MILLORDGAP", "x")):
            self.assertEqual(self.maintenance(**{field: bad}).status_code, 200, field)
        self.assertEqual(next(b for b in self.db("AOIMS_SYSPARAM") if b["BRANCH_ID"] == "factory2")["SYSP_MILLTIMEKG"], 0.273)

    def test_user_create_and_rules(self):
        new = {"USER_ID": "Malee.T", "USER_NAME": "Malee Thong", "PASSWORD": "secret123", "ROLE_CODE": "OPERATOR", "BRANCH_ID": "factory2"}
        self.assertEqual(self.post("/admin/users/new", new).status_code, 302)
        saved = next(u for u in self.db("AOIMS_USER") if u["USER_ID"] == "malee.t")
        self.assertNotIn("secret123", saved["PASSWORD_HASH"])
        self.assertIn("already exists", self.post("/admin/users/new", new).get_data(as_text=True))
        self.assertIn("at least 8", self.post("/admin/users/new", dict(new, USER_ID="other", PASSWORD="short")).get_data(as_text=True))
        self.assertIn("3-50 characters", self.post("/admin/users/new", dict(new, USER_ID="a b")).get_data(as_text=True))
        self.logout()
        self.assertEqual(self.login("malee.t", password="secret123").status_code, 302)

    def test_last_active_administrator_is_protected(self):
        page = self.post("/admin/users/admin", {"USER_NAME": "Admin", "ROLE_CODE": "ADMIN", "BRANCH_ID": "ALL", "USER_STATUS": "I"})
        self.assertIn("At least one active administrator", page.get_data(as_text=True))

    def test_unlocking_a_user_clears_the_failed_login_count(self):
        with self.app.app_context():
            col("AOIMS_USER").update_one({"USER_ID": "nittaya.p"}, {"$set": {"LOGIN_FAIL_COUNT": 5}})
        self.post("/admin/users/nittaya.p", {"USER_NAME": "Nittaya", "ROLE_CODE": "OPERATOR", "BRANCH_ID": "factory2", "USER_STATUS": "A"})
        user = next(u for u in self.db("AOIMS_USER") if u["USER_ID"] == "nittaya.p")
        self.assertEqual((user["USER_STATUS"], user["LOGIN_FAIL_COUNT"]), ("A", 0))

    def test_role_create_and_permission_rule(self):
        data = {"is_new": "1", "ROLE_CODE": "supervisor", "ROLE_NAME": "Supervisor", "ROLE_STATUS": "A",
                "perm_SV_SERVICE_view": "on", "perm_SV_SERVICE_create": "on", "perm_AD_USER_edit": "on"}   # edit without view
        self.assertEqual(self.post("/admin/roles/save", data, "/admin/roles/").status_code, 302)
        rows = {r["MENU_CODE"]: r for r in self.db("AOIMS_ROLEAUTH") if r["ROLE_CODE"] == "SUPERVISOR"}
        self.assertTrue(rows["SV_SERVICE"]["CAN_VIEW"] and rows["SV_SERVICE"]["CAN_CREATE"])
        self.assertFalse(rows["AD_USER"]["CAN_EDIT"])                    # dropped: the screen cannot be viewed
        self.assertEqual(len(rows), 24)                                  # one row per menu (update 31: SCADA)
        self.assertIn("already exists", self.post("/admin/roles/save", data, "/admin/roles/").get_data(as_text=True))

    def test_role_in_use_cannot_be_made_inactive_and_admin_stays_full(self):
        page = self.post("/admin/roles/save", {"is_new": "0", "ROLE_CODE": "OPERATOR", "ROLE_NAME": "Operator", "ROLE_STATUS": "I"}, "/admin/roles/")
        self.assertIn("cannot be made inactive", page.get_data(as_text=True))
        self.post("/admin/roles/save", {"is_new": "0", "ROLE_CODE": "ADMIN", "ROLE_NAME": "Administrator", "ROLE_STATUS": "I"}, "/admin/roles/")
        self.assertEqual(next(r for r in self.db("AOIMS_ROLE") if r["ROLE_CODE"] == "ADMIN")["ROLE_STATUS"], "A")
        self.assertEqual(self.client.get("/admin/roles/").status_code, 200)

    def test_customer_code_and_validation(self):
        data = {"CUST_NAME": "Tim Harper", "MOBILE_NO": "095-000-0000", "ADDRESS": "1 Test Road", "DISTANCE_KM": "4.5"}
        self.post("/admin/customers/new", data)
        self.post("/admin/customers/new", dict(data, CUST_NAME="Second", DISTANCE_KM=""))
        new = sorted((c for c in self.db("AOIMS_CUSTOMER") if c["CUST_NAME"] in ("Tim Harper", "Second")), key=lambda c: c["CUST_CODE"])
        self.assertTrue(all(re.match(r"^C\d{6}\d{3}$", c["CUST_CODE"]) for c in new))
        self.assertEqual(int(new[1]["CUST_CODE"][-3:]) - int(new[0]["CUST_CODE"][-3:]), 1)
        self.assertEqual((new[0]["DISTANCE_KM"], new[1]["DISTANCE_KM"]), (4.5, None))
        self.assertIn("name, phone and address", self.post("/admin/customers/new", dict(data, ADDRESS="")).get_data(as_text=True))
        self.assertIn("0 or more", self.post("/admin/customers/new", dict(data, DISTANCE_KM="-1")).get_data(as_text=True))
        self.post("/admin/customers/" + new[0]["CUST_CODE"], dict(data, MOBILE_NO="095-111-1111"))
        self.assertEqual(next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == new[0]["CUST_CODE"])["MOBILE_NO"], "095-111-1111")


class MillingOrders(Base):
    def setUp(self):
        super().setUp()
        self.login("admin", "factory2")
        self.client.get("/lang/en")

    def test_quote_matches_the_mill_test(self):
        response = self.post("/service/milling/quote", {"CUST_CODE": "C261001001", "REGIST_PADDY": "33", "BRAN_ORDER": "2", "BROKE_ORDER": "1"},
                             "/service/milling/new")
        body = response.get_json()
        self.assertEqual(body["outputs"], {"rice": 17.0, "bran": 3.3, "broken": 2.0, "husk": 7.26})
        # fee 1 unit x 12 + bran 2 x 12.00 x 90% + broken 1 x 12.30 x 90%
        self.assertEqual(body["total"], 12 + 21.6 + 11.07)
        self.assertEqual(self.db("AOITS_ORDMILLING"), [])               # a review saves nothing

    def test_quote_reports_missing_inputs(self):
        for data in ({"REGIST_PADDY": "33"}, {"CUST_CODE": "C261001001", "REGIST_PADDY": "0"},
                     {"CUST_CODE": "C261001001", "REGIST_PADDY": "33", "BRAN_ORDER": "-1"}):
            response = self.post("/service/milling/quote", data, "/service/milling/new")
            self.assertEqual(response.status_code, 422)
            self.assertFalse(response.get_json()["ok"])

    def test_order_is_created_with_every_field(self):
        order = self.new_order(BRAN_ORDER="2", BROKE_ORDER="1")
        self.assertRegex(order["ORD_ID"], r"^ML-\d{8}-0001$")
        self.assertEqual((order["QR_ID"], order["BRANCH_ID"], order["MILL_STATUS"], order["CANCEL_REQ_FLG"]), ("0001", "factory2", "Q", "N"))
        self.assertEqual((order["EXPCT_RICE"], order["EXPCT_BRAN"], order["EXPCT_BROKE"], order["EXPCT_HUSK"]), (17.0, 3.3, 2.0, 7.26))
        self.assertEqual((order["EST_PAY_MILLFEE"], order["EST_PAY_BRAN"], order["EST_PAY_BROKE"], order["EST_PAY_DELIVERY"]), (12.0, 21.6, 11.07, 0.0))
        self.assertEqual(order["OPE_USER"], "admin")
        self.assertIsNotNone(order["EXPCT_RECEIVE_TIME"])
        self.assertEqual(self.new_order()["QR_ID"], "0002")
        self.assertEqual(len(self.db("AOILOG_ORDMILLING")), 2)
        page = self.text("/service/milling/" + order["ORD_ID"])
        self.assertIn('data-qr="AOI-%s"' % order["ORD_ID"], page)
        self.assertIn('"no": "0001"', page.replace("&#34;", '"'))                    # the printed label carries the queue number

    def test_takeaway_order_runs_to_closed_and_posts_income(self):
        order = self.new_order()
        for status in ("I", "F", "C"):
            self.assertEqual(self.set_status(order["ORD_ID"], status).status_code, 302)
            self.assertEqual(self.order(order["ORD_ID"])["MILL_STATUS"], status)
        ledger = self.db("AOITS_ACCOUNT")
        self.assertEqual(len(ledger), 1)
        self.assertEqual((ledger[0]["ACC_TYPE"], ledger[0]["ACC_AMOUNT"], ledger[0]["REF_ORD_ID"], ledger[0]["AUTO_FLG"]),
                         ("IN", 12.0, order["ORD_ID"], True))
        self.assertEqual(len(self.db("AOILOG_ACCOUNT")), 1)
        self.set_status(order["ORD_ID"], "Q")                           # a closed order cannot change any more
        self.assertEqual(self.order(order["ORD_ID"])["MILL_STATUS"], "C")

    def test_changes_outside_the_flow_are_refused(self):
        order = self.new_order()
        for status in ("F", "C", "D", "X"):
            self.set_status(order["ORD_ID"], status)
            self.assertEqual(self.order(order["ORD_ID"])["MILL_STATUS"], "Q", status)

    def test_cancellation_request_flow(self):
        order = self.new_order()
        ord_id = order["ORD_ID"]
        self.set_status(ord_id, "I")
        self.post("/service/milling/%s/cancel-request" % ord_id)
        self.assertEqual(self.order(ord_id)["CANCEL_REQ_FLG"], "P")
        self.assertIn("!", self.text("/"))
        self.set_status(ord_id, "F")                                    # blocked while the request is pending
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "I")
        self.post("/service/milling/%s/cancel-answer" % ord_id, {"answer": "reject", "reason": "Already milled"})
        saved = self.order(ord_id)
        self.assertEqual((saved["CANCEL_REQ_FLG"], saved["CANCEL_REJECT_REASON"], saved["MILL_STATUS"]), ("R", "Already milled", "I"))
        self.post("/service/milling/%s/cancel-request" % ord_id)
        self.post("/service/milling/%s/cancel-answer" % ord_id, {"answer": "approve", "reason": "  "})
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "I")       # approving puts the order on Hold: a reason is required
        self.post("/service/milling/%s/cancel-answer" % ord_id, {"answer": "approve", "reason": "Customer changed plans"})
        saved = self.order(ord_id)
        self.assertEqual((saved["CANCEL_REQ_FLG"], saved["MILL_STATUS"], saved["HOLD_REASON"]), ("A", "H", "Customer changed plans"))
        self.set_status(ord_id, "Q")                                    # back to the queue drops the approval
        saved = self.order(ord_id)
        self.assertEqual((saved["CANCEL_REQ_FLG"], saved["MILL_STATUS"]), ("N", "Q"))
        self.set_status(ord_id, "H")
        self.set_status(ord_id, "CC")
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "CC")
        self.assertEqual(self.db("AOITS_ACCOUNT"), [])                  # nothing is posted for a cancelled order

    def test_delivery_order_and_type_changes(self):
        with self.app.app_context():
            col("AOIMS_SYSPARAM").update_one({"BRANCH_ID": "factory2"}, {"$set": {"SYSP_DELIVERYFEEKM": 5.0}})
        order = self.new_order(ORDER_TYPE="D")                          # address and 6 km come from the customer master
        ord_id = order["ORD_ID"]
        self.assertEqual((order["DELIVERY_DISTANCE"], order["EST_PAY_DELIVERY"]), (6.0, 30.0))
        self.assertTrue(order["DELIVERY_ADDRESS"])
        self.post("/service/milling/%s/details" % ord_id, {"DELIVERY_ADDRESS": "9 New Road", "DELIVERY_DISTANCE": "10"})
        saved = self.order(ord_id)
        self.assertEqual((saved["DELIVERY_ADDRESS"], saved["EST_PAY_DELIVERY"]), ("9 New Road", 50.0))
        for status in ("I", "F", "D"):
            self.set_status(ord_id, status)
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "D")
        self.post("/service/milling/%s/type" % ord_id, {"new_type": "T"})          # the customer picks up instead
        saved = self.order(ord_id)
        self.assertEqual((saved["ORDER_TYPE"], saved["MILL_STATUS"], saved["EST_PAY_DELIVERY"]), ("T", "F", 0.0))
        self.post("/service/milling/%s/type" % ord_id, {"new_type": "D", "DELIVERY_ADDRESS": "", "DELIVERY_DISTANCE": ""})
        saved = self.order(ord_id)
        self.assertEqual((saved["ORDER_TYPE"], saved["EST_PAY_DELIVERY"]), ("D", 30.0))   # customer's address and distance again
        self.set_status(ord_id, "C")                                    # a delivery order closes only after Ready for Delivery
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "F")

    def test_print_qr_saves_through_the_page_and_answers_in_json(self):
        data = {"CUST_CODE": "C261001001", "REGIST_PADDY": "33", "ORDER_TYPE": "T", "csrf_token": self.token("/service/milling/new")}
        body = self.client.post("/service/milling/new", data=data, headers={"Accept": "application/json"}).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual((body["qr_id"], body["label"]["no"], body["is_delivery"]), ("0001", "0001", False))
        self.assertEqual(body["list_url"], "/service/milling/")
        self.assertEqual(len(self.db("AOITS_ORDMILLING")), 1)
        data["REGIST_PADDY"] = "0"
        response = self.client.post("/service/milling/new", data=data, headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("paddy", response.get_json()["error"])

    def test_scan_field_lists_customers_whose_code_contains_the_text(self):
        body = self.client.get("/service/milling/customers.json?code=C2610010").get_json()
        self.assertFalse(body["exact"])
        self.assertEqual(len(body["customers"]), 5)
        body = self.client.get("/service/milling/customers.json?code=c261001003").get_json()
        self.assertTrue(body["exact"])
        self.assertEqual([c["code"] for c in body["customers"]], ["C261001003"])
        self.assertEqual(self.client.get("/service/milling/customers.json?code=XYZ").get_json()["customers"], [])

    def test_new_customer_from_customer_service_offers_the_way_back(self):
        data = {"CUST_NAME": "New Farmer", "MOBILE_NO": "095-222-3333", "ADDRESS": "3 Field Road", "DISTANCE_KM": "2"}
        response = self.post("/admin/customers/new?next=/service/milling/new", data, "/admin/customers/new?next=/service/milling/new")
        self.assertEqual(response.status_code, 302)
        code = next(c["CUST_CODE"] for c in self.db("AOIMS_CUSTOMER") if c["CUST_NAME"] == "New Farmer")
        self.assertIn("/admin/customers/%s" % code, response.headers["Location"])        # stays on the saved customer
        page = self.text(response.headers["Location"])
        self.assertIn('href="/service/milling/new?cust=%s" data-return-url' % code, page)
        self.assertIn("Go back to Customer Service", page)
        self.assertIn(">New Farmer<", self.text("/service/milling/new?cust=" + code).replace("value=\"New Farmer\"", ">New Farmer<"))

    def test_priority_can_be_changed_while_the_order_is_open(self):
        order = self.new_order()
        self.post("/service/milling/%s/priority" % order["ORD_ID"], {"priority": "U"})
        self.assertEqual(self.order(order["ORD_ID"])["MILL_PRIORITY"], "U")
        self.post("/service/milling/%s/priority" % order["ORD_ID"], {"priority": "X"})
        self.assertEqual(self.order(order["ORD_ID"])["MILL_PRIORITY"], "U")
        for status in ("H", "CC"):
            self.set_status(order["ORD_ID"], status)
        self.post("/service/milling/%s/priority" % order["ORD_ID"], {"priority": "L"})
        self.assertEqual(self.order(order["ORD_ID"])["MILL_PRIORITY"], "U")          # a cancelled order does not change

    def test_review_shows_the_label_with_the_next_queue_number(self):
        first = self.new_order()
        body = self.post("/service/milling/quote", {"CUST_CODE": "C261001003", "REGIST_PADDY": "33"}, "/service/milling/new").get_json()
        self.assertEqual(body["label"]["no"], "0002")
        self.assertEqual(body["label"]["qr"], "AOI-" + first["ORD_ID"][:-4] + "0002")
        self.assertEqual(self.new_order()["QR_ID"], "0002")                          # the preview did not use the number up

    def test_details_popup_returns_only_the_details(self):
        order = self.new_order()
        popup = self.text("/service/milling/%s?popup=1" % order["ORD_ID"])
        self.assertNotIn("<html", popup)
        self.assertIn('class="order-details od-editable"', popup)
        self.assertIn('data-qr="AOI-%s"' % order["ORD_ID"], popup)
        self.assertIn("<html", self.text("/service/milling/" + order["ORD_ID"]))

    def test_list_filters_by_status_and_type(self):
        take = self.new_order()
        deliver = self.new_order(ORDER_TYPE="D")
        self.set_status(take["ORD_ID"], "I")
        page = self.text("/service/milling/?status=I")
        self.assertIn(take["ORD_ID"], page)
        self.assertNotIn(deliver["ORD_ID"], page)
        page = self.text("/service/milling/?type=D")
        self.assertIn(deliver["ORD_ID"], page)
        self.assertNotIn(take["ORD_ID"], page)

    def test_delivery_needs_a_distance(self):
        with self.app.app_context():
            col("AOIMS_CUSTOMER").update_one({"CUST_CODE": "C261001001"}, {"$set": {"DISTANCE_KM": None}})
        response = self.post("/service/milling/new", {"CUST_CODE": "C261001001", "REGIST_PADDY": "33", "ORDER_TYPE": "D"}, "/service/milling/new")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Enter the delivery distance", response.get_data(as_text=True))

    def test_orders_belong_to_the_branch_signed_in_to(self):
        order = self.new_order()
        self.logout()
        self.login("admin", "factory1")
        self.assertEqual(self.client.get("/service/milling/" + order["ORD_ID"]).status_code, 404)
        self.assertEqual(self.set_status(order["ORD_ID"], "I").status_code, 404)
        self.assertNotIn(order["ORD_ID"], self.text("/service/milling/"))

    def test_operator_cannot_cancel_or_answer_requests_but_manager_can(self):
        self.logout()
        self.login("somchai.k")                                         # works at Factory 1
        order = self.new_order()
        ord_id = order["ORD_ID"]
        self.set_status(ord_id, "H")
        self.assertEqual(self.set_status(ord_id, "CC").status_code, 403)
        self.set_status(ord_id, "Q")
        self.post("/service/milling/%s/cancel-request" % ord_id)
        self.assertEqual(self.post("/service/milling/%s/cancel-answer" % ord_id, {"answer": "approve"}).status_code, 403)
        self.logout()
        self.login("prasert.m", "factory1")
        self.assertEqual(self.post("/service/milling/%s/cancel-answer" % ord_id, {"answer": "approve", "reason": "Customer asked"}).status_code, 302)
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "H")
        self.assertEqual(self.set_status(ord_id, "CC").status_code, 302)
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "CC")

    def test_receive_time_follows_the_queue(self):
        first = self.new_order(REGIST_PADDY="1500")
        second = self.new_order(REGIST_PADDY="33")
        urgent = self.new_order(REGIST_PADDY="33", MILL_PRIORITY="U")
        self.assertLessEqual(urgent["EXPCT_RECEIVE_TIME"], first["EXPCT_RECEIVE_TIME"])
        self.assertGreaterEqual(second["EXPCT_RECEIVE_TIME"], first["EXPCT_RECEIVE_TIME"])



class Update4(Base):
    """Tim's review of 2026-10-08: inbound at Finished, list order, hold reason, take-home selling orders."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def onhand(self):
        return self.db("AOINV_ONHAND")

    def bs(self, ord_id=None):
        orders = sorted(self.db("AOITS_ORDBUYSELL"), key=lambda o: o["ORD_ID"])
        return next(o for o in orders if o["ORD_ID"] == ord_id) if ord_id else orders

    def bs_status(self, ord_id, status, **fields):
        data = {"new_status": status}
        data.update(fields)
        return self.post("/service/buysell/%s/status" % ord_id, data)

    def test_finished_receives_the_rice_into_inventory(self):
        order = self.new_order()
        ord_id = order["ORD_ID"]
        self.set_status(ord_id, "I")
        for fields in ({"LOC_ID": ""}, {"LOC_ID": "1-2-3"}, {"LOC_ID": "01-002-005", "INV_QTY": "0"}, {"LOC_ID": "01-002-005", "INV_QTY": "x"}):
            self.set_status(ord_id, "F", **fields)
            self.assertEqual(self.order(ord_id)["MILL_STATUS"], "I", fields)     # nothing is saved without a valid inbound
        self.assertEqual(self.onhand(), [])
        with self.app.app_context():
            col("AOIMS_LOC").insert_one({"BRANCH_ID": "factory2", "LOC_ID": "09-009-009", "LOC_STATUS": "B"})
        self.set_status(ord_id, "F", LOC_ID="09-009-009")
        self.assertIn("Location 09-009-009 is blocked", self.text("/service/milling/"))
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "I")
        self.set_status(ord_id, "F", LOC_ID="01-002-005", INV_QTY="16.5", INV_STATUS="A")
        saved = self.order(ord_id)
        self.assertEqual((saved["MILL_STATUS"], saved["REF_INV_LOC"]), ("F", "01-002-005"))
        stock = self.onhand()
        self.assertEqual(len(stock), 1)
        self.assertEqual((stock[0]["BRANCH_ID"], stock[0]["MAT_CODE"], stock[0]["INV_QTY"], stock[0]["SUB_1"], stock[0]["SUB_2"], stock[0]["INV_STATUS"]),
                         ("factory2", "RICE_W5", 16.5, "LOT-" + ord_id, ord_id, "A"))
        self.assertRegex(stock[0]["INV_ID"], r"^IV-\d{8}-0001$")
        log = self.db("AOILOG_ONHAND")
        self.assertEqual((log[0]["OPE_TYPE"], log[0]["MOVE_TYPE"], log[0]["REF_ORD_ID"]), ("I", "Rice Mill", ord_id))
        loc = next(l for l in self.db("AOIMS_LOC") if l["LOC_ID"] == "01-002-005")
        self.assertEqual(loc["LOC_STATUS"], "O")
        self.set_status(ord_id, "C")                                              # the customer collects: stock released
        self.assertEqual(self.onhand(), [])
        self.assertEqual([l["OPE_TYPE"] for l in self.db("AOILOG_ONHAND")], ["I", "O"])
        loc = next(l for l in self.db("AOIMS_LOC") if l["LOC_ID"] == "01-002-005")
        self.assertEqual(loc["LOC_STATUS"], "A")
        self.assertIn("01-002-005", self.text("/service/milling/%s?popup=1" % ord_id))

    def test_the_list_offers_the_inbound_popup_with_the_last_location(self):
        first = self.new_order()
        self.set_status(first["ORD_ID"], "I")
        self.set_status(first["ORD_ID"], "F", LOC_ID="02-003-001")
        second = self.new_order()
        self.set_status(second["ORD_ID"], "I")
        page = self.text("/service/milling/")
        self.assertIn('data-form="inbound-%s"' % second["ORD_ID"], page)
        self.assertIn('<template id="inbound-%s">' % second["ORD_ID"], page)
        self.assertIn('name="LOC_ID" value="02-003-001"', page)                # suggested: the last location used
        self.assertIn('name="INV_QTY" value="17"', page)                        # expected rice

    def test_hold_needs_a_reason_and_the_new_flow(self):
        order = self.new_order()
        ord_id = order["ORD_ID"]
        self.set_status(ord_id, "H", HOLD_REASON=" ")
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "Q")
        self.set_status(ord_id, "H", HOLD_REASON="Waiting for dry paddy")
        saved = self.order(ord_id)
        self.assertEqual((saved["MILL_STATUS"], saved["HOLD_REASON"]), ("H", "Waiting for dry paddy"))
        self.assertIn("Waiting for dry paddy", self.text("/service/milling/"))
        self.set_status(ord_id, "I")                                             # Hold -> In-process (new)
        saved = self.order(ord_id)
        self.assertEqual((saved["MILL_STATUS"], saved["HOLD_REASON"]), ("I", None))
        self.set_status(ord_id, "Q")                                             # In-process -> Queue (new)
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "Q")
        self.set_status(ord_id, "CC")                                            # Queue -> Cancelled (new)
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "CC")
        other = self.new_order()["ORD_ID"]
        page = self.text("/service/milling/").replace("&#34;", '"')
        card = page[page.index('data-order="%s"' % other):]
        self.assertIn('"name": "HOLD_REASON"', card)
        self.assertIn('"required": true', card)

    def test_list_order_requests_in_process_hold_queue_finished_then_ended(self):
        orders = {}
        for name, prio in (("queue_low", "L"), ("queue_urgent", "U"), ("inproc", "M"), ("hold", "M"), ("finished", "M"),
                           ("closed", "M"), ("cancelled", "M"), ("request", "L")):
            orders[name] = self.new_order(MILL_PRIORITY=prio)["ORD_ID"]
        for name, steps in (("inproc", ["I"]), ("hold", ["H"]), ("finished", ["I", "F"]), ("closed", ["I", "F", "C"]),
                            ("cancelled", ["CC"]), ("request", [])):
            for status in steps:
                self.set_status(orders[name], status)
        self.post("/service/milling/%s/cancel-request" % orders["request"])
        page = self.text("/service/milling/")
        shown = re.findall(r'class="status-card[^"]*" data-order="([^"]+)"', page)
        expected = ["request", "inproc", "hold", "queue_urgent", "queue_low", "finished", "closed", "cancelled"]
        self.assertEqual(shown, [orders[n] for n in expected])

    def test_take_home_bran_and_broken_become_selling_orders(self):
        """Update 39: ONE selling order for the milling order, a line per SKU."""
        order = self.new_order(BRAN_ORDER="2", BROKE_ORDER="1")
        ord_id = order["ORD_ID"]
        linked = self.bs()
        self.assertEqual(len(linked), 1)
        sl = linked[0]
        self.assertEqual(order["REF_BUYING_ORDER"], sl["ORD_ID"])
        self.assertRegex(sl["ORD_ID"], r"^SL-\d{8}-0001$")
        self.assertEqual((sl["ORD_TYPE"], sl["ORD_STATUS"], sl["MAT_CODE"], sl["ORD_QTY"], sl["ORD_AMOUNT"], sl["REF_ORD_ID"], sl["CUST_CODE"]),
                         ("SL", "O", "BRAN", 3.0, 32.67, ord_id, "C261001001"))
        lines = {l["MAT_CODE"]: l for l in sl["ORD_ITEMS"]}
        self.assertEqual((lines["BRAN"]["ORD_QTY"], lines["BRAN"]["ORD_AMOUNT"]), (2.0, 21.6))
        self.assertEqual((lines["BROKEN"]["ORD_QTY"], lines["BROKEN"]["ORD_AMOUNT"], lines["BROKEN"]["PRICING_UINT"]), (1.0, 11.07, 11.07))
        self.assertEqual(len(self.db("AOILOG_ORDBUYSELL")), 1)
        page = self.text("/service/buysell/")
        self.assertIn("From Milling #%s" % order["QR_ID"], page)
        self.assertIn("Rice bran 2 kg, Broken rice (White) 1 kg", page)
        self.assertIn("Can be prepared when milling order #%s is finished" % order["QR_ID"], page)
        # cannot be prepared before the milling is finished
        self.bs_status(sl["ORD_ID"], "P", LOC_ID="01-002-005")
        self.assertEqual(self.bs(sl["ORD_ID"])["ORD_STATUS"], "O")
        self.set_status(ord_id, "I")
        self.set_status(ord_id, "F", LOC_ID="01-002-005")
        page = self.text("/service/buysell/")
        self.assertIn('data-form="prepare-%s"' % sl["ORD_ID"], page)
        self.assertIn('name="INV_QTY_BRAN"', page)
        self.assertIn('name="INV_QTY_BROKEN"', page)
        self.assertIn('name="LOC_ID" value="01-002-005"', page)                  # next to the customer's rice
        self.bs_status(sl["ORD_ID"], "P", LOC_ID="01-002-005", INV_QTY_BRAN="2", INV_QTY_BROKEN="1")
        self.assertEqual((self.bs(sl["ORD_ID"])["ORD_STATUS"], self.bs(sl["ORD_ID"])["REF_INV_LOC"]), ("P", "01-002-005"))
        stock = {s["MAT_CODE"]: s for s in self.onhand() if s["SUB_2"] == sl["ORD_ID"]}
        self.assertEqual((stock["BRAN"]["INV_QTY"], stock["BRAN"]["SUB_1"], stock["BROKEN"]["INV_QTY"]), (2.0, "LOT-" + ord_id, 1.0))
        # update 28: the milling order cannot be closed while its selling order is open
        self.assertIn('data-linked-open="%s"' % sl["ORD_ID"], self.text("/service/milling/"))
        self.set_status(ord_id, "C")
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "F")
        self.bs_status(sl["ORD_ID"], "C")                                        # the selling order posts both
        self.assertEqual(self.bs(sl["ORD_ID"])["ORD_STATUS"], "C")
        entries = self.db("AOITS_ACCOUNT")
        self.assertEqual([(a["REF_ORD_ID"], a["REF_ORD_TYPE"], a["ACC_TYPE"], a["ACC_AMOUNT"], a["ACC_DESC"]) for a in entries],
                         [(sl["ORD_ID"], "SL", "IN", 32.67, "Sold Rice bran, Broken rice (White)")])
        self.set_status(ord_id, "C")                                             # now it closes: only its fee is posted
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "C")
        self.assertEqual([(a["REF_ORD_ID"], a["ACC_AMOUNT"]) for a in self.db("AOITS_ACCOUNT")][-1], (ord_id, 12.0))
        self.assertEqual([s["MAT_CODE"] for s in self.onhand() if s["SUB_2"] == sl["ORD_ID"]], [])
        detail = self.text("/service/milling/%s?popup=1" % ord_id)
        self.assertIn('data-linked="%s"' % sl["ORD_ID"], detail)
        self.assertIn("Rice bran, Broken rice (White) taken home", detail)
        receipt = self.text("/service/buysell/billing?ids=" + sl["ORD_ID"])
        self.assertIn("21.60", receipt)
        self.assertIn("11.07", receipt)

    def test_old_take_home_orders_are_merged(self):
        """Update 39 migration: two Open orders (one per SKU, made before) become one order with two lines."""
        order = self.new_order(BRAN_ORDER="2", BROKE_ORDER="1")
        sl = self.bs()[0]
        with self.app.app_context():
            from app.models.buysell_order import BuySellOrder
            c = col("AOITS_ORDBUYSELL")
            c.update_one({"ORD_ID": sl["ORD_ID"]}, {"$set": {"MAT_CODE": "BRAN", "ORD_QTY": 2.0, "ORD_AMOUNT": 21.6, "PRICING_UINT": 10.8},
                                                   "$unset": {"ORD_ITEMS": ""}})
            c.insert_one(dict({k: v for k, v in sl.items() if k != "ORD_ITEMS"}, ORD_ID="SL-OLD-0002", QR_ID="0002", MAT_CODE="BROKEN",
                              ORD_QTY=1.0, ORD_AMOUNT=11.07, PRICING_UINT=11.07))
            self.assertEqual(BuySellOrder.merge_take_home("seed"), 1)
            self.assertEqual(BuySellOrder.merge_take_home("seed"), 0)           # once
        merged = self.bs(sl["ORD_ID"])
        self.assertEqual(({l["MAT_CODE"]: l["ORD_QTY"] for l in merged["ORD_ITEMS"]}, merged["ORD_AMOUNT"]), ({"BRAN": 2.0, "BROKEN": 1.0}, 32.67))
        self.assertEqual(self.bs("SL-OLD-0002")["ORD_STATUS"], "CC")
        self.assertEqual(self.order(order["ORD_ID"])["REF_BUYING_ORDER"], sl["ORD_ID"])

    def test_take_home_order_can_be_maintained_until_unpaid(self):
        """Update 39: Edit items changes the kg (and the amount) of each line; the milling order follows."""
        order = self.new_order(BRAN_ORDER="2", BROKE_ORDER="1")
        ord_id, sl = order["ORD_ID"], self.bs()[0]
        self.assertIn('data-open-form="edit-%s"' % sl["ORD_ID"], self.text("/service/buysell/"))
        self.post("/service/buysell/%s/items" % sl["ORD_ID"], {"QTY_BRAN": "4", "QTY_BROKEN": "0"})
        sl2 = self.bs(sl["ORD_ID"])
        lines = {l["MAT_CODE"]: l for l in sl2["ORD_ITEMS"]}
        self.assertEqual((lines["BRAN"]["ORD_QTY"], lines["BROKEN"]["ORD_QTY"], sl2["ORD_QTY"]), (4.0, 0.0, 4.0))
        self.assertEqual(sl2["ORD_AMOUNT"], lines["BRAN"]["ORD_AMOUNT"])
        self.assertGreater(lines["BRAN"]["ORD_AMOUNT"], 21.6)
        milling = self.order(ord_id)
        self.assertEqual((milling["BRAN_ORDER"], milling["BROKE_ORDER"], milling["EST_PAY_BRAN"], milling["EST_PAY_BROKE"]),
                         (4.0, 0.0, lines["BRAN"]["ORD_AMOUNT"], 0.0))
        self.post("/service/buysell/%s/items" % sl["ORD_ID"], {"QTY_BRAN": "0", "QTY_BROKEN": "0"})     # all zero: refused
        self.assertEqual(self.bs(sl["ORD_ID"])["ORD_QTY"], 4.0)
        # prepared: the stock kept for the customer follows
        self.set_status(ord_id, "I")
        self.set_status(ord_id, "F", LOC_ID="01-002-005")
        self.bs_status(sl["ORD_ID"], "P", LOC_ID="01-002-005")
        self.post("/service/buysell/%s/items" % sl["ORD_ID"], {"QTY_BRAN": "3", "QTY_BROKEN": "1.5"})
        stock = {s["MAT_CODE"]: s["INV_QTY"] for s in self.onhand() if s["SUB_2"] == sl["ORD_ID"]}
        self.assertEqual(stock, {"BRAN": 3.0, "BROKEN": 1.5})
        self.assertEqual((self.order(ord_id)["BRAN_ORDER"], self.order(ord_id)["BROKE_ORDER"]), (3.0, 1.5))
        logs = [l for l in self.db("AOILOG_ONHAND") if l["MOVE_TYPE"] == "Order changed"]
        self.assertEqual(len(logs), 1)                    # bran -1 (broken came in as a new inbound)
        # unpaid: no more changes
        self.bs_status(sl["ORD_ID"], "U")
        self.post("/service/buysell/%s/items" % sl["ORD_ID"], {"QTY_BRAN": "1"})
        self.assertEqual(self.bs(sl["ORD_ID"])["ORD_QTY"], 4.5)
        self.assertNotIn('data-open-form="edit-%s"' % sl["ORD_ID"], self.text("/service/buysell/"))

    def test_milling_order_is_cancelled_after_its_selling_orders(self):
        order = self.new_order(BRAN_ORDER="2")
        response = self.set_status(order["ORD_ID"], "CC")                        # update 28: refused, SL still open
        self.assertEqual(self.order(order["ORD_ID"])["MILL_STATUS"], "Q")
        self.assertIn("cannot be closed or cancelled yet", self.client.get(response.headers["Location"]).get_data(as_text=True))
        self.assertEqual(self.bs()[0]["ORD_STATUS"], "O")
        self.bs_status(self.bs()[0]["ORD_ID"], "CC")
        self.set_status(order["ORD_ID"], "CC")
        self.assertEqual(self.order(order["ORD_ID"])["MILL_STATUS"], "CC")
        self.assertEqual(self.db("AOITS_ACCOUNT"), [])

    def test_order_without_take_home_creates_no_selling_order(self):
        order = self.new_order()
        self.assertEqual(self.bs(), [])
        self.assertIsNone(order["REF_BUYING_ORDER"])

    def test_older_orders_without_selling_orders_still_post_everything(self):
        order = self.new_order(BRAN_ORDER="2")
        with self.app.app_context():                                             # as registered before update 4
            col("AOITS_ORDMILLING").update_one({"ORD_ID": order["ORD_ID"]}, {"$set": {"REF_BUYING_ORDER": None}})
            col("AOITS_ORDBUYSELL").delete_many({"REF_ORD_ID": order["ORD_ID"]})
        for status in ("I", "F", "C"):
            self.set_status(order["ORD_ID"], status)
        self.assertEqual([a["ACC_AMOUNT"] for a in self.db("AOITS_ACCOUNT")], [33.6])

    def test_buysell_rights(self):
        self.logout()
        self.login("somchai.k")                                                  # operator: view and edit, no delete
        order = self.new_order(BRAN_ORDER="2")
        sell = self.bs()[0]
        self.assertEqual(self.client.get("/service/buysell/").status_code, 200)
        self.assertEqual(self.bs_status(sell["ORD_ID"], "CC").status_code, 403)
        self.logout()
        self.login("admin", "factory2")
        self.assertEqual(self.client.get("/service/buysell/%s/status" % sell["ORD_ID"]).status_code, 405)
        self.bs_status(sell["ORD_ID"], "P", LOC_ID="01-001-001")                 # another branch's order
        self.assertEqual(self.bs(sell["ORD_ID"])["ORD_STATUS"], "O")
        self.assertNotIn(sell["ORD_ID"], self.text("/service/buysell/"))
        self.assertIn('href="/service/buysell/"', self.text("/?g=SV"))



class Update5(Base):
    """Tim's review of 2026-10-09: editable order details, delivery time confirmation at the top of the list."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    bs = Update4.bs

    def details(self, ord_id, **fields):
        return self.post("/service/milling/%s/details" % ord_id, fields)

    def test_details_recalculate_outputs_charges_and_linked_orders(self):
        order = self.new_order(BRAN_ORDER="2")
        ord_id = order["ORD_ID"]
        self.details(ord_id, CUST_CODE="C261001002", REGIST_PADDY="66", BRAN_ORDER="3", BROKE_ORDER="1", REMARK="Dry paddy")
        saved = self.order(ord_id)
        self.assertEqual((saved["CUST_CODE"], saved["REGIST_PADDY"], saved["EXPCT_RICE"], saved["EST_PAY_MILLFEE"], saved["REMARK"]),
                         ("C261001002", 66.0, 34.0, 12.0, "Dry paddy"))
        self.assertEqual((saved["BRAN_ORDER"], saved["BROKE_ORDER"], saved["QR_ID"]), (3.0, 1.0, order["QR_ID"]))   # same queue number
        self.assertEqual(len(self.bs()), 1)                                      # update 39: still one order
        sl = self.bs()[0]
        lines = {l["MAT_CODE"]: l for l in sl["ORD_ITEMS"]}
        self.assertEqual((lines["BRAN"]["ORD_QTY"], lines["BRAN"]["ORD_AMOUNT"], sl["CUST_CODE"]), (3.0, saved["EST_PAY_BRAN"], "C261001002"))
        self.assertEqual((lines["BROKEN"]["ORD_QTY"], sl["ORD_STATUS"]), (1.0, "O"))
        self.assertEqual(saved["REF_BUYING_ORDER"], sl["ORD_ID"])
        self.details(ord_id, BRAN_ORDER="0", BROKE_ORDER="1")                     # no bran any more: its line is 0
        sl = self.bs()[0]
        self.assertEqual(({l["MAT_CODE"]: l["ORD_QTY"] for l in sl["ORD_ITEMS"]}, sl["MAT_CODE"], sl["ORD_STATUS"]), ({"BRAN": 0.0, "BROKEN": 1.0}, "BROKEN", "O"))
        self.details(ord_id, BRAN_ORDER="0", BROKE_ORDER="0")                     # nothing taken home: cancelled
        self.assertEqual(self.bs()[0]["ORD_STATUS"], "CC")
        self.assertIsNone(self.order(ord_id)["REF_BUYING_ORDER"])
        self.details(ord_id, BRAN_ORDER="1")                                      # again: a new order
        self.assertEqual([o["ORD_STATUS"] for o in self.bs()], ["CC", "O"])
        for fields in ({"REGIST_PADDY": "0"}, {"REGIST_PADDY": "x"}, {"BRAN_ORDER": "-1"}, {"CUST_CODE": "C000000000"}):
            self.details(ord_id, **fields)
            self.assertEqual(self.order(ord_id)["REGIST_PADDY"], 66.0, fields)

    def test_quantities_lock_at_finished_but_remark_still_changes(self):
        order = self.new_order(BRAN_ORDER="2")
        ord_id = order["ORD_ID"]
        self.set_status(ord_id, "I")
        self.details(ord_id, REGIST_PADDY="40")                                  # In-process: still allowed
        self.assertEqual(self.order(ord_id)["REGIST_PADDY"], 40.0)
        self.set_status(ord_id, "F")
        self.details(ord_id, REGIST_PADDY="50")
        self.assertIn("can only change before the order is Finished", self.text("/service/milling/"))
        self.assertEqual(self.order(ord_id)["REGIST_PADDY"], 40.0)
        self.details(ord_id, REMARK="Pick up tomorrow")
        self.assertEqual(self.order(ord_id)["REMARK"], "Pick up tomorrow")
        popup = self.text("/service/milling/%s?popup=1" % ord_id)
        self.assertNotIn('name="REGIST_PADDY"', popup)
        self.assertIn('name="REMARK"', popup)

    def test_prepared_selling_order_blocks_a_quantity_change(self):
        order = self.new_order(BRAN_ORDER="2")
        ord_id = order["ORD_ID"]
        with self.app.app_context():
            col("AOITS_ORDBUYSELL").update_one({"REF_ORD_ID": ord_id}, {"$set": {"ORD_STATUS": "P"}})
        self.details(ord_id, BRAN_ORDER="5")
        self.assertEqual(self.order(ord_id)["BRAN_ORDER"], 2.0)
        self.assertEqual(self.bs()[0]["ORD_QTY"], 2.0)

    def test_details_are_read_only_without_edit_right_or_when_closed(self):
        order = self.new_order()
        for status in ("I", "F", "C"):
            self.set_status(order["ORD_ID"], status)
        self.assertNotIn("od-edit-form", self.text("/service/milling/%s?popup=1" % order["ORD_ID"]))
        self.details(order["ORD_ID"], REMARK="late")
        self.assertFalse(self.order(order["ORD_ID"]).get("REMARK"))

    def test_delivery_waits_at_the_top_until_its_time_is_confirmed(self):
        with self.app.app_context():
            col("AOIMS_SYSPARAM").update_one({"BRANCH_ID": "factory2"}, {"$set": {"SYSP_DELIVERYFEEKM": 5.0}})
        inproc = self.new_order()
        self.set_status(inproc["ORD_ID"], "I")
        deliver = self.new_order(ORDER_TYPE="D")
        ord_id = deliver["ORD_ID"]
        for status in ("I", "F", "D"):
            self.set_status(ord_id, status)
        page = self.text("/service/milling/")
        shown = re.findall(r'class="status-card[^"]*" data-order="([^"]+)"', page)
        self.assertEqual(shown, [ord_id, inproc["ORD_ID"]])
        self.assertIn('class="status-card needs-attention" data-order="%s"' % ord_id, page)
        self.assertIn('name="DELIVERY_ARRIVAL_TIME" value="20', page)                    # suggested: now + 2 hours
        self.post("/service/milling/%s/delivery" % ord_id, {"action": "confirm", "DELIVERY_ADDRESS": "9 New Road",
                                                           "DELIVERY_DISTANCE": "10", "DELIVERY_ARRIVAL_TIME": ""})
        self.assertFalse(self.order(ord_id)["DELIVERY_CONFIRM_FLG"])                     # arrival time required
        self.post("/service/milling/%s/delivery" % ord_id, {"action": "confirm", "DELIVERY_ADDRESS": "9 New Road",
                                                           "DELIVERY_DISTANCE": "10", "DELIVERY_ARRIVAL_TIME": "2026-10-09T15:30"})
        saved = self.order(ord_id)
        self.assertEqual((saved["DELIVERY_CONFIRM_FLG"], saved["DELIVERY_ADDRESS"], saved["EST_PAY_DELIVERY"]), (True, "9 New Road", 50.0))
        with self.app.app_context():
            from app.timeutil import to_local
            self.assertEqual(to_local(saved["DELIVERY_ARRIVAL_TIME"]).strftime("%Y-%m-%d %H:%M"), "2026-10-09 15:30")
        shown = re.findall(r'class="status-card[^"]*" data-order="([^"]+)"', self.text("/service/milling/"))
        self.assertEqual(shown, [inproc["ORD_ID"], ord_id])                               # back to the usual sequence
        self.assertIn("Delivery time confirmed", self.text("/service/milling/"))
        self.post("/service/milling/%s/delivery" % ord_id, {"action": "reopen"})         # failed delivery: confirm again
        self.assertFalse(self.order(ord_id)["DELIVERY_CONFIRM_FLG"])
        self.assertEqual(re.findall(r'class="status-card[^"]*" data-order="([^"]+)"', self.text("/service/milling/"))[0], ord_id)
        self.post("/service/milling/%s/delivery" % inproc["ORD_ID"], {"action": "confirm", "DELIVERY_ADDRESS": "x",
                                                                     "DELIVERY_DISTANCE": "1", "DELIVERY_ARRIVAL_TIME": "2026-10-09T15:30"})
        self.assertNotIn("DELIVERY_CONFIRM_FLG", [k for k, v in self.order(inproc["ORD_ID"]).items() if v])



class Update6(Base):
    """Customer Service > Buy / Sell (the design's rate tables) and their flow on Update Buy/Sell Status."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    bs = Update4.bs

    def trade(self, ord_type, mode="open", cust="C261001001", **fields):
        data = {"ORD_TYPE": ord_type, "mode": mode, "CUST_CODE": cust, "csrf_token": self.token("/service/milling/new")}
        data.update(fields)
        return self.client.post("/service/buysell/new", data=data, headers={"Accept": "application/json"})

    def bs_status(self, ord_id, status, **fields):
        data = {"new_status": status}
        data.update(fields)
        return self.post("/service/buysell/%s/status" % ord_id, data)

    def test_page_shows_the_buy_and_sell_tables_with_current_prices(self):
        page = self.text("/service/milling/new")
        self.assertIn('id="buy-rate-table"', page)
        self.assertIn('id="sell-rate-table"', page)
        self.assertIn('data-mat="PADDY"', page)
        self.assertIn('data-rate="16.15"', page)                          # paddy buying price at Factory 2
        self.assertIn('data-rate="20.41"', page)                          # paddy selling price
        self.assertNotIn('data-svc="svc-buy" disabled', page)
        self.assertIn('class="svc-btn active" type="button" data-svc="svc-sell"', self.text("/service/milling/new?svc=sell"))

    def test_open_creates_one_order_per_product(self):
        body = self.trade("BY", QTY_PADDY="500", QTY_BRAN="20").get_json()
        self.assertTrue(body["ok"], body)
        orders = self.bs()
        self.assertEqual([(o["ORD_TYPE"], o["MAT_CODE"], o["ORD_QTY"], o["PRICING_UINT"], o["ORD_AMOUNT"], o["ORD_STATUS"]) for o in orders],
                         [("BY", "PADDY", 500.0, 16.15, 8075.0, "O"), ("BY", "BRAN", 20.0, 7.68, 153.6, "O")])
        self.assertRegex(orders[0]["ORD_ID"], r"^BY-\d{8}-0001$")
        self.assertIsNone(orders[0]["REF_ORD_ID"])
        self.assertEqual(body["list_url"], "/service/buysell/")
        self.assertIn("Order created (2 item(s))", body["message"])
        page = self.text("/service/buysell/")
        self.assertIn("New Order", page)
        self.assertIn('data-form="prepare-%s"' % orders[0]["ORD_ID"], page)      # buying: Prepared receives the goods

    def test_registration_checks(self):
        for fields, message in (({"cust": ""}, "customer name"), ({}, "at least one product"), ({"QTY_RICE_W5": "-1"}, "negative"),
                                ({"QTY_RICE_W5": "x"}, "number")):
            cust = fields.pop("cust", "C261001001")
            body = self.trade("SL", cust=cust, **fields).get_json()
            self.assertFalse(body["ok"])
            self.assertIn(message, body["error"].lower() if message.islower() else body["error"])
        with self.app.app_context():
            col("AOIMS_PRICE").update_one({"BRANCH_ID": "factory2", "MAT_CODE": "RICE_W5"}, {"$set": {"PRICE_SELL": 0}})
        self.assertIn("no current price for Rice", self.trade("SL", QTY_RICE_W5="5").get_json()["error"])
        self.assertEqual(self.bs(), [])
        self.logout()
        self.login("wanida.s")                                              # no create right on Customer Service
        self.assertEqual(self.trade("SL", QTY_BRAN="5").status_code, 403)

    def test_buy_close_receives_stock_and_posts_the_expense(self):
        body = self.trade("BY", "close", QTY_PADDY="500", LOC_ID="03-001-001", INV_STATUS="A").get_json()
        self.assertTrue(body["ok"], body)
        order = self.bs()[0]
        self.assertEqual((order["ORD_STATUS"], order["REF_INV_LOC"]), ("C", "03-001-001"))
        stock = self.db("AOINV_ONHAND")
        self.assertEqual([(s["MAT_CODE"], s["INV_QTY"], s["LOC_ID"], s["SUB_1"], s["SUB_2"]) for s in stock],
                         [("PADDY", 500.0, "03-001-001", "LOT-" + order["ORD_ID"], None)])           # the mill's own stock
        ledger = self.db("AOITS_ACCOUNT")
        self.assertEqual([(a["ACC_TYPE"], a["ACC_AMOUNT"], a["REF_ORD_TYPE"], a["ACC_DESC"]) for a in ledger], [("EX", 8075.0, "BY", "Bought Paddy (White)")])
        self.assertFalse(self.trade("BY", "close", QTY_PADDY="5", LOC_ID="bad").get_json()["ok"])      # location checked first
        self.assertEqual(len(self.bs()), 1)
        receipt = self.text(body["billing_url"] + "&popup=1")
        self.assertIn("8,075.00", receipt)
        self.assertIn("data-print-receipt", receipt)

    def test_sell_close_releases_stock_first_in_first_out(self):
        self.trade("BY", "close", QTY_BRAN="30", LOC_ID="02-001-001")
        self.trade("BY", "close", QTY_BRAN="50", LOC_ID="02-001-002")
        check = self.post("/service/buysell/check", {"ORD_TYPE": "SL", "QTY_BRAN": "40"}, "/service/milling/new").get_json()
        self.assertEqual((check["lines"][0]["available"], check["lines"][0]["short"]), (80.0, False))
        body = self.trade("SL", "close", QTY_BRAN="40").get_json()
        self.assertTrue(body["ok"], body)
        stock = sorted(self.db("AOINV_ONHAND"), key=lambda s: s["LOC_ID"])
        self.assertEqual([(s["LOC_ID"], s["INV_QTY"]) for s in stock], [("02-001-002", 40.0)])     # the older 30 kg went first
        sell = [o for o in self.bs() if o["ORD_TYPE"] == "SL"][0]
        self.assertEqual((sell["ORD_STATUS"], sell["ORD_AMOUNT"]), ("C", 480.0))
        self.assertIn(("IN", 480.0, "Sold Rice bran"), [(a["ACC_TYPE"], a["ACC_AMOUNT"], a["ACC_DESC"]) for a in self.db("AOITS_ACCOUNT")])
        out = [l for l in self.db("AOILOG_ONHAND") if l["OPE_TYPE"] == "O"]
        self.assertEqual([(l["LOC_ID"] if "LOC_ID" in l else l["NEWLOC_ID"], l["INV_QTY"], l["MOVE_TYPE"]) for l in out],
                         [("02-001-001", 30.0, "Selling"), ("02-001-002", 10.0, "Selling")])
        check = self.post("/service/buysell/check", {"ORD_TYPE": "SL", "QTY_BRAN": "100"}, "/service/milling/new").get_json()
        self.assertEqual((check["lines"][0]["available"], check["lines"][0]["short"]), (40.0, True))

    def test_open_orders_run_through_the_list(self):
        self.trade("BY", QTY_RICE_W5="100")
        self.trade("SL", QTY_RICE_W5="60")
        buy, sell = sorted(self.bs(), key=lambda o: o["ORD_TYPE"])
        self.bs_status(buy["ORD_ID"], "P")                                        # buying needs the location
        self.assertEqual(self.bs(buy["ORD_ID"])["ORD_STATUS"], "O")
        self.bs_status(buy["ORD_ID"], "P", LOC_ID="01-001-001", INV_QTY="98")     # 98 kg actually received
        self.assertEqual(self.bs(buy["ORD_ID"])["ORD_STATUS"], "P")
        self.assertEqual(self.db("AOINV_ONHAND")[0]["INV_QTY"], 98.0)
        page = self.text("/service/buysell/")
        card = page[page.index('data-order="%s"' % buy["ORD_ID"]):]
        self.assertNotIn('value="CC"', card[:card.index("</select>")])           # received goods: no cancel
        self.bs_status(buy["ORD_ID"], "C")
        self.assertEqual(self.bs(buy["ORD_ID"])["ORD_STATUS"], "C")
        self.bs_status(sell["ORD_ID"], "P")                                       # selling: no stock moves at Prepared
        self.assertEqual(self.bs(sell["ORD_ID"])["ORD_STATUS"], "P")
        self.assertIn("on hand: 98.00 kg", self.text("/service/buysell/"))        # the close confirmation shows the stock
        self.assertEqual(self.db("AOINV_ONHAND")[0]["INV_QTY"], 98.0)
        self.bs_status(sell["ORD_ID"], "U")
        self.bs_status(sell["ORD_ID"], "C")
        self.assertEqual(self.db("AOINV_ONHAND")[0]["INV_QTY"], 38.0)
        entries = sorted((a["REF_ORD_TYPE"], a["ACC_TYPE"], a["ACC_AMOUNT"]) for a in self.db("AOITS_ACCOUNT"))
        self.assertEqual(entries, [("BY", "EX", 1880.0), ("SL", "IN", 1320.0)])

    def test_billing_belongs_to_the_branch(self):
        body = self.trade("SL", QTY_PADDY="10").get_json()
        self.logout()
        self.login("admin", "factory1")
        self.assertEqual(self.client.get(body["billing_url"]).status_code, 404)



class Update7(Base):
    """Inventory: list, add, edit operations, transfer, history, dashboard."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def stock(self):
        return sorted(self.db("AOINV_ONHAND"), key=lambda r: (r["MAT_CODE"], r["LOC_ID"], r["INV_STATUS"]))

    def add(self, **fields):
        data = {"MAT_CODE": "HUSK", "INV_QTY": "300", "LOC_ID": "03-001-001", "INV_STATUS": "A", "OPE_TYPE": "I", "REASON": "milling"}
        data.update(fields)
        return self.post("/inventory/new", data, "/inventory/new")

    def test_menu_opens_the_three_screens(self):
        home = self.text("/?g=IV")
        for path in ("/inventory/", "/inventory/history", "/inventory/dashboard"):
            self.assertIn('href="%s"' % path, home)
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_add_new_item_for_example_husk(self):
        self.add(SUB_1="h1")
        rec = self.stock()[0]
        self.assertEqual((rec["MAT_CODE"], rec["INV_QTY"], rec["LOC_ID"], rec["SUB_1"], rec["SUB_2"], rec["INV_STATUS"]),
                         ("HUSK", 300.0, "03-001-001", "H1", None, "A"))
        self.add(SUB_1="h1", INV_QTY="50")                                      # same item and place: added together
        self.assertEqual([r["INV_QTY"] for r in self.stock()], [350.0])
        log = self.db("AOILOG_ONHAND")
        self.assertEqual([(l["OPE_TYPE"], l["MOVE_TYPE"], l["INV_QTY"]) for l in log], [("I", "Rice Mill", 300.0), ("I", "Rice Mill", 50.0)])
        self.add(INV_QTY="10", REASON="other", REMARK="")                          # Other needs a remark
        self.add(INV_QTY="0")
        self.add(LOC_ID="x")
        self.add(MAT_CODE="GOLD")
        self.assertEqual(len(self.stock()), 1)
        self.add(INV_QTY="10", REASON="other", REMARK="Found in store", OPE_TYPE="AI")      # blank lot: LOT + today
        self.assertRegex(next(r for r in self.stock() if r["SUB_1"] != "H1")["SUB_1"], r"^LOT-\d{8}$")
        self.assertIn(("AI", "Other - Found in store"), [(l["OPE_TYPE"], l["MOVE_TYPE"]) for l in self.db("AOILOG_ONHAND")])
        page = self.text("/inventory/")
        self.assertIn("Husk", page)
        self.assertIn("350.00", page)

    def test_edit_item_operations(self):
        self.add(MAT_CODE="BRAN", SUB_1="B1", INV_QTY="100")
        rec = self.stock()[0]
        url = "/inventory/item/" + rec["INV_ID"]
        self.assertIn("Edit Item", self.text(url))
        self.post(url, {"REC_ID": rec["INV_ID"], "OPE_TYPE": "AD", "INV_QTY": "30", "REASON": "selling"})
        self.assertEqual([r["INV_QTY"] for r in self.stock()], [70.0])
        response = self.post(url, {"REC_ID": rec["INV_ID"], "OPE_TYPE": "O", "INV_QTY": "500", "REASON": "selling"})   # not that much
        self.assertIn("has only 70.00 kg", response.get_data(as_text=True))
        self.post(url, {"REC_ID": rec["INV_ID"], "OPE_TYPE": "AI", "INV_QTY": "20", "LOC_ID": "02-002-002", "REASON": "producing"})
        self.assertEqual([(r["LOC_ID"], r["INV_QTY"]) for r in self.stock()], [("02-002-002", 20.0), ("03-001-001", 70.0)])
        self.post(url, {"REC_ID": rec["INV_ID"], "OPE_TYPE": "ST", "NEW_STATUS": "B", "INV_QTY": "10"})        # part of a location
        rows = [(r["LOC_ID"], r["INV_STATUS"], r["INV_QTY"]) for r in self.stock()]
        self.assertEqual(sorted(rows), [("02-002-002", "A", 20.0), ("03-001-001", "A", 60.0), ("03-001-001", "B", 10.0)])
        st = [l for l in self.db("AOILOG_ONHAND") if l["OPE_TYPE"] == "ST"][0]
        self.assertEqual((st["ORI_STATUS"], st["NEW_STATUS"], st["INV_QTY"]), ("A", "B", 10.0))
        self.assertIn("Blocked", self.text("/inventory/"))

    def test_transfer(self):
        self.add(MAT_CODE="PADDY", INV_QTY="500", LOC_ID="01-001-001")
        rec = self.stock()[0]
        self.assertIn('data-qty="500"', self.text("/inventory/transfer?inv=" + rec["INV_ID"]))
        for fields in ({"TO_LOC": "01-001-001", "INV_QTY": "5"}, {"TO_LOC": "01-001-002", "INV_QTY": "900"}, {"TO_LOC": "bad", "INV_QTY": "5"}):
            self.post("/inventory/transfer", dict(fields, INV_ID=rec["INV_ID"]), "/inventory/transfer")
        self.assertEqual([r["INV_QTY"] for r in self.stock()], [500.0])
        self.post("/inventory/transfer", {"INV_ID": rec["INV_ID"], "TO_LOC": "01-001-002", "INV_QTY": "200"}, "/inventory/transfer")
        self.assertEqual([(r["LOC_ID"], r["INV_QTY"]) for r in self.stock()], [("01-001-001", 300.0), ("01-001-002", 200.0)])
        t = [l for l in self.db("AOILOG_ONHAND") if l["OPE_TYPE"] == "T"][0]
        self.assertEqual((t["ORILOC_ID"], t["NEWLOC_ID"], t["INV_QTY"]), ("01-001-001", "01-001-002", 200.0))
        locs = {l["LOC_ID"]: l["LOC_STATUS"] for l in self.db("AOIMS_LOC")}
        self.assertEqual((locs["01-001-001"], locs["01-001-002"]), ("O", "O"))
        self.post("/inventory/transfer", {"INV_ID": rec["INV_ID"], "TO_LOC": "01-001-002", "INV_QTY": "300"}, "/inventory/transfer")
        self.assertEqual([(r["LOC_ID"], r["INV_QTY"]) for r in self.stock()], [("01-001-002", 500.0)])
        self.assertEqual({l["LOC_ID"]: l["LOC_STATUS"] for l in self.db("AOIMS_LOC")}["01-001-001"], "A")

    def test_customer_stock_shows_who_it_is_kept_for(self):
        order = self.new_order()
        self.set_status(order["ORD_ID"], "I")
        self.post("/service/milling/%s/status" % order["ORD_ID"], {"new_status": "F", "LOC_ID": "01-002-005"})
        page = self.text("/inventory/")
        self.assertIn("Kept for: Milling #0001", page)
        self.assertIn("LOT-" + order["ORD_ID"], page)

    def test_history_filters_and_sorts(self):
        self.add(MAT_CODE="PADDY", INV_QTY="500")
        self.add(MAT_CODE="BRAN", INV_QTY="40")
        page = self.text("/inventory/history")
        self.assertLess(page.index("Rice bran"), page.index("Paddy"))                 # newest first
        page = self.text("/inventory/history?sort=asc")
        self.assertLess(page.index("Paddy"), page.index("Rice bran"))
        page = self.text("/inventory/history?col=sku&v=paddy")
        self.assertIn("+500.00 kg", page)
        self.assertNotIn("+40.00 kg", page)
        self.assertIn("No matching movements", self.text("/inventory/history?col=qty&v=999"))

    def test_dashboard_numbers(self):
        self.add(MAT_CODE="PADDY", INV_QTY="2500")
        self.add(MAT_CODE="BRAN", INV_QTY="40")
        rec = [r for r in self.stock() if r["MAT_CODE"] == "BRAN"][0]
        self.post("/inventory/item/" + rec["INV_ID"], {"REC_ID": rec["INV_ID"], "OPE_TYPE": "O", "INV_QTY": "15", "REASON": "selling"})
        page = self.text("/inventory/dashboard")
        self.assertIn("2,525.00 kg", page)                  # total stock
        self.assertIn("+2,540.00 kg", page)                 # in today
        self.assertIn("-15.00 kg", page)                    # out today
        self.assertIn('id="chart-pie"', page)
        self.assertIn("below 2000 kg: Rice bran</p>", page)  # SKUs the mill keeps whose own stock is below 2000 kg

    def test_rights(self):
        self.logout()
        self.login("somchai.k")                             # operator: IV_LIST view/create/edit, history view, no dashboard
        self.assertEqual(self.client.get("/inventory/").status_code, 200)
        self.assertEqual(self.client.get("/inventory/dashboard").status_code, 403)
        self.logout()
        self.login("wanida.s")                              # account role: dashboard only
        self.assertEqual(self.client.get("/inventory/").status_code, 403)
        self.assertEqual(self.client.get("/inventory/dashboard").status_code, 200)



class Update8(Base):
    """The last seven menus: Pricing, Margin, Account, Marketing, Service Status, Current Price, Service Dashboard."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def price(self, mat, branch="factory2"):
        return next(p for p in self.db("AOIMS_PRICE") if p["MAT_CODE"] == mat and p["BRANCH_ID"] == branch)

    def test_every_menu_opens_and_none_is_planned(self):
        for group in ("SV", "IV", "FN", "AT", "AD"):
            home = self.text("/?g=" + group)
            self.assertNotIn("/planned/", home, group)
            for path in re.findall(r'href="(/[^"?]*)" data-menu', home):
                self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_pricing_approve_edit_and_custom(self):
        paddy = self.price("PADDY")
        self.assertEqual(paddy["PRICE_STATUS"], "W")
        page = self.text("/finance/pricing?branch=factory2")
        self.assertIn("Approve", page)
        self.post("/finance/pricing", {"branch": "factory2", "MAT_CODE": "PADDY", "PRICE_BUY": "16.40", "PRICE_SELL": "20.50"}, "/finance/pricing")
        paddy = self.price("PADDY")
        self.assertEqual((paddy["PRICE_STATUS"], paddy["PRICE_BUY"], paddy["PRICE_SELL"]), ("A", 16.4, 20.5))
        self.assertIn('data-rate="16.4"', self.text("/service/milling/new"))                 # Buy table uses the new price
        self.post("/finance/pricing", {"branch": "factory2", "MAT_CODE": "HUSK", "PRICE_SELL": "15.5"}, "/finance/pricing")
        self.assertEqual((self.price("HUSK")["PRICE_SELL"], self.price("HUSK")["PRICE_STATUS"]), (15.5, "C"))
        self.post("/finance/pricing", {"branch": "factory2", "MAT_CODE": "BRAN", "PRICE_BUY": "-1", "PRICE_SELL": "12"}, "/finance/pricing")
        self.assertEqual(self.price("BRAN")["PRICE_BUY"], 7.68)
        self.assertGreaterEqual(len(self.db("AOILOG_PRICE")), 2)
        self.assertIn("16.40", self.text("/service/price"))                                     # Current Price board

    def test_market_and_margin_make_a_new_suggestion_that_waits_for_approval(self):
        self.post("/finance/margin", {"branch": "factory2", "buy_target__BRAN": "5", "sell_target__BRAN": "10",
                                      "buy_range__BRAN": "3-7", "sell_range__BRAN": "8-12", "floor__BRAN": "6", "ceiling__BRAN": "13"},
                  "/finance/margin")
        bran = self.price("BRAN")
        self.assertEqual((bran["MG_BUY_TARGET"], bran["MG_BUY_RANGEFROM"], bran["MG_BUY_RANGETO"], bran["MG_BUY_MIN"], bran["MG_SELL_MAX"]),
                         (5.0, 3.0, 7.0, 6.0, 13.0))
        self.assertEqual((bran["SUGPRICE_BUY"], bran["SUGPRICE_SELL"]), (7.6, 12.76))       # 8 x 0.95, 11.6 x 1.10
        self.assertEqual(bran["PRICE_STATUS"], "W")
        self.post("/finance/pricing", {"branch": "factory2", "MAT_CODE": "BRAN", "action": "market", "MKPRICE_BUY": "10", "MKPRICE_SELL": "14"}, "/finance/pricing")
        bran = self.price("BRAN")
        self.assertEqual((bran["SUGPRICE_BUY"], bran["SUGPRICE_SELL"]), (9.5, 13.0))        # sell capped at the ceiling
        self.assertIn('value="9.50"', self.text("/finance/pricing?branch=factory2&f=pending"))  # the suggestion is proposed
        for bad in ({"buy_range__BRAN": "7-3"}, {"floor__BRAN": "20", "ceiling__BRAN": "10"}, {"buy_target__BRAN": "x"}):
            data = {"branch": "factory2", "buy_target__BRAN": "5", "sell_target__BRAN": "10"}
            data.update(bad)
            self.post("/finance/margin", data, "/finance/margin")
            self.assertEqual(self.price("BRAN")["MG_BUY_RANGEFROM"], 3.0, bad)

    def test_approve_needs_the_approve_right(self):
        self.logout()
        self.login("wanida.s")                                      # account role: view pricing only
        self.assertEqual(self.client.get("/finance/pricing").status_code, 200)
        self.assertEqual(self.post("/finance/pricing", {"MAT_CODE": "PADDY", "PRICE_BUY": "1", "PRICE_SELL": "2"}, "/finance/pricing").status_code, 403)

    def test_account_manual_entries(self):
        self.post("/finance/account", {"ACC_DATE": "2026-10-11", "ACC_TYPE": "EX", "ACC_DESC": "Electricity", "ACC_AMOUNT": "1250.50", "REF_ORD_ID": "INV-9"}, "/finance/account")
        entry = self.db("AOITS_ACCOUNT")[0]
        self.assertEqual((entry["ACC_TYPE"], entry["ACC_AMOUNT"], entry["AUTO_FLG"], entry["REF_ORD_ID"]), ("EX", 1250.5, False, "INV-9"))
        for bad in ({"ACC_DESC": "", "ACC_AMOUNT": "5"}, {"ACC_DESC": "x", "ACC_AMOUNT": "0"}, {"ACC_DESC": "x", "ACC_AMOUNT": "5", "ACC_DATE": "13/13"}):
            self.post("/finance/account", bad, "/finance/account")
        self.assertEqual(len(self.db("AOITS_ACCOUNT")), 1)
        order = self.new_order()
        for status in ("I", "F", "C"):
            self.set_status(order["ORD_ID"], status)
        page = self.text("/finance/account")
        self.assertIn("-1,250.50", page)
        self.assertIn("+12.00", page)
        self.assertIn('href="/service/milling/%s"' % order["ORD_ID"], page)               # link to the milling order
        self.assertIn("-1,238.50", page)                                                   # net balance
        auto = next(e for e in self.db("AOITS_ACCOUNT") if e["AUTO_FLG"])
        self.post("/finance/account", {"action": "delete", "ACC_ID": auto["ACC_ID"]}, "/finance/account")
        self.assertEqual(len(self.db("AOITS_ACCOUNT")), 2)                                 # auto-posted entries stay
        self.post("/finance/account", {"action": "delete", "ACC_ID": entry["ACC_ID"]}, "/finance/account")
        self.assertEqual(len(self.db("AOITS_ACCOUNT")), 1)
        self.assertEqual([l["LOG_ACTION"] for l in self.db("AOILOG_ACCOUNT") if l["ACC_ID"] == entry["ACC_ID"]], ["C", "D"])
        self.assertIn("Expense", self.text("/finance/account?f=expense"))

    def test_marketing_changes_the_milling_discount(self):
        self.post("/finance/marketing", {"branch": "factory2", "PROMO_STATUS": "A", "DISCOUNT_PCT": "50", "VALID_FROM": "", "VALID_TO": ""}, "/finance/marketing")
        self.assertEqual(self.new_order(BRAN_ORDER="2")["EST_PAY_BRAN"], 12.0)            # 2 kg x 12.00 x 50%
        self.post("/finance/marketing", {"branch": "factory2", "DISCOUNT_PCT": "50"}, "/finance/marketing")   # switched off
        self.assertEqual(self.new_order(BRAN_ORDER="2")["EST_PAY_BRAN"], 24.0)
        self.assertIn("Off", self.text("/finance/marketing?branch=factory2"))
        for bad in ({"DISCOUNT_PCT": "150"}, {"DISCOUNT_PCT": "5", "VALID_FROM": "2026-10-10", "VALID_TO": "2026-10-01"}):
            self.post("/finance/marketing", dict(bad, branch="factory2", PROMO_STATUS="A"), "/finance/marketing")
            self.assertEqual(next(p for p in self.db("AOIMS_PROMOTION") if p["BRANCH_ID"] == "factory2")["PROMO_STATUS"], "I")

    def test_service_status_price_and_dashboard(self):
        a, b = self.new_order(), self.new_order(MILL_PRIORITY="U")
        self.set_status(a["ORD_ID"], "I")
        self.assertIn("/service/price#cs-status", self.client.get("/service/status").headers["Location"])
        tv = self.text("/service/price")                                   # Current Services: prices + the queue
        self.assertIn('id="cs-status"', tv)
        self.assertIn('data-auto-refresh="30"', tv)
        self.assertLess(tv.index("In process"), tv.index("Queue"))
        self.assertIn("#" + b["QR_ID"], tv)
        self.assertIn("20.41", self.text("/service/price"))
        dash = self.text("/admin/milling/analysis?tab=today")
        self.assertIn('href="/service/milling/?status=I"', dash)
        self.assertIn('id="chart-svc-status"', dash)
        trade = self.text("/admin/milling/analysis?tab=trade")
        self.assertIn("Total Sell Today", trade)

    def test_maintenance_branch_rules(self):
        self.logout()
        self.login("somchai.k")                                       # operator: no financial screens
        for path in ("/finance/pricing", "/finance/margin", "/finance/account", "/finance/marketing", "/admin/milling/analysis?tab=today"):
            self.assertEqual(self.client.get(path).status_code, 403, path)
        self.assertEqual(self.client.get("/service/price").status_code, 200)


class Update9(Base):
    """Automation: Hardware Control, Machine Usage Statistics, Electricity, AI, and the device API."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def machine(self, machine_id="F2-SORT"):
        return next(m for m in self.db("AOIMS_MACHINE") if m["MACHINE_ID"] == machine_id)

    def issue_key(self, path, field, ident):
        self.post(path, {"action": "key", field: ident}, path)
        page = self.text(path + "?setup=1")
        key = re.search(r'value="([A-Za-z0-9_-]{30,})" id="device-key"', page).group(1)
        self.assertNotIn(key, self.text(path + "?setup=1"))                     # shown once only
        return key

    def api(self, method, path, key, body=None):
        return getattr(self.client, method)(path, json=body, headers={"X-Device-Key": key})

    def shift(self, name, flt, field, delta):
        with self.app.app_context():
            for doc in col(name).find(flt):
                col(name).update_one({"_id": doc["_id"]}, {"$set": {field: doc[field] - delta}})

    def test_menu_group_and_screens(self):
        home = self.text("/?g=AT")
        for path in ("/automation/ai", "/automation/hardware", "/automation/electricity", "/automation/usage"):
            self.assertIn('href="%s"' % path, home)
            page = self.text(path)
            self.assertNotRegex(page, r">\s*(mc|el|mu|ai|auto|hw)[A-Z]\w+\s*<", path)   # no raw text keys
        self.assertIn("Husker", self.text("/automation/hardware"))

    def test_machine_setup_and_manual_status(self):
        self.post("/automation/hardware", {"action": "save", "mode": "new", "MACHINE_ID": "f2-dry", "MACHINE_NAME": "Dryer 1",
                                           "MACHINE_TYPE": "DRYER", "RATED_KW": "11", "CAPACITY_KGH": "800"}, "/automation/hardware")
        dryer = self.machine("F2-DRY")
        self.assertEqual((dryer["BRANCH_ID"], dryer["MACHINE_STATUS"], dryer["RATED_KW"], dryer["CONTROL_FLG"]), ("factory2", "O", 11.0, False))
        before = len(self.db("AOIMS_MACHINE"))
        for bad in ({"MACHINE_ID": "F2-DRY", "MACHINE_NAME": "Again"}, {"MACHINE_ID": "x", "MACHINE_NAME": "Short"},
                    {"MACHINE_ID": "F2-NEW", "MACHINE_NAME": ""}, {"MACHINE_ID": "F2-NEW", "MACHINE_NAME": "n", "RATED_KW": "-1"}):
            self.post("/automation/hardware", dict(bad, action="save", mode="new"), "/automation/hardware")
        self.assertEqual(len(self.db("AOIMS_MACHINE")), before)
        self.post("/automation/hardware", {"action": "status", "MACHINE_ID": "F2-DRY", "MACHINE_STATUS": "R", "NOTE": "morning"}, "/automation/hardware")
        self.assertEqual(self.machine("F2-DRY")["MACHINE_STATUS"], "R")
        log = self.db("AOILOG_MACHINE")[-1]
        self.assertEqual((log["OLD_STATUS"], log["NEW_STATUS"], log["SOURCE"], log["NOTE"]), ("O", "R", "USER", "morning"))
        self.assertEqual(self.post("/automation/hardware", {"action": "status", "MACHINE_ID": "F1-SORT", "MACHINE_STATUS": "R"}).status_code, 404)  # other branch

    def test_usage_hours_from_the_status_history(self):
        self.post("/automation/hardware", {"action": "status", "MACHINE_ID": "F2-SORT", "MACHINE_STATUS": "R"})
        self.shift("AOILOG_MACHINE", {"MACHINE_ID": "F2-SORT"}, "LOG_DATE", timedelta(hours=3))
        self.post("/automation/hardware", {"action": "status", "MACHINE_ID": "F2-SORT", "MACHINE_STATUS": "F"})
        self.shift("AOILOG_MACHINE", {"MACHINE_ID": "F2-SORT", "NEW_STATUS": "F"}, "LOG_DATE", timedelta(hours=1))
        from app.models.automation import Machine
        from app.timeutil import now_utc
        with self.app.app_context():                                    # the last 4 hours (not "today": the test may run after midnight)
            u = Machine.usage(self.machine(), now_utc() - timedelta(hours=4), now_utc())
        self.assertAlmostEqual(u["hours"]["R"], 2.0, delta=0.05)
        self.assertAlmostEqual(u["hours"]["F"], 1.0, delta=0.05)
        self.assertEqual(u["faults"], 1)
        self.assertAlmostEqual(u["energy_est"], 3.0, delta=0.2)                 # 2 h x 1.5 kW (the sorter)
        page = self.text("/automation/usage?days=7")
        self.assertIn('<td class="num">2.0</td>', page)
        self.assertIn('id="chart-run-machines"', page)

    def test_commands_need_a_connected_device_and_expire(self):
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-SORT", "CMD": "START"})
        self.assertEqual(self.db("AOITS_MACHINE_CMD"), [])                       # no remote control yet
        self.post("/automation/hardware", {"action": "save", "MACHINE_ID": "F2-SORT", "MACHINE_NAME": "Husker", "MACHINE_TYPE": "HUSKER",
                                           "RATED_KW": "15", "CONTROL_FLG": "1"})
        key = self.issue_key("/automation/hardware", "MACHINE_ID", "F2-SORT")
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-SORT", "CMD": "START"})
        self.assertEqual(self.db("AOITS_MACHINE_CMD"), [])                       # device never reported: offline
        self.assertEqual(self.api("get", "/api/v1/machines/F2-SORT/commands", "wrong").status_code, 401)
        self.assertEqual(self.api("post", "/api/v1/machines/F2-SORT/status", key, {"status": "I"}).get_json()["status"], "I")
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-SORT", "CMD": "START"})
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-SORT", "CMD": "STOP"})   # busy: refused
        cmds = self.db("AOITS_MACHINE_CMD")
        self.assertEqual([(c["CMD"], c["CMD_STATUS"]) for c in cmds], [("START", "P")])
        work = self.api("get", "/api/v1/machines/F2-SORT/commands", key).get_json()["commands"]
        self.assertEqual([w["command"] for w in work], ["START"])
        self.assertEqual(self.api("post", "/api/v1/commands/%s/ack" % work[0]["id"], "wrong", {"ok": True}).status_code, 401)
        self.assertEqual(self.api("post", "/api/v1/commands/%s/ack" % work[0]["id"], key, {"ok": True}).get_json()["status"], "D")
        self.assertEqual(self.machine()["MACHINE_STATUS"], "R")
        self.assertEqual(self.api("post", "/api/v1/commands/%s/ack" % work[0]["id"], key, {"ok": True}).status_code, 422)   # only once
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-SORT", "CMD": "STOP"})
        self.shift("AOITS_MACHINE_CMD", {"CMD": "STOP"}, "REQ_DATE", timedelta(minutes=5))
        self.assertEqual(self.api("get", "/api/v1/machines/F2-SORT/commands", key).get_json()["commands"], [])  # expired, never runs late
        self.assertEqual(next(c for c in self.db("AOITS_MACHINE_CMD") if c["CMD"] == "STOP")["CMD_STATUS"], "X")
        self.assertEqual(self.machine()["MACHINE_STATUS"], "R")
        old = key
        key = self.issue_key("/automation/hardware", "MACHINE_ID", "F2-SORT")
        self.assertEqual(self.api("post", "/api/v1/machines/F2-SORT/status", old, {"status": "I"}).status_code, 401)
        self.assertEqual(self.api("post", "/api/v1/machines/F2-SORT/status", key, {"status": "Z"}).status_code, 422)
        self.assertNotIn(key, str(self.machine()))                               # only the hash is stored

    def test_command_rights(self):
        self.logout()
        self.login("somchai.k", "factory1")                                       # operator: status yes, start/stop no
        self.client.get("/lang/en")
        self.assertEqual(self.client.get("/automation/hardware").status_code, 200)
        self.assertEqual(self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F1-SORT", "CMD": "START"}).status_code, 403)
        self.assertEqual(self.post("/automation/hardware", {"action": "key", "MACHINE_ID": "F1-SORT"}).status_code, 403)
        self.post("/automation/hardware", {"action": "status", "MACHINE_ID": "F1-SORT", "MACHINE_STATUS": "M"})
        self.assertEqual(self.machine("F1-SORT")["MACHINE_STATUS"], "M")
        self.assertEqual(self.client.get("/automation/ai").status_code, 403)
        self.logout()
        self.login("wanida.s")
        self.assertEqual(self.client.get("/automation/hardware").status_code, 403)
        self.assertEqual(self.client.get("/automation/electricity").status_code, 200)
        self.assertEqual(self.post("/automation/electricity", {"action": "reading", "METER_ID": "F2-MAIN", "READ_KWH": "5"}).status_code, 403)

    def test_meter_readings_cost_and_posting(self):
        add = lambda kwh, when="": self.post("/automation/electricity", {"action": "reading", "METER_ID": "F2-MAIN", "READ_KWH": kwh, "READ_DATE": when})
        add("1000")
        self.shift("AOITS_METER_READ", {}, "READ_DATE", timedelta(hours=2))
        add("1100")
        add("1050")                                                               # lower than the last: refused
        add("1200", "2099-01-01T00:00")                                          # future: refused
        reads = sorted(self.db("AOITS_METER_READ"), key=lambda r: r["READ_DATE"])
        self.assertEqual([(r["READ_KWH"], r["USED_KWH"]) for r in reads], [(1000.0, 0.0), (1100.0, 100.0)])
        with self.app.app_context():
            from app.models.automation import Meter
            from app.timeutil import now_utc
            meter = Meter.find("factory2", "F2-MAIN")
            with self.assertRaises(ValidationError) as caught:
                Meter.add_reading(meter, 1500, now_utc() - timedelta(hours=5), "U", "admin")
            self.assertEqual(caught.exception.key, "errMeterOrder")
        self.post("/automation/electricity", {"action": "rate", "SYSP_ELEC_RATE": "5"})
        self.post("/automation/electricity", {"action": "rate", "SYSP_ELEC_RATE": "500"})   # refused
        self.assertEqual(next(b for b in self.db("AOIMS_SYSPARAM") if b["BRANCH_ID"] == "factory2")["SYSP_ELEC_RATE"], 5.0)
        page = self.text("/automation/electricity?days=1")
        self.assertIn("500.00", page)                                             # 100 kWh x 5
        month = next(r for r in self.db("AOITS_METER_READ"))["READ_DATE"].astimezone(timezone(timedelta(hours=7))).strftime("%Y-%m")
        self.post("/automation/electricity", {"action": "post", "MONTH": month})
        self.post("/automation/electricity", {"action": "post", "MONTH": month})  # once only
        entries = [e for e in self.db("AOITS_ACCOUNT") if e["REF_ORD_ID"].startswith("EL-")]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["ACC_TYPE"], entries[0]["ACC_AMOUNT"], entries[0]["AUTO_FLG"]), ("EX", 500.0, False))
        self.assertIn("100.0 kWh", entries[0]["ACC_DESC"])
        self.post("/finance/account", {"action": "delete", "ACC_ID": entries[0]["ACC_ID"]}, "/finance/account")
        self.post("/automation/electricity", {"action": "post", "MONTH": month})  # posted again after the delete
        self.assertEqual(len([e for e in self.db("AOITS_ACCOUNT") if e["REF_ORD_ID"].startswith("EL-")]), 1)

    def test_smart_meter_api(self):
        key = self.issue_key("/automation/electricity", "METER_ID", "F2-MAIN")
        self.assertEqual(self.api("post", "/api/v1/meters/F2-MAIN/readings", "bad", {"kwh": 1}).status_code, 401)
        self.assertEqual(self.api("post", "/api/v1/meters/F2-MAIN/readings", key, {"kwh": 50, "time": "2026-01-01T08:00:00+07:00"}).status_code, 200)
        r = self.api("post", "/api/v1/meters/F2-MAIN/readings", key, {"kwh": 62.5})
        self.assertEqual(r.get_json()["used_kwh"], 12.5)
        self.assertEqual(self.api("post", "/api/v1/meters/F2-MAIN/readings", key, {"kwh": 10}).get_json()["error"], "errMeterLower")
        self.assertEqual(self.api("post", "/api/v1/meters/F2-MAIN/readings", key, {"kwh": 70, "time": "soon"}).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/meters/F2-MAIN/readings", data={"kwh": 1}).status_code, 401)  # no CSRF page, still a key

    def test_ai_forecast_advice_and_alerts(self):
        orders = [self.new_order(REGIST_PADDY="70") for _ in range(2)]
        self.shift("AOITS_ORDMILLING", {}, "REGIST_DATE", timedelta(days=7))      # same weekday last week
        page = self.text("/automation/ai")
        self.assertIn('id="chart-forecast"', page)
        with self.app.app_context():
            from app.models import ai
            fc = ai.forecast("factory2")
            self.assertEqual(fc[6]["kg"], 140.0)                                  # 7 days ahead = today's weekday
            self.assertEqual(fc[6]["orders"], 2.0)
            self.assertEqual(ai.queue_load("factory2")["kg"], 140.0)
            self.assertTrue(all(a["advice"] in ("keep", "raise_buy", "lower_buy", "manual") for a in ai.price_advice("factory2")))
            snap = ai.snapshot("factory2")
            self.assertNotIn("C261001001", str(snap))                             # no customer data is sent
        self.post("/automation/hardware", {"action": "status", "MACHINE_ID": "F2-SORT", "MACHINE_STATUS": "F"})
        page = self.text("/automation/ai")
        self.assertIn("is in fault now", page)
        self.assertIn("1 fault(s)", page)
        self.assertTrue(orders)

    def test_ask_needs_a_key_and_sends_totals(self):
        self.post("/automation/ai", {"QUESTION": "How much paddy is waiting?"}, "/automation/ai")
        self.assertIn("not set up", self.text("/automation/ai"))
        self.assertEqual(self.db("AOILOG_AI_ASK"), [])
        self.app.config["AI_API_KEY"] = "test-secret"
        sent = {}

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"content": [{"type": "text", "text": "140 kg is waiting."}]}).encode()

        def fake_urlopen(req, timeout=None):
            sent["body"] = json.loads(req.data.decode())
            sent["key"] = req.get_header("X-api-key")
            return Reply()
        self.new_order(REGIST_PADDY="140")
        with mock.patch("app.models.ai.urllib.request.urlopen", fake_urlopen):
            self.post("/automation/ai", {"QUESTION": "How much paddy is waiting?"}, "/automation/ai")
        self.assertEqual(sent["key"], "test-secret")
        self.assertIn("140", sent["body"]["messages"][0]["content"])
        self.assertNotIn("C261001001", sent["body"]["messages"][0]["content"])
        page = self.text("/automation/ai")
        self.assertIn("140 kg is waiting.", page)
        self.assertNotIn("test-secret", page)
        self.assertEqual(self.db("AOILOG_AI_ASK")[0]["USER_ID"], "admin")
        with mock.patch("app.models.ai.urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            self.post("/automation/ai", {"QUESTION": "again"}, "/automation/ai")
        self.assertIn("did not answer", self.text("/automation/ai"))


class Update10(Base):
    """Rice varieties, milling tests, market prices from the Thai Rice Millers Association, profit check."""
    SAMPLE_PDF = os.path.join(os.path.dirname(__file__), "sample_pricerice.pdf")
    PRICE_PAGE = ('<html><a href="images/introc_1/Pricerice05102569.pdf">old</a> '
                  '<a href="images/introc_1/Pricerice12102569.pdf">new</a></html>')

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    bs = Update4.bs
    trade = Update6.trade

    def price(self, mat, branch="factory2"):
        return next(p for p in self.db("AOIMS_PRICE") if p["MAT_CODE"] == mat and p["BRANCH_ID"] == branch)

    def yields(self, variety, branch="factory2"):
        return next(y for y in self.db("AOIMS_MILLYIELD") if y["BRANCH_ID"] == branch and y["RICE_VARIETY"] == variety)

    def fake_site(self):
        pdf = open(self.SAMPLE_PDF, "rb").read()
        calls = []

        def get(url, timeout=30):
            calls.append(url)
            return pdf if url.endswith(".pdf") else self.PRICE_PAGE.encode()
        return mock.patch("app.models.market.http_get", get), calls

    # ---------- varieties ----------
    def test_every_variety_has_its_own_skus_and_prices(self):
        codes = {m["MAT_CODE"] for m in self.db("AOIMS_MATERIAL")}
        self.assertEqual(codes, {"PADDY_HM", "RICE_HM", "BROKEN_HM", "PADDY", "RICE_W5", "BROKEN", "PADDY_GL", "RICE_GL",
                                 "BROKEN_GL", "BRAN", "HUSK"})
        self.assertEqual(len([p for p in self.db("AOIMS_PRICE") if p["BRANCH_ID"] == "factory2"]), 11)
        page = self.text("/service/milling/new?svc=buy")
        self.assertIn('name="QTY_PADDY_HM"', page)
        self.assertIn('<tr class="rate-group"><th colspan="4">Jasmine</th></tr>', page)
        self.assertNotIn('name="QTY_HUSK"', page)
        board = self.text("/service/price")
        self.assertIn("Rice (Glutinous)", board)
        body = self.trade("BY", "close", QTY_PADDY_HM="100", LOC_ID="03-001-001", INV_STATUS="A").get_json()
        self.assertTrue(body["ok"], body)
        self.assertEqual([(s["MAT_CODE"], s["INV_QTY"]) for s in self.db("AOINV_ONHAND")], [("PADDY_HM", 100.0)])

    def test_milling_order_of_a_variety_uses_its_yields_skus_and_prices(self):
        with self.app.app_context():
            col("AOIMS_MILLYIELD").update_one({"BRANCH_ID": "factory2", "RICE_VARIETY": "HM"}, {"$set": {"YIELD_RICE": 0.6, "YIELD_BROKE": 0.05}})
        order = self.new_order(RICE_VARIETY="HM", REGIST_PADDY="100", BROKE_ORDER="2")
        self.assertEqual((order["RICE_VARIETY"], order["EXPCT_RICE"], order["EXPCT_BROKE"]), ("HM", 60.0, 5.0))
        sell = self.price("BROKEN_HM")["PRICE_SELL"]
        self.assertEqual(order["EST_PAY_BROKE"], round(2 * sell * 0.9, 2))          # jasmine broken rice price, 10% off
        linked = self.bs()
        self.assertEqual([(o["MAT_CODE"], o["ORD_QTY"]) for o in linked], [("BROKEN_HM", 2.0)])
        self.assertEqual(self.new_order(RICE_VARIETY="xx")["RICE_VARIETY"], "WH")    # unknown -> white rice
        self.set_status(order["ORD_ID"], "I")
        self.set_status(order["ORD_ID"], "F")
        self.assertEqual([s["MAT_CODE"] for s in self.db("AOINV_ONHAND")], ["RICE_HM"])
        self.assertIn('<span class="badge variety-badge">Jasmine</span>', self.text("/service/milling/"))

    def test_changing_the_variety_moves_the_take_home_order_to_the_new_sku(self):
        order = self.new_order(BROKE_ORDER="1")
        self.assertEqual(self.bs()[0]["MAT_CODE"], "BROKEN")
        self.post("/service/milling/%s/details" % order["ORD_ID"], {"CUST_CODE": order["CUST_CODE"], "REGIST_PADDY": "33",
                                                                  "BRAN_ORDER": "0", "BROKE_ORDER": "1", "RICE_VARIETY": "GL"})
        self.assertEqual(self.order(order["ORD_ID"])["RICE_VARIETY"], "GL")
        linked = [o for o in self.bs() if o["ORD_STATUS"] != "CC"]
        self.assertEqual([o["MAT_CODE"] for o in linked], ["BROKEN_GL"])

    # ---------- milling tests ----------
    def test_seeded_tests_give_the_averaged_rates(self):
        with self.app.app_context():
            from app.models.mill_test import MillTest
            MillTest.apply("factory2", "admin")            # Base.setUp put the old numbers back; work them out again
        y = self.yields("WH")
        self.assertEqual((y["YIELD_RICE"], y["YIELD_BROKE"], y["YIELD_BRAN"], y["YIELD_HUSK"]), (0.5556, 0.0648, 0.1, 0.22))
        self.assertEqual(next(b for b in self.db("AOIMS_SYSPARAM") if b["BRANCH_ID"] == "factory2")["SYSP_MILLTIMEKG"], 0.294)
        page = self.text("/admin/milling/production?branch=factory2&v=GL")
        self.assertIn("Customer B", page)
        self.assertIn("for all varieties", page)

    def test_adding_and_deleting_tests_updates_the_rates(self):
        path = "/admin/milling/production"
        self.post(path, {"branch": "factory2", "v": "HM", "action": "test", "RICE_VARIETY": "HM", "PADDY_KG": "50",
                         "MILL_MIN": "15", "RICE_KG": "30", "BROKE_KG": "4", "HUSK_KG": "11", "TEST_REF": "Hom Mali lot 1"}, path)
        hm = self.yields("HM")
        self.assertEqual((hm["YIELD_RICE"], hm["YIELD_BROKE"], hm["YIELD_HUSK"]), (0.6, 0.08, 0.22))     # its own test only
        self.assertEqual(self.yields("WH")["YIELD_RICE"], 0.5556)                                    # all-variety tests
        minutes = next(b for b in self.db("AOIMS_SYSPARAM") if b["BRANCH_ID"] == "factory2")["SYSP_MILLTIMEKG"]
        self.assertEqual(minutes, round((9 + 10 + 12.75 + 15) / (33 + 36 + 39 + 50), 3))
        for bad in ({"PADDY_KG": "10", "RICE_KG": "8", "BROKE_KG": "3"}, {"PADDY_KG": "10", "RICE_KG": ""},
                    {"PADDY_KG": "10", "RICE_KG": "5", "TEST_DATE": "2099-01-01"}):
            self.post(path, dict(bad, branch="factory2", action="test"), path)
        self.assertEqual(len([t for t in self.db("AOITS_MILL_TEST") if t["BRANCH_ID"] == "factory2"]), 4)
        test = next(t for t in self.db("AOITS_MILL_TEST") if t["TEST_REF"] == "Hom Mali lot 1")
        self.post(path, {"branch": "factory2", "action": "test_delete", "TEST_ID": test["TEST_ID"]}, path)
        self.assertEqual(self.yields("HM")["YIELD_RICE"], 0.5556)                                    # back to all tests
        self.logout()
        self.login("somchai.k", "factory1")
        self.assertEqual(self.post(path, {"action": "test", "PADDY_KG": "1", "RICE_KG": "0.5"}, "/").status_code, 403)

    # ---------- market prices ----------
    def test_report_parsing(self):
        from app.models import market
        self.assertEqual(market.report_date("x/Pricerice14112568.pdf").date().isoformat(), "2025-11-14")
        links = market.find_reports(self.PRICE_PAGE, "http://www.thairicemillers.org/index.php?Ntype=19")
        self.assertEqual(links[0][0], "http://www.thairicemillers.org/images/introc_1/Pricerice12102569.pdf")
        self.assertEqual(market.parse_line("ข้าวหอมมะลิ 100% ชั้น 2(65/66) 3,250-3,350 3,300"),
                         ("ข้าวหอมมะลิ 100% ชั้น 2(65/66)", [(3250.0, 3350.0), (3300.0, 3300.0)]))
        rows = market.parse_text("ราคาประจำวันที่ 12 ตุลาคม 2569\nข้าวเปลือกเจ้า\n8,600-8,900 8,700\nข้าวขาว 5% 1,560-1,60015,800")
        self.assertEqual([(r["label"], r["values"]) for r in rows],
                         [("ข้าวเปลือกเจ้า", [[8600.0, 8900.0], [8700.0, 8700.0]]), ("ข้าวขาว 5%", [[1560.0, 1600.0], [15800.0, 15800.0]])])
        self.assertEqual(market.normalize("ข้าวเปลือก"), market.normalize("ขาวเปลอก"))        # tone marks lost in PDF text
        with self.app.app_context():
            rows = market.parse_text(market.pdf_text(open(self.SAMPLE_PDF, "rb").read()))
        self.assertIn("ข้าวเปลือกหอมมะลิ", [r["label"] for r in rows])
        self.assertEqual(market.unit_factor("auto", 14900), 0.001)
        self.assertEqual(market.unit_factor("auto", 3300), 0.01)

    def test_fetch_applies_market_prices_and_waits_for_approval(self):
        patch, calls = self.fake_site()
        with patch:
            self.post("/finance/pricing", {"branch": "factory2", "action": "fetch"}, "/finance/pricing")
        self.assertTrue(calls[0].startswith("http://www.thairicemillers.org/"))
        self.assertTrue(calls[1].endswith("Pricerice12102569.pdf"))                   # the newest report
        report = self.db("AOITS_MKREPORT")[-1]
        self.assertEqual((report["REPORT_STATUS"], report["REPORT_DATE"].date().isoformat()), ("OK", "2026-10-12"))
        gl = self.price("PADDY_GL")
        self.assertEqual((gl["MKPRICE_BUY"], gl["MKPRICE_SELL"], gl["PRICE_STATUS"], gl["MKPRICE_SRC"]),
                         (11.75, 11.75, "W", report["REPORT_ID"]))                       # 11,500-12,000 per ton
        self.assertEqual(self.price("RICE_HM")["MKPRICE_SELL"], 33.0)                  # 3,250-3,350 per 100 kg
        self.assertEqual(self.price("PADDY")["MKPRICE_BUY"], 17)                        # 8.75 is 49% off: not applied
        self.assertEqual(report["APPLIED"]["PADDY"], "jump")
        self.assertEqual(self.price("PADDY_GL", "factory1")["MKPRICE_BUY"], 11.75)      # every branch
        self.assertIn("Report 12 OCT 2026", self.text("/finance/pricing?branch=factory2"))
        with patch:
            self.post("/finance/market", {"action": "fetch"}, "/finance/market")
            with self.app.app_context():
                from app.models.market import MarketReport
                self.assertTrue(MarketReport.fetch("admin")["ALREADY"])               # the daily fetch skips a known report
        self.assertEqual(len([r for r in self.db("AOITS_MKREPORT") if r["REPORT_STATUS"] == "OK"]), 1)   # not imported twice

    def test_mapping_upload_and_download_errors(self):
        page = self.text("/finance/market")
        self.assertIn("No report imported yet", page)
        data = {"csrf_token": self.token("/finance/market"), "action": "upload",
                "REPORT_PDF": (io.BytesIO(open(self.SAMPLE_PDF, "rb").read()), "Pricerice12102569.pdf")}
        self.client.post("/finance/market", data=data, content_type="multipart/form-data")
        page = self.text("/finance/market")
        self.assertIn("33.00 THB/kg", page)
        self.assertIn("Moved over 30%: check", page)
        self.post("/finance/market", {"action": "map", "ROW__PADDY": "", "COL__PADDY": "0", "PICK__PADDY": "avg", "UNIT__PADDY": "auto",
                                      "ROW__RICE_W5": "ข้าวขาว 5%", "COL__RICE_W5": "1", "PICK__RICE_W5": "avg", "UNIT__RICE_W5": "100kg"}, "/finance/market")
        maps = {m["MAT_CODE"]: m for m in self.db("AOIMS_MKMAP")}
        self.assertFalse(maps["PADDY"]["ACTIVE_FLG"])
        self.assertEqual(maps["RICE_W5"]["COL_INDEX"], 1)
        self.post("/finance/market", {"action": "apply"}, "/finance/market")
        self.assertEqual(self.price("RICE_W5")["MKPRICE_SELL"], 15.8)                  # second number: 1,580 per 100 kg
        bad = {"csrf_token": self.token("/finance/market"), "action": "upload", "REPORT_PDF": (io.BytesIO(b"hello"), "x.pdf")}
        self.client.post("/finance/market", data=bad, content_type="multipart/form-data")
        self.assertIn("not a readable PDF", self.text("/finance/market"))
        with mock.patch("app.models.market.http_get", side_effect=urllib.error.URLError("down")):
            self.post("/finance/market", {"action": "fetch"}, "/finance/market")
        self.assertIn("could not be reached", self.text("/finance/market"))
        self.logout()
        self.login("somchai.k", "factory1")
        self.assertEqual(self.client.get("/finance/market").status_code, 403)

    def test_daily_fetch_runs_once_a_day(self):
        from app.models import market
        with self.app.app_context(), mock.patch("app.models.market.now_local") as now:
            from datetime import datetime as dt
            now.return_value = dt(2026, 10, 12, 8, 0)
            self.assertFalse(market.daily_due())                                       # before AOI_MARKET_HOUR
            now.return_value = dt(2026, 10, 12, 9, 5)
            self.assertTrue(market.daily_due())
            self.assertFalse(market.daily_due())                                       # another worker, same day
            now.return_value = dt(2026, 10, 13, 9, 5)
            self.assertTrue(market.daily_due())

    # ---------- profit check ----------
    def test_profit_check_and_recommended_buying_price(self):
        with self.app.app_context():
            col("AOIMS_MILLYIELD").update_one({"BRANCH_ID": "factory2", "RICE_VARIETY": "WH"},
                                              {"$set": {"YIELD_RICE": 0.6, "YIELD_BROKE": 0.05, "YIELD_BRAN": 0.1, "YIELD_HUSK": 0.2}})
            for mat, sell in (("RICE_W5", 20.0), ("BROKEN", 12.0), ("BRAN", 10.0), ("HUSK", 1.0)):
                col("AOIMS_PRICE").update_one({"BRANCH_ID": "factory2", "MAT_CODE": mat}, {"$set": {"PRICE_SELL": sell, "PRICE_STATUS": "A"}})
            col("AOIMS_PRICE").update_one({"BRANCH_ID": "factory2", "MAT_CODE": "PADDY"},
                                          {"$set": {"MKPRICE_BUY": 15.0, "MG_BUY_TARGET": 10.0, "MG_BUY_MIN": None, "MG_BUY_MAX": None,
                                                    "PRICE_STATUS": "W", "PRICE_BUY": 14.0}})
            col("AOIMS_SYSPARAM").update_one({"BRANCH_ID": "factory2"}, {"$set": {"SYSP_PROCCOSTKG": 0.5, "SYSP_ELEC_RATE": 0, "SYSP_LABORDAY": 0, "SYSP_ELECMONTH": 0}})
            from app.models import profit
            e = profit.evaluate("factory2", "WH")
        # value = 0.6 x 20 + 0.05 x 12 + 0.1 x 10 + 0.2 x 1 = 13.80; cost 0.50; break-even 13.30
        self.assertEqual((e["value"], e["cost"], e["break_even"]), (13.8, 0.5, 13.3))
        self.assertEqual(e["market_sug"], 13.5)                                         # 15.00 - 10%
        self.assertEqual(e["highest"], 11.97)                                           # 13.30 - 10%
        self.assertEqual((e["recommended"], e["limited"], e["profit"]), (11.97, True, 1.33))
        self.assertEqual(e["rice_break_even"], round((11.97 + 0.5 - 1.8) / 0.6, 2))
        page = self.text("/finance/pricing?branch=factory2&v=WH")
        self.assertIn('id="profit-table"', page)
        self.assertIn('id="buy-PADDY" name="PRICE_BUY" value="11.97"', page)           # the approval starts from it
        self.assertIn("Suggested below the market suggestion (13.50)", page)
        self.assertNotIn('data-mat="PADDY_HM"', page)                                   # variety filter
        self.assertEqual(self.post("/admin/milling/maintenance", {"branch": "factory2", "SYSP_PROCCOSTKG": "-1"}).status_code, 200)


class Update12(Base):
    """Labour cost in the profit check, and the NABC production index as a supply indicator on AI."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def branch(self, branch_id="factory2"):
        return next(b for b in self.db("AOIMS_SYSPARAM") if b["BRANCH_ID"] == branch_id)

    @staticmethod
    def nabc(months=30, newest=(2569, 8), boost=20, per_page=20):
        """Pages like agriapi.nabc.go.th: newest month first; the newest 12 months are `boost` higher."""
        recs = []
        y, m = newest
        for k in range(months):
            recs.append({"year_th": str(y), "month": "%02d" % m, "product_sector": "ภาคเกษตรกรรม", "product_category": "ภาคเกษตรกรรม",
                         "product_group": "ภาคเกษตรกรรม", "commod": "ภาคเกษตรกรรม", "product_name": "ภาคเกษตรกรรม",
                         "production_index": 100.0 + (boost if k < 12 else 0) + k % 3, "data_date": "2026-09-06T17:00:00.000Z"})
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        pages = [recs[i:i + per_page] for i in range(0, len(recs), per_page)]
        calls = []

        def get(url):
            calls.append(url)
            n = int(re.search(r"page=(\d+)", url).group(1))
            return {"success": True, "data": pages[n - 1],
                    "pagination": {"total": months, "limit": per_page, "page": n, "totalPages": len(pages), "currentPage": n}}
        return get, calls

    # ---------- labour ----------
    def test_labour_cost_is_set_on_milling_maintenance(self):
        self.assertEqual((self.branch()["SYSP_LABORDAY"], self.branch()["SYSP_WORKERS"]), (600.0, 2))
        data = {"branch": "factory2", "SYSP_MILLTIMEKG": "0.273", "SYSP_MILLORDGAP": "1", "SYSP_OPETIMESTART": "09:00",
                "SYSP_OPETIMEEND": "18:00", "SYSP_QUEUEPERIOD": "10:00", "SYSP_DELIVERTIMEADD": "2", "SYSP_MILLFEEUNITKG": "50",
                "SYSP_MILLFEEPERUNIT": "12", "SYSP_DELIVERYFEEKM": "0", "SYSP_LABORDAY": "650", "SYSP_WORKERS": "2",
                "SYSP_PLANKGDAY": "2500"}
        self.assertEqual(self.post("/admin/milling/maintenance", data).status_code, 302)
        self.assertEqual((self.branch()["SYSP_LABORDAY"], self.branch()["SYSP_WORKERS"], self.branch()["SYSP_PLANKGDAY"]), (650.0, 2, 2500.0))
        for bad in ({"SYSP_LABORDAY": "-1"}, {"SYSP_WORKERS": "1.5"}, {"SYSP_WORKERS": "x"}, {"SYSP_PLANKGDAY": "-5"}):
            self.assertEqual(self.post("/admin/milling/maintenance", dict(data, **bad)).status_code, 200, bad)
        self.assertIn('name="SYSP_LABORDAY" value="650"', self.text("/admin/milling/maintenance?branch=factory2"))

    def test_labour_per_kg_follows_the_daily_volume(self):
        from app.models import profit
        with self.app.app_context():
            work = profit.labour("factory2")
        self.assertEqual((work["source"], work["kg_day"], work["planned"]), ("planned", 2000.0, 2000.0))   # 2 tons a day
        self.assertEqual(round(work["per_kg"], 2), 0.6)                  # 600 x 2 workers / 2,000 kg
        with self.app.app_context():
            col("AOIMS_SYSPARAM").update_many({}, {"$set": {"SYSP_PLANKGDAY": 0}})                    # 0: real volume
            work = profit.labour("factory2")
        capacity = 9 * 60 / 0.273                                       # 09:00-18:00 at 0.273 min/kg
        self.assertEqual((work["source"], work["kg_day"]), ("capacity", round(capacity, 1)))
        self.assertAlmostEqual(work["per_kg"], 1200 / capacity, places=4)
        self.new_order(REGIST_PADDY="200")
        self.new_order(REGIST_PADDY="100")
        with self.app.app_context():
            work = profit.labour("factory2")
            e = profit.evaluate("factory2", "WH")
        self.assertEqual((work["source"], work["kg_day"], work["days"]), ("orders", 300.0, 1))
        self.assertEqual(e["labour_kg"], 4.0)                           # 600 THB x 2 workers / 300 kg
        self.assertEqual(e["cost"], round(e["energy_cost"] + 4.0 + e["other"], 2))
        page = self.text("/finance/pricing?branch=factory2")
        self.assertIn("1,200 THB/day ÷ 300 kg/day", page)
        self.assertIn('id="pf-labour-note"', page)
        with self.app.app_context():
            col("AOIMS_SYSPARAM").update_many({}, {"$set": {"SYSP_PLANKGDAY": 2000.0}})
            self.assertEqual(profit.labour("factory2")["per_kg_actual"], 4.0)
        self.assertIn("Real average so far: 300 kg per day (1 day(s) with orders in 30 days), which would make labour 4.00 THB/kg.",
                      self.text("/finance/pricing?branch=factory2"))

    def test_electricity_from_the_monthly_bill(self):
        from app.models import profit
        with self.app.app_context():
            e = profit.evaluate("factory2", "WH")
        self.assertEqual((e["energy_source"], e["energy_cost"]), ("bill", round(9000 / (2000 * 26), 2)))   # 0.17 THB/kg
        self.assertEqual(e["labour_kg"], 0.6)
        page = self.text("/finance/pricing?branch=factory2")
        self.assertIn("(monthly bill)", page)
        self.post("/automation/electricity", {"action": "reading", "METER_ID": "F2-MAIN", "READ_KWH": "1000"})
        self.shift_reading = None
        with self.app.app_context():
            for r in col("AOITS_METER_READ").find():
                col("AOITS_METER_READ").update_one({"_id": r["_id"]}, {"$set": {"READ_DATE": r["READ_DATE"] - timedelta(hours=3)}})
        self.post("/automation/electricity", {"action": "reading", "METER_ID": "F2-MAIN", "READ_KWH": "1010"})
        self.new_order(REGIST_PADDY="100")
        with self.app.app_context():
            e = profit.evaluate("factory2", "WH")
        self.assertEqual((e["energy_source"], e["kwh_kg"]), ("meter", 0.1))               # meter readings come first
        self.assertEqual(self.post("/admin/milling/maintenance", {"branch": "factory2", "SYSP_WORKDAYS": "40"}).status_code, 200)

    def test_two_workers_are_set_once(self):
        self.assertEqual({b["SYSP_WORKERS"] for b in self.db("AOIMS_SYSPARAM")}, {2})
        with self.app.app_context():
            col("AOIMS_SYSPARAM").update_many({}, {"$set": {"SYSP_WORKERS": 3}})              # changed on the screen later
            from scripts.seed import seed
            seed(log=lambda _m: None)
        self.assertEqual({b["SYSP_WORKERS"] for b in self.db("AOIMS_SYSPARAM")}, {3})        # kept

    def test_older_databases_get_the_labour_cost(self):
        with self.app.app_context():
            col("AOIMS_SYSPARAM").update_many({}, {"$unset": {"SYSP_LABORDAY": "", "SYSP_WORKERS": "", "SYSP_PLANKGDAY": ""}})
            from scripts.seed import seed
            seed(log=lambda _m: None)
        self.assertEqual((self.branch("factory1")["SYSP_LABORDAY"], self.branch("factory1")["SYSP_WORKERS"],
                          self.branch("factory1")["SYSP_PLANKGDAY"]), (600.0, 2, 2000.0))

    # ---------- supply indicator ----------
    def test_production_index_is_fetched_page_by_page(self):
        from app.models import supply
        self.assertEqual(supply.parse_record({"year_th": "2569", "month": "08", "product_name": "ภาคเกษตรกรรม",
                                              "production_index": 151.386303454559})["PERIOD"], "2026-08")
        self.assertIsNone(supply.parse_record({"year_th": "2569", "month": "13", "production_index": 1}))
        get, calls = self.nabc()
        with mock.patch("app.models.supply._get_json", get):
            self.post("/automation/ai", {"action": "supply"}, "/automation/ai")
            self.assertEqual(len(calls), 2)                              # 30 months, 20 per page
            self.assertTrue(calls[0].startswith("https://agriapi.nabc.go.th/api/production-index-month/sector?"))
            self.post("/automation/ai", {"action": "supply"}, "/automation/ai")
        self.assertEqual(len(self.db("AOITS_NABC_INDEX")), 30)           # nothing doubled
        with self.app.app_context():
            sp = supply.summary()
        self.assertEqual((sp["period"], sp["signal"]), ("2026-08", "up"))
        self.assertEqual(sp["yoy"], round((120 - 100) / 100 * 100, 1))  # Aug 2569 (120) vs Aug 2568 (100)
        page = self.text("/automation/ai")
        self.assertIn('id="chart-supply"', page)
        self.assertIn("more supply, paddy prices may soften", page)
        self.assertIn("Agricultural production index 08/2569: +20.0% vs last year", page)   # alert
        with self.app.app_context():
            from app.models import ai
            self.assertEqual(ai.snapshot("factory2")["supply_indicator_nabc_agri_production_index"]["signal"], "up")

    def test_supply_errors_and_rights(self):
        with mock.patch("app.models.supply._get_json", side_effect=urllib.error.URLError("down")):
            self.post("/automation/ai", {"action": "supply"}, "/automation/ai")
        self.assertIn("could not be reached", self.text("/automation/ai"))
        with mock.patch("app.models.supply._get_json", lambda url: {"success": False}):
            self.post("/automation/ai", {"action": "supply"}, "/automation/ai")
        self.assertIn("unexpected format", self.text("/automation/ai"))
        self.assertIn("No index data yet", self.text("/automation/ai"))
        self.logout()
        self.login("wanida.s")                                          # accountant: AI view only
        self.assertEqual(self.post("/automation/ai", {"action": "supply"}).status_code, 403)


class Update13(Base):
    """Market prices from the NABC daily farm prices (agriapi.nabc.go.th/api/daily-prices/product)."""
    HM = "ข้าวเปลือกเจ้าหอมมะลิ 105"

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    price = Update10.price

    @staticmethod
    def site(days, per_day=4, per_page=6, products=None):
        """Fake NABC: `days` newest first, `per_day` markets each; day_price per ton. Returns (get, calls)."""
        products = products or {Update13.HM: 18000}
        calls = []

        def records(product):
            base = products[product]
            out = []
            for d in days:
                for k in range(per_day):
                    out.append({"data_date": d, "day": int(d[8:]), "month": int(d[5:7]), "year_th": int(d[:4]) + 543,
                                "product_category": "ข้าวหอมมะลิ", "product_name": product, "market_name": "โรงสี %d" % k,
                                "province": ["บุรีรัมย์", "ร้อยเอ็ด", "ขอนแก่น", "ศรีสะเกษ"][k % 4],
                                "day_price": base + 500 * k, "unit": "บาท/ตัน"})
            return out

        def get(product, page):
            calls.append((product, page))
            if product not in products:
                return {"success": True, "data": [], "pagination": {"limit": per_page, "page": page, "count": 0, "total": 0}}
            recs = records(product)
            chunk = recs[(page - 1) * per_page: page * per_page]
            return {"success": True, "data": chunk, "pagination": {"limit": per_page, "offset": (page - 1) * per_page,
                                                                   "page": page, "count": len(chunk), "total": len(recs)}}
        return get, calls

    def test_daily_check_reads_page_one_first(self):
        from app.models import nabc_price
        get, calls = self.site(["2026-09-30", "2026-09-29", "2026-09-26"])
        with self.app.app_context(), mock.patch("app.models.nabc_price._get", get):
            first = nabc_price.sync(self.HM)
            self.assertEqual((first["new"], first["latest"]), (12, "2026-09-30"))
            self.assertEqual(len(calls), 2)                                  # 12 records, 6 per page
            calls.clear()
            self.assertEqual(nabc_price.sync(self.HM)["new"], 0)
            self.assertEqual(calls, [(self.HM, 1)])                           # nothing new: page 1 only
        get, calls = self.site(["2026-10-01", "2026-09-30", "2026-09-29", "2026-09-26"])
        with self.app.app_context(), mock.patch("app.models.nabc_price._get", get):
            again = nabc_price.sync(self.HM)
            self.assertEqual((again["new"], again["latest"]), (4, "2026-10-01"))
            self.assertEqual(calls, [(self.HM, 1), (self.HM, 2)])           # stopped at the stored days
            found = nabc_price.market_price(self.HM)
            self.assertEqual((found["date"], found["n"], found["kg"], found["low"], found["high"]), ("2026-10-01", 4, 18.75, 18.0, 19.5))
            self.assertEqual(nabc_price.market_price(self.HM, ["ขอนแก่น"])["kg"], 19.0)
            self.assertEqual(nabc_price.market_price(self.HM, stat="high")["kg"], 19.5)
            self.assertEqual(len(nabc_price.trend(self.HM)), 4)
        self.assertEqual(len(self.db("AOITS_NABC_PRICE")), 16)

    def test_fetch_now_applies_the_nabc_price_and_waits_for_approval(self):
        get, calls = self.site(["2026-09-30", "2026-09-29"])
        with mock.patch("app.models.nabc_price._get", get):
            self.post("/finance/pricing", {"branch": "factory2", "action": "fetch"}, "/finance/pricing")
        hm = self.price("PADDY_HM")
        self.assertEqual((hm["MKPRICE_BUY"], hm["MKPRICE_SELL"], hm["MKPRICE_SRC"], hm["PRICE_STATUS"]), (18.75, 18.75, "NABC:2026-09-30", "W"))
        self.assertEqual(self.price("PADDY_HM", "factory1")["MKPRICE_BUY"], 18.75)    # every branch
        page = self.text("/finance/pricing?branch=factory2&v=HM")
        self.assertIn("Market price from the NABC daily prices of 30 SEP 2026", page)
        market = self.text("/finance/market")
        self.assertIn('id="nabc-prices"', market)
        self.assertIn("18.75 THB/kg from 4 market(s) on 2026-09-30", market)
        self.assertIn("could not be reached", market)                        # the association site is not faked here

    def test_mapping_chooses_the_source(self):
        maps = {m["MAT_CODE"]: m for m in self.db("AOIMS_MKMAP")}
        self.assertEqual((maps["PADDY_HM"]["SOURCE"], maps["PADDY_HM"]["NABC_PRODUCT"]), ("NABC", self.HM))
        form = {"action": "map", "SRC__PADDY_GL": "NABC", "ROW__PADDY_GL": "", "NP__PADDY_GL": "ข้าวเปลือกเหนียว ก.6",
                "NPV__PADDY_GL": "ขอนแก่น, ร้อยเอ็ด", "NPS__PADDY_GL": "avg"}
        self.post("/finance/market", form, "/finance/market")
        gl = next(m for m in self.db("AOIMS_MKMAP") if m["MAT_CODE"] == "PADDY_GL")
        self.assertEqual((gl["SOURCE"], gl["NABC_PROVINCES"], gl["NABC_STAT"], gl["ACTIVE_FLG"]), ("NABC", "ขอนแก่น, ร้อยเอ็ด", "avg", False))
        self.post("/finance/market", {"action": "map", "SRC__RICE_HM": "NABC", "ROW__RICE_HM": "x", "NP__RICE_HM": ""}, "/finance/market")
        self.assertIn("type the NABC product name", self.text("/finance/market"))
        get, _calls = self.site(["2026-09-30"])                              # the glutinous name is unknown to NABC
        with mock.patch("app.models.nabc_price._get", get):
            self.post("/finance/market", {"action": "fetch"}, "/finance/market")
        self.assertIn("NABC has no prices for", self.text("/finance/market"))
        self.assertEqual(self.price("PADDY_HM")["MKPRICE_SRC"], "NABC:2026-09-30")   # the other product still applied

    def test_older_mapping_moves_jasmine_paddy_to_nabc(self):
        with self.app.app_context():
            col("AOIMS_MKMAP").update_one({"MAT_CODE": "PADDY_HM"}, {"$unset": {"SOURCE": "", "NABC_PRODUCT": ""}, "$set": {"ACTIVE_FLG": True}})
            from scripts.seed import seed
            seed(log=lambda _m: None)
        hm = next(m for m in self.db("AOIMS_MKMAP") if m["MAT_CODE"] == "PADDY_HM")
        self.assertEqual((hm["SOURCE"], hm["NABC_PRODUCT"]), ("NABC", self.HM))


class Update16(Base):
    """TV display per factory: /tv/<branch>?key=..., no sign-in, its own key per branch."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def issue(self, branch="factory2"):
        self.post("/admin/milling/maintenance", {"branch": branch, "action": "tvkey"}, "/admin/milling/maintenance?branch=" + branch)
        page = self.text("/admin/milling/maintenance?branch=" + branch)
        return re.search(r'id="tv-link" class="mono" readonly value="http://localhost([^"]+)"', page).group(1)

    def test_each_factory_has_its_own_link(self):
        self.assertIn("No link yet", self.text("/admin/milling/maintenance?branch=factory2"))
        link2, link1 = self.issue("factory2"), self.issue("factory1")
        tv = self.app.test_client()                                    # the TV: not signed in
        self.assertEqual(tv.get(link2).status_code, 200)
        self.assertEqual(tv.get(link2.replace("factory2", "factory1")).status_code, 403)     # the key is per factory
        self.assertEqual(tv.get("/tv/factory2").status_code, 403)
        self.assertEqual(tv.get("/tv/factory2/data?key=wrong").status_code, 403)
        body = tv.get(link2.replace("/tv/factory2?", "/tv/factory2/data?")).get_json()
        self.assertEqual((body["branch"], body["branch_name"]), ("factory2", "โรงงาน 2"))
        self.assertEqual((body["motor"]["status"], body["motor"]["running"]), ("O", False))    # update 36: main motor
        page = tv.get(link2).get_data(as_text=True)
        self.assertIn('id="tvb-motor"', page)
        self.assertIn("tvb-circuit", page)
        new2 = self.issue("factory2")
        self.assertNotEqual(new2, link2)
        self.assertEqual(tv.get(link2).status_code, 403)                # the old link stops at once
        self.assertEqual(tv.get(new2).status_code, 200)
        self.assertEqual(tv.get(link1).status_code, 200)                # the other factory is not touched
        self.logout()
        self.login("somchai.k", "factory1")                             # operator: Milling Maintenance not allowed
        self.assertEqual(self.post("/admin/milling/maintenance", {"branch": "factory1", "action": "tvkey"}).status_code, 403)

    def test_board_shows_ready_milling_and_queue(self):
        from app.models import tv as tvm
        self.assertEqual(tvm.short_name("สมชาย ใจดี"), "สมชาย จ.")
        self.assertEqual(tvm.short_name("Somchai"), "Somchai")
        a, b, c, d = (self.new_order(REGIST_PADDY=str(kg), RICE_VARIETY=v) for kg, v in ((100, "HM"), (60, "WH"), (200, "GL"), (40, "WH")))
        for order in (a, b):
            self.set_status(order["ORD_ID"], "I")
            self.set_status(order["ORD_ID"], "F")
        self.set_status(c["ORD_ID"], "I")
        with self.app.app_context():
            board = tvm.board("factory2")
            other = tvm.board("factory1")
        self.assertEqual([r["no"] for r in board["ready"]], [b["QR_ID"], a["QR_ID"]])    # newest finished first
        self.assertTrue(board["ready"][0]["new"])
        self.assertEqual(board["ready"][1]["variety"], "HM")
        self.assertEqual([r["no"] for r in board["milling"]], [c["QR_ID"]])
        self.assertTrue(1 <= board["milling"][0]["pct"] <= 99)
        self.assertEqual([r["no"] for r in board["queue"]], [d["QR_ID"]])
        self.assertTrue(board["queue"][0]["receive"]["time"])
        self.assertEqual(tvm.short_name("มาลี แก้วใส"), "มาลี ก.")
        self.assertEqual((other["ready"], other["milling"], other["queue"]), ([], [], []))  # only its own factory
        full = next(x for x in self.db("AOIMS_CUSTOMER") if x["CUST_CODE"] == a["CUST_CODE"])["CUST_NAME"]
        self.assertEqual(board["ready"][0]["name"], tvm.short_name(full))
        page = self.app.test_client().get(self.issue("factory2")).get_data(as_text=True)
        self.assertNotIn(full, page)                                    # full names never reach the public screen
        self.assertIn('src="/static/js/tv.js"', page)
        self.assertTrue(any(p["code"] == "PADDY_HM" for p in board["prices"]))


class Update21(Base):
    """Default priority by variety, location digits only, menu names, LINE messages on the status flow."""
    USER = "U" + "0123456789abcdef" * 2

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def test_default_priority_follows_the_variety(self):
        for variety, priority in (("HM", "H"), ("WH", "M"), ("GL", "L")):
            self.assertEqual(self.new_order(RICE_VARIETY=variety, MILL_PRIORITY="")["MILL_PRIORITY"], priority)
        self.assertEqual(self.new_order(RICE_VARIETY="GL", MILL_PRIORITY="U")["MILL_PRIORITY"], "U")   # chosen one stays
        page = self.text("/service/milling/new")
        self.assertIn('value="HM" data-priority="H"', page)
        self.assertIn('value="GL" data-priority="L"', page)

    def test_location_digits_only(self):
        from app.models.inventory import Location
        self.assertEqual(Location.clean("00001001"), "00-001-001")
        self.assertEqual(Location.clean("01-002-005"), "01-002-005")
        self.assertEqual(Location.clean(" 01 002 005 "), "01-002-005")
        for bad in ("0100200", "010020051", "ab"):
            with self.assertRaises(ValidationError):
                Location.clean(bad)
        ord_id = self.new_order()["ORD_ID"]
        self.set_status(ord_id, "I")
        self.set_status(ord_id, "F", LOC_ID="02003001")
        self.assertEqual(self.order(ord_id)["REF_INV_LOC"], "02-003-001")
        self.assertIn("data-loc", self.text("/inventory/new"))

    def test_menu_names(self):
        home = self.text("/?g=SV")
        self.assertIn("Milling Order List", home)
        self.assertIn("Buying/Selling Order List", home)
        self.assertNotIn("Update Milling Status", home)
        self.client.get("/lang/th")
        self.assertIn("รายการคำสั่งสีข้าว", self.text("/?g=SV"))

    def notes(self, ord_id):
        return [n for n in self.db("AOITS_NOTIFY") if n["ORD_ID"] == ord_id]

    def test_line_message_when_a_take_away_order_is_finished(self):
        ord_id = self.new_order()["ORD_ID"]
        self.set_status(ord_id, "I")
        self.assertEqual(self.notes(ord_id), [])
        self.set_status(ord_id, "F")
        (note,) = self.notes(ord_id)
        self.assertEqual((note["NOTIFY_FLG"], note["ERROR"], note["ORD_TYPE"]), ("0", "lnNotConfigured", "ML"))
        self.assertIn("ข้าวของคุณสีเสร็จแล้ว", note["NOTIFY_MESSAGE"])
        page = self.text("/service/milling/")
        self.assertIn("LINE not sent", page)
        self.assertIn("/line-resend", page)
        with mock.patch.dict(os.environ, {"AOI_LINE_CHANNEL_TOKEN": "token"}):
            self.post("/service/milling/%s/line-resend" % ord_id)
            self.assertEqual(self.notes(ord_id)[0]["ERROR"], "lnNoUser")        # the customer is not linked yet
            self.post("/admin/customers/C261001001", {
                "CUST_NAME": "อรุณ พงษ์ไพร", "MOBILE_NO": "081-234-5678", "ADDRESS": "45 หมู่ 2", "LINE_USER_ID": "abc"},
                "/admin/customers/C261001001")
            self.assertNotEqual(next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == "C261001001").get("LINE_USER_ID"), "abc")
            with self.app.app_context():
                col("AOIMS_CUSTOMER").update_one({"CUST_CODE": "C261001001"}, {"$set": {"LINE_USER_ID": self.USER}})
            with mock.patch("app.models.notify._push", return_value=None) as push:
                self.post("/service/milling/%s/line-resend" % ord_id)
            push.assert_called_once()
            self.assertEqual(push.call_args[0][0], self.USER)
        note = self.notes(ord_id)[0]
        self.assertEqual((note["NOTIFY_FLG"], note["ERROR"]), ("1", None))
        self.assertIsNotNone(note["NOTIFY_DATETIME"])
        self.assertIn("LINE Sent", self.text("/service/milling/"))

    def test_line_message_for_delivery_waits_for_the_delivery_time(self):
        ord_id = self.new_order(ORDER_TYPE="D")["ORD_ID"]
        for status in ("I", "F", "D"):
            self.set_status(ord_id, status)
        (milled,) = self.notes(ord_id)                                       # update 33: "milled" at Finished
        self.assertEqual((milled["NOTIFY_KIND"], milled["ERROR"]), ("finished_delivery", "lnNotConfigured"))
        self.assertIn("แจ้งเวลาจัดส่ง", milled["NOTIFY_MESSAGE"])
        with self.app.app_context():
            col("AOIMS_CUSTOMER").update_one({"CUST_CODE": "C261001001"}, {"$set": {"LINE_USER_ID": self.USER}})
        with mock.patch.dict(os.environ, {"AOI_LINE_CHANNEL_TOKEN": "token"}), \
                mock.patch("app.models.notify._push", return_value="HTTP 400 Invalid reply token") as push:
            self.post("/service/milling/%s/delivery" % ord_id, {"DELIVERY_ADDRESS": "9 New Road", "DELIVERY_DISTANCE": "10",
                                                               "DELIVERY_ARRIVAL_TIME": "2026-10-09T15:30"})
        note = sorted(self.notes(ord_id), key=lambda n: n["NOTIFY_ID"])[-1]
        self.assertEqual(note["NOTIFY_FLG"], "9")
        self.assertIn("15:30", push.call_args[0][1])
        self.assertIn("จัดส่ง", note["NOTIFY_MESSAGE"])
        self.assertIn("LINE Failed", self.text("/service/milling/"))
        self.assertTrue(self.order(ord_id)["DELIVERY_CONFIRM_FLG"])         # the confirmation itself is saved

    def test_a_broken_line_never_stops_the_status_change(self):
        ord_id = self.new_order()["ORD_ID"]
        self.set_status(ord_id, "I")
        with mock.patch("app.models.notify.queue", side_effect=RuntimeError("down")):
            self.set_status(ord_id, "F")
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "F")


class Update22(Base):
    """QR label as the key (delivery too), scan + weigh to start milling, status timeline and Milling Analysis."""
    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def events(self, ord_id):
        return sorted((e for e in self.db("AOILOG_MILLSTATUS") if e["ORD_ID"] == ord_id), key=lambda e: e["EVENT_SEQ"])

    def test_delivery_orders_print_the_label_too(self):
        page = self.text("/service/milling/" + self.new_order(ORDER_TYPE="D")["ORD_ID"])
        self.assertIn('id="qr-label"', page)
        self.assertIn('id="print-label-btn"', page)

    def test_milling_starts_only_with_this_orders_qr(self):
        a, b = self.new_order(ORDER_TYPE="D"), self.new_order()
        self.assertIn('data-form="start-%s"' % a["ORD_ID"], self.text("/service/milling/"))
        self.set_status(a["ORD_ID"], "I", SCAN_QR="")
        self.assertEqual(self.order(a["ORD_ID"])["MILL_STATUS"], "Q")
        self.set_status(a["ORD_ID"], "I", SCAN_QR="AOI-" + b["ORD_ID"])            # the label of another bag
        self.assertEqual(self.order(a["ORD_ID"])["MILL_STATUS"], "Q")
        self.set_status(a["ORD_ID"], "I", SCAN_QR=" aoi-" + a["ORD_ID"].lower(), ACT_PADDY="31.5")
        saved = self.order(a["ORD_ID"])
        self.assertEqual((saved["MILL_STATUS"], saved["ACT_PADDY"]), ("I", 31.5))
        self.assertIsNotNone(saved["SCAN_DATE"])
        self.set_status(a["ORD_ID"], "F", INV_QTY="16.2")
        self.assertEqual(self.order(a["ORD_ID"])["ACT_RICE"], 16.2)

    def test_every_status_change_is_tracked(self):
        ord_id = self.new_order()["ORD_ID"]
        self.set_status(ord_id, "I")
        self.set_status(ord_id, "H", HOLD_REASON="Belt broken")
        self.set_status(ord_id, "I", ACT_PADDY="32")
        self.set_status(ord_id, "F", INV_QTY="16.5")
        self.set_status(ord_id, "C")
        events = self.events(ord_id)
        self.assertEqual([(e["FROM_STATUS"], e["TO_STATUS"]) for e in events],
                         [("", "Q"), ("Q", "I"), ("I", "H"), ("H", "I"), ("I", "F"), ("F", "C")])
        self.assertEqual(events[2]["REASON"], "Belt broken")
        self.assertTrue(events[1]["SCAN_FLG"] and events[3]["SCAN_FLG"])
        self.assertEqual((events[3]["ACT_PADDY"], events[4]["ACT_RICE"]), (32.0, 16.5))
        self.assertIsNone(events[0]["MINUTES_IN_FROM"])
        self.assertTrue(all(e["MINUTES_IN_FROM"] is not None for e in events[1:]))
        self.assertTrue(all(e["OPE_USER"] == "admin" and e["CUST_CODE"] == "C261001001" for e in events))
        self.post("/service/milling/%s/priority" % ord_id, {"priority": "H"})    # not a status change: nothing new
        self.assertEqual(len(self.events(ord_id)), 6)
        page = self.text("/service/milling/%s?popup=1" % ord_id)
        self.assertIn('id="od-timeline"', page)
        self.assertIn("Belt broken", page)
        self.assertIn("Rice received", page)

    def test_cancel_approval_and_pickup_change_are_tracked(self):
        ord_id = self.new_order()["ORD_ID"]
        self.post("/service/milling/%s/cancel-request" % ord_id)
        self.post("/service/milling/%s/cancel-answer" % ord_id, {"answer": "approve", "reason": "Customer changed mind"})
        self.post("/service/milling/%s/status" % ord_id, {"new_status": "CC"})
        self.assertEqual([e["TO_STATUS"] for e in self.events(ord_id)], ["Q", "H", "CC"])
        self.assertEqual(self.events(ord_id)[1]["REASON"], "Customer changed mind")

    def test_backfill_from_the_old_snapshots(self):
        ord_id = self.new_order()["ORD_ID"]
        self.set_status(ord_id, "I")
        self.set_status(ord_id, "H")
        with self.app.app_context():
            from app.models import mill_event
            col("AOILOG_MILLSTATUS").delete_many({})              # an order made before update 22
            self.assertEqual(mill_event.backfill(), 1)
            self.assertEqual(mill_event.backfill(), 0)            # once only
        events = self.events(ord_id)
        self.assertEqual([e["TO_STATUS"] for e in events], ["Q", "I", "H"])
        self.assertEqual({e["SOURCE"] for e in events}, {"BACKFILL"})
        self.assertEqual(events[2]["REASON"], "Machine check")

    def test_milling_analysis_and_csv(self):
        a = self.new_order(RICE_VARIETY="HM", REGIST_PADDY="100")["ORD_ID"]
        b = self.new_order(RICE_VARIETY="WH", REGIST_PADDY="50")["ORD_ID"]
        self.set_status(a, "I", ACT_PADDY="98")
        self.set_status(a, "F", INV_QTY="49")
        self.set_status(b, "CC")
        page = self.text("/admin/milling/analysis")
        self.assertIn("Milling Analysis", page)
        self.assertIn('id="ma-variety-table"', page)
        self.assertIn("50.00%", page)                               # 49 kg rice / 98 kg weighed paddy
        self.assertIn("อรุณ พงษ์ไพร", page)
        with self.app.app_context():
            from app.models import mill_analysis as ma
            from app.timeutil import today_local
            result = ma.analyse("factory2", today_local(), today_local())
        self.assertEqual((result["total"]["orders"], result["total"]["done"], result["total"]["cancelled"]), (2, 1, 1))
        self.assertEqual(result["total"]["scanned"], 1)
        row = next(r for r in result["rows"] if r["ord_id"] == a)
        self.assertAlmostEqual(row["yield"], 0.5)
        self.assertIsNotNone(row["wait"])
        self.assertEqual(self.text("/admin/milling/analysis?tab=period&variety=GL").count("<tr><td>"), 0)
        csv_orders = self.client.get("/admin/milling/analysis?tab=period&csv=orders")
        self.assertEqual(csv_orders.mimetype, "text/csv")
        body = csv_orders.get_data(as_text=True)
        self.assertTrue(body.startswith("\ufeffORD_ID,QR_ID"))
        self.assertIn(a, body)
        events = self.client.get("/admin/milling/analysis?tab=period&csv=events").get_data(as_text=True)
        self.assertEqual(events.count("EV-" + a), 3)               # Q, I, F
        self.logout()
        self.login("somchai.k", "factory1")                         # operators do not see the analysis
        self.assertEqual(self.client.get("/admin/milling/analysis").status_code, 403)


class Update23(Base):
    """Mobile country code (+66 default); Ready for Delivery pop-up confirms the time and sends LINE."""
    USER = "U" + "0123456789abcdef" * 2

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def customer(self, code="C261001001"):
        return next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == code)

    def test_mobile_country_code(self):
        page = self.text("/admin/customers/C261001001")
        self.assertIn('name="MOBILE_CC"', page)
        self.assertIn('<option value="+66" selected>', page)
        form = {"CUST_NAME": "อรุณ พงษ์ไพร", "MOBILE_NO": "09 123 4567", "ADDRESS": "45 หมู่ 2", "MOBILE_CC": "+95"}
        self.post("/admin/customers/C261001001", form, "/admin/customers/C261001001")
        self.assertEqual((self.customer()["MOBILE_CC"], self.customer()["MOBILE_NO"]), ("+95", "09 123 4567"))
        self.post("/admin/customers/C261001001", dict(form, MOBILE_CC="+999"), "/admin/customers/C261001001")
        self.assertEqual(self.customer()["MOBILE_CC"], "+66")                     # unknown code: Thailand
        self.post("/admin/customers/C261001001", dict(form, MOBILE_NO="abc"), "/admin/customers/C261001001")
        self.assertEqual(self.customer()["MOBILE_NO"], "09 123 4567")              # refused
        self.assertIn("+66 9 123 4567", self.text("/admin/customers/"))
        found = self.client.get("/service/milling/customers.json?q=6691234").get_json()
        self.assertTrue(any(c["code"] == "C261001001" for c in found["customers"]))
        from app.models import phone
        self.assertEqual(phone.display("+66", "081-234-5678"), "+66 81-234-5678")
        self.assertEqual(phone.display(None, ""), "")

    def test_ready_for_delivery_popup_confirms_and_sends_line(self):
        ord_id = self.new_order(ORDER_TYPE="D")["ORD_ID"]
        for status in ("I", "F"):
            self.set_status(ord_id, status)
        page = self.text("/service/milling/")
        self.assertIn('data-form="ready-%s"' % ord_id, page)
        self.assertIn('name="DELIVERY_ARRIVAL_TIME"', page)
        self.set_status(ord_id, "D", DELIVERY_ADDRESS="9 New Road", DELIVERY_DISTANCE="10", DELIVERY_ARRIVAL_TIME="")
        self.assertEqual(self.order(ord_id)["MILL_STATUS"], "F")                    # no time: nothing changes
        with self.app.app_context():
            col("AOIMS_CUSTOMER").update_one({"CUST_CODE": "C261001001"}, {"$set": {"LINE_USER_ID": self.USER}})
            col("AOIMS_SYSPARAM").update_one({"BRANCH_ID": "factory2"}, {"$set": {"SYSP_DELIVERYFEEKM": 5.0}})
        with mock.patch.dict(os.environ, {"AOI_LINE_CHANNEL_TOKEN": "token"}), \
                mock.patch("app.models.notify._push", return_value=None) as push:
            response = self.set_status(ord_id, "D", DELIVERY_ADDRESS="9 New Road", DELIVERY_DISTANCE="10",
                                       DELIVERY_ARRIVAL_TIME="2026-10-09T15:30")
        saved = self.order(ord_id)
        self.assertEqual((saved["MILL_STATUS"], saved["DELIVERY_CONFIRM_FLG"], saved["EST_PAY_DELIVERY"]), ("D", True, 50.0))
        push.assert_called_once()
        self.assertIn("15:30", push.call_args[0][1])
        notices = self.text(response.headers["Location"])
        self.assertIn("LINE message sent to the customer", notices)
        self.assertIn("LINE Sent", notices)

    def test_line_problem_is_told_to_the_operator(self):
        ord_id = self.new_order()["ORD_ID"]
        self.set_status(ord_id, "I")
        response = self.set_status(ord_id, "F")
        self.assertIn("The LINE message was not sent", self.text(response.headers["Location"]))


class Update24(Base):
    """LINE registration through the webhook: ticket QR or mobile number; reasons on the card; setup card."""
    SECRET = "test-channel-secret"
    USER = "U" + "fedcba9876543210" * 2

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")
        env = mock.patch.dict(os.environ, {"AOI_LINE_CHANNEL_TOKEN": "token", "AOI_LINE_CHANNEL_SECRET": self.SECRET,
                                           "AOI_LINE_OA_ID": "@783ycdvd"})
        env.start()
        self.addCleanup(env.stop)
        self.replies = []
        post = mock.patch("app.models.notify._post", side_effect=lambda url, payload: self.replies.append(payload) or None)
        post.start()
        self.addCleanup(post.stop)

    def call(self, events, secret=None):
        import base64, hashlib, hmac as _hmac
        body = json.dumps({"destination": "Uxxx", "events": events}).encode("utf-8")
        sign = base64.b64encode(_hmac.new((secret or self.SECRET).encode(), body, hashlib.sha256).digest()).decode()
        return self.app.test_client().post("/line/webhook", data=body, headers={"X-Line-Signature": sign, "Content-Type": "application/json"})

    def text_event(self, text, user=None):
        return {"type": "message", "replyToken": "r-%d" % len(self.replies), "source": {"type": "user", "userId": user or self.USER},
                "message": {"type": "text", "id": "1", "text": text}}

    def last_reply(self):
        return self.replies[-1]["messages"][0]["text"]

    def cust(self, code="C261001001"):
        return next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == code)

    def test_signature_is_required(self):
        self.assertEqual(self.app.test_client().get("/line/webhook").status_code, 200)
        self.assertEqual(self.call([], secret="wrong").status_code, 403)
        self.assertEqual(self.call([]).status_code, 200)                       # LINE's Verify button
        self.assertEqual(self.app.test_client().post("/line/webhook", data=b"{}").status_code, 403)

    def test_follow_says_how_to_link(self):
        self.call([{"type": "follow", "replyToken": "r", "source": {"type": "user", "userId": self.USER}}])
        self.assertIn("QR LINE", self.last_reply())
        self.assertEqual(self.replies[-1]["replyToken"], "r")
        self.assertEqual(self.db("AOILOG_LINE")[-1]["RESULT"], "welcome")

    def test_ticket_qr_links_and_resends_the_waiting_message(self):
        order = self.new_order()
        ord_id = order["ORD_ID"]
        self.set_status(ord_id, "I")
        self.set_status(ord_id, "F")                                           # not linked yet: lnNoUser
        note = next(n for n in self.db("AOITS_NOTIFY") if n["ORD_ID"] == ord_id)
        self.assertEqual(note["ERROR"], "lnNoUser")
        page = self.text("/service/milling/")
        self.assertIn("Customer not linked to LINE yet", page)                 # the reason is on the card
        detail = self.text("/service/milling/" + ord_id)
        self.assertIn("oaMessage/%40783ycdvd/?text=LINK%20" + ord_id, detail)  # the LINE QR on the label
        with self.app.app_context():
            from app.models import line_link
            code = line_link.link_text(ord_id)
        with mock.patch("app.models.notify._push", return_value=None) as push:
            self.call([self.text_event(code + "  ")])
        self.assertEqual(self.cust()["LINE_USER_ID"], self.USER)
        self.assertIn("#" + order["QR_ID"], self.last_reply())
        push.assert_called_once()                                              # the "rice ready" message went out
        self.assertEqual(next(n for n in self.db("AOITS_NOTIFY") if n["ORD_ID"] == ord_id)["NOTIFY_FLG"], "1")
        self.assertEqual(self.db("AOILOG_LINE")[-1]["RESULT"], "linked_ticket")

    def test_a_guessed_ticket_is_refused(self):
        ord_id = self.new_order()["ORD_ID"]
        self.call([self.text_event("LINK %s-000000" % ord_id)])
        self.assertFalse(self.cust().get("LINE_USER_ID"))
        self.assertEqual(self.db("AOILOG_LINE")[-1]["RESULT"], "bad_ticket")

    def test_mobile_number_links_once(self):
        self.call([self.text_event("081-234-5678")])
        self.assertEqual(self.cust()["LINE_USER_ID"], self.USER)
        self.assertIn("✅", self.last_reply())
        other = "U" + "1" * 32
        self.call([self.text_event("+66812345678", user=other)])               # someone else with the same number
        self.assertEqual(self.cust()["LINE_USER_ID"], self.USER)
        self.assertEqual(self.db("AOILOG_LINE")[-1]["RESULT"], "phone_taken")
        self.call([self.text_event("0899999999", user=other)])
        self.assertEqual(self.db("AOILOG_LINE")[-1]["RESULT"], "phone_not_found")
        self.call([self.text_event("hello", user=self.USER)])
        self.assertEqual(self.db("AOILOG_LINE")[-1]["RESULT"], "help_linked")

    def test_setup_card_and_poster(self):
        page = self.text("/admin/milling/maintenance?branch=factory2")
        self.assertIn('id="line-setup"', page)
        self.assertIn('data-tv-path="/line/webhook"', page)
        self.assertIn("@783ycdvd", page)
        poster = self.text("/admin/milling/line-poster")
        self.assertIn('data-qr="https://line.me/R/ti/p/%40783ycdvd"', poster)
        self.assertIn("poster.js", poster)

    def test_not_a_friend_reason(self):
        from app.models import notify
        self.assertEqual(notify.reason_key("HTTP 400 Failed to send messages"), "lnNotFriend")
        self.assertEqual(notify.reason_key("timed out"), "lnApiError")
        self.assertEqual(notify.reason_key("lnNoUser"), "lnNoUser")


class Update25(Base):
    """LINE QR only until linked; Milling Analysis in Administration; LINE messages; pricing tabs; price history."""
    USER = "U" + "abcdef0123456789" * 2

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def test_line_qr_only_until_the_customer_is_linked(self):
        with mock.patch.dict(os.environ, {"AOI_LINE_OA_ID": "@783ycdvd"}):
            first = self.new_order()["ORD_ID"]
            self.assertIn("oaMessage", self.text("/service/milling/" + first))
            with self.app.app_context():
                col("AOIMS_CUSTOMER").update_one({"CUST_CODE": "C261001001"}, {"$set": {"LINE_USER_ID": self.USER,
                                                 "LINE_DISPLAY_NAME": "Tim", "LINE_LINK_VIA": "TICKET"}})
            second = self.new_order()["ORD_ID"]
            self.assertNotIn("oaMessage", self.text("/service/milling/" + second))
            self.assertIn("LINE linked: Tim", self.text("/admin/customers/C261001001"))

    def test_link_keeps_the_line_display_name(self):
        env = {"AOI_LINE_CHANNEL_TOKEN": "t", "AOI_LINE_CHANNEL_SECRET": "s", "AOI_LINE_OA_ID": "@x"}
        with mock.patch.dict(os.environ, env), mock.patch("app.models.notify.profile", return_value={"displayName": "Tim K"}):
            with self.app.app_context():
                from app.models import line_link
                line_link.answer_text(self.USER, "081-234-5678")
        c = next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == "C261001001")
        self.assertEqual((c["LINE_USER_ID"], c["LINE_DISPLAY_NAME"], c["LINE_LINK_VIA"]), (self.USER, "Tim K", "PHONE"))

    def test_milling_analysis_moved_to_administration(self):
        self.assertIn('data-menu="AD_MILLANALYSIS"', self.text("/?g=AD"))
        self.assertNotIn("AD_MILLANALYSIS", self.text("/?g=SV"))
        self.assertNotIn('data-menu="SV_DASHBOARD"', self.text("/?g=SV"))
        for tab, marker in (("period", 'id="ma-kpis"'), ("today", 'id="svc-dashboard-grid"'), ("trade", 'id="trade-dashboard-grid"')):
            page = self.text("/admin/milling/analysis?tab=" + tab)
            self.assertIn(marker, page)
            self.assertIn('id="ma-tabs"', page)
        self.assertIn("/admin/milling/analysis?tab=today", self.client.get("/service/dashboard").headers["Location"])
        self.assertIn("tab=period", self.client.get("/service/analysis?csv=orders").headers["Location"])

    def test_line_message_maintenance(self):
        page = self.text("/admin/line-messages/")
        self.assertIn("ML_FINISHED", page)
        self.assertIn("LN_WELCOME", page)
        self.assertIn("{rice_kg}", page)
        bad = {"MSG_ID": "ML_FINISHED", "MSG_NAME": "Ready", "MSG_TEXT": "Hello {nope}", "ACTIVE_FLG": "1"}
        self.post("/admin/line-messages/", bad, "/admin/line-messages/")
        self.assertEqual(self.db("AOIMS_LINEMSG"), [])
        good = dict(bad, MSG_TEXT="คุณ{name} คิว #{no} ข้าว {rice_kg} กก. พร้อมรับที่{branch} ยอด {pay} บาท")
        self.post("/admin/line-messages/", good, "/admin/line-messages/")
        self.assertEqual(self.db("AOIMS_LINEMSG")[0]["MSG_TEXT"], good["MSG_TEXT"])
        self.assertIn("สมชาย ก.", self.text("/admin/line-messages/"))                # preview with sample values
        order = self.new_order()
        self.set_status(order["ORD_ID"], "I")
        self.set_status(order["ORD_ID"], "F", INV_QTY="16.5")
        note = next(n for n in self.db("AOITS_NOTIFY") if n["ORD_ID"] == order["ORD_ID"])
        self.assertIn("คิว #%s ข้าว 16.50 กก. พร้อมรับที่โรงงาน 2" % order["QR_ID"], note["NOTIFY_MESSAGE"])
        self.post("/admin/line-messages/", dict(good, ACTIVE_FLG=""), "/admin/line-messages/")   # switched off
        other = self.new_order()
        self.set_status(other["ORD_ID"], "I")
        self.set_status(other["ORD_ID"], "F")
        self.assertFalse(any(n["ORD_ID"] == other["ORD_ID"] for n in self.db("AOITS_NOTIFY")))
        self.post("/admin/line-messages/", {"MSG_ID": "LN_HELP", "MSG_TEXT": "สวัสดีครับ", "MSG_NAME": "x"}, "/admin/line-messages/")
        with self.app.app_context():
            from app.models import line_link
            self.assertEqual(line_link.answer_text(self.USER, "hello")[0], "สวัสดีครับ")
        self.post("/admin/line-messages/", {"MSG_ID": "LN_HELP", "action": "reset"}, "/admin/line-messages/")
        with self.app.app_context():
            self.assertIn("QR LINE", line_link.answer_text(self.USER, "hello")[0])
        self.logout()
        self.login("somchai.k", "factory1")
        self.assertEqual(self.client.get("/admin/line-messages/").status_code, 403)

    def test_pricing_tabs(self):
        price = self.text("/finance/pricing?branch=factory2")
        market_page = self.text("/finance/market?branch=factory2")
        for page in (price, market_page):
            self.assertIn('id="pricing-tabs"', page)
        self.assertIn('id="profit-card"', price)
        self.assertNotIn('id="market-history"', price)
        self.assertIn('id="market-history"', market_page)
        self.assertNotIn('id="profit-card"', market_page)

    def nabc_rows(self, days):
        rows = []
        for i, day in enumerate(days):
            for market_name, price in (("A", 14000 + i * 10), ("B", 14200 + i * 10), ("C", 15000 + i * 10)):
                rows.append({"data_date": day, "product_name": "ข้าวเปลือกเจ้าหอมมะลิ 105", "market_name": market_name,
                             "province": "ขอนแก่น", "day_price": price, "unit": "บาท/ตัน"})
        return rows

    def test_market_price_history(self):
        from datetime import date
        today = date.today()
        days = [(today - timedelta(days=d)).isoformat() for d in (400, 200, 60, 10, 1)]
        rows = self.nabc_rows(sorted(days, reverse=True))
        pages = [rows[:6], rows[6:12], rows[12:]]
        def fake(product, page):
            return {"data": pages[page - 1] if page <= len(pages) else [], "pagination": {"total": len(rows)}}
        with mock.patch("app.models.nabc_price._get", side_effect=fake):
            self.post("/finance/market", {"action": "history"}, "/finance/market")
            self.post("/finance/market", {"action": "history"}, "/finance/market")    # second time: nothing new
        self.assertEqual(len(self.db("AOITS_NABC_PRICE")), 15)
        with self.app.test_request_context():
            from app.models import market_history
            s12 = next(x for x in market_history.series("factory2", "12M")["skus"] if x["code"] == "PADDY_HM")
            s36 = next(x for x in market_history.series("factory2", "36M")["skus"] if x["code"] == "PADDY_HM")
            s3 = next(x for x in market_history.series("factory2", "3M")["skus"] if x["code"] == "PADDY_HM")
        self.assertEqual((len(s3["points"]), len(s12["points"]), len(s36["points"])), (3, 4, 5))
        self.assertEqual(s36["points"][0][1], 14.24)                              # median of the oldest day (14,240 THB/ton)
        page = self.text("/finance/market")
        self.assertIn('data-mkh-sku="PADDY_HM" checked', page)
        self.assertIn("market_chart.js", page)
        self.assertIn('data-mkh-period="36M"', page)


class Update26(Base):
    """Quick jump; Current Services; customer name check, delete, queue prompt; relink LINE from Customer Maintenance."""
    USER = "U" + "0123456789abcdef" * 2

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def customer(self, **fields):
        data = {"CUST_NAME": "Test Newcomer", "MOBILE_CC": "+66", "MOBILE_NO": "0891112222", "ADDRESS": "1 Road", "DISTANCE_KM": ""}
        data.update(fields)
        return self.post("/admin/customers/new", data, "/admin/customers/new")

    def test_duplicate_name_is_refused(self):
        self.assertEqual(self.customer(CUST_NAME="  อรุณ   พงษ์ไพร ").status_code, 200)       # same name, extra spaces
        self.assertEqual(len([c for c in self.db("AOIMS_CUSTOMER") if c["CUST_NAME"].replace(" ", "") == "อรุณพงษ์ไพร"]), 1)
        check = self.client.get("/admin/customers/check.json?name=" + "อรุณ พงษ์ไพร").get_json()
        self.assertEqual((check["ok"], check["dup"]["code"]), (False, "C261001001"))
        self.assertTrue(self.client.get("/admin/customers/check.json?name=อรุณ พงษ์ไพร&code=C261001001").get_json()["ok"])
        self.post("/admin/customers/C261001002", {"CUST_NAME": "อรุณ พงษ์ไพร", "MOBILE_CC": "+66", "MOBILE_NO": "0898765432",
                                                    "ADDRESS": "x"}, "/admin/customers/C261001002")
        self.assertEqual(next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == "C261001002")["CUST_NAME"], "สุนีย์ แก้วมณี")

    def test_new_customer_asks_to_queue(self):
        response = self.customer()
        self.assertEqual(response.status_code, 302)
        code = next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_NAME"] == "Test Newcomer")["CUST_CODE"]
        page = self.text(response.headers["Location"])
        self.assertIn("/service/milling/new?cust=" + code, page)
        self.assertIn("data-return-text", page)

    def test_delete_customer(self):
        self.customer()
        code = next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_NAME"] == "Test Newcomer")["CUST_CODE"]
        self.post("/admin/customers/%s/delete" % code, {}, "/admin/customers/" + code)
        self.assertFalse(any(c["CUST_CODE"] == code for c in self.db("AOIMS_CUSTOMER")))       # no orders: removed
        ord_id = self.new_order()["ORD_ID"]
        self.post("/admin/customers/C261001001/delete", {}, "/admin/customers/C261001001")      # open order: refused
        self.assertNotEqual(next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == "C261001001").get("CUST_STATUS"), "D")
        with self.app.app_context():
            col("AOITS_ORDMILLING").update_one({"ORD_ID": ord_id}, {"$set": {"MILL_STATUS": "C"}})
        self.post("/admin/customers/C261001001/delete", {}, "/admin/customers/C261001001")      # past orders: hidden
        c = next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == "C261001001")
        self.assertEqual(c["CUST_STATUS"], "D")
        self.assertIn("No customers match", self.text("/admin/customers/?q=อรุณ"))
        self.assertEqual(self.customer(CUST_NAME="อรุณ พงษ์ไพร").status_code, 302)            # the name is free again
        before = len(self.db("AOITS_ORDMILLING"))
        self.post("/service/milling/new", {"CUST_CODE": "C261001001", "REGIST_PADDY": "33", "BRAN_ORDER": "0", "BROKE_ORDER": "0",
                                           "ORDER_TYPE": "T", "MILL_PRIORITY": "M"}, "/service/milling/new")
        self.assertEqual(len(self.db("AOITS_ORDMILLING")), before)

    def test_current_services_replaces_service_status(self):
        self.assertNotIn('data-menu="SV_STATUS"', self.text("/?g=SV"))
        self.assertIn("Current Services", self.text("/?g=SV"))
        self.assertFalse(any("SV_STATUS" in m.values() for m in self.db("AOIMS_MENU")))
        page = self.text("/service/price")
        self.assertIn('id="cs-prices"', page)
        self.assertIn('id="cs-status"', page)

    def test_quick_jump_on_administration_pages(self):
        self.assertIn("data-quick-jump", self.text("/admin/milling/"))
        self.assertIn("data-quick-jump", self.text("/?g=AD"))
        self.assertNotIn("data-quick-jump", self.text("/?g=SV"))

    def test_relink_another_line_account(self):
        with self.app.app_context():
            col("AOIMS_CUSTOMER").update_one({"CUST_CODE": "C261001001"}, {"$set": {"LINE_USER_ID": "Uold", "LINE_LINK_VIA": "PHONE"}})
        with mock.patch.dict(os.environ, {"AOI_LINE_OA_ID": "@x"}):
            page = self.text("/admin/customers/C261001001")
        self.assertIn('id="cust-line-relink"', page)
        self.assertIn("LINK%20C261001001-", page)
        with self.app.app_context():
            from app.models import line_link
            _, bad, _ = line_link.answer_text(self.USER, "LINK C261001001-000000")
            reply, result, cust = line_link.answer_text(self.USER, line_link.link_text("C261001001"))
        self.assertEqual((bad, result, cust), ("bad_ticket", "linked_customer", "C261001001"))
        c = next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == "C261001001")
        self.assertEqual((c["LINE_USER_ID"], c["LINE_LINK_VIA"]), (self.USER, "STAFF_QR"))
        self.post("/admin/customers/C261001001/line-unlink", {}, "/admin/customers/C261001001")
        self.assertIsNone(next(c for c in self.db("AOIMS_CUSTOMER") if c["CUST_CODE"] == "C261001001")["LINE_USER_ID"])


class Update27(Base):
    """Financial > Investment & Break-even (draft): investment items, budget vs actual, break-even point."""

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def inv(self, data, tab="capex"):
        data = dict(data, branch="factory2", tab=tab)
        return self.post("/finance/investment", data, "/finance/investment?branch=factory2")

    def test_menu_and_rights(self):
        self.assertIn('data-menu="FN_INVEST"', self.text("/?g=FN"))
        for tab in ("capex", "budget", "breakeven"):
            self.assertEqual(self.client.get("/finance/investment?tab=" + tab).status_code, 200)
        self.logout()
        self.login("somchai.k", branch="factory1")                     # operator: no right
        self.assertEqual(self.client.get("/finance/investment").status_code, 403)

    def test_capex_depreciation_and_loan(self):
        self.inv({"action": "capex_save", "CAPEX_CAT": "MACHINE", "CAPEX_NAME": "Mill set", "CAPEX_AMOUNT": "1100000",
                  "SALVAGE_VALUE": "100000", "LIFE_YEARS": "", "FUND_SOURCE": "LOAN", "BUY_DATE": "2026-01-05"})
        self.inv({"action": "capex_save", "CAPEX_CAT": "LAND", "CAPEX_NAME": "Land", "CAPEX_AMOUNT": "500000"})
        self.inv({"action": "capex_save", "CAPEX_CAT": "EQUIP", "CAPEX_NAME": "Bad", "CAPEX_AMOUNT": "10", "SALVAGE_VALUE": "20"})
        items = self.db("AOIMS_CAPEX")
        self.assertEqual(len(items), 2)                                   # salvage above the amount is refused
        self.inv({"action": "project", "LOAN_AMOUNT": "1000000", "LOAN_RATE": "10", "LOAN_YEARS": "2",
                  "SERVICE_SHARE": "100", "BYPROD_KEEP": "100", "CALC_VARIETY": "HM"})
        with self.app.test_request_context():
            from app.models import investment
            a = investment.analyse("factory2", 2026)
        self.assertEqual((a["total"], a["dep_year"]), (1600000.0, 100000.0))   # 10 years by default, land not depreciated
        self.assertEqual(a["loan"]["interest_year1"], 100000.0)
        self.assertAlmostEqual(a["loan"]["payment_year"], 576190.48, places=2)
        self.assertEqual(a["fixed_month"], round((100000 + 100000) / 12, 2))
        self.assertEqual(a["var_kg"], round(a["eval"]["energy_cost"] + a["eval"]["other"], 3))
        self.assertEqual(a["be_month"], round(a["fixed_month"] / a["cm_kg"]))
        mill = items[0]["CAPEX_ID"] if items[0]["CAPEX_CAT"] == "MACHINE" else items[1]["CAPEX_ID"]
        self.inv({"action": "capex_delete", "CAPEX_ID": mill})
        self.assertEqual(len(self.db("AOIMS_CAPEX")), 1)
        self.assertEqual({l["LOG_ACTION"] for l in self.db("AOILOG_INVEST")}, {"C", "D"})

    def test_budget_and_actual(self):
        from datetime import date
        year = date.today().year
        self.inv({"action": "budget_fill", "year": str(year)}, "budget")
        lines = {b["BUDGET_CAT"]: b for b in self.db("AOITS_BUDGET")}
        self.assertEqual(lines["LABOUR"]["BUDGET_AMOUNT"], 600 * 2 * 26 * 12)
        self.assertEqual((lines["ELEC"]["BUDGET_AMOUNT"], lines["ELEC"]["COST_TYPE"]), (108000, "V"))
        self.inv({"action": "budget", "year": str(year), "B_MAINT": "12000", "C_MAINT": "F", "B_LABOUR": "374400", "C_LABOUR": "F",
                  "B_ELEC": "108000", "C_ELEC": "V", "B_MILLFEE": "60000"}, "budget")
        self.post("/finance/account", {"ACC_TYPE": "EX", "ACC_DESC": "Belt", "ACC_AMOUNT": "500", "ACC_CAT": "MAINT"}, "/finance/account")
        self.post("/finance/account", {"ACC_TYPE": "IN", "ACC_DESC": "Misc", "ACC_AMOUNT": "50"}, "/finance/account")
        with self.app.test_request_context():
            from app.models import investment
            bva = investment.budget_vs_actual("factory2", year)
            a = investment.analyse("factory2", year)
        by = {l["BUDGET_CAT"]: l for l in bva["lines"]}
        self.assertEqual((by["MAINT"]["actual"], by["OTHERIN"]["actual"], by["MAINT"]["BUDGET_AMOUNT"]), (500, 50, 12000))
        self.assertEqual(bva["totals"]["EX"]["budget"], 374400 + 12000 + 108000)
        self.assertEqual(a["fixed_parts"]["budget"], round((374400 + 12000) / 12, 2))    # electricity is variable
        self.assertIn('id="iv-budget-table"', self.text("/finance/investment?tab=budget&year=%d" % year))
        self.inv({"action": "budget_copy", "year": str(year + 1)}, "budget")
        self.assertEqual(len([b for b in self.db("AOITS_BUDGET") if b["BUDGET_YEAR"] == year + 1]), 4)

    def test_sample_project(self):
        self.inv({"action": "sample"})
        self.assertEqual(len(self.db("AOIMS_CAPEX")), 6)
        page = self.text("/finance/investment?tab=breakeven")
        self.assertIn('id="iv-be-chart"', page)
        self.assertIn("investment.js", page)
        self.inv({"action": "sample"})                                    # only once
        self.assertEqual(len(self.db("AOIMS_CAPEX")), 6)


class Update29(Base):
    """Hardware Control: the main milling motor ON/OFF first; the sample machines are not in use."""
    machine, issue_key, api = Update9.machine, Update9.issue_key, Update9.api

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def test_main_motor_on_off(self):
        motor = self.machine("F2-MOTOR")
        self.assertEqual((motor["MACHINE_TYPE"], motor["RATED_KW"], motor["RATED_AMP"], motor["RATED_RPM"], motor["CONTROL_FLG"]),
                         ("MAIN_MOTOR", 15.0, 30.0, 1450, True))
        self.assertFalse(self.machine("F2-SORT")["ACTIVE_FLG"])
        page = self.text("/automation/hardware")
        cards = re.findall(r'class="card machine-card[^"]*" data-machine="([^"]+)"', page)
        self.assertEqual(cards, ["F2-MOTOR"])
        self.assertIn("Mitsubishi SUPER LINE SF-J", page)
        self.assertIn('id="mc-not-used"', page)
        self.assertIn('disabled>&#9654; ON', page)                               # no device connected yet
        key = self.issue_key("/automation/hardware", "MACHINE_ID", "F2-MOTOR")
        self.api("post", "/api/v1/machines/F2-MOTOR/status", key, {"status": "I"})
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-MOTOR", "CMD": "START"})
        work = self.api("get", "/api/v1/machines/F2-MOTOR/commands", key).get_json()["commands"]
        self.assertEqual([c["command"] for c in work], ["START"])
        self.api("post", "/api/v1/commands/%s/ack" % work[0]["id"], key, {"ok": True, "result": "contactor on"})
        self.assertEqual(self.machine("F2-MOTOR")["MACHINE_STATUS"], "R")
        self.assertIn("ON &middot; Running", self.text("/automation/hardware"))
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-MOTOR", "CMD": "STOP"})
        work = self.api("get", "/api/v1/machines/F2-MOTOR/commands", key).get_json()["commands"]
        self.api("post", "/api/v1/commands/%s/ack" % work[0]["id"], key, {"ok": True})
        self.assertEqual(self.machine("F2-MOTOR")["MACHINE_STATUS"], "I")

    def test_only_machines_in_use_count_for_the_estimate(self):
        with self.app.app_context():
            from app.models import profit
            kwh, source = profit.energy_per_kg("factory2")
        self.assertEqual(source, "estimate")
        self.assertAlmostEqual(kwh, 15.0 * 0.273 / 60, places=6)                # the 15 kW motor only
        self.post("/automation/hardware", {"action": "save", "mode": "edit", "MACHINE_ID": "F2-SORT", "MACHINE_NAME": "Husker",
                                           "MACHINE_TYPE": "HUSKER", "RATED_KW": "15", "ACTIVE_FLG": "1"}, "/automation/hardware")
        self.assertTrue(self.machine("F2-SORT")["ACTIVE_FLG"])
        self.assertIn('data-machine="F2-SORT"', self.text("/automation/hardware"))


class Update30(Base):
    """Speed control and live values of the main motor; the gateway and the virtual inverter."""
    machine, issue_key, api = Update9.machine, Update9.issue_key, Update9.api

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def connected(self):
        key = self.issue_key("/automation/hardware", "MACHINE_ID", "F2-MOTOR")
        self.api("post", "/api/v1/machines/F2-MOTOR/telemetry", key, {"running": False, "set_hz": 50, "out_hz": 0, "amp": 0})
        return key

    def work(self, key):
        return self.api("get", "/api/v1/machines/F2-MOTOR/commands", key).get_json()["commands"]

    def test_speed_command_range_and_start_value(self):
        key = self.connected()
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-MOTOR", "CMD": "SPEED", "CMD_VALUE": "55"})
        self.assertEqual(self.work(key), [])                                     # above 50 Hz: refused
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-MOTOR", "CMD": "SPEED", "CMD_VALUE": "42.5"})
        cmd = self.work(key)[0]
        self.assertEqual((cmd["command"], cmd["value"]), ("SPEED", 42.5))
        self.api("post", "/api/v1/commands/%s/ack" % cmd["id"], key, {"ok": True, "result": "speed 42.5 Hz"})
        self.assertEqual(self.machine("F2-MOTOR")["SPEED_SET_HZ"], 42.5)
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-MOTOR", "CMD": "START"})
        cmd = self.work(key)[0]
        self.assertEqual((cmd["command"], cmd["value"]), ("START", 42.5))          # starts at the last speed set
        self.assertIn("42.5 Hz", self.text("/automation/hardware"))

    def test_telemetry_sets_live_values_and_status(self):
        key = self.connected()
        body = {"running": True, "set_hz": 45, "out_hz": 44.8, "amp": 21.3, "volt": 340, "rpm": 1300, "fault": ""}
        reply = self.api("post", "/api/v1/machines/F2-MOTOR/telemetry", key, body).get_json()
        self.assertEqual((reply["status"], reply["min_hz"], reply["max_hz"]), ("R", 30.0, 50.0))
        live = self.client.get("/automation/hardware/live.json").get_json()["machines"][0]
        self.assertEqual((live["id"], live["out_hz"], live["amp"], live["rpm"], live["running"], live["fault"]),
                         ("F2-MOTOR", 44.8, 21.3, 1300.0, True, None))
        self.assertTrue(live["samples"])
        self.api("post", "/api/v1/machines/F2-MOTOR/telemetry", key, dict(body, running=False, out_hz=0, amp=0, fault="E.THM"))
        self.assertEqual(self.machine("F2-MOTOR")["MACHINE_STATUS"], "F")
        self.assertIn('data-f="reset-form"', self.text("/automation/hardware"))
        self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-MOTOR", "CMD": "RESET"})
        cmd = self.work(key)[0]
        self.api("post", "/api/v1/commands/%s/ack" % cmd["id"], key, {"ok": True})
        self.assertEqual(self.machine("F2-MOTOR")["MACHINE_STATUS"], "I")
        self.assertEqual(self.api("post", "/api/v1/machines/F2-MOTOR/telemetry", "wrong", body).status_code, 401)
        self.assertEqual(self.api("post", "/api/v1/machines/F2-MOTOR/telemetry", key, dict(body, amp="x")).status_code, 422)
        self.assertEqual(len(self.db("AOITS_MACHINE_TELE")), 3)

    def test_gateway_with_the_virtual_inverter(self):
        import socket
        import time as _time
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        import vfd_sim
        import aoi_gateway
        with socket.socket() as probe:                                           # a free port
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        vfd = vfd_sim.start("127.0.0.1", port, 1, console=False, accel=0.5, decel=0.5)
        inverter = aoi_gateway.Inverter(aoi_gateway.ModbusTCP("127.0.0.1", port), 30, 50)
        self.assertEqual(aoi_gateway.carry_out(inverter, {"command": "START", "value": 60}), "run forward 50.0 Hz")  # kept at 50
        _time.sleep(1.0)
        values = inverter.read()
        self.assertTrue(values["running"])
        self.assertEqual((values["set_hz"], values["out_hz"], values["fault"]), (50.0, 50.0, ""))
        self.assertGreater(values["amp"], 10)
        self.assertGreater(values["rpm"], 1400)
        aoi_gateway.carry_out(inverter, {"command": "SPEED", "value": 40})
        _time.sleep(0.6)
        self.assertEqual(inverter.read()["out_hz"], 40.0)
        inverter.bus.write(aoi_gateway.REG["control"], 0x04)                     # reverse: refused by the inverter
        _time.sleep(0.6)
        self.assertEqual(inverter.read()["out_hz"], 0.0)
        aoi_gateway.carry_out(inverter, {"command": "START", "value": 45})
        _time.sleep(0.7)
        with vfd.lock:
            vfd.load = 250.0                                                     # jam -> overcurrent trip
        _time.sleep(0.4)
        values = inverter.read()
        self.assertEqual((values["running"], values["fault"]), (False, "E.OC2"))
        aoi_gateway.carry_out(inverter, {"command": "RESET"})
        self.assertEqual(inverter.read()["fault"], "")


class Update31(Base):
    """Tim's plant plan on the SCADA: all machines, only main motor 1 in use."""
    machine = Update9.machine

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def test_plant_machines(self):
        mine = {m["MACHINE_ID"]: m for m in self.db("AOIMS_MACHINE") if m["BRANCH_ID"] == "factory2"}
        self.assertEqual(set(mine), {"F2-" + x for x in ("SILO1", "SILO2", "CONV1", "CONV2", "INBOUND", "SORT", "MOTOR", "MOTOR2", "PACK", "AMR")})
        self.assertEqual([k for k, m in mine.items() if m.get("ACTIVE_FLG") is not False], ["F2-MOTOR"])
        self.assertEqual((mine["F2-MOTOR"]["SHIFT"], mine["F2-MOTOR2"]["SHIFT"]), ("DAY", "NIGHT"))
        self.assertEqual(mine["F2-INBOUND"]["FEEDS_TO"], "F2-MOTOR,F2-MOTOR2")
        self.assertEqual(mine["F2-SORT"]["FEEDS_TO"], "F2-INBOUND")
        self.assertEqual(mine["F2-PACK"]["FEEDS_TO"], "F2-AMR")                     # update 34
        self.assertEqual(mine["F2-MOTOR2"]["RATED_AMP"], 30.0)
        self.assertNotIn("F2-HUSK", mine)                                       # the update 9 samples are gone
        self.assertEqual(re.findall(r'data-machine="([^"]+)"', self.text("/automation/hardware")), ["F2-MOTOR"])

    def test_scada_screen_and_data(self):
        self.assertIn('data-menu="AT_SCADA"', self.text("/?g=AT"))
        page = self.text("/automation/scada")
        self.assertIn('id="scada-canvas"', page)
        self.assertIn("scada.js", page)
        self.assertIn('id="scada-trend-hz"', page)                              # update 34: the control-room design
        self.assertNotIn(' style="', page.split('id="scada"')[1].split("</section>")[0])   # no inline styles (CSP)
        self.assertIn("fonts.googleapis.com", self.client.get("/automation/scada").headers["Content-Security-Policy"])
        data = self.client.get("/automation/scada.json").get_json()
        nodes = {n["id"]: n for n in data["nodes"]}
        self.assertEqual((nodes["F2-MOTOR"]["status"], nodes["F2-MOTOR"]["active"]), ("O", True))
        self.assertEqual((nodes["F2-SILO1"]["status"], nodes["F2-SILO1"]["col"], nodes["F2-SILO1"]["feeds"]), ("DIS", 0, ["F2-CONV1"]))
        self.assertEqual((data["kpi"]["in_use"], data["kpi"]["planned"]), (1, 9))
        self.assertIn(data["shift"], ("DAY", "NIGHT"))
        self.logout()
        self.login("wanida.s")                                                  # accountant: no SCADA
        self.assertEqual(self.client.get("/automation/scada").status_code, 403)

    def test_scada_place_and_command_back_to_scada(self):
        self.post("/automation/hardware", {"action": "save", "mode": "edit", "MACHINE_ID": "F2-PACK", "MACHINE_NAME": "Packing",
                                           "MACHINE_TYPE": "PACKER", "SCADA_COL": "4", "SCADA_ROW": "1", "FEEDS_TO": "f2-amr",
                                           "SHIFT": "DAY"}, "/automation/hardware")
        pack = self.machine("F2-PACK")
        self.assertEqual((pack["SCADA_COL"], pack["SCADA_ROW"], pack["FEEDS_TO"], pack["SHIFT"], pack["ACTIVE_FLG"]), (4, 1, "F2-AMR", "DAY", False))
        self.post("/automation/hardware", {"action": "save", "mode": "edit", "MACHINE_ID": "F2-PACK", "MACHINE_NAME": "Packing",
                                           "MACHINE_TYPE": "PACKER", "SCADA_COL": "9"}, "/automation/hardware")
        self.assertEqual(self.machine("F2-PACK")["SCADA_COL"], 4)                # out of range: refused
        reply = self.post("/automation/hardware", {"action": "command", "MACHINE_ID": "F2-MOTOR", "CMD": "START", "back": "scada"})
        self.assertTrue(reply.headers["Location"].endswith("/automation/scada"))


class Update33(Base):
    """LINE at Finished for delivery orders, customer QR card, linked buying/selling orders search."""

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def test_customer_qr_card(self):
        page = self.text("/admin/customers/C261001001")
        self.assertIn('data-qr="C261001001"', page)
        card = self.text("/admin/customers/C261001001/card?n=4")
        self.assertEqual(card.count('class="cust-card"'), 4)
        self.assertIn("+66 81-234-5678", card)
        self.assertEqual(self.client.get("/admin/customers/C999999999/card").status_code, 404)

    def test_finished_opens_the_linked_buying_orders(self):
        order = self.new_order(BRAN_ORDER="2")
        self.set_status(order["ORD_ID"], "I")
        response = self.set_status(order["ORD_ID"], "F")
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("/service/buysell/?q=" + order["ORD_ID"], page)
        self.assertIn("data-return-url", page)
        other = self.new_order(BRAN_ORDER="1")
        found = self.text("/service/buysell/?q=" + order["ORD_ID"])
        self.assertEqual(len(re.findall(r'class="status-card" data-order=', found)), 1)
        self.assertNotIn(other["REF_BUYING_ORDER"], found)
        self.assertIn('value="%s"' % order["ORD_ID"], found)
        self.assertEqual(len(re.findall(r'class="status-card" data-order=', self.text("/service/buysell/?q=" + order["QR_ID"]))), 1)


class Update37(Base):
    """Camera QR scan buttons, milling order list search (the label QR opens the order)."""

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def test_scan_fields_and_scripts(self):
        page = self.text("/service/milling/new")
        self.assertIn('id="qr-scan-overlay"', page)
        self.assertIn("js/qrdecode.js", page)
        self.assertIn("js/qrscan.js", page)
        self.assertIn('id="reg-scan" data-scan ', page)
        self.assertIn('data-scan="submit"', self.text("/admin/customers/"))
        self.assertIn('id="bs-search" name="q"', self.text("/service/buysell/"))
        listing = self.text("/service/milling/")
        self.assertIn('id="ml-search"', listing)
        self.assertIn('data-scan="submit"', listing)

    def test_milling_list_search(self):
        first = self.new_order()
        second = self.new_order()
        cards = lambda page: re.findall(r'class="status-card[^"]*" data-order="([^"]+)"', page)
        page = self.text("/service/milling/?q=AOI-" + first["ORD_ID"])          # the label QR scanned
        self.assertEqual(cards(page), [first["ORD_ID"]])
        self.assertIn("data-auto-popup", page)
        page = self.text("/service/milling/?q=" + second["QR_ID"])
        self.assertIn(second["ORD_ID"], cards(page))
        self.assertNotIn("data-auto-popup", page)
        self.assertEqual(set(cards(self.text("/service/milling/?q=C261001001"))) >= {first["ORD_ID"], second["ORD_ID"]}, True)
        self.assertEqual(cards(self.text("/service/milling/?q=nobody-here")), [])
        # a status change from the searched list comes back to the same search
        response = self.set_status(first["ORD_ID"], "I", back="list", q_filter="AOI-" + first["ORD_ID"])
        self.assertIn("q=AOI-" + first["ORD_ID"], response.headers["Location"])

    def test_buysell_search_takes_the_milling_label(self):
        order = self.new_order(BRAN_ORDER="2")
        found = self.text("/service/buysell/?q=AOI-" + order["ORD_ID"])
        self.assertEqual(len(re.findall(r'class="status-card" data-order=', found)), 1)


class Update39(Base):
    """TV full screen button; the customer card prints (the QR-label print rule no longer hides it)."""

    def test_tv_has_full_screen(self):
        with self.app.app_context():
            from app.models import tv
            key = tv.issue_key("factory2", "admin")
        page = self.text("/tv/factory2?key=" + key)
        self.assertIn('id="tvb-full"', page)
        self.assertIn("เต็มจอ · Full screen", page)

    def test_card_page_is_not_hidden_in_print(self):
        css = self.text("/static/css/app.css")
        self.assertIn("body:not(.cust-card-page):not(.line-poster) * { visibility: hidden; }", css)
        self.assertNotIn("\n  body * { visibility: hidden; }", css)


class Update40(Base):
    """SCADA commands by fetch (the page and its full screen stay): JSON answer."""

    def setUp(self):
        super().setUp()
        self.login("admin")
        self.client.get("/lang/en")

    def test_command_answers_json_for_the_scada(self):
        page = self.text("/automation/scada")
        self.assertIn("data-command-url", page)
        r = self.post("/automation/hardware", {"action": "command", "back": "scada", "MACHINE_ID": "F2-MOTOR", "CMD": "START"},
                      "/automation/scada", headers={"X-AOI-Fetch": "1"})
        body = r.get_json()
        self.assertIsNotNone(body)
        self.assertIn("ok", body)
        self.assertTrue(body["message"])
        r = self.post("/automation/hardware", {"action": "command", "back": "scada", "MACHINE_ID": "F2-MOTOR", "CMD": "START"},
                      "/automation/scada")
        self.assertEqual(r.status_code, 302)                                   # the plain form still redirects


if __name__ == "__main__":
    unittest.main()
