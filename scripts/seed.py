"""Creates the indexes and the starting master data (branches, menus, roles, users, prices ...).

    python -m scripts.seed            add what is missing; never changes records that already exist (except: when
                                      the sample milling tests are added, the yields and minutes per kg follow them)
    python -m scripts.seed --reset    DELETE every collection of this system first, then seed again

The sample users all get the password in AOI_SEED_PASSWORD (default aoi12345). Change it before real use.
"""
import os
import sys
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

BRANCHES = [("factory1", "Factory 1"), ("factory2", "Factory 2")]
MILL_SETTINGS = {                       # defaults of AOI DB Structure 1.14
    "SYSP_MILLTIMEKG": 0.273, "SYSP_MILLORDGAP": 1.0, "SYSP_OPETIMESTART": "09:00", "SYSP_OPETIMEEND": "18:00",
    "SYSP_QUEUEPERIOD": "10:00, 13:00, 16:00, 18:00", "SYSP_DELIVERTIMEADD": 2.0,
    "SYSP_MILLFEEUNITKG": 50.0, "SYSP_MILLFEEPERUNIT": 12.0, "SYSP_DELIVERYFEEKM": 0.0,
    "SYSP_PROCCOSTKG": 0.0,
    "SYSP_LABORDAY": 600.0,              # labour cost per worker per day (THB), from Tim
    "SYSP_WORKERS": 2,                   # workers per working day (Tim, update 15)
    "SYSP_ELECMONTH": 9000.0,            # electricity bill per month (THB), Tim: about 8,000-10,000
    "SYSP_WORKDAYS": 26,                 # working days per month
    "SYSP_PLANKGDAY": 2000.0,            # planned paddy per day (kg) for the labour cost per kg, from Tim              # other processing cost per kg paddy (labour, sacks ...) for the profit check
    "SYSP_ELEC_RATE": 4.5,               # sample THB per kWh; set the real rate on Automation > Electricity
}
YIELD = {"YIELD_RICE": 0.5152, "YIELD_BRAN": 0.1000, "YIELD_BROKE": 0.0606, "YIELD_HUSK": 0.2200}
from app.models import variety as _var
MATERIALS = [(code, _var.DESC_TH[code], _var.MAT_TYPE[_var.kind_of(code)]) for code in _var.ALL]
# names before the rice varieties (update 10): PADDY and BROKEN are now the white-rice SKUs
OLD_MAT_DESC = {"PADDY": "ข้าวเปลือก", "BROKEN": "ปลายข้าว"}
# sample market prices (THB/kg) of the new varieties' SKUs; the market price import replaces them
NEW_SKU_MARKET = {"PADDY_HM": 15.0, "RICE_HM": 33.0, "BROKEN_HM": 18.0, "PADDY_GL": 12.0, "RICE_GL": 24.0, "BROKEN_GL": 14.0}
NEW_SKU_MARGIN = (5.0, 8.0)        # sample buy / sell margin targets (%) of those SKUs
# milling tests from the mill (Tim, 2026-10): (reference, paddy kg, minutes, rice kg, broken kg or None)
MILL_TESTS = [("Mill test", 33.0, 9.0, 17.0, 2.0), ("Customer A", 36.0, 10.0, 20.5, 2.0), ("Customer B", 39.0, 12.75, 22.5, 3.0)]
# (market buy, market sell, suggested buy, suggested sell, buy, sell, status) per branch, from the design prototype
PRICES = {
    "factory1": {"PADDY": (17, 18.9, 16.15, 20.41, 16.15, 20.41, "W"), "RICE_W5": (19.8, 21.4, 18.8, 22, 18.8, 22, "A"),
                 "BRAN": (8, 11.6, 7.6, 12, 7.6, 12, "A"), "BROKEN": (11.5, 11.9, 10.9, 12.3, 10.9, 12.3, "A"),
                 "HUSK": (None, None, None, None, 0, 15, "C")},
    "factory2": {"PADDY": (17, 18.9, 16.32, 20.41, 16.15, 20.41, "W"), "RICE_W5": (19.8, 21.4, 19.01, 22, 18.8, 22, "W"),
                 "BRAN": (8, 11.6, 7.68, 12, 7.68, 12, "A"), "BROKEN": (11.5, 11.9, 11.04, 12.3, 11.04, 12.3, "A"),
                 "HUSK": (None, None, None, None, 0, 14.5, "C")},
}
# sample machines of each factory: (id suffix, name, type, rated kW, capacity kg/h) - change them on Hardware Control
# update 29: the main milling motor (Tim's nameplate: Mitsubishi SUPER LINE SF-J, 20 HP / 15 kW, 4 pole, frame 160L,
# 380 V 50 Hz 30 A 1450 rpm) is the one machine in use; the sample machines below are kept but not in use
MAIN_MOTOR = {"MACHINE_NAME": "มอเตอร์หลักเครื่องสีข้าว", "MACHINE_TYPE": "MAIN_MOTOR", "RATED_KW": 15.0, "CAPACITY_KGH": None,
              "MACHINE_SEQ": 0, "CONTROL_FLG": True, "ACTIVE_FLG": True, "MOTOR_MODEL": "Mitsubishi SUPER LINE SF-J 20HP 4P 160L",
              "RATED_VOLT": 380.0, "RATED_AMP": 30.0, "RATED_RPM": 1450,
              "SPEED_MIN_HZ": 30.0, "SPEED_MAX_HZ": 50.0, "SPEED_SET_HZ": 50.0}      # update 30: speed by the inverter
