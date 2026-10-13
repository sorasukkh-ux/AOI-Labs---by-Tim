"""LINE webhook: LINE calls POST /line/webhook when someone adds the Official Account or writes to it.

Set the address on LINE Developers > the channel > Messaging API > Webhook URL (https://<server>/line/webhook),
tap Verify and turn on "Use webhook". Each call is signed with the channel secret (X-Line-Signature); calls
without a valid signature are refused. There is no sign-in or CSRF token here (the caller is LINE, not a browser).
GET /line/webhook answers "AOI Labs LINE webhook" so the address can be checked in a browser.
"""
import json

from flask import Blueprint, abort, jsonify, request

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
