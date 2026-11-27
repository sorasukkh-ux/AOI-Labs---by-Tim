"""CONTROLLER layer: one module per area of the system. Each is a Flask blueprint."""


def register_controllers(app):
    from . import auth, home, admin_users, admin_roles, admin_customers, admin_milling, service_milling, service_buysell, inventory, finance, service_info, automation, device_api, tv, line_api, admin_line, admin_printer
    for module in (auth, home, admin_users, admin_roles, admin_customers, admin_milling, service_milling, service_buysell, inventory, finance, service_info, automation, device_api, tv, line_api, admin_line, admin_printer):
        app.register_blueprint(module.bp)
    app.register_blueprint(admin_printer.print_bp)            # update 141: POST /print
