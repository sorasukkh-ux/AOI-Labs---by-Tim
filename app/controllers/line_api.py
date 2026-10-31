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
    resp = Response(qr_png.png(coupon.qr_text(c), scale=12), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp
