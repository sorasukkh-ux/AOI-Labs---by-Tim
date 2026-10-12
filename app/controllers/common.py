"""Small helpers shared by the controllers."""
from flask import flash

from ..i18n import t
from ..models.branch import Branch

# Which controller opens each screen. Menus missing here are planned screens (see home.pending).
MENU_ENDPOINTS = {
    "SV_SERVICE": "service_milling.new_order",
    "SV_MILLSTATUS": "service_milling.order_list",
    "SV_STATUS": "service_info.tv",
    "SV_PRICE": "service_info.current_price",
    "SV_DASHBOARD": "service_info.dashboard",
    "SV_BSSTATUS": "service_buysell.order_list",
    "IV_LIST": "inventory.item_list",
    "IV_HISTORY": "inventory.history",
    "IV_DASHBOARD": "inventory.dashboard",
    "FN_PRICING": "finance.pricing",
    "FN_MARGIN": "finance.margin",
    "FN_ACCOUNT": "finance.account",
    "FN_MARKETING": "finance.marketing",
    "AT_AI": "automation.ai_screen",
    "AT_HARDWARE": "automation.hardware",
    "AT_ELECTRIC": "automation.electricity",
    "AT_MACHINE": "automation.usage",
    "AD_MILLPROD": "admin_milling.production",
    "AD_MILLTIME": "admin_milling.maintenance",
    "AD_USER": "admin_users.user_list",
    "AD_ROLE": "admin_roles.role_form",
    "AD_CUSTOMER": "admin_customers.customer_list",
}


def branch_label(branch_id):
    """Factory 1 / โรงงาน 1 for the known branches, the stored BRANCH_NAME for any other."""
    if branch_id == Branch.ALL:
        return t("branchAll")
    key = "branch" + str(branch_id or "").capitalize()
    text = t(key)
    if text != key:
        return text
    branch = Branch.get(branch_id)
    return (branch or {}).get("BRANCH_NAME") or str(branch_id or "")


def flash_error(error):
    """Shows a model ValidationError in the user's language."""
    flash(t(error.key, **error.params), "error")


def flash_ok(key, **params):
    flash(t(key, **params), "ok")