MACHINES = [("HUSK", "Husker", "HUSKER", 15.0, 1500.0), ("WHITE", "Whitener", "WHITENER", 30.0, 1200.0),
            ("POLISH", "Polisher", "POLISHER", 22.0, 1200.0), ("GRADE", "Grader", "GRADER", 3.0, 2000.0)]
MENUS = [  # MENU_CODE, MENU_NAME, MENU_GROUP, MENU_SUBGROUP, MENU_SEQ
    ("SV_SERVICE", "Customer Service", "SV", "", 1),
    ("SV_PRICE", "Current Services", "SV", "", 3), ("SV_MILLSTATUS", "Milling Order List", "SV", "", 4),
    ("SV_BSSTATUS", "Buying/Selling Order List", "SV", "", 5),
    ("IV_LIST", "Inventory List", "IV", "", 1), ("IV_HISTORY", "Inventory Historical", "IV", "", 2),
    ("IV_DASHBOARD", "Inventory Dashboard", "IV", "", 3),
    ("FN_PRICING", "Pricing Maintenance", "FN", "", 1), ("FN_MARGIN", "Margin Maintenance", "FN", "", 2),
    ("FN_ACCOUNT", "Account", "FN", "", 3), ("FN_MARKETING", "Marketing", "FN", "", 4),
    ("FN_INVEST", "Investment & Break-even", "FN", "", 5),
    ("AD_MILLPROD", "Milling Production", "AD", "MILL", 1), ("AD_MILLTIME", "Milling Maintenance", "AD", "MILL", 2),
    ("AD_MILLANALYSIS", "Milling Analysis", "AD", "MILL", 3),
    ("AD_USER", "User Maintenance", "AD", "USER", 3), ("AD_ROLE", "Role Authorization", "AD", "USER", 4),
    ("AT_AI", "AI", "AT", "", 1), ("AT_HARDWARE", "Hardware Control", "AT", "", 2),
    ("AT_ELECTRIC", "Electricity", "AT", "", 3), ("AT_MACHINE", "Machine Usage Statistics", "AT", "", 4),
    ("AD_CUSTOMER", "Customer Registration", "AD", "CUST", 5),
    ("AD_LINEMSG", "LINE Message Maintenance", "AD", "CUST", 6),
]
ROLES = [("ADMIN", "Administrator", "Full access to every screen"),
         ("MANAGER", "Manager", "Runs service and inventory, approves prices"),
         ("OPERATOR", "Operator", "Takes milling, buy and sell orders and updates status"),
         ("ACCOUNT", "Accountant", "Keeps the account ledger and views prices")]
