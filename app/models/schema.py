"""Every collection of AOI DB Structure 1.42 and its indexes.

MongoDB creates a collection the first time a record is written, so "creating the tables" means
creating the indexes below. Unique indexes are the primary keys of the DB structure.
Each entry: collection name -> list of (fields, unique).
"""
from ..db import get_db

COLLECTIONS = {
    # ---- masters ----
    "AOIMS_MATERIAL": [(["MAT_CODE"], True)],
    "AOIMS_UOM": [(["UOM_CODE"], True)],
    "AOIMS_CUSTOMER": [(["CUST_CODE"], True), (["MOBILE_NO"], False), (["CUST_NAME"], False)],
    "AOIMS_LOC": [(["BRANCH_ID", "LOC_ID"], True)],
    "AOIMS_SYSPARAM": [(["BRANCH_ID"], True)],
    "AOIMS_PRICE": [(["BRANCH_ID", "MAT_CODE"], True)],
    "AOIMS_PROMOTION": [(["BRANCH_ID", "PROMO_CODE"], True)],
    "AOIMS_MILLYIELD": [(["BRANCH_ID", "RICE_VARIETY"], True)],
    "AOIMS_USER": [(["USER_ID"], True), (["ROLE_CODE"], False)],
    "AOIMS_ROLE": [(["ROLE_CODE"], True)],
    "AOIMS_MENU": [(["MENU_CODE"], True)],
    "AOIMS_LINEMSG": [(["MSG_ID"], True)],
    "AOIMS_ROLEAUTH": [(["ROLE_CODE", "MENU_CODE"], True)],
    # ---- transactions ----
    "AOITS_ORDMILLING": [(["ORD_ID"], True), (["BRANCH_ID", "MILL_STATUS"], False), (["CUST_CODE"], False)],
    "AOITS_ORDBUYSELL": [(["ORD_ID"], True), (["BRANCH_ID", "ORD_STATUS"], False), (["REF_ORD_ID"], False)],
    "AOITS_QR_PRINT": [(["PRINT_ID"], True), (["ORD_ID"], False)],
    "AOITS_NOTIFY": [(["NOTIFY_ID"], True), (["ORD_ID"], False)],
    # ---- investment & break-even (1.33, draft) ----
    "AOIMS_INVPROJECT": [(["BRANCH_ID"], True)],
    "AOIMS_CAPEX": [(["CAPEX_ID"], True), (["BRANCH_ID"], False)],
    "AOITS_BUDGET": [(["BRANCH_ID", "BUDGET_YEAR", "BUDGET_CAT"], True)],
    "AOILOG_INVEST": [(["LOG_ID"], True), (["TABLE_NAME", "REC_KEY"], False)],
    "AOITS_ACCOUNT": [(["ACC_ID"], True), (["BRANCH_ID", "ACC_DATE"], False)],
    "AOINV_ONHAND": [(["INV_ID"], True), (["BRANCH_ID", "LOC_ID"], False), (["BRANCH_ID", "SUB_2"], False), (["MAT_CODE"], False)],
    # ---- logs ----
    "AOILOG_ONHAND": [(["INV_ID", "LOG_DATE"], False)],
    "AOILOG_ORDMILLING": [(["ORD_ID", "LOG_DATE"], False)],
    "AOILOG_LINE": [(["LOG_ID"], True), (["LOG_DATE"], False), (["LINE_USER_ID"], False)],
    "AOILOG_MILLSTATUS": [(["EVENT_ID"], True), (["ORD_ID", "EVENT_DATE"], False), (["BRANCH_ID", "EVENT_DATE"], False)],
    "AOILOG_ORDBUYSELL": [(["ORD_ID", "LOG_DATE"], False)],
    "AOILOG_PRICE": [(["BRANCH_ID", "MAT_CODE", "LOG_DATE"], False)],
    "AOILOG_ACCOUNT": [(["ACC_ID", "LOG_DATE"], False)],
    # ---- automation (1.20) ----
    "AOIMS_MACHINE": [(["MACHINE_ID"], True), (["BRANCH_ID", "MACHINE_SEQ"], False)],
    "AOIMS_METER": [(["METER_ID"], True), (["BRANCH_ID"], False)],
    "AOITS_MACHINE_CMD": [(["CMD_ID"], True), (["MACHINE_ID", "CMD_STATUS"], False), (["BRANCH_ID", "REQ_DATE"], False)],
    "AOITS_METER_READ": [(["READ_ID"], True), (["METER_ID", "READ_DATE"], False), (["BRANCH_ID", "READ_DATE"], False)],
    "AOITS_MACHINE_TELE": [(["MACHINE_ID", "TEL_DATE"], False)],
    "AOITS_HEALTH": [(["CHECK_DATE"], False)],                       # update 48
    "AOITS_SYSLOG": [(["LAST_DATE"], False), (["MESSAGE", "LEVEL"], False)],
    "AOITS_MONITOR": [(["KEY"], True), (["LAST_DATE"], False)],     # update 49
    "AOILOG_MACHINE": [(["MACHINE_ID", "LOG_DATE"], False), (["BRANCH_ID", "LOG_DATE"], False)],
    "AOIMS_MKMAP": [(["MAT_CODE"], True)],
    "AOITS_MKREPORT": [(["REPORT_ID"], True), (["FETCH_DATE"], False)],
    "AOITS_NABC_PRICE": [(["PRODUCT_NAME", "DATA_DATE", "MARKET_NAME", "PROVINCE"], True), (["PRODUCT_NAME", "DATA_DATE"], False)],
    "AOITS_NABC_INDEX": [(["SCOPE", "PERIOD"], True)],
    "AOITS_MILL_TEST": [(["TEST_ID"], True), (["BRANCH_ID", "TEST_DATE"], False)],
    "AOILOG_AI_ASK": [(["BRANCH_ID", "LOG_DATE"], False), (["USER_ID", "LOG_DATE"], False)],
    # ---- technical (not in the DB structure workbook): running numbers for order and customer codes ----
    "AOISYS_COUNTER": [(["COUNTER_KEY"], True)],
}


# Indexes of earlier versions that would now be wrong (1.15: a location ID is unique per branch, not overall).
# 1.21: the yield ratios are kept per branch and rice variety.
OBSOLETE = {"AOIMS_LOC": ["uk_LOC_ID"], "AOINV_ONHAND": ["ix_LOC_ID"], "AOIMS_MILLYIELD": ["uk_BRANCH_ID"]}


def ensure_indexes(db=None):
    db = db if db is not None else get_db()
    for name, index_names in OBSOLETE.items():
        for index_name in index_names:
            try:
                db[name].drop_index(index_name)
            except Exception:                         # not there (new database or the in-memory stand-in)
                pass
    # 1.21: yield records saved before the varieties existed are the white-rice records
    db["AOIMS_MILLYIELD"].update_many({"RICE_VARIETY": {"$exists": False}}, {"$set": {"RICE_VARIETY": "WH"}})
    for name, indexes in COLLECTIONS.items():
        for fields, unique in indexes:
            db[name].create_index([(f, 1) for f in fields], unique=unique,
                                  name=("uk_" if unique else "ix_") + "_".join(fields))
