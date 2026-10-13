"""AOIMS_MENU: the screens of the system, grouped for the home screen."""
from .base import col, NO_ID

GROUPS = [("SV", "groupService"), ("IV", "groupInventory"), ("FN", "groupFinancial"), ("AT", "groupAutomation"), ("AD", "groupAdmin")]
SUBGROUP_LABELS = {"MILL": "subAdminMilling", "USER": "subAdminUser", "CUST": "subAdminCustomer"}
# Translation key of each screen name (MENU_NAME is used when a menu has no key here).
LABEL_KEYS = {
    "SV_SERVICE": "tabService", "SV_PRICE": "tabCurrentServices", "SV_MILLSTATUS": "tabUpdateStatus",
    "SV_BSSTATUS": "tabUpdateBuySellStatus", "AD_MILLANALYSIS": "tabMillAnalysis", "AD_LINEMSG": "tabLineMsg",
    "IV_LIST": "tabInventoryList", "IV_HISTORY": "tabInventoryHistory", "IV_DASHBOARD": "tabInventoryDashboard",
    "FN_PRICING": "tabPricing", "FN_MARGIN": "tabMargin", "FN_ACCOUNT": "tabAccount", "FN_MARKETING": "tabMarketing", "FN_INVEST": "tabInvest",
    "AD_MILLPROD": "tabMillingProduction", "AD_MILLTIME": "tabMillingTime", "AD_USER": "tabUser", "AD_ROLE": "tabRole",
    "AD_CUSTOMER": "tabCustomer",
    "AT_AI": "tabAi", "AT_HARDWARE": "tabHardware", "AT_ELECTRIC": "tabElectric", "AT_MACHINE": "tabMachineUsage",
}
ICONS = {
    "SV_SERVICE": "\U0001F9FE", "SV_PRICE": "\U0001F4FA", "SV_MILLSTATUS": "\U0001F504",
    "SV_BSSTATUS": "\U0001F6D2", "AD_MILLANALYSIS": "\U0001F4CA", "AD_LINEMSG": "\U0001F4AC", "IV_LIST": "\U0001F4E6", "IV_HISTORY": "\U0001F4DC",
    "IV_DASHBOARD": "\U0001F4C8", "FN_PRICING": "\U0001F3F7", "FN_MARGIN": "\U0001F4D0", "FN_ACCOUNT": "\U0001F4D2",
    "FN_MARKETING": "\U0001F4E3", "FN_INVEST": "\U0001F4B0", "AD_MILLPROD": "\U0001F3ED", "AD_MILLTIME": "⏱", "AD_USER": "\U0001F465",
    "AD_ROLE": "\U0001F510", "AD_CUSTOMER": "\U0001F464",
    "AT_AI": "\U0001F916", "AT_HARDWARE": "\U0001F39B\uFE0F", "AT_ELECTRIC": "⚡", "AT_MACHINE": "⚙\uFE0F",
}


class Menu:
    COL = "AOIMS_MENU"

    @classmethod
    def all(cls):
        order = {code: index for index, (code, _key) in enumerate(GROUPS)}
        menus = list(col(cls.COL).find({}, NO_ID))
        menus.sort(key=lambda m: (order.get(m.get("MENU_GROUP"), 99), m.get("MENU_SEQ") or 0, m["MENU_CODE"]))
        return menus

    @classmethod
    def codes(cls):
        return [m["MENU_CODE"] for m in cls.all()]