# v = view, c = create, e = edit, d = delete/cancel, a = approve. ADMIN needs no rows: it always has full access.
ROLE_AUTH = {
    "MANAGER": {"SV_SERVICE": "vced", "SV_PRICE": "v", "SV_MILLSTATUS": "veda", "SV_BSSTATUS": "vea",
                "AD_MILLANALYSIS": "v", "AD_LINEMSG": "ve", "IV_LIST": "vced", "IV_HISTORY": "v", "IV_DASHBOARD": "v", "FN_PRICING": "vea",
                "FN_MARGIN": "ve", "FN_ACCOUNT": "v", "FN_MARKETING": "ve", "FN_INVEST": "vce", "AD_CUSTOMER": "vced", "AD_MILLTIME": "ve",
                "AD_MILLPROD": "ve", "AD_USER": "v", "AT_AI": "vc", "AT_HARDWARE": "vcea", "AT_ELECTRIC": "vcea",
                "AT_MACHINE": "v"},
    "OPERATOR": {"SV_SERVICE": "vce", "SV_PRICE": "v", "SV_MILLSTATUS": "ve", "SV_BSSTATUS": "ve",
                 "IV_LIST": "vce", "IV_HISTORY": "v", "AD_CUSTOMER": "vce", "AT_HARDWARE": "ve", "AT_ELECTRIC": "vc",
                 "AT_MACHINE": "v"},
    "ACCOUNT": {"SV_PRICE": "v", "SV_BSSTATUS": "ve", "AD_MILLANALYSIS": "v", "IV_DASHBOARD": "v", "FN_PRICING": "v",
                "FN_MARGIN": "v", "FN_ACCOUNT": "vced", "FN_MARKETING": "v", "FN_INVEST": "vced", "AT_AI": "v", "AT_ELECTRIC": "va",
                "AT_MACHINE": "v"},
}
USERS = [  # USER_ID, USER_NAME, ROLE_CODE, BRANCH_ID, MOBILE_NO, USER_STATUS
    ("admin", "ผู้ดูแลระบบหลัก", "ADMIN", "ALL", "081-000-0001", "A"),
    ("prasert.m", "ประเสริฐ มั่นคง", "MANAGER", "ALL", "081-000-0002", "A"),
    ("somchai.k", "สมชาย แก้วใส", "OPERATOR", "factory1", "081-000-0003", "A"),
    ("nittaya.p", "นิตยา พรหมมา", "OPERATOR", "factory2", "081-000-0004", "L"),
    ("wanida.s", "วนิดา สุขใจ", "ACCOUNT", "factory2", "081-000-0005", "A"),
    ("anan.t", "อนันต์ ทองดี", "OPERATOR", "factory1", "", "I"),
]
CUSTOMERS = [  # CUST_CODE, CUST_NAME, MOBILE_NO, LINE_ID, ADDRESS, DISTANCE_KM
    ("C261001001", "อรุณ พงษ์ไพร", "081-234-5678", "arun.pp", "45 หมู่ 2 ต.บ้านเป็ด อ.เมือง จ.ขอนแก่น 40000", 6.0),
    ("C261001002", "สุนีย์ แก้วมณี", "089-876-5432", "sunee_kw", "88/4 หมู่ 3 ต.ในเมือง อ.เมือง จ.ขอนแก่น 40000", 3.5),
    ("C261001003", "วิภา ศรีสุข", "086-111-2233", "wipa.srisuk", "212 ถ.มิตรภาพ ต.ศิลา อ.เมือง จ.ขอนแก่น 40000", 12.0),
    ("C261001004", "ประยุทธ คงมั่น", "082-555-7890", "prayutkm", "15 หมู่ 7 ต.ท่าพระ อ.เมือง จ.ขอนแก่น 40260", 18.0),
    ("C261001005", "ธนากร ศิริวัฒน์", "090-333-4455", "thanakorn.s", "77 ถ.หน้าเมือง ต.ในเมือง อ.เมือง จ.ขอนแก่น 40000", 2.0),
]
SEED_USER = "system"
PERM_FIELDS = (("v", "CAN_VIEW"), ("c", "CAN_CREATE"), ("e", "CAN_EDIT"), ("d", "CAN_DELETE"), ("a", "CAN_APPROVE"))


