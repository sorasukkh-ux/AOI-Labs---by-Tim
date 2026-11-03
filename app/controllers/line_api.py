"""LINE webhook: LINE calls POST /line/webhook when someone adds the Official Account or writes to it.

Set the address on LINE Developers > the channel > Messaging API > Webhook URL (https://<server>/line/webhook),
tap Verify and turn on "Use webhook". Each call is signed with the channel secret (X-Line-Signature); calls
without a valid signature are refused. There is no sign-in or CSRF token here (the caller is LINE, not a browser).
GET /line/webhook answers "AOI Labs LINE webhook" so the address can be checked in a browser.
"""
import json

from flask import Blueprint, Response, abort, jsonify, request

from ..models import line_link

bp = Blueprint("line_api", __name__, url_prefix="/line")


@bp.route("/webhook", methods=["GET", "POST"], strict_slashes=False)
def webhook():
    if request.method == "GET":
        return "AOI Labs LINE webhook: " + ("ready" if line_link.secret() else "AOI_LINE_CHANNEL_SECRET is not set"), 200, \
            {"Content-Type": "text/plain; charset=utf-8"}
    body = request.get_data()
    if not line_link.secret():
        abort(503, description="AOI_LINE_CHANNEL_SECRET is not set")
    if not line_link.valid_signature(body, request.headers.get("X-Line-Signature", "")):
        abort(403, description="bad signature")
    try:
        events = json.loads(body.decode("utf-8") or "{}").get("events", [])
    except ValueError:
        abort(400)
    return jsonify({"ok": True, "handled": line_link.handle(events)})


@bp.route("/qr/<ord_id>.png")
def order_qr(ord_id):
    """Update 47: the QR picture of a milling order for the customer's LINE (LINE fetches it: no sign-in). The address
    carries a secret token made from the order ID, so other orders cannot be guessed."""
    import secrets
    from ..models import notify, qr_png
    from ..models.milling_order import MillingOrder
    order = MillingOrder.find(ord_id)
    if not order or not secrets.compare_digest(request.args.get("t", ""), notify.qr_token(order["ORD_ID"])):
        abort(404)
    resp = Response(qr_png.png("AOI-" + order["ORD_ID"], scale=14), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.route("/card/<code>.png")
def customer_qr(code):
    """Update 48: the customer card QR (the customer code) for the customer's LINE."""
    import secrets
    from ..models import notify, qr_png
    from ..models.customer import Customer
    customer = Customer.find(code)
    if not customer or not secrets.compare_digest(request.args.get("t", ""), notify.card_token(customer["CUST_CODE"])):
        abort(404)
    resp = Response(qr_png.png(customer["CUST_CODE"], scale=14), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@bp.route("/coupon/<coupon_id>.png")
def coupon_qr(coupon_id):
    """Update 96: the QR picture of a campaign coupon for the customer's LINE (secret token in the address)."""
    import secrets
    from ..models import notify, qr_png, coupon
    c = coupon.get(coupon_id)
    if not c or not secrets.compare_digest(request.args.get("t", ""), notify.coupon_token(c["COUPON_ID"])):
        abort(404)
    # update 105: LINE's image server must be allowed to cache the picture, else the QR shows blank on the card
    resp = Response(qr_png.png(coupon.qr_text(c), scale=14), mimetype="image/png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


# ---------- update 108: member registration from the leaflet (the link comes in the LINE reply) ----------
@bp.route("/register/<tk>", methods=["GET", "POST"])
def register(tk):
    from flask import render_template
    from ..models import member_reg, notify, line_msg
    from ..models.base import ValidationError
    from ..models.tv import short_name
    from ..security import check_csrf
    from ..i18n import t_in
    try:
        user_id, leaf_id = member_reg.read_token(tk)
    except ValidationError as error:
        return render_template("line/register.html", error=t_in("th", error.key), done=None, form={}), 400
    already = member_reg.linked_customer(user_id)
    if already:
        return render_template("line/register.html", error=None, done={"name": short_name(already.get("CUST_NAME", "")),
                               "code": already["CUST_CODE"], "kind": "already"}, form={})
    form, error = {}, None
    if request.method == "POST":
        check_csrf()
        form = request.form
        try:
            customer, kind = member_reg.register(user_id, leaf_id, request.form)
        except ValidationError as err:
            error = t_in("th", err.key, **err.params)
        else:
            name = short_name(customer.get("CUST_NAME", "")) or customer["CUST_CODE"]
            if notify.configured():
                notify._push(user_id, line_msg.text("LN_REG_DONE", name=name, code=customer["CUST_CODE"]))
                notify.send_card(customer, line_link.request_base())
            return render_template("line/register.html", error=None, done={"name": name, "code": customer["CUST_CODE"], "kind": kind}, form={})
    leaf = member_reg.get(leaf_id) if leaf_id else None
    return render_template("line/register.html", error=error, done=None, form=form, leaf=leaf)