def seed(log=print, reset=False):
    """Must run inside an application context."""
    from app.db import get_db
    from app.models.base import audit_new
    from app.models.schema import COLLECTIONS, ensure_indexes

    db = get_db()
    if reset:
        for name in COLLECTIONS:
            db.drop_collection(name)
        log("All collections dropped.")
    ensure_indexes(db)
    added = {}

    def add(collection, key, values):
        """Inserts the record only when no record with this key exists yet."""
        if db[collection].find_one(key, {"_id": 0}) is None:
            doc = dict(key, **values)
            doc.update(audit_new(SEED_USER))
            db[collection].insert_one(doc)
            added[collection] = added.get(collection, 0) + 1

    add("AOIMS_UOM", {"UOM_CODE": "KG"}, {"UOM_DES": "Kilogram", "UNIT_WEIGHT": 1.0})
    for code, desc, mat_type in MATERIALS:
        add("AOIMS_MATERIAL", {"MAT_CODE": code}, {"MAT_DESC": desc, "MAT_TYPE": mat_type, "NOTE": None})
    # update 12: databases made before the labour cost get 600 THB per day (and 2 workers since update 15)
    db["AOIMS_SYSPARAM"].update_many({"SYSP_LABORDAY": {"$exists": False}}, {"$set": {"SYSP_LABORDAY": 600.0, "SYSP_WORKERS": 2}})
    # update 15: Tim asked for 2 workers. Done once (a counter marks it), so a number changed later on Milling
    # Maintenance is kept.
    if db["AOISYS_COUNTER"].find_one({"COUNTER_KEY": "MIG-WORKERS-2"}, {"_id": 0}) is None:
        db["AOIMS_SYSPARAM"].update_many({}, {"$set": {"SYSP_WORKERS": 2}})
        db["AOISYS_COUNTER"].insert_one({"COUNTER_KEY": "MIG-WORKERS-2", "SEQ": 1})
    # update 15: the monthly electricity bill (9,000 THB) and 26 working days for databases made before them
    db["AOIMS_SYSPARAM"].update_many({"SYSP_ELECMONTH": {"$exists": False}}, {"$set": {"SYSP_ELECMONTH": 9000.0, "SYSP_WORKDAYS": 26}})
    # update 14: the planned paddy per day (2 tons) for databases made before it
    db["AOIMS_SYSPARAM"].update_many({"SYSP_PLANKGDAY": {"$exists": False}}, {"$set": {"SYSP_PLANKGDAY": 2000.0}})
    for code, old in OLD_MAT_DESC.items():
        db["AOIMS_MATERIAL"].update_one({"MAT_CODE": code, "MAT_DESC": old}, {"$set": {"MAT_DESC": _var.DESC_TH[code]}})
    for branch_id, name in BRANCHES:
        add("AOIMS_SYSPARAM", {"BRANCH_ID": branch_id}, dict(MILL_SETTINGS, BRANCH_NAME=name))
        for variety in _var.VARIETIES:
            add("AOIMS_MILLYIELD", {"BRANCH_ID": branch_id, "RICE_VARIETY": variety}, dict(YIELD))
        add("AOIMS_PROMOTION", {"BRANCH_ID": branch_id, "PROMO_CODE": "MILL_OWN"},
            {"PROMO_NAME": "Milling promotion", "DISCOUNT_PCT": 10.0, "PROMO_STATUS": "A", "VALID_FROM": None, "VALID_TO": None})
        for mat, (mk_buy, mk_sell, sug_buy, sug_sell, buy, sell, status) in PRICES[branch_id].items():
            add("AOIMS_PRICE", {"BRANCH_ID": branch_id, "MAT_CODE": mat}, {
                "MKPRICE_BUY": mk_buy, "MKPRICE_SELL": mk_sell, "SUGPRICE_BUY": sug_buy, "SUGPRICE_SELL": sug_sell,
                "PRICE_BUY": float(buy), "PRICE_SELL": float(sell), "PRICE_STATUS": status, "UOM_CODE": "KG",
                "MG_BUY_TARGET": None, "MG_BUY_RANGEFROM": None, "MG_BUY_RANGETO": None, "MG_BUY_MIN": None, "MG_BUY_MAX": None,
                "MG_SELL_TARGET": None, "MG_SELL_RANGEFROM": None, "MG_SELL_RANGETO": None, "MG_SELL_MIN": None, "MG_SELL_MAX": None})
        buy_t, sell_t = NEW_SKU_MARGIN
        for mat, market in NEW_SKU_MARKET.items():
            buy, sell = round(market * (1 - buy_t / 100), 2), round(market * (1 + sell_t / 100), 2)
            add("AOIMS_PRICE", {"BRANCH_ID": branch_id, "MAT_CODE": mat}, {
                "MKPRICE_BUY": market, "MKPRICE_SELL": market, "SUGPRICE_BUY": buy, "SUGPRICE_SELL": sell,
                "PRICE_BUY": buy, "PRICE_SELL": sell, "PRICE_STATUS": "A", "UOM_CODE": "KG",
                "MG_BUY_TARGET": buy_t, "MG_BUY_RANGEFROM": 3.0, "MG_BUY_RANGETO": 8.0, "MG_BUY_MIN": None, "MG_BUY_MAX": None,
                "MG_SELL_TARGET": sell_t, "MG_SELL_RANGEFROM": 5.0, "MG_SELL_RANGETO": 12.0, "MG_SELL_MIN": None, "MG_SELL_MAX": None})
        tests_before = added.get("AOITS_MILL_TEST", 0)
        for n, (ref, paddy, minutes, rice, broken) in enumerate(MILL_TESTS, start=1):
            add("AOITS_MILL_TEST", {"TEST_ID": "MT-SEED-%s-%d" % (branch_id.upper(), n)}, {
                "BRANCH_ID": branch_id, "RICE_VARIETY": None, "TEST_DATE": datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc),
                "PADDY_KG": paddy, "MILL_MIN": minutes, "RICE_KG": rice, "BROKE_KG": broken, "BRAN_KG": None, "HUSK_KG": None,
                "TEST_REF": ref})
        # update 11: Customer A's broken rice was weighed (2 kg); the sample test saved by update 10 without it is corrected
        fixed = db["AOITS_MILL_TEST"].update_one({"TEST_ID": "MT-SEED-%s-2" % branch_id.upper(), "BROKE_KG": None,
                                                  "OPE_USER": SEED_USER}, {"$set": {"BROKE_KG": 2.0}}).modified_count
        if added.get("AOITS_MILL_TEST", 0) > tests_before or fixed:   # new or corrected tests: the rates follow them
            from app.models.mill_test import MillTest
            MillTest.apply(branch_id, SEED_USER)
        prefix = "F1" if branch_id == "factory1" else "F2"
        for seq, (suffix, name, mtype, kw, capacity) in enumerate(MACHINES, start=1):
            add("AOIMS_MACHINE", {"MACHINE_ID": prefix + "-" + suffix}, {
                "BRANCH_ID": branch_id, "MACHINE_NAME": name, "MACHINE_TYPE": mtype, "RATED_KW": kw, "CAPACITY_KGH": capacity,
                "MACHINE_SEQ": seq, "CONTROL_FLG": False, "METER_ID": None, "MACHINE_STATUS": "O", "STATUS_TIME": None,
                "STATUS_SOURCE": "USER", "DEVICE_KEY_HASH": None, "LAST_SEEN": None, "ACTIVE_FLG": False})
        add("AOIMS_MACHINE", {"MACHINE_ID": prefix + "-MOTOR"}, dict(MAIN_MOTOR, BRANCH_ID=branch_id, METER_ID=None, MACHINE_STATUS="O",
                                                                   STATUS_TIME=None, STATUS_SOURCE="USER", DEVICE_KEY_HASH=None, LAST_SEEN=None))
        # update 30: the main motor's speed range (30-50 Hz, never above the 50 Hz it was made for)
        db["AOIMS_MACHINE"].update_many({"MACHINE_ID": prefix + "-MOTOR", "SPEED_MIN_HZ": {"$exists": False}},
                                        {"$set": {"SPEED_MIN_HZ": 30.0, "SPEED_MAX_HZ": 50.0, "SPEED_SET_HZ": 50.0}})
        # update 29: sample machines never set up (no device key, still the seed's record) are not in use
        db["AOIMS_MACHINE"].update_many({"MACHINE_ID": {"$in": [prefix + "-" + m[0] for m in MACHINES]}, "OPE_USER": SEED_USER,
                                         "DEVICE_KEY_HASH": None, "ACTIVE_FLG": {"$exists": False}}, {"$set": {"ACTIVE_FLG": False}})
        add("AOIMS_METER", {"METER_ID": prefix + "-MAIN"}, {"BRANCH_ID": branch_id, "METER_NAME": "Main meter",
                                                          "MACHINE_ID": None, "DEVICE_KEY_HASH": None, "LAST_SEEN": None})
    from app.models.market import seed_default_map
    seed_default_map(SEED_USER)                   # first guess of which report row is which SKU (Financial > Market prices)
    # update 26: Service Status is part of Current Services (SV_PRICE): who could open it can open Current Services
    for auth in list(db["AOIMS_ROLEAUTH"].find({"MENU_CODE": "SV_STATUS", "CAN_VIEW": True}, {"_id": 0})):
        if db["AOIMS_ROLEAUTH"].find_one({"ROLE_CODE": auth["ROLE_CODE"], "MENU_CODE": "SV_PRICE"}) is None:
            db["AOIMS_ROLEAUTH"].insert_one(dict({"ROLE_CODE": auth["ROLE_CODE"], "MENU_CODE": "SV_PRICE", "CAN_VIEW": True,
                                                  "CAN_CREATE": False, "CAN_EDIT": False, "CAN_DELETE": False, "CAN_APPROVE": False},
                                                 **audit_new(SEED_USER)))
        else:
            db["AOIMS_ROLEAUTH"].update_one({"ROLE_CODE": auth["ROLE_CODE"], "MENU_CODE": "SV_PRICE"}, {"$set": {"CAN_VIEW": True}})
    db["AOIMS_ROLEAUTH"].delete_many({"MENU_CODE": "SV_STATUS"})
    db["AOIMS_MENU"].delete_many({"MENU_CODE": "SV_STATUS"})
    db["AOIMS_MENU"].update_one({"MENU_CODE": "SV_PRICE", "MENU_NAME": "Current Price"}, {"$set": {"MENU_NAME": "Current Services"}})
    # update 25: Service Dashboard + Milling Analysis became Administration > Milling > Milling Analysis.
    # A role that could open either keeps the new screen; the old menus and their rights are removed.
    for auth in list(db["AOIMS_ROLEAUTH"].find({"MENU_CODE": {"$in": ["SV_DASHBOARD", "SV_ANALYSIS"]}}, {"_id": 0})):
        if auth.get("CAN_VIEW") and db["AOIMS_ROLEAUTH"].find_one({"ROLE_CODE": auth["ROLE_CODE"], "MENU_CODE": "AD_MILLANALYSIS"}) is None:
            db["AOIMS_ROLEAUTH"].insert_one(dict({"ROLE_CODE": auth["ROLE_CODE"], "MENU_CODE": "AD_MILLANALYSIS", "CAN_VIEW": True,
                                                  "CAN_CREATE": False, "CAN_EDIT": False, "CAN_DELETE": False, "CAN_APPROVE": False},
                                                 **audit_new(SEED_USER)))
    db["AOIMS_ROLEAUTH"].delete_many({"MENU_CODE": {"$in": ["SV_DASHBOARD", "SV_ANALYSIS"]}})
    db["AOIMS_MENU"].delete_many({"MENU_CODE": {"$in": ["SV_DASHBOARD", "SV_ANALYSIS"]}})
    # update 21: the two status screens are now called order lists (only the old default names are changed)
    for code, old_name in (("SV_MILLSTATUS", "Update Milling Status"), ("SV_BSSTATUS", "Update Buy/Sell Status")):
        new_name = next(m[1] for m in MENUS if m[0] == code)
        db["AOIMS_MENU"].update_one({"MENU_CODE": code, "MENU_NAME": old_name}, {"$set": {"MENU_NAME": new_name}})
    for code, name, group, sub, seq in MENUS:
        add("AOIMS_MENU", {"MENU_CODE": code}, {"MENU_NAME": name, "MENU_GROUP": group, "MENU_SUBGROUP": sub or None, "MENU_SEQ": seq})
    for code, name, desc in ROLES:
        add("AOIMS_ROLE", {"ROLE_CODE": code}, {"ROLE_NAME": name, "ROLE_DESC": desc, "ROLE_STATUS": "A"})
    for role, menus in ROLE_AUTH.items():
        for menu_code, _name, _group, _sub, _seq in MENUS:
            flags = menus.get(menu_code, "")
            add("AOIMS_ROLEAUTH", {"ROLE_CODE": role, "MENU_CODE": menu_code}, {field: letter in flags for letter, field in PERM_FIELDS})
    password_hash = None                    # every sample user starts with the same password, so one hash is enough
    for user_id, name, role, branch, mobile, status in USERS:
        if db["AOIMS_USER"].find_one({"USER_ID": user_id}, {"_id": 0}) is None:
            password_hash = password_hash or generate_password_hash(os.environ.get("AOI_SEED_PASSWORD", "aoi12345"))
            add("AOIMS_USER", {"USER_ID": user_id}, {
                "USER_NAME": name, "PASSWORD_HASH": password_hash, "ROLE_CODE": role, "BRANCH_ID": branch,
                "MOBILE_NO": mobile, "USER_STATUS": status, "LOGIN_FAIL_COUNT": 0, "LAST_LOGIN": None})
    for code, name, mobile, line_id, address, distance in CUSTOMERS:
        add("AOIMS_CUSTOMER", {"CUST_CODE": code}, {"CUST_NAME": name, "MOBILE_NO": mobile, "MOBILE_CC": "+66", "LINE_ID": line_id,
                                                    "ADDRESS": address, "DISTANCE_KM": distance})
    # update 23: mobile numbers saved before the country code are Thai numbers
    db["AOIMS_CUSTOMER"].update_many({"MOBILE_CC": {"$exists": False}}, {"$set": {"MOBILE_CC": "+66"}})
    # update 22: the status timeline of orders made before it, from their AOILOG_ORDMILLING snapshots (once)
    from app.models import mill_event
    rebuilt = mill_event.backfill()
    if rebuilt:
        log("Status timeline rebuilt for %d earlier milling order(s)" % rebuilt)
    if added:
        log("Seed data added: " + ", ".join("%s %d" % item for item in sorted(added.items())))
    else:
        log("Seed data already present. Nothing changed.")
    return added


def main():
    from app import create_app, DatabaseUnavailable
    reset = "--reset" in sys.argv
    if reset and "--yes" not in sys.argv:
        answer = input("This deletes ALL data of this system in the configured database. Type DELETE to continue: ")
        if answer.strip() != "DELETE":
            print("Cancelled.")
            return 1
    try:
        app = create_app()
    except DatabaseUnavailable as error:
        print("ERROR: %s" % error)
        return 1
    with app.app_context():
        seed(reset=reset)
    return 0


if __name__ == "__main__":
    sys.exit(main())
